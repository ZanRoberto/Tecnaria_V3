
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
            "it": "Sei un esperto tecnico dei prodotti Tecnaria. Rispondi in modo preciso, chiaro e professionale.",
            "en": "You are a technical expert on Tecnaria products. Answer clearly, precisely and professionally.",
            "fr": "Vous êtes un expert technique des produits Tecnaria. Répondez de manière claire, précise et professionnelle.",
            "de": "Sie sind ein technischer Experte für Tecnaria-Produkte. Antworten Sie klar, präzise und professionell.",
            "es": "Eres un experto técnico en productos Tecnaria. Responde con claridad, precisión y profesionalidad."
        }
        system_prompt = istruzioni.get(lang, istruzioni["en"])

        # 🔍 Estrazione primaria da Google Docs
        context = estrai_testo_vocami()

        # Fallback: se non contiene la domanda → cerca dal sito Tecnaria
        if user_prompt.lower() not in context.lower():
            context = scrape_tecnaria_results(user_prompt)

        if not context.strip():
            return jsonify({"error": "Nessuna informazione trovata."}), 400

        # ✅ Prompt flessibile e realistico
        prompt = f"""Il seguente testo tecnico contiene informazioni reali tratte dalla documentazione ufficiale di Tecnaria (Google Docs o sito).

Usa queste informazioni per rispondere alla domanda, ma puoi riorganizzare e spiegare meglio se necessario.

TESTO TECNICO:
{context}

DOMANDA:
{user_prompt}

RISPOSTA TECNICA:"""

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        answer = response.choices[0].message.content
        return jsonify({"answer": answer})
    except Exception as e:
        return jsonify({"error": f"Errore: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
