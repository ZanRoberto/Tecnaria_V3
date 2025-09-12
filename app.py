# -*- coding: utf-8 -*-
import os
import logging
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# --- IMPORT UNICO del modulo scraper ---
# (così leggiamo sempre l'INDEX aggiornato dal modulo)
import scraper_tecnaria as ST

# =========================
# Configurazione di base
# =========================
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))

# =========================
# Flask app
# =========================
app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# Log un po' più verboso in debug
if os.environ.get("DEBUG", "0") == "1":
    app.logger.setLevel(logging.DEBUG)
else:
    app.logger.setLevel(logging.INFO)


def _ensure_index_ready() -> bool:
    """
    Se l'indice è vuoto o mancante, prova a ricostruirlo una volta.
    Ritorna True se ci sono documenti, False altrimenti.
    """
    try:
        docs = len(ST.INDEX)
    except Exception:
        docs = 0

    if docs == 0:
        app.logger.warning("[app] INDEX vuoto: costruisco da %s", os.path.abspath(DOC_DIR))
        try:
            built = ST.build_index(DOC_DIR)
            app.logger.info("[app] build_index completato: %s blocchi indicizzati", built)
        except Exception as e:
            app.logger.exception("[app] Errore build_index: %s", e)

    try:
        docs = len(ST.INDEX)
    except Exception:
        docs = 0

    return docs > 0


# =========================
# Build indice all'avvio
# =========================
try:
    _ensure_index_ready()
except Exception as e:
    # Non blocco l'avvio del server: /health mostrerà 'empty'
    app.logger.exception("[app] Errore iniziale nell'ensure_index_ready: %s", e)


# =========================
# Routes
# =========================
@app.route("/", methods=["GET"])
def home():
    # Usa templates/index.html se presente; altrimenti rispondi JSON minimale
    try:
        return render_template("index.html")
    except Exception:
        return jsonify({
            "service": "Tecnaria · Assistente documentale",
            "message": "Servizio attivo. Usa POST /ask per fare domande.",
            "docs": len(ST.INDEX) if hasattr(ST, "INDEX") else 0
        })


@app.route("/ask", methods=["POST"])
def ask():
    """
    body x-www-form-urlencoded:
      - domanda: testo domanda
    ritorna: {answer, found, from, score, ...}
    """
    q = (request.form.get("domanda") or "").strip()
    if not q:
        return jsonify({"answer": "Nessuna domanda ricevuta.", "found": False, "from": None})

    # Assicurati indice pronto
    if not _ensure_index_ready():
        return jsonify({
            "answer": "Indice non pronto. Riprova tra qualche secondo.",
            "found": False,
            "from": None
        })

    # Ricerca
    try:
        result = ST.search_best_answer(q, threshold=SIM_THRESHOLD, topk=TOPK)
        # Fallback: ritenta con soglia più bassa una sola volta
        if not result.get("found") and SIM_THRESHOLD > 0.28:
            app.logger.debug("[app] Nessun match sopra %.2f, ritento con 0.28", SIM_THRESHOLD)
            result = ST.search_best_answer(q, threshold=0.28, topk=TOPK)
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
    """
    Ricostruisce l'indice manualmente.
    """
    try:
        built = ST.build_index(DOC_DIR)
        return jsonify({"status": "ok", "docs": built})
    except Exception as e:
        app.logger.exception("[app] Errore /reload: %s", e)
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    """
    Health-check parlante per Render.
    """
    try:
        docs = len(ST.INDEX)
    except Exception:
        docs = 0

    status = "ok" if docs > 0 else "empty"
    return jsonify({
        "status": status,
        "docs": docs,
        "threshold": SIM_THRESHOLD,
        "topk": TOPK,
        "doc_dir": os.path.abspath(DOC_DIR)
    })


# =========================
# Avvio locale
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=(os.environ.get("DEBUG", "0") == "1"))
