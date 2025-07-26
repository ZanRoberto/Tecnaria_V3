import os
from flask import Flask, render_template, request, jsonify
import openai
import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz
from urllib.parse import urljoin
from dotenv import load_dotenv

# Carica chiavi API
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

app = Flask(__name__)

# --- CONFIG ---
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

# --- FUNZIONI ---

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
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "Rispondi come se fossi un esperto tecnico di Tecnaria. Usa solo fonti ufficiali o risposte coerenti con il sito Tecnaria."},
                    {"role": "user", "content": domanda}
                ],
                temperature=0.2,
                max_tokens=1000
            )
            return response['choices'][0]['message']['content'].strip()
        except Exception as e:
            return f"❌ Errore OpenAI: {e}"

    if risposta_doc and risposta_web:
        return f"📚 Dai documenti:\n{risposta_doc}\n\n🌐 Dal sito:\n{risposta_web}"
    elif risposta_doc:
        return f"📚 Dai documenti:\n{risposta_doc}"
    else:
        return f"🌐 Dal sito:\n{risposta_web}"

# --- ROUTE PRINCIPALE ---
@app.route("/", methods=["GET", "POST"])
def home():
    risposta = ""
    if request.method == "POST":
        domanda = request.form.get("domanda", "")
        if domanda.strip():
            risposta = ottieni_risposta_unificata(domanda)
    return render_template("index.html", risposta=risposta)

# --- AVVIO SERVER ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
