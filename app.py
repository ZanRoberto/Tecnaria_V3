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

# OpenAI per traduzioni
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

openai_client = None
if OPENAI_API_KEY:
    try:
        from openai import OpenAI
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception:
        openai_client = None


# =========================================================
# UTILS TESTO
# =========================================================

def _normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    cleaned = []
    for ch in s:
        if ch.isalnum() or ch.isspace():
            cleaned.append(ch)
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


# =========================================================
# LINGUA: RICONOSCIMENTO
# =========================================================

def detect_language(text: str) -> str:
    """
    Rilevamento semplice:
    - controlla script (cirillico, arabo, ecc.)
    - heuristics su parole chiave
    - se nulla di chiaro → 'other'
    """
    if not text:
        return "it"

    # script non latino → other
    for ch in text:
        if "\u0400" <= ch <= "\u04FF":
            return "other"  # cirillico
        if "\u4E00" <= ch <= "\u9FFF":
            return "other"  # cinese
        if "\u0600" <= ch <= "\u06FF":
            return "other"  # arabo
        if "\u0590" <= ch <= "\u05FF":
            return "other"  # ebraico

    txt = _normalize_text(text)
    tokens = set(txt.split())

    it_words = {"che", "cosa", "posso", "come", "quando", "dove", "non", "uso", "connettori", "soletta", "travetto"}
    en_words = {"what", "can", "use", "how", "when", "where", "not", "connector", "slab", "beam", "steel"}
    fr_words = {"quoi", "puis", "utiliser", "comment", "quand", "ou", "non", "connecteur", "dalle"}
    de_words = {"was", "kann", "verwenden", "wie", "wann", "wo", "nicht", "verbinder", "platte", "stahl"}
    es_words = {"que", "puedo", "usar", "como", "cuando", "donde", "no", "conector", "losa", "viga"}

    scores = {
        "it": len(tokens & it_words),
        "en": len(tokens & en_words),
        "fr": len(tokens & fr_words),
        "de": len(tokens & de_words),
        "es": len(tokens & es_words),
    }

    best_lang = max(scores, key=scores.get)
    if scores[best_lang] == 0:
        return "other"

    return best_lang


def choose_language(q: str) -> str:
    lang = detect_language(q)
    if lang in SUPPORTED_LANGS:
        return lang
    return FALLBACK_LANG  # es. russo → en


# =========================================================
# TRADUZIONI DINAMICHE (DOMANDA + RISPOSTA)
# =========================================================

def _call_openai_translate(prompt_system: str, target_lang: str, text: str) -> Optional[str]:
    if openai_client is None:
        return None
    try:
        resp = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": prompt_system},
                {
                    "role": "user",
                    "content": f"Target language: {target_lang}\n\n{text}"
                }
            ],
            temperature=0.2,
            max_tokens=800,
        )
        out = resp.choices[0].message.content.strip()
        return out or None
    except Exception:
        return None


def translate_question_to_it(q: str) -> str:
    """
    Traduci la domanda in italiano per il matching.
    Se qualcosa va storto → restituisce la domanda originale.
    """
    if not q or openai_client is None:
        return q

    # se è già italiano, non toccare
    if detect_language(q) == "it":
        return q

    system_msg = (
        "Sei un traduttore tecnico. Traduci il testo seguente in ITALIANO in modo preciso, "
        "mantenendo il significato strutturale e i nomi dei prodotti. "
        "Rispondi solo con la frase tradotta, senza aggiunte."
    )
    translated = _call_openai_translate(system_msg, "it", q)
    return translated or q


def translate_dynamic_answer(text: str, target_lang: str) -> str:
    """
    Traduci la RISPOSTA nella lingua dell'utente.
    - se target_lang == it → restituisce text
    - se target_lang supportata → traduce
    - se target_lang non supportata → traduce in FALLBACK_LANG
    - se non disponibile OpenAI → restituisce text
    """
    if not text:
        return text

    if target_lang == "it":
        return text

    if target_lang not in SUPPORTED_LANGS:
        target_lang = FALLBACK_LANG

    if openai_client is None:
        return text

    system_msg = (
        "You are a precise technical translator for structural engineering Q&A. "
        "Translate the following answer from Italian to the target language. "
        "Mantieni intatti nomi propri (Tecnaria, VCEM, CTF, CTCEM, CTL, P560, Diapason, ecc.) "
        "e il tono professionale. Non aggiungere testo extra."
    )

    translated = _call_openai_translate(system_msg, target_lang, text)
    return translated or text


# =========================================================
# CARICAMENTO FAMIGLIE / NLM MATCH
# =========================================================

def get_families_dir() -> str:
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

    items = data.get("items", [])
    for item in items:
        item.setdefault("questions", [])
        item.setdefault("tags", [])
        item.setdefault("canonical", "")
        item.setdefault("response_variants", [])
        item.setdefault("mode", data.get("variants_strategy", "dynamic"))
    return data


def score_item(q_tokens: List[str], item: Dict[str, Any]) -> float:
    if not q_tokens:
        return 0.0

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

    score = inter / len(q_set)

    # boost se domanda molto simile
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

    if best is None or best_score < 0.18:
        return None

    return best


def pick_response_text(item: Dict[str, Any]) -> str:
    mode = item.get("mode", "dynamic")
    canonical = (item.get("canonical") or "").strip()
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

    return canonical or (variants[0].strip() if variants else "")


# =========================================================
# API
# =========================================================

class AskPayload(BaseModel):
    q: str
    family: str
    lang: Optional[str] = None  # override manuale opzionale


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
        "fallback_lang": FALLBACK_LANG,
        "translation": bool(openai_client is not None)
    }


@app.post("/api/ask")
def api_ask(payload: AskPayload):
    original_q = (payload.q or "").strip()
    if not original_q:
        raise HTTPException(status_code=400, detail="Domanda vuota.")

    family = (payload.family or "").strip().upper()
    if not family:
        raise HTTPException(status_code=400, detail="Famiglia mancante.")

    # lingua target risposta
    if payload.lang:
        lang = payload.lang.lower()
        if lang not in SUPPORTED_LANGS:
            lang = FALLBACK_LANG
    else:
        lang = choose_language(original_q)

    # traduciamo la domanda in IT per il matching (se serve)
    q_for_match = original_q
    if detect_language(original_q) != "it":
        q_for_match = translate_question_to_it(original_q)

    try:
        fam = load_family(family)
    except FileNotFoundError:
        msg_it = "Nessuna base conoscitiva disponibile per questa famiglia."
        text = translate_dynamic_answer(msg_it, lang)
        return {
            "ok": False,
            "family": family,
            "q": original_q,
            "lang": lang,
            "text": text
        }

    item = pick_best_item(fam, q_for_match)
    if not item:
        msg_it = "Nessuna risposta trovata per questa domanda."
        text = translate_dynamic_answer(msg_it, lang)
        return {
            "ok": False,
            "family": family,
            "q": original_q,
            "lang": lang,
            "text": text
        }

    base_text_it = pick_response_text(item)
    if not base_text_it:
        msg_it = "Contenuto non disponibile per questa voce."
        text = translate_dynamic_answer(msg_it, lang)
        return {
            "ok": False,
            "family": family,
            "q": original_q,
            "lang": lang,
            "id": item.get("id"),
            "text": text
        }

    final_text = translate_dynamic_answer(base_text_it, lang)

    return {
        "ok": True,
        "family": family,
        "q": original_q,
        "lang": lang,
        "id": item.get("id"),
        "mode": item.get("mode", "dynamic"),
        "text": final_text
    }
