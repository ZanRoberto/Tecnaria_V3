import os
import json
import unicodedata
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# -------------------------------------------------
# PATH DI BASE
# -------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = STATIC_DIR / "data"

# -------------------------------------------------
# CONFIG DI BASE (usata anche da /api/config)
# -------------------------------------------------
SUPPORTED_LANGS = ["it", "en", "fr", "de", "es"]
FALLBACK_LANG = "en"

app = FastAPI(title="Tecnaria Sinapsi — Q/A")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cache in memoria dei file famiglia
FAMILY_CACHE: Dict[str, Dict[str, Any]] = {}


# -------------------------------------------------
# MODELLI I/O
# -------------------------------------------------
class AskPayload(BaseModel):
    q: str
    family: str


# -------------------------------------------------
# UTILITY
# -------------------------------------------------
def normalize_text(text: str) -> str:
    """Normalizza il testo per il confronto: minuscole, niente accenti, niente punteggiatura extra."""
    if not text:
        return ""
    text = text.lower()
    text = "".join(
        c for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    )
    # rimuovo solo caratteri troppo strani, tengo lettere/numeri/spazi
    cleaned = []
    for c in text:
        if c.isalnum() or c.isspace():
            cleaned.append(c)
        else:
            cleaned.append(" ")
    return " ".join("".join(cleaned).split())


def guess_lang(text: str) -> str:
    """Heuristica semplice per capire la lingua dalla domanda."""
    t = text.lower()
    # ultra grezza ma sufficiente per routing base
    if any(w in t for w in ["perché", "perche", "posso", "devo", "solaio", "connettori"]):
        return "it"
    if any(w in t for w in ["because", "can i", "should i", "beam", "slab"]):
        return "en"
    if any(w in t for w in ["pourquoi", "comment", "plancher"]):
        return "fr"
    if any(w in t for w in ["warum", "wie", "decken", "verbinden"]):
        return "de"
    if any(w in t for w in ["por qué", "porque", "puedo", "los conectores", "forjado"]):
        return "es"
    # se non riconosciuta → fallback inglese
    return FALLBACK_LANG


def load_family(family: str) -> Dict[str, Any]:
    """
    Carica il file JSON della famiglia.
    Usa il nome così come lo passi tu dalla UI: VCEM, CTF, CTL, CTCEM, DIAPASON, P560, COMM.
    Cerca <FAMILY>.json o <FAMILY>.JSON nella cartella /static/data.
    """
    key = family.upper()
    if key in FAMILY_CACHE:
        return FAMILY_CACHE[key]

    # nomi possibili: VCEM.json, VCEM.golden.json ecc.
    candidates: List[Path] = []
    for p in DATA_DIR.glob(f"{key}*.json"):
        candidates.append(p)

    if not candidates:
        raise FileNotFoundError(f"Nessun file JSON trovato per la famiglia {family}")

    # prendo il più specifico che inizia esattamente con FAMILY (consistente con come hai nominato i file)
    candidates.sort()
    path = candidates[0]

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # validazione minima
    if "items" not in data or not isinstance(data["items"], list):
        raise ValueError(f"File famiglia {family} senza 'items' valido: {path}")

    FAMILY_CACHE[key] = data
    return data


def collect_patterns(item: Dict[str, Any]) -> List[str]:
    """Raccoglie tutte le possibili frasi di match da un blocco (questions + paraphrases)."""
    patterns: List[str] = []
    for field in ("questions", "paraphrases", "aliases", "triggers"):
        vals = item.get(field) or []
        if isinstance(vals, str):
            vals = [vals]
        for v in vals:
            v = (v or "").strip()
            if v:
                patterns.append(v)
    return patterns


def match_score(q_norm: str, pattern_norm: str) -> float:
    """
    Semplice punteggio di similarità:
    - 1.0 se pattern contenuto integralmente
    - token overlap otherwise
    """
    if not pattern_norm:
        return 0.0
    if pattern_norm in q_norm or q_norm in pattern_norm:
        return 1.0
    q_tokens = set(q_norm.split())
    p_tokens = set(pattern_norm.split())
    if not p_tokens:
        return 0.0
    inter = len(q_tokens & p_tokens)
    return inter / len(p_tokens)


def find_best_item(family_data: Dict[str, Any], q: str) -> Optional[Tuple[Dict[str, Any], float]]:
    """Trova il blocco migliore per la domanda."""
    q_norm = normalize_text(q)
    best_item = None
    best_score = 0.0

    for item in family_data.get("items", []):
        for patt in collect_patterns(item):
            s = match_score(q_norm, normalize_text(patt))
            if s > best_score:
                best_score = s
                best_item = item

    # soglia: se troppo basso, lo consideriamo "nessuna risposta"
    if best_item and best_score >= 0.35:
        return best_item, best_score
    return None


def choose_answer(item: Dict[str, Any], lang: str) -> Tuple[str, str]:
    """
    Sceglie la risposta nella lingua richiesta.
    Campi supportati nel JSON:
    - answer_it, answer_en, answer_fr, answer_de, answer_es
    - oppure 'answer' (italiano) come default.
    """
    # normalizza chiavi
    lang = lang.lower()
    for key in (f"answer_{lang}",):
        if key in item and item[key]:
            return item[key], lang

    # se non c'è, prova italiano
    if "answer_it" in item and item["answer_it"]:
        return item["answer_it"], "it"

    # fallback "answer"
    if "answer" in item and item["answer"]:
        return item["answer"], "it"

    # infine fallback inglese se presente
    if "answer_en" in item and item["answer_en"]:
        return item["answer_en"], "en"

    # se proprio non c'è nulla:
    return "", lang


# -------------------------------------------------
# ROUTES
# -------------------------------------------------
@app.get("/")
async def index():
    """
    Serve SEMPRE l'interfaccia HTML.
    Se vedi solo JSON quando apri il dominio, significa che questa route è stata cambiata.
    """
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=500, detail="index.html non trovato.")
    return FileResponse(index_path)


@app.get("/api/config")
async def api_config():
    """
    Config usata dalla UI.
    NON restituisce più HTML, solo meta-informazioni.
    """
    return {
        "app": "Tecnaria Sinapsi — Q/A",
        "status": "OK",
        "families_dir": str(DATA_DIR),
        "supported_langs": SUPPORTED_LANGS,
        "fallback_lang": FALLBACK_LANG,
        # la traduzione automatica lato OpenAI può essere agganciata in futuro;
        # per ora ricordiamo che le risposte GOLD sono nei JSON.
        "translation": True,
    }


@app.post("/api/ask")
async def api_ask(payload: AskPayload):
    q = (payload.q or "").strip()
    family = (payload.family or "").strip().upper()

    if not q:
        raise HTTPException(status_code=400, detail="Domanda vuota.")

    try:
        fam_data = load_family(family)
    except FileNotFoundError:
        return {
            "ok": False,
            "family": family,
            "q": q,
            "text": f"Nessun dataset GOLD trovato per la famiglia {family}.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # rileva lingua domanda
    lang = guess_lang(q)

    found = find_best_item(fam_data, q)
    if not found:
        # segnale pulito alla UI: JSON da arricchire
        return {
            "ok": False,
            "family": family,
            "q": q,
            "lang": lang,
            "text": "Nessuna risposta trovata per questa domanda.",
        }

    item, score = found
    answer, used_lang = choose_answer(item, lang)

    if not answer:
        return {
            "ok": False,
            "family": family,
            "q": q,
            "lang": lang,
            "id": item.get("id"),
            "mode": item.get("mode", "dynamic"),
            "text": "Blocco GOLD trovato ma privo di testo risposta.",
        }

    return {
        "ok": True,
        "family": family,
        "q": q,
        "lang": used_lang,
        "id": item.get("id"),
        "mode": item.get("mode", "dynamic"),
        "score": round(float(score), 3),
        "text": answer,
    }
