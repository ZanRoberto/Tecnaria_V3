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
OVERLAY_DIR = os.path.join(DATA_DIR, "overlays")

FALLBACK_FAMILY = "COMM"
FALLBACK_ID = "COMM-FALLBACK-NOANSWER-0001"
FALLBACK_MESSAGE = (
    "Per questa domanda non trovo una risposta GOLD appropriata nei dati caricati. "
    "Meglio un confronto diretto con l’ufficio tecnico Tecnaria."
)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(title="TECNARIA GOLD – MATCHING v11", version="11.0.0")

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
    return {"ok": True, "message": "UI non trovata"}


# ============================================================
# MODELLI
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
# LOAD KB
# ============================================================

def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_master_blocks() -> List[Dict[str, Any]]:
    data = load_json(MASTER_PATH)
    return data.get("blocks", [])


def load_overlay_blocks() -> List[Dict[str, Any]]:
    blocks = []
    p = Path(OVERLAY_DIR)
    if not p.exists():
        return blocks
    for f in p.glob("*.json"):
        try:
            d = load_json(str(f))
            blocks.extend(d.get("blocks", []))
        except:
            pass
    return blocks


# ============================================================
# STATE
# ============================================================

class KBState:
    master_blocks: List[Dict[str, Any]] = []
    overlay_blocks: List[Dict[str, Any]] = []

S = KBState()


def reload_all():
    S.master_blocks = load_master_blocks()
    S.overlay_blocks = load_overlay_blocks()
    print(f"[KB LOADED] master={len(S.master_blocks)} overlay={len(S.overlay_blocks)}")


reload_all()


# ============================================================
# MATCHING ENGINE (LESSIC + AI RERANK) – CORRETTO
# ============================================================

def score_trigger(trigger: str, q_tokens: set, q_norm: str) -> float:
    trig_norm = normalize(trigger)
    if not trig_norm:
        return 0.0

    trig_tokens = set(trig_norm.split())
    score = 0.0

    # 1) token match totale
    if trig_tokens and trig_tokens.issubset(q_tokens):
        score += 3.0

    # 2) match parziale > metà token
    inter = trig_tokens.intersection(q_tokens)
    if trig_tokens and len(inter) >= max(1, len(trig_tokens) // 2):
        score += len(inter) / len(trig_tokens)

    # 3) substring significativa
    if len(trig_norm) >= 10 and trig_norm in q_norm:
        score += 0.5

    return score


def lexical_candidates(question: str, blocks: List[Dict[str, Any]], limit: int = 15):
    q_norm = normalize(question)
    q_tokens = set(tokenize(question))
    scored = []

    for block in blocks:
        block_score = 0.0
        for trigger in block.get("triggers", []):
            block_score += score_trigger(trigger, q_tokens, q_norm)

        # penalizza overview generali
        if "OVERVIEW" in block.get("id", "").upper():
            block_score *= 0.5

        if block_score > 0:
            scored.append((block_score, block))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:limit]


def ai_rerank(question: str, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    candidate_ids = [b.get("id") for b in candidates]

    try:
        desc = "\n".join(
            f"- ID:{b.get('id')} | Q:{b.get('question_it')}" for b in candidates
        )

        prompt = (
            "Sei un motore di routing per una knowledge base. "
            "Ti do una domanda utente e una lista di blocchi possibili.\n\n"
            f"DOMANDA:\n{question}\n\n"
            f"CANDIDATI:\n{desc}\n\n"
            "Devi restituire SOLO l'ID del blocco che risponde meglio.\n"
            "NON DEVI GENERARE TESTO.\n"
            "NON DEVI CREARE RISPOSTE.\n"
            "Se nessun ID è adatto, scegli quello più vicino tra i candidati.\n"
            "Rispondi SOLO con un ID presente nella lista."
        )

        res = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20,
            temperature=0.0,
        )

        chosen = (res.choices[0].message.content or "").strip()

        # ❗ BLOCCO CHE EVITA RISPOSTE INVENTATE
        if chosen not in candidate_ids:
            return candidates[0]     # fallback lessicale

        for b in candidates:
            if b.get("id") == chosen:
                return b

    except Exception as e:
        print("[AI RERANK ERROR]", e)

    return candidates[0]


def find_best_block(question: str) -> Tuple[Dict[str, Any], float]:

    # overlay → se presenti
    over = lexical_candidates(question, S.overlay_blocks)
    if over:
        cands = [b for s, b in over]
        best = ai_rerank(question, cands)
        score = max(s for s, b in over if b is best)
        return best, float(score)

    # master
    master = lexical_candidates(question, S.master_blocks)
    if not master:
        return None, 0.0

    cands = [b for s, b in master]
    best = ai_rerank(question, cands)
    score = max(s for s, b in master if b is best)

    return best, float(score)


# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/health")
def health():
    return {
        "ok": True,
        "master_blocks": len(S.master_blocks),
        "overlay_blocks": len(S.overlay_blocks)
    }


@app.post("/api/reload")
def api_reload():
    reload_all()
    return {
        "ok": True,
        "master_blocks": len(S.master_blocks),
        "overlay_blocks": len(S.overlay_blocks)
    }


@app.post("/api/ask", response_model=AskResponse)
def api_ask(req: AskRequest):

    if req.mode.lower() != "gold":
        raise HTTPException(400, "Modalità non supportata (solo gold).")

    question = (req.question or "").strip()
    if not question:
        raise HTTPException(400, "Domanda vuota.")

    block, score = find_best_block(question)

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
        id=block.get("id", "UNKNOWN-ID"),
        mode=block.get("mode", "gold"),
        lang=req.lang,
        score=float(score)
    )
