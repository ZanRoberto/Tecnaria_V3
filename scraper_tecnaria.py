import requests
from bs4 import BeautifulSoup

def estrai_info_tecnaria(domanda):
    try:
        url = "https://www.tecnaria.com"
        response = requests.get(url, timeout=5)
        soup = BeautifulSoup(response.text, "html.parser")
        testo_completo = soup.get_text(separator=' ').lower()

        if any(parola in testo_completo for parola in domanda.lower().split()):
            return "✅ Informazioni trovate direttamente dal sito Tecnaria.com."
        else:
            return ""
    except Exception as e:
        return f"(Errore durante lo scraping: {e})"
