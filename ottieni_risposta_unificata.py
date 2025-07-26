# ottieni_risposta_unificata.py

import os
import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

CARTELLA_DOCUMENTI = "documenti"
PAGINE_TECNARIA = [
    "https://www.tecnaria.com/it/index.html",
    "https://www.tecnaria.com/it/prodotti.html",
    "https://www.tecnaria.com/it/connettori-solai-legno.html",
    "https://www.tecnaria.com/it/connettori-solai-acciaio.html",
    "https://www.tecnaria.com/it/connettori-solai-calcestruzzo.html",
    "https://www.tecnaria.com/it/applicazioni.html",
    "https://www.tecnaria.com/it/chiodatrici.html",
    "https://www.tecnaria.com/it/download.html",
    "https://www.tecnaria.com/it/contatti.html"
]

def estrai_testo_dai_documenti(domanda: str, soglia_similitudine: int = 65) -> str:
    if not os.path.exists(CARTELLA_DOCUMENTI):
        return ""

    risultati = []
    for nome_file in os.listdir(CARTELLA_DOCUMENTI):
        if nome_file.endswith(".txt"):
            percorso = os.path.join(CARTELLA_DOCUMENTI, nome_file)
            try:
                with open(percorso, 'r', encoding='utf-8') as f:
                    testo = f.read()
                    score = fuzz.partial_ratio(domanda.lower(), testo.lower())
                    if score >= soglia_similitudine:
                        risultati.append((score, nome_file, testo.strip()))
            except:
                continue

    if risultati:
        risultati.sort(reverse=True)
        return risultati[0][2][:3000]
    else:
        return ""

def estrai_testo_da_url(url):
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            return soup.get_text(separator='\n', strip=True)
    except:
        return ""
    return ""

def estrai_contenuto_dal_sito(domanda: str, soglia_similitudine: int = 60) -> str:
    risultati = []
    for url in PAGINE_TECNARIA:
        testo = estrai_testo_da_url(url)
        if testo:
            score = fuzz.partial_ratio(domanda.lower(), testo.lower())
            if score >= soglia_similitudine:
                risultati.append((score, url, testo))

    if risultati:
        risultati.sort(reverse=True)
        top_score, top_url, top_testo = risultati[0]
        return f"🌐 Contenuto rilevante da:\n{top_url}\n\n{top_testo[:3000]}"
    return ""

def ottieni_risposta_unificata(domanda):
    risposta_doc = estrai_testo_dai_documenti(domanda)
    risposta_web = estrai_contenuto_dal_sito(domanda)

    if not risposta_doc and not risposta_web:
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "Rispondi come se fossi un esperto tecnico di Tecnaria."},
                    {"role": "user", "content": domanda}
                ],
                temperature=0.2,
                max_tokens=1000
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"❌ Errore OpenAI: {e}"

    if risposta_doc and risposta_web:
        return f"📚 Dai documenti:\n{risposta_doc}\n\n🌐 Dal sito:\n{risposta_web}"
    elif risposta_doc:
        return risposta_doc
    else:
        return risposta_web
