import os
import json
import re
import asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------
# OPENAI CLIENT - VERSIONE ASYNC (necessaria per Render)
# ---------------------------------------------------------
import openai
openai.api_key = os.getenv("OPENAI_API_KEY")

async def ask_external_gpt(question: str) -> str:
    """
    Versione ASINCRONA della chiamata GPT.
    Se fallisce → ritorna None.
    """
    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": 
                 "Rispondi SOLO su argomenti di Tecnaria S.p.A. "
                 "CTF, P560, lamiera grecata, card, limiti di posa, ETA, prove Tecnaria. "
                 "Se la domanda non riguarda Tecnaria, rispondi SOLO: 'Fuori dominio'."},
                {"role": "user", "content": question}
            ],
            max_tokens=500,
            temperature=0.2
        )
        return response.choices[0].message["content"].strip()
    except Exception:
        return None


# ---------------------------------------------------------
# PATH E CARICAMENTO KB JSON
# ---------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(STATIC_DIR, "data")
MASTER_PATH = os.path.join(DATA_DIR, "ctf_system_COMPLETE_GOLD_master.json")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------
# LOAD KNOWLEDGE BASE
# ---------------------------------------------------------
if os.path.exists(MASTER_PATH):
    with open(MASTER_PATH, "r", encoding="utf-8") as f:
        MASTER_KB = json.load(f)
else:
    MASTER_KB = []


# ---------------------------------------------------------
# MODEL PER /api/ask
# ---------------------------------------------------------
class AskRequest(BaseModel):
    question: str


# ---------------------------------------------------------
# MATCHER INTERNO JSON
# ---------------------------------------------------------
def match_json(question: str):
    """
    Ritorna il miglior blocco dal JSON, oppure None.
    Matching semantico semplificato.
    """
    q = question.lower()
    best_score = 0
    best_block = None

    for b in MASTER_KB:
        score = 0

        # match nei trigger
        for t in b.get("triggers", []):
            if t.lower() in q:
                score += 3

        # match nel testo domanda
        if b.get("question_it") and b["question_it"].lower() in q:
            score += 2

        if score > best_score:
            best_score = score
            best_block = b

    return best_block


# ---------------------------------------------------------
# JUDGE: sceglie tra GPT e JSON
# ---------------------------------------------------------
async def judge_answer(question, gpt_answer, json_block):
    """
    Regole:
    1) Se GPT ha risposto 'Fuori dominio' → usa JSON
    2) Se GPT è None → usa JSON
    3) Se JSON esiste ed è molto coerente → preferisci JSON
    4) Default → GPT
    """

    # Caso 1: GPT ha risposto fuori dominio
    if gpt_answer is None or "fuori dominio" in gpt_answer.lower():
        if json_block:
            return json_block["answer_it"]
        return "Non trovo risposta né in GPT né nel database Tecnaria."

    # Caso 2: JSON molto forte
    if json_block:
        # Heuristic per capire se JSON è molto coerente
        if any(t.lower() in question.lower() for t in json_block.get("triggers", [])):
            # JSON vince
            return json_block["answer_it"]

    # Caso 3: GPT vince
    return gpt_answer


# ---------------------------------------------------------
# API: PAGINA PRINCIPALE (INTERFACCIA)
# ---------------------------------------------------------
@app.get("/")
async def serve_ui():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ---------------------------------------------------------
# API PRINCIPALE /api/ask (motore 14.4)
# ---------------------------------------------------------
@app.post("/api/ask")
async def ask_api(req: AskRequest):
    q = req.question.strip()

    # 1) Avvia in parallelo GPT + JSON
    gpt_task = asyncio.create_task(ask_external_gpt(q))
    json_block = match_json(q)

    # 2) Attendi GPT
    gpt_answer = await gpt_task

    # 3) Scegli il migliore
    final_answer = await judge_answer(q, gpt_answer, json_block)

    return {
        "answer": final_answer,
        "mode": "v14.4",
        "used_gpt": gpt_answer,
        "used_json": json_block
    }


# ---------------------------------------------------------
# HEALTH CHECK
# ---------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "OK", "version": "14.4", "master_blocks": len(MASTER_KB)}
