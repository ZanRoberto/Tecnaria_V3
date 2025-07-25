import os
import openai
from documenti_utils import estrai_testo_dai_documenti

openai.api_key = os.getenv("OPENAI_API_KEY")

def ottieni_risposta_unificata(domanda):
    # Primo tentativo: cerca nei documenti locali
    risposta_documenti = estrai_testo_dai_documenti(domanda)

    if risposta_documenti != "Nessun documento contiene informazioni rilevanti rispetto alla tua domanda.":
        return risposta_documenti

    # Secondo tentativo: chiedi a OpenAI (modello aggiornato)
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Rispondi come se fossi un esperto tecnico di Tecnaria."},
                {"role": "user", "content": domanda}
            ],
            temperature=0.2,
            max_tokens=1000
        )
        return response['choices'][0]['message']['content'].strip()
    except Exception as e:
        return f"❌ Errore nell'API di OpenAI: {e}"
