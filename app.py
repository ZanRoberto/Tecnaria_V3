import os
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from openai import OpenAI

# inizializza Flask
app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

# client OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# modello da usare
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
FALLBACK_MODEL = os.getenv("OPENAI_MODEL_FALLBACK", "gpt-4o-mini")

# system prompt: tecnico/commerciale/amministrativo Tecnaria
SYSTEM_PROMPT = """Sei un assistente tecnico e commerciale di Tecnaria S.p.A. (Bassano del Grappa).
Rispondi SEMPRE e SOLO su prodotti, servizi e documentazione Tecnaria (CTF, CTL, CTCEM/VCEM, Diapason, P560 chiodatrice, ecc.).
Non parlare mai di altre aziende.
Stile di risposta:
- Domande tecniche → rispondi come il miglior tecnico di Tecnaria, con dettagli chiari, riferimenti normativi/ETA/EC4, istruzioni di posa e prestazioni.
- Domande commerciali/amministrative → rispondi come il miglior funzionario commerciale/amministrativo di Tecnaria, con contatti, offerte, condizioni.
- Mantieni sempre chiarezza, ordine e professionalità.
- Se non sei sicuro, invita a contattare direttamente l’ufficio tecnico Tecnaria.
"""

# --- ROUTE HOME ---
@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

# --- ROUTE STATUS (healthcheck per Render) ---
@app.route("/status", methods=["GET"])
def status():
    return jsonify({"status": "ok", "service": "Tecnaria Bot - ChatGPT esteso universale"})

# --- ROUTE ASK ---
@app.route("/ask", methods=["POST"])
def ask():
    try:
        data = request.get_json()
        question = data.get("question", "").strip()

        if not question:
            return jsonify({"error": "Domanda mancante"}), 400

        # chiamata a OpenAI
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": question}
                ],
                temperature=float(os.getenv("OPENAI_TEMPERATURE", 0)),
                max_tokens=800
            )
        except Exception:
            # fallback model
            response = client.chat.completions.create(
                model=FALLBACK_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": question}
                ],
                temperature=float(os.getenv("OPENAI_TEMPERATURE", 0)),
                max_tokens=800
            )

        answer = response.choices[0].message.content.strip()
        return jsonify({"answer": answer})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
