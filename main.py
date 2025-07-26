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
    Scarica prodotti solo da tecnaria.com con immagini e link.
    """
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")

        products = soup.find_all("div", class_="product-item")
        if not products:
            return None

        html_response = f"{HTML_STYLE}<h2 style='color:#004080;'>{title}</h2>"
        counter = 1

        for product in products:
            name_tag = product.find("h3")
            image_tag = product.find("img")
            link_tag = product.find("a", href=True)

            name = name_tag.get_text(strip=True) if name_tag else "Prodotto Tecnaria"
            image = image_tag["src"] if image_tag else ""
            link = link_tag["href"] if link_tag else ""

            # Mostra solo contenuti che provengono da tecnaria.com
            if "tecnaria.com" not in image or "tecnaria.com" not in link:
                continue

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

        return html_response
    except Exception as e:
        print(f"Errore fetch_tecnaria_products: {e}")
        return None


@app.route("/ask", methods=["POST"])
def ask():
    user_question = request.json.get("question", "").lower()

    # 1) Se la domanda riguarda connettori, mostriamo immagini da Tecnaria
    if "connettori" in user_question:
        html_content = fetch_tecnaria_products(
            "https://tecnaria.com/it/connettori-solai-legno.html",
            "Connettori Tecnaria per Solai in Legno"
        )
        if html_content:
            return jsonify({"answer": html_content})

    # 2) Se riguarda chiodatrici, mostriamo immagini da Tecnaria
    if "chiodatrici" in user_question:
        html_content = fetch_tecnaria_products(
            "https://tecnaria.com/it/chiodatrici/",
            "Chiodatrici Tecnaria"
        )
        if html_content:
            return jsonify({"answer": html_content})

    # 3) Fallback - Risposta testuale dai documenti (logica preesistente)
    return jsonify({"answer": "📚 Dai documenti:\n" 
                              "Non sono state trovate immagini, ma posso fornire solo il testo tecnico."})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
