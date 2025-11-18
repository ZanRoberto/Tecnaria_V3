import os
import json
import re
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from openai import OpenAI
from pydantic import BaseModel

# ============================================================
#  SETTINGS
# ============================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GPT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

client = OpenAI(api_key=OPENAI_API_KEY)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MASTER_PATH = os.path.join(BASE_DIR, "ctf_system_COMPLETE_GOLD_master.json")

# ============================================================
#  LOAD MASTER JSON
# ============================================================

def load_master_json():
    try:
        with open(MASTER_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"blocks": []}

MASTER_DATA = load_master_json()


# ============================================================
#  FASTAPI APP
# ============================================================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# ============================================================
#  REQUEST MODEL
# ============================================================

class AskRequest(BaseModel):
    question: str


# ============================================================
#  CHATGPT FIRST FUNCTION
# ============================================================

async def ask_chatgpt(question: str):
    """Always query ChatGPT first."""
    prompt = f"""
Sei l’Assistente Tecnico GOLD di Tecnaria SPA.

Rispondi **SOLO** con contenuti verificabili relativi a:
- CTF, P560, CTL, CTL MAXI, VCEM, CTCEM, DIAPASON, GTS,
- cataloghi Tecnaria,
- ETA ufficiali Tecnaria,
- lamiera grecata, chiodi idonei Tecnaria, card di controllo,
- sistemi collaboranti acciaio-calcestruzzo Tecnaria.

⚠️ NON inventare nulla di aziende diverse o tecnologie non Tecnaria.
⚠️ Se la domanda non è pertinente a Tecnaria, rispondi:
    "Domanda non pertinente ai prodotti Tecnaria SPA."

Domanda dell’utente:
{question}

Risposta Tecnica GOLD:
"""

    try:
        completion = client.chat.completions.create(
            model=GPT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )

        answer = completion.choices[0].message["content"].strip()

        if not answer or len(answer) < 10:
            return None

        return answer

    except Exception as e:
        print("❌ ERRORE GPT:", e)
        return None


# ============================================================
#  JSON SEARCH (FALLBACK SOLO SE GPT FALLISCE)
# ============================================================

def search_json(question: str):
    question_l = question.lower()

    best_block = None
    best_score = 0

    for block in MASTER_DATA.get("blocks", []):
        for trig in block.get("triggers", []):
            trig_l = trig.lower()
            if trig_l in question_l or question_l in trig_l:
                score = len(trig_l)
                if score > best_score:
                    best_block = block
                    best_score = score

    return best_block


# ============================================================
#  API: /api/ask
# ============================================================

@app.post("/api/ask")
async def ask_api(req: AskRequest):

    question = req.question.strip()
    if not question:
        return {"answer": "Domanda vuota.", "source": "none"}

    # 1) CHATGPT FIRST
    gpt_answer = await ask_chatgpt(question)

    if gpt_answer:
        return {"answer": gpt_answer, "source": "ChatGPT"}

    # 2) FALLBACK JSON
    block = search_json(question)

    if block:
        return {
            "answer": block.get("answer_it", "Risposta JSON trovata ma incompleta."),
            "source": "JSON"
        }

    return {
        "answer": "Non trovo risposta né in ChatGPT né nel database Tecnaria.",
        "source": "none"
    }


# ============================================================
#  SERVE UI (INTEGRATO)
# ============================================================

HTML_UI = """
<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8" />
<title>Tecnaria GOLD</title>
<style>
    body { background:#0f1115; color:white; font-family:Arial; }
    #box { width:80%; margin:40px auto; padding:20px; background:#11141a; border-radius:10px;}
    button { padding:12px 22px; background:#ff7f00; color:white; border:none; border-radius:10px; font-size:18px; cursor:pointer;}
    input { width:80%; padding:12px; border-radius:8px; font-size:18px; }
    #answer { margin-top:30px; white-space:pre-wrap; font-size:20px; line-height:1.4; }
    .source { margin-top:10px; font-size:14px; color:#aaa; }
</style>
</head>
<body>

<div id="box">
<h1>Assistente Tecnaria • Modalità GOLD</h1>
<div id="answer">Nessuna risposta.</div>
<div class="source" id="source"></div>

<br><br>
<input id="question" placeholder="Scrivi la tua domanda su CTF, P560, lamiera…" />
<button onclick="send()">Invia →</button>
</div>

<script>
async function send(){
    let q = document.getElementById("question").value;
    if(!q) return;

    const res = await fetch("/api/ask", {
        method:"POST",
        headers:{ "Content-Type":"application/json" },
        body: JSON.stringify({question:q})
    });

    const data = await res.json();
    document.getElementById("answer").innerText = data.answer;
    document.getElementById("source").innerText = "Fonte: " + data.source;
}
</script>

</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTML_UI


# ============================================================
# STATUS
# ============================================================

@app.get("/status")
async def status():
    return {"status": "Tecnaria Bot v14.6 ChatGPT-FIRST", "blocks": len(MASTER_DATA.get("blocks", []))}
