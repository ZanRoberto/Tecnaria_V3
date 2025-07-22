import requests
from bs4 import BeautifulSoup
import re
import os

def estrai_testo_vocami():
    links_file = "documenti.txt"
    testo_completo = ""

    if not os.path.exists(links_file):
        return ""

    with open(links_file, "r") as file:
        links = [line.strip() for line in file if line.strip()]

    for url in links:
        try:
            response = requests.get(url)
            soup = BeautifulSoup(response.text, "html.parser")
            blocchi_testo = soup.find_all(['p', 'h1', 'h2', 'h3', 'li'])
            contenuto = "\n".join([blocco.get_text(strip=True) for blocco in blocchi_testo])
            contenuto = re.sub(r'\s+', ' ', contenuto)
            testo_completo += contenuto + "\n"
        except Exception as e:
            print(f"Errore durante l'accesso a {url}: {str(e)}")

    return testo_completo.strip()
