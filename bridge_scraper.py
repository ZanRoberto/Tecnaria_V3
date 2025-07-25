import os
import openai
from documenti_utils import estrai_testo_dai_documenti
from deep_translator import GoogleTranslator
from langdetect import detect

openai.api_key = os.getenv("OPENAI_API_KEY")

def ottieni_risposta_unificata(domanda):
    # Estrai contenuto dei documenti da Google Drive o altre fonti
    testo_documenti = estrai_testo_dai_documenti(domanda)

    # Lingua originale della domanda
    lingua = detect(domanda)
    if lingua != "it":
        domanda = GoogleTranslator(source=lingua, target='it').translate(domanda)

    prompt = f"""
CONTENUTO DOCUMENTI TECNARIA:
{testo_documenti}

DOMANDA UTENTE:
{domanda}

ISTRUZIONI:
- Rispondi sempre come tecnico esperto di Tecnaria.
- Non menzionare mai prodotti concorrenti o generici.
- Basati solo sulle informazioni ufficiali di Tecnaria.
- Se la risposta non è nei documenti, ammetti che non è disponibile.

RISPOSTA:
"""

    risposta = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "Sei un assistente tecnico specializzato nei prodotti e nelle soluzioni Tecnaria."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3,
        max_tokens=800
    )

    contenuto_risposta = risposta.choices[0].message["content"]

    # Se la lingua originale era diversa da italiano, ritraduci
    if lingua != "it":
        contenuto_risposta = GoogleTranslator(source='it', target=lingua).translate(contenuto_risposta)

    return contenuto_risposta
