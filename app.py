import os
import json
import random
import unicodedata
from functools import lru_cache
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# =========================================================
# CONFIG
# =========================================================

APP_NAME = "Tecnaria Sinapsi — Q/A"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FAMILIES_DIR = os.path.join(BASE_DIR, "static", "data")

SUPPORTED_LANGS = ["it", "en", "fr", "de", "es"]
FALLBACK_LANG = "en"  # per lingue non supportate → rispondiamo in inglese

# OpenAI per traduzioni (opzionale)
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
    if not text:
        return "it"

    # script non latino
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
# TRADUZIONI (OPZIONALI)
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
    if not q or openai_client is None:
        return q
    if detect_language(q) == "it":
        return q

    system_msg = (
        "Sei un traduttore tecnico. Traduci il testo seguente in ITALIANO, "
        "mantieni significato e nomi prodotti. Solo testo tradotto."
    )
    translated = _call_openai_translate(system_msg, "it", q)
    return translated or q


def translate_dynamic_answer(text: str, target_lang: str) -> str:
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
        "Translate from Italian to target language. Keep product names (Tecnaria, VCEM, CTF, CTCEM, CTL, P560, Diapason)."
    )

    translated = _call_openai_translate(system_msg, target_lang, text)
    return translated or text


# =========================================================
# CARICAMENTO FAMIGLIE / MATCHING NLM
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
        # se non specificato, di default dynamic
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

    # boost se molto simile a una delle domande note
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

    # soglia: se troppo basso → nessuna risposta
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

# Static files (per sicurezza, se ti servono asset futuri)
static_dir = os.path.join(BASE_DIR, "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
def serve_index():
    """
    Serve l'interfaccia HTML principale.
    Se index.html non c'è, mostra solo info base JSON.
    """
    index_path = os.path.join(BASE_DIR, "index.html")
    if os.path.isfile(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    # fallback se manca l'HTML
    return f"""<pre>{APP_NAME}\nstatus: OK\nfamilies_dir: {get_families_dir()}</pre>"""


@app.get("/api/config")
def api_config():
    return {
        "ok": True,
        "app": APP_NAME,
        "families_dir": get_families_dir(),
        "supported_langs": SUPPORTED_LANGS,
        "fallback_lang": FALLBACK_LANG,
        "translation": bool(openai_client is not None),
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
        lang = (payload.lang or "").lower()
        if lang not in SUPPORTED_LANGS:
            lang = FALLBACK_LANG
    else:
        lang = choose_language(original_q)

    # Domanda per il matching (in IT se necessario)
    q_for_match = original_q
    if detect_language(original_q) != "it":
        q_for_match = translate_question_to_it(original_q)

    # Carica famiglia
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
            "text": text,
        }

    # Match NLM
    item = pick_best_item(fam, q_for_match)
    if not item:
        msg_it = "Nessuna risposta trovata per questa domanda."
        text = translate_dynamic_answer(msg_it, lang)
        return {
            "ok": False,
            "family": family,
            "q": original_q,
            "lang": lang,
            "text": text,
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
            "text": text,
        }

    final_text = translate_dynamic_answer(base_text_it, lang)

    return {
        "ok": True,
        "family": family,
        "q": original_q,
        "lang": lang,
        "id": item.get("id"),
        "mode": item.get("mode", "dynamic"),
        "text": final_text,
    }
