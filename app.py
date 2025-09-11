# app.py
import os
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
import scraper_tecnaria as st

# === Flask ===
app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# === Reload indice all'avvio ===
REINDEX_ON_STARTUP = os.environ.get("REINDEX_ON_STARTUP", "1") == "1"
if REINDEX_ON_STARTUP:
    st.build_index()

# === Routes tecniche ===
@app.get("/health")
def health():
    info = st.list_index()
    return jsonify({
        "status": "ok",
        "docs": info.get("count", 0),
        "lines": info.get("lines", 0),
        "blocks": info.get("blocks", 0)
    })

@app.post("/reload")
def reload_index():
    info = st.reload_index()
    return jsonify({"status": "reloaded", **info})

@app.get("/ls")
def ls():
    info = st.list_index()
    return jsonify({"status": "ok", **info})

# === API principale ===
@app.post("/ask")
def ask():
    data = request.get_json(silent=True) or {}
    q = (data.get("q") or "").strip()
    if not q:
        return jsonify({"ok": False, "answer": "Inserisci una domanda.", "debug": {}}), 400
    res = st.search_best_answer(q)
    if not res["found"]:
        # Risposta sobria, senza citare "documenti locali"
        return jsonify({"ok": False, "answer": "Non ho trovato una risposta precisa. Prova con una formulazione leggermente diversa.", "debug": res}), 200
    # Ritorno solo la RISPOSTA
    return jsonify({"ok": True, "answer": res["answer"], "debug": res})

# === UI ===
@app.get("/")
def home():
    # index.html in templates/
    return render_template("index.html")

# opzionale: servire logo/immagini statiche
@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

if __name__ == "__main__":
    # Per debug locale; su Render usa gunicorn
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
