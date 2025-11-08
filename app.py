import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Iterable, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# =========================
# PATH & CONFIG
# =========================

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = STATIC_DIR / "data"
INDEX_FILE = STATIC_DIR / "index.html"
CONFIG_PATH = DATA_DIR / "config.runtime.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "app": "Tecnaria Sinapsi — Q/A",
    "families_dir": str(DATA_DIR),
    "supported_langs": ["it", "en", "fr", "de", "es"],
    "fallback_lang": "en",
    "translation": False,  # nessuna chiamata esterna qui
}

if CONFIG_PATH.exists():
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            FILE_CONF = json.load(f)
    except Exception:
        FILE_CONF = {}
else:
    FILE_CONF = {}

CONFIG: Dict[str, Any] = {**DEFAULT_CONFIG, **FILE_CONF}

# =========================
# APP
# =========================

app = FastAPI(title="Tecnaria Sinapsi — Q/A", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# =========================
# MODELS
# =========================

class AskPayload(BaseModel):
    q: str
    family: Optional[str] = None  # opzionale, usata solo come BOOST se presente


# =========================
# CACHE
# =========================

FAMILY_CACHE: Dict[str, Dict[str, Any]] = {}

# =========================
# UTILS
# =========================

_nonword_re = re.compile(r"[^\w]+", re.UNICODE)


def normalize(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = (
        text.replace("à", "a")
        .replace("è", "e")
        .replace("é", "e")
        .replace("ì", "i")
        .replace("ò", "o")
        .replace("ù", "u")
    )
    text = _nonword_re.sub(" ", text)
    return " ".join(text.split())


def guess_lang(text: str) -> str:
    t = text.lower()

    # euristiche semplici, sufficienti per routing lingua
    if any(x in t for x in [" il ", " lo ", " la ", " dei ", " delle ", " connettori ", " soletta ", " calcestruzzo "]):
        return "it"
    if any(x in t for x in [" the ", " can i ", " beam", " slab", " steel "]):
        return "en"
    if any(x in t for x in [" le ", " des ", " béton", " acier", "avec "]):
        return "fr"
    if any(x in t for x in [" der ", " die ", " das ", "verbund", "stahl", "beton"]):
        return "de"
    if any(x in t for x in [" el ", " los ", " las ", " hormigón", " acero "]):
        return "es"

    # default: italiano nel contesto Tecnaria
    return "it"


def resolve_family_key(fam: Optional[str]) -> Optional[str]:
    if not fam:
        return None
    fam = fam.strip().upper()
    if not fam:
        return None

    aliases = {
        "VCEM": "VCEM",
        "CTF": "CTF",
        "CTL": "CTL",
        "CTL_MAXI": "CTL_MAXI",
        "CTCEM": "CTCEM",
        "DIAPASON": "DIAPASON",
        "P560": "P560",
        "COMM": "COMM",
    }
    return aliases.get(fam, fam)


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def iter_items(data: Any) -> Iterable[Dict[str, Any]]:
    """
    Supporta varie strutture:
      - {"items":[...]}
      - {"blocks":[...]}
      - lista diretta [...]
      - fallback: dict nidificati
    """
    if data is None:
        return

    if isinstance(data, list):
        for it in data:
            if isinstance(it, dict):
                yield it
        return

    if isinstance(data, dict):
        if "items" in data and isinstance(data["items"], list):
            for it in data["items"]:
                if isinstance(it, dict):
                    yield it
            return
        if "blocks" in data and isinstance(data["blocks"], list):
            for it in data["blocks"]:
                if isinstance(it, dict):
                    yield it
            return
        # fallback: tutti i valori che sono dict
        for v in data.values():
            if isinstance(v, dict):
                yield v


def collect_patterns(item: Dict[str, Any]) -> List[str]:
    """
    Raccoglie tutte le possibili forme di domanda / trigger
    da un item, indipendentemente da come è scritto il JSON.
    """
    out: List[str] = []

    scalar_keys = [
        "q",
        "question",
        "domanda",
        "title",
        "label",
    ]
    list_keys = [
        "q_list",
        "questions",
        "patterns",
        "triggers",
        "variants",
        "synonyms",
    ]

    for k in scalar_keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())

    for k in list_keys:
        v = item.get(k)
        if isinstance(v, list):
            for e in v:
                if isinstance(e, str) and e.strip():
                    out.append(e.strip())

    # fallback: usa pancia della risposta se non c'è nient'altro
    if not out:
        txt = extract_any_answer_text(item)
        if txt:
            out.append(txt[:120])

    return out


def extract_any_answer_text(item: Dict[str, Any]) -> str:
    """
    Estrae un testo risposta da varie strutture:
    - item["answer"] (str o dict)
    - item["answers"][lang]
    - item["text"], item["it"], item["en"], ecc.
    """
    ans = item.get("answer")
    if isinstance(ans, str) and ans.strip():
        return ans.strip()
    if isinstance(ans, dict):
        # preferisci IT, poi EN, poi qualsiasi
        for k in ["it", "IT", "ita", "en", "EN"]:
            v = ans.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for v in ans.values():
            if isinstance(v, str) and v.strip():
                return v.strip()

    answers = item.get("answers")
    if isinstance(answers, dict):
        for k in ["it", "en"]:
            v = answers.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for v in answers.values():
            if isinstance(v, str) and v.strip():
                return v.strip()

    for key in ["text", "it", "en", "fr", "de", "es"]:
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()

    return ""


def choose_answer(item: Dict[str, Any], lang: str) -> Tuple[Optional[str], str]:
    """
    Sceglie la risposta nella lingua della domanda se possibile,
    altrimenti fallback in cascata (config), altrimenti qualunque testo valido.
    """
    supported = CONFIG["supported_langs"]
    fallback = CONFIG["fallback_lang"]

    ans = item.get("answer")

    # answer = stringa
    if isinstance(ans, str) and ans.strip():
        return ans.strip(), lang

    # answer = dict per lingua
    if isinstance(ans, dict):
        if lang in ans and isinstance(ans[lang], str) and ans[lang].strip():
            return ans[lang].strip(), lang
        if fallback in ans and isinstance(ans[fallback], str) and ans[fallback].strip():
            return ans[fallback].strip(), fallback
        for lg in supported:
            if lg in ans and isinstance(ans[lg], str) and ans[lg].strip():
                return ans[lg].strip(), lg
        for v in ans.values():
            if isinstance(v, str) and v.strip():
                return v.strip(), lang

    # answers = dict
    answers = item.get("answers")
    if isinstance(answers, dict):
        if lang in answers and isinstance(answers[lang], str) and answers[lang].strip():
            return answers[lang].strip(), lang
        if fallback in answers and isinstance(answers[fallback], str) and answers[fallback].strip():
            return answers[fallback].strip(), fallback
        for lg in supported:
            if lg in answers and isinstance(answers[lg], str) and answers[lg].strip():
                return answers[lg].strip(), lg
        for v in answers.values():
            if isinstance(v, str) and v.strip():
                return v.strip(), lang

    # chiavi dirette o text
    for key in [lang, fallback, "it", "en", "fr", "de", "es", "text"]:
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip(), (key if key in supported else lang)

    return None, lang


def match_score(q_norm: str, patt_norm: str) -> float:
    """
    Score semplice basato su overlap token.
    Niente cosine, niente magia fragile.
    """
    if not q_norm or not patt_norm:
        return 0.0

    q_tokens = q_norm.split()
    p_tokens = patt_norm.split()
    if not q_tokens or not p_tokens:
        return 0.0

    q_set = set(q_tokens)
    p_set = set(p_tokens)

    inter = len(q_set & p_set)
    if inter == 0:
        return 0.0

    overlap_q = inter / len(q_set)
    overlap_p = inter / len(p_set)

    score = 0.7 * overlap_q + 0.3 * overlap_p
    if score < 0:
        score = 0.0
    if score > 1:
        score = 1.0
    return float(score)


# =========================
# ROOT & CONFIG
# =========================

@app.get("/")
def root():
    if INDEX_FILE.exists():
        return FileResponse(str(INDEX_FILE))
    return {
        "app": CONFIG["app"],
        "status": "OK",
        "families_dir": CONFIG["families_dir"],
    }


@app.get("/api/config")
def api_config():
    return {
        "ok": True,
        "app": CONFIG["app"],
        "families_dir": CONFIG["families_dir"],
        "supported_langs": CONFIG["supported_langs"],
        "fallback_lang": CONFIG["fallback_lang"],
        "translation": CONFIG.get("translation", False),
        "status": "OK",
    }


# =========================
# CORE: /api/ask
# =========================

@app.post("/api/ask")
async def api_ask(payload: AskPayload):
    q = (payload.q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Domanda vuota.")

    # family è opzionale: se c'è è solo un BOOST, non un filtro
    selected_family = resolve_family_key(payload.family)
    lang = guess_lang(q)
    q_norm = normalize(q)

    best_item: Optional[Dict[str, Any]] = None
    best_family: Optional[str] = None
    best_score: float = 0.0

    # Scansiona TUTTI i JSON in /static/data
    for path in DATA_DIR.glob("*.json"):
        stem = path.stem.upper()
        # salta config o file non di dominio se ne hai
        if "CONFIG" in stem:
            continue

        # cache
        if stem in FAMILY_CACHE:
            data = FAMILY_CACHE[stem]
        else:
            try:
                data = read_json(path)
                FAMILY_CACHE[stem] = data
            except Exception:
                continue

        for item in iter_items(data):
            patterns = collect_patterns(item)
            if not patterns:
                continue

            for patt in patterns:
                s = match_score(q_norm, normalize(patt))
                if s <= 0.0:
                    continue

                # piccolo bonus se coincide con famiglia suggerita
                if selected_family and (
                    stem == selected_family or selected_family in stem
                ):
                    s += 0.03

                if s > best_score:
                    best_score = s
                    best_item = item
                    best_family = stem

    # Soglia unica globale
    if not best_item or best_score < 0.35:
        return {
            "ok": False,
            "family": selected_family or None,
            "q": q,
            "lang": lang,
            "text": (
                "Per questa domanda non è ancora presente una risposta GOLD nei contenuti Tecnaria. "
                "Se è una casistica reale di cantiere, aggiungere il blocco corrispondente nel JSON."
            ),
        }

    answer, used_lang = choose_answer(best_item, lang)
    if not answer:
        return {
            "ok": False,
            "family": best_family,
            "q": q,
            "lang": lang,
            "text": (
                "È stato individuato un blocco logico ma senza testo risposta valido. "
                f"Verifica il file JSON della famiglia {best_family} (ID: {best_item.get('id')})."
            ),
        }

    return {
        "ok": True,
        "family": best_family,
        "q": q,
        "lang": used_lang,
        "id": best_item.get("id"),
        "mode": best_item.get("mode", "dynamic"),
        "score": round(float(best_score), 3),
        "text": answer,
    }
