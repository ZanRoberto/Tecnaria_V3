import os
import json
import re
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from openai import OpenAI

# ============================================================
# CONFIG BASE
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(STATIC_DIR, "data")
MASTER_PATH = os.path.join(DATA_DIR, "ctf_system_COMPLETE_GOLD_master.json")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o").strip() or "gpt-4o"

client = None
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)

# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(title="Tecnaria Bot v14.7")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # front e back sono sullo stesso dominio, ma così siamo tranquilli
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# mount static
if not os.path.isdir(STATIC_DIR):
    os.makedirs(STATIC_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ============================================================
# MODELLI
# ============================================================

class QuestionRequest(BaseModel):
    question: str


class AnswerResponse(BaseModel):
    answer: str
    source: str
    meta: Dict[str, Any]


# ============================================================
# CARICAMENTO KB
# ============================================================

KB_BLOCKS: List[Dict[str, Any]] = []


def load_kb() -> None:
    global KB_BLOCKS
    if not os.path.exists(MASTER_PATH):
        print(f"[WARN] MASTER_PATH non trovato: {MASTER_PATH}")
        KB_BLOCKS = []
        return

    try:
        with open(MASTER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "blocks" in data:
            KB_BLOCKS = data["blocks"]
        elif isinstance(data, list):
            KB_BLOCKS = data
        else:
            KB_BLOCKS = []
        print(f"[INFO] KB caricata: {len(KB_BLOCKS)} blocchi")
    except Exception as e:
        print(f"[ERROR] caricando KB: {e}")
        KB_BLOCKS = []


load_kb()


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\sàèéìòóùç]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def score_block(question_norm: str, block: Dict[str, Any]) -> float:
    """
    Matching ultra-semplice per sicurezza:
    - contiamo le parole chiave in comune tra domanda e triggers / question_it.
    """
    triggers = " ".join(block.get("triggers", []))
    q_it = block.get("question_it", "")
    text = normalize(triggers + " " + q_it)
    if not text:
        return 0.0

    q_words = set(question_norm.split())
    b_words = set(text.split())
    common = q_words & b_words
    if not common:
        return 0.0

    # piccolo punteggio normalizzato
    return len(common) / max(len(q_words), 1)


def match_from_kb(question: str, threshold: float = 0.18) -> Optional[Dict[str, Any]]:
    if not KB_BLOCKS:
        return None
    qn = normalize(question)
    best_block = None
    best_score = 0.0
    for b in KB_BLOCKS:
        s = score_block(qn, b)
        if s > best_score:
            best_score = s
            best_block = b
    if best_score < threshold:
        return None
    return best_block


# ============================================================
# LLM: CHATGPT FIRST
# ============================================================

SYSTEM_PROMPT = """
Sei l'assistente tecnico GOLD di Tecnaria S.p.A. (Bassano del Grappa).
Rispondi SOLO su prodotti, sistemi e applicazioni Tecnaria (CTF, P560, CTL, CTL MAXI, VCEM, CTCEM, DIAPASON, ecc.).
Se la domanda non riguarda in modo chiaro Tecnaria, spiega che l'ambito è fuori campo e invita a contattare l'ufficio tecnico.

Stile:
- linguaggio tecnico-ingegneristico chiaro, niente marketing
- risposte strutturate in punti quando utile
- cita SEMPRE esplicitamente che si tratta di sistemi Tecnaria S.p.A.
"""


def call_chatgpt(question: str) -> str:
    if client is None:
        return (
            "Al momento il motore ChatGPT esterno non è disponibile "
            "(OPENAI_API_KEY mancante). Contatta l'ufficio tecnico Tecnaria."
        )

    try:
        completion = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            temperature=0.2,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        print(f"[ERROR] chiamando ChatGPT: {e}")
        return (
            "Si è verificato un errore nella chiamata al motore esterno. "
            "Per sicurezza, contatta direttamente l’ufficio tecnico Tecnaria."
        )


# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/")
async def root() -> FileResponse:
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=500, detail="index.html non trovato")
    return FileResponse(index_path)


@app.get("/api/status")
async def status():
    return {
        "status": "Tecnaria Bot v14.7 attivo",
        "kb_blocks": len(KB_BLOCKS),
        "openai_enabled": bool(OPENAI_API_KEY),
        "model": OPENAI_MODEL,
    }


@app.post("/api/ask", response_model=AnswerResponse)
async def api_ask(req: QuestionRequest):
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Domanda vuota")

    try:
        # 1) ChatGPT FIRST
        gpt_answer = call_chatgpt(question)

        # 2) Tentativo match KB per verifica / confronto
        kb_block = match_from_kb(question)
        kb_answer = None
        kb_id = None
        if kb_block:
            kb_answer = kb_block.get("answer_it") or kb_block.get("answer", "")
            kb_id = kb_block.get("id")

        # 3) Strategia semplice: per ora VINCE SEMPRE GPT
        final_answer = gpt_answer

        meta: Dict[str, Any] = {
            "used_chatgpt": True,
            "used_kb": kb_block is not None,
            "kb_id": kb_id,
        }

        # In futuro possiamo far decidere a ChatGPT quale delle due è migliore,
        # ma per il collaudo cliente basta così.

        return AnswerResponse(
            answer=final_answer,
            source="chatgpt_first",
            meta=meta,
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] /api/ask: {e}")
        # NON lasciamo mai la UI senza testo
        return AnswerResponse(
            answer=f"[ERRORE] Si è verificato un problema interno: {e}",
            source="error",
            meta={"exception": str(e)},
        )
