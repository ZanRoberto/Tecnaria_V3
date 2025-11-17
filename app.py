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

app = FastAPI(title="TECNARIA GOLD – MATCHING v10", version="10.0.0")

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
    blocks: List[Dict[str, Any]] = []
    p = Path(OVERLAY_DIR)
    if not p.exists():
        return blocks
    for f in p.glob("*.json"):
        try:
            d = load_json(str(f))
            blocks.extend(d.get("blocks", []))
        except Exception as e:
            print("[OVERLAY ERROR]", f, e)
    return blocks


# ============================================================
# STATO KB (IN MEMORIA)
# ============================================================

class KBState:
    master_blocks: List[Dict[str, Any]] = []
    overlay_blocks: List[Dict[str, Any]] = []


S = KBState()


def reload_all():
    S.master_blocks = load_master_blocks()
    S.overlay_blocks = load_overlay_blocks()
    print(f"[KB] master={len(S.master_blocks)} overlay={len(S.overlay_blocks)}")


reload_all()


# ============================================================
# MATCHING – SOLO LOGICA (NESSUN CAMBIO AI BLOCCHI)
# ============================================================

def score_trigger(trigger: str, q_tokens: set, q_norm: str) -> float:
    trig_norm = normalize(trigger)
    if not trig_norm:
        return 0.0
    trig_tokens = set(trig_norm.split())

    score = 0.0

    # 1) MATCH TOTALE
    if trig_norm == q_norm:
        score += 5.0

    # 2) TUTTI I TOKEN DEL TRIGGER PRESENTI
    if trig_tokens and trig_tokens.issubset(q_tokens):
        score += 3.0

    # 3) MATCH PARZIALE (>= metà token)
    inter = trig_tokens.intersection(q_tokens)
    if trig_tokens:
        if len(inter) >= max(1, len(trig_tokens) // 2):
            score += len(inter) / len(trig_tokens)

    # 4) SUBSTRING SIGNIFICATIVA
    if trig_norm in q_norm and len(trig_norm) >= 10:
        score += 0.5

    return score


def lexical_candidates(question: str, blocks: List[Dict[str, Any]], max_candidates: int = 15):
    q_norm = normalize(question)
    q_tokens = set(tokenize(question))

    scored: List[Tuple[float, Dict[str, Any]]] = []

    for block in blocks:
        local_score = 0.0
        triggers = block.get("triggers", []) or []
        for trig in triggers:
            local_score += score_trigger(trig, q_tokens, q_norm)

        # Penalizza gli OVERVIEW se ci sono alternative
        bid = block.get("id", "")
        if "OVERVIEW" in bid.upper():
            local_score *= 0.5

        if local_score > 0:
            scored.append((local_score, block))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:max_candidates]


def ai_rerank(question: str, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Usa OpenAI per scegliere il blocco migliore tra i candidati.
    Non cambia i blocchi, sceglie solo l'ID.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    try:
        desc_lines = []
        for b in candidates:
            bid = b.get("id", "UNKNOWN")
            fam = b.get("family", "")
            q_it = b.get("question_it") or ""
            tags = ", ".join(b.get("tags", []) or [])
            desc_lines.append(f"- ID: {bid} | family: {fam} | domanda: {q_it} | tags: {tags}")

        desc = "\n".join(desc_lines)

        prompt = (
            "Sei un motore di instradamento interno. "
            "Ti do una domanda dell'utente e una lista di blocchi di knowledge base. "
            "Ogni blocco ha ID, famiglia e testo della domanda a cui risponde.\n\n"
            f"DOMANDA UTENTE:\n{question}\n\n"
            f"BLOCCHI CANDIDATI:\n{desc}\n\n"
            "Devi rispondere SOLO con l'ID del blocco che risponde meglio alla domanda.\n"
            "Regole:\n"
            "- Preferisci blocchi che spiegano procedure o condizioni quando la domanda inizia con 'come', 'in quali casi', 'quando'.\n"
            "- Evita blocchi di 'overview generale' se esistono blocchi più specifici.\n"
            "- Se la domanda chiede limiti o divieti ('in quali casi non posso usare', 'quando non è ammesso'), "
            "scegli blocchi che parlano di limiti, fuori campo, divieti.\n"
            "- Rispondi SOLO con l'ID esatto, senza altro testo."
        )

        res = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20,
            temperature=0.0,
        )

        chosen = (res.choices[0].message.content or "").strip()
        for b in candidates:
            if b.get("id") == chosen:
                return b

    except Exception as e:
        print("[AI_RERANK_ERROR]", e)

    # Fallback: il migliore lessicale
    return candidates[0]


def find_best_block(question: str) -> Tuple[Dict[str, Any], float]:
    """
    1) overlay (se presenti)
    2) master
    3) ai-rerank tra i candidati
    """
    # Overlay prioritari (se li userai in futuro)
    overlay_scored = lexical_candidates(question, S.overlay_blocks)
    if overlay_scored:
        overlay_blocks = [b for s, b in overlay_scored]
        best_overlay = ai_rerank(question, overlay_blocks)
        best_score = max(s for s, b in overlay_scored if b is best_overlay)
        return best_overlay, float(best_score)

    # Master
    master_scored = lexical_candidates(question, S.master_blocks)
    if not master_scored:
        return None, 0.0

    master_blocks = [b for s, b in master_scored]
    best_master = ai_rerank(question, master_blocks)
    best_score = 0.0
    for s, b in master_scored:
        if b is best_master:
            best_score = s
            break

    return best_master, float(best_score)


# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/health")
def health():
    return {
        "ok": True,
        "master_blocks": len(S.master_blocks),
        "overlay_blocks": len(S.overlay_blocks),
    }


@app.post("/api/reload")
def api_reload():
    reload_all()
    return {
        "ok": True,
        "master_blocks": len(S.master_blocks),
        "overlay_blocks": len(S.overlay_blocks),
    }


@app.post("/api/ask", response_model=AskResponse)
def api_ask(req: AskRequest):

    if req.mode.lower() != "gold":
        raise HTTPException(400, "Modalità non supportata (solo 'gold').")

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
        score=float(score),
    )
