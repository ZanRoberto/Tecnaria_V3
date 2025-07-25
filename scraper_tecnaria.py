import os
from openai import OpenAI
from datetime import datetime
from rapidfuzz import fuzz

LOG_FILE = "log_interazioni.txt"

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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
        return "\n\n".join([f"📄 {nome} (score: {score}):\n{contenuto}" for nome, score, contenuto in risultati])
    else:
        log_interazione(domanda, [])
        return "Nessun documento contiene informazioni rilevanti rispetto alla tua domanda."

def ottieni_risposta_unificata(domanda):
    risposta_documenti = estrai_testo_dai_documenti(domanda)

    if risposta_documenti != "Nessun documento contiene informazioni rilevanti rispetto alla tua domanda.":
        return risposta_documenti

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Rispondi come se fossi un esperto tecnico di Tecnaria."},
                {"role": "user", "content": domanda}
            ],
            temperature=0.3,
            max_tokens=1000
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"❌ Errore nell'API di OpenAI: {e}"
