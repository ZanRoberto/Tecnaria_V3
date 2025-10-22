# tecnaria_api.py — SHIM SEMPLICE: espone sempre /, /health, /api/ask (GET+POST)
from importlib import import_module
from typing import Any, Dict
from fastapi import FastAPI, Query
from pydantic import BaseModel
import time

# 1) App sicura
app = FastAPI(title="Tecnaria_V3 (shim)")

# 2) Importa il tuo app.py (se serve per intent_route e contatori)
try:
    mod = import_module("app")
except Exception:
    mod = None

JSON_BAG = {}
FAQ_ROWS = 0
DATA_DIR = None
intent_route = None

if mod:
    JSON_BAG = getattr(mod, "JSON_BAG", {}) or {}
    DATA_DIR = getattr(mod, "DATA_DIR", None)
    # conta FAQ se presenti con vari nomi
    for name in ("FAQ_ROWS", "FAQ_ITEMS", "FAQ"):
        v = getattr(mod, name, None)
        if v is not None:
            try:
                FAQ_ROWS = len(v)
                break
            except Exception:
                pass
    intent_route = getattr(mod, "intent_route", None)

# 3) Root e health sempre disponibili
@app.get("/")
def _root():
    return {
        "app": "Tecnaria_V3 (online)",
        "status": "ok",
        "data_dir": str(DATA_DIR) if DATA_DIR else None,
        "json_loaded": list(JSON_BAG.keys()),
        "faq_rows": FAQ_ROWS
    }

@app.get("/health")
def _health():
    return {"ok": True, "json_loaded": list(JSON_BAG.keys()), "faq_rows": FAQ_ROWS}

# 4) Modelli I/O per /api/ask
class AskIn(BaseModel):
    q: str

class AskOut(BaseModel):
    ok: bool
    match_id: str
    ms: int
    text: str | None = ""
    html: str | None = ""
    lang: str | None = None
    family: str | None = None
    intent: str | None = None
    source: str | None = "shim"
    score: float | int | None = None

def _answer(q: str) -> Dict[str, Any]:
    t0 = time.time()
    if callable(intent_route):
        routed = intent_route(q or "")
        ms = int((time.time() - t0) * 1000)
        return {
            "ok": True,
            "match_id": str(routed.get("match_id") or routed.get("id") or "<NULL>"),
            "ms": ms,
            "text": str(routed.get("text") or ""),
            "html": str(routed.get("html") or ""),
            "lang": routed.get("lang"),
            "family": routed.get("family"),
            "intent": routed.get("intent"),
            "source": str(routed.get("source") or "shim"),
            "score": routed.get("score"),
        }
    # fallback se manca intent_route
    ms = int((time.time() - t0) * 1000)
    return {
        "ok": False,
        "match_id": "<MISSING_INTENT_ROUTE>",
        "ms": ms,
        "text": "In app.py non trovo 'intent_route(q)'. Aggiungilo o usa il blocco router che ti ho dato.",
        "html": ""
    }

# 5) /api/ask disponibile sia in POST che in GET (per test rapidi)
@app.post("/api/ask", response_model=AskOut)
def api_ask_post(body: AskIn):
    return _answer(body.q)

@app.get("/api/ask", response_model=AskOut)
def api_ask_get(q: str = Query("", description="Query test veloce via GET")):
    return _answer(q)
