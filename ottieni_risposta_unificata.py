from estrai_dai_documenti import estrai_testo_dai_documenti
from deep_translator import GoogleTranslator
from documenti_utils import normalizza_testo
import os

def ottieni_risposta_unificata(domanda, lingua="it"):
    risposta = ""

    # Estrai testo dai documenti nella cartella /documenti
    testo_completo = estrai_testo_dai_documenti("documenti")

    # Applichiamo normalizzazione
    testo_normalizzato = normalizza_testo(testo_completo)

    # Verifica se la domanda è in una lingua diversa
    if lingua != "it":
        domanda = GoogleTranslator(source='auto', target='it').translate(domanda)

    # Logica di risposta semplificata (sostituibile con AI avanzata o regex mirata)
    if "connettori" in domanda.lower() and "legno" in domanda.lower():
        if "immagini" in domanda.lower() or "foto" in domanda.lower():
            with open("documenti/connettori_legno_tecnaria_immagini.html", "r", encoding="utf-8") as f:
                risposta = f.read()
        else:
            with open("documenti/connettori_legno_tecnaria.txt", "r", encoding="utf-8") as f:
                risposta = f.read()
    else:
        # Risposta generica se non c'è un match specifico
        risposta = "🔍 Al momento non ho trovato una risposta precisa. Prova a riformulare la domanda oppure consulta i documenti ufficiali su https://www.tecnaria.com."

    # Traduzione finale se serve
    if lingua != "it":
        risposta = GoogleTranslator(source='auto', target=lingua).translate(risposta)

    return risposta
