# ottieni_risposta_unificata.py
from documenti_utils import estrai_testo_dai_documenti
from estrai_dal_sito import estrai_contenuto_dal_sito
import openai
import os

openai.api_key = os.getenv("OPENAI_API_KEY")

def ottieni_risposta_unificata(domanda):
    risposta_doc = estrai_testo_dai_documenti(domanda)
    risposta_web = estrai_contenuto_dal_sito(domanda)

    if not risposta_doc and not risposta_web:
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
            return f"❌ Errore API OpenAI: {e}"

    if risposta_doc and risposta_web:
        return f"📚 Dai documenti:\n{risposta_doc}\n\n🌐 Dal sito:\n{risposta_web}"
    elif risposta_doc:
        return risposta_doc
    else:
        return risposta_web
