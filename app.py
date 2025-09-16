# app.py
from flask import Flask, request, jsonify, render_template
from configuratore_connettori import pipeline_connettore, get_defaults

# Istanza Flask visibile a Gunicorn
app = Flask(__name__)

# Health check
@app.get("/")
def health():
    return "ok"


# ====== Endpoint JSON per il tuo bot ======
@app.route("/ordina_connettore", methods=["POST"])
def ordina_connettore():
    """
    Body JSON atteso:
    {
      "domanda": "Ordina connettore CTF su lamiera grecata; soletta 60 mm; copriferro 25",
      "defaults": {
         "supporto": "lamiera_grecata",
         "copriferro_mm": 25
      }
    }
    """
    payload = request.get_json(silent=True) or {}
    domanda = (payload.get("domanda") or "").strip()
    defaults = payload.get("defaults") or get_defaults()

    if not domanda:
        return jsonify({"status": "ERROR", "detail": "Campo 'domanda' mancante"}), 400

    data = pipeline_connettore(domanda, defaults=defaults)
    return jsonify(data), 200


# ====== Endpoint form HTML semplice (per test manuali) ======
@app.route("/test", methods=["GET", "POST"])
def test_form():
    risposta = ""
    if request.method == "POST":
        domanda = (request.form.get("domanda") or "").strip()
        if domanda:
            data = pipeline_connettore(domanda)
            if data.get("status") == "ASK_CLIENT":
                risposta = f"⚠️ Mi serve un dato: {data.get('question')}"
            elif data.get("status") == "OK":
                result = data.get("result", {})
                risposta = result.get("mostra_al_cliente") or str(result)
            else:
                risposta = "Errore nel configuratore."
    return render_template("index.html", risposta=risposta)


if __name__ == "__main__":
    # Per debug locale (es. python app.py)
    app.run(host="0.0.0.0", port=5000, debug=True)
