import os
import json
import re
import unicodedata
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ============================================================
#  PATH / FILE
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "static", "data")

# 👉 FILE UNICO CHE USI TU (NOME CORRETTO)
KB_PATH = os.path.join(DATA_DIR, "ctf_system.json")

FALLBACK_FAMILY = "COMM"
FALLBACK_ID = "COMM-FALLBACK-NOANSWER-0001"
FALLBACK_MESSAGE = (
    "Per questa domanda non trovo una risposta GOLD nei dati caricati. "
    "Meglio un confronto diretto con l’ufficio tecnico Tecnaria, indicando tipo di solaio, travi, spessori e vincoli."
)

# ============================================================
#  FASTAPI
# ============================================================

app = FastAPI(title="Tecnaria Sinapsi – Motore GOLD CTF_SYSTEM + P560")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
#  MODELLI
# ============================================================

class AskRequest(BaseModel):
    q: str
    lang: str = "it"
    mode: str = "gold"


class AskResponse(BaseModel):
    ok: bool
    answer: str
    family: str
    id: str
    mode: str
    lang: str


# ============================================================
#  UTIL DI NORMALIZZAZIONE
# ============================================================

def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )


def normalize_text(s: str) -> str:
    if s is None:
        return ""
    s = strip_accents(s)
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def tokenize_norm(s: str) -> List[str]:
    return normalize_text(s).split()


# ============================================================
#  STATO IN MEMORIA
# ============================================================

class BrainState:
    blocks: List[Dict[str, Any]] = []
    tokens_by_id: Dict[str, Set[str]] = {}


S = BrainState()

# ============================================================
#  CARICAMENTO KB
# ============================================================

def load_kb() -> None:
    if not os.path.exists(KB_PATH):
        raise RuntimeError(f"File KB non trovato: {KB_PATH}")

    with open(KB_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    blocks = data.get("blocks")
    if not isinstance(blocks, list):
        raise RuntimeError("Struttura KB non valida: manca 'blocks' come lista")

    S.blocks = []
    S.tokens_by_id = {}

    for blk in blocks:
        if not isinstance(blk, dict):
            continue
        blk_id = blk.get("id")
        if not isinstance(blk_id, str):
            continue

        S.blocks.append(blk)
        tokens: Set[str] = set()

        def add_tokens(val: Any):
            if isinstance(val, str):
                for t in tokenize_norm(val):
                    if t:
                        tokens.add(t)
            elif isinstance(val, list):
                for v in val:
                    if isinstance(v, str):
                        for t in tokenize_norm(v):
                            if t:
                                tokens.add(t)

        add_tokens(blk.get("question_it"))
        add_tokens(blk.get("triggers", []))
        add_tokens(blk.get("tags", []))

        fam = str(blk.get("family", "")).upper()
        if "P560" in fam:
            tokens.add("p560")
        if "CTF" in fam:
            tokens.add("ctf")

        S.tokens_by_id[blk_id] = tokens

    print(f"[BOOT] Caricati {len(S.blocks)} blocchi GOLD da {KB_PATH}")


# ============================================================
#  SCORING
# ============================================================

def score_block(q_norm: str, q_tokens: Set[str], blk: Dict[str, Any]) -> float:
    blk_id = blk.get("id", "")
    family = str(blk.get("family", "")).upper()
    score = 0.0

    for field, weight in [
        ("triggers", 10.0),
        ("tags", 3.0),
    ]:
        vals = blk.get(field, [])
        if not isinstance(vals, list):
            continue
        for v in vals:
            if not isinstance(v, str):
                continue
            tv = normalize_text(v)
            if tv and tv in q_norm:
                score += weight

    ref = S.tokens_by_id.get(blk_id, set())
    common = q_tokens & ref
    score += 1.0 * len(common)

    if "P560" in family or "P560" in blk_id.upper():
        if any(tok in q_tokens for tok in ["p560", "pistola", "sparachiodi", "chiodatrice"]):
            score += 8.0

    if "CTF" in family or "CTF_SYSTEM" in family:
        if any(tok in q_tokens for tok in ["ctf", "lamiera", "grecata", "ondina", "card"]):
            score += 5.0

    return score


def find_best_block(q: str) -> Optional[Dict[str, Any]]:
    q_norm = normalize_text(q)
    if not q_norm:
        return None
    q_tokens = set(q_norm.split())

    best: Optional[Dict[str, Any]] = None
    best_score: float = 0.0

    for blk in S.blocks:
        s = score_block(q_norm, q_tokens, blk)
        if s > best_score:
            best_score = s
            best = blk

    if best is None or best_score <= 0.0:
        return None

    return best


# ============================================================
#  ESTRAZIONE RISPOSTA
# ============================================================

def pick_answer_it(blk: Dict[str, Any]) -> Optional[str]:
    ans = blk.get("answer_it")
    if isinstance(ans, str) and ans.strip():
        return ans.strip()

    ga = blk.get("gold_answer_it")
    if isinstance(ga, str) and ga.strip():
        return ga.strip()

    rv = blk.get("response_variants")
    if isinstance(rv, dict):
        g = rv.get("gold") or rv.get("GOLD") or {}
        if isinstance(g, dict):
            txt = g.get("it") or g.get("IT")
            if isinstance(txt, str) and txt.strip():
                return txt.strip()

    return None


def enforce_terminologia(family: str, txt: str) -> str:
    if not isinstance(txt, str):
        return txt
    return re.sub(r"\bperni\b", "chiodi idonei Tecnaria", txt, flags=re.IGNORECASE)


# ============================================================
#  ENDPOINTS
# ============================================================

@app.on_event("startup")
def on_startup():
    load_kb()


@app.get("/health")
def health():
    return {
        "ok": True,
        "blocks_loaded": len(S.blocks),
        "kb_path": KB_PATH,
    }


@app.post("/api/ask", response_model=AskResponse)
def api_ask(req: AskRequest) -> AskResponse:
    if (req.mode or "").lower() != "gold":
        raise HTTPException(status_code=400, detail="Modalità non supportata. Usa mode=gold.")

    q = (req.q or "").strip()
    if not q:
        return AskResponse(
            ok=False,
            answer="Domanda vuota: specifica il quesito su P560, CTF o altri connettori Tecnaria.",
            family=FALLBACK_FAMILY,
            id=FALLBACK_ID,
            mode="gold",
            lang=req.lang or "it",
        )

    blk = find_best_block(q)
    if not blk:
        return AskResponse(
            ok=False,
            answer=FALLBACK_MESSAGE,
            family=FALLBACK_FAMILY,
            id=FALLBACK_ID,
            mode="gold",
            lang=req.lang or "it",
        )

    txt = pick_answer_it(blk)
    if not txt:
        return AskResponse(
            ok=False,
            answer="Risposta GOLD non trovata per il blocco selezionato.",
            family=str(blk.get("family", FALLBACK_FAMILY)),
            id=str(blk.get("id", FALLBACK_ID)),
            mode="gold",
            lang=req.lang or "it",
        )

    family = str(blk.get("family", FALLBACK_FAMILY))
    txt = enforce_terminologia(family, txt)

    return AskResponse(
        ok=True,
        answer=txt,
        family=family,
        id=str(blk.get("id", FALLBACK_ID)),
        mode="gold",
        lang=req.lang or "it",
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
