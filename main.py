from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

HTML_STYLE = """
<style>
  .image-container img {
    max-width: 250px;
    cursor: pointer;
    border-radius: 6px;
    transition: transform 0.2s ease-in-out;
  }
  .image-container img:hover {
    transform: scale(1.05);
  }
  .lightbox {
    display: none;
    position: fixed;
    z-index: 9999;
    padding-top: 60px;
    left: 0;
    top: 0;
    width: 100%;
    height: 100%;
    overflow: auto;
    background-color: rgba(0, 0, 0, 0.8);
  }
  .lightbox img {
    display: block;
    margin: auto;
    max-width: 90%;
    max-height: 80%;
  }
  .lightbox:target {
    display: block;
  }
</style>
"""

def fetch_tecnaria_products(url, title):
    """
    Scarica i prodotti con nome, immagine e link da una pagina Tecnaria.
    """
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")

        html_response = f"{HTML_STYLE}<h2 style='color:#004080;'>{title}</h2>"

        products = soup.find_all("div", class_="product-item")
        counter = 1
        for product in products:
            name = product.find("h3").get_text(strip=True) if product.find("h3") else "Prodotto Tecnaria"
            image = product.find("img")["src"] if product.find("img") else ""
            link = product.find("a")["href"] if product.find("a") else "#"

            html_response += f"""
            <div class="image-container" style="background:#f9f9f9; padding:15px; margin-bottom:20px; border-radius:8px;">
              <h3>🔩 {name}</h3>
              <a href="#img{counter}"><img src="{image}" alt="{name}"></a>
              <div id="img{counter}" class="lightbox">
                <img src="{image}" alt="{name} grande">
              </div>
              <p><a href="{link}" target="_blank">Scheda tecnica</a></p>
            </div>
            """
            counter += 1

        return html_response if products else None
    except Exception as e:
        print(f"Errore fetch_tecnaria_products: {e}")
        return None

@app.route("/ask", methods=["POST"])
def ask():
    question = request.json.get("question", "").lower()

    # Connettori per legno
    if "connettori" in question or "tecnaria" in question:
        html_content = fetch_tecnaria_products(
            "https://tecnaria.com/solai-in-legno/prodotti-restauro-solai-legno/",
            "Connettori Tecnaria per Solai in Legno"
        )
        if html_content:
            return jsonify({"answer": html_content})

    # Chiodatrici Tecnaria
    if "chiodatrici" in question:
        html_content = fetch_tecnaria_products(
            "https://tecnaria.com/chiodatrici/",
            "Chiodatrici Tecnaria"
        )
        if html_content:
            return jsonify({"answer": html_content})

    return jsonify({"answer": "Non ho trovato informazioni specifiche su questa richiesta."})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
