# -*- coding: utf-8 -*-
import os, time, threading
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# Import dallo scraper
from scraper_tecnaria import (
    build_index,
    is_ready,
    search_best_answer,
    get_counters,
)

DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.30"))
TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))

app = Flask(__name__)
CORS(app)

# -------------------------------
# Indicizzazione in BACKGROUND
# -------------------------------

_index_thread = None
_index_lock = threading.Lock()
_index_started = False

def _build_index_bg():
    try:
        app.logger.warning("[app] indicizzazione in background da %s ...", DOC_DIR)
        build_index(DOC_DIR)
        bl, fl, rl = get_counters()
        app.logger.warning("[app] indice pronto: blocchi=%s file=%s righe=%s", bl, fl, rl)
    except Exception as e:
        app.logger.exception("[app] errore indicizzazione: %s", e)

def ensure_index_async():
    """Avvia l'indicizzazione in background una sola volta (non blocca il boot)."""
    global _index_started, _index_thread
    with _index_lock:
        if not _index_started:
            _index_started = True
            _index_thread = threading.Thread(target=_build_index_bg, daemon=True)
            _index_thread.start()

# Avvia subito il thread, ma NON bloccare l'avvio del servizio
ensure_index_async()

# -------------------------------
# ROUTES
# -------------------------------

@app.route("/", methods=["GET"])
def home():
    # pagina minimale opzionale (se non usi template, puoi servire solo l'API)
    return render_template("index.html") if os.path.exists("templates/index.html") else "Tecnaria · Assistente documentale"

@app.route("/ask", methods=["POST"])
def ask():
    q = (request.form.get("domanda") or "").strip()
    if not q:
        return jsonify({"found": False, "answer": "Nessuna domanda ricevuta.", "from": None})

    # aspetta al massimo 3s se l'indice non è pronto (tipico primo colpo dopo il deploy)
    t0 = time.time()
    while not is_ready() and (time.time() - t0) < 3:
        time.sleep(0.1)

    if not is_ready():
        return jsonify({
            "found": False,
            "answer": "Indice in costruzione. Riprova tra qualche secondo.",
            "from": None
        })

    res = search_best_answer(q, threshold=SIM_THRESHOLD, topk=TOPK)
    if not res.get("found") and SIM_THRESHOLD > 0.25:
        # fallback soft
        res = search_best_answer(q, threshold=0.25, topk=TOPK)
    return jsonify(res)

@app.route("/reload", methods=["POST"])
def reload_idx():
    # ricostruzione manuale on-demand (non necessaria in Render, ma utile in locale)
    def _task():
        try:
            build_index(DOC_DIR)
            bl, fl, rl = get_counters()
            app.logger.warning("[/reload] indice pronto: blocchi=%s file=%s righe=%s", bl, fl, rl)
        except Exception as e:
            app.logger.exception("[/reload] errore: %s", e)

    threading.Thread(target=_task, daemon=True).start()
    return jsonify({"status": "started"})

@app.route("/health", methods=["GET"])
def health():
    bl, fl, rl = get_counters()
    return jsonify({
        "status": "ok" if is_ready() else "building",
        "docs": bl,
        "files": fl,
        "lines": rl,
        "threshold": SIM_THRESHOLD,
        "topk": TOPK,
        "doc_dir": os.path.abspath(DOC_DIR)
    })

# Avvio locale
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
