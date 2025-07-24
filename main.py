from flask import Flask, request, render_template_string
from bridge_scraper import ottieni_risposta_unificata
import os

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <title>Chatbot Tecnaria</title>
    <style>
        body { font-family: Arial, sans-serif; background: #f4f4f4; margin: 0; padding: 0; }
        .container { max-width: 700px; margin: 50px auto; background: #ffffff; padding: 30px;
                     border-radius: 8px; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2); }
        h1 { text-align: center; color: #333333; }
        form { margin-top: 20px; }
        input[type="text"] { width: 100%; padding: 10px; margin-bottom: 15px;
                             border: 1px solid #cccccc; border-radius: 4px; }
        input[type="submit"] { padding: 10px 20px; background-color: #005b9f; color: white;
                               border: none; border-radius: 4px; cursor: pointer; }
        input[type="submit"]:hover { background-color: #003f6f; }
        .risposta { margin-top: 20px; background: #e9f5ff; padding: 15px;
                    border-left: 4px solid #0077cc; border-radius: 4px; word-wrap: break-word; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Chatbot Tecnaria</h1>
        <form method="POST">
            <input type="text" name="domanda" placeholder="Scrivi la tua domanda..." required>
            <input type="submit" value="Invia">
        </form>

        {% if risposta %}
        <div class="risposta">{{ risposta | safe }}</div>
        {% endif %}
    </div>
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def index():
    risposta = ""
    if request.method == "POST":
        domanda = request.form["domanda"]
        risposta = ottieni_risposta_unificata(domanda)
    return render_template_string(HTML_TEMPLATE, risposta=risposta)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
