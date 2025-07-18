import requests
from bs4 import BeautifulSoup
import urllib.parse

def scrape_tecnaria_results(query):
    try:
        search_url = f"https://www.google.com/search?q=site:tecnaria.com+{urllib.parse.quote(query)}"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(search_url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        link_tag = next((a for a in soup.find_all("a", href=True) if "tecnaria.com" in a["href"]), None)
        if not link_tag:
            return ""
        page = requests.get(link_tag["href"], headers=headers, timeout=10)
        soup_page = BeautifulSoup(page.text, "html.parser")
        paragraphs = soup_page.find_all("p")
        return "\n".join(p.get_text() for p in paragraphs)
    except Exception as e:
        return ""
