import os
import json
import random
import re
from functools import lru_cache
from typing import List, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# =========================
# CONFIGURAZIONE DI BASE
# =========================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FAMILIES_DIR = os.path.join(BASE_DIR, "static", "data")

CONFIG_PATH = os.path.join(DEFAULT_FAMILIES_DIR, "config.runtime.json")

app = FastAPI(title="Tecnaria Sinapsi — Q/A")

# CORS aperto per interfaccia web
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# MODELLI
# =========================

class AskPayload(BaseModel):
    q: str
    family: Optional[str] = None   # es. "VCEM", "CTF", etc.
    vs: Optional[str] = None       # "canonical" | "dynamic" | None


# =========================
# UTILITY JSON
# =========================

def _read_json(path: str) -> Dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"File non trovato: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=64)
def get_config() -> Dict:
    if os.path.exists(CONFIG_PATH):
        try:
            return _read_json(CONFIG_PATH)
        except Exception:
            return {}
    return {}


def get_families_dir() -> str:
    cfg = get_config()
    d = cfg.get("families_dir") or DEFAULT_FAMILIES_DIR
    return d


# =========================
# SINONIMI + NORMALIZZAZIONE
# =========================

# Dizionario minimo di sinonimi tecnici / linguaggio naturale.
# Espandibile senza toccare il codice.
SYNONYMS = {
    # azioni generiche
    "usare": ["utilizzare", "impiegare", "adoperare"],
    "posare": ["installare", "montare", "fissare", "mettere"],
    "fissare": ["ancorare", "avvitare"],
    "sparare": ["chiodare", "pistola", "sparachiodi"],
    "errore": ["sbaglio", "sbagliato", "errato"],
    # oggetti
    "vcem": ["vcem"],
    "ctf": ["ctf"],
    "ctl": ["ctl"],
    "ctcem": ["ctcem"],
    "p560": ["p560"],
    "diapason": ["diapason"],
    # concetti
    "dove": ["dove", "in quali casi", "in che casi"],
    "certificazione": ["certificazioni", "eta", "ce", "marcatura"],
    "prova": ["prove", "test", "sperimentale"],
    "soletta": ["soletta", "getto", "cls", "calcestruzzo"],
    "acciaio": ["trave", "profilato", "heb", "hea", "lamaiera", "lamiera"],
    "laterocemento": ["laterocemento", "travetto"],
}

# parole che non aiutano nel matching
STOPWORDS = {
    "il", "lo", "la", "i", "gli", "le",
    "un", "una", "uno",
    "di", "a", "da", "in", "con", "su", "per",
    "che", "cosa", "come", "quando", "dove", "quale", "quali",
    "posso", "puoi", "devo", "si", "no",
    "tecnaria",
    "connettori", "connettore"
}


def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\wàèéìòùç]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def expand_synonyms(tokens: List[str]) -> List[str]:
    """Sostituisce/aggiunge sinonimi base per robustezza."""
    expanded = []
    for t in tokens:
        expanded.append(t)
        for base, syns in SYNONYMS.items():
            if t == base or t in syns:
                expanded.append(base)
    return expanded


def tokenize(text: str) -> List[str]:
    norm = normalize_text(text)
    tokens = [t for t in norm.split(" ") if t and t not in STOPWORDS]
    return expand_synonyms(tokens)


def jaccard_score(a: List[str], b: List[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    if inter == 0:
        return 0.0
    union = len(sa | sb)
    return inter / union


def tag_bonus(q_norm_tokens: List[str], item: Dict) -> float:
    bonus = 0.0
    tags = item.get("tags") or []
    if not tags:
        return 0.0
    qset = set(q_norm_tokens)
    for tag in tags:
        tnorm = normalize_text(tag)
        if tnorm and tnorm in qset:
            bonus += 0.04  # piccolo boost per ogni tag centrato
    # massimo bonus limitato
    return min(bonus, 0.15)


# =========================
# CARICAMENTO FAMIGLIE
# =========================

@lru_cache(maxsize=64)
def load_family(family: str) -> Dict:
    family = (family or "").strip()
    if not family:
        raise HTTPException(status_code=400, detail="Parametro 'family' mancante.")

    families_dir = get_families_dir()

    # Strategia: cerco prima Family.json, poi *.golden.json se esiste
    candidates = [
        os.path.join(families_dir, f"{family}.json"),
        os.path.join(families_dir, f"{family}.golden.json"),
        os.path.join(families_dir, f"{family}.sinapsi.gold.json"),
    ]

    for path in candidates:
        if os.path.exists(path):
            data = _read_json(path)
            # normalizza struttura minima
            if "items" not in data:
                raise HTTPException(
                    status_code=500,
                    detail=f"Formato JSON famiglia '{family}' non valido (manca 'items')."
                )
            return data

    raise HTTPException(
        status_code=404,
        detail=f"Famiglia '{family}' non trovata in {families_dir}."
    )


# =========================
# MOTORE DI MATCHING NLM-LITE
# =========================

def match_item(user_q: str, family_data: Dict) -> Optional[Dict]:
    """
    Cerca l'item migliore nella famiglia usando:
    - tokenizzazione + sinonimi
    - jaccard tra domanda utente e domande/descrizioni note
    - bonus dai tag
    Restituisce item con 'best_mode' e 'best_variant_index'
    """
    q_tokens = tokenize(user_q)
    if not q_tokens:
        return None

    best = None
    best_score = 0.0

    items = family_data.get("items", [])

    for item in items:
        # costruiamo lista di testi da confrontare: domande note + canonical
        texts = []
        texts.extend(item.get("questions") or [])
        canonical = item.get("canonical")
        if canonical:
            texts.append(canonical)

        item_best_local = 0.0

        for txt in texts:
            cand_tokens = tokenize(txt)
            base = jaccard_score(q_tokens, cand_tokens)
            if base == 0:
                continue
            bonus = tag_bonus(q_tokens, item)
            score = base + bonus
            if score > item_best_local:
                item_best_local = score

        if item_best_local > best_score:
            best_score = item_best_local
            best = item

    # soglie:
    # > 0.32 = match sicuro
    # 0.22–0.32 = match soft, accettato se abbiamo almeno domande/tag coerenti
    if best and best_score >= 0.32:
        return best

    if best and best_score >= 0.22:
        # match morbido accettato per evitare buchi su domande naturali
        return best

    return None


def choose_answer(item: Dict, vs: Optional[str]) -> (str, str, int):
    """
    Restituisce (testo, mode, variant_index).
    mode: 'canonical' o 'dynamic'
    variant_index: indice nella lista (per debug UI), -1 se canonical puro.
    """
    variants = item.get("response_variants") or []
    canonical = item.get("canonical") or ""

    # Se l'utente chiede esplicitamente canonical
    if vs == "canonical":
        if not canonical:
            # fallback: prima variant
            if variants:
                return variants[0], "canonical", 0
            return "", "canonical", -1
        return canonical, "canonical", -1

    # Modalità dinamica / default
    if vs == "dynamic" or vs is None:
        pool = []
        # diamo più peso al canonical ma includiamo varianti
        if canonical:
            pool.append(("canonical", canonical))
        for i, v in enumerate(variants):
            pool.append((f"v{i}", v))

        if not pool:
            return "", "dynamic", -1

        label, text = random.choice(pool)
        if label == "canonical":
            return text, "dynamic", -1
        else:
            idx = int(label[1:])
            return text, "dynamic", idx

    # fallback strano
    if canonical:
        return canonical, "canonical", -1
    if variants:
        return variants[0], "canonical", 0
    return "", "canonical", -1


# =========================
# ENDPOINTS
# =========================

@app.get("/")
def root():
    return {
        "app": "Tecnaria Sinapsi — Q/A",
        "status": "OK",
        "families_dir": get_families_dir()
    }


@app.get("/api/config")
def api_config():
    return {
        "ok": True,
        "app": "Tecnaria Sinapsi — Q/A",
        "families_dir": get_families_dir()
    }


@app.post("/api/ask")
def api_ask(payload: AskPayload):
    q = (payload.q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Domanda vuota.")

    if not payload.family:
        raise HTTPException(status_code=400, detail="Parametro 'family' obbligatorio.")

    family_name = payload.family.strip().upper()

    try:
        family_data = load_family(family_name)
    except HTTPException as e:
        # Errore già formattato
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    item = match_item(q, family_data)

    if not item:
        # Nessun match convincente: risposta controllata
        return {
            "ok": False,
            "family": family_name,
            "q": q,
            "text": "Nessuna risposta trovata per questa domanda."
        }

    text, mode, vidx = choose_answer(item, payload.vs)

    if not text:
        return {
            "ok": False,
            "family": family_name,
            "q": q,
            "text": "Nessuna risposta trovata per questa domanda."
        }

    return {
        "ok": True,
        "family": family_name,
        "id": item.get("id"),
        "mode": mode,
        "variant_index": vidx,
        "text": text.strip()
    }
