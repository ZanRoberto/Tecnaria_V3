# -*- coding: utf-8 -*-
import os
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# === Import dal tuo scraper ===
# Lo scraper ora espone: build_index, search_best_answer, is_ready, ensure_ready_blocking, INDEX
from scraper_tecnaria import (
    build_index,
    search_best_answer,
    is_ready,
    ensure_ready_blocking,
    INDEX,
)

# === Config ===
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))

# === Flask ===
app = Flask(__name__)
CORS(app)

def _ensure_index_ready():
    """
    Se l'indice non è pronto, prova a ricostruirlo in background.
    Ritorna True se ci sono documenti, False altrimenti.
    """
    if not is_ready():
        app.logger.warning("[app] INDEX non pronto: avvio build in background")
        ensure_ready_blocking(timeout_sec=0)
    try:
        docs = len(INDEX) if INDEX is not None else 0
    except Exception:
        docs = 0
    return docs > 0

# === Costruisci subito all'avvio (non blocca se richiede tempo) ===
_ensure_index_ready()

# === Routes ===
@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/ask", methods=["POST"])
def ask():
    q = (request.form.get("domanda") or "").strip()
    if not q:
        return jsonify({"answer": "Nessuna domanda ricevuta.", "found": False, "from": None})

    if not _ensure_index_ready():
        return jsonify({
            "answer": "Indice non pronto. Riprova tra qualche secondo.",
            "found": False,
            "from": None
        })

    try:
        result = search_best_answer(q, threshold=SIM_THRESHOLD, topk=TOPK)
        return jsonify(result)
    except Exception as e:
        app.logger.exception("[app] Errore in search_best_answer: %s", e)
        return jsonify({
            "answer": "Errore interno durante la ricerca.",
            "found": False,
            "from": None
        }), 500

@app.route("/reload", methods=["POST"])
def reload():
    try:
        build_index(DOC_DIR)
        docs = len(INDEX) if INDEX is not None else 0
        return jsonify({"status": "ok", "docs": docs})
    except Exception as e:
        app.logger.exception("[app] Errore /reload: %s", e)
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    try:
        docs = len(INDEX) if INDEX is not None else 0
    except Exception:
        docs = 0
    return jsonify({
        "status": "ok" if docs > 0 else "empty",
        "docs": docs,
        "threshold": SIM_THRESHOLD,
        "topk": TOPK,
        "doc_dir": os.path.abspath(DOC_DIR),
        "ready": is_ready(),
    })

# === Avvio locale ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
