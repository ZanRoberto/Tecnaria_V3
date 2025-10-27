# app.py
# Tecnaria_V3 — App FastAPI completa con Q/A GOLD
# - Carica dataset GOLD da static/data/
# - Espone /qa/search (top-k) e /qa/ask (best match)
# - Nessun requisito extra rispetto a FastAPI/uvicorn già presenti
# - Nessuna dipendenza da percorsi Windows

from __future__ import annotations

import json
import pathlib
from typing import List, Dict, Any, Optional
from functools import lru_cache

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ------------------------------------------------------------------------------
# Config base
# ------------------------------------------------------------------------------
APP_DIR = pathlib.Path(__file__).parent
DATA_DIR = APP_DIR / "static" / "data"

# Nomi "canonici" (se presenti, hanno priorità)
GOLD_FILES = ["ctf_gold.json", "ctl_gold.json", "p560_gold.json"]

# ------------------------------------------------------------------------------
# Modelli Pydantic
# ------------------------------------------------------------------------------
class QAItem(BaseModel):
    qid: Optional[str] = None
    family: Optional[str] = None
    question: str
    answer: str
    tags: Optional[List[str]] = []
    level: Optional[str] = None
    source_hint: Optional[str] = None

class SearchResponse(BaseModel):
    query: str
    count: int
    results: List[QAItem]

class AskResponse(BaseModel):
    query: str
    result: Optional[QAItem] = None
    found: bool

# ------------------------------------------------------------------------------
# App FastAPI
# ------------------------------------------------------------------------------
app = FastAPI(
    title="Tecnaria Q/A Service",
    version="1.0.0",
    description="Ricerca e risposta su dataset GOLD (CTF/CTL/P560) da static/data/."
)

# CORS (aperto: adegua se hai front-end su dominio specifico)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # inserisci il dominio del front-end se vuoi limitarlo
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------------------
# Caricamento dataset (tollerante, no invenzioni)
# ------------------------------------------------------------------------------
def _iter_candidate_files() -> List[pathlib.Path]:
    """Ritorna la lista di file da caricare, in ordine di priorità."""
    candidates: List[pathlib.Path] = []

    # 1) Priorità ai tre nomi canonici, se esistono
    for name in GOLD_FILES:
        p = DATA_DIR / name
        if p.exists() and p.is_file():
            candidates.append(p)

    # 2) Poi qualunque *_gold.json (permette versioni tipo ctf_gold_v2.json)
    for p in sorted(DATA_DIR.glob("*_gold.json")):
        if p not in candidates:
            candidates.append(p)

    # Se non c'è niente, meglio fallire esplicitamente
    if not candidates:
        raise FileNotFoundError(
            f"Nessun dataset GOLD trovato. Attesi almeno uno tra: {', '.join(GOLD_FILES)} "
            f"oppure qualsiasi *_gold.json in {DATA_DIR}"
        )
    return candidates

def _normalize_records(raw: Any) -> List[Dict[str, Any]]:
    """Accetta sia dict con chiave 'items' sia lista pura di item."""
    if isinstance(raw, dict) and "items" in raw and isinstance(raw["items"], list):
        return raw["items"]
    if isinstance(raw, list):
        return raw
    raise ValueError("Formato dataset non valido: atteso {'items':[...]} oppure lista di item.")

@lru_cache(maxsize=1)
def load_gold() -> List[QAItem]:
    items: List[QAItem] = []
    seen_files = set()

    candidates = _iter_candidate_files()
    for p in candidates:
        if p.resolve() in seen_files:
            continue
        seen_files.add(p.resolve())
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        for rec in _normalize_records(data):
            # Mappa campi minimi obbligatori; se mancanti, salta
            q = (rec.get("question") or "").strip()
            a = (rec.get("answer") or "").strip()
            if not q or not a:
                continue
            item = QAItem(
                qid=rec.get("qid"),
                family=rec.get("family"),
                question=q,
                answer=a,
                tags=rec.get("tags") or [],
                level=rec.get("level"),
                source_hint=rec.get("source_hint"),
            )
            items.append(item)

    if not items:
        raise ValueError("Nessun item valido caricato dai dataset GOLD.")
    return items

# ------------------------------------------------------------------------------
# Ranking semplice (lessicale robusto, leggero, deterministico)
# ------------------------------------------------------------------------------
def _score(item: QAItem, ql: str) -> float:
    base = 0.0

    fam = (item.family or "").lower()
    if fam and fam in ql:
        base += 2.0

    # boost sui tag presenti nella query
    for t in (item.tags or []):
        t0 = (t or "").lower().strip()
        if t0 and t0 in ql:
            base += 1.0

    qtxt = (item.question or "").lower()
    atxt = (item.answer or "").lower()

    # match pieno della query nel testo domanda
    if ql and ql in qtxt:
        base += 1.5

    # token-level matching
    tokens = {tok for tok in ql.split() if tok}
    for tok in tokens:
        if tok in qtxt:
            base += 0.40
        if tok in atxt:
            base += 0.20

    return base

def _rank(query: str, k: int = 5) -> List[QAItem]:
    ql = (query or "").lower().strip()
    if not ql:
        return []
    items = load_gold()
    ranked = sorted(items, key=lambda it: _score(it, ql), reverse=True)
    return ranked[: max(1, k)]

# ------------------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------------------
@app.get("/", summary="Health & Info")
def root() -> Dict[str, Any]:
    """Health check + meta."""
    try:
        n = len(load_gold())
        files = [str(p.name) for p in _iter_candidate_files()]
    except Exception as e:
        return {
            "service": "Tecnaria Q/A Service",
            "status": "error",
            "error": str(e),
        }
    return {
        "service": "Tecnaria Q/A Service",
        "status": "ok",
        "items_loaded": n,
        "data_dir": str(DATA_DIR),
        "files": files,
    }

@app.get("/qa/search", response_model=SearchResponse, summary="Restituisce top-k Q/A")
def qa_search(
    q: str = Query(..., min_length=2, description="Testo della ricerca"),
    k: int = Query(5, ge=1, le=25, description="Numero risultati")
) -> SearchResponse:
    try:
        results = _rank(q, k=k)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore durante la ricerca: {e}")
    return SearchResponse(query=q, count=len(results), results=results)

@app.get("/qa/ask", response_model=AskResponse, summary="Risposta singola migliore")
def qa_ask(
    q: str = Query(..., min_length=2, description="Domanda libera")
) -> AskResponse:
    try:
        best = _rank(q, k=1)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore durante la ricerca: {e}")

    if not best:
        return AskResponse(query=q, result=None, found=False)
    return AskResponse(query=q, result=best[0], found=True)

# ------------------------------------------------------------------------------
# Run locale (opzionale). In produzione su Render usa gunicorn/uvicorn worker.
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
