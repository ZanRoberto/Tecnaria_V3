import json
import os
import random
from typing import Dict, Any, List, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# -------------------------------------------------------
# CONFIGURAZIONE DI BASE
# -------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "static", "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.runtime.json")

DEFAULT_MODE = "gold"  # modalità di default
CURRENT_MODE = DEFAULT_MODE  # stato globale (cambia con GOLD:/CANONICO:)

FAMILIES: Dict[str, Dict[str, Any]] = {}  # cache JSON famiglie


# -------------------------------------------------------
# MODELLI
# -------------------------------------------------------

class AskRequest(BaseModel):
    question: str
    lang: str | None = None
    mode: str | None = None  # opzionale, se la UI vuole forzare


class AskResponse(BaseModel):
    answer: str
    family: str | None
    id: str | None
    score: float
    lang: str
    mode: str
    meta: Dict[str, Any] = {}


# -------------------------------------------------------
# UTILS CARICAMENTO
# -------------------------------------------------------

def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def detect_lang(text: str) -> str:
    t = text.lower()
    # euristiche minime: se la domanda arriva in lingue diverse, le usiamo solo per meta
    if any(w in t for w in [" the ", " can ", " how ", "what "]):
        return "en"
    if "¿" in t or any(w in t for w in [" que ", " donde ", " puedo "]):
        return "es"
    if any(w in t for w in [" quel ", " où ", " pourquoi "]):
        return "fr"
    if any(w in t for w in [" was ", " wie ", " warum ", " welche "]):
        return "de"
    return "it"


def normalize_text(s: str) -> str:
    return " ".join((s or "").lower().strip().split())


def keyword_score(question: str, text: str) -> float:
    q = set(normalize_text(question).split())
    t = set(normalize_text(text).split())
    if not q or not t:
        return 0.0
    inter = len(q & t)
    if inter == 0:
        return 0.0
    return inter / len(q)


# -------------------------------------------------------
# CARICAMENTO CONFIG & FAMIGLIE
# -------------------------------------------------------

def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_PATH):
        # fallback se manca il file
        return {
            "mode": DEFAULT_MODE,
            "families": ["COMM", "CTCEM", "CTF", "CTL", "CTL_MAXI", "DIAPASON", "P560", "VCEM"],
            "lock_to_core": True
        }
    cfg = load_json(CONFIG_PATH)
    # normalizza
    cfg.setdefault("mode", DEFAULT_MODE)
    return cfg


def load_families() -> None:
    global FAMILIES
    cfg = load_config()
    families = cfg.get("families", [])
    loaded = {}
    for fam in families:
        path = os.path.join(DATA_DIR, f"{fam}.json")
        if not os.path.exists(path):
            continue
        data = load_json(path)
        items = data.get("items", [])
        # normalizza ogni blocco
        for it in items:
            it.setdefault("id", "")
            it.setdefault("questions", [])
            it.setdefault("canonical", "")
            # response_variants: list[str] per GOLD
            if "response_variants" not in it or not isinstance(it["response_variants"], list):
                it["response_variants"] = []
            # mode interno blocco (non la UI): default dynamic
            it.setdefault("mode", "dynamic")
        loaded[fam] = {
            "family": data.get("family", fam),
            "items": items,
            "meta": data.get("meta", {}),
        }
    FAMILIES = loaded


# -------------------------------------------------------
# LOGICA GOLD / CANONICO
# -------------------------------------------------------

def apply_mode_command(question: str) -> Tuple[str, str]:
    """
    Legge GOLD: / CANONICO: all'inizio domanda e aggiorna CURRENT_MODE.
    Ritorna (domanda_pulita, mode_attivo)
    """
    global CURRENT_MODE
    q = question.lstrip()

    upper = q.upper()
    if upper.startswith("GOLD:"):
        CURRENT_MODE = "gold"
        return q[5:].lstrip(), CURRENT_MODE

    if upper.startswith("CANONICO:") or upper.startswith("CANONICAL:"):
        CURRENT_MODE = "canonical"
        return q.split(":", 1)[1].lstrip(), CURRENT_MODE

    # se non c'è comando esplicito: resta il mode corrente
    return question, CURRENT_MODE


def find_best_block(question: str) -> Tuple[str | None, Dict[str, Any] | None, float]:
    """
    Cerca tra tutte le famiglie il blocco più rilevante.
    """
    best_family = None
    best_block = None
    best_score = 0.0
    q_norm = normalize_text(question)

    for fam, data in FAMILIES.items():
        for it in data["items"]:
            # punteggio su questions + canonical
            score_q = max(
                (keyword_score(q_norm, qq) for qq in it.get("questions", [])),
                default=0.0
            )
            score_c = keyword_score(q_norm, it.get("canonical", ""))
            score = max(score_q, score_c)
            if score > best_score:
                best_score = score
                best_family = fam
                best_block = it

    return best_family, best_block, float(best_score)


def generate_answer(block: Dict[str, Any], mode: str) -> str:
    """
    GOLD:
      - se ci sono response_variants: ne usa una (testo GOLD)
      - se non ci sono: usa canonical (fallback tecnico)
    CANONICO:
      - usa sempre canonical; se manca, usa prima variant
    """
    canonical = (block.get("canonical") or "").strip()
    variants: List[str] = [v.strip() for v in block.get("response_variants", []) if str(v).strip()]

    if mode == "gold":
        if variants:
            return random.choice(variants)
        if canonical:
            return canonical
        return "Per questa domanda il blocco trovato non contiene ancora una risposta GOLD strutturata."

    # mode canonical
    if canonical:
        return canonical
    if variants:
        return variants[0]
    return "Per questa domanda non è presente un testo canonico nel blocco selezionato."


# -------------------------------------------------------
# FASTAPI
# -------------------------------------------------------

app = FastAPI(title="TECNARIA Sinapsi", version="3.0", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# Static per interfaccia
static_path = os.path.join(BASE_DIR, "static")
if os.path.isdir(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")


@app.on_event("startup")
def startup_event():
    load_families()


@app.get("/api/config")
def get_config():
    cfg = load_config()
    return {
        "ok": True,
        "message": "TECNARIA Sinapsi backend attivo",
        "mode": CURRENT_MODE,
        "families": list(FAMILIES.keys()),
        "config": cfg
    }


@app.post("/api/mode")
def set_mode(payload: Dict[str, Any]):
    global CURRENT_MODE
    mode = (payload.get("mode") or "").lower()
    if mode not in ["gold", "canonical"]:
        raise HTTPException(status_code=400, detail="Mode must be 'gold' or 'canonical'.")
    CURRENT_MODE = mode
    return {"ok": True, "mode": CURRENT_MODE}


@app.post("/api/ask", response_model=AskResponse)
def ask(req: AskRequest):
    if not req.question or not req.question.strip():
        raise HTTPException(status_code=400, detail="Domanda mancante.")

    # comandi inline GOLD:/CANONICO:
    clean_question, active_mode = apply_mode_command(req.question)

    # override da payload (es. toggle UI)
    if req.mode:
        m = req.mode.lower()
        if m in ["gold", "canonical"]:
            active_mode = m

    # lingua solo meta / future use
    lang = req.lang or detect_lang(clean_question)

    family, block, score = find_best_block(clean_question)

    # soglia minima per considerare valida la risposta
    MIN_SCORE = 0.15

    if not block or score < MIN_SCORE:
        return AskResponse(
            answer="Per questa domanda non è ancora presente una risposta GOLD strutturata nei file Tecnaria. Va aggiunto un blocco dedicato nel JSON.",
            family=family,
            id=None,
            score=score,
            lang=lang,
            mode=active_mode,
            meta={"reason": "no_match_or_low_score"}
        )

    answer = generate_answer(block, active_mode)

    return AskResponse(
        answer=answer,
        family=family,
        id=block.get("id"),
        score=score,
        lang=lang,
        mode=active_mode,
        meta={
            "tags": block.get("tags", []),
            "matched_mode": block.get("mode", "dynamic")
        }
    )


@app.get("/")
def root():
    # pagina minimale: la tua UI custom può usare /static
    return {
        "ok": True,
        "message": "TECNARIA Sinapsi backend attivo",
        "mode": CURRENT_MODE,
        "families": list(FAMILIES.keys())
    }
