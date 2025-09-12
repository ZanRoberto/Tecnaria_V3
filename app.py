# app.py
# ---------------------------------------------------------
# Flask app per l'Assistente Tecnaria (solo contenuti locali).
# Gunicorn entrypoint: app:app
# ---------------------------------------------------------

import os
import threading
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS

# Import dal tuo scraper
from scraper_tecnaria import build_index, search_best_answer, INDEX

# ---------------------------------------------------------
# Config
# ---------------------------------------------------------
DOC_DIR = os.environ.get("DOC_DIR") or os.environ.get("DOCS_FOLDER") or os.environ.get("KNOWLEDGE_DIR") or "./documenti_gTab"
REINDEX_ON_STARTUP = os.environ.get("REINDEX_ON_STARTUP", "1") in ("1", "true", "True")
DEBUG_SCRAPER = os.environ.get("DEBUG_SCRAPER", "0") in ("1", "true", "True")

# Flask
app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# Per evitare race-condition durante la (ri)indicizzazione
_index_lock = threading.Lock()

def _ensure_index():
    """Costruisce l'indice se vuoto."""
    if not INDEX.get("blocks"):
        with _index_lock:
            # doppio check per evitare re-build concorrente
            if not INDEX.get("blocks"):
                stats = build_index(DOC_DIR)
                if DEBUG_SCRAPER:
                    print(f"[app] Indice costruito: {stats}", flush=True)

# ---------------------------------------------------------
# Startup: indicizza se richiesto
# ---------------------------------------------------------
if REINDEX_ON_STARTUP:
    try:
        stats = build_index(DOC_DIR)
        if DEBUG_SCRAPER:
            print(f"[app] Startup reindex: {stats}", flush=True)
    except Exception as e:
        # Non blocchiamo l'avvio, si potrà ricaricare da /reload
        print(f"[app][WARNING] build_index all'avvio fallita: {e}", flush=True)

# ---------------------------------------------------------
# Routes
# ---------------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    # Serve la UI (templates/index.html deve esistere)
    return render_template("index.html")

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

@app.route("/health", methods=["GET"])
def health():
    # Stato indice
    blocks = len(INDEX.get("blocks", []))
    files = len(INDEX.get("files", set()))
    ready = bool(blocks)
    return jsonify({
        "status": "ok",
        "ready": ready,
        "blocks": blocks,
        "files": files
    })

@app.route("/reload", methods=["POST"])
def reload_index():
    with _index_lock:
        stats = build_index(DOC_DIR)
    return jsonify({
        "status": "ok",
        "message": "Indice ricaricato",
        "blocks": stats.get("blocks", 0),
        "files": stats.get("files", 0),
        "lines": stats.get("lines", 0),
    })

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json(silent=True) or {}
    q = (data.get("q") or "").strip()

    if not q:
        return jsonify({
            "found": False,
            "answer": "",
            "score": 0,
            "path": None,
            "line": None,
            "question": "",
            "tags": "",
            "error": "Domanda vuota"
        })

    # Assicuriamoci che l'indice esista
    _ensure_index()

    # Se ancora vuoto, restituiamo un messaggio chiaro
    if not INDEX.get("blocks"):
        return jsonify({
            "found": False,
            "answer": "",
            "score": 0,
            "path": None,
            "line": None,
            "question": "",
            "tags": "",
            "error": "Indice non pronto: carica i .txt in documenti_gTab/ e ricarica."
        })

    # Cerca la risposta migliore
    res = search_best_answer(q)

    # Normalizziamo i tipi per sicurezza
    out = {
        "found": bool(res.get("found")),
        "answer": res.get("answer", ""),
        "score": res.get("score", 0),
        "path": res.get("path"),
        "line": res.get("line"),
        "question": res.get("question", ""),
        "tags": res.get("tags", "")
    }
    return jsonify(out)

# ---------------------------------------------------------
# Main (solo per debug locale)
# ---------------------------------------------------------
if __name__ == "__main__":
    # Avvio per debug locale (es: python app.py)
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
