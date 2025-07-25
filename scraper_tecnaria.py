import os
import openai
from flask import Flask, render_template, request
from documenti_utils import estrai_testo_dai_documenti

app = Flask(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")

def ottieni_risposta_unificata(domanda: str) -> str:
    """
    Combina il contenuto rilevante dai documenti e invia tutto a OpenAI per generare la risposta.
    """
    contesto_documenti = estrai_testo_dai_documenti(domanda)

    prompt = f"""
    Domanda dell'utente: {domanda}

    Documenti rilevanti:
    {contesto_documenti}

    Rispondi nel modo più preciso possibile utilizzando le informazioni trovate nei documenti. Se ci sono dati chiari (es. indirizzi, orari, contatti), fornisci risposta diretta e completa.
    """

    try:
        completamento = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Sei un assistente esperto di Tecnaria."},
                {"role": "user", "content": prompt}
            ]
        )
        risposta = completamento.choices[0].message.content.strip()
        return risposta
    except Exception as e:
        return f"❌ Errore nel generare la risposta: {str(e)}"

@app.route('/', methods=['GET', 'POST'])
def index():
    risposta = ""
    if request.method == 'POST':
        domanda = request.form['domanda']
        risposta = ottieni_risposta_unificata(domanda)
    return render_template('index.html', risposta=risposta)

if __name__ == '__main__':
    app.run(debug=True)
