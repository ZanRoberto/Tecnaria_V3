import os
import json
import random
from pathlib import Path
from difflib import SequenceMatcher
from typing import Dict, Any, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import HTMLResponse, FileResponse
from starlette.staticfiles import StaticFiles

# ============================================================
# PATH BASE E STATIC
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
FAMILIES_DIR = STATIC_DIR / "data"

app = FastAPI(title="Tecnaria Sinapsi — Q/A")

# CORS aperto (puoi restringere ai tuoi domini se vuoi)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serviamo gli asset statici
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Cache in memoria delle famiglie
_families_cache: Dict[str, Dict[str, Any]] = {}


# ============================================================
# UTILS LETTURA JSON
# ============================================================

def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"File non trovato: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_family(name: str) -> Dict[str, Any]:
    """
    Carica una famiglia (VCEM, CTF, CTCEM, ecc.) da FAMILIES_DIR.
    Si aspetta un file chiamato: <family>.json
    """
    if name in _families_cache:
        return _families_cache[name]

    path = FAMILIES_DIR / f"{name}.json"
    data = _read_json(path)

    # Normalizza il campo family
    if data.get("family") != name:
        data["family"] = name

    # Normalizza items come lista
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError(f"'items' mancante o non lista in {path}")
    data["items"] = items

    _families_cache[name] = data
    return data


# ============================================================
# MATCHING LOGIC
# ============================================================

def _norm(text: str) -> str:
    return " ".join(str(text).lower().strip().split())


def match_item(user_q: str, family_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Trova l'item migliore per la domanda:
    - Usa item['q'] (lista o stringa) se presente
    - Usa item['questions'] (lista o stringa) se presente
    - Similarità fuzzy + esatto + contiene
    """

    items = family_data.get("items", [])
    if not items:
        return None

    qn = _norm(user_q)
    best_item = None
    best_score = 0.0

    for item in items:
        triggers: List[str] = []

        # Supporta sia "q" che "questions"
        if "q" in item:
            if isinstance(item["q"], list):
                triggers.extend(item["q"])
            else:
                triggers.append(item["q"])

        if "questions" in item:
            if isinstance(item["questions"], list):
                triggers.extend(item["questions"])
            else:
                triggers.append(item["questions"])

        # Se non hai trigger, salta
        if not triggers:
            continue

        for t in triggers:
            tn = _norm(t)
            if not tn:
                continue

            # esatto
            if qn == tn:
                score = 1.0
            # contenuto
            elif tn in qn or qn in tn:
                score = 0.92
            else:
                # fuzzy
                score = SequenceMatcher(None, qn, tn).ratio()

            if score > best_score:
                best_score = score
                best_item = item

    # Soglia minima: sotto questo meglio "nessuna risposta"
    if best_item and best_score >= 0.55:
        return best_item

    return None


def pick_response(item: Dict[str, Any]) -> str:
    """
    Ritorna una risposta GOLD dinamica:
    - se ci sono response_variants, ne sceglie una a caso
    - altrimenti usa canonical
    """
    variants = item.get("response_variants")
    canonical = (item.get("canonical") or "").strip()

    if isinstance(variants, list) and variants:
        # filtra stringhe non vuote
        vs = [v.strip() for v in variants if isinstance(v, str) and v.strip()]
        if vs:
            return random.choice(vs)

    if canonical:
        return canonical

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
    Se esiste un index.html in /static, serve quello.
    Altrimenti mostra una scritta semplice.
    """
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse("<h1>Tecnaria Sinapsi — Q/A</h1>")


@app.get("/api/config")
async def api_config():
    """
    Ritorna info base per health-check lato client.
    """
    return {
        "ok": True,
        "app": "Tecnaria Sinapsi — Q/A",
        "families_dir": str(FAMILIES_DIR)
    }


@app.post("/api/ask")
async def api_ask(payload: AskPayload):
    """
    Endpoint principale di Q/A.
    Usa:
    - payload.family -> VCEM, CTF, CTCEM, CTL, ...
    - payload.q -> domanda utente in linguaggio naturale
    """
    user_q = payload.q.strip()
    family = payload.family.strip()

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
    except Exception as e:
        # Log interno, risposta neutra verso l'esterno
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
