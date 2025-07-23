from flask import Flask, request, jsonify, render_template
from openai import OpenAI
import os
import fasttext
from deep_translator import GoogleTranslator

# Carica modello per rilevamento lingua
lang_model = fasttext.load_model("lid.176.ftz")

# Inizializza Flask e OpenAI
app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Funzione per rilevare la lingua della domanda
def rileva_lingua(testo):
    try:
        pred = lang_model.predict(testo.replace("\n", ""))[0][0]
        return pred.replace("__label__", "")
    except:
        return "it"

# Funzione per tradurre la risposta (in fallback)
def traduci(testo, target_lang):
    try:
        return GoogleTranslator(source='auto', target=target_lang).translate(testo)
    except:
        return testo

@app.route("/")
def index():
    return render_template("chat.html")

@app.route("/ask", methods=["POST"])
def ask():
    try:
        user_prompt = request.json.get("prompt", "").strip()
        lingua_domanda = rileva_lingua(user_prompt)

        # Flusso ITALIANO - usa solo documenti.txt + scraping sito Tecnaria
        if lingua_domanda == "it":
            if os.path.exists("documenti.txt"):
                with open("documenti.txt", "r", encoding="utf-8") as f:
                    context = f.read()
            else:
                context = ""

            # Prompt tecnico
            system_prompt = "Sei un esperto dei prodotti Tecnaria. Rispondi sempre in italiano, in modo tecnico, solo con contenuti del file e/o dal sito Tecnaria.com."

            prompt = f"""Contesto:
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
            return jsonify({"answer": risposta})

        # Flusso MULTILINGUA
        else:
            # Risposta in quella lingua basata su breve descrizione generale
            system_prompt = f"You are a multilingual assistant that responds in {lingua_domanda} with technical, professional tone about Tecnaria products."
            base_context = """Tecnaria produces structural connectors for composite floors: wood-concrete, steel-concrete and concrete-concrete. These connectors are used to increase the bearing capacity of slabs and meet seismic regulations. Visit www.tecnaria.com for more."""

            prompt = f"""
Context:
{base_context}

User question:
{user_prompt}

Answer in {lingua_domanda}:
"""

            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.4
            )
            risposta = response.choices[0].message.content.strip()
            return jsonify({"answer": risposta})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
