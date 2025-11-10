import os
import json
import re
import unicodedata
import random
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# OpenAI client (v2 style: from openai import OpenAI)
try:
    from openai import OpenAI  # type: ignore
except Exception:  # se manca la lib, il backend funziona lo stesso senza traduzioni
    OpenAI = None  # type: ignore

# ---------------------------------------------------------
# Percorsi base
# ---------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "static", "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.runtime.json")

# ---------------------------------------------------------
# FastAPI
# ---------------------------------------------------------

app = FastAPI(title="Tecnaria Sinapsi Backend", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------
# Stato globale / cache
# ---------------------------------------------------------

_family_cache: Dict[str, Dict[str, Any]] = {}
_config_cache: Optional[Dict[str, Any]] = None

# Modalità di risposta:
# - GOLD  => usa varianti narrative / dynamic
# - CANONICAL => usa canonical / risposte secche
MODE_GOLD = "gold"
MODE_CANONICAL = "canonical"
_current_mode: str = MODE_GOLD  # default: GOLD sempre

# OpenAI (opzionale per traduzioni)
USE_OPENAI = bool(os.getenv("OPENAI_API_KEY")) and OpenAI is not None
openai_client: Optional[OpenAI] = None
if USE_OPENAI:
    try:
        openai_client = OpenAI()
    except Exception:
        openai_client = None
        USE_OPENAI = False

# ---------------------------------------------------------
# Modelli Pydantic
# ---------------------------------------------------------

class AskRequest(BaseModel):
    q: str
    family: Optional[str] = None  # opzionale, la UI spesso lo manda già


# ---------------------------------------------------------
# Utilità base
# ---------------------------------------------------------

def normalize(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def safe_family_filename(family: str) -> str:
    # I file reali sono tipo CTF.json, VCEM.json, ecc.
    base = family.upper()
    return f"{base}.json"


def load_config() -> Dict[str, Any]:
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    # default sicuro
    cfg: Dict[str, Any] = {
        "admin": {
            "response_policy": {
                "mode": "dynamic",
                "variant_selection": "longest",
                "variant_seed": 20251106,
            }
        }
    }

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            file_cfg = json.load(f)
        if isinstance(file_cfg, dict):
            admin = file_cfg.get("admin") or {}
            rp = admin.get("response_policy") or {}
            cfg["admin"]["response_policy"].update(rp)
    except FileNotFoundError:
        pass
    except Exception:
        # se il file è rotto, andiamo con i default
        pass

    _config_cache = cfg
    return cfg


def get_response_policy() -> Dict[str, Any]:
    cfg = load_config()
    return (cfg.get("admin") or {}).get("response_policy") or {}


def list_families() -> List[str]:
    if not os.path.isdir(DATA_DIR):
        return []
    fams: List[str] = []
    for fname in os.listdir(DATA_DIR):
        if not fname.lower().endswith(".json"):
            continue
        base = fname.rsplit(".", 1)[0]
        # escludi file di config
        if base.lower().startswith("config.runtime"):
            continue
        fams.append(base.upper())
    return sorted(set(fams))


def load_family(family: str) -> Dict[str, Any]:
    family = family.upper()
    if family in _family_cache:
        return _family_cache[family]

    path = os.path.join(DATA_DIR, safe_family_filename(family))
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Family '{family}' not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading family '{family}': {e}")

    # normalizza struttura
    if isinstance(data, list):
        items = data
        data = {"family": family, "items": items}
    else:
        items = data.get("items") or []
        data["items"] = items

    for idx, item in enumerate(items):
        if "id" not in item:
            item["id"] = f"{family}-{idx+1:04d}"

    _family_cache[family] = data
    return data


# ---------------------------------------------------------
# Riconoscimento famiglia dalla domanda (instradamento automatico)
# ---------------------------------------------------------

FAMILY_SYNONYMS: Dict[str, List[str]] = {
    "CTF": ["ctf"],
    "CTL": ["ctl", "legno"],
    "CTL_MAXI": ["ctl maxi", "maxi"],
    "VCEM": ["vcem"],
    "CTCEM": ["ctcem"],
    "P560": ["p560", "spit p560"],
    "DIAPASON": ["diapason"],
}

def guess_families_from_text(text: str) -> List[str]:
    t = text.lower()
    found: List[str] = []
    for fam, keys in FAMILY_SYNONYMS.items():
        for k in keys:
            if k in t:
                found.append(fam)
                break
    return found


# ---------------------------------------------------------
# Language detection & translation (semplice ma efficace)
# ---------------------------------------------------------

SUPPORTED_LANGS = ["it", "en", "fr", "de", "es"]

def detect_lang(text: str) -> str:
    """
    Heuristics leggere: basta per capire se rispondere in it/en/fr/de/es.
    Se dubbio → it.
    """
    t = text.strip()
    if not t:
        return "it"
    low = t.lower()

    # inglese
    if re.search(r"\b(what|which|can i|how|where|why)\b", low):
        return "en"

    # spagnolo
    if re.search(r"\b(qué|dónde|cómo|cuándo|por qué)\b", low):
        return "es"

    # francese
    if re.search(r"\b(quel|quelle|quels|quelles|comment|pourquoi|où)\b", low):
        return "fr"

    # tedesco
    if re.search(r"\b(was|wie|warum|wo|welche|welcher|welches)\b", low):
        return "de"

    # italiano (accenti tipici)
    if re.search(r"[àèéìòù]", low):
        return "it"

    # fallback
    return "it"


def openai_translate(text: str, target_lang: str, source_lang: Optional[str] = None) -> str:
    """
    Traduzione tecnica con OpenAI (se disponibile).
    Se qualcosa va storto → restituisce il testo originale.
    """
    if not text or not USE_OPENAI or not openai_client:
        return text

    target_lang = target_lang.lower()
    if target_lang not in SUPPORTED_LANGS:
        return text

    try:
        completion = openai_client.chat.completions.create(
            model=os.getenv("OPENAI_TRANSLATION_MODEL", "gpt-4.1-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Sei un traduttore tecnico. Traduci il testo nella lingua '{target_lang}'. "
                        "Mantieni invariati marchi, sigle e nomi dei prodotti Tecnaria. "
                        "Rispondi SOLO con il testo tradotto."
                    ),
                },
                {
                    "role": "user",
                    "content": text,
                },
            ],
            max_tokens=800,
        )
        out = completion.choices[0].message.content or ""
        out = out.strip()
        return out or text
    except Exception:
        return text


# ---------------------------------------------------------
# Selezione variante GOLD vs CANONICO
# ---------------------------------------------------------

def pick_best_variant(variants: Any) -> str:
    """
    variants può essere:
    - lista di stringhe
    - dict {chiave: stringa o lista}
    Restituisce la variante più lunga (stile GOLD) o random se da policy.
    """
    texts: List[str] = []

    if isinstance(variants, list):
        texts = [v.strip() for v in variants if isinstance(v, str) and v.strip()]
    elif isinstance(variants, dict):
        for v in variants.values():
            if isinstance(v, str):
                v = v.strip()
                if v:
                    texts.append(v)
            elif isinstance(v, list):
                for s in v:
                    if isinstance(s, str):
                        s = s.strip()
                        if s:
                            texts.append(s)

    if not texts:
        return ""

    policy = get_response_policy()
    sel = (policy.get("variant_selection") or "longest").lower()
    seed = policy.get("variant_seed")

    if sel == "random":
        rnd = random.Random(seed or None)
        return rnd.choice(texts)

    # default: longest = più ricca, stile GOLD
    return max(texts, key=len)


def extract_answer(block: Dict[str, Any], lang: str, mode: str) -> str:
    """
    mode:
      - MODE_GOLD      => usa response_variants (GOLD), fallback canonical
      - MODE_CANONICAL => usa canonical/answer_xx, fallback varianti
    """
    lang = (lang or "it").lower()

    variants = block.get("response_variants")
    canonical = block.get("canonical") or block.get(f"canonical_{lang}")
    answer_lang = block.get(f"answer_{lang}")
    answer_it = block.get("answer_it")

    # CANONICO: priorità a canonical / answer_lang
    if mode == MODE_CANONICAL:
        if answer_lang:
            base = answer_lang
        elif canonical:
            base = canonical
        elif answer_it:
            base = answer_it
        else:
            base = pick_best_variant(variants)
        return (base or "").strip()

    # GOLD (dynamic): priorità varianti narrative
    base = ""
    if variants:
        base = pick_best_variant(variants)

    if not base:
        if answer_lang:
            base = answer_lang
        elif answer_it:
            base = answer_it
        elif canonical:
            base = canonical

    return (base or "").strip()


# ---------------------------------------------------------
# Matching domanda → item JSON
# ---------------------------------------------------------

def collect_item_text(item: Dict[str, Any]) -> str:
    parts: List[str] = []

    for key in ("questions", "q", "question", "paraphrases", "tags"):
        v = item.get(key)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            parts.extend([s for s in v if isinstance(s, str)])

    # includo anche canonical e varianti per allargare il match
    for key in ("canonical", "answer_it"):
        v = item.get(key)
        if isinstance(v, str):
            parts.append(v)

    return " ".join(parts)


def score_item(q_norm: str, item: Dict[str, Any]) -> float:
    if not q_norm:
        return 0.0
    hay = normalize(collect_item_text(item))
    if not hay:
        return 0.0
    q_terms = set(q_norm.split())
    if not q_terms:
        return 0.0
    hay_terms = set(hay.split())
    overlap = q_terms & hay_terms
    if not overlap:
        return 0.0
    # piccolo boost se troviamo molte parole in comune
    return len(overlap) / len(q_terms)


def find_best_block(query_it: str, families: Optional[List[str]] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str], float]:
    q_norm = normalize(query_it)
    if not q_norm:
        return (None, None, 0.0)

    if not families:
        families = list_families()

    best_item: Optional[Dict[str, Any]] = None
    best_family: Optional[str] = None
    best_score: float = 0.0

    for fam in families:
        try:
            data = load_family(fam)
        except HTTPException:
            continue
        items = data.get("items") or []
        for item in items:
            s = score_item(q_norm, item)
            if s > best_score:
                best_score = s
                best_item = item
                best_family = fam

    # soglia minima per evitare abbinamenti stupidi
    if best_score < 0.15:
        return (None, None, 0.0)

    return best_item, best_family, best_score


# ---------------------------------------------------------
# API
# ---------------------------------------------------------

@app.get("/")
async def root():
    return {
        "ok": True,
        "message": "TECNARIA Sinapsi backend attivo",
        "mode": _current_mode,
        "families": list_families(),
    }


@app.get("/api/config")
async def api_config():
    rp = get_response_policy()
    return {
        "ok": True,
        "current_mode": _current_mode,
        "admin_response_policy": rp,
        "translation_enabled": bool(USE_OPENAI and openai_client),
        "families": list_families(),
    }


@app.post("/api/ask")
async def api_ask(payload: AskRequest):
    global _current_mode

    raw_q = (payload.q or "").strip()
    if not raw_q:
        raise HTTPException(status_code=400, detail="Missing 'q' in request body")

    # -------------------------------------------------
    # Gestione comandi GOLD: / CANONICO: persistenti
    # -------------------------------------------------
    txt = raw_q.lstrip()
    low = txt.lower()

    if low.startswith("gold:"):
        _current_mode = MODE_GOLD
        # rimuovi il prefisso dalla domanda
        txt = txt[5:].strip()
    elif low.startswith("canonico:") or low.startswith("canonical:"):
        _current_mode = MODE_CANONICAL
        idx = low.find(":")
        txt = txt[idx + 1 :].strip()

    if not txt:
        # se l'utente ha scritto solo GOLD: o CANONICO:
        return {
            "ok": True,
            "message": f"Modalità aggiornata a '{_current_mode}'. Inserisci la domanda successiva.",
            "mode": _current_mode,
        }

    mode = _current_mode

    # -------------------------------------------------
    # Lingua della domanda
    # -------------------------------------------------
    user_lang = detect_lang(txt)

    # Testo per il matching: lavoriamo in italiano.
    query_for_match = txt
    if user_lang != "it":
        # se hai la key, traduciamo in it per agganciare i JSON
        query_for_match = openai_translate(txt, "it", source_lang=user_lang)

    # -------------------------------------------------
    # Famiglie candidate
    # -------------------------------------------------
    families: Optional[List[str]] = None

    if payload.family:
        families = [payload.family.upper()]
    else:
        guessed = guess_families_from_text(txt)
        if guessed:
            families = guessed
        else:
            families = None  # tutte

    # -------------------------------------------------
    # Matching
    # -------------------------------------------------
    item, fam, score = find_best_block(query_for_match, families)

    if not item or not fam:
        return {
            "ok": False,
            "message": "Blocco trovato ma senza risposta valida.",
            "lang": user_lang,
            "mode": mode,
        }

    # -------------------------------------------------
    # Estrazione risposta (sempre IT come base)
    # -------------------------------------------------
    base_it = extract_answer(item, "it", mode)
    if not base_it:
        return {
            "ok": False,
            "message": "Blocco trovato ma senza risposta valida.",
            "family": fam,
            "id": item.get("id"),
            "lang": user_lang,
            "mode": mode,
        }

    # -------------------------------------------------
    # Traduzione finale nella lingua dell'utente
    # -------------------------------------------------
    if user_lang == "it":
        final_text = base_it
    else:
        final_text = openai_translate(base_it, user_lang, source_lang="it")

    return {
        "ok": True,
        "family": fam,
        "id": item.get("id"),
        "score": round(score, 4),
        "lang": user_lang,
        "mode": mode,
        "text": final_text,
    }
