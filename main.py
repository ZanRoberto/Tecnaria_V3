from flask import Flask, request, jsonify, render_template
import os
from bridge_scraper import estrai_testo_vocami
from scraper_tecnaria import scrape_tecnaria_results
from openai import OpenAI
import fasttext
import re

app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
lang_model = fasttext.load_model("lid.176.bin")

def detect_language(text):
    prediction = lang_model.predict(text.replace("\n", " "))[0][0]
    return prediction.replace("__label__", "")

@app.route("/")
def index():
    return render_template("chat.html")

@app.route("/ask", methods=["POST"])
def ask():
    try:
        user_prompt = request.json.get("prompt", "").strip()
        language = detect_language(user_prompt)

        context = estrai_testo_vocami()
        smart_match = ""

        if user_prompt.lower() not in context.lower():
            smart_match = scrape_tecnaria_results(user_prompt)

        if not smart_match.strip():
            smart_match = context  # fallback

        prompt = f"""Contesto tecnico:
{smart_match}

Domanda:
{user_prompt}

Rispondi nella lingua: {language}.
Risposta tecnica:"""

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Sei un esperto tecnico dei prodotti Tecnaria. Rispondi in modo chiaro e pertinente."},
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
