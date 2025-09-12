# -*- coding: utf-8 -*-
import os
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# Import dal tuo scraper
from scraper_tecnaria import build_index, search_best_answer, INDEX

# Config
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))

# Flask app
app = Flask(__name__)
CORS(app)

# -------------------------------
# Rotte
# -------------------------------

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/ask", methods=["POST"])
def ask():
    q = (request.form.get("domanda") or "").strip()
    if not q:
        return jsonify({"answer": "Nessuna domanda ricevuta.", "found": False})
    
    result = search_best_answer(q, threshold=SIM_THRESHOLD, topk=TOPK)
    return jsonify(result)

@app.route("/reload", methods=["POST"])
def reload():
    build_index(DOC_DIR)
    return jsonify({"status": "ok", "docs": len(INDEX)})

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "docs": len(INDEX),
        "threshold": SIM_THRESHOLD,
        "topk": TOPK
    })

# -------------------------------
# Avvio locale
# -------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
