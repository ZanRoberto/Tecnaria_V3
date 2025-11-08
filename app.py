import json
import os
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import difflib

# -------------------------------------------------------
# PATH & CONFIG
# -------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "static" / "data"
INDEX_FILE = BASE_DIR / "static" / "index.html"
CONFIG_FILE = DATA_DIR / "config.runtime.json"

FAMILY_CACHE: Dict[str, Dict[str, Any]] = {}

DEFAULT_SUPPORTED_LANGS = ["it", "en", "fr", "de", "es"]
DEFAULT_FALLBACK_LANG = "en"


def load_config() -> Dict[str, Any]:
    if CONFIG_FILE.is_file():
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    else:
        cfg = {}

    cfg.setdefault("supported_langs", DEFAULT_SUPPORTED_LANGS)
    cfg.setdefault("fallback_lang", DEFAULT_FALLBACK_LANG)
    # flag per traduzione automatica (se un domani vuoi attivarla)
    cfg.setdefault("translation", False)
    return cfg


CONFIG = load_config()

# -------------------------------------------------------
# FASTAPI SETUP
# -------------------------------------------------------

app = FastAPI(title="Tecnaria Sinapsi — Q/A")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static (solo per index/html/css/js già presenti)
if (BASE_DIR / "static").is_dir():
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


class AskPayload(BaseModel):
    q: str
    family: Optional[str] = None  # es. "VCEM", "CTF", "CTL", "P560", ...


# -------------------------------------------------------
# UTILS
# -------------------------------------------------------

def normalize(text: str) -> str:
    if not text:
        return ""
    text = text.lower().strip()
    text = "".join(
        c for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    )
    return " ".join(text.split())


def guess_lang(q: str) -> str:
    q_norm = q.lower()
    # euristiche semplici ma sufficienti per routing lingua
    if any(w in q_norm for w in [" il ", " lo ", " la ", " dei ", "della ", "connettori", "soletta", "calcestruzzo"]):
        return "it"
    if any(w in q_norm for w in [" the ", " can i ", " steel ", "beam", "slab"]):
        return "en"
    if any(w in q_norm for w in [" le ", " les ", "avec ", "béton", "acier"]):
        return "fr"
    if any(w in q_norm for w in [" der ", "die ", "das ", "verbund", "beton", "stahl"]):
        return "de"
    if any(w in q_norm for w in [" el ", " los ", "las ", "hormigón", "acero"]):
        return "es"
    # default: italiano (contesto Tecnaria)
    return "it"


def resolve_family_key(raw: str) -> str:
    """Normalizza il nome famiglia in una chiave tipo 'VCEM', 'CTF', ecc."""
    if not raw:
        return ""
    key = raw.upper().strip()
    key = key.replace(" ", "").replace("/", "_")
    return key


def resolve_family_path(family_key: str) -> Optional[Path]:
    """Trova il file JSON relativo alla famiglia."""
    if not family_key:
        return None

    key = family_key.upper()

    # candidati diretti
    candidates = [
        DATA_DIR / f"{key}.json",
        DATA_DIR / f"{key}.golden.json",
        DATA_DIR / f"{key.lower()}.json",
        DATA_DIR / f"{key.lower()}.golden.json",
    ]
    for c in candidates:
        if c.is_file():
            return c

    # fallback: cerca per prefisso / contenuto nome
    for p in DATA_DIR.glob("*.json"):
        stem = p.stem.upper()
        if stem == key or key in stem:
            return p

    return None


def read_json(path: Path) -> Dict[str, Any]:
    if not path or not path.is_file():
        raise FileNotFoundError(f"File non trovato: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_family(family_key: str) -> Dict[str, Any]:
    """Carica (con cache) il JSON di una famiglia."""
    key = resolve_family_key(family_key)
    if not key:
        raise FileNotFoundError("Chiave famiglia vuota.")

    if key in FAMILY_CACHE:
        return FAMILY_CACHE[key]

    path = resolve_family_path(key)
    if not path:
        raise FileNotFoundError(f"Nessun JSON trovato per famiglia '{family_key}'.")

    data = read_json(path)
    FAMILY_CACHE[key] = data
    return data


def iter_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = data.get("items")
    if isinstance(items, list):
        return items
    # supporta anche formato piatto: lista top-level
    if isinstance(data, list):
        return data
    return []


def collect_patterns(item: Dict[str, Any]) -> List[str]:
    patterns: List[str] = []

    # vari possibili campi usati nei tuoi JSON
    for key in ["q", "qs", "questions", "patterns", "triggers"]:
        v = item.get(key)
        if isinstance(v, str):
            patterns.append(v)
        elif isinstance(v, list):
            patterns.extend(str(x) for x in v if x)

    # se non c'è nulla, usa testo risposta come ultimo fallback
    if not patterns:
        for key in ["prompt", "title"]:
            if key in item and isinstance(item[key], str):
                patterns.append(item[key])

    return [p for p in patterns if p.strip()]


def match_score(q_norm: str, patt_norm: str) -> float:
    if not q_norm or not patt_norm:
        return 0.0
    return difflib.SequenceMatcher(None, q_norm, patt_norm).ratio()


def extract_answers(item: Dict[str, Any]) -> Dict[str, str]:
    """
    Normalizza le risposte in un dict {lang: text}.
    Supporta vari layout (answer, answers, text, ecc.).
    """
    # già dict multilingua
    if isinstance(item.get("answers"), dict):
        return {k: str(v) for k, v in item["answers"].items()}

    # singola risposta con campo lingua
    if "answer" in item:
        ans = item["answer"]
        if isinstance(ans, dict):
            return {k: str(v) for k, v in ans.items()}
        lang = item.get("lang", "it")
        return {lang: str(ans)}

    if "text" in item:
        ans = item["text"]
        if isinstance(ans, dict):
            return {k: str(v) for k, v in ans.items()}
        lang = item.get("lang", "it")
        return {lang: str(ans)}

    if "a" in item:
        ans = item["a"]
        if isinstance(ans, dict):
            return {k: str(v) for k, v in ans.items()}
        lang = item.get("lang", "it")
        return {lang: str(ans)}

    # niente trovato → vuoto
    return {}


def choose_answer(item: Dict[str, Any], q_lang: str) -> Tuple[Optional[str], str]:
    """
    Sceglie la risposta nella lingua più adatta alla domanda.
    Se la lingua non è disponibile, usa fallback_lang o qualsiasi.
    """
    answers = extract_answers(item)
    if not answers:
        return None, q_lang

    # se c'è la lingua della domanda, usa quella
    if q_lang in answers:
        return answers[q_lang], q_lang

    # se c'è italiano e la domanda è it-like
    if q_lang == "it" and "it" in answers:
        return answers["it"], "it"

    # fallback globale da config
    fb = CONFIG.get("fallback_lang", DEFAULT_FALLBACK_LANG)
    if fb in answers:
        return answers[fb], fb

    # altrimenti prendi la prima disponibile
    lang, txt = next(iter(answers.items()))
    return txt, lang


def find_best_item(family_data: Dict[str, Any], q: str) -> Optional[Tuple[Dict[str, Any], float]]:
    """Trova il miglior item dentro una singola famiglia."""
    q_norm = normalize(q)
    best_item = None
    best_score = 0.0

    for item in iter_items(family_data):
        for patt in collect_patterns(item):
            s = match_score(q_norm, normalize(patt))
            if s > best_score:
                best_score = s
                best_item = item

    if best_item and best_score >= 0.35:
        return best_item, best_score

    return None


def find_best_item_any_family(exclude_family: str, q: str) -> Optional[Tuple[str, Dict[str, Any], float]]:
    """
    Cerca la risposta migliore in tutte le famiglie (tranne quella esclusa).
    Serve per auto-routing: es. domanda P560 mentre sei su VCEM.
    """
    q_norm = normalize(q)
    best_family = None
    best_item = None
    best_score = 0.0

    exclude_key = resolve_family_key(exclude_family)

    for path in DATA_DIR.glob("*.json"):
        stem_key = path.stem.upper()
        # salta config e roba non di dominio
        if "CONFIG" in stem_key:
            continue

        # evita la stessa famiglia
        if exclude_key and (stem_key == exclude_key or exclude_key in stem_key):
            continue

        # carica famiglia (con cache)
        if stem_key in FAMILY_CACHE:
            data = FAMILY_CACHE[stem_key]
        else:
            try:
                data = read_json(path)
                FAMILY_CACHE[stem_key] = data
            except Exception:
                continue

        for item in iter_items(data):
            for patt in collect_patterns(item):
                s = match_score(q_norm, normalize(patt))
                if s > best_score:
                    best_score = s
                    best_item = item
                    best_family = stem_key

    if best_item and best_score >= 0.40:
        return best_family, best_item, best_score

    return None


# -------------------------------------------------------
# ENDPOINTS
# -------------------------------------------------------

@app.get("/")
async def root():
    # Se c'è l'index nuovo, servilo; altrimenti info base JSON
    if INDEX_FILE.is_file():
        return StaticFiles(directory=str(BASE_DIR / "static")).lookup_path("index.html")[0]
    return {
        "app": "Tecnaria Sinapsi — Q/A",
        "status": "OK",
        "families_dir": str(DATA_DIR),
    }


@app.get("/api/config")
async def api_config():
    return {
        "ok": True,
        "app": "Tecnaria Sinapsi — Q/A",
        "families_dir": str(DATA_DIR),
        "supported_langs": CONFIG.get("supported_langs", DEFAULT_SUPPORTED_LANGS),
        "fallback_lang": CONFIG.get("fallback_lang", DEFAULT_FALLBACK_LANG),
        "translation": CONFIG.get("translation", False),
    }


@app.post("/api/ask")
async def api_ask(payload: AskPayload):
    q = (payload.q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Domanda vuota.")

    raw_family = payload.family or ""
    family = resolve_family_key(raw_family)
    lang = guess_lang(q)

    # 1) prova nella famiglia selezionata
    primary_item = None
    primary_score = 0.0

    if family:
        try:
            fam_data = load_family(family)
            found = find_best_item(fam_data, q)
            if found:
                primary_item, primary_score = found
        except FileNotFoundError:
            fam_data = None
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Errore famiglia {family}: {e}")

    if primary_item:
        answer, used_lang = choose_answer(primary_item, lang)
        if answer:
            return {
                "ok": True,
                "family": family,
                "q": q,
                "lang": used_lang,
                "id": primary_item.get("id"),
                "mode": primary_item.get("mode", "dynamic"),
                "score": round(float(primary_score), 3),
                "text": answer,
            }

    # 2) se non ha trovato in quella famiglia, cerca in tutte (auto-routing NLM)
    fallback = find_best_item_any_family(exclude_family=family, q=q)
    if fallback:
        fb_family, fb_item, fb_score = fallback
        answer, used_lang = choose_answer(fb_item, lang)
        if answer:
            return {
                "ok": True,
                "family": fb_family,
                "q": q,
                "lang": used_lang,
                "id": fb_item.get("id"),
                "mode": fb_item.get("mode", "dynamic"),
                "score": round(float(fb_score), 3),
                "text": answer,
            }

    # 3) nessun GOLD trovato da nessuna parte → messaggio pulito
    return {
        "ok": False,
        "family": family or None,
        "q": q,
        "lang": lang,
        "text": (
            "Per questa domanda non è ancora presente una risposta GOLD nei contenuti Tecnaria. "
            "Contatta l’ufficio tecnico o arricchisci il JSON della famiglia pertinente."
        ),
    }
