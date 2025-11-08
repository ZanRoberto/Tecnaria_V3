import os
import json
import re
from functools import lru_cache
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

APP_NAME = "Tecnaria Sinapsi — Q/A"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FAMILIES_DIR = os.path.join(BASE_DIR, "static", "data")
RUNTIME_CONFIG = os.path.join(FAMILIES_DIR, "config.runtime.json")

DEFAULT_SUPPORTED_LANGS = ["it", "en", "fr", "de", "es"]
DEFAULT_FALLBACK_LANG = "en"

app = FastAPI(title=APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskPayload(BaseModel):
    q: str
    family: Optional[str] = None  # opzionale: se assente, instradamento automatico


# -----------------------
# Utility di base
# -----------------------

def _safe_read_json(path: str) -> Any:
    if not os.path.exists(path):
        raise FileNotFoundError(f"File non trovato: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_runtime_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    if os.path.exists(RUNTIME_CONFIG):
        try:
            cfg = _safe_read_json(RUNTIME_CONFIG)
        except Exception:
            cfg = {}
    return {
        "supported_langs": cfg.get("supported_langs", DEFAULT_SUPPORTED_LANGS),
        "fallback_lang": cfg.get("fallback_lang", DEFAULT_FALLBACK_LANG),
        "translation": bool(cfg.get("translation", False)),
    }


@lru_cache(maxsize=1)
def get_config_cached() -> Dict[str, Any]:
    cfg = _load_runtime_config()
    cfg["families_dir"] = FAMILIES_DIR
    return cfg


def detect_lang_from_text(text: str, supported: List[str]) -> str:
    """
    Riconoscimento super-semplificato.
    Se non riconosciamo in modo sensato, usiamo 'it' come default logico
    (dato il dominio) e gestiamo fallback a livello di risposta.
    """
    t = text.lower()

    # euristiche minime
    if any(ch in t for ch in ["è", "ò", "à", "ì", "ù"]):
        return "it"
    if re.search(r"\b(the|and|can|use|with|on|steel|beam|beams)\b", t):
        return "en"
    if re.search(r"\b(le|les|des|avec|peut|utiliser)\b", t):
        return "fr"
    if re.search(r"\b(der|die|das|und|mit|kann)\b", t):
        return "de"
    if re.search(r"\b(el|los|las|con|puedo|usar)\b", t):
        return "es"

    # default dominio
    if "it" in supported:
        return "it"
    return supported[0] if supported else "it"


def normalize_family_name(name: str) -> str:
    return name.strip().upper()


@lru_cache(maxsize=None)
def list_family_files() -> Dict[str, str]:
    """
    Mappa: NOME_FAMIGLIA -> path file json
    Usa il nome file (senza estensione) come family.
    """
    if not os.path.isdir(FAMILIES_DIR):
        raise RuntimeError(f"Directory families non trovata: {FAMILIES_DIR}")

    mapping: Dict[str, str] = {}
    for fname in os.listdir(FAMILIES_DIR):
        if not fname.lower().endswith(".json"):
            continue
        if fname == os.path.basename(RUNTIME_CONFIG):
            continue
        family = os.path.splitext(fname)[0].upper()
        mapping[family] = os.path.join(FAMILIES_DIR, fname)
    return mapping


@lru_cache(maxsize=None)
def load_family_data(family: str) -> List[Dict[str, Any]]:
    family_norm = normalize_family_name(family)
    files = list_family_files()
    if family_norm not in files:
        raise FileNotFoundError(f"Family JSON non trovato per: {family_norm}")
    raw = _safe_read_json(files[family_norm])

    # Supporta array diretto, { "blocks": [...] }, { "data": [...] }
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        if "blocks" in raw and isinstance(raw["blocks"], list):
            items = raw["blocks"]
        elif "data" in raw and isinstance(raw["data"], list):
            items = raw["data"]
        else:
            # Se è un dict generico, prendiamo i valori che sono dict
            items = []
            for v in raw.values():
                if isinstance(v, dict):
                    items.append(v)
    else:
        items = []

    # Normalizzazione minima
    norm_items: List[Dict[str, Any]] = []
    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        item = dict(it)
        item.setdefault("id", item.get("ID", f"{family_norm}-{idx:04d}"))
        item.setdefault("family", family_norm)
        norm_items.append(item)

    return norm_items


# -----------------------
# Estrazione testo risposta
# -----------------------

def extract_answer_text(item: Dict[str, Any], lang: str, fallback_lang: str) -> Optional[str]:
    """
    Cerca la miglior risposta leggibile nel blocco.
    Ordine:
    - answers[lang]
    - answers[fallback_lang]
    - answers[it]
    - answer
    - text
    - canonical / dynamic (solo se stringa chiara, NON etichette)
    - it / en diretti (se stringhe)
    Se non trova nulla o solo vuoto → None.
    """
    def clean(s: Any) -> str:
        return s.strip() if isinstance(s, str) else ""

    answers = item.get("answers") or item.get("answer_map") or {}
    if isinstance(answers, dict):
        txt = clean(answers.get(lang))
        if txt:
            return txt
        txt = clean(answers.get(fallback_lang))
        if txt:
            return txt
        txt = clean(answers.get("it"))
        if txt:
            return txt
        # qualsiasi lingua disponibile
        for v in answers.values():
            txt = clean(v)
            if txt:
                return txt

    # campi diretti
    for key in ["answer", "text", "it", "en"]:
        txt = clean(item.get(key))
        if txt:
            return txt

    # se canonical/dynamic sono usati come vere risposte testuali
    for key in ["canonical", "dynamic"]:
        val = item.get(key)
        txt = clean(val)
        if txt and not re.fullmatch(r"(canonical|dynamic)", txt.lower()):
            return txt

    return None


# -----------------------
# Scoring / matching
# -----------------------

WORD_RE = re.compile(r"[a-z0-9àèéìòóùç]+")

def tokenize(text: str) -> List[str]:
    return WORD_RE.findall(text.lower())


def item_score(q: str, item: Dict[str, Any]) -> float:
    """
    Scoring robusto ma semplice.
    - overlap parole domanda/risposta/patterns
    - boost se matcha patterns
    - nessuna soglia assassina: se c'è match, usiamo il best.
    """
    q_tokens = set(tokenize(q))
    if not q_tokens:
        return 0.0

    score = 0.0

    # patterns (trigger espliciti)
    patterns = item.get("patterns") or item.get("tags") or []
    if isinstance(patterns, str):
        patterns = [patterns]
    if isinstance(patterns, list):
        for p in patterns:
            if not isinstance(p, str):
                continue
            p_low = p.lower().strip()
            if not p_low:
                continue
            # se il pattern appare nella domanda → forte boost
            if p_low in q.lower():
                score += 2.0

    # Consideriamo testo domanda (se presente nel blocco)
    for key in ["q", "question"]:
        txt = item.get(key)
        if isinstance(txt, str) and txt.strip():
            itoks = set(tokenize(txt))
            common = q_tokens & itoks
            if common:
                score += len(common) / max(len(itoks), 1)

    # Consideriamo anche un po' il testo risposta come contesto
    # (solo per raffinare, non per inventare).
    answer_for_score = ""
    if isinstance(item.get("answer"), str):
        answer_for_score = item["answer"]
    elif isinstance(item.get("text"), str):
        answer_for_score = item["text"]

    if answer_for_score:
        atoks = set(tokenize(answer_for_score))
        common = q_tokens & atoks
        if common:
            score += 0.3 * (len(common) / max(len(q_tokens), 1))

    # Se contiene riferimenti molto chiari (VCEM, P560, CTF etc.)
    strong_keywords = ["vcem", "p560", "ctf", "ctl", "ctcem", "diapason"]
    for kw in strong_keywords:
        if kw in q.lower():
            # se anche l'item parla di quel kw → bonus
            txt_full = json.dumps(item, ensure_ascii=False).lower()
            if kw in txt_full:
                score += 0.7

    return score


def choose_best_block(q: str, lang: str, fallback_lang: str,
                      families: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """
    Cerca il miglior blocco tra le famiglie richieste (o tutte).
    Scarta in automatico i blocchi senza testo risposta.
    Nessuna soglia rigida: se c'è almeno 1 blocco con score > 0, usiamo il top.
    """
    if families is None:
        families = list(list_family_files().keys())

    q_stripped = q.strip()
    if not q_stripped:
        return None

    best = None
    best_score = 0.0

    for fam in families:
        try:
            items = load_family_data(fam)
        except FileNotFoundError:
            continue

        for item in items:
            # prendo subito la risposta; se non c'è, scarto
            answer_text = extract_answer_text(item, lang, fallback_lang)
            if not answer_text:
                continue

            s = item_score(q_stripped, item)
            if s > best_score:
                best_score = s
                best = {
                    "family": fam,
                    "id": str(item.get("id", "")),
                    "mode": infer_mode(item),
                    "lang": lang,
                    "text": answer_text,
                }

    if best_score <= 0.0:
        return None
    return best


def infer_mode(item: Dict[str, Any]) -> str:
    """
    canonical / dynamic per leggenda frontend.
    """
    mode = item.get("mode") or item.get("answer_type") or ""
    if isinstance(mode, str):
        m = mode.lower()
        if "canon" in m:
            return "canonical"
        if "dyn" in m:
            return "dynamic"
    # euristiche minime
    if "canonical" in item:
        return "canonical"
    if "dynamic" in item:
        return "dynamic"
    return "dynamic"


# -----------------------
# API
# -----------------------

@app.get("/api/config")
def api_config():
    cfg = get_config_cached()
    return {
        "app": APP_NAME,
        "status": "OK",
        "families_dir": cfg["families_dir"],
        "supported_langs": cfg["supported_langs"],
        "fallback_lang": cfg["fallback_lang"],
        "translation": cfg["translation"],
    }


@app.post("/api/ask")
def api_ask(payload: AskPayload):
    cfg = get_config_cached()
    supported = cfg["supported_langs"]
    fallback_lang = cfg["fallback_lang"]

    q = (payload.q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Domanda vuota.")

    lang = detect_lang_from_text(q, supported)
    if lang not in supported:
        lang = fallback_lang

    # Se family è specificata, cerco solo lì.
    families = None
    family_norm = None
    if payload.family:
        family_norm = normalize_family_name(payload.family)
        if family_norm not in list_family_files():
            raise HTTPException(status_code=404, detail=f"Famiglia '{payload.family}' non trovata.")
        families = [family_norm]

    best = choose_best_block(q, lang, fallback_lang, families=families)

    if not best:
        # nessun match GOLD trovato
        return {
            "ok": False,
            "family": family_norm,
            "q": q,
            "lang": lang,
            "text": "Per questa domanda non è ancora presente una risposta GOLD nei contenuti Tecnaria. "
                    "Se è una casistica reale di cantiere, aggiungi il blocco corrispondente nel JSON appropriato."
        }

    return {
        "ok": True,
        "family": best["family"],
        "id": best["id"],
        "mode": best["mode"],
        "lang": best["lang"],
        "text": best["text"],
    }
