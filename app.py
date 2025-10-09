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

BASE_DIR = os.path.dirname(__file__)
DATA_PATH = os.path.join(BASE_DIR, "static", "data", "SINAPSI_GLOBAL_TECNARIA_EXT.json")

I18N_DIR = os.path.join(BASE_DIR, "static", "i18n")
I18N_CACHE_DIR = os.getenv("I18N_CACHE_DIR", os.path.join(BASE_DIR, "static", "i18n-cache"))

ALLOWED_LANGS = {"it", "en", "fr", "de", "es"}

_lock = threading.Lock()

# =========================
# UTILS
# =========================

def load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERRORE] Impossibile leggere {path}: {e}")
        return {}

# Mappa frasi tipiche EN/ES/FR/DE -> forma IT presente nel KB
CANON = {
    # CTF - codici
    r"\bcan you (tell|list).*\bctf code": "mi puoi dire i codici dei ctf?",
    r"puedes.*c[oó]digos.*ctf": "mi puoi dire i codici dei ctf?",
    r"peux[- ]tu.*codes.*ctf": "mi puoi dire i codici dei ctf?",
    r"kannst du.*ctf.*codes": "mi puoi dire i codici dei ctf?",
    # CTF - posa/chiodatrice
    r"\bhow to install\b.*ctf|tools and constraints": "connettori ctf: si può usare una chiodatrice qualsiasi?",
    r"peux[- ]tu.*poser.*ctf|outils.*contraintes": "connettori ctf: si può usare una chiodatrice qualsiasi?",
    r"wie.*montiert.*ctf|werkzeuge|vorgaben": "connettori ctf: si può usare una chiodatrice qualsiasi?",
    r"como.*instala.*ctf|herramientas.*l[ií]mites": "connettori ctf: si può usare una chiodatrice qualsiasi?",
    # CEM-E - resine
    r"\bdo.*ctcem.*(use|using).*resin": "i connettori tecnaria ctcem per solai in laterocemento si posano con resine?",
    r"los conectores.*ctcem.*resinas": "i connettori tecnaria ctcem per solai in laterocemento si posano con resine?",
    r"les connecteurs.*ctcem.*r[eé]sines": "i connettori tecnaria ctcem per solai in laterocemento si posano con resine?",
    r"ctcem.*harz|harze": "i connettori tecnaria ctcem per solai in laterocemento si posano con resine?",
    # CEM-E - famiglie
    r"which connectors.*(hollow|hollow[- ]block).*slab": "quali connettori tecnaria ci sono per solai in laterocemento?",
    r"qu[eé]\s+conectores.*(bovedillas|forjados)": "quali connettori tecnaria ci sono per solai in laterocemento?",
    r"quels connecteurs.*(hourdis|planchers)": "quali connettori tecnaria ci sono per solai in laterocemento?",
    r"welche verbind(er|ungen).*hohlstein(decken)?": "quali connettori tecnaria ci sono per solai in laterocemento?",
    # Guard-rail CTC
    r"\bare ctc (codes|from) tecnaria": "i ctc sono un codice tecnaria?",
    r"ctc.*c[oó]digo.*tecnaria": "i ctc sono un codice tecnaria?",
    r"ctc.*code.*tecnaria": "i ctc sono un codice tecnaria?",
    r"sind ctc.*tecnaria": "i ctc sono un codice tecnaria?",
}

def normalize_query_to_it(q: str) -> str:
    ql = q.lower().strip()
    # se già italiano, tienilo com’è
    if "ctf" in ql and "codici" in ql: 
        return ql
    if "connettori ctf" in ql or "chiodatrice" in ql:
        return ql
    if "ctcem" in ql and "resine" in ql:
        return ql
    if "solai in laterocemento" in ql:
        return ql
    if re.search(r"\bi ctc\b|\bctc\b.*tecnaria", ql):
        return ql
    # altrimenti prova le mappe
    for pat, canon in CANON.items():
        if re.search(pat, ql):
            return canon
    return ql  # fallback: non mappata, resta com’è

# =========================
# INIZIALIZZAZIONE APP
# =========================

app = FastAPI(title="Tecnaria BOT", version="3.5")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# =========================
# CARICAMENTO KNOWLEDGE BASE
# =========================

KB: Dict[str, dict] = {}
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

count, _ = load_kb()
print(f"[INIT] Caricate {count} voci KB da {DATA_PATH}")

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
    ql = q.lower().strip()
    if not ql:
        return {"ok": True, "count": len(KB), "items": []}
    matches = []
    for item in KB.values():
        if ql in item["q"].lower() or ql in item["a"].lower():
            matches.append(item)
        if len(matches) >= k:
            break
    return {"ok": True, "count": len(matches), "items": matches}

# =========================
# ENDPOINT PRINCIPALE
# =========================

@app.post("/api/ask")
async def api_ask(req: Request):
    try:
        body = await req.json()
        q_raw = body.get("q", "")
        if not q_raw or not q_raw.strip():
            return JSONResponse({"error": "Domanda vuota"}, status_code=400)

        q_it = normalize_query_to_it(q_raw)

        # match semplice: se la domanda canonica è substring del campo "q" della KB
        for item in KB.values():
            if q_it in item["q"].lower():
                html = f"""
                <div class="card" style="border:1px solid #e5e7eb;border-radius:12px;padding:16px;font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
                    <h2 style="margin:0 0 8px 0;font-size:18px;color:#111827;">Risposta Tecnaria</h2>
                    <p style="margin:0 0 8px 0;line-height:1.5;color:#111827;">{item["a"]}</p>
                    <p style="margin:8px 0 0 0;color:#6b7280;font-size:12px;">⏱ {int(time.time() % 1000)} ms</p>
                </div>
                """
                return {"ok": True, "html": html}

        # fallback: stessa logica di prima
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
