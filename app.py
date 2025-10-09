import os
import re
import json
import time
import threading
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# =========================
# CONFIGURAZIONE DI BASE
# =========================

# Percorso assoluto forzato (indipendente da Render o cartelle esterne)
BASE_DIR = os.path.dirname(__file__)
DATA_PATH = os.path.join(BASE_DIR, "static", "data", "SINAPSI_GLOBAL_TECNARIA_EXT.json")

I18N_DIR = os.path.join(BASE_DIR, "static", "i18n")
I18N_CACHE_DIR = os.getenv("I18N_CACHE_DIR", os.path.join(BASE_DIR, "static", "i18n-cache"))  # su Render: /tmp/i18n-cache

ALLOWED_LANGS = {"it", "en", "fr", "de", "es"}
DO_NOT_TRANSLATE = [
    "Tecnaria", "CTF", "CTL", "Diapason", "GTS",
    "SPIT P560", "HSBR14", "ETA 18/0447", "ETA 13/0786",
    "mm", "µm"
]

_lock = threading.Lock()

# =========================
# UTILS: FILESYSTEM & JSON
# =========================

def load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERRORE] Impossibile leggere {path}: {e}")
        return {}

# =========================
# INIZIALIZZAZIONE APP
# =========================

app = FastAPI(title="Tecnaria BOT", version="3.5")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# CARICAMENTO KNOWLEDGE BASE
# =========================

KB = {}
_meta = {}

def load_kb() -> Tuple[int, dict]:
    global KB, _meta
    try:
        with _lock:
            data = load_json(DATA_PATH)
            if not data:
                return 0, {}
            KB = {item["id"]: item for item in data.get("qa", [])}
            _meta = data.get("meta", {})
            return len(KB), _meta
    except Exception as e:
        print(f"[ERRORE] KB non caricata: {e}")
        return 0, {}

# Precarica all’avvio
n, _ = load_kb()
print(f"[INIT] Caricate {n} voci KB da {DATA_PATH}")

# =========================
# ENDPOINTS DI SERVIZIO
# =========================

@app.get("/health")
def health():
    return {"ok": True, "kb_items": len(KB), "langs": list(ALLOWED_LANGS)}

@app.get("/debug-paths")
def debug_paths():
    return {
        "DATA_PATH": DATA_PATH,
        "DATA_PATH_type": "file" if os.path.isfile(DATA_PATH) else "missing",
        "I18N_DIR": I18N_DIR,
        "I18N_DIR_type": "dir" if os.path.isdir(I18N_DIR) else "missing",
        "I18N_CACHE_DIR": I18N_CACHE_DIR,
        "I18N_CACHE_DIR_type": "dir" if os.path.isdir(I18N_CACHE_DIR) else "missing",
        "ALLOWED_LANGS": list(ALLOWED_LANGS)
    }

@app.post("/reload-kb")
def reload_kb():
    n, _ = load_kb()
    return {"ok": True, "kb_items": n}

@app.get("/kb/ids")
def kb_ids():
    return list(KB.keys())

@app.get("/kb/item")
def kb_item(id: str):
    if id in KB:
        return KB[id]
    return JSONResponse({"error": "ID non trovato"}, status_code=404)

@app.get("/kb/search")
def kb_search(q: str = "", k: int = 10):
    q = q.lower().strip()
    if not q:
        return {"ok": True, "count": len(KB), "items": []}
    matches = []
    for item in KB.values():
        if q in item["q"].lower() or q in item["a"].lower():
            matches.append(item)
        if len(matches) >= k:
            break
    return {"ok": True, "count": len(matches), "items": matches}

# =========================
# ENDPOINT PRINCIPALE: /api/ask
# =========================

@app.post("/api/ask")
async def api_ask(req: Request):
    try:
        body = await req.json()
        q = body.get("q", "").strip().lower()
        if not q:
            return JSONResponse({"error": "Domanda vuota"}, status_code=400)

        for item in KB.values():
            if q in item["q"].lower():
                html = f"""
                <div class="card" style="border:1px solid #e5e7eb;border-radius:12px;padding:16px;font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
                    <h2 style="margin:0 0 8px 0;font-size:18px;color:#111827;">Risposta Tecnaria</h2>
                    <p style="margin:0 0 8px 0;line-height:1.5;color:#111827;">{item["a"]}</p>
                    <p style="margin:8px 0 0 0;color:#6b7280;font-size:12px;">⏱ {int(time.time() % 1000)} ms</p>
                </div>
                """
                return {"ok": True, "html": html}

        return {
            "ok": True,
            "html": """
            <div class="card" style="border:1px solid #e5e7eb;border-radius:12px;padding:16px;font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
                <h2 style="margin:0 0 8px 0;font-size:18px;color:#111827;">Risposta Tecnaria</h2>
                <p style="margin:0 0 8px 0;line-height:1.5;color:#111827;">Non ho trovato elementi sufficienti su domini autorizzati o nelle regole. Raffina la domanda o aggiorna le regole.</p>
                <p style="margin:8px 0 0 0;color:#6b7280;font-size:12px;">⏱ 0 ms</p>
            </div>
            """
        }

    except Exception as e:
        print("[ERRORE /api/ask]", e)
        return JSONResponse({"error": str(e)}, status_code=500)
