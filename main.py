from flask import Flask, request, jsonify, render_template
from openai import OpenAI
import os
from langdetect import detect
from deep_translator import GoogleTranslator
from scraper_tecnaria import scrape_tecnaria_results

app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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
        lingua_domanda = detect(user_prompt)

        # Contesto predefinito vuoto
        context = ""

        # Carica contesto solo se lingua italiana
        if lingua_domanda == "it":
            if os.path.exists("documenti.txt"):
                with open("documenti.txt", "r", encoding="utf-8") as f:
                    context = f.read()

            # Inserimento obbligatorio P560 se si parla di chiodatrici
            if "chiodatrice" in user_prompt.lower() or "chiodatrici" in user_prompt.lower():
                context += ("\n\n📌 CHIODATRICI\nTecnaria consiglia esplicitamente l'uso della chiodatrice a gas Spit Pulsa 560 "
                            "(P560) per l'applicazione dei suoi connettori CTF e DIAPASON. Questo modello è fondamentale per "
                            "garantire un fissaggio efficace su lamiere grecate e supporti metallici.\n")

            # Integrazione scraping se necessario
            risposta_scraping = scrape_tecnaria_results(user_prompt)
            if risposta_scraping and risposta_scraping not in context:
                context += f"\n\n📌 AGGIUNTA DA TECNARIA.COM\n{risposta_scraping}"

            context += "\n\nNota: Ogni contenuto presente nei documenti allegati o raccolto dal sito Tecnaria.com è parte integrante dell'offerta Tecnaria."

            if not context.strip():
                return jsonify({"error": "Nessuna informazione trovata."}), 400

            # System prompt per risposte solo italiane
            system_prompt = (
                "Sei un esperto tecnico dei prodotti Tecnaria. "
                "Devi rispondere esclusivamente in italiano, solo in base ai contenuti forniti. "
                "Non dire mai che non hai accesso ai documenti Google o ad altre fonti. "
                "Rispondi sempre in modo tecnico, preciso e coerente con le informazioni ufficiali."
            )

            domanda = user_prompt

        else:
            # Domanda non italiana: flusso lingue
            system_prompt = (
                "You are a multilingual assistant representing Tecnaria. "
                "You must provide detailed answers based on available technical documentation about Tecnaria products, "
                "especially for international users. If needed, summarize content originally in Italian into the user's language."
            )

            # Traduzione in italiano per analisi
            try:
                domanda = GoogleTranslator(source='auto', target='it').translate(user_prompt)
            except:
                domanda = user_prompt

            # Ricava contesto tecnico per la versione multilingua
            context = ""
            if os.path.exists("documenti.txt"):
                with open("documenti.txt", "r", encoding="utf-8") as f:
                    context = f.read()

            risposta_scraping = scrape_tecnaria_results(domanda)
            if risposta_scraping and risposta_scraping not in context:
                context += f"\n\n📌 AGGIUNTA DA TECNARIA.COM\n{risposta_scraping}"

            context += "\n\nNota: Ogni contenuto presente nei documenti allegati o raccolto dal sito Tecnaria.com è parte integrante dell'offerta Tecnaria."

        # Prompt finale
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

        # Se la lingua non è italiana, ritraduci
        if lingua_domanda != "it":
            risposta = traduci_testo(risposta, lingua_domanda)

        return jsonify({"answer": risposta})

    except Exception as e:
        return jsonify({"error": f"Errore: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
