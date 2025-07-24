from scraper_tecnaria import estrai_info_tecnaria
from deep_translator import GoogleTranslator
from langdetect import detect
import os

def traduci_se_necessario(testo, target='it'):
    try:
        lingua = detect(testo)
        if lingua != target:
            return GoogleTranslator(source='auto', target=target).translate(testo)
        return testo
    except Exception:
        return testo

def carica_documenti_locali():
    cartella = "documenti"
    testo_completo = ""
    if os.path.exists(cartella):
        for nome_file in os.listdir(cartella):
            if nome_file.endswith(".txt"):
                percorso = os.path.join(cartella, nome_file)
                with open(percorso, "r", encoding="utf-8") as f:
                    testo_completo += "\n" + f.read()
    return testo_completo

def ottieni_risposta_unificata(domanda, openai_client=None):
    try:
        domanda = traduci_se_necessario(domanda)
        
        contenuto_sito = estrai_info_tecnaria(domanda)
        contenuto_locale = carica_documenti_locali()

        prompt_finale = (
            "Rispondi alla domanda usando tutte le informazioni possibili derivate da sito e documenti.\n\n"
            f"DOMANDA:\n{domanda}\n\n"
            f"CONTENUTO SITO:\n{contenuto_sito}\n\n"
            f"CONTENUTO DOCUMENTI:\n{contenuto_locale}\n\n"
            "Risposta:"
        )

        if openai_client:
            risposta = openai_client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "user", "content": prompt_finale}
                ],
                temperature=0.3
            )
            return risposta.choices[0].message.content.strip()
        else:
            return "🧠 (Simulazione) Risposta da AI non disponibile senza OpenAI Client."

    except Exception as e:
        return f"❌ Errore durante la generazione della risposta: {str(e)}"
