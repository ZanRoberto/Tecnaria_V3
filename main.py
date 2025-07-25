# main.py

import os
import openai
from flask import Flask, render_template, request
from bridge_scraper import ottieni_risposta_unificata

# Imposta la chiave API di OpenAI dalla variabile di ambiente
openai.api_key = os.getenv("OPENAI_API_KEY")

# Inizializza l'app Flask
app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    risposta = None
    if request.method == 'POST':
        domanda = request.form['domanda']
        risposta = ottieni_risposta_unificata(domanda)
    return render_template('index.html', risposta=risposta)

if __name__ == '__main__':
    # Esegue l'app su host 0.0.0.0 per compatibilità con Render
    app.run(debug=True, host='0.0.0.0')
