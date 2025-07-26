import os
import openai
from langdetect import detect
from deep_translator import GoogleTranslator
from dotenv import load_dotenv

load_dotenv()

openai.api_key = os.getenv("OPENAI_API_KEY")

def ottieni_risposta_unificata(domanda):
    try:
        # 🔍 Legge tutti i file .txt nella cartella /documenti
        documenti_dir = "documenti"
        contesto = ""
        for nome_file in os.listdir(documenti_dir):
            if nome_file.endswith(".txt"):
                percorso = os.path.join(documenti_dir, nome_file)
                try:
                    with open(percorso, "r", encoding="utf-8") as f:
                        contesto += f"\n\n### FILE: {nome_file} ###\n"
                        contesto += f.read()
                except Exception as e:
                    contesto += f"\n[Errore nella lettura di {nome_file}: {e}]\n"

        # 🔤 Traduzione domanda (per compatibilità con OpenAI)
        lingua_originale = detect(domanda)
        domanda_en = GoogleTranslator(source='auto', target='en').translate(domanda)

        # ⚠️ Prompt rigido: NO invenzioni
        prompt = f"""You are a technical assistant for the company Tecnaria. 
Only answer using the content provided in the 'context' below. 
If the answer is not explicitly found in the context, simply reply:
"I'm sorry, I could not find any relevant information in the documents provided."

CONTEXT:
{contesto}

QUESTION:
{domanda_en}
"""

        # 🧠 Chiamata all’API OpenAI
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0,
            max_tokens=1200
        )

        risposta_en = response.choices[0].message["content"]

        # 🔁 Traduzione finale nella lingua dell’utente
        if lingua_originale != "en":
            risposta = GoogleTranslator(source='en', target=lingua_originale).translate(risposta_en)
        else:
            risposta = risposta_en

        return risposta

    except Exception as e:
        return f"Errore durante l'elaborazione: {e}"
