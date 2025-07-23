import langdetect
from difflib import get_close_matches

# 📂 Legge il contenuto del file italiano ufficiale
with open("documenti_ordini.txt", "r", encoding="utf-8") as f:
    testo_it = f.read()

# 📂 (Opzionale) Carica i file in altre lingue se presenti
try:
    with open("documenti_ordini_en.txt", "r", encoding="utf-8") as f:
        testo_en = f.read()
except:
    testo_en = ""

# 🔍 Funzione di risposta intelligente basata su similarità
def trova_risposta(domanda, testo):
    domanda = domanda.lower()
    frasi = testo.split(".")
    frasi = [f.strip() for f in frasi if len(f.strip()) > 20]
    match = get_close_matches(domanda, frasi, n=1, cutoff=0.3)
    if match:
        return match[0] + "."
    else:
        return "Non ho trovato una risposta precisa nei materiali ufficiali Tecnaria. Ti consiglio di scrivere a info@tecnaria.com o chiamare il numero 0424 502029."

# 🌐 Rilevamento lingua della domanda
def rileva_lingua(testo):
    try:
        return langdetect.detect(testo)
    except:
        return "unknown"

# 💬 Ciclo di dialogo
while True:
    domanda = input("Tu: ")
    lingua = rileva_lingua(domanda)

    if lingua == "it":
        risposta = trova_risposta(domanda, testo_it)
    elif lingua == "en" and testo_en:
        risposta = trova_risposta(domanda, testo_en)
    else:
        risposta = "Sorry, I can only reply in Italian using official content from Tecnaria. Please ask your question in Italian, or send an email to info@tecnaria.com."

    print("Bot:", risposta)
