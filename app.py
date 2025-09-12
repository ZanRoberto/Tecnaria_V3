# -*- coding: utf-8 -*-
import os
import time
import threading
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# === Import dal nuovo scraper ===
# Assicurati che scraper_tecnaria.py sia quello che ti ho dato (con build_index, is_ready, search_best_answer, INDEX)
from scraper_tecnaria import (
    build_index,
    is_ready,
    search_best_answer,
    INDEX,
)

# =========================
# Config da ENV
# =========================
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))
BOOT_INDEX_TIMEOUT = float(os.environ.get("BOOT_INDEX_TIMEOUT", "55"))  # evita 502 al primo avvio
DEBUG = os.environ.get("DEBUG_APP", "1") == "1"

# =========================
# Flask
# =========================
app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

# =========================
# Build indice (thread-safe)
# =========================
_build_lock = threading.Lock()
_index_started = False

def ensure_index_ready(blocking: bool = True, timeout: float = BOOT_INDEX_TIMEOUT) -> bool:
    """
    - Se l'indice è pronto -> True
    - Se non è pronto:
        * blocking=True: prova a costruirlo subito (con lock) e attende fino a 'timeout'
        * blocking=False: avvia un thread in background e torna subito
    """
    global _index_started
    try:
        if is_ready():
            return True
    except Exception:
        pass

    if not _index_started:
        # Primo tentativo di build
        def _do_build():
            try:
                app.logger.warning("[app] INDEX vuoto → costruzione da %s", DOC_DIR)
                build_index(DOC_DIR)
                app.logger.info("[app] INDEX pronto: %s blocchi", len(INDEX.get("docs", [])))
            except Exception as e:
                app.logger.exception("[app] Errore in build_index: %s", e)

        _index_started = True
        if blocking:
            with _build_lock:
                _do_build()
        else:
            # avvia in background per non bloccare la richiesta corrente
            th = threading.Thread(target=lambda: ( _build_lock.acquire(), _do_build(), _build_lock.release() ), daemon=True)
            th.start()

    if not blocking:
        # Non blocca: ritorna lo stato attuale
        return is_ready()

    # Se blocking, attende fino a timeout
    t0 = time.time()
    while time.time() - t0 < timeout:
        if is_ready():
            return True
        time.sleep(0.2)
    return is_ready()

# Costruisci l'indice SUBITO all'avvio (ma senza bloccare troppo)
# Per evitare timeouts su Render al boot, usiamo blocking=True ma con timeout controllato.
ensure_index_ready(blocking=True, timeout=BOOT_INDEX_TIMEOUT)

# =========================
# Routes
# =========================
@app.route("/", methods=["GET"])
def home():
    # Se non hai un template index.html, puoi restituire una mini pagina inline
    try:
        return render_template("index.html")
    except Exception:
        return """
        <html>
          <head><meta charset="utf-8"><title>Tecnaria · Assistente documentale</title></head>
          <body style="font-family: sans-serif; max-width:820px; margin:2rem auto;">
            <h1>Tecnaria · Assistente documentale</h1>
            <form action="/ask" method="post">
              <label for="domanda">Fai una domanda (solo contenuti locali):</label><br>
              <input style="width:100%; padding:8px" type="text" id="domanda" name="domanda" placeholder="es. mi parli della P560?" />
              <button style="margin-top:10px" type="submit">Chiedi</button>
            </form>
            <p style="margin-top:2rem; color:#666">Indice: <span id="health">verifica <a href="/health">/health</a></span></p>
          </body>
        </html>
        """, 200, {"Content-Type": "text/html; charset=utf-8"}

@app.route("/ask", methods=["POST"])
def ask():
    """
    Accetta:
      - x-www-form-urlencoded:  key=domanda
      - JSON: { "domanda": "..." }
    """
    q = ""
    # Form classico
    if request.form and "domanda" in request.form:
        q = (request.form.get("domanda") or "").strip()
    else:
        # JSON
        try:
            data = request.get_json(silent=True) or {}
            q = (data.get("domanda") or "").strip()
        except Exception:
            q = ""

    if not q:
        return jsonify({"answer": "Nessuna domanda ricevuta.", "found": False, "from": None})

    # Se l'indice non è pronto, prova a ricostruire in modo non bloccante e rispondi
    if not ensure_index_ready(blocking=False):
        return jsonify({
            "answer": "Indice non pronto. Riprova tra qualche secondo.",
            "found": False,
            "from": None
        })

    try:
        result = search_best_answer(q, threshold=SIM_THRESHOLD, topk=TOPK)

        # piccolo tentativo di “rescue” se non trova, abbassa soglia una volta
        if not result.get("found") and SIM_THRESHOLD > 0.28:
            result = search_best_answer(q, threshold=0.28, topk=TOPK)

        # opzionale: allega un piccolo debug lato client (disattivabile via ENV)
        if DEBUG:
            result["_debug"] = {
                "docs": len(INDEX.get("docs", [])),
                "threshold_used": SIM_THRESHOLD,
                "topk_used": TOPK
            }
        return jsonify(result)
    except Exception as e:
        app.logger.exception("[app] Errore in /ask: %s", e)
        return jsonify({
            "answer": "Errore interno durante la ricerca.",
            "found": False,
            "from": None
        }), 500

@app.route("/reload", methods=["POST"])
def reload():
    """
    Forza la ricostruzione dell'indice.
    """
    try:
        with _build_lock:
            build_index(DOC_DIR)
        docs = len(INDEX.get("docs", [])) if INDEX else 0
        return jsonify({"status": "ok", "docs": docs})
    except Exception as e:
        app.logger.exception("[app] Errore /reload: %s", e)
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    """
    Stato dell’applicazione e indice.
    """
    try:
        docs = len(INDEX.get("docs", [])) if INDEX else 0
    except Exception:
        docs = 0

    resp = {
        "status": "ok" if is_ready() else "building" if _index_started else "empty",
        "docs": docs,
        "threshold": SIM_THRESHOLD,
        "topk": TOPK,
        "doc_dir": os.path.abspath(DOC_DIR),
        "pid": os.getpid(),
    }
    return jsonify(resp)

# =========================
# Avvio locale
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # In locale puoi tenere debug=True; su Render gunicorn gestisce i worker.
    app.run(host="0.0.0.0", port=port, debug=True)
