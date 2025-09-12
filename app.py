# app.py  — versione “free plan ready”
# -*- coding: utf-8 -*-
import os
import time
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# === Import dal tuo scraper (DEVE esistere) ===
# build_index(doc_dir) -> costruisce/ricostruisce l’indice
# search_best_answer(q, threshold, topk) -> dict {answer, found, score, from, ...}
# INDEX -> lista/dict con i documenti indicizzati (per capire se è pronto)
from scraper_tecnaria import build_index, search_best_answer, INDEX

# === Config ===
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")           # cartella con i .txt
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))
BOOT_RETRIES = int(os.environ.get("BOOT_RETRIES", "8"))          # tentativi avvio
BOOT_SLEEP = float(os.environ.get("BOOT_SLEEP", "1.0"))          # sec tra tentativi

app = Flask(__name__)
CORS(app)

def _index_size() -> int:
    """Ritorna il numero di elementi indicizzati oppure 0 se INDEX non è pronto."""
    try:
        return len(INDEX) if INDEX is not None else 0
    except Exception:
        return 0

def _ensure_index_ready() -> bool:
    """
    Se l’indice è vuoto, prova a costruirlo.
    Ritorna True se pronto (>=1 doc), False se no.
    """
    if _index_size() > 0:
        return True
    app.logger.warning("[app] INDEX vuoto: costruisco da %s", os.path.abspath(DOC_DIR))
    try:
        build_index(DOC_DIR)
    except Exception as e:
        app.logger.exception("[app] Errore build_index: %s", e)
    return _index_size() > 0

# === COSTRUZIONE BLOCCANTE ALL’AVVIO (piano free: niente /health) ===
def _boot_blocking_build():
    for i in range(BOOT_RETRIES):
        if _ensure_index_ready():
            app.logger.info("[app] Indice pronto (%d file).", _index_size())
            return
        time.sleep(BOOT_SLEEP)
    # Anche se fallisce, l’app resta su: le route riproveranno
    app.logger.error("[app] Indice NON pronto dopo il boot, riproverò on-demand.")

_boot_blocking_build()

# === ROUTES ===
@app.route("/", methods=["GET"])
def home():
    """
    Mostra la pagina solo quando l’indice è pronto.
    Se non fosse pronto (caso raro), tenta una ricostruzione rapida.
    """
    if not _ensure_index_ready():
        # un ultimo tentativo immediato
        for _ in range(3):
            time.sleep(0.5)
            if _ensure_index_ready():
                break
    return render_template("index.html")  # la tua UI attuale

@app.route("/ask", methods=["POST"])
def ask():
    q = (request.form.get("domanda") or "").strip()
    if not q:
        return jsonify({"answer": "Nessuna domanda ricevuta.", "found": False, "from": None})

    # Assicurati che l’indice sia pronto (anche se si fosse “svuotato”)
    if not _ensure_index_ready():
        # piccolo backoff e ultimo tentativo
        time.sleep(0.6)
        if not _ensure_index_ready():
            return jsonify({
                "answer": "Indice non pronto. Riprova tra un istante.",
                "found": False,
                "from": None
            })

    # Ricerca
    try:
        result = search_best_answer(q, threshold=SIM_THRESHOLD, topk=TOPK)
        # fail-safe: abbassa un filo la soglia se non ha trovato nulla
        if not result.get("found") and SIM_THRESHOLD > 0.28:
            result = search_best_answer(q, threshold=0.28, topk=TOPK)
        return jsonify(result)
    except Exception as e:
        app.logger.exception("[app] Errore in search_best_answer: %s", e)
        return jsonify({"answer": "Errore interno.", "found": False, "from": None}), 500

@app.route("/reload", methods=["POST"])
def reload():
    """Ricarica manualmente l’indice (utile durante i test)."""
    ok = _ensure_index_ready()
    return jsonify({"status": "ok" if ok else "error", "docs": _index_size()})

@app.route("/health", methods=["GET"])
def health():
    """Comodo anche sul piano free per vedere a colpo d’occhio lo stato."""
    docs = _index_size()
    return jsonify({
        "status": "ok" if docs > 0 else "empty",
        "docs": docs,
        "threshold": SIM_THRESHOLD,
        "topk": TOPK,
        "doc_dir": os.path.abspath(DOC_DIR)
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
