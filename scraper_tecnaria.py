import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re

BASE_URL = "https://www.tecnaria.com"
visited_urls = set()
MAX_PAGINE = 35

def estrai_testo_da_pagina(url):
    try:
        response = requests.get(url, timeout=5)
        if response.status_code != 200:
            return ""

        soup = BeautifulSoup(response.text, 'html.parser')
        # Rimuove gli script e gli stili
        for script in soup(["script", "style", "noscript"]):
            script.decompose()

        testi = [element.get_text(separator=" ", strip=True) for element in soup.find_all(["p", "h1", "h2", "h3", "li", "td"])]
        return " ".join(testi)
    except Exception:
        return ""

def filtra_url_validi(soup, base_url):
    link_set = set()
    for tag in soup.find_all('a', href=True):
        href = tag['href']
        href = urljoin(base_url, href)
        parsed = urlparse(href)
        if parsed.netloc == urlparse(BASE_URL).netloc and '#' not in href and 'mailto:' not in href:
            link_set.add(href.split('?')[0])
    return link_set

def esplora_sito(base_url, max_pagine=MAX_PAGINE):
    da_visitare = [base_url]
    contenuti = []
    while da_visitare and len(visited_urls) < max_pagine:
        url = da_visitare.pop(0)
        if url in visited_urls:
            continue
        visited_urls.add(url)

        try:
            response = requests.get(url, timeout=5)
            if response.status_code != 200:
                continue
            soup = BeautifulSoup(response.text, 'html.parser')
            testo = estrai_testo_da_pagina(url)
            if testo:
                contenuti.append(f"[{url}]\n{testo}\n")

            nuovi_link = filtra_url_validi(soup, base_url)
            for link in nuovi_link:
                if link not in visited_urls:
                    da_visitare.append(link)

        except Exception:
            continue
    return "\n".join(contenuti)

def estrai_info_tecnaria(domanda):
    try:
        testo_completo = esplora_sito(BASE_URL)
        if not testo_completo:
            return "⚠️ Nessun contenuto trovato dal sito Tecnaria."

        # Filtriamo solo le parti rilevanti alla domanda
        domanda_lower = domanda.lower()
        paragrafi = testo_completo.split('\n')
        rilevanti = [p for p in paragrafi if domanda_lower in p.lower() or any(k in p.lower() for k in domanda_lower.split())]
        if not rilevanti:
            return "⚠️ Nessun contenuto direttamente rilevante trovato."

        return "\n".join(rilevanti[:15])  # Max 15 paragrafi
    except Exception as e:
        return f"❌ Errore scraping: {str(e)}"
