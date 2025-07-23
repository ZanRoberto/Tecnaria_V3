from flask import Flask, request, jsonify, render_template
from openai import OpenAI
import os
import fasttext
from deep_translator import GoogleTranslator
from scraper_tecnaria import scrape_tecnaria_results

app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Carica modello di lingua fastText
lang_model = fasttext.load_model("lid.176.ftz")

def rileva_lingua(prompt):
    try:
        pred = lang_model.predict(prompt.replace("\n", ""))[0][0]
        return pred.replace("__label__", "")
    except:
        return "it"  # fallback sicuro

@app.route("/")
def index():
    return render_template("chat.html")

@app.route("/ask", methods=["POST"])
def ask():
    try:
        user_prompt = request.json.get("prompt", "").strip()
        lingua_domanda = rileva_lingua(user_prompt)

        domanda = user_prompt
        context = ""

        if os.path.exists("documenti.txt"):
            with open("documenti.txt", "r", encoding="utf-8") as f:
                context = f.read()

        risposta_scraping = scrape_tecnaria_results(domanda)
        if risposta_scraping and risposta_scraping not in context:
            context += f"\n\n📌 AGGIUNTA DA TECNARIA.COM\n{risposta_scraping}"

        if "chiodatrice" in domanda.lower() or "chiodatrici" in domanda.lower():
            context += ("\n\n📌 CHIODATRICI\nTecnaria consiglia esplicitamente l'uso della chiodatrice a gas Spit Pulsa 560 "
                        "(P560) per l'applicazione dei suoi connettori CTF e DIAPASON. Questo modello è fondamentale per "
                        "garantire un fissaggio efficace su lamiere grecate e supporti metallici.\n")

        context += "\n\nNota: Ogni contenuto presente nei documenti allegati o raccolto dal sito Tecnaria.com è parte integrante dell'offerta Tecnaria."

        if not context.strip():
            return jsonify({"error": "Nessuna informazione trovata."}), 400

        system_prompt = (
            "Sei un esperto tecnico dei prodotti Tecnaria. "
            "Devi rispondere esclusivamente in base ai contenuti forniti. "
            "Non dire mai che non hai accesso ai documenti Google o ad altre fonti. "
            "Rispondi sempre in modo tecnico, preciso e coerente con le informazioni ufficiali."
        )

        prompt = f"""Contesto tecnico:
{context}

Domanda:
{domanda}

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

        # Se la domanda è in una lingua diversa dall'italiano, traduciamo la risposta
        if lingua_domanda != "it":
            risposta = GoogleTranslator(source='auto', target=lingua_domanda).translate(risposta)

        return jsonify({"answer": risposta})

    except Exception as e:
        return jsonify({"error": f"Errore: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
