# documenti_utils.py

import os
import datetime
from fuzzywuzzy import fuzz

def log_interazione(domanda, risultati):
    with open("log_documenti.txt", "a", encoding="utf-8") as f:
        f.write(f"\n🕓 {datetime.datetime.now()}\n🔍 Domanda: {domanda}\n")
        for file, punteggio in risultati:
            f.write(f"📄 {file} - Punteggio: {punteggio}\n")
        f.write("-" * 50 + "\n")

def estrai_testo_dai_documenti(domanda: str, soglia_rilevanza: int = 65) -> str:
    """
    Scansiona i file .txt nella cartella 'documenti' e restituisce i contenuti
    più rilevanti usando fuzzy matching. Logga l'interazione.
    """
    cartella = 'documenti'
    if not os.path.exists(cartella):
        return "❌ Nessun documento trovato nella cartella."

    risultati = []
    contenuti_rilevanti = []

    for nome_file in os.listdir(cartella):
        if nome_file.endswith(".txt"):
            percorso = os.path.join(cartella, nome_file)
            try:
                with open(percorso, 'r', encoding='utf-8') as f:
                    testo = f.read()
                    punteggio = fuzz.partial_ratio(domanda.lower(), testo.lower())
                    if punteggio >= soglia_rilevanza:
                        contenuti_rilevanti.append(f"📄 {nome_file}:\n{testo}")
                        risultati.append((nome_file, punteggio))
            except Exception as e:
                contenuti_rilevanti.append(f"⚠️ Errore leggendo {nome_file}: {str(e)}")

    log_interazione(domanda, risultati)

    if contenuti_rilevanti:
        return "\n\n".join(contenuti_rilevanti)
    else:
        return "Nessun documento contiene informazioni rilevanti rispetto alla tua domanda."
