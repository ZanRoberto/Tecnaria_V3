
import requests
from bs4 import BeautifulSoup
import re

def estrai_testo_vocami():
    url = "https://docs.google.com/document/d/e/2PACX-1vSqy0-FZAqOGvnCFZwwuBfT1cwXFpmSpkWfrRiT8RlbQpdQy-_1hOaqIslih5ULSa0XhVt0V8QeWJDP/pub"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Trova i paragrafi ed elimina contenuti vuoti
        paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]

        testo_pulito = "\n".join(paragraphs)
        print(f"[SCRAPER] Documento caricato: {len(paragraphs)} paragrafi, {len(testo_pulito)} caratteri")
        return testo_pulito
    except Exception as e:
        print(f"[SCRAPER ERROR] {e}")
        return ""
