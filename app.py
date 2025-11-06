from __future__ import annotations

import json
import re
import random
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# === PERCORSI BASE ===
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "static" / "data"
CONFIG_PATH = DATA_DIR / "config.runtime.json"

# === APP FASTAPI ===
app = FastAPI(title="Tecnaria Sinapsi — Q/A", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # restringi in produzione
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === CACHE IN MEMORIA ===
_families_cache: Dict[str, Dict[str, Any]] = {}
_config_cache: Dict[str, Any] = {}
_rr_counters: Dict[str, int] = {}  # round-robin per item id
_rng = random.Random(20251106)     # RNG deterministico (puoi variare da config)

# ---------------------------
# I/O utility
# ---------------------------
def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"File non trovato: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def load_config() -> Dict[str, Any]:
    global _config_cache, _rng
    try:
        cfg = _read_json(CONFIG_PATH)
        _config_cache = cfg
        # opzionale: seed da config
        seed = (
            ((cfg.get("admin") or {}).get("response_policy") or {}).get("variant_seed")
        )
        if isinstance(seed, int):
            _rng.seed(seed)
        return cfg
    except FileNotFoundError:
        default_cfg = {
            "admin": {
                "response_policy": {
                    "mode": "canonical",
                    "variant_selection": "round_robin",
                    "variant_seed": 20251106,
                },
                "security": {"admin_token_sha256": ""},
            }
        }
        _config_cache = default_cfg
        return default_cfg

def get_config() -> Dict[str, Any]:
    # Ricarica sempre per permettere switch a caldo editando il file
    return load_config()

def load_family(family_name: str) -> Dict[str, Any]:
    """
    Carica una famiglia (es. 'VCEM') da static/data/VCEM.json
    """
    global _families_cache
    fname = f"{family_name.upper()}.json"
    path = DATA_DIR / fname
    data = _read_json(path)
    # normalizzazione minima
    if "family" not in data:
        data["family"] = family_name.upper()
    if "response_policy" not in data:
        data["response_policy"] = {
            "mode": "inherit",
            "variant_selection": "inherit",
            "lock_to_item_core": False,
        }
    if "items" not in data:
        data["items"] = []
    _families_cache[family_name.upper()] = data
    return data

# ---------------------------
# Normalizzazione & sinonimi
# ---------------------------
SINONIMI_MAP = {
    r"\bsparachiodi\b": "p560",
    r"\bchiodatrice\b": "p560",
    r"\bpistola\b": "p560",
    r"\bspit\b": "p560",
    r"\bcolpo\b": "p560",
    r"\bcls\b": "c25/30",
    r"\bbeton\b": "c25/30",
    r"\bsoffiare\b": "pulire",
    r"\baspirare\b": "pulire",
    r"\bforatura\b": "foro",
    r"\bforare\b": "foro",
    r"\bavvitatura\b": "avvitare",
    r"\blocmente\b": "laterocemento",
}

TOKEN_SPLIT = re.compile(r"[^a-z0-9àèéìòùç]+", re.IGNORECASE)

def normalize_query(q: str) -> str:
    q = q.strip().lower()
    q = re.sub(r"[^\w\sàèéìòùç]", " ", q, flags=re.UNICODE)
    for pat, repl in SINONIMI_MAP.items():
        q = re.sub(pat, repl, q)
    q = re.sub(r"\s+", " ", q)
    return q.strip()

def tokenize(q: str) -> List[str]:
    return [t for t in TOKEN_SPLIT.split(q) if t]

# ---------------------------
# Matching (keywords + paraphrases)
# ---------------------------
def kw_score(query_norm: str, item: Dict[str, Any]) -> int:
    score = 0
    kws = (item.get("trigger") or {}).get("keywords") or []
    for kw in kws:
        kw_norm = kw.lower().strip()
        if kw_norm and kw_norm in query_norm:
            score += 1
    return score

def paraphrase_score(query_norm: str, item: Dict[str, Any]) -> float:
    """
    Semplice overlap di token (Jaccard) con ogni paraphrase; si prende il massimo.
    """
    paras = item.get("paraphrases") or []
    if not paras:
        return 0.0
    q_tokens = set(tokenize(query_norm))
    best = 0.0
    for p in paras:
        p_norm = normalize_query(p)
        p_tokens = set(tokenize(p_norm))
        inter = len(q_tokens & p_tokens)
        union = max(1, len(q_tokens | p_tokens))
        jaccard = inter / union
        if jaccard > best:
            best = jaccard
    return best

def pick_best_item(query: str, family_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    qn = normalize_query(query)
    items = family_data.get("items") or []
    best: Tuple[float, Dict[str, Any]] | None = None

    for it in items:
        peso = float(((it.get("trigger") or {}).get("peso")) or 0.9)
        s_kw = kw_score(qn, it)
        s_pp = paraphrase_score(qn, it)
        score = (s_kw * 1.0 + s_pp * 0.8) * peso
        if best is None or score > best[0]:
            best = (score, it)

    if best and best[0] > 0:
        return best[1]
    return None

# ---------------------------
# Policy: canonical / dynamic
# ---------------------------
def effective_policy(
    cfg: Dict[str, Any], family: Dict[str, Any], item: Dict[str, Any]
) -> Dict[str, Any]:
    g = ((cfg.get("admin") or {}).get("response_policy")) or {}
    g_mode = g.get("mode", "canonical")
    g_vs = g.get("variant_selection", "round_robin")

    f = (family.get("response_policy")) or {}
    f_mode = f.get("mode", "inherit")
    f_vs = f.get("variant_selection", "inherit")
    f_lock = bool(f.get("lock_to_item_core", False))

    i = (item.get("response_policy")) or {}
    i_mode = i.get("mode", "inherit")
    i_lock = bool(i.get("lock_to_core", False))
    i_cindex = int(i.get("canonical_index", 0))

    mode = i_mode if i_mode != "inherit" else (f_mode if f_mode != "inherit" else g_mode)
    variant_sel = f_vs if f_vs != "inherit" else g_vs
    lock_core = i_lock or f_lock

    return {
        "mode": mode,
        "variant_selection": variant_sel,
        "lock_to_core": lock_core,
        "canonical_index": i_cindex,
    }

def select_response_text(
    item: Dict[str, Any], policy: Dict[str, Any], family_name: str
) -> Tuple[str, int, str]:
    """
    Ritorna (testo, variant_index, mode_usato)
    """
    mode = policy["mode"]
    lock_core = policy["lock_to_core"]
    variants: List[str] = item.get("response_variants") or []
    core = (item.get("core_sentence") or "").strip()

    if lock_core:
        return (core, -1, f"{mode}+lock_core")

    if mode == "canonical":
        idx = int(policy.get("canonical_index", 0))
        if variants:
            idx = max(0, min(idx, len(variants) - 1))
            return (variants[idx], idx, mode)
        return (core or item.get("domanda", ""), -1, mode)

    # dynamic
    if not variants:
        return (core or item.get("domanda", ""), -1, mode)

    vs = policy.get("variant_selection", "round_robin")
    item_key = f"{family_name}:{item.get('id','UNKNOWN')}"
    if vs == "random":
        idx = _rng.randint(0, len(variants) - 1)
    else:
        c = _rr_counters.get(item_key, -1) + 1
        _rr_counters[item_key] = c
        idx = c % len(variants)
    return (variants[idx], idx, mode)

# ---------------------------
# MODELLI Pydantic (fix per FastAPI/Python 3.13)
# ---------------------------
class AskPayload(BaseModel):
    q: str
    family: Optional[str] = "VCEM"

# ---------------------------
# API
# ---------------------------
@app.get("/")
def root():
    return {"ok": True, "app": "Tecnaria Sinapsi — Q/A", "families_dir": str(DATA_DIR)}

@app.post("/api/ask", response_model=None)
async def api_ask(payload: AskPayload):
    q = (payload.q or "").strip()
    family = (payload.family or "VCEM").upper()

    if not q:
        raise HTTPException(status_code=400, detail="Campo 'q' mancante o vuoto.")

    cfg = get_config()
    fam = load_family(family)

    item = pick_best_item(q, fam)
    if not item:
        return {
            "ok": False,
            "family": family,
            "q": q,
            "text": "Nessuna risposta trovata per questa domanda.",
        }

    pol = effective_policy(cfg, fam, item)
    text, vidx, used_mode = select_response_text(item, pol, family)

    return {
        "ok": True,
        "family": family,
        "id": item.get("id"),
        "mode": used_mode,
        "variant_index": vidx,
        "text": text,
    }

@app.post("/admin/policy/mode", response_model=None)
async def set_policy_mode(
    request: Request,
    mode: str,
    x_admin_token: Optional[str] = Header(default=None, convert_underscores=False),
):
    """
    Cambia modalità (canonical/dynamic) a runtime.
    Richiede X-Admin-Token (hash in config.runtime.json).
    """
    allowed = {"canonical", "dynamic"}
    if mode not in allowed:
        raise HTTPException(status_code=400, detail=f"mode deve essere in {allowed}")

    cfg = get_config()
    sec = ((cfg.get("admin") or {}).get("security")) or {}
    expected_hash = (sec.get("admin_token_sha256") or "").strip()

    if not expected_hash:
        raise HTTPException(status_code=403, detail="Admin token non configurato.")
    if not x_admin_token:
        raise HTTPException(status_code=401, detail="X-Admin-Token mancante.")

    got_hash = hashlib.sha256(x_admin_token.encode("utf-8")).hexdigest()
    if got_hash != expected_hash:
        raise HTTPException(status_code=403, detail="Token non valido.")

    # aggiorna RAM
    admin = cfg.setdefault("admin", {})
    pol = admin.setdefault("response_policy", {})
    pol["mode"] = mode

    # persisti su file (best-effort)
    try:
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return {"ok": True, "mode": mode}
