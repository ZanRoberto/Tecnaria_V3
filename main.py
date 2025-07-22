from flask import Flask, request, jsonify, render_template
from openai import OpenAI
import os
import fasttext
from langdetect import detect
from deep_translator import GoogleTranslator

# Carica modello fastText una sola volta
lang_model = fasttext.load_model("lid.176.ftz")

app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def rileva_lingua(prompt):
    try:
        pred = lang_model.predict(prompt.replace("\n", ""))[0][0]
        return pred.replace("__label__", "")
    except:
        return detect(prompt)

def traduci_testo(testo, lingua_target):
    try:
        return GoogleTranslator(source='auto', target=lingua_target).translate(testo)
    except:
        return testo

@app.route("/")
def index():
    return render_template("chat.html")

@app.route("/ask", methods=["POST"])
def ask():
    try:
        user_prompt = request.json.get("prompt", "").strip()
        lingua = rileva_lingua(user_prompt)

        # 🔁 Legge il contenuto tecnico dal file aggiornato da bridge_scraper
        if os.path.exists("documenti.txt"):
            with open("documenti.txt", "r", encoding="utf-8") as f:
                context = f.read()
        else:
            context = ""

        if not context.strip():
            return jsonify({"error": "Nessuna informazione trovata."}), 400

        system_prompt = f"Sei un esperto tecnico dei prodotti Tecnaria. Rispondi in modo professionale nella lingua: {lingua}."

        prompt = f"""Contesto tecnico:
{context}

Domanda:
{user_prompt}

Risposta:"""

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )

        risposta = response.choices[0].message.content.strip()

        # 🔁 Fallback: traduci se risposta è in lingua diversa
        lingua_risposta = rileva_lingua(risposta)
        if lingua_risposta != lingua:
            risposta = traduci_testo(risposta, lingua)

        return jsonify({"answer": risposta})

    except Exception as e:
        return jsonify({"error": f"Errore: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
