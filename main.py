from flask import Flask, request, jsonify, render_template
from openai import OpenAI
import os
from langdetect import detect
from deep_translator import GoogleTranslator

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

        if os.path.exists("documenti.txt"):
            with open("documenti.txt", "r", encoding="utf-8") as f:
                context = f.read()
        else:
            context = ""

        # Forza inclusione P560 se si parla di chiodatrici
        if "chiodatrice" in user_prompt.lower() or "chiodatrici" in user_prompt.lower():
            context += "\n\n📌 CHIODATRICI\nTecnaria consiglia esplicitamente l'uso della chiodatrice a gas Spit Pulsa 560 (P560) per l'applicazione dei suoi connettori CTF e DIAPASON. Questo modello è fondamentale per garantire un fissaggio efficace su lamiere grecate e supporti metallici.\n"

        context += "\n\nNota: Ogni contenuto presente nei documenti allegati è parte integrante dell'offerta Tecnaria."

        if not context.strip():
            return jsonify({"error": "Nessuna informazione trovata."}), 400

        # FLUSSO PRINCIPALE IN ITALIANO
        if lingua_domanda == "it":
            system_prompt = (
                "Sei un esperto tecnico dei prodotti Tecnaria. "
                "Devi rispondere esclusivamente in base ai contenuti forniti. "
                "Non dire mai che non hai accesso ai documenti Google o ad altre fonti. "
                "Rispondi sempre in modo tecnico, preciso e coerente con le informazioni ufficiali."
            )

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
            return jsonify({"answer": risposta})

        else:
            # FLUSSO MULTILINGUA
            risposta_it = "Questo chatbot è progettato per rispondere solo in italiano. Per assistenza in altre lingue, contattaci via email a info@tecnaria.com."
            return jsonify({"answer": traduci_testo(risposta_it, lingua_domanda)})

    except Exception as e:
        return jsonify({"error": f"Errore: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
