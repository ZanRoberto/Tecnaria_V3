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

app = FastAPI(title="TECNARIA GOLD – MATCHING v12.2 (A)", version="12.2.0")

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
        except Exception:
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
# MATCHING ENGINE (LESSIC + AI RERANK) – v12.2
# ============================================================

def score_trigger(trigger: str, q_tokens: set, q_norm: str) -> float:
    """
    Punteggio di un singolo trigger.
    Patch v12.1: i trigger con UNA SOLA PAROLA (es. 'ctf', 'posare')
    vengono ignorati perché troppo generici e rumorosi.
    """
    trig_norm = normalize(trigger)
    if not trig_norm:
        return 0.0

    trig_tokens = set(trig_norm.split())

    # ❗ Trigger troppo generici (una sola parola) → li ignoriamo
    if len(trig_tokens) <= 1:
        return 0.0

    score = 0.0

    # 1) token match totale
    if trig_tokens.issubset(q_tokens):
        score += 3.0

    # 2) match parziale > metà token
    inter = trig_tokens.intersection(q_tokens)
    if len(inter) >= max(1, len(trig_tokens) // 2):
        score += len(inter) / len(trig_tokens)

    # 3) substring significativa
    if len(trig_norm) >= 10 and trig_norm in q_norm:
        score += 0.5

    return score


def score_block(question: str, block: Dict[str, Any]) -> float:
    """
    Punteggio complessivo di un blocco:
    - somma dei punteggi trigger
    - + similarità domanda_utente vs question_it del blocco
    """
    q_norm = normalize(question)
    q_tokens = set(tokenize(question))

    # trigger score
    trig_score = 0.0
    for trigger in block.get("triggers", []) or []:
        trig_score += score_trigger(trigger, q_tokens, q_norm)

    # question_it similarity
    q_it = block.get("question_it") or ""
    q_it_tokens = set(tokenize(q_it)) if q_it else set()

    sim_score = 0.0
    if q_it_tokens:
        inter = q_tokens.intersection(q_it_tokens)
        if inter:
            # % dei token della domanda del blocco che compaiono nella domanda utente
            sim_score = len(inter) / len(q_it_tokens)
            # pesiamo di più la similarità semantica della domanda
            sim_score *= 3.0  # peso forte

    total = trig_score + sim_score

    # penalizza overview
    if "OVERVIEW" in (block.get("id") or "").upper():
        total *= 0.5

    return total


def lexical_candidates(question: str, blocks: List[Dict[str, Any]], limit: int = 15):
    scored: List[Tuple[float, Dict[str, Any]]] = []

    for block in blocks:
        s = score_block(question, block)
        if s > 0:
            scored.append((s, block))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:limit]


def ai_rerank(question: str, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Usa l'AI SOLO per scegliere l'ID tra i candidati.
    NON può generare testo, NON può inventare ID.
    Patch v12.2 (A):
    - se la domanda parla di lamiera/ala/ondina/laminazione/rigonfiamenti,
      evitiamo blocchi che parlano SOLO di chiodi difettosi/punta danneggiata.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # --- PATCH STRADA A: filtro "geometria vs chiodi"
    q_norm = normalize(question)
    geometry_terms = [
        "lamiera", "ondina", "onda", "ala",
        "imbarcata", "imbarcato", "imbarcatura",
        "laminazione", "rigonfiamento", "bombatura",
        "rigidita", "rigidezza"
    ]
    defect_terms = [
        "chiodo", "chiodi", "punta", "danneggiata",
        "danneggiato", "danneggiate", "danneggiati",
        "difettoso", "difettosi", "difettosa"
    ]

    question_is_geometry = any(t in q_norm for t in geometry_terms)

    filtered_candidates = candidates
    if question_is_geometry:
        tmp = []
        for b in candidates:
            text_block = (
                (b.get("id") or "") + " " +
                (b.get("question_it") or "") + " " +
                " ".join(b.get("triggers") or [])
            )
            tb_norm = normalize(text_block)

            has_defect = any(t in tb_norm for t in defect_terms)
            has_geometry = any(t in tb_norm for t in geometry_terms)

            # Se il blocco parla SOLO di difetti dei chiodi e NON di geometria,
            # lo escludiamo in presenza di domanda geometrica.
            if question_is_geometry and has_defect and not has_geometry:
                continue

            tmp.append(b)

        # Se abbiamo ancora almeno un candidato dopo il filtro, usiamo quelli.
        # Se li abbiamo esclusi tutti, torniamo alla lista originale.
        if tmp:
            filtered_candidates = tmp

    candidates = filtered_candidates
    candidate_ids = [b.get("id") for b in candidates]

    try:
        desc = "\n".join(
            f"- ID:{b.get('id')} | Q:{b.get('question_it')}"
            for b in candidates
        )

        prompt = (
            "Sei un motore di routing per una knowledge base. "
            "Ti do una domanda utente e una lista di blocchi possibili.\n\n"
            f"DOMANDA:\n{question}\n\n"
            f"CANDIDATI:\n{desc}\n\n"
            "Devi restituire SOLO l'ID del blocco che risponde meglio.\n"
            "Regole specifiche:\n"
            "- Se la domanda parla di puntale, appoggio, ondina, inclinazione della P560, "
            "scegli blocchi che parlano di appoggio o puntale, NON blocchi che parlano di sovra-infissione o propulsore.\n"
            "- Se la domanda parla di distanza testa–piastra, profondità o propulsore, allora scegli blocchi su sovra-infissione.\n"
            "- Evita blocchi di OVERVIEW se esistono blocchi più specifici.\n"
            "- Rispondi SOLO con un ID presente nella lista dei candidati.\n"
        )

        res = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20,
            temperature=0.0,
        )

        chosen = (res.choices[0].message.content or "").strip()

        if chosen not in candidate_ids:
            return candidates[0]

        for b in candidates:
            if b.get("id") == chosen:
                return b

    except Exception as e:
        print("[AI RERANK ERROR]", e)

    return candidates[0]


def find_best_block(question: str) -> Tuple[Dict[str, Any], float]:
    # Overlay (se li userai)
    over = lexical_candidates(question, S.overlay_blocks)
    if over:
        over_blocks = [b for s, b in over]
        best_o = ai_rerank(question, over_blocks)
        best_s = max(s for s, b in over if b is best_o)
        return best_o, float(best_s)

    # Master
    master = lexical_candidates(question, S.master_blocks)
    if not master:
        return None, 0.0

    master_blocks = [b for s, b in master]
    best_m = ai_rerank(question, master_blocks)
    best_s = max(s for s, b in master if b is best_m)

    return best_m, float(best_s)


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
