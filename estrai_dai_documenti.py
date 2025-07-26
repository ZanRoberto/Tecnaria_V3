# estrai_dal_sito.py
import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

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
        return f"🌐 Contenuto rilevante da: {top_url}\n\n{top_testo[:3000]}"
    return ""
