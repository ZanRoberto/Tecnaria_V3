from bs4 import BeautifulSoup

def estrai_testo_da_url(link):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(link, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            paragrafi = soup.find_all("p")
            testo = "\n".join(p.get_text().strip() for p in paragrafi if len(p.get_text()) > 30)
            print(f"✅ Testo estratto da: {link}")
            return testo
        else:
            print(f"⚠️ Errore HTTP: {response.status_code} per {link}")
            return ""
    except Exception as e:
        print(f"⚠️ Errore durante l'accesso a {link}: {e}")
        return ""
