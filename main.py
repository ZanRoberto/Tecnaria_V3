
from flask import Flask, request, jsonify, render_template
import os
from bridge_scraper import estrai_testo_vocami
from scraper_tecnaria import scrape_tecnaria_results
from openai import OpenAI
import fasttext

# Caricamento modello lingua FastText
lang_model = fasttext.load_model("lid.176.ftz")

app = Flask(__name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@app.route("/")
def index():
    return render_template("chat.html")

@app.route("/ask", methods=["POST"])
def ask():
    try:
        user_prompt = request.json.get("prompt", "").strip()
        if not user_prompt:
            return jsonify({"error": "Domanda non valida."}), 400

        # Rilevamento lingua della domanda
        detected_lang = lang_model.predict(user_prompt)[0][0].replace("__label__", "")

        # Estrazione testo dai documenti condivisi
        context = estrai_testo_vocami()

        # Se la domanda non è coperta dal contesto, fallback con scraping
        if user_prompt.lower() not in context.lower():
            extra_context = scrape_tecnaria_results(user_prompt)
            context += f"\n\n{extra_context}"

        # Prompt intelligente
        prompt = f"""Sei un esperto tecnico di Tecnaria. Rispondi in modo preciso e utile alla domanda seguente. Se nel testo sono presenti strumenti consigliati (es. Spit Pulsa 560), includili come se fossero ufficialmente associati.

Testo disponibile:
{context}

Domanda:
{user_prompt}

Risposta nella stessa lingua della domanda:"""

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Rispondi usando solo le informazioni fornite nel testo, includendo anche strumenti consigliati come se fossero prodotti ufficiali. Usa la lingua della domanda."},
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
