import os
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from openai import OpenAI

# ============================================================
# CONFIG
# ============================================================

client = OpenAI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "static", "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")

MASTER_PATH = os.path.join(DATA_DIR, "ctf_system_COMPLETE_GOLD_master.json")

FALLBACK_FAMILY = "COMM"
FALLBACK_ID = "COMM-FALLBACK-NOANSWER-0001"
FALLBACK_MESSAGE = (
    "Per questa domanda non trovo una risposta GOLD appropriata. "
    "Meglio confrontarsi con l’ufficio tecnico Tecnaria."
)

# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(title="TECNARIA GOLD – MATCHING DEFINITIVO", version="9.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(path):
        return FileResponse(path)
    return {"ok": True, "message": "UI mancante"}


# ============================================================
# NORMALIZZAZIONE
# ============================================================

def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )

def normalize(t: str) -> str:
    if not isinstance(t, str):
        return ""
    t = strip_accents(t)
    t = t.lower()
    t = re.sub(r"[^a-z0-9àèéìòùç\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def tokenize(text: str) -> List[str]:
    return normalize(text).split(" ")


# ============================================================
# MODELLO
# ============================================================

class AskRequest(BaseModel):
    question: str
    lang: str = "it"
    mode: str = "gold"

class AskResponse(BaseModel):
    ok: bool
    answer: str
    family: str
    id: str
    mode: str
    lang: str
    score: float


# ============================================================
# LOAD KB
# ============================================================

def load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_master():
    return load_json(MASTER_PATH).get("blocks", [])


# ============================================================
# MATCHING DEFINITIVO
# ============================================================

def score_trigger(trigger: str, q_tokens: set, q_norm: str) -> float:
    trig_norm = normalize(trigger)
    trig_tokens = set(trig_norm.split())

    score = 0.0

    # 1) MATCH TOTALE
    if trig_norm == q_norm:
        score += 5.0

    # 2) TUTTI I TOKEN DEL TRIGGER PRESENTI
    if trig_tokens.issubset(q_tokens):
        score += 3.0

    # 3) MATCH PARZIALE (>= metà token)
    inter = trig_tokens.intersection(q_tokens)
    if len(inter) >= max(1, len(trig_tokens) // 2):
        score += len(inter) / len(trig_tokens)

    # 4) MATCH SEMANTICO (substring significativa)
    if trig_norm in q_norm and len(trig_norm) >= 8:
        score += 1.0

    return score


def find_best_block(question: str, blocks: List[Dict]) -> Tuple[Dict, float]:
    q_norm = normalize(question)
    q_tokens = set(tokenize(question))

    scored = []

    for block in blocks:
        local_score = 0.0
        for trig in block.get("triggers", []):
            local_score += score_trigger(trig, q_tokens, q_norm)

        if local_score > 0:
            scored.append((local_score, block))

    if not scored:
        return None, 0.0

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1], scored[0][0]


# ============================================================
# ENDPOINTS
# ============================================================

@app.post("/api/ask", response_model=AskResponse)
def api_ask(req: AskRequest):

    if req.mode.lower() != "gold":
        raise HTTPException(400, "Modalità non supportata.")

    blocks = load_master()

    block, score = find_best_block(req.question, blocks)

    if block is None:
        return AskResponse(
            ok=False,
            answer=FALLBACK_MESSAGE,
            family=FALLBACK_FAMILY,
            id=FALLBACK_ID,
            mode="gold",
            lang=req.lang,
            score=0.0
        )

    answer = (
        block.get(f"answer_{req.lang}")
        or block.get("answer_it")
        or FALLBACK_MESSAGE
    )

    return AskResponse(
        ok=True,
        answer=answer,
        family=block.get("family", "CTF_SYSTEM"),
        id=block.get("id", "UNKNOWN"),
        mode=block.get("mode", "gold"),
        lang=req.lang,
        score=float(score)
    )


@app.get("/health")
def health():
    blocks = load_master()
    return {
        "ok": True,
        "master_blocks": len(blocks),
    }
