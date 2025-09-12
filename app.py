# -*- coding: utf-8 -*-
import os, time, traceback
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# ==== Import dal tuo scraper ====
from scraper_tecnaria import (
    build_index,
    search_best_answer,
    is_ready,
    docs_count,
    DOC_DIR as SCRAPER_DOC_DIR,
)

# ==== Config ====
DOC_DIR = os.environ.get("DOC_DIR", SCRAPER_DOC_DIR or "documenti_gTab")
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))
BLOCK_UNTIL_READY_SECONDS = int(os.environ.get("BLOCK_UNTIL_READY_SECONDS", "20"))

# ==== Flask ====
app = Flask(__name__)
CORS(app)

def ensure_ready_blocking(max_wait_sec: int = BLOCK_UNTIL_READY_SECONDS) -> bool:
    """
    Se l'indice non è pronto, costruiscilo SINCRONAMENTE e attendi fino a max_wait_sec.
    Ritorna True se pronto, False se ancora vuoto dopo l'attesa.
    """
    if is_ready():
        return True
    app.logger.warning("[app] INDEX non pronto: costruzione sincrona da %s", DOC_DIR)
    try:
        build_index(DOC_DIR)  # build sincrona
    except Exception as e:
        app.logger.exception("[app] Errore in build_index: %s", e)
        return False

    # attesa (breve) finché non risulta pronto
    deadline = time.time() + max_wait_sec
    while time.time() < deadline:
        if is_ready():
            return True
        time.sleep(0.2)
    return is_ready()

# costruiamo all’avvio (e blocchiamo finché pronto o scade timeout breve)
ensure_ready_blocking()

# ==== Routes ====
@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/ask", methods=["POST"])
def ask():
    q = (request.form.get("domanda") or "").strip()
    if not q:
        return jsonify({"answer": "Nessuna domanda ricevuta.", "found": False, "from": None})

    # blocca finché l'indice è pronto (entro il timeout)
    if not ensure_ready_blocking():
        return jsonify({
            "answer": "Indice non pronto. Riprova tra qualche secondo.",
            "found": False,
            "from": None
        })

    try:
        result = search_best_answer(q, threshold=SIM_THRESHOLD, topk=TOPK)
        if not result.get("found") and SIM_THRESHOLD > 0.28:
            # secondo tentativo più “aperto”
            result = search_best_answer(q, threshold=0.28, topk=TOPK)
        return jsonify(result)
    except Exception as e:
        app.logger.exception("[app] Errore in search_best_answer: %s", e)
        return jsonify({
            "answer": "Errore interno durante la ricerca.",
            "found": False,
            "from": None,
            "debug": {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[:2000]},
        }), 500

@app.route("/reload", methods=["POST"])
def reload():
    try:
        build_index(DOC_DIR)
        return jsonify({"status": "ok", "docs": docs_count()})
    except Exception as e:
        app.logger.exception("[app] Errore /reload: %s", e)
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    ready = is_ready()
    return jsonify({
        "status": "ok" if ready else "building" if docs_count() == 0 else "degraded",
        "ready": ready,
        "docs": docs_count(),
        "threshold": SIM_THRESHOLD,
        "topk": TOPK,
        "doc_dir": os.path.abspath(DOC_DIR),
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
