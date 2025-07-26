from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

def get_tecnaria_connectors():
    """
    Recupera automaticamente i dati e le immagini dei connettori per legno da tecnaria.com.
    """
    url = "https://tecnaria.com/solai-in-legno/prodotti-restauro-solai-legno/"
    response = requests.get(url, timeout=10)
    soup = BeautifulSoup(response.text, "html.parser")

    results = []

    products = soup.find_all("div", class_="product-item")
    for product in products:
        name_tag = product.find("h3")
        img_tag = product.find("img")
        link_tag = product.find("a", href=True)

        name = name_tag.get_text(strip=True) if name_tag else "Prodotto senza nome"
        img_url = img_tag["src"] if img_tag and "src" in img_tag.attrs else ""
        product_link = link_tag["href"] if link_tag else ""

        results.append({
            "name": name,
            "image": img_url,
            "link": product_link
        })

    return results


@app.route("/ask", methods=["POST"])
def ask():
    user_question = request.json.get("question", "").lower()

    if "connettori" in user_question or "tecnaria" in user_question:
        products = get_tecnaria_connectors()
        if not products:
            return jsonify({"answer": "Non ho trovato connettori Tecnaria al momento."})

        html_response = "<h2>Connettori Tecnaria per Legno</h2>"
        for p in products:
            html_response += f"""
            <div style='margin-bottom: 20px;'>
                <h3>{p['name']}</h3>
                <img src='{p['image']}' alt='{p['name']}' width='300'><br>
                <a href='{p['link']}' target='_blank'>Vai alla scheda prodotto</a>
            </div>
            """

        return jsonify({"answer": html_response})

    return jsonify({"answer": "Non ho informazioni su questa richiesta."})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
