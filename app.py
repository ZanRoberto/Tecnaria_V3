# -*- coding: utf-8 -*-
import os
import time
import logging
from typing import Any, Dict, Optional

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# === Import dal tuo scraper (nuovo) ===
# Devono esistere in scraper_tecnaria.py:
#   build_index(doc_dir: str) -> dict
#   search_best_answer(query: str, threshold: float=None, topk: int=None) -> dict
#   is_ready() -> bool
#   INDEX (lista/struttura dell'indice)
from scraper_tecnaria import build_index, search_best_answer, is_ready, INDEX

# =========================
# Config
# =========================
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))
BOOT_RETRIES = int(os.environ.get("BOOT_RETRIES", "3"))
BOOT_WAIT_SECS = float(os.environ.get("BOOT_WAIT_SECS", "1.5"))
SHOW_DEBUG = os.environ.get("SHOW_DEBUG", "1") == "1"

# =========================
# Flask
# =========================
app = Flask(__name__)
CORS(app)

# Log più verboso su Render
logging.basicConfig(level=logging.INFO)
log = app.logger

def _ensure_index_ready() -> bool:
    """Cerca di avere l’indice pronto; se vuoto ricostruisce."""
    try:
        n = len(INDEX) if INDEX is not None else 0
    except Exception:
        n = 0
    if n == 0 or not is_ready():
        log.warning("[app] INDEX vuoto o non pronto: provo build_index(%s)", DOC_DIR)
        try:
            build_index(DOC_DIR)
        except Exception as e:
            log.exception("[app] Errore in build_index: %s", e)
    # ricontrollo
    try:
        n = len(INDEX) if INDEX is not None else 0
    except Exception:
        n = 0
    ready = bool(n > 0 and is_ready())
    log.info("[app] Ready=%s, docs=%d", ready, n)
    return ready

# === Costruisci l'indice SUBITO all’avvio (con piccoli retry: utile su piani free) ===
for attempt in range(BOOT_RETRIES):
    if _ensure_index_ready():
        break
    time.sleep(BOOT_WAIT_SECS)

# =========================
# Routes
# =========================
@app.route("/", methods=["GET"])
def home() -> Any:
    # Se usi un template, altrimenti puoi restituire una pagina semplice
    try:
        return render_template("index.html")
    except Exception:
        return "Tecnaria · Assistente documentale", 200

@app.route("/ask", methods=["POST"])
def ask() -> Any:
    """
    Accetta:
      - form: domanda=...
      - json: {"q": "..."} o {"domanda": "..."}
    Ritorna sempre JSON con almeno: answer, found, from, (debug se SHOW_DEBUG=1).
    """
    try:
        q = (request.form.get("domanda") or "").strip()
        if not q:
            data = request.get_json(silent=True) or {}
            q = (data.get("q") or data.get("domanda") or "").strip()

        if not q:
            resp = {"answer": "Nessuna domanda ricevuta.", "found": False, "from": None}
            if SHOW_DEBUG:
                resp["debug"] = {"note": "empty_query"}
            return jsonify(resp)

        # Assicuro indice pronto (costruisce se serve)
        if not _ensure_index_ready():
            resp = {"answer": "Indice non pronto. Riprova tra qualche secondo.", "found": False, "from": None}
            if SHOW_DEBUG:
                resp["debug"] = {"note": "index_not_ready"}
            return jsonify(resp)

        # Ricerca “miglior risposta”
        result = search_best_answer(q, threshold=SIM_THRESHOLD, topk=TOPK)

        # Se non trova, prova una sola volta ad allentare soglia
        if not result.get("found") and SIM_THRESHOLD > 0.28:
            result = search_best_answer(q, threshold=0.28, topk=TOPK)

        # Garanzia chiavi minime
        out = {
            "answer": result.get("answer") or "Non ho trovato una risposta precisa.",
            "found": bool(result.get("found")),
            "from": result.get("from"),
        }
        # Propaga info utili
        if "score" in result:
            out["score"] = result["score"]
        if "tags" in result:
            out["tags"] = result["tags"]
        if SHOW_DEBUG:
            out["debug"] = result.get("debug", {})

        return jsonify(out)

    except Exception as e:
        log.exception("[/ask] Errore interno: %s", e)
        out = {"answer": "Errore interno durante la ricerca.", "found": False, "from": None}
        if SHOW_DEBUG:
            out["debug"] = {"error": f"{type(e).__name__}: {e}"}
        return jsonify(out), 200  # mai 500

@app.route("/reload", methods=["POST"])
def reload_index() -> Any:
    try:
        build_index(DOC_DIR)
        n = len(INDEX) if INDEX is not None else 0
        out = {"status": "ok", "docs": n, "ready": is_ready()}
        if SHOW_DEBUG:
            out["debug"] = {"doc_dir": os.path.abspath(DOC_DIR)}
        return jsonify(out)
    except Exception as e:
        log.exception("[/reload] Errore: %s", e)
        out = {"status": "error", "error": str(e)}
        return jsonify(out), 200  # mai 500

@app.route("/health", methods=["GET"])
def health() -> Any:
    try:
        n = len(INDEX) if INDEX is not None else 0
    except Exception:
        n = 0
    out = {
        "status": "ok" if (n > 0 and is_ready()) else "empty",
        "docs": n,
        "threshold": SIM_THRESHOLD,
        "topk": TOPK,
        "doc_dir": os.path.abspath(DOC_DIR),
        "ready": is_ready(),
    }
    return jsonify(out), 200

# === Avvio locale ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
