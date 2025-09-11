# app.py
# Assistente documentale Tecnaria

import os
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from scraper_tecnaria import build_index, search_answer

# Flask app
app = Flask(__name__)
CORS(app)

# Stato indice
INDEX = None
INDEX_READY = False


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return {"status": "ok", "ready": INDEX_READY}, 200


@app.post("/reload")
def reload_index():
    global INDEX, INDEX_READY
    try:
        docs_folder = os.environ.get("DOC_DIR", "documenti_gTab")
        INDEX = build_index(docs_folder)
        INDEX_READY = True
        return {
            "ok": True,
            "blocks": INDEX.get("blocks", 0),
            "files": INDEX.get("files", 0),
            "lines": INDEX.get("lines", 0),
        }, 200
    except Exception as e:
        INDEX_READY = False
        return {"ok": False, "error": str(e)}, 500


@app.post("/ask")
def ask():
    global INDEX, INDEX_READY
    data = request.get_json(silent=True) or {}
    q = (data.get("question") or "").strip()

    if not q:
        return {"answer": "", "found": False}, 200
    if not INDEX_READY or not INDEX:
        return {
            "answer": "⚠️ Indice non pronto. Premi 'Ricarica indice' prima di fare domande.",
            "found": False,
        }, 200

    answer, found, score, path, line = search_answer(INDEX, q)
    return {
        "answer": answer or "",
        "found": bool(found),
        "score": float(score or 0),
        "path": path,
        "line": line,
    }, 200


if __name__ == "__main__":
    # Avvio in locale (non usato su Render, dove parte gunicorn)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
