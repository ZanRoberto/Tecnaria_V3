import os
from fuzzywuzzy import fuzz
from datetime import datetime

LOG_FILE = "log_interazioni.txt"

def log_interazione(domanda, risultati):
    with open(LOG_FILE, 'a', encoding='utf-8') as log:
        log.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Domanda: {domanda}\n")
        for nome_file, score, contenuto in risultati:
            log.write(f"  - {nome_file} (score: {score})\n")

def estrai_testo_dai_documenti(domanda: str, soglia_similitudine: int = 65) -> str:
    cartella = 'documenti'
    if not os.path.exists(cartella):
        return "❌ Nessun documento trovato nella cartella."

    risultati = []

    for nome_file in os.listdir(cartella):
        if nome_file.endswith(".txt"):
            percorso = os.path.join(cartella, nome_file)
            try:
                with open(percorso, 'r', encoding='utf-8') as f:
                    testo = f.read()
                    score = fuzz.partial_ratio(domanda.lower(), testo.lower())
                    if score >= soglia_similitudine:
                        risultati.append((nome_file, score, testo.strip()))
            except Exception as e:
                risultati.append((nome_file, 0, f"⚠️ Errore leggendo {nome_file}: {str(e)}"))

    if risultati:
        risultati.sort(key=lambda x: x[1], reverse=True)
        log_interazione(domanda, risultati)
        return "\n\n".join([f"📄 {nome} (score: {score}):\n{contenuto[:1000]}..." for nome, score, contenuto in risultati])
    else:
        log_interazione(domanda, [])
        return "Nessun documento contiene informazioni rilevanti rispetto alla tua domanda."
