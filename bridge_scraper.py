from scraper_tecnaria import estrai_info_tecnaria
from documenti_utils import estrai_testo_dai_documenti  # <- nome aggiornato qui
from openai import OpenAI
import os

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def ottieni_risposta_unificata(domanda_utente: str) -> str:
    risposta_documenti = estrai_testo_dai_documenti(domanda_utente)
    risposta_tecnaria = estrai_info_tecnaria(domanda_utente)

    prompt = f"""Agisci come esperto tecnico dei prodotti Tecnaria.
Rispondi in modo dettagliato, concreto e senza frasi vaghe.
Consulta anche le seguenti informazioni:

📄 Dai documenti:
{risposta_documenti}

🌐 Dal sito Tecnaria:
{risposta_tecnaria}

🧾 Domanda dell’utente:
{domanda_utente}

Rispondi come se fossi un tecnico specializzato di Tecnaria, chiaro e preciso, senza citare fonti.
"""

    try:
        completamento = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4
        )
        return completamento.choices[0].message.content.strip()
    except Exception as e:
        return f"❌ Errore nel generare la risposta AI: {str(e)}"
