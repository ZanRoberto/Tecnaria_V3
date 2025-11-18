import os
import json
import re
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel
from openai import OpenAI

# ====================================================
#   OPENAI CLIENT  (FIX DEFINITIVO v14.5)
# ====================================================

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

if not os.environ.get("OPENAI_API_KEY"):
    print("⚠️  ATTENZIONE: OPENAI_API_KEY NON TROVATA NELL'AMBIENTE RENDER!")
    print("    Il motore GPT non funzionerà finché non la inserisci correttamente.")


# ====================================================
#  PATHS
# ====================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(STATIC_DIR, "data")
MASTER_PATH = os.path.join(DATA_DIR, "ctf_system_COMPLETE_GOLD_master.json")

# ====================================================
#  FASTAPI
# ====================================================

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ====================================================
# Load JSON Knowledge Base
# ====================================================

def load_master_blocks():
    try:
        with open(MASTER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("blocks", [])
    except Exception as e:
        print("ERRORE CARICANDO MASTER JSON:", e)
        return []

MASTER_BLOCKS = load_master_blocks()
print(f"[KB LOADED] master={len(MASTER_BLOCKS)}")


# ====================================================
# Input model
# ====================================================

class AskRequest(BaseModel):
    question: str


# ====================================================
# Matching dei blocchi (logica esistente, migliorata)
# ====================================================

def score_block(question: str, block: dict) -> float:
    q = question.lower()

    score = 0.0

    # 1. Triggers
    for trig in block.get("triggers", []):
        if trig.lower() in q:
            score += 4

    # 2. Keywords
    for kw in block.get("tags", []):
        if kw.lower() in q:
            score += 1.2

    # 3. Intent
    intent = block.get("intent", "").lower()
    if intent and intent in q:
        score += 2

    return score


def get_best_local_answer(question: str):
    scored = []
    for b in MASTER_BLOCKS:
        scored.append((b, score_block(question, b)))

    scored.sort(key=lambda x: x[1], reverse=True)

    if scored[0][1] < 1.5:
        return None

    return scored[0][0]


# ====================================================
#  MOTORE GPT: ricerca + risposta
# ====================================================

async def ask_gpt(question: str):
    try:
        prompt = (
            f"Rispondi SOLO sulla base dei dati TECNARIA (CTF, P560, lamiera grecata). "
            f"Se la domanda non riguarda Tecnaria o va fuori tema, rispondi: 'Fuori campo Tecnaria'.\n\n"
            f"Domanda: {question}\nRisposta:"
        )

        completion = client.responses.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            input=prompt
        )

        answer = completion.output_text
        return answer

    except Exception as e:
        print("🔥 ERRORE GPT:", e)
        traceback.print_exc()
        return None


# ====================================================
#  ROUTES
# ====================================================

@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.post("/api/ask")
async def ask_api(req: AskRequest, request: Request):
    question = req.question.strip()

    try:
        # ===============================
        # 1) LOCAL GOLD MATCH
        # ===============================

        local_block = get_best_local_answer(question)

        # ===============================
        # 2) GPT ANSWER (motore web)
        # ===============================

        gpt_answer = await ask_gpt(question)

        # ===============================
        # 3) JUDGE MODE (scegli la migliore)
        # ===============================

        judged = None

        judge_prompt = f"""
Sei il motore JUDGE. Devi valutare due risposte alla stessa domanda relativa a Tecnaria S.p.A.

DOMANDA:
{question}

RISPOSTA A (local JSON):
{local_block.get("answer_it") if local_block else "NESSUNA"}

RISPOSTA B (GPT):
{gpt_answer if gpt_answer else "NESSUNA"}

ISTRUZIONE:
Scegli la risposta più completa, fedele alla documentazione Tecnaria (CTF, P560, lamiera), tecnica, ingegneristica, e più utile al cliente.  
Rispondi SOLO con la versione finale, senza spiegazioni.
"""

        judge_completion = client.responses.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            input=judge_prompt
        )

        judged = judge_completion.output_text

        if not judged or judged.strip() == "":
            judged = gpt_answer or (local_block["answer_it"] if local_block else "")

        # ===============================
        # 4) OUTPUT JSON
        # ===============================

        return {
            "final_answer": judged,
            "local_used": local_block is not None,
            "gpt_used": gpt_answer is not None,
            "local_score": score_block(question, local_block) if local_block else 0,
            "version": "14.5"
        }

    except Exception as e:
        traceback.print_exc()
        return JSONResponse(
            {"error": "Errore motore GOLD 14.5", "details": str(e)},
            status_code=500
        )
