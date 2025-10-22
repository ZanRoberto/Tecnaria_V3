# tecnaria_api.py — ENTRYPOINT SHIM: avvia sempre l'API anche se app.py non espone 'app'
from importlib import import_module
from typing import Any, Dict
from fastapi import FastAPI
from pydantic import BaseModel
import time

mod = import_module("app")  # importa il tuo app.py (non serve che esponga 'app')
_json_bag = getattr(mod, "JSON_BAG", {}) or {}
_faq_rows = 0
for cand in ("FAQ_ROWS", "FAQ_ITEMS", "FAQ"):
    v = getattr(mod, cand, None)
    if v is not None:
        try:
            _faq_rows = len(v)
            break
        except Exception:
            pass
_data_dir = getattr(mod, "DATA_DIR", None)

# 1) Usa 'app' se esiste ed è una FastAPI
app: Any = getattr(mod, "app", None)
if not isinstance(app, FastAPI):
    app = FastAPI(title="Tecnaria_V3 (shim)")

# 2) Root & health sempre disponibili
@app.get("/")
def _root():
    try:
        return {
            "app": "Tecnaria_V3",
            "status": "ok",
            "data_dir": str(_data_dir) if _data_dir else None,
            "json_loaded": list(_json_bag.keys()),
            "faq_rows": _faq_rows,
            "shim": True
        }
    except Exception:
        return {"app": "Tecnaria_V3", "status": "ok", "shim": True}

@app.get("/health")
def _health():
    return {
        "ok": True,
        "json_loaded": list(_json_bag.keys()),
        "faq_rows": _faq_rows,
        "shim": True
    }

# 3) /api/ask — se in app.py c'è 'intent_route', lo usiamo; altrimenti messaggio chiaro
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

_intent_route = getattr(mod, "intent_route", None)

@app.post("/api/ask", response_model=AskOut)
def api_ask(body: AskIn) -> AskOut:
    t0 = time.time()
    if callable(_intent_route):
        try:
            routed: Dict[str, Any] = _intent_route(body.q or "")
            ms = int((time.time() - t0) * 1000)
            return AskOut(
                ok=True,
                match_id=str(routed.get("match_id") or routed.get("id") or "<NULL>"),
                ms=ms,
                text=str(routed.get("text") or ""),
                html=str(routed.get("html") or ""),
                lang=routed.get("lang"),
                family=routed.get("family"),
                intent=routed.get("intent"),
                source=str(routed.get("source") or "shim"),
                score=routed.get("score"),
            )
        except Exception as e:
            ms = int((time.time() - t0) * 1000)
            return AskOut(ok=False, match_id="<ERROR>", ms=ms, text=f"Shim error: {e}")
    # fallback se manca intent_route
    ms = int((time.time() - t0) * 1000)
    return AskOut(
        ok=False,
        match_id="<MISSING_INTENT_ROUTE>",
        ms=ms,
        text="In app.py non trovo 'intent_route(q)'. Aggiungilo o esporta 'app'."
    )
