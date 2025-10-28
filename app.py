# app.py
# Tecnaria GOLD – App unica con best-answer, P560-only per CTF e fallback multilingua.
# Dipendenze: fastapi, uvicorn (già usate nel tuo progetto). Nessun requisito extra.

from __future__ import annotations
import json, re, os, sys, traceback
from pathlib import Path
from typing import List, Dict, Any, Tuple
from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware

APP_NAME = "Tecnaria GOLD Master"
BASE_DIR = Path(__file__).parent
DATA_PATHS = [
    BASE_DIR / "static" / "data" / "gold_master.json",  # file master consigliato
    # Fallback opzionali (non obbligatori). Verranno ignorati se non esistono.
    BASE_DIR / "static" / "data" / "ctf_gold.json",
    BASE_DIR / "static" / "data" / "ctl_gold.json",
    BASE_DIR / "static" / "data" / "p560_gold.json",
    BASE_DIR / "static" / "data" / "ctcem_gold.json",
    BASE_DIR / "static" / "data" / "vcem_gold.json",
]

DEFAULT_LANG = "it"
SUPPORTED_LANGS = {"it","en","fr","de","es"}

app = FastAPI(title=APP_NAME)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ---------------------------
# Caricamento e normalizzazione
# ---------------------------

GOLD: Dict[str, Any] = {}
ITEMS: List[Dict[str, Any]] = []
POLICY: Dict[str, Any] = {}
COMPILED_PATTERNS: List[Tuple[int, str, re.Pattern]] = []  # (index, lang, regex)

WORD_RE = re.compile(r"[a-z0-9+/.-]+", re.I)

def _lower(s: str) -> str:
    return (s or "").strip().lower()

def _tokenize(s: str) -> List[str]:
    return [w.lower() for w in WORD_RE.findall(s or "")]

def _exists(p: Path) -> bool:
    try:
        return p.exists() and p.is_file()
    except Exception:
        return False

def load_first_existing(paths: List[Path]) -> Tuple[Path, Dict[str, Any]]:
    # preferisci gold_master.json; altrimenti prova i fallback come merge semplice
    merged = None
    used_path = None
    for p in paths:
        if _exists(p):
            try:
                data = json.loads(p.read_text("utf-8"))
                if merged is None:
                    merged = data
                    used_path = p
                else:
                    # merge *semplice*: aggiunge items se esistono altre sorgenti
                    if "items" in data:
                        merged.setdefault("items", [])
                        merged["items"].extend(data["items"])
                    # mantieni meta/policy della prima fonte valida
            except Exception:
                continue
    if merged is None:
        raise FileNotFoundError("Nessun file dati trovato (gold_master.json o fallback).")
    return used_path, merged

def compile_patterns():
    global COMPILED_PATTERNS
    COMPILED_PATTERNS = []
    for idx, it in enumerate(ITEMS):
        pats = it.get("patterns", {})
        for lang, arr in pats.items():
            if not isinstance(arr, list):
                continue
            for pat in arr:
                # trasformiamo in regex 'soft' che tollera spazi/punteggiatura
                rx = re.escape(pat.strip())
                rx = rx.replace(r"\ ", r"\s+")
                try:
                    COMPILED_PATTERNS.append((idx, lang, re.compile(rx, re.I)))
                except re.error:
                    # se una pattern non compila, la saltiamo
                    continue

def bootstrap():
    global GOLD, ITEMS, POLICY
    used, GOLD = load_first_existing(DATA_PATHS)
    meta = GOLD.get("meta", {})
    POLICY = meta.get("policy", {})
    items = GOLD.get("items", [])

    # dedup semplice su id
    seen = set()
    normalized = []
    for it in items:
        if not isinstance(it, dict):
            continue
        _id = it.get("id") or f"auto-{len(seen)}"
        if _id in seen:
            continue
        seen.add(_id)
        # normalizza campi minimi
        it.setdefault("priority", 50)
        it.setdefault("family", "")
        it.setdefault("intent", "")
        it.setdefault("patterns", {})
        it.setdefault("keywords", [])
        it.setdefault("answer", {})
        it.setdefault("tags", [])
        normalized.append(it)

    # ordina per priority desc, poi per id
    normalized.sort(key=lambda x: (-int(x.get("priority", 0)), str(x.get("id",""))))
    ITEMS = normalized
    compile_patterns()

bootstrap()

# ---------------------------
# Scoring & Regole
# ---------------------------

NON_TECNARIA_BLOCK = set([_lower(x) for x in POLICY.get("non_tecnaria_blocklist", [])])
LAMIERA_TOKENS_OK = set([_lower(x) for x in POLICY.get("lamiera_height_tokens_ok", [])])

def detect_language(s: str) -> str:
    # euristica minimale: se contiene molte parole italiane comuni => it
    s_l = _lower(s)
    if any(k in s_l for k in ["qual", "come", "posso", "che cosa", "connettori", "viti", "chiodi", "lamiera"]):
        return "it"
    # default
    return DEFAULT_LANG

def hard_rule_ctf_p560_only(question_l: str) -> int | None:
    """
    Se la domanda riguarda CTF + utensili non Tecnaria -> forza l'item policy P560-only.
    Se vede H75 in senso 'pistola' (vs), ignora e reindirizza alla regola: H75 è quota lamiera.
    Ritorna l'indice dell'item forzato, o None se non applicabile.
    """
    qtok = set(_tokenize(question_l))
    mentions_ctf = "ctf" in qtok or "acciaio" in qtok
    mentions_tool = any(w in qtok for w in ["pistola","chiodatrice","sparatore","sparo","utensile","nailer","gun"])
    mentions_non_tecnaria = any(b in question_l for b in NON_TECNARIA_BLOCK)
    mentions_h75 = "h75" in qtok and ("vs" in qtok or "contro" in qtok or "confronto" in qtok)

    if mentions_ctf and (mentions_tool or mentions_non_tecnaria or mentions_h75):
        # cerca item con intent policy_tecnaria_only
        for i, it in enumerate(ITEMS):
            if it.get("family") == "CTF" and it.get("intent") == "policy_tecnaria_only":
                return i
    return None

def soft_score(item: Dict[str,Any], q: str, lang: str) -> float:
    score = 0.0
    ql = _lower(q)
    qtok = _tokenize(q)
    qset = set(qtok)

    # 1) pattern match (peso alto)
    pats = item.get("patterns", {})
    for plang, arr in pats.items():
        if plang != lang and plang != DEFAULT_LANG:
            continue
        for pat in arr:
            if not pat: 
                continue
            if re.search(rf"\b{re.escape(pat.strip())}\b", ql, flags=re.I):
                score += 8.0

    # 2) regex compilate (già costruite): match precise -> bonus
    # (già coperto dal punto 1 per la lingua corretta)

    # 3) keywords overlap
    kw = [k.lower() for k in item.get("keywords", [])]
    score += 2.0 * sum(1 for k in kw if k in qset)

    # 4) family hint
    fam = _lower(item.get("family",""))
    if fam and fam in qset:
        score += 1.5

    # 5) intent hint
    intent = _lower(item.get("intent",""))
    if intent and intent in ql:
        score += 1.0

    # 6) priorità come bias
    score += 0.05 * float(item.get("priority", 0))

    # 7) penalità se domanda cita brand non Tecnaria (evita risposte deboli)
    if any(b in ql for b in NON_TECNARIA_BLOCK):
        score -= 1.0

    return score

def pick_best_answer(question: str, lang_hint: str|None=None) -> Dict[str, Any]:
    if not question or not question.strip():
        return {"answer": "Fammi una domanda sui prodotti Tecnaria (CTF, CTL, CTL MAXI, P560, CTCEM, VCEM...).", "meta": {"reason":"empty"}}

    lang = (lang_hint or detect_language(question) or DEFAULT_LANG)
    if lang not in SUPPORTED_LANGS:
        lang = DEFAULT_LANG

    ql = _lower(question)

    # HARD RULE: P560-only per CTF con utensili non Tecnaria / confronto improprio (es. H75 come 'pistola')
    forced_idx = hard_rule_ctf_p560_only(ql)
    candidates = enumerate(ITEMS)

    best = None
    best_score = -1e9

    for idx, it in candidates:
        # se regola dura ha selezionato un indice, scoriamo gli altri molto più bassi
        base = soft_score(it, question, lang)
        if forced_idx is not None:
            if idx == forced_idx:
                base += 1000.0  # forza scelta
            else:
                base -= 1000.0

        # leggera preferenza se l'item ha la lingua richiesta nel testo answer
        ans_block = it.get("answer", {})
        if lang in ans_block:
            base += 0.5
        elif DEFAULT_LANG in ans_block:
            base += 0.2

        if base > best_score:
            best_score = base
            best = (idx, it)

    if not best:
        return {"answer": "Non ho trovato una risposta adeguata. Prova con parole chiave come CTF, CTL, P560, lamiera 1×1,5, viti Ø10, ecc.", "meta": {"reason":"no_match"}}

    idx, item = best
    ans_block = item.get("answer", {})
    text = ans_block.get(lang) or ans_block.get(DEFAULT_LANG) or ""

    # Nota H75: se la domanda contiene H75 e 'vs'/'contro', inseriamo chiarimento una sola volta
    qtok = set(_tokenize(ql))
    if "h75" in qtok and ("vs" in qtok or "contro" in qtok or "confronto" in qtok):
        note = "\n\n**Nota**: *H75* è una **quota/altezza di lamiera grecata**, non un utensile Tecnaria; con **CTF** è ammessa se **ben serrata**. Gli utensili ammessi per CTF sono **solo SPIT P560** con kit Tecnaria."
        if note.strip() not in text:
            text += note

    return {
        "answer": text.strip(),
        "item": {
            "id": item.get("id"),
            "family": item.get("family"),
            "intent": item.get("intent"),
            "priority": item.get("priority"),
            "tags": item.get("tags", []),
        },
        "score": round(best_score, 3),
        "lang": lang
    }

# ---------------------------
# API
# ---------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "items": len(ITEMS),
        "families": sorted({it.get("family","") for it in ITEMS if it.get("family")}),
        "policy": POLICY,
    }

@app.post("/ask")
def ask(payload: Dict[str, Any] = Body(...)):
    """
    payload: { "q": "testo domanda", "lang": "it|en|..." }
    Ritorna una sola risposta (best-answer).
    """
    try:
        q = payload.get("q","")
        lang = payload.get("lang")
        res = pick_best_answer(q, lang)
        return {"ok": True, "data": res}
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "trace": traceback.format_exc(limit=2)
        }

@app.post("/reload")
def reload_data():
    try:
        bootstrap()
        return {"ok": True, "reloaded_items": len(ITEMS)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# Avvio locale: uvicorn app:app --reload --port 8000
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
