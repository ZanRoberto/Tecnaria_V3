import os
import json
import random
from pathlib import Path
from typing import Dict, Any, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import HTMLResponse, FileResponse
from starlette.staticfiles import StaticFiles
from difflib import SequenceMatcher

# ============================================================
# CONFIGURAZIONE BASE
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = STATIC_DIR / "data"

app = FastAPI(title="Tecnaria Sinapsi — Q/A")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Cache famiglie in memoria
_FAMILY_CACHE: Dict[str, Dict[str, Any]] = {}

# ============================================================
# UTILITA' LETTURA JSON
# ============================================================

def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"File non trovato: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_family(name: str) -> Dict[str, Any]:
    """
    Carica una famiglia (VCEM, CTF, CTL, ecc.) da static/data/<NAME>.json
    e la tiene in cache.
    """
    if name in _FAMILY_CACHE:
        return _FAMILY_CACHE[name]

    path = DATA_DIR / f"{name}.json"
    data = _read_json(path)

    # normalizza struttura minima
    if data.get("family") != name:
        data["family"] = name

    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError(f"'items' mancante o non lista in {path}")

    data["items"] = items
    _FAMILY_CACHE[name] = data
    return data

# ============================================================
# NLM LITE: NORMALIZZAZIONE & MATCHING
# ============================================================

# parole vuote che non aiutano il match
STOPWORDS = {
    "i", "il", "lo", "la", "l", "gli", "le",
    "un", "uno", "una",
    "di", "dei", "degli", "del", "della", "delle",
    "per", "con", "su", "nel", "nello", "nella", "nelle", "nei",
    "da", "al", "ai", "agli", "alle",
    "e", "ed", "o",
    "che", "come", "quale", "quali",
    "posso", "può", "puoi", "si", "sono", "sia", "siano",
    "devo", "serve", "mi", "ti", "ci", "vi",
    "riguardo", "riferimento", "parli", "parlami",
    "uso", "usare", "usati", "usato",
    "quando", "dove",
    "ctf", "vсem", "ctl", "ctcem", "p560", "tecnaria"  # brand/famiglie li gestiamo via tag
}

# booster semantici: frasi naturali → concetti
PHRASE_MAP = [
    ("dove posso usare", " applicazioni utilizzo campo-uso "),
    ("dove si usano", " applicazioni utilizzo campo-uso "),
    ("per che tipo di solaio", " applicazioni solaio campo-uso "),
    ("per quali solai", " applicazioni solaio campo-uso "),
    ("quando scelgo", " scelta campo-uso applicazioni "),
    ("quando usare", " scelta campo-uso applicazioni "),
    ("a cosa servono", " funzione scopo utilizzo "),
    ("a cosa serve", " funzione scopo utilizzo "),
    ("mi parli dei", " cosa-sono descrizione "),
    ("mi parli del", " cosa-sono descrizione "),
    ("cosa sono", " cosa-sono definizione "),
    ("che cosa sono", " cosa-sono definizione "),
    ("come funzionano", " funzionamento principio "),
    ("come funziona", " funzionamento principio "),
    ("come vengono fissati", " come si fissano fissaggio posa "),
    ("come vengono montati", " come si fissano fissaggio posa "),
    ("come vengono installati", " come si fissano fissaggio posa "),
    ("posa dei", " fissaggio posa installazione "),
    ("modo di fissaggio", " fissaggio posa installazione "),
]

# sinonimi passivo/attivo per verbi tipici
VERB_EQ = {
    "vengono fissati": "si fissano",
    "viene fissato": "si fissa",
    "vengono montati": "si fissano",
    "viene montato": "si fissa",
    "vengono installati": "si fissano",
    "viene installato": "si fissa",
    "vengono posati": "si posano",
    "viene posato": "si posa",
    "vengono usati": "si usano",
    "viene usato": "si usa",
    "vengono impiegati": "si usano",
    "viene impiegato": "si usa",
    "vengono applicati": "si usano",
    "viene applicato": "si usa"
}


def _norm(text: str) -> str:
    """Normalizza testo per il matching semantico leggero."""
    s = str(text).lower()

    # attivo/passivo → forma base
    for old, new in VERB_EQ.items():
        if old in s:
            s = s.replace(old, new)

    # frasi → concetti (booster)
    for old, new in PHRASE_MAP:
        if old in s:
            s = s.replace(old, new)

    # spazi puliti
    return " ".join(s.strip().split())


def _tokens(text: str) -> set:
    """Token significativi (senza stopwords)."""
    return {t for t in _norm(text).split() if t and t not in STOPWORDS}


def _item_trigger_texts(item: Dict[str, Any]) -> List[str]:
    """
    Costruisce tutti i testi che rappresentano il "significato"
    dell'item: domande, q, tag, pezzo di canonical.
    """
    triggers: List[str] = []

    # campi legacy
    q_field = item.get("q")
    if isinstance(q_field, list):
        triggers.extend(q_field)
    elif isinstance(q_field, str):
        triggers.append(q_field)

    # campo ufficiale
    qs_field = item.get("questions")
    if isinstance(qs_field, list):
        triggers.extend(qs_field)
    elif isinstance(qs_field, str):
        triggers.append(qs_field)

    # tag come indizi semantici
    tags = item.get("tags") or []
    if isinstance(tags, list) and tags:
        triggers.append(" ".join(str(t) for t in tags))

    # un estratto della canonical per dare contesto
    canon = (item.get("canonical") or "").strip()
    if canon:
        triggers.append(canon[:260])

    # pulizia
    out = []
    for t in triggers:
        t = str(t).strip()
        if t:
            out.append(t)
    return out


def match_item(user_q: str, family_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Trova l'item GOLD più coerente con la domanda naturale."""
    items = family_data.get("items", [])
    if not items:
        return None

    qn = _norm(user_q)
    q_tokens = _tokens(user_q)

    best_item = None
    best_score = 0.0

    for item in items:
        trigger_texts = _item_trigger_texts(item)
        if not trigger_texts:
            continue

        for t in trigger_texts:
            tn = _norm(t)
            if not tn:
                continue

            t_tokens = _tokens(t)

            # similarità di forma
            base = SequenceMatcher(None, qn, tn).ratio()

            # overlap di parole chiave
            overlap = 0.0
            if t_tokens:
                overlap = len(q_tokens & t_tokens) / max(len(t_tokens), 1)

            bonus = 0.0

            # bonus se domanda parla esplicitamente di utilizzo/campo-uso
            key_use = {"campo-uso", "applicazioni", "utilizzo"}
            if q_tokens & key_use and ("campo-uso" in tn or "applicazioni" in tn):
                bonus += 0.18

            # bonus se coincide o contiene
            if qn == tn:
                bonus += 0.30
            elif tn in qn or qn in tn:
                bonus += 0.18

            # leggera penalizzazione per trigger troppo corti (tipo solo 1 parola)
            if len(tn) < 25:
                bonus -= 0.06

            score = 0.55 * base + 0.35 * overlap + bonus

            if score > best_score:
                best_score = score
                best_item = item

    # soglia: tarata per non perdere domande naturali ma evitare accoppiamenti assurdi
    if best_item and best_score >= 0.40:
        return best_item

    return None

# ============================================================
# SELEZIONE RISPOSTA GOLD (DINAMICA SERIA)
# ============================================================

def pick_response(item: Dict[str, Any]) -> str:
    """
    Sceglie una risposta GOLD:
    - preferisce varianti lunghe e complete,
    - se non ci sono, usa la canonical,
    - mai moncherini ridicoli.
    """
    canonical = (item.get("canonical") or "").strip()
    variants = item.get("response_variants") or []

    rich_variants: List[str] = []
    if isinstance(variants, list):
        for v in variants:
            if not isinstance(v, str):
                continue
            txt = v.strip()
            # consideriamo GOLD solo testi con un minimo di corpo
            if len(txt) >= 140:
                rich_variants.append(txt)

    if rich_variants:
        return random.choice(rich_variants)

    if canonical:
        return canonical

    # fallback: se qualcuno ha lasciato varianti corte le usiamo solo se non c'è altro
    fallback = [v.strip() for v in variants if isinstance(v, str) and v.strip()]
    if fallback:
        return random.choice(fallback)

    return "Risposta non disponibile."

# ============================================================
# MODELLI RICHIESTA
# ============================================================

class AskPayload(BaseModel):
    q: str
    family: str

# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse("<h1>Tecnaria Sinapsi — Q/A</h1>")


@app.get("/api/config")
async def api_config():
    return {
        "ok": True,
        "app": "Tecnaria Sinapsi — Q/A",
        "families_dir": str(DATA_DIR)
    }


@app.post("/api/ask")
async def api_ask(payload: AskPayload):
    user_q = (payload.q or "").strip()
    fam_name = (payload.family or "").strip()

    if not user_q:
        return {
            "ok": False,
            "family": fam_name,
            "q": payload.q,
            "text": "Domanda vuota."
        }

    # carica famiglia
    try:
        fam = load_family(fam_name)
    except FileNotFoundError:
        return {
            "ok": False,
            "family": fam_name,
            "q": user_q,
            "text": f"Famiglia '{fam_name}' non disponibile."
        }
    except Exception:
        return {
            "ok": False,
            "family": fam_name,
            "q": user_q,
            "text": "Errore nella lettura dei dati di questa famiglia."
        }

    # matching semantico
    item = match_item(user_q, fam)

    if not item:
        return {
            "ok": False,
            "family": fam_name,
            "q": user_q,
            "text": "Nessuna risposta trovata per questa domanda."
        }

    # risposta GOLD dinamica
    text = pick_response(item)

    return {
        "ok": True,
        "family": fam_name,
        "id": item.get("id"),
        "mode": "dynamic",
        "text": text
    }
