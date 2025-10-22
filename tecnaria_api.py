# tecnaria_api.py — entrypoint robusto per Render
from importlib import import_module
from typing import Any

mod = import_module("app")
app: Any = getattr(mod, "app", None)

if app is None:
    try:
        from fastapi import FastAPI
        for name, val in vars(mod).items():
            if isinstance(val, FastAPI):
                app = val
                break
    except Exception:
        app = None

if app is None:
    create_app = getattr(mod, "create_app", None)
    if callable(create_app):
        app = create_app()

if app is None:
    from fastapi import FastAPI
    app = FastAPI(title="Tecnaria (fallback)")
    @app.get("/")
    def _fallback_root():
        return {
            "ok": False,
            "error": "Nessuna variabile FastAPI 'app' trovata in app.py",
            "hint": "Definisci 'app = FastAPI(...)' in app.py oppure esporta create_app()."
        }
    @app.get("/health")
    def _fallback_health():
        return {"ok": False, "reason": "missing app in app.py"}
