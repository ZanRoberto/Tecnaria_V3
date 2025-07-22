import requests
from bs4 import BeautifulSoup
import urllib.parse
import re

def scrape_tecnaria_results(query):
    try:
        search_url = f"https://www.google.com/search?q=site:tecnaria.com+{urllib.parse.quote(query)}"
        headers = {"User-Agent": "Mozilla/5.0"}

        response = requests.get(search_url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")

        # Trova il primo link valido a tecnaria.com
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "url?q=" in href and "tecnaria.com" in href:
                real_url = href.split("url?q=")[1].split("&")[0]
                break
        else:
            return ""

        # Scarica la pagina target
        page = requests.get(real_url, headers=headers, timeout=10)
        soup_page = BeautifulSoup(page.text, "html.parser")
        paragraphs = soup_page.find_all("p")

        contenuto = "\n".join(p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 20)
        return contenuto

    except Exception as e:
        return f"Errore durante lo scraping: {e}"

if __name__ == "__main__":
    query = "sedi Francia orari contatti prodotti"
    risultato = scrape_tecnaria_results(query)

    # Salva il testo direttamente nel documento usato dal bot
    if risultato:
        with open("documenti.txt", "w", encoding="utf-8") as f:
            f.write(risultato)
        print("✅ Documento aggiornato con successo.")
    else:
        print("⚠️ Nessun contenuto trovato.")
