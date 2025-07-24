import requests
from bs4 import BeautifulSoup

def estrai_info_tecnaria(query):
    try:
        base_url = "https://www.tecnaria.com"
        response = requests.get(base_url, timeout=10)
        response.encoding = "utf-8"
        if response.status_code != 200:
            return ""
        soup = BeautifulSoup(response.text, "html.parser")
        testo = soup.get_text(separator=" ", strip=True)
        if query.lower() in testo.lower():
            inizio = testo.lower().find(query.lower())
            estratto = testo[inizio:inizio+1000]
            return estratto.strip()
        else:
            return ""
    except Exception as e:
        return ""
