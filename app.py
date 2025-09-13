# -*- coding: utf-8 -*-
import os
import time
from flask import Flask, request, jsonify, render_template
from scraper_tecnaria import build_index, is_ready, search_best_answer

app = Flask(__name__, template_folder="templates", static_folder="static")

# ======================
# ENV & Stato auto-index
# ======================
DOC_DIR = os.getenv("DOC_DIR", "documenti_gTab")
SINAPSI_PATH = os.getenv("SINAPSI_BOT_JSON", "SINAPSI_BOT.JSON")

AUTO_REINDEX_WATCH = os.getenv("AUTO_REINDEX_WATCH", "1") == "1"    # guarda i .txt
WATCH_SINAPSI      = os.getenv("WATCH_SINAPSI", "1") == "1"         # guarda SINAPSI_BOT_JSON
AUTO_REINDEX_TTL   = int(os.getenv("AUTO_REINDEX_TTL", "0"))        # secondi; 0 = controlla sempre
SHOW_MATCHED_QUESTION = os.getenv("SHOW_MATCHED_QUESTION", "0") == "1"

_last_index_built_at = 0.0
_last_docs_mtime = 0.0
_last_sinapsi_mtime = 0.0

def _tree_mtime(path: str) -> float:
    most = 0.0
    if not os.path.exists(path):
        return 0.0
    for root, _, files in os.walk(path):
        for fn in files:
            if fn.lower().endswith(".txt"):
                try:
                    m = os.path.getmtime(os.path.join(root, fn))
                    if m > most:
                        most = m
                except Exception:
                    pass
    return most

def _file_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except Exception:
        return 0.0

def ensure_index_fresh(force: bool = False):
    """Ricostruisce l'indice quando serve (startup, reload, txt/sinapsi cambiati, TTL)."""
    global _last_index_built_at, _last_docs_mtime, _last_sinapsi_mtime

    now = time.time()
    needs = force or not is_ready()

    # TTL
    if AUTO_REINDEX_TTL > 0 and (now - _last_index_built_at) > AUTO_REINDEX_TTL:
        needs = True

    # Watch .txt
    if AUTO_REINDEX_WATCH:
        docs_m = _tree_mtime(DOC_DIR)
        if docs_m > _last_docs_mtime:
            needs = True

    # Watch Sinapsi JSON
    if WATCH_SINAPSI:
        sin_m = _file_mtime(SINAPSI_PATH)
        if sin_m > _last_sinapsi_mtime:
            needs = True

    if needs:
        n = build_index(DOC_DIR)
        _last_index_built_at = now
        _last_docs_mtime = _tree_mtime(DOC_DIR)
        _last_sinapsi_mtime = _file_mtime(SINAPSI_PATH)
        app.logger.warning(f"[autoindex] rebuilt index: docs_count={n} dir={DOC_DIR} sinapsi={SINAPSI_PATH}")
        return {"rebuilt": True, "docs": n}

    return {"rebuilt": False}

# ==============
# Startup sicuro
# ==============
try:
    app.logger.warning(f"[startup] Building index from: {DOC_DIR}")
    ensure_index_fresh(force=True)
    app.logger.warning(f"[startup] ready={is_ready()}")
except Exception as e:
    app.logger.exception("[startup] index build failed")

# =======
# Routes
# =======
@app.route("/", methods=["GET"])
def home():
    # Se hai templates/index.html lo usa; altrimenti risponde semplice.
    try:
        return render_template("index.html")
    except Exception:
        return "Tecnaria · Assistente documentale", 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "ready": is_ready(), "doc_dir": DOC_DIR, "sinapsi": SINAPSI_PATH}), 200

@app.route("/reload", methods=["POST"])
def reload_index():
    try:
        out = ensure_index_fresh(force=True)
        return jsonify({"ok": True, **out}), 200
    except Exception as e:
        app.logger.exception("[reload] failed")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json(silent=True) or {}
    q = (data.get("q") or "").strip()
    if not q:
        return jsonify({"found": False, "answer": "", "error": "empty question"}), 400

    try:
        ensure_index_fresh(force=False)
    except Exception:
        app.logger.exception("[ask] ensure_index_fresh error")

    res = search_best_answer(q)

    if not SHOW_MATCHED_QUESTION:
        res.pop("matched_question", None)

    return jsonify(res), 200

# ---- main (utile per esecuzione locale) ----
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("DEBUG", "0") == "1")
