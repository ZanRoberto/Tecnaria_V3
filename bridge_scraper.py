import os
from deep_translator import GoogleTranslator
from scraper_tecnaria import estrai_info_tecnaria
from langdetect import detect
import openai

openai.api_key = os.getenv("OPENAI_API_KEY")

def carica_documenti(cartella="documenti"):
    testo = ""
    if os.path.exists(cartella):
        for nome_file in os.listdir(cartella):
            if nome_file.endswith(".txt"):
                with open(os.path.join(cartella, nome_file), "r", encoding="utf-8") as file:
                    testo += file.read() + "\n"
    return testo

def traduci_testo(testo, target_lang="en"):
    try:
        return GoogleTranslator(source='auto', target=target_lang).translate(testo)
    except Exception:
        return testo

def ottieni_risposta_unificata(domanda, lingua):
    contesto_doc = carica_documenti()
    contesto_sito = estrai_info_tecnaria(domanda)
    contesto_unificato = f"{contesto_doc.strip()}\n{contesto_sito.strip()}"

    if lingua != "it":
        domanda = traduci_testo(domanda, target_lang="en")
        contesto_unificato = traduci_testo(contesto_unificato, target_lang="en")

    risposta = chiama_openai(domanda, contesto_unificato)

    if lingua != "it":
        risposta = traduci_testo(risposta, target_lang=lingua)

    return risposta

def chiama_openai(domanda, contesto):
    prompt = (
        f"Domanda: {domanda}\n\n"
        f"Contesto Tecnaria (documenti + sito):\n{contesto}\n\n"
        f"Risposta tecnica il più possibile precisa e coerente:"
    )
    try:
        completion = openai.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}]
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        return f"⚠️ Errore: {e}"
