# app.py
import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI

# Inizializza FastAPI
app = FastAPI(title="Tecnaria Bot - ChatGPT esteso")

# Inizializza client OpenAI
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Prompt di stile che forza tutte le risposte
CHATGPT_STILE_TECNARIA = """
Tu sei un tecnico esperto di TECNARIA S.p.A. (connettori acciaio-calcestruzzo/legno, lamiera grecata, posa in opera, chiodatrici SPIT).
Stile: ChatGPT esteso, discorsivo ma tecnico, con ragionamento chiaro e distinzioni per casi (A/B/C o bullet).
Requisiti:
- Rispondi come a un cliente tecnico: contesto, quando/come si usa, alternative, pro/contro, dettagli di posa e verifiche.
- Metti in evidenza scelte pratiche (CTL vs CTF vs connettori acciaio–cls) e quando ha senso usare la SPIT P560.
- Evita frasi che sminuiscono un prodotto (niente formule tipo “non è un connettore”): spiega cosa FA e QUANDO si usa.
- Concludi con una sintesi operativa chiara (3–5 punti).
- Niente tabelle salvo sia indispensabile. Fornisci sempre una sola risposta completa (non 3 varianti).
- Se la domanda è vaga, scegli l’interpretazione più utile e dai comunque una risposta operativa.
Lingua: italiano.
"""

# Schema per la richiesta
class AskBody(BaseModel):
    question: str

# Endpoint principale
@app.post("/ask")
def ask(body: AskBody):
    try:
        resp = client.responses.create(
            model="gpt-5-turbo",   # usa GPT-5 (o quello che hai attivo sul tuo account)
            instructions=CHATGPT_STILE_TECNARIA,
            input=[
                {
                    "role": "user",
                    "content": body.question.strip()
                }
            ],
            temperature=0.3,           # più stabile e tecnico
            max_output_tokens=1200     # spazio sufficiente per risposta estesa
        )
        # Responses API ha già l'attributo output_text comodo
        return {"answer": resp.output_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
