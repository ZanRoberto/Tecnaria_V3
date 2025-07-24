from flask import Flask, request, render_template
from bridge_scraper import ottieni_risposta_unificata

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    risposta = None
    if request.method == "POST":
        domanda = request.form["domanda"]
        risposta = ottieni_risposta_unificata(domanda)
    return render_template("index.html", risposta=risposta)

if __name__ == "__main__":
    app.run(debug=True)
