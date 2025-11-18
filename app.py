import os
import json
import re
import unicodedata
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from openai import OpenAI
client = OpenAI()

# ============================================================
# CONFIG PATH
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "static", "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")

KB_PATH = os.path.join(DATA_DIR, "ctf_system_COMPLETE_GOLD_master.json")

# Folder for external/new candidates
NEW_GOLD_DIR = os.path.join(DATA_DIR, "new_gold_candidates")
os.makedirs(NEW_GOLD_DIR, exist_ok=True)

NEW_GOLD_LOG = os.path.join(DATA_DIR, "new_gold_candidates.jsonl")

FALLBACK_MESSAGE = (
    "Per questa domanda non trovo una risposta GOLD nei dati. "
    "Contatta l'ufficio tecnico Tecnaria per una verifica specifica."
)

# ============================================================
# FastAPI setup
# ============================================================

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ============================================================
# Load KB
# ============================================================

with open(KB_PATH, "r", encoding="utf-8") as f:
    KB = json.load(f)

MASTER_BLOCKS = KB.get("blocks", [])

# ============================================================
# MODELS
# ============================================================

class AskRequest(BaseModel):
    question: str
    lang: Optional[str] = "it"


# ============================================================
# UTILITIES
# ============================================================

def normalize(text: str) -> str:
    text = text.lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]+", " ", text)


def trigger_score(question: str, triggers: List[str]) -> float:
    """
    Simple lexical score:
    +0.3 for each trigger found
    """
    q = normalize(question)
    score = 0.0
    for t in triggers:
        if normalize(t) in q:
            score += 0.3
    return min(score, 1.0)


def match_json_block(question: str) -> Optional[Dict[str, Any]]:
    """
    Partial soft match: compute trigger score for each block.
    """
    best_block = None
    best_score = 0.0

    for b in MASTER_BLOCKS:
        triggers = b.get("triggers", [])
        s = trigger_score(question, triggers)
        if s > best_score:
            best_score = s
            best_block = b

    if best_score < 0.15:  # threshold: below this it's almost chance
        return None

    return best_block


# ============================================================
# EXTERNAL SEARCH (ChatGPT)
# ============================================================

def ask_external_gpt(question: str) -> str:
    """
    Query GPT with hard constraints: respond ONLY about Tecnaria SPA topics.
    """
    try:
        completion = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Rispondi solo con informazioni tecniche reali, coerenti con "
                        "Tecnaria S.p.A. (Bassano del Grappa), i suoi prodotti (CTF, VCEM, "
                        "CTCEM, CTL, CTL MAXI, P560, Diapason) e la loro documentazione. "
                        "Se la domanda non riguarda strettamente questi argomenti, "
                        "rispondi: 'Fuori dominio Tecnaria'."
                    ),
                },
                {"role": "user", "content": question},
            ],
            max_tokens=600,
        )
        answer = completion.choices[0].message["content"].strip()
        return answer

    except Exception:
        return "Errore GPT esterno"


# ============================================================
# JUDGE AI — confronta JSON vs esterna
# ============================================================

def judge_response(question: str, json_ans: str, ext_ans: str) -> str:
    """
    Force GPT to act only as a judge and output *ESTERNA* or *JSON*.
    """
    try:
        completion = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Sei un arbitro tecnico. Devi scegliere quale risposta è "
                        "maggiormente corretta (tecnicamente e rispetto a Tecnaria). "
                        "Rispondi SOLO con una parola: 'JSON' o 'ESTERNA'."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"DOMANDA: {question}\n\n"
                        f"RISPOSTA_JSON:\n{json_ans}\n\n"
                        f"RISPOSTA_ESTERNA:\n{ext_ans}\n\n"
                        "Qual è corretta?"
                    ),
                },
            ],
            max_tokens=20,
        )
        verdict = completion.choices[0].message["content"].strip().upper()
        if "ESTERNA" in verdict:
            return "ESTERNA"
        return "JSON"
    except Exception:
        return "JSON"


# ============================================================
# SAVE NEW GOLD CANDIDATE
# ============================================================

def save_new_candidate(question: str, external_answer: str):
    """
    Save in .jsonl + individual file.
    """
    record = {
        "question": question,
        "answer_external": external_answer,
    }

    # JSONL
    with open(NEW_GOLD_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # Individual file
    safe_name = re.sub(r"[^a-zA-Z0-9]+", "_", question)[:80]
    fp = os.path.join(NEW_GOLD_DIR, f"{safe_name}.json")

    with open(fp, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)


# ============================================================
# MAIN ENDPOINT
# ============================================================

@app.post("/api/ask")
async def ask(req: AskRequest):
    question = req.question.strip()

    # 1) Query esterna GPT
    external_answer = ask_external_gpt(question)

    # 2) Query JSON KB
    block = match_json_block(question)
    json_answer = block.get("answer_it") if block else None

    # Se entrambe falliscono
    if not external_answer and not json_answer:
        return {"answer": FALLBACK_MESSAGE}

    # 3) Judge (ALWAYS — per tua richiesta "1")
    verdict = judge_response(question, json_answer or "", external_answer or "")

    if verdict == "ESTERNA":
        # Save candidate for integration
        save_new_candidate(question, external_answer)
        final = external_answer
    else:
        final = json_answer or external_answer

    return {
        "answer": final,
        "source": verdict.lower(),
    }


@app.get("/")
def root():
    return {"status": "Tecnaria Bot v14.0 attivo", "master_blocks": len(MASTER_BLOCKS)}
