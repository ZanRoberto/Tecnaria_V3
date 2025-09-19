from flask import Flask, render_template, request, jsonify

# Configura Flask
app = Flask(__name__, static_folder="static", template_folder="templates")


# ROUTE PRINCIPALE: mostra interfaccia bot
@app.route("/")
def home():
    return render_template("index.html")


# API per rispondere alle domande
@app.route("/api/answer", methods=["POST"])
def api_answer():
    data = request.get_json(force=True) or {}
    question = (data.get("question") or "").strip()
    mode = (data.get("mode") or "dettagliata").strip()
    context = (data.get("context") or "").strip()

    # 🔹 MOCK: risposta di test, così verifichi l’aggancio front-end/back-end
    # Qui andrai a integrare la tua logica reale (GPT + filtri + allegati)
    resp = {
        "answer": f"[OK] Domanda ricevuta\nMode: {mode}\nDomanda: {question}\nContext: {context}",
        "meta": {
            "needs_params": False,
            "required_keys": []
        }
    }
    return jsonify(resp)


# ROUTE di health-check (per Render / debug)
@app.route("/health")
def health():
    return "ok", 200


# Avvio in locale
if __name__ == "__main__":
    # debug=True solo in locale, su Render usa gunicorn
    app.run(host="0.0.0.0", port=8000, debug=True)
