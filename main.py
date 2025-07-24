from flask import Flask, render_template, request
import os
from bridge_scraper import ottieni_risposta_unificata
from langdetect import detect

app = Flask(__name__)

# Homepage con form
@app.route("/", methods=["GET", "POST"])
def index():
    risposta = ""
    if request.method == "POST":
        # Acquisizione domanda dell’utente
        domanda = request.form["domanda"]

        # Rileva la lingua della domanda (per capire se usare italiano o no)
        lingua = rileva_lingua(domanda)

        # Ottiene la risposta da documenti + sito Tecnaria
        risposta = ottieni_risposta_unificata(domanda, lingua)

    # Mostra la pagina HTML con la risposta
    return render_template("index.html", risposta=risposta)

# Funzione che rileva automaticamente la lingua della domanda
def rileva_lingua(testo):
    try:
        return detect(testo)
    except:
        return "it"  # default italiano se qualcosa va storto

# Avvio server Flask
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=10000)
