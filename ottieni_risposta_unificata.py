import os
import openai
from langdetect import detect
from deep_translator import GoogleTranslator
from dotenv import load_dotenv

load_dotenv()

openai.api_key = os.getenv("OPENAI_API_KEY")

def ottieni_risposta_unificata(domanda):
    try:
        # 🔍 Unisce il contenuto di tutti i file .txt nella cartella 'documenti'
        documenti_dir = "documenti"
        contesto = ""
        for nome_file in os.listdir(documenti_dir):
            if nome_file.endswith(".txt"):
                percorso = os.path.join(documenti_dir, nome_file)
                try:
                    with open(percorso, "r", encoding="utf-8") as f:
                        contesto += f"\n\n### CONTENUTO DI {nome_file} ###\n"
                        contesto += f.read()
                except Exception as e:
                    contesto += f"\n[errore nella lettura di {nome_file}: {e}]\n"

        # 🔤 Rileva lingua e traduce la domanda in inglese per compatibilità con OpenAI
        lingua_originale = detect(domanda)
        domanda_en = GoogleTranslator(source='auto', target='en').translate(domanda)

        # 🤖 Chiamata all'API OpenAI
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a technical assistant specialized in Tecnaria products. You only answer using the content below. Do not invent."},
                {"role": "user", "content": f"Context:\n{contesto}"},
                {"role": "user", "content": f"Question: {domanda_en}"}
            ],
            temperature=0.4,
            max_tokens=1200
        )

        risposta_en = response.choices[0].message["content"]

        # 🔁 Traduci di nuovo la risposta nella lingua originale (se non inglese)
        if lingua_originale != "en":
            risposta = GoogleTranslator(source='en', target=lingua_originale).translate(risposta_en)
        else:
            risposta = risposta_en

        return risposta

    except Exception as e:
        return f"Si è verificato un errore: {e}"
