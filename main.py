import re
from flask import Flask, request, jsonify
from difflib import get_close_matches
from deep_translator import GoogleTranslator

app = Flask(__name__)

# Legge tutto il contenuto di documenti.txt (caricato in anticipo)
def leggi_documento_locale():
    with open("documenti.txt", "r", encoding="utf-8") as f:
        return f.read()

# Funzione intelligente che risponde a domande con traduzione e somiglianza
def genera_risposta(domanda_utente, testo_documenti):
    # Traduci la domanda in italiano e rileva la lingua originale
    traduttore = GoogleTranslator(source='auto', target='it')
    domanda_it = traduttore.translate(domanda_utente)
    lingua_utente = traduttore.source

    # Per log/debug automatico
    print(f"🌍 Lingua rilevata: {lingua_utente} | Domanda originale: {domanda_utente} | Tradotta: {domanda_it}")

    frasi = [fr.strip() for fr in testo_documenti.split('.') if len(fr.strip()) > 10]
    corrispondenze = get_close_matches(domanda_it.lower(), frasi, n=1, cutoff=0.3)

    if corrispondenze:
        risposta_it = corrispondenze[0]
        risposta_tradotta = GoogleTranslator(source='it', target=lingua_utente).translate(risposta_it)
        return risposta_tradotta
    else:
        risposta_default = "Ho letto il documento, ma non ho trovato una frase che risponda direttamente alla tua domanda."
        return GoogleTranslator(source='it', target=lingua_utente).translate(risposta_default)

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    domanda = data.get("domanda", "")
    testo = leggi_documento_locale()
    risposta = genera_risposta(domanda, testo)
    return jsonify({"risposta": risposta})

if __name__ == "__main__":
    app.run(debug=True)
