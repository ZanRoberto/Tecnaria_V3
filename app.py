import os
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from openai import OpenAI

# ============================================================
#  CONFIG BASE (identica alla tua)
# ============================================================

client = OpenAI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "static", "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")

MASTER_PATH = os.path.join(DATA_DIR, "ctf_system_COMPLETE_GOLD_v3.json")
OVERLAY_DIR = os.path.join(DATA_DIR, "overlays")

FALLBACK_FAMILY = "COMM"
FALLBACK_ID = "COMM-FALLBACK-NOANSWER-0001"
FALLBACK_MESSAGE = (
    "Per questa domanda non trovo una risposta GOLD nei dati caricati. "
    "Meglio un confronto diretto con l’ufficio tecnico Tecnaria."
)

# ============================================================
#  FASTAPI
# ============================================================

app = FastAPI(title="TECNARIA GOLD – Overlay First", version="7.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_ui():
    path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(path):
        return FileResponse(path)
    return {"ok": True, "message": "UI non trovata"}

# ============================================================
#  MODELLI
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
#  NORMALIZZAZIONE
# ============================================================

def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )


def normalize(text: str) -> str:
    if not isinstance(text, str):
        return ""
    t = strip_accents(text)
    t = t.lower()
    t = re.sub(r"\s+", " ", t).strip()
    return t


def tokenize(text: str) -> List[str]:
    t = normalize(text)
    return t.split(" ") if t else []


# ============================================================
#  LOAD KB (SEPARATI)
# ============================================================

def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_master():
    return load_json(MASTER_PATH).get("blocks", [])

def load_overlay():
    blocks = []
    p = Path(OVERLAY_DIR)
    if p.exists():
        for f in p.glob("*.json"):
            try:
                d = load_json(str(f))
                blocks.extend(d.get("blocks", []))
            except Exception as e:
                print(f"[OVERLAY ERROR] {f}: {e}")
    return blocks


# ============================================================
#  STATE
# ============================================================

class KBState:
    master_blocks = []
    overlay_blocks = []
    master_index = []
    overlay_index = []

S = KBState()

# ============================================================
#  INDEX BUILD
# ============================================================

def build_index():
    S.master_blocks = load_master()
    S.overlay_blocks = load_overlay()

    S.master_index = []
    S.overlay_index = []

    # overlay first index
    for idx, block in enumerate(S.overlay_blocks):
        for trig in block.get("triggers", []) or []:
            t_norm = normalize(trig)
            if t_norm:
                S.overlay_index.append((idx, t_norm, set(tokenize(trig))))

    # master index
    for idx, block in enumerate(S.master_blocks):
        for trig in block.get("triggers", []) or []:
            t_norm = normalize(trig)
            if t_norm:
                S.master_index.append((idx, t_norm, set(tokenize(trig))))

    print(f"[INDEX] overlay={len(S.overlay_blocks)} master={len(S.master_blocks)}")

build_index()

# ============================================================
#  MATCHING (overlay first)
# ============================================================

def lexical_match_in(index_list, blocks, question):
    q_norm = normalize(question)
    q_tokens = set(tokenize(question))

    matches = []
    best_score = 0.0

    for idx, trig_norm, trig_tokens in index_list:
        score = 0.0

        # substring
        if trig_norm in q_norm:
            score += len(trig_norm) / max(10, len(q_norm))

        # token overlap
        if q_tokens and trig_tokens:
            inter = q_tokens.intersection(trig_tokens)
            if inter:
                score += len(inter) / len(trig_tokens)

        if score > 0:
            matches.append((score, blocks[idx]))
            if score > best_score:
                best_score = score

    matches.sort(key=lambda x: x[0], reverse=True)
    return [b for s, b in matches], best_score


def ai_rerank(question, candidates):
    if not candidates or len(candidates) == 1:
        return candidates[0] if candidates else None

    try:
        desc = "\n".join(
            f"- ID: {b.get('id')} | Triggers: {', '.join(b.get('triggers', []))}"
            for b in candidates
        )

        prompt = (
            f"Domanda: {question}\n"
            f"Blocchi:\n{desc}\n\n"
            f"Scegli solo l'ID migliore."
        )

        result = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0.0,
        )

        chosen = result.choices[0].message.content.strip()
        for b in candidates:
            if b.get("id") == chosen:
                return b

    except Exception as e:
        print("[RERANK ERROR]", e)

    return candidates[0]


def find_best_block(question):

    # 🔥 1️⃣ MATCH OVERLAY FIRST
    overlay_candidates, o_score = lexical_match_in(
        S.overlay_index, S.overlay_blocks, question
    )

    if overlay_candidates:
        return ai_rerank(question, overlay_candidates), o_score

    # 🔥 2️⃣ IF NO OVERLAY → MASTER
    master_candidates, m_score = lexical_match_in(
        S.master_index, S.master_blocks, question
    )

    if master_candidates:
        return ai_rerank(question, master_candidates), m_score

    return None, 0.0

# ============================================================
#  ENDPOINTS
# ============================================================

@app.get("/health")
def health():
    return {
        "ok": True,
        "overlay_blocks": len(S.overlay_blocks),
        "master_blocks": len(S.master_blocks),
        "overlay_index": len(S.overlay_index),
        "master_index": len(S.master_index),
    }


@app.post("/api/ask", response_model=AskResponse)
def api_ask(req: AskRequest):

    if req.mode.lower() != "gold":
        raise HTTPException(400, "Modalità non supportata.")

    q = req.question.strip()
    if not q:
        raise HTTPException(400, "Domanda vuota.")

    block, score = find_best_block(q)

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
        or block.get("answer")
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


@app.post("/api/reload")
def reload_kb():
    build_index()
    return {
        "ok": True,
        "overlay_blocks": len(S.overlay_blocks),
        "master_blocks": len(S.master_blocks),
    }
