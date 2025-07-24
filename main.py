from flask import Flask, request, render_template
from deep_translator import GoogleTranslator
from langdetect import detect
import os
import openai

# Inizializzazione Flask
app = Flask(__name__)

# API Key OpenAI da variabile d’ambiente
openai.api_key = os.getenv("OPENAI_API_KEY")

# Carica i documenti
with open("documenti.txt", "r", encoding="utf-8") as f:
    documenti = f.read()

# Funzione per rilevare lingua
def rileva_lingua(testo):
    try:
        return detect(testo)
    except Exception:
        return "unknown"

@app.route("/", methods=["GET", "POST"])
def index():
    risposta = ""
    if request.method == "POST":
        domanda = request.form["domanda"]
        lingua = rileva_lingua(domanda)

        try:
            # Prompt finale
            prompt = f"""Agisci come un assistente esperto di prodotti Tecnaria. Usa solo il contenuto seguente per rispondere:

{documenti}

Domanda: {domanda}
Risposta:"""

            # Nuova sintassi OpenAI >=1.0.0
            response = openai.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "Sei un esperto dei prodotti Tecnaria. Rispondi solo con informazioni contenute nel documento fornito. Se non sai qualcosa, dì che non è specificato."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=1000,
            )
            risposta = response.choices[0].message.content.strip()

            # Traduci se domanda non in italiano
            if lingua != "it":
                risposta = GoogleTranslator(source='it', target=lingua).translate(risposta)

        except Exception as e:
            risposta = f"Si è verificato un errore interno: {str(e)}"

    return render_template("index.html", risposta=risposta)

if __name__ == "__main__":
    app.run(debug=True)
