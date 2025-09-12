# app.py
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import os, time

from scraper_tecnaria import build_index, search_best_answer

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

# -----------------------
# Config e stato
# -----------------------
DOC_DIR = os.environ.get("DOC_DIR") or os.environ.get("DOCS_FOLDER") or os.environ.get("KNOWLEDGE_DIR") or "./documenti_gTab"
REINDEX_ON_STARTUP = os.environ.get("REINDEX_ON_STARTUP", "1") == "1"

INDEX_META = {"blocks": 0, "files": 0, "lines": 0, "ts": 0}

# -----------------------
# Auto seed all'avvio
# -----------------------
def seed_index_if_needed():
    global INDEX_META
    # Sempre ricostruzione se richiesto o se indice vuoto
    if REINDEX_ON_STARTUP or INDEX_META["blocks"] == 0:
        meta = build_index(DOC_DIR)
        INDEX_META = {
            "blocks": meta.get("blocks", 0),
            "files": meta.get("files", 0),
            "lines": meta.get("lines", 0),
            "ts": time.time(),
        }

seed_index_if_needed()

# -----------------------
# Routes
# -----------------------
@app.route("/", methods=["GET"])
def home():
    # Piccolo flag per mostrare se indice è pronto
    index_ready = INDEX_META["blocks"] > 0
    return render_template("index.html", index_ready=index_ready)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "blocks": INDEX_META["blocks"],
        "docs": INDEX_META["files"],
        "lines": INDEX_META["lines"],
        "ts": INDEX_META["ts"]
    })

@app.route("/reload", methods=["POST"])
def reload_index():
    global INDEX_META
    meta = build_index(DOC_DIR)
    INDEX_META = {
        "blocks": meta.get("blocks", 0),
        "files": meta.get("files", 0),
        "lines": meta.get("lines", 0),
        "ts": time.time(),
    }
    return jsonify({
        "status": "ok",
        "message": f"Indice ricaricato: {INDEX_META['blocks']} blocchi / {INDEX_META['files']} file / {INDEX_META['lines']} righe"
    })

@app.route("/ask", methods=["POST"])
def ask():
    seed_index_if_needed()  # nel dubbio, garantisce indice
    payload = request.get_json(force=True, silent=True) or {}
    q = (payload.get("q") or "").strip()
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
    res = search_best_answer(q)
    return jsonify(res)

# Piccolo endpoint lista file (diagnostica)
@app.route("/ls", methods=["GET"])
def ls():
    try:
        files = sorted([f for f in os.listdir(DOC_DIR) if f.lower().endswith(".txt")])
    except Exception as e:
        files = []
    return jsonify({"dir": DOC_DIR, "count": len(files), "files": files})

# -----------------------
# Entrypoint
# -----------------------
if __name__ == "__main__":
    # Per debug locale:
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=True)
