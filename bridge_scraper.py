# bridge_scraper.py

import os
import openai
import requests
from bs4 import BeautifulSoup
from documenti_utils import estrai_testo_dai_documenti

openai.api_key = os.getenv("OPENAI_API_KEY")

def esegui_scraping_tecnaria(domanda: str) -> str:
    url = "https://www.tecnaria.com"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        testo_estratto = soup.get_text(separator=' ', strip=True)
        if domanda.lower() in testo_estratto.lower():
            return f"🌐 Sito Tecnaria:\n{testo_estratto[:3000]}..."
        else:
            return "🔍 Nessuna informazione rilevante trovata direttamente sul sito Tecnaria."
    except Exception as e:
        return f"⚠️ Errore durante lo scraping del sito Tecnaria: {str(e)}"

def ottieni_risposta_unificata(domanda: str) -> str:
    testo_sito = esegui_scraping_tecnaria(domanda)
    testo_documenti = estrai_testo_dai_documenti(domanda)

    prompt = f"""
Rispondi come se fossi un tecnico esperto di Tecnaria. Fornisci una risposta chiara, utile e basata su fonti reali.
Domanda: {domanda}

📄 Dai documenti:
{testo_documenti}

🌐 Dal sito:
{testo_sito}

Scrivi solo la risposta finale. Non elencare le fonti.
"""

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.3
        )
        return response.choices[0].message['content'].strip()
    except Exception as e:
        return f"❌ Errore nel generare la risposta: {str(e)}"
