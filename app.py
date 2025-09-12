# -*- coding: utf-8 -*-
import os
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# === Import dal tuo scraper (questi simboli ESISTONO nello scraper qui sotto) ===
from scraper_tecnaria import build_index, search_best_answer, INDEX

# ===== Config da ENV (con default sicuri) =====
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.30"))
TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))

# ===== Flask =====
app = Flask(__name__)
CORS(app)

def _ensure_index_ready():
    """
    Se l'indice è vuoto o mancante, prova a ricostruirlo.
    Ritorna True se ci sono documenti, False altrimenti.
    """
    try:
        docs = len(INDEX) if INDEX is not None else 0
    except Exception:
        docs = 0

    if docs == 0:
        app.logger.warning("[app] INDEX vuoto: ricostruisco da %s", DOC_DIR)
        try:
            build_index(DOC_DIR)
        except Exception as e:
            app.logger.exception("[app] Errore build_index: %s", e)

    # ricontrollo
    try:
        docs = len(INDEX) if INDEX is not None else 0
    except Exception:
        docs = 0

    if docs == 0:
        abs_dir = os.path.abspath(DOC_DIR)
        try:
            listing = os.listdir(abs_dir)
        except Exception as e:
            listing = [f"(errore a listare {abs_dir}: {e})"]
        app.logger.error("[app] Ancora vuoto. DOC_DIR=%s | abs=%s | list=%s",
                         DOC_DIR, abs_dir, listing)
        try:
            build_index(abs_dir)
            docs = len(INDEX) if INDEX is not None else 0
        except Exception as e:
            app.logger.exception("[app] Secondo tentativo fallito: %s", e)

    return docs > 0

# ===== Costruisci l'indice subito all'avvio =====
_ensure_index_ready()

# ===== Routes =====
@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/ask", methods=["POST"])
def ask():
    q = (request.form.get("domanda") or "").strip()
    if not q:
        return jsonify({
            "found": False,
            "answer": "Nessuna domanda ricevuta.",
            "from": None
        })

    # assicurati che l'indice sia pronto
    ready = _ensure_index_ready()
    if not ready:
        return jsonify({
            "found": False,
            "answer": "Indice non pronto. Riprova tra qualche secondo.",
            "from": None
        })

    # ricerca ibrida
    try:
        result = search_best_answer(q, threshold=SIM_THRESHOLD, topk=TOPK)
        if (not result.get("found")) and SIM_THRESHOLD > 0.25:
            # un tentativo extra un po' più permissivo
            result = search_best_answer(q, threshold=0.25, topk=TOPK)
        return jsonify(result)
    except Exception as e:
        app.logger.exception("[app] Errore in search_best_answer: %s", e)
        return jsonify({
            "found": False,
            "answer": "Errore interno durante la ricerca.",
            "from": None
        }), 500

@app.route("/reload", methods=["POST"])
def reload():
    try:
        build_index(DOC_DIR)
        docs = len(INDEX) if INDEX is not None else 0
        return jsonify({"status": "ok", "docs": docs})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    try:
        docs = len(INDEX) if INDEX is not None else 0
    except Exception:
        docs = 0
    abs_dir = os.path.abspath(DOC_DIR)
    try:
        txt_on_disk = len([n for n in os.listdir(abs_dir) if n.lower().endswith(".txt")])
    except Exception as e:
        txt_on_disk = f"errore list: {e}"
    return jsonify({
        "status": "ok" if docs > 0 else "empty",
        "docs_in_index": docs,
        "txt_on_disk": txt_on_disk,
        "doc_dir": DOC_DIR,
        "abs_doc_dir": abs_dir,
        "threshold": SIM_THRESHOLD,
        "topk": TOPK
    })

# ===== Avvio locale =====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
