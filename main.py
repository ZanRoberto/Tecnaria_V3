import os
import re

from flask import Flask, request, render_template

app = Flask(__name__)

# Carica i documenti dal file
with open("documenti.txt", "r", encoding="utf-8") as f:
    documenti = f.read()


def risposta_bot(domanda):
    domanda = domanda.strip()
    lingua = rileva_lingua(domanda)

    if lingua != "it":
        return None  # DOMANDA NON ITALIANA: flusso esterno

    # Se è in italiano → cerca la risposta
    return cerca_risposta_italiana(domanda)


def rileva_lingua(testo):
    testo = testo.lower()
    parole_italiane = ["il", "la", "di", "un", "una", "per", "con", "che", "del", "dei", "della", "mi", "dove", "come"]
    if any(p in testo.split() for p in parole_italiane):
        return "it"
    return "altro"


def cerca_risposta_italiana(domanda):
    domanda = domanda.lower()

    # Risposta diretta se domanda contiene "contatti"
    if "contatti" in domanda or "telefono" in domanda or "email" in domanda:
        return (
            "<strong>Contatti Tecnaria</strong><br>"
            "Tecnaria S.p.A.<br>"
            "Viale Pecori Giraldi, 55<br>"
            "36061 Bassano del Grappa (VI) - ITALIA<br>"
            "P.IVA: 01277680243<br>"
            "Telefono: <a href=\"tel:+390424502029\">+39 0424 502029</a><br>"
            "Fax: +39 0424 502386<br>"
            "Email: <a href=\"mailto:info@tecnaria.com\">info@tecnaria.com</a><br>"
            "Sito web: <a href=\"https://www.tecnaria.com\">www.tecnaria.com</a>"
        )

    # Altrimenti cerca corrispondenza nei documenti
    for paragrafo in documenti.split("\n\n"):
        if domanda in paragrafo.lower():
            return paragrafo

    # Altrimenti risposta fallback
    return "Mi dispiace, non ho trovato informazioni precise nella documentazione."


@app.route("/", methods=["GET", "POST"])
def index():
    risposta = ""
    if request.method == "POST":
        domanda = request.form["domanda"]
        risposta = risposta_bot(domanda)

        if risposta is None:
            risposta = "Mi dispiace, posso rispondere solo a domande in italiano."

    return render_template("index.html", risposta=risposta)


if __name__ == "__main__":
    app.run(debug=True)
