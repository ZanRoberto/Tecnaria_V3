import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

PAGINE_TECNARIA = [
    "https://www.tecnaria.com/it/prodotto/chiodatrice-p560-per-connettori-ctf/",
    "https://www.tecnaria.com/it/prodotto/chiodatrice-p560-per-connettori-diapason/",
    "https://www.tecnaria.com/it/prodotti/connettori-solai-calcestruzzo.html",
    "https://www.tecnaria.com/it/prodotti/connettori-solai-acciaio.html"
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

def cerca_sul_sito(domanda: str, soglia: int = 60) -> str:
    risultati = []
    for url in PAGINE_TECNARIA:
        testo = estrai_testo_da_url(url)
        if testo:
            score = fuzz.partial_ratio(domanda.lower(), testo.lower())
            if score >= soglia:
                risultati.append((score, url, testo))

    if risultati:
        risultati.sort(reverse=True)
        score, url, testo = risultati[0]
        snippet = '\n'.join(testo.split('\n')[:20])
        return f"🌐 Tecnaria.com – Pagina trovata: {url}\n\n{snippet}"
    return ""
