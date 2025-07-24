from scraper_tecnaria import estrai_info_tecnaria
import os

def carica_documenti():
    cartella = "documenti"
    testi = []
    if not os.path.exists(cartella):
        return testi
    for nome_file in os.listdir(cartella):
        if nome_file.endswith(".txt"):
            with open(os.path.join(cartella, nome_file), "r", encoding="utf-8") as f:
                testi.append(f.read())
    return testi

def ottieni_risposta_unificata(domanda):
    risposta_docs = ""
    for contenuto in carica_documenti():
        if domanda.lower() in contenuto.lower():
            risposta_docs = contenuto
            break

    risposta_sito = estrai_info_tecnaria(domanda)

    if risposta_docs and risposta_sito:
        return f"{risposta_docs}\n\n---\n\n{risposta_sito}"
    elif risposta_docs:
        return risposta_docs
    elif risposta_sito:
        return risposta_sito
    else:
        return "Mi dispiace, non ho trovato informazioni rilevanti."
