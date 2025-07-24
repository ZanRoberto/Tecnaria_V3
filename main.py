import os
import openai
from flask import Flask, render_template, request
from bs4 import BeautifulSoup

app = Flask(__name__)

# Usa la nuova interfaccia OpenAI >= 1.0
client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Carica tutti i contenuti dai documenti .txt nella cartella "documenti"
def carica_documenti():
    documenti = []
    cartella = "documenti"
    for nome_file in os.listdir(cartella):
        if nome_file.endswith(".txt"):
            with open(os.path.join(cartella, nome_file), "r", encoding="utf-8") as file:
                documenti.append(file.read())
    return "\n".join(documenti)

# Pulizia base per contenuti HTML
def rendi_html_sicuro(risposta):
    soup = BeautifulSoup(risposta, "html.parser")
    return str(soup)

@app.route("/", methods=["GET", "POST"])
def index():
    risposta = ""
    if request.method == "POST":
        domanda = request.form["domanda"]
        contesto = carica_documenti()
        messaggi = [
            {"role": "system", "content": "Sei un assistente di Tecnaria. Rispondi solo in italiano e solo usando informazioni dai documenti forniti. Includi link cliccabili se presenti nei documenti."},
            {"role": "user", "content": f"{contesto}\n\nDomanda: {domanda}"}
        ]
        try:
            completion = client.chat.completions.create(
                model="gpt-4",
                messages=messaggi,
                temperature=0.2
            )
            risposta = completion.choices[0].message.content
            risposta = rendi_html_sicuro(risposta)
        except Exception as e:
            risposta = f"Si è verificato un errore: {str(e)}"
    return render_template("index.html", risposta=risposta)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
