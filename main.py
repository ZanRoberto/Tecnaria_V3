from flask import Flask, render_template, request
import os
import openai
from bs4 import BeautifulSoup

# Inizializza Flask
app = Flask(__name__)

# Chiave API OpenAI (deve essere impostata come variabile d’ambiente su Render)
openai.api_key = os.getenv("OPENAI_API_KEY")

# Caricamento del contenuto del file documenti.txt
with open("documenti.txt", "r", encoding="utf-8") as file:
    documenti = file.read()

# Funzione per pulire e formattare HTML nella risposta
def formatta_testo_html(text):
    return BeautifulSoup(text, "html.parser").prettify()

@app.route("/", methods=["GET", "POST"])
def index():
    risposta = ""
    if request.method == "POST":
        domanda = request.form["domanda"]

        # Prompt al modello
        prompt = f"""
Rispondi alla seguente domanda utilizzando solo le informazioni contenute nel seguente documento tecnico di Tecnaria (formattato in HTML):

DOMANDA: {domanda}
DOCUMENTO:
{documenti}
Rispondi in italiano e includi link cliccabili se presenti nel testo.
"""
        try:
            completamento = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1000
            )
            risposta = completamento["choices"][0]["message"]["content"]
        except Exception as e:
            risposta = f"Si è verificato un errore: {e}"

    return render_template("index.html", risposta=risposta)

# Avvio del server Flask con host e porta compatibili con Render
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
