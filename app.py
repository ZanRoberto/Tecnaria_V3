# -*- coding: utf-8 -*-
import os
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# === Import dallo scraper ===
from scraper_tecnaria import build_index, search_best_answer, INDEX

# === Config ===
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))

# === Flask ===
app = Flask(__name__)
CORS(app)

def _ensure_index_ready():
    """Se l'indice è vuoto o mancante, prova a ricostruirlo. Ritorna True se ci sono documenti."""
    try:
        docs = len(INDEX) if INDEX is not None else 0
    except Exception:
        docs = 0
    if docs == 0:
        app.logger.warning("[app] INDEX vuoto: ricostruisco da %s", os.path.abspath(DOC_DIR))
        try:
            build_index(DOC_DIR)
        except Exception as e:
            app.logger.exception("[app] Errore build_index: %s", e)
    try:
        docs = len(INDEX) if INDEX is not None else 0
    except Exception:
        docs = 0
    return docs > 0

# Costruisci l'indice SUBITO all’avvio
_ensure_index_ready()

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/ask", methods=["POST"])
def ask():
    q = (request.form.get("domanda") or "").strip()
    if not q:
        return jsonify({"answer": "Nessuna domanda ricevuta.", "found": False, "from": None})

    ready = _ensure_index_ready()
    if not ready:
        return jsonify({
            "answer": "Indice non pronto. Riprova tra qualche secondo.",
            "found": False,
            "from": None
        })

    try:
        result = search_best_answer(q, threshold=SIM_THRESHOLD, topk=TOPK)
        # Fallback: abbassa soglia una volta se non trova
        if not result.get("found") and SIM_THRESHOLD > 0.28:
            result = search_best_answer(q, threshold=0.28, topk=TOPK)
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
        "doc_dir": os.path.abspath(DOC_DIR)
    })

# Avvio locale
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
