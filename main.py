import os
import re
import json

from flask import Flask, request, render_template

app = Flask(__name__)

# Carica i documenti dal file
with open("documenti.txt", "r", encoding="utf-8") as f:
    documenti = f.read()

# Funzione base di risposta

def risposta_bot(domanda):
    domanda = domanda.strip()
    lingua = rileva_lingua(domanda)

    if lingua != "it":
        return None  # Flusso esterno gestito da altro modulo

    risposta = cerca_risposta_italiana(domanda)
    return risposta


def rileva_lingua(testo):
    if re.search(r"[àèéìòù]", testo, re.IGNORECASE):
        return "it"
    if re.search(r"[a-zA-Z]", testo):
        parole = testo.lower().split()
        if any(p in parole for p in ["il", "la", "di", "un", "una", "per", "con", "che"]):
            return "it"
        else:
            return "en"
    return "unknown"


def cerca_risposta_italiana(domanda):
    domanda_bassa = domanda.lower()

    if "contatti" in domanda_bassa:
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

    # Cerca risposta generica nei documenti
    pattern = re.compile(rf".*{re.escape(domanda_bassa)}.*", re.IGNORECASE)
    for riga in documenti.splitlines():
        if pattern.match(riga):
            return riga

    return "Mi dispiace, non ho trovato informazioni precise nella documentazione."  # fallback


@app.route("/", methods=["GET", "POST"])
def index():
    risposta = ""
    if request.method == "POST":
        domanda = request.form["domanda"]
        risposta = risposta_bot(domanda)

        if risposta is None:
            return render_template("index.html", risposta="Questo chatbot è progettato per rispondere solo in italiano. Per assistenza in altre lingue, contattaci via email a info@tecnaria.com.")

    return render_template("index.html", risposta=risposta)


if __name__ == "__main__":
    app.run(debug=True)
