import openai
import os
from documenti_utils import estrai_testo_dai_documenti

openai.api_key = os.getenv("OPENAI_API_KEY")

def ottieni_risposta_unificata(domanda):
    # Ricerca nei documenti locali
    risposta_documenti = estrai_testo_dai_documenti(domanda)

    if risposta_documenti != "Nessun documento contiene informazioni rilevanti rispetto alla tua domanda.":
        return risposta_documenti

    # Se non troviamo una risposta nei documenti, chiediamo a OpenAI
    try:
        completamento = openai.Completion.create(
            engine="text-davinci-003",
            prompt=domanda,
            max_tokens=1000
        )
        return completamento.choices[0].text.strip()
    except Exception as e:
        return f"Errore nell'API di OpenAI: {e}"
