# app.py — FastAPI "offline" per Tecnaria_V3
# - Usa SOLO il dataset: static/data/tecnaria_gold.json
# - Endpoint: /ping, /status, /ask
# - Nessuna dipendenza esterna (niente OpenAI)
# - Matching semplice per trigger/keywords + family hints

import json, re, unicodedata, time
from pathlib import Path
from typing import Dict, Any, List, Tuple
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

APP_DIR = Path(__file__).parent
DATA_FILE = APP_DIR / "static" / "data" / "tecnaria_gold.json"  # <<— PERCORSO FISSO

app = FastAPI(title="Tecnaria Sinapsi — Q/A (offline)")

# ====== UTILS ======
def normalize(text: str) -> str:
    if not text: 
        return ""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.lower()
    t = re.sub(r"[^a-z0-9àèéìíòóùç\s\-_/]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def tokenize(text: str) -> List[str]:
    return normalize(text).split()

# ====== LOADER CON CACHE ======
_db_cache: Dict[str, Any] = {}
_db_mtime: float = 0.0

def load_db(force: bool = False) -> Dict[str, Any]:
    global _db_cache, _db_mtime
    if not DATA_FILE.exists():
        raise FileNotFoundError(f"File non trovato: {DATA_FILE}")
    mtime = DATA_FILE.stat().st_mtime
    if force or (mtime != _db_mtime) or not _db_cache:
        raw = DATA_FILE.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON NON VALIDO: {e}")
        if not isinstance(data, dict) or "items" not in data or not isinstance(data["items"], list):
            raise ValueError("JSON valido ma senza chiave 'items' (lista).")
        _db_cache = data
        _db_mtime = mtime
    return _db_cache

# ====== RISPOSTORE ======
FAMILY_HINT_WEIGHT = {
    "CTF": 1.1, "CTL": 1.1, "CTL MAXI": 1.15, "CTCEM": 1.1,
    "VCEM": 1.1, "P560": 1.05, "DIAPASON": 1.0, "GTS": 1.0,
    "ACCESSORI": 1.0, "CONFRONTO": 0.9, "PROBLEMATICHE": 1.0, "KILLER": 1.0, "COMM": 0.85
}
FAM_TOKENS = ["ctf","ctl","maxi","ctcem","vcem","p560","diapason","gts","accessori","confronto","problematiche","killer","comm"]

def family_hints_from_text(text_norm: str) -> set:
    hints = set()
    for fam in FAM_TOKENS:
        if fam in text_norm:
            hints.add(fam.upper() if fam != "maxi" else "CTL MAXI")
    return hints

def score_item(question_norm: str, item: Dict[str, Any], family_hints: set) -> Tuple[float, int]:
    trig = (item.get("trigger") or {})
    kws = [normalize(k) for k in (trig.get("keywords") or [])]
    peso = float(trig.get("peso", 1.0))
    hits = 0
    q_tokens = tokenize(question_norm)
    for kw in kws:
        if kw and (kw in question_norm or any(tok.startswith(kw) or kw.startswith(tok) for tok in q_tokens)):
            hits += 1
    fam = (item.get("family") or "").upper()
    fam_boost = 1.0
    if fam in FAMILY_HINT_WEIGHT and (fam in family_hints or (fam == "CTL MAXI" and "CTL MAXI" in family_hints)):
        fam_boost = FAMILY_HINT_WEIGHT[fam]
    score = (hits * peso) * fam_boost
    return score, hits

def answer_from_json(question: str) -> Dict[str, Any]:
    db = load_db()
    items = db.get("items", [])
    qn = normalize(question)
    hints = family_hints_from_text(qn)

    best = None
    best_score = -1.0
    best_hits = 0
    for it in items:
        s, h = score_item(qn, it, hints)
        if s > best_score:
            best_score, best, best_hits = s, it, h

    if best is None or best_score <= 0:
        # fallback deterministico: prova alcune ID note
        for fid in ("CTF-0001", "COMM-0001"):
            cand = next((it for it in items if it.get("id") == fid), None)
            if cand:
                best, best_score, best_hits = cand, 0.1, 0
                break

    resp_text = (best.get("risposta") or best.get("answer") or "").strip()
    return {
        "answer": resp_text,
        "meta": {
            "best_item": {"id": best.get("id"), "family": best.get("family")},
            "trigger_hits": best_hits,
            "score_internal": best_score
        }
    }

# ====== SCHEMI I/O ======
class AskInput(BaseModel):
    question: str

# ====== ENDPOINTS ======
@app.get("/ping")
def ping():
    return "alive"

@app.get("/status")
def status():
    try:
        db = load_db(force=False)
        n = len(db.get("items", []))
        return JSONResponse({"ok": True, "file": str(DATA_FILE), "items": n, "message": "PRONTO"})
    except FileNotFoundError as e:
        return JSONResponse({"ok": False, "file": str(DATA_FILE), "items": 0, "message": "FILE NON TROVATO", "error": str(e)}, status_code=500)
    except ValueError as e:
        return JSONResponse({"ok": False, "file": str(DATA_FILE), "items": 0, "message": "JSON NON VALIDO", "error": str(e)}, status_code=500)
    except Exception as e:
        return JSONResponse({"ok": False, "file": str(DATA_FILE), "items": 0, "message": "ERRORE GENERICO", "error": str(e)}, status_code=500)

@app.post("/ask")
def ask(body: AskInput):
    question = (body.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question mancante")
    # garantiamo che il JSON sia valido prima
    st = status()
    if isinstance(st, JSONResponse):
        js = st.body
    try:
        db = load_db()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Dataset non disponibile: {e}")
    result = answer_from_json(question)
    return {"ok": True, "question": question, **result}
