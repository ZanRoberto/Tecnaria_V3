# app.py — Tecnaria · Assistente documentale
# Boot sincrono + auto-reindex su cambi dei file (senza pulsanti)

import os
import hashlib
import time
import threading
from pathlib import Path
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# --- DALLO SCRAPER ---
# build_index(docs_dir) -> dict con chiavi: data, blocks, files, lines, ...
# search_best_answer(index, question) -> (answer, found, score, path, line)
from scraper_tecnaria import build_index, search_best_answer  # alias già gestito

app = Flask(__name__)
CORS(app)

# ----------------- CONFIG -----------------
DOCS_DIR = (
    os.environ.get("DOC_DIR")
    or os.environ.get("DOCS_FOLDER")
    or os.environ.get("KNOWLEDGE_DIR")
    or "documenti_gTab"
)
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.30"))
REINDEX_ON_STARTUP = os.environ.get("REINDEX_ON_STARTUP", "1") == "1"
# Rindicizza se cambia l’hash della cartella o se è passato troppo tempo
AUTO_REINDEX_SECONDS = int(os.environ.get("AUTO_REINDEX_SECONDS", "0"))  # 0 = disattivo

# --------------- STATO INDICE --------------
INDEX = None
INDEX_READY = False
INDEX_ERR = None
INDEX_FINGERPRINT = None
INDEX_TS = 0.0
LOCK = threading.Lock()

def _dir_fingerprint(base: str) -> str:
    """
    Fingerprint dei .txt in base/ (path+mtime+size).
    Se cambia, sappiamo che ci sono nuovi/aggiornati file.
    """
    p = Path(base)
    if not p.exists():
        return "MISSING"
    h = hashlib.sha1()
    for fp in sorted(p.rglob("*.txt")):
        try:
            st = fp.stat()
            h.update(str(fp.relative_to(p)).encode("utf-8"))
            h.update(str(int(st.st_mtime)).encode("utf-8"))
            h.update(str(st.st_size).encode("utf-8"))
        except Exception:
            continue
    return h.hexdigest()

def _build_index_sync():
    """Costruisce l’indice in modo sincrono e aggiorna fingerprint e timestamp."""
    global INDEX, INDEX_READY, INDEX_ERR, INDEX_FINGERPRINT, INDEX_TS
    with LOCK:
        try:
            print(f"[app] Indicizzazione SINCRONA da: {DOCS_DIR}")
            idx = build_index(DOCS_DIR)
            INDEX = idx
            INDEX_READY = True
            INDEX_ERR = None
            INDEX_FINGERPRINT = _dir_fingerprint(DOCS_DIR)
            INDEX_TS = time.time()
            print(f"[app] Indice pronto: blocks={idx.get('blocks',0)} files={idx.get('files',0)} lines={idx.get('lines',0)}")
        except Exception as e:
            INDEX = None
            INDEX_READY = False
            INDEX_ERR = str(e)
            INDEX_FINGERPRINT = None
            print(f"[app][ERRORE] Indicizzazione fallita: {e}")

def _ensure_index_ready_and_fresh():
    """Se non pronto o non fresco (file cambiati / timeout), ricostruisce ora."""
    global INDEX, INDEX_READY, INDEX_FINGERPRINT, INDEX_TS
    with LOCK:
        # Non pronto? costruisci
        if not INDEX_READY or INDEX is None:
            _build_index_sync()
            return
        # Cambi nei file? ricostruisci
        current_fp = _dir_fingerprint(DOCS_DIR)
        if INDEX_FINGERPRINT != current_fp:
            print("[app] Cambi nella cartella documenti: rindicizzazione automatica…")
            _build_index_sync()
            return
        # Timeout tempo (opzionale)
        if AUTO_REINDEX_SECONDS > 0 and (time.time() - INDEX_TS) > AUTO_REINDEX_SECONDS:
            print("[app] Reindex per timeout di freschezza…")
            _build_index_sync()
            return

# --------- BOOT: indice PRONTO prima di servire ----------
if REINDEX_ON_STARTUP:
    _build_index_sync()
else:
    INDEX_READY = False
    INDEX = None
    INDEX_ERR = None
    INDEX_FINGERPRINT = None

# ------------------- ROUTES -------------------
@app.get("/")
def home():
    return render_template("index.html")

@app.get("/health")
def health():
    info = {
        "status": "ok",
        "ready": INDEX_READY,
        "error": INDEX_ERR,
        "docs_dir": DOCS_DIR,
        "fingerprint": INDEX_FINGERPRINT or "n/d",
        "built_at": INDEX_TS,
    }
    if INDEX and isinstance(INDEX, dict):
        info.update({
            "blocks": INDEX.get("blocks", 0),
            "files": INDEX.get("files", 0),
            "lines": INDEX.get("lines", 0),
        })
    return jsonify(info), 200

# /reload resta disponibile (anche se non serve più in UI)
@app.post("/reload")
def reload_index():
    _build_index_sync()
    return jsonify({
        "ok": INDEX_READY,
        "blocks": INDEX.get("blocks", 0) if INDEX else 0,
        "files": INDEX.get("files", 0) if INDEX else 0,
        "lines": INDEX.get("lines", 0) if INDEX else 0,
        "error": INDEX_ERR,
        "fingerprint": INDEX_FINGERPRINT or "n/d",
        "built_at": INDEX_TS,
    }), 200 if INDEX_READY else 500

@app.post("/ask")
def ask():
    data = request.get_json(silent=True) or {}
    q = (data.get("question") or "").strip()
    if not q:
        return jsonify({"answer":"", "found":False, "score":0}), 200

    # Garantisce indice pronto e fresco senza pulsanti
    _ensure_index_ready_and_fresh()

    if not INDEX_READY or INDEX is None:
        msg = "Indice non disponibile. Carica i .txt in documenti_gTab/."
        if INDEX_ERR: msg += f" (Dettaglio: {INDEX_ERR})"
        return jsonify({"answer": msg, "found": False, "score": 0}), 200

    try:
        answer, found, score, path, line = search_best_answer(INDEX, q)
    except Exception as e:
        print(f"[app][ERRORE] search_best_answer: {e}")
        return jsonify({
            "answer": "Errore in ricerca. Riprova tra qualche secondo.",
            "found": False, "score": 0
        }), 200

    return jsonify({
        "answer": answer or "",
        "found": bool(found),
        "score": float(score or 0),
        "path": path, "line": line
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
