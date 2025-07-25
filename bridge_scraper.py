import os
import requests
import openai
from bs4 import BeautifulSoup
from documenti_utils import estrai_testo_dai_documenti

# Imposta la chiave API OpenAI
openai.api_key = os.getenv("OPENAI_API_KEY")

def ottieni_risposta_unificata(domanda: str) -> str:
    # 1. Prova a rispondere dai documenti locali
    risposta_documenti = estrai_testo_dai_documenti(domanda)
    if "Nessun documento contiene" not in risposta_documenti:
        return f"📚 Risposta dai documenti:\n\n{risposta_documenti}"

    # 2. Se non trova nulla, prova a fare scraping dal sito Tecnaria
    try:
        url = "https://www.tecnaria.com"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            soup = BeautifulSoup(response.content, 'html.parser')
            testo = soup.get_text(separator=' ', strip=True)
            if domanda.lower() in testo.lower():
                return f"🌐 Risposta dal sito Tecnaria:\n\n{testo[:1000]}..."
    except Exception as e:
        pass  # Fallisce silenziosamente e passa a OpenAI

    # 3. Ultima risorsa: chiedi a OpenAI
    try:
        completamento = openai.Completion.create(
            engine="text-davinci-003",  # oppure gpt-3.5-turbo se usi la nuova API Chat
            prompt=domanda,
            max_tokens=1000,
            temperature=0.4
        )
        return f"🤖 Risposta AI:\n\n{completamento.choices[0].text.strip()}"
    except Exception as e:
        return f"❌ Errore finale: né documenti, né sito, né AI hanno risposto.\nDettagli: {e}"
