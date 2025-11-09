from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ============================================================
# PERCORSI
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = STATIC_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.runtime.json"

# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Tecnaria Sinapsi — Q/A",
    version="GOLD-TEC-2025-11-09"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restringere in produzione se serve
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ============================================================
# CACHE (in RAM per processo)
# ============================================================

_families_cache: Dict[str, Dict[str, Any]] = {}
_config_cache: Dict[str, Any] = {}

# ============================================================
# MODELLI INPUT
# ============================================================

class AskPayload(BaseModel):
    q: str
    family: Optional[str] = None

# ============================================================
# UTILITY LETTURA CONFIG
# ============================================================

def _read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def load_config() -> Dict[str, Any]:
    """
    Legge config.runtime.json se presente.
    Se manca, usa default GOLD: dynamic + longest.
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
    # rilettura sempre dal file: se cambi config in produzione, si aggiorna
    return load_config()

# ============================================================
# GESTIONE FAMIGLIE
# ============================================================

def list_families() -> List[str]:
    """
    Ritorna i nomi delle famiglie attive.
    Regole:
    - tutti i *.json in DATA_DIR
    - esclusi config.runtime.json
    - esclusi file marcati .off.json / .bak.json
    """
    if not DATA_DIR.exists():
        return []

    fams: List[str] = []
    for p in DATA_DIR.glob("*.json"):
        name = p.name
        low = name.lower()
        if "config.runtime" in low:
            continue
        if low.endswith(".off.json") or low.endswith(".bak.json"):
            continue
        fams.append(p.stem.upper())

    return sorted(set(fams))

def load_family(family_name: str) -> Dict[str, Any]:
    """
    Carica una famiglia (GOLD o legacy tollerata):
    - static/data/{FAMILY}.json

    Supporta:
    - formato GOLD: { "family": "...", "items": [...] }
    - formato legacy: [ {...}, {...} ]
    """
    global _families_cache
    key = family_name.upper()

    if key in _families_cache:
        return _families_cache[key]

    path = DATA_DIR / f"{key}.json"
    data = _read_json(path)

    if isinstance(data, dict):
        data.setdefault("family", key)
        items = data.get("items")
        if not isinstance(items, list):
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

    # assegna ID mancanti
    for i, item in enumerate(data.get("items", [])):
        if isinstance(item, dict) and "id" not in item:
            item["id"] = f"{key}-{i:04d}"

    _families_cache[key] = data
    return data

def get_family_items(family_name: str) -> List[Dict[str, Any]]:
    fam = load_family(family_name)
    items = fam.get("items", [])
    return [it for it in items if isinstance(it, dict)]

# ============================================================
# NORMALIZZAZIONE TESTO / LINGUA
# ============================================================

SINONIMI_MAP = {
    r"\bsparachiodi\b": "p560",
    r"\bchiodatrice\b": "p560",
    r"\bpistola\b": "p560",
    r"\bspit\b": "p560",
    r"\bperni?\b": "chiodi idonei tecnaria",  # output normalizzato
}

FAMILY_ALIASES = {
    "P560": ["p560", "sparachiodi", "pistola", "spit"],
    "CTF": ["ctf"],
    "CTL": ["ctl"],
    "CTL_MAXI": ["ctl maxi", "ctlmaxi", "maxi ctl"],
    "VCEM": ["vcem"],
    "CTCEM": ["ctcem"],
    "DIAPASON": ["diapason"],
    "COMM": ["comm", "commerciale", "info tecnaria"],
}

def normalize(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii", "ignore")
    text = text.lower()
    for pattern, repl in SINONIMI_MAP.items():
        text = re.sub(pattern, repl, text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def detect_lang(q: str) -> str:
    ql = q.lower()
    if any(w in ql for w in [" the ", " connector ", "steel", "beam"]):
        return "en"
    if any(w in ql for w in [" que ", " hormigon", "conectores"]):
        return "es"
    if any(w in ql for w in [" beton", "connecteur"]):
        return "fr"
    if any(w in ql for w in [" beton", "verbinder", "stahl"]):
        return "de"
    return "it"

# ============================================================
# TRIGGER DAI BLOCCHI
# ============================================================

def extract_trigger_texts(item: Dict[str, Any]) -> List[str]:
    out: List[str] = []

    # liste di domande / parafrasi
    for key in ("questions", "paraphrases", "triggers", "variants"):
        v = item.get(key)
        if isinstance(v, list):
            for e in v:
                if isinstance(e, str) and e.strip():
                    out.append(e.strip())

    # singoli campi
    for key in ("q", "question", "domanda", "title", "label", "context"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())

    # tags
    tags = item.get("tags")
    if isinstance(tags, list):
        for t in tags:
            if isinstance(t, str) and t.strip():
                out.append(t.strip())

    # canonical / answer_it come supporto semantico
    if isinstance(item.get("canonical"), str):
        out.append(item["canonical"])
    if isinstance(item.get("answer_it"), str):
        out.append(item["answer_it"])

    return out

# ============================================================
# MATCHING / SCORING
# ============================================================

def jaccard(a: List[str], b: List[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    if inter == 0:
        return 0.0
    return inter / float(len(sa | sb))

def detect_family_mentions(nq: str) -> List[str]:
    found: List[str] = []
    for fam, aliases in FAMILY_ALIASES.items():
        for a in aliases:
            if a in nq:
                found.append(fam)
                break
    return found

def score_item(query: str, item: Dict[str, Any], family: str) -> float:
    """
    Scoring robusto:
    - jaccard tra query e triggers
    - match forte su substring
    - boost se la query nomina esplicitamente la famiglia corretta
    - penalità se nomina un'altra famiglia
    """
    nq = normalize(query)
    if not nq:
        return 0.0

    q_tokens = nq.split()
    triggers = extract_trigger_texts(item)
    if not triggers:
        return 0.0

    best = 0.0
    for t in triggers:
        nt = normalize(t)
        if not nt:
            continue

        if nq == nt:
            return 1.0

        if nq in nt or nt in nq:
            if 0.9 > best:
                best = 0.9

        t_tokens = nt.split()
        if not t_tokens:
            continue

        j = jaccard(q_tokens, t_tokens)
        if j > best:
            best = j

    mentioned = detect_family_mentions(nq)
    fam = family.upper()

    if mentioned:
        if fam in mentioned:
            # parla proprio di questa famiglia → alza
            best = max(best, 0.85)
        else:
            # cita altra famiglia → riduci rilevanza
            best *= 0.6

    return float(best)

# ============================================================
# POLICY & COSTRUZIONE TESTO
# ============================================================

def _merge_policy(cfg: Dict[str, Any]) -> Tuple[str, str]:
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
    """
    Supporta:
    - GOLD: canonical + response_variants (lista)
    - legacy: response_variants come dict → usa tutti i valori
    - legacy: answer_it come canonical/extra
    - altri campi testuali come extra
    """
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
    elif isinstance(rv, dict):
        for v in rv.values():
            if isinstance(v, str) and v.strip():
                variants.append(v.strip())

    extras: List[str] = []

    # answer_it: se non c'è canonical, lo usa; altrimenti extra
    answer_it = item.get("answer_it")
    if isinstance(answer_it, str) and answer_it.strip():
        if not canonical:
            canonical = answer_it.strip()
        else:
            extras.append(answer_it.strip())

    # altri campi legacy
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
    rich = [t for t in texts if len(t) >= 80]
    base = rich if rich else texts
    return max(base, key=len)

def pick_response_text(item: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[str]:
    """
    Applica policy GOLD:
    - dynamic + longest → sempre la risposta più ricca disponibile
    - compatibile con GOLD e legacy CTL/CTF
    """
    mode, variant_selection = _merge_policy(cfg)
    src = _get_item_text_sources(item)

    canonical = src["canonical"]
    variants = src["variants"]
    extras = src["extras"]

    # modalita canonical: usata solo se forzata da config
    if mode == "canonical":
        if canonical:
            return canonical
        return _pick_longest(variants + extras)

    # modalita dynamic: scegli il massimo
    candidate_texts: List[str] = []

    if variants:
        if variant_selection == "first":
            candidate_texts.append(variants[0])
        else:
            v = _pick_longest(variants)
            if v:
                candidate_texts.append(v)

    if canonical:
        candidate_texts.append(canonical)

    candidate_texts.extend(extras)
    candidate_texts = [t for t in candidate_texts if t and t.strip()]

    if not candidate_texts:
        return None

    return _pick_longest(candidate_texts)

# ============================================================
# TROVA MIGLIOR BLOCCO
# ============================================================

def find_best_item(
    question: str,
    family_hint: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], float]:
    """
    Se family_hint è valorizzata → cerca solo lì.
    Se no → scorri tutte le famiglie attive.
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
            s = score_item(question, it, fam)
            if s > best_score:
                best_score = s
                best_item = it
                best_family = fam

    return best_item, best_family, float(best_score)

# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/api/config")
def api_config():
    cfg = get_config()
    mode, vs = _merge_policy(cfg)
    return {
        "ok": True,
        "app": "Tecnaria Sinapsi — Q/A",
        "version": app.version,
        "families_dir": str(DATA_DIR),
        "families": list_families(),
        "policy": {
            "mode": mode,
            "variant_selection": vs,
        },
    }

@app.post("/api/ask")
async def api_ask(payload: AskPayload):
    q = (payload.q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Campo 'q' obbligatorio.")

    family = payload.family.upper().strip() if payload.family else None
    cfg = get_config()

    item, fam, score = find_best_item(q, family)

    # soglia minima per evitare abbinamenti fuori fuoco
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
        # caso limite: blocco senza testo valido
        return {
            "ok": False,
            "q": q,
            "family": fam,
            "lang": detect_lang(q),
            "id": item.get("id"),
            "score": float(score),
            "text": "Blocco trovato ma senza risposta valida."
        }

    # regola lessicale dura: mai "perni"
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

# ============================================================
# ROOT / HEALTHCHECK
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """
    Se esiste static/index.html → serve l'interfaccia.
    Altrimenti pagina di stato semplice.
    """
    index_html = STATIC_DIR / "index.html"
    if index_html.exists():
        return HTMLResponse(index_html.read_text(encoding="utf-8"))
    cfg = get_config()
    mode, vs = _merge_policy(cfg)
    html = f"""
    <h1>Tecnaria Sinapsi — Q/A</h1>
    <p>Backend attivo.</p>
    <p>Policy: mode={mode}, variant_selection={vs}</p>
    <p>Famiglie attive: {', '.join(list_families())}</p>
    """
    return HTMLResponse(html, status_code=200)

# ============================================================
# LOG AVVIO (Render friendly)
# ============================================================

@app.on_event("startup")
async def on_startup():
    try:
        cfg = get_config()
        mode, vs = _merge_policy(cfg)
        fams = list_families()
        print("🟢 SINAPSI GOLD AVVIATA")
        print(f"📂 DATA_DIR: {DATA_DIR}")
        print(f"📦 Famiglie attive: {fams}")
        print(f"⚙️  Policy: mode={mode}, variant_selection={vs}")
    except Exception as e:
        print("🔴 ERRORE STARTUP SINAPSI:", e)
