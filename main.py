
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

        # 🌍 Rileva lingua del prompt
        try:
            lang = detect(user_prompt)
        except:
            lang = "en"  # fallback se detection fallisce

        # 📌 Istruzioni multilingua
        istruzioni = {
            "it": "Sei un esperto tecnico dei prodotti Tecnaria. Rispondi con precisione e chiarezza in italiano.",
            "en": "You are a technical expert on Tecnaria products. Answer clearly and precisely in English.",
            "fr": "Vous êtes un expert technique des produits Tecnaria. Répondez de manière claire et précise en français.",
            "de": "Sie sind ein technischer Experte für Tecnaria-Produkte. Antworten Sie klar und präzise auf Deutsch.",
            "es": "Eres un experto técnico en productos Tecnaria. Responde con claridad y precisión en español."
        }
        system_prompt = istruzioni.get(lang, istruzioni["en"])

        # 🔍 Estrai contenuto dal documento Google
        context = estrai_testo_vocami()

        # Se il contenuto non include la domanda, fallback su scraping
        if user_prompt.lower() not in context.lower():
            context = scrape_tecnaria_results(user_prompt)

        if not context.strip():
            return jsonify({"error": "Nessuna informazione trovata."}), 400

        prompt = f"""Contesto tecnico:
{context}

Domanda:
{user_prompt}

Risposta tecnica:"""

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
