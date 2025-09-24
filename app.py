import os
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from openai import OpenAI

app = Flask(__name__)
CORS(app)

# 🔑 Inizializza client OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 🎯 System prompt obbligatorio per lo stile "ChatGPT tecnico Tecnaria"
SYSTEM_PROMPT = """
Sei il miglior tecnico esperto di Tecnaria S.p.A. (Bassano del Grappa).
Rispondi **solo** su prodotti e servizi Tecnaria (CTF, CTL, CTL MAXI, CTCEM/VCEM, Diapason, P560, ecc.).
Stile di risposta richiesto:
- Apri sempre con “Sì”, “No” oppure “Dipende”.
- Fornisci subito il modello esatto consigliato (es. “CTL MAXI 12/040”).
- Aggiungi motivazioni tecniche (spessori, viti, altezze, alternative).
- Usa sempre bullet point operativi e note pratiche di cantiere.
- Concludi con riferimenti a norme, istruzioni di posa o documentazione Tecnaria.
- Non divagare mai su prodotti non Tecnaria e non inventare altre marche.
- Risposte sempre chiare, complete, proporzionate al livello tecnico della domanda.
"""

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/ask", methods=["POST"])
def ask():
    data = request.json
    question = data.get("question", "")

    if not question:
        return jsonify({"error": "Domanda mancante"}), 400

    try:
        # 🔥 Chiamata al modello
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question}
            ],
            temperature=float(os.getenv("OPENAI_TEMPERATURE", 0)),
            max_tokens=int(os.getenv("MAX_TOKENS", 800))
        )

        answer = response.choices[0].message.content.strip()
        return jsonify({"answer": answer})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
