# scraper_tecnaria.py
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE_URL = "https://www.tecnaria.com"

def estrai_info_tecnaria(domanda):
    try:
        links_esplorati = set()
        testo_completo = ""

        def esplora_link(url, profondita=0, max_prof=2):
            if profondita > max_prof or url in links_esplorati:
                return
            links_esplorati.add(url)

            try:
                response = requests.get(url, timeout=10)
                if response.status_code != 200:
                    return
                soup = BeautifulSoup(response.text, 'html.parser')

                # Aggiungi tutto il testo utile di ogni pagina
                testo_pagina = soup.get_text(separator=' ', strip=True)
                testo_completo_nonlocal[0] += f"\n\n[{url}]\n{testo_pagina}"

                # Trova tutti i link interni e prosegui
                for link_tag in soup.find_all('a', href=True):
                    href = link_tag['href']
                    link_assoluto = urljoin(BASE_URL, href)
                    if BASE_URL in link_assoluto:
                        esplora_link(link_assoluto, profondita + 1, max_prof)

            except Exception:
                pass

        testo_completo_nonlocal = [""]
        esplora_link(BASE_URL)

        # Selezione semplice: restituisce primi 4000 caratteri rilevanti
        return testo_completo_nonlocal[0][:4000]

    except Exception as e:
        return "❌ Errore nello scraping del sito Tecnaria."
