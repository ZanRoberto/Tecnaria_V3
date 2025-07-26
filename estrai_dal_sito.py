# estrai_dal_sito.py
import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz
from urllib.parse import urljoin

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

def estrai_testo_da_url(url):
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            return soup.get_text(separator='\n', strip=True)
        else:
            return ""
    except:
        return ""

def estrai_contenuto_dal_sito(domanda: str, soglia_similitudine: int = 60) -> str:
    migliori_risultati = []

    for url in PAGINE_TECNARIA:
        testo = estrai_testo_da_url(url)
        if testo:
            score = fuzz.partial_ratio(domanda.lower(), testo.lower())
            if score >= soglia_similitudine:
                migliori_risultati.append((score, url, testo))

    if migliori_risultati:
        migliori_risultati.sort(reverse=True)
        testo_rilevante = migliori_risultati[0][2][:3000]
        url_rilevante = migliori_risultati[0][1]
        return f"🌐 Contenuto rilevante da:\n{url_rilevante}\n\n{testo_rilevante}"
    else:
        return ""
