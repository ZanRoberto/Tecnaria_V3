from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# ============================================================
# PATH & APP
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = STATIC_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.runtime.json"

app = FastAPI(
    title="Tecnaria Sinapsi — Q/A",
    version="GOLD-TEC-2025-11-09"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restringere in produzione
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ============================================================
# CACHE
# ============================================================

_families_cache: Dict[str, Dict[str, Any]] = {}
_config_cache: Dict[str, Any] = {}

# ============================================================
# MODELLI
# ============================================================

class AskPayload(BaseModel):
    q: str
    family: Optional[str] = None

# ============================================================
# UTILITY I/O
# ============================================================

def _read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def load_config() -> Dict[str, Any]:
    """
    Legge config.runtime.json se presente.
    Se manca, usa default: dynamic + longest.
    """
    global _config_cache

    try:
        cfg = _read_json(CONFIG_PATH)
    except FileNotFoundError:
        cfg = {
            "admin": {
                "response_policy": {
                    "mode": "dynamic",
                    "variant_selection": "longest",
                    "variant_seed": 20251106
                },
                "security": {
                    "admin_token_sha256": ""
                }
            }
        }

    _config_cache = cfg
    return cfg

def get_config() -> Dict[str, Any]:
    # rileggiamo ogni volta: così puoi modificare config.runtime.json senza redeploy
    return load_config()

# ============================================================
# FAMILY HANDLING
# ============================================================

def list_families() -> List[str]:
    """
    Lista delle famiglie attive:
    - tutti i .json in DATA_DIR
    - esclude config.runtime.json
    - esclude file disattivati (.OFF, .off, .bak, ecc.)
    """
    if not DATA_DIR.exists():
        return []

    fams: List[str] = []
    for p in DATA_DIR.glob("*.json"):
        name = p.name

        if name.lower() == "config.runtime.json":
            continue
        if name.lower().endswith(".off.json"):
            continue
        if name.lower().endswith(".bak.json"):
            continue

        fams.append(p.stem.upper())

    return sorted(set(fams))


def load_family(family_name: str) -> Dict[str, Any]:
    """
    Carica una famiglia GOLD:
    - static/data/{FAMILY}.json
    Formati supportati:
    - GOLD: { "family": "...", "items": [...], "meta": {...} }
    - legacy (fallback): lista di item → wrappata in items[]
    """
    global _families_cache

    key = family_name.upper()
    if key in _families_cache:
        return _families_cache[key]

    path = DATA_DIR / f"{key}.json"
    data = _read_json(path)

    # normalizzazione formati
    if isinstance(data, dict):
        data.setdefault("family", key)
        items = data.get("items")
        if not isinstance(items, list):
            # fallback: se c'è "data" come lista
            if isinstance(data.get("data"), list):
                data["items"] = data["data"]
            else:
                data["items"] = []
    elif isinstance(data, list):
        data = {
            "family": key,
            "items": data,
        }
    else:
        raise HTTPException(status_code=500, detail=f"Formato JSON non valido per famiglia {family_name}")

    # assegna id mancanti
    for i, item in enumerate(data.get("items", [])):
        if isinstance(item, dict) and "id" not in item:
            item["id"] = f"{key}-{i:04d}"

    _families_cache[key] = data
    return data

def get_family_items(family_name: str) -> List[Dict[str, Any]]:
    fam = load_family(family_name)
    items = fam.get("items", [])
    return [b for b in items if isinstance(b, dict)]

# ============================================================
# NORMALIZZAZIONE & LINGUA
# ============================================================

SINONIMI_MAP = {
    r"\bsparachiodi\b": "p560",
    r"\bchiodatrice\b": "p560",
    r"\bpistola\b": "p560",
    r"\bspit\b": "p560",
    r"\bperno\b": "chiodo idoneo tecnaria",  # MAI 'perni' in output
    r"\bcls\b": "calcestruzzo",
}

def normalize(text: str) -> str:
    if not text:
        return ""
    # rimuovo accenti solo se serve per matching; qui base ascii va bene
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii", "ignore")
    text = text.lower()
    for pattern, repl in SINONIMI_MAP.items():
        text = re.sub(pattern, repl, text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def detect_lang(q: str) -> str:
    ql = q.lower()
    if any(w in ql for w in [" the ", " connector ", "beam", "steel"]):
        return "en"
    if any(w in ql for w in [" que ", " hormigón", "conectores"]):
        return "es"
    if any(w in ql for w in [" béton", "connecteur"]):
        return "fr"
    if any(w in ql for w in ["verbinder", "beton", "stahl"]):
        return "de"
    return "it"

# ============================================================
# MATCHING
# ============================================================

def extract_trigger_texts(item: Dict[str, Any]) -> List[str]:
    """
    Estrae tutte le possibili frasi di innesco da un item GOLD:
    - questions / tags / canonical / eventuali campi legacy.
    """
    out: List[str] = []

    # domande classiche
    for key in ("questions", "paraphrases", "triggers", "variants"):
        v = item.get(key)
        if isinstance(v, list):
            for e in v:
                if isinstance(e, str) and e.strip():
                    out.append(e.strip())

    # chiavi singole eventuali
    for key in ("q", "question", "domanda", "title", "label"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())

    # tags come micro trigger
    tags = item.get("tags")
    if isinstance(tags, list):
        for t in tags:
            if isinstance(t, str) and t.strip():
                out.append(t.strip())

    # canonical come booster semantico
    canonical = item.get("canonical")
    if isinstance(canonical, str) and canonical.strip():
        out.append(canonical.strip())

    return out

def jaccard(a: List[str], b: List[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    if inter == 0:
        return 0.0
    return inter / len(sa | sb)

def score_item(query: str, item: Dict[str, Any]) -> float:
    nq = normalize(query)
    if not nq:
        return 0.0

    triggers = extract_trigger_texts(item)
    if not triggers:
        return 0.0

    q_tokens = nq.split()
    best = 0.0

    for t in triggers:
        nt = normalize(t)
        if not nt:
            continue

        # match forte: substring
        if nq in nt or nt in nq:
            return 1.0

        t_tokens = nt.split()
        if not t_tokens:
            continue

        j = jaccard(q_tokens, t_tokens)
        if j > best:
            best = j

    return float(best)

# ============================================================
# POLICY & SELEZIONE TESTO
# ============================================================

def _merge_policy(cfg: Dict[str, Any]) -> Tuple[str, str]:
    """
    Legge la policy globale da config.runtime.json.
    mode: canonical | dynamic
    variant_selection: longest | first
    (niente random, niente cagate)
    """
    admin = (cfg.get("admin") or {})
    pol = (admin.get("response_policy") or {})

    mode = pol.get("mode", "dynamic")
    if mode not in ("canonical", "dynamic"):
        mode = "dynamic"

    vs = pol.get("variant_selection", "longest")
    if vs not in ("longest", "first"):
        vs = "longest"

    return mode, vs

def _get_item_text_sources(item: Dict[str, Any]) -> Dict[str, Any]:
    canonical = item.get("canonical")
    if isinstance(canonical, str):
        canonical = canonical.strip()
    else:
        canonical = None

    variants: List[str] = []
    rv = item.get("response_variants")
    if isinstance(rv, list):
        for v in rv:
            if isinstance(v, str) and v.strip():
                variants.append(v.strip())

    # legacy support minimale
    extras: List[str] = []
    for key in ("answer", "risposta", "text", "content"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            extras.append(v.strip())

    answers = item.get("answers")
    if isinstance(answers, dict):
        for v in answers.values():
            if isinstance(v, str) and v.strip():
                extras.append(v.strip())

    return {
        "canonical": canonical,
        "variants": variants,
        "extras": extras,
    }

def _pick_longest(texts: List[str]) -> Optional[str]:
    if not texts:
        return None
    # preferisci testi "ricchi"
    rich = [t for t in texts if len(t) >= 80]
    base = rich if rich else texts
    return max(base, key=len)

def pick_response_text(item: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[str]:
    """
    Applica la policy globale GOLD:
    - mode: dynamic → usa sempre il massimo disponibile
    - variant_selection: longest → sceglie la variante più completa
    """
    mode, variant_selection = _merge_policy(cfg)
    src = _get_item_text_sources(item)

    canonical = src["canonical"]
    variants = src["variants"]
    extras = src["extras"]

    # CANONICAL
    if mode == "canonical":
        # priorità canonical, se c'è
        if canonical:
            return canonical
        # altrimenti longest tra variants + extras
        return _pick_longest(variants + extras)

    # DYNAMIC: scegli sempre il massimo disponibile
    if variants:
        if variant_selection == "first":
            return variants[0]
        # longest
        return _pick_longest(variants)

    # fallback se non ci sono variants
    if canonical and extras:
        return _pick_longest([canonical] + extras)
    if canonical:
        return canonical
    if extras:
        return _pick_longest(extras)

    return None

# ============================================================
# FIND BEST ITEM
# ============================================================

def find_best_item(
    question: str,
    family_hint: Optional[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[str], float]:
    """
    Se family_hint è valorizzata → cerca solo lì.
    Se no → scorri tutte le famiglie GOLD attive.
    """
    if family_hint:
        families = [family_hint.upper()]
    else:
        families = list_families()

    best_item: Optional[Dict[str, Any]] = None
    best_family: Optional[str] = None
    best_score: float = 0.0

    for fam in families:
        try:
            items = get_family_items(fam)
        except FileNotFoundError:
            continue

        for it in items:
            s = score_item(question, it)
            if s > best_score:
                best_score = s
                best_item = it
                best_family = fam

    return best_item, best_family, best_score

# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/api/config")
def api_config():
    cfg = get_config()
    pol = (cfg.get("admin") or {}).get("response_policy", {})
    return {
        "ok": True,
        "app": "Tecnaria Sinapsi — Q/A",
        "version": app.version,
        "families_dir": str(DATA_DIR),
        "families": list_families(),
        "policy": pol,
    }

@app.post("/api/ask")
async def api_ask(payload: AskPayload):
    q = (payload.q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Campo 'q' obbligatorio.")

    family = payload.family.upper().strip() if payload.family else None
    cfg = get_config()

    item, fam, score = find_best_item(q, family)

    # soglia minima per non sparare cose fuori fuoco
    if not item or not fam or score < 0.25:
        return {
            "ok": False,
            "q": q,
            "family": family,
            "lang": detect_lang(q),
            "score": float(score),
            "text": "Nessuna risposta trovata."
        }

    text = pick_response_text(item, cfg)

    if not text:
        return {
            "ok": False,
            "q": q,
            "family": fam,
            "lang": detect_lang(q),
            "id": item.get("id"),
            "score": float(score),
            "text": "Blocco trovato ma senza risposta valida."
        }

    # hard rule: mai 'perni'
    text = re.sub(r"\bperni?\b", "chiodi idonei Tecnaria", text, flags=re.IGNORECASE)

    return {
        "ok": True,
        "q": q,
        "family": fam,
        "lang": detect_lang(q),
        "id": item.get("id"),
        "score": float(score),
        "text": text
    }

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """
    Se esiste static/index.html → serve UI.
    Altrimenti mostra info base.
    """
    index_html = STATIC_DIR / "index.html"
    if index_html.exists():
        return HTMLResponse(index_html.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h1>Tecnaria Sinapsi — Q/A</h1><p>Backend attivo.</p>",
        status_code=200,
    )
