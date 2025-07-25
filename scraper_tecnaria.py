# scraper_tecnaria.py

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from langdetect import detect
from deep_translator import GoogleTranslator

BASE_URL = "https://www.tecnaria.com"
MAX_PAGINE = 50  # limite di sicurezza

def estrai_testo_pagina(url):
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.content, "html.parser")
        paragrafi = soup.find_all(["p", "h1", "h2", "h3", "li"])
        testo = "\n".join(p.get_text(strip=True) for p in paragrafi if p.get_text(strip=True))
        return testo
    except Exception as e:
        return ""

def estrai_info_tecnaria(domanda):
    visitati = set()
    da_visitare = [BASE_URL]
    testi_rilevanti = []

    while da_visitare and len(visitati) < MAX_PAGINE:
        url = da_visitare.pop(0)
        if url in visitati:
            continue
        visitati.add(url)

        try:
            response = requests.get(url, timeout=10)
            soup = BeautifulSoup(response.content, "html.parser")
            testo_pagina = estrai_testo_pagina(url)

            if domanda.lower() in testo_pagina.lower():
                testi_rilevanti.append(f"\n🔗 {url}\n{testo_pagina[:1000]}...")

            # Scansiona nuovi link da seguire
            for link in soup.find_all("a", href=True):
                href = link['href']
                full_url = urljoin(url, href)
                if BASE_URL in full_url and full_url not in visitati:
                    da_visitare.append(full_url)

        except Exception:
            continue

    # Se nulla trovato, prova a tradurre la domanda e ripetere
    if not testi_rilevanti and detect(domanda) != "it":
        domanda_it = GoogleTranslator(source='auto', target='it').translate(domanda)
        return estrai_info_tecnaria(domanda_it)

    return "\n\n".join(testi_rilevanti[:3]) if testi_rilevanti else ""
