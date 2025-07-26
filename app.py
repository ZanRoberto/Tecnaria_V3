from flask import Flask, render_template, request
from ottieni_risposta_unificata import ottieni_risposta_unificata

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def home():
    risposta = ""
    if request.method == "POST":
        domanda = request.form["domanda"]
        risposta = ottieni_risposta_unificata(domanda)
    return render_template("index.html", risposta=risposta)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
