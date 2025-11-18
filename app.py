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
#  PATH / FILE
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATIC_DIR = os.path.join(BASE_DIR, "static")
if not os.path.exists(STATIC_DIR):
    STATIC_DIR = "static"

DATA_DIR = os.path.join(STATIC_DIR, "data")
MASTER_PATH = os.path.join(DATA_DIR, "ctf_system_COMPLETE_GOLD_master.json")
CANDIDATES_PATH = os.path.join(DATA_DIR, "candidates.jsonl")

# ============================================================
# FASTAPI SETUP
# ============================================================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ============================================================
# KB LOAD
# ============================================================

def load_master_blocks(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Master KB file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    blocks = data.get("blocks", [])
    print(f"[KB LOADED] master={len(blocks)} overlay=0")
    return blocks

MASTER_BLOCKS: List[Dict[str, Any]] = load_master_blocks(MASTER_PATH)

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
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize(text: str) -> List[str]:
    return normalize(text).split()


def keyword_score(question_tokens: List[str], block: Dict[str, Any]) -> float:
    """
    Punteggio lessicale puro: trigger + question_it.
    """
    qset = set(question_tokens)

    triggers = block.get("triggers", [])
    if isinstance(triggers, str):
        triggers = [triggers]

    score = 0.0

    for trig in triggers:
        t_tokens = set(tokenize(trig))
        if not t_tokens:
            continue
        common = qset & t_tokens
        if common:
            score += len(common) / len(t_tokens)

    q_text = block.get("question_it", "")
    if q_text:
        qt_tokens = set(tokenize(q_text))
        if qt_tokens:
            common_q = qset & qt_tokens
            if common_q:
                score += 0.5 * (len(common_q) / len(qt_tokens))

    fam = block.get("family", "")
    if fam:
        fam_tokens = set(tokenize(fam))
        if fam_tokens & qset:
            score += 0.2

    tags = block.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]
    for tag in tags:
        tag_tokens = set(tokenize(tag))
        if tag_tokens & qset:
            score += 0.1

    return score


def apply_ctf_p560_logic(question: str, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Rafforzamento logico per domande su P560 / CTF / card / lamiera ecc.
    NON tocca i file JSON, lavora solo sul ranking in memoria.
    """
    q_norm = normalize(question)
    tokens = set(q_norm.split())

    is_p560 = "p560" in tokens or "p 560" in question.lower()
    is_ctf = "ctf" in tokens
    has_card = "card" in tokens
    has_lamiera = "lamiera" in tokens or "grecata" in tokens
    has_colpo = "colpo" in tokens or "colpi" in tokens

    boosted: List[Dict[str, Any]] = []
    for b in blocks:
        fam = b.get("family", "").upper()
        intent = b.get("intent", "") or ""
        tags = " ".join(b.get("tags", []))
        qtext = b.get("question_it", "")

        bonus = 0.0

        if is_p560 and "P560" in fam:
            bonus += 1.5
        if is_p560 and "P560" in tags.upper():
            bonus += 0.8
        if is_p560 and "P560" in qtext.upper():
            bonus += 0.5

        if is_ctf and "CTF_SYSTEM" in fam:
            bonus += 1.0

        if has_card and ("card" in qtext.lower() or "card" in tags.lower()):
            bonus += 0.7

        if has_lamiera and ("lamiera" in qtext.lower() or "lamiera" in tags.lower()):
            bonus += 0.7

        if has_colpo and ("colpo" in qtext.lower() or "colpi" in qtext.lower()):
            bonus += 0.5

        if "KILLER" in b.get("id", "").upper():
            bonus += 0.3

        b = dict(b)
        b["_logic_bonus"] = bonus
        boosted.append(b)

    return boosted


def match_block(question: str, blocks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Matching ibrido:
    - punteggio lessicale base
    - boost logico P560/CTF
    """
    if not question.strip():
        return None

    q_tokens = tokenize(question)
    boosted_blocks = apply_ctf_p560_logic(question, blocks)

    scored: List[Dict[str, Any]] = []
    for b in boosted_blocks:
        base = keyword_score(q_tokens, b)
        bonus = b.get("_logic_bonus", 0.0)
        score = base + bonus
        if "p560" in normalize(question) and "P560" in b.get("family", "").upper():
            score += 1.0
        b = dict(b)
        b["_score"] = score
        scored.append(b)

    scored.sort(key=lambda x: x.get("_score", 0.0), reverse=True)

    if not scored:
        return None

    best = scored[0]
    if best.get("_score", 0.0) < 0.2:
        return None

    return best


# ============================================================
# OPENAI HELPERS (ESTERNO)
# ============================================================

def openai_answer(question: str) -> str:
    """
    Chiede a OpenAI una risposta "esterna" stile ChatGPT ma
    strettamente limitata al contesto Tecnaria.
    """
    system_msg = (
        "Sei l'assistente tecnico Tecnaria. Rispondi solo su argomenti "
        "legati ai prodotti, cataloghi, documentazione e applicazioni Tecnaria. "
        "Se la domanda è fuori da questo perimetro, rispondi in modo neutro ma "
        "non inventare nulla al di fuori del contesto Tecnaria."
    )

    completion = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": question},
        ],
        max_output_tokens=800,
    )

    try:
        return completion.output_text
    except Exception:
        return "Non sono riuscito a recuperare una risposta esterna affidabile."


def judge_response(question: str, json_answer: str, external_answer: str) -> str:
    """
    Decide se è migliore la risposta del JSON o quella esterna.
    Ritorna:
    - 'JSON'      -> usare solo la risposta del master JSON
    - 'ESTERNA'   -> usare la risposta esterna e salvarla come candidato
    """
    prompt = (
        "Sei un giudice tecnico. Ti do una domanda e due risposte:\n"
        "• RISPOSTA_JSON: proveniente dal master JSON Tecnaria (più sicura se pertinente)\n"
        "• RISPOSTA_ESTERNA: proveniente da ChatGPT (potenzialmente più aggiornata)\n\n"
        "Devi scegliere quale delle due è MIGLIORE per la domanda, tenendo conto di:\n"
        "- aderenza al contesto Tecnaria (prodotti CTF, P560, VCEM, ecc.)\n"
        "- correttezza tecnica e assenza di fantasie\n"
        "- chiarezza e completezza.\n\n"
        "Rispondi SOLO con una parola: JSON oppure ESTERNA."
    )

    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": f"DOMANDA: {question}\n\nRISPOSTA_JSON:\n{json_answer}\n\nRISPOSTA_ESTERNA:\n{external_answer}",
        },
    ]

    completion = client.responses.create(
        model="gpt-4.1-mini",
        input=messages,
        max_output_tokens=10,
    )

    try:
        raw = completion.output_text.strip().upper()
    except Exception:
        return "JSON"

    if "ESTERNA" in raw and "JSON" not in raw:
        return "ESTERNA"
    return "JSON"


def save_new_candidate(question: str, answer: str) -> None:
    """
    Salva in un file .jsonl i nuovi QA esterni che il giudice ha scelto come migliori,
    per futura integrazione manuale nel master JSON.
    """
    os.makedirs(os.path.dirname(CANDIDATES_PATH), exist_ok=True)
    record = {
        "question": question,
        "answer": answer,
    }
    try:
        with open(CANDIDATES_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[CANDIDATE_SAVE_ERROR] {e}")


# ============================================================
# API
# ============================================================

@app.post("/api/ask")
async def ask(req: AskRequest, request: Request):
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Domanda vuota.")

    # 1) Tentativo con JSON GOLD (ibrido lessicale + logica)
    block = match_block(question, MASTER_BLOCKS)
    json_answer = ""
    if block:
        json_answer = block.get("answer_it", "").strip()

    # 2) In parallelo: risposta esterna (ChatGPT) limitata al mondo Tecnaria
    external_answer = openai_answer(question)

    # 3) Giudice (usa modello leggero) – per tua richiesta "1")
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
async def root():
    """
    Serve la UI Tecnaria GOLD (index.html nella cartella static).
    """
    index_path = os.path.join(STATIC_DIR, "index.html")
    return FileResponse(index_path)


@app.get("/health")
def health():
    """
    Endpoint di stato usato da Render / ping esterni.
    """
    return {
        "status": "Tecnaria Bot v14.1 attivo",
        "master_blocks": len(MASTER_BLOCKS),
    }
