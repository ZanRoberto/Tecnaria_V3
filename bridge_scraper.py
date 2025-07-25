# bridge_scraper.py
import os
from scraper_tecnaria import estrai_info_tecnaria
from documenti_reader import estrai_info_documenti
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=api_key)

def ottieni_risposta_unificata(domanda_utente):
    risposta_sito = estrai_info_tecnaria(domanda_utente)
    risposta_doc = estrai_info_documenti(domanda_utente)
    
    # Componi il prompt con contesto
    prompt = f"""
Rispondi come se fossi un esperto tecnico dell’azienda Tecnaria, usando tono chiaro, professionale ma diretto. 
Analizza la seguente domanda dell’utente:
"{domanda_utente}"

Hai a disposizione queste due fonti:
1. Contenuto del sito Tecnaria (🔍): {risposta_sito}
2. Contenuto dei documenti ufficiali (📄): {risposta_doc}

Se non trovi nulla, rispondi con “Mi dispiace, non ho trovato informazioni rilevanti.”

Dai una risposta unica e utile, senza citare fonti esplicitamente.
    """

    try:
        risposta_ai = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Sei un assistente esperto di prodotti Tecnaria."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.4,
        )
        risposta_finale = risposta_ai.choices[0].message.content.strip()
        # Aggiungi solo l’icona silenziosa a fine risposta
        if "non ho trovato" in risposta_finale.lower():
            return "❌ " + risposta_finale
        else:
            return risposta_finale + " 🤖"

    except Exception as e:
        return "⚠️ Errore nel generare la risposta AI. Controlla l’API key e riprova."
