# bridge_scraper.py
import os
from openai import OpenAI
from documenti_utils import estrai_testo_dai_documenti

# ✅ Inizializza il client moderno OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def ottieni_risposta_unificata(domanda: str) -> str:
    """
    Unisce contenuti documentali e risposta AI in un'unica risposta finale.
    """
    contesto_documenti = estrai_testo_dai_documenti(domanda)

    try:
        risposta_ai = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Sei un esperto tecnico dei prodotti Tecnaria. Dai risposte serie e precise solo basate sui documenti aziendali e contenuti presenti."},
                {"role": "user", "content": f"{domanda}\n\nContesto dai documenti:\n{contesto_documenti}"}
            ],
            temperature=0.4,
            max_tokens=500
        )
        testo_ai = risposta_ai.choices[0].message.content.strip()
        return testo_ai

    except Exception as e:
        return f"⚠️ Errore durante la generazione della risposta AI: {str(e)}"
