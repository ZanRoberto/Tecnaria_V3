import os
import json
import random
import unicodedata
from functools import lru_cache
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# =========================================================
# CONFIG
# =========================================================

APP_NAME = "Tecnaria Sinapsi — Q/A"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FAMILIES_DIR = os.path.join(BASE_DIR, "static", "data")

SUPPORTED_LANGS = ["it", "en", "fr", "de", "es"]
FALLBACK_LANG = "en"  # per lingue non supportate → rispondiamo in inglese


# =========================================================
# UTILS
# =========================================================

def _normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    # togliamo caratteri non alfanumerici base (manteniamo spazio)
    cleaned = []
    for ch in s:
        if ch.isalnum() or ch.isspace():
            cleaned.append(ch)
        # altrimenti skip
    return " ".join("".join(cleaned).split())


def _tokenize(s: str) -> List[str]:
    s = _normalize_text(s)
    if not s:
        return []
    return [t for t in s.split() if len(t) > 1]


def _read_json(path: str) -> Any:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File non trovato: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _safe_get(d: Dict, key: str, default=None):
    v = d.get(key, default)
    return v if v is not None else default


# =========================================================
# LINGUA: RICONOSCIMENTO + TRADUZIONE DINAMICA
# =========================================================

def detect_language(text: str) -> str:
    """
    Rilevamento molto semplice:
    - se prevalgono parole tipiche di it/en/fr/de/es → quella lingua
    - se caratteri cirillici/altro → considerata 'other'
    - se dubbio → 'it'
    """
    if not text:
        return "it"

    # check caratteri non latini
    for ch in text:
        if "\u0400" <= ch <= "\u04FF":
            return "other"  # es. russo
        if "\u4E00" <= ch <= "\u9FFF":
            return "other"  # cinese
        if "\u0600" <= ch <= "\u06FF":
            return "other"  # arabo
        if "\u0590" <= ch <= "\u05FF":
            return "other"  # ebraico

    txt = _normalize_text(text)
    tokens = set(txt.split())

    # Liste minimaliste per hint linguistico
    it_words = {"che", "cosa", "posso", "come", "quando", "dove", "non", "uso", "connettori", "soletta"}
    en_words = {"what", "can", "use", "how", "when", "where", "not", "connector", "slab"}
    fr_words = {"quoi", "puis", "utiliser", "comment", "quand", "ou", "non", "connecteur"}
    de_words = {"was", "kann", "verwenden", "wie", "wann", "wo", "nicht", "verbinder"}
    es_words = {"que", "puedo", "usar", "como", "cuando", "donde", "no", "conector"}

    scores = {
        "it": len(tokens & it_words),
        "en": len(tokens & en_words),
        "fr": len(tokens & fr_words),
        "de": len(tokens & de_words),
        "es": len(tokens & es_words),
    }

    best_lang = max(scores, key=scores.get)
    if scores[best_lang] == 0:
        # niente match credibile → consideriamo "other"
        return "other"

    return best_lang


def translate_dynamic(text: str, target_lang: str) -> str:
    """
    Stub traduzione dinamica.

    Logica:
    - se target_lang == "it": torna il testo così com'è.
    - se target_lang in {en, fr, de, es}:
        QUI puoi integrare una chiamata all'API OpenAI / traduttore esterno.
        Per ora, se non configurato, restituiamo lo stesso testo
        (per non rompere il servizio).
    - se target_lang non supportata: rispondiamo in inglese.
    """
    if not text:
        return text

    if target_lang == "it":
        return text

    if target_lang not in SUPPORTED_LANGS:
        # fallback hard → english
        target_lang = FALLBACK_LANG

    # QUI puoi innestare il vero motore di traduzione.
    # Per ora lasciamo il testo in italiano per evitare errori
    # ma l'architettura è pronta.
    # Esempio (pseudo):
    # return external_translate(text, target_lang)

    return text  # placeholder: da sostituire con traduzione reale


def choose_language(q: str) -> str:
    lang = detect_language(q)
    if lang in SUPPORTED_LANGS:
        return lang
    # se è lingua non supportata → fallback in inglese
    return FALLBACK_LANG


# =========================================================
# CARICAMENTO FAMIGLIE / NLM MATCH
# =========================================================

def get_families_dir() -> str:
    # permette override via env / config.runtime.json
    cfg_path = os.path.join(DEFAULT_FAMILIES_DIR, "config.runtime.json")
    if os.path.isfile(cfg_path):
        try:
            cfg = _read_json(cfg_path)
            d = cfg.get("families_dir")
            if d:
                return d
        except Exception:
            pass
    return DEFAULT_FAMILIES_DIR


@lru_cache(maxsize=128)
def load_family(family: str) -> Dict[str, Any]:
    families_dir = get_families_dir()
    filename = f"{family}.json"
    path = os.path.join(families_dir, filename)
    data = _read_json(path)

    # Normalizziamo struttura
    items = data.get("items", [])
    for item in items:
        # normalizza campi mancanti
        item.setdefault("questions", [])
        item.setdefault("tags", [])
        item.setdefault("canonical", "")
        item.setdefault("response_variants", [])
        item.setdefault("mode", data.get("variants_strategy", "dynamic"))
    return data


def score_item(q_tokens: List[str], item: Dict[str, Any]) -> float:
    """
    Matching semplice:
    - considera domande, tags, canonical
    - calcola overlap token / boosting su domande
    """
    if not q_tokens:
        return 0.0

    # costruiamo un set di testo indicizzato
    bag: List[str] = []

    for qq in item.get("questions", []):
        bag.extend(_tokenize(qq))

    for tg in item.get("tags", []):
        bag.extend(_tokenize(tg))

    bag.extend(_tokenize(item.get("canonical", "")))

    if not bag:
        return 0.0

    bag_set = set(bag)
    q_set = set(q_tokens)

    inter = len(bag_set & q_set)
    if inter == 0:
        return 0.0

    # ratio semplice
    score = inter / len(q_set)

    # se c'è match forte su una domanda, boost
    for qq in item.get("questions", []):
        qq_tokens = set(_tokenize(qq))
        if len(qq_tokens & q_set) >= max(2, int(0.6 * len(q_set))):
            score += 0.5
            break

    return score


def pick_best_item(family_data: Dict[str, Any], q: str) -> Optional[Dict[str, Any]]:
    items = family_data.get("items", [])
    if not items:
        return None

    q_tokens = _tokenize(q)
    if not q_tokens:
        return None

    best = None
    best_score = 0.0

    for item in items:
        s = score_item(q_tokens, item)
        if s > best_score:
            best_score = s
            best = item

    # soglia minima: se troppo basso, meglio dire nessuna risposta
    if best is None or best_score < 0.18:
        return None

    return best


def pick_response_text(item: Dict[str, Any]) -> str:
    mode = item.get("mode", "dynamic")
    canonical = item.get("canonical", "").strip()
    variants = item.get("response_variants") or []

    if mode == "canonical":
        return canonical or (variants[0].strip() if variants else "")

    if mode == "dynamic":
        pool = []
        if canonical:
            pool.append(canonical)
        pool.extend([v for v in variants if isinstance(v, str) and v.strip()])
        if not pool:
            return ""
        return random.choice(pool).strip()

    # fallback
    return canonical or (variants[0].strip() if variants else "")


# =========================================================
# API
# =========================================================

class AskPayload(BaseModel):
    q: str
    family: str
    # opzionale: permetti override lingua manuale se mai servirà
    lang: Optional[str] = None


app = FastAPI(title=APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "app": APP_NAME,
        "status": "OK",
        "families_dir": get_families_dir()
    }


@app.get("/api/config")
def api_config():
    return {
        "ok": True,
        "app": APP_NAME,
        "families_dir": get_families_dir(),
        "supported_langs": SUPPORTED_LANGS,
        "fallback_lang": FALLBACK_LANG
    }


@app.post("/api/ask")
def api_ask(payload: AskPayload):
    q = (payload.q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Domanda vuota.")

    family = (payload.family or "").strip().upper()
    if not family:
        raise HTTPException(status_code=400, detail="Famiglia mancante.")

    # rileva lingua
    if payload.lang:
        lang = payload.lang.lower()
        if lang not in SUPPORTED_LANGS:
            lang = FALLBACK_LANG
    else:
        lang = choose_language(q)

    try:
        fam = load_family(family)
    except FileNotFoundError:
        return {
            "ok": False,
            "family": family,
            "q": q,
            "lang": lang,
            "text": "Nessuna base conoscitiva disponibile per questa famiglia."
        }

    item = pick_best_item(fam, q)
    if not item:
        # nessun match adeguato
        msg_it = "Nessuna risposta trovata per questa domanda."
        text = translate_dynamic(msg_it, lang)
        return {
            "ok": False,
            "family": family,
            "q": q,
            "lang": lang,
            "text": text
        }

    base_text = pick_response_text(item)
    if not base_text:
        msg_it = "Contenuto non disponibile per questa voce."
        text = translate_dynamic(msg_it, lang)
        return {
            "ok": False,
            "family": family,
            "q": q,
            "lang": lang,
            "id": item.get("id"),
            "text": text
        }

    final_text = translate_dynamic(base_text, lang)

    return {
        "ok": True,
        "family": family,
        "q": q,
        "lang": lang,
        "id": item.get("id"),
        "mode": item.get("mode", "dynamic"),
        "text": final_text
    }
