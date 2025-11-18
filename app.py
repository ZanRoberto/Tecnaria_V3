import json
import os
import re
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from openai import OpenAI

# =============================================================
# CONFIGURAZIONE
# =============================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

MASTER_JSON_PATH = "static/data/ctf_system_COMPLETE_GOLD_master.json"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============================================================
# CARICA KNOWLEDGE BASE
# =============================================================

if os.path.exists(MASTER_JSON_PATH):
    with open(MASTER_JSON_PATH, "r", encoding="utf-8") as f:
        KB = json.load(f)
else:
    KB = {"blocks": []}

# =============================================================
# PROMPT TECNARIA (sistema)
# =============================================================

SYSTEM_PROMPT = """
Sei l’assistente tecnico ufficiale di TECNARIA SPA (Bassano del Grappa).

DEVI rispondere SOLO su:
- Connettori strutturali Tecnaria (CTF, CTCEM, CTL, CTL MAXI, VCEM, DIAPASON, GTS, accessori)
- Pistola P560 Tecnaria
- Chiodi idonei Tecnaria HSBR14/16/18
- Sistemi collaboranti acciaio–calcestruzzo, legno–calcestruzzo
- Lamiera grecata, deformazioni, ondine
- Appoggio, colpi di prova, card Tecnaria, propulsori P560
- ETA Tecnaria
- Codici commerciali Tecnaria
- Processi di posa, validità dei fissaggi, errori di posa

NON devi:
- inventare contenuti non presenti nelle logiche Tecnaria
- parlare di aziende o prodotti non Tecnaria
- uscire dal perimetro dei sistemi collaboranti Tecnaria

Stile:
- Modalità GOLD Tecnaria
- Linguaggio tecnico-ingegneristico chiaro
- Coerenza assoluta con le prove e la documentazione Tecnaria
"""

# =============================================================
# STRUMENTI DI MATCHING
# =============================================================

def normalize(t):
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()

def match_block(question, block):
    score = 0
    q = normalize(question)

    for trig in block.get("triggers", []):
        if normalize(trig) in q:
            score += 3

    for kw in ["p560", "ctf", "ondina", "lamiera", "card", "propulsore"]:
        if kw in q and kw in normalize(block.get("question_it", "")):
            score += 1

    return score


def best_json_match(question):
    best = None
    best_score = 0

    for b in KB["blocks"]:
        s = match_block(question, b)
        if s > best_score:
            best_score = s
            best = b

    return best, best_score


# =============================================================
# GPT-FIRST LOGIC
# =============================================================

async def ask_gpt(question):
    try:
        r = client.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question}
            ],
            temperature=0.1,
            max_tokens=600
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        return None


# =============================================================
# SCELTA RISPOSTA MIGLIORE (GPT vs JSON)
# =============================================================

def judge_best(question, gpt_answer, json_answer):
    if gpt_answer and not json_answer:
        return gpt_answer

    if json_answer and not gpt_answer:
        return json_answer

    if not gpt_answer and not json_answer:
        return None

    gpt_score = sum(k in gpt_answer.lower() for k in ["ctf","p560","card","lamiera","tecnaria"])
    json_score = sum(k in json_answer.lower() for k in ["ctf","p560","card","lamiera","tecnaria"])

    if gpt_score >= json_score:
        return gpt_answer
    else:
        return json_answer


# =============================================================
# API MODEL
# =============================================================

class AskRequest(BaseModel):
    question: str

# =============================================================
# INTERFACCIA WEB INLINE
# =============================================================

INDEX_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Tecnaria Bot v14.6</title>
    <style>
        body { font-family: Arial; background: #f5f5f5; padding: 30px; }
        #box { background: white; padding: 20px; border-radius: 10px; max-width: 800px; margin: auto; }
        textarea { width: 100%; height: 120px; font-size: 16px; }
        button { padding: 10px 20px; background: #0066cc; color: white; border: none; border-radius: 5px; cursor: pointer; }
        #response { margin-top: 20px; background: #eef; padding: 15px; border-radius: 10px; }
    </style>
</head>
<body>
<div id="box">
    <h2>Assistente Tecnaria • Modalità GOLD</h2>
    <textarea id="question" placeholder="Scrivi la tua domanda..."></textarea>
    <br><br>
    <button onclick="ask()">Invia</button>
    <div id="response"></div>
</div>

<script>
async function ask() {
    const q = document.getElementById("question").value;
    const res = await fetch("/api/ask", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({question: q})
    });
    const data = await res.json();
    document.getElementById("response").innerHTML = data.answer || "Nessuna risposta.";
}
</script>

</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    return INDEX_HTML
# =============================================================
# API /api/ask
# =============================================================

@app.post("/api/ask")
async def ask_api(payload: AskRequest):

    q = payload.question.strip()

    # 1) PRIMA GPT (risposta esterna sempre)
    gpt_answer = await ask_gpt(q)

    # 2) POI JSON (fallback)
    best_block, score = best_json_match(q)
    json_answer = best_block.get("answer_it") if best_block else None

    # 3) JUDGE → sceglie la migliore
    final = judge_best(q, gpt_answer, json_answer)

    # 4) Se GPT ha dato qualcosa di valido E json non ha un blocco forte
    #    → aggiunge automaticamente come nuovo blocco GOLD
    if gpt_answer and (score < 3):
        new_block = {
            "id": f"AUTO-{len(KB['blocks'])+1}",
            "family": "AUTO_TECH",
            "mode": "gold",
            "lang": "it",
            "intent": "auto_generated",
            "tags": ["auto", "gpt", "tecnaria"],
            "triggers": [q],
            "question_it": q,
            "answer_it": gpt_answer
        }
        KB["blocks"].append(new_block)

        # Salva sul disco
        try:
            with open(MASTER_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(KB, f, indent=2, ensure_ascii=False)
        except:
            pass  # fail-safe, NON blocca demo cliente

    return {"answer": final}


# =============================================================
# ENDPOINT DI CONFIG / DEBUG
# =============================================================

@app.get("/api/config")
async def config():
    return {
        "status": "Tecnaria Bot v14.6 attivo",
        "master_blocks": len(KB["blocks"]),
        "mode": "GPT-first + JSON fallback + auto-merge"
    }
