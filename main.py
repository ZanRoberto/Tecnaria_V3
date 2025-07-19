
from flask import Flask, request, jsonify, render_template
import os
from bridge_scraper import estrai_testo_vocami
from scraper_tecnaria import scrape_tecnaria_results
from openai import OpenAI
from langdetect import detect

app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@app.route("/")
def index():
    return render_template("chat.html")

@app.route("/ask", methods=["POST"])
def ask():
    try:
        user_prompt = request.json.get("prompt", "").strip()

        # 🌍 Rileva lingua
        try:
            lang = detect(user_prompt)
        except:
            lang = "en"

        # 🧠 Istruzione multilingua
        istruzioni = {
            "it": "Sei un esperto tecnico dei prodotti Tecnaria. Rispondi solo sulla base del testo fornito. Non inventare.",
            "en": "You are a technical expert on Tecnaria products. Answer only based on the provided text. Do not improvise.",
            "fr": "Vous êtes un expert technique de Tecnaria. Répondez uniquement à partir du texte fourni. N'inventez rien.",
            "de": "Sie sind ein technischer Experte für Tecnaria-Produkte. Antworten Sie nur auf Grundlage des bereitgestellten Textes.",
            "es": "Eres un experto técnico en productos Tecnaria. Responde solo en base al texto proporcionado. No inventes."
        }
        system_prompt = istruzioni.get(lang, istruzioni["en"])

        # 🔍 Estrazione primaria da Google Doc
        context = estrai_testo_vocami()

        # Se il contesto non contiene la domanda → fallback su scraping
        if user_prompt.lower() not in context.lower():
            context = scrape_tecnaria_results(user_prompt)

        if not context.strip():
            return jsonify({"error": "Nessuna informazione trovata."}), 400

        prompt = f"""Il testo seguente è tratto direttamente dalla documentazione ufficiale Tecnaria. Utilizza solo queste informazioni per rispondere alla domanda, senza inventare o generalizzare.

TESTO ORIGINALE:
{context}

DOMANDA:
{user_prompt}

RISPOSTA TECNICA (solo basata sul testo):"""

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )
        answer = response.choices[0].message.content
        return jsonify({"answer": answer})
    except Exception as e:
        return jsonify({"error": f"Errore: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
