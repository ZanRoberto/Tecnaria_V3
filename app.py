import os
from flask import Flask, request, jsonify
from ottieni_risposta_unificata import ottieni_risposta_unificata

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "🤖 Bot Tecnaria attivo. Usa POST /ask"

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json(force=True)
    domanda = data.get("domanda", "").strip()
    if not domanda:
        return jsonify({"risposta": "⚠️ Inserisci una domanda valida."})
    risposta = ottieni_risposta_unificata(domanda)
    return jsonify({"risposta": risposta})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
