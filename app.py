import os
import json
import random
from pathlib import Path
from typing import Dict, Any, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import HTMLResponse, FileResponse
from starlette.staticfiles import StaticFiles
from difflib import SequenceMatcher

# ============================================================
# PATH BASE E STATIC
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
FAMILIES_DIR = STATIC_DIR / "data"

app = FastAPI(title="Tecnaria Sinapsi — Q/A")

# CORS (aperto, puoi restringere ai tuoi domini se vuoi)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Cache famiglie
_families_cache: Dict[str, Dict[str, Any]] = {}

# ============================================================
# UTILITÀ LETTURA JSON
# ============================================================

def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"File non trovato: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def load_family(name: str) -> Dict[str, Any]:
    """
    Carica una famiglia (VCEM, CTF, CTCEM, CTL, CTL_MAXI, P560, DIAPASON, COMM).
    Si aspetta file: static/data/<name>.json
    """
    if name in _families_cache:
        return _families_cache[name]

    path = FAMILIES_DIR / f"{name}.json"
    data = _read_json(path)

    # Normalizza
    if data.get("family") != name:
        data["family"] = name

    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError(f"'items' mancante o non lista in {path}")
    data["items"] = items

    _families_cache[name] = data
    return data

# ============================================================
# MATCHING LOGIC (TECNARIA_GOLD)
# ============================================================

STOPWORDS = {
    "i","il","la","le","un","una","uno",
    "di","dei","degli","del","della",
    "per","con","su","nel","nella","in",
    "che","come","posso","può","puoi","si","sono",
    "uso","usare","si possono","si può","devo","quando",
    "riguardo","riferimento","riferimento ai",
    "tecnaria", "vcem", "ctf", "ctcem", "ctl", "p560", "diapason"
}

def _norm(text: str) -> str:
    return " ".join(str(text).lower().strip().split())

def _tokens(text: str) -> set:
    return {t for t in _norm(text).split() if t and t not in STOPWORDS}

def match_item(user_q: str, family_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Trova l'item migliore usando:
    - campi 'q' e/o 'questions' (stringa o lista)
    - similarità fuzzy + overlap parole chiave
    - penalizza trigger ridicoli/moncherini
    """
    items = family_data.get("items", [])
    if not items:
        return None

    qn = _norm(user_q)
    q_tokens = _tokens(user_q)

    best_item = None
    best_score = 0.0

    for item in items:
        triggers: List[str] = []

        # supporta 'q'
        q_field = item.get("q")
        if isinstance(q_field, list):
            triggers.extend(q_field)
        elif isinstance(q_field, str):
            triggers.append(q_field)

        # supporta 'questions'
        qs_field = item.get("questions")
        if isinstance(qs_field, list):
            triggers.extend(qs_field)
        elif isinstance(qs_field, str):
            triggers.append(qs_field)

        if not triggers:
            continue

        for t in triggers:
            tn = _norm(t)
            if not tn:
                continue

            t_tokens = _tokens(t)

            base = SequenceMatcher(None, qn, tn).ratio()

            if t_tokens:
                overlap = len(q_tokens & t_tokens) / max(len(t_tokens), 1)
            else:
                overlap = 0.0

            bonus = 0.0
            if qn == tn:
                bonus += 0.25
            elif tn in qn or qn in tn:
                bonus += 0.15

            score = 0.65 * base + 0.35 * overlap + bonus

            # penalizza trigger troppo corti (evita moncherini tipo 'Se il cls è duro...')
            if len(tn) < 40:
                score -= 0.08

            if score > best_score:
                best_score = score
                best_item = item

    # Soglia minima: sotto meglio nessuna risposta
    if best_item and best_score >= 0.55:
        return best_item

    return None

def pick_response(item: Dict[str, Any]) -> str:
    """
    Restituisce risposta GOLD:
    - cerca response_variants 'ricche' (min 140 caratteri)
    - se ci sono, ne sceglie una random -> dinamico serio
    - se non ci sono, usa canonical
    - se manca tutto, messaggio neutro
    """
    canonical = (item.get("canonical") or "").strip()
    variants = item.get("response_variants") or []

    rich_variants: List[str] = []
    if isinstance(variants, list):
        for v in variants:
            if not isinstance(v, str):
                continue
            txt = v.strip()
            # scarta varianti ridicole/corte
            if len(txt) >= 140:
                rich_variants.append(txt)

    if rich_variants:
        return random.choice(rich_variants)

    if canonical:
        return canonical

    # fallback su varianti, anche se non ricche (ma solo se proprio non c'è altro)
    fallback = [v.strip() for v in variants if isinstance(v, str) and v.strip()]
    if fallback:
        return random.choice(fallback)

    return "Risposta non disponibile."

# ============================================================
# MODELLI API
# ============================================================

class AskPayload(BaseModel):
    q: str
    family: str

# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    """
    Serve index.html dalla cartella static, se presente.
    """
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse("<h1>Tecnaria Sinapsi — Q/A</h1>")

@app.get("/api/config")
async def api_config():
    return {
        "ok": True,
        "app": "Tecnaria Sinapsi — Q/A",
        "families_dir": str(FAMILIES_DIR)
    }

@app.post("/api/ask")
async def api_ask(payload: AskPayload):
    user_q = (payload.q or "").strip()
    family = (payload.family or "").strip()

    if not user_q:
        return {
            "ok": False,
            "family": family,
            "q": payload.q,
            "text": "Domanda vuota."
        }

    try:
        fam = load_family(family)
    except FileNotFoundError:
        return {
            "ok": False,
            "family": family,
            "q": user_q,
            "text": f"Famiglia '{family}' non disponibile."
        }
    except Exception:
        return {
            "ok": False,
            "family": family,
            "q": user_q,
            "text": "Errore nella lettura dei dati di questa famiglia."
        }

    item = match_item(user_q, fam)

    if not item:
        return {
            "ok": False,
            "family": family,
            "q": user_q,
            "text": "Nessuna risposta trovata per questa domanda."
        }

    text = pick_response(item)

    return {
        "ok": True,
        "family": family,
        "id": item.get("id"),
        "mode": "dynamic",
        "text": text
    }
