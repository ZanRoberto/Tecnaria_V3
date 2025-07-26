import requests
from bs4 import BeautifulSoup

def estrai_contenuto_dal_sito(url):
    """
    Estrae il contenuto testuale leggibile da una pagina web.
    Rimuove script, style e parti irrilevanti.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()  # solleva errore se HTTP != 200

        soup = BeautifulSoup(response.text, "html.parser")

        # Rimuove script e style
        for script in soup(["script", "style", "noscript", "iframe"]):
            script.extract()

        # Estrai solo testo utile
        testo = soup.get_text(separator=' ', strip=True)

        # Pulisci da spazi doppi
        testo = ' '.join(testo.split())

        return testo

    except Exception as e:
        return f"[Errore durante l'estrazione dal sito: {str(e)}]"
