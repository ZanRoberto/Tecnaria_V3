from __future__ import annotations
import json, os, re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Query
from pydantic import BaseModel

# -----------------------------
# Config base
# -----------------------------
APP_TITLE = "Tecnaria Sinapsi — Q/A"
DATA_DIR = Path(__file__).parent / "static" / "data"
GOLD_FILE = DATA_DIR / "tecnaria_gold.json"   # <<— nome fisso richiesto
ALLOWED_FAMILIES = {"CTF", "CTL", "CTL MAXI", "DIAPASON", "GTS", "VCEM", "CTCEM", "SPIT P560", "ACCESSORI"}

app = FastAPI(title=APP_TITLE)

# -----------------------------
# Modelli I/O
# -----------------------------
class QAItem(BaseModel):
    family: str
    question: str
    answer: str
    tags: List[str] = []
    score: float = 1.0
    lang: Optional[str] = None

class AskResponse(BaseModel):
    family: Optional[str]
    score: float
    language: str
    best_answer: str
    best_question: str
    tags: List[str] = []

# -----------------------------
# Seed Fallback (stringa CHIUSA correttamente)
# -----------------------------
DEFAULT_FALLBACK = QAItem(
    family="GENERIC",
    question="Domanda poco chiara o fuori ambito.",
    answer=(
        "Domanda poco chiara o fuori ambito Tecnaria.\n\n"
        "Esempi utili di domanda:\n"
        "• «CTF: posso usare una chiodatrice generica o serve la SPIT P560?»\n"
        "• «CTL MAXI su tavolato 25–30 mm e soletta 5–6 cm: quali viti?»\n"
        "• «CTCEM/VCEM: serve resina per laterocemento?»\n\n"
        "Suggerimento: indica famiglia (CTF, CTL/CTL MAXI, P560, VCEM, Diapason…) e il caso d’uso."
    ),
    tags=["fallback", "help"],
    score=0.0,
    lang="it",
)

# -----------------------------
# Caricamento GOLD
# -----------------------------
GOLD: List[QAItem] = []

def _detect_lang(text: str) -> str:
    t = text.lower()
    if re.search(r"[àèéìíòóù]", t) or " che " in t or " come " in t:
        return "it"
    if re.search(r"\b(que|cómo|cuál|dónde|por qué)\b", t):
        return "es"
    if re.search(r"\b(comment|pourquoi|quelle|où)\b", t):
        return "fr"
    if re.search(r"\b(welche|warum|wie|wo)\b", t):
        return "de"
    return "en"

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()

def _score(q: str, item: QAItem) -> float:
    # pesi semplici: overlap parole + boost per famiglia
    qn = set(_norm(q).split())
    kn = set(_norm(item.question + " " + " ".join(item.tags)).split())
    overlap = len(qn & kn) / (len(qn) + 1e-6)
    fam_boost = 0.10 if any(f.lower() in _norm(q) for f in [item.family]) else 0.0
    return round(min(1.0, overlap + fam_boost), 4)

def load_gold() -> None:
    GOLD.clear()

    # seed: se il file manca, non crolla
    if not GOLD_FILE.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        DATA_DIR.joinpath("README.txt").write_text("Metti qui tecnaria_gold.json", encoding="utf-8")
        GOLD.append(DEFAULT_FALLBACK)
        return

    with GOLD_FILE.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    # Accetta sia lista piatta sia oggetti con chiavi note
    items: List[Dict[str, Any]] = raw if isinstance(raw, list) else raw.get("items", [])
    for it in items:
        try:
            fam = it.get("family") or it.get("famiglia") or ""
            fam = fam.strip().upper()
            if fam and fam not in ALLOWED_FAMILIES and fam != "GENERIC":
                # scarta roba non Tecnaria
                continue

            q = (it.get("question") or it.get("q") or "").strip()
            a = (it.get("answer") or it.get("a") or "").strip()
            tags = it.get("tags") or []
            lang = it.get("lang") or _detect_lang(a or q)

            if not q or not a:
                continue

            GOLD.append(QAItem(family=fam or "GENERIC", question=q, answer=a, tags=tags, lang=lang))
        except Exception:
            # in caso di riga sporca, ignora e continua
            continue

    if not GOLD:
        GOLD.append(DEFAULT_FALLBACK)

load_gold()

# -----------------------------
# Health
# -----------------------------
@app.get("/health")
def health():
    return {"ok": True, "items": len(GOLD), "file": str(GOLD_FILE)}

# -----------------------------
# ASK (una sola Risposta Migliore)
# -----------------------------
@app.get("/qa/ask", response_model=AskResponse)
def qa_ask(q: str = Query(..., min_length=2, description="Domanda utente"),
           family: Optional[str] = Query(None, description="Filtra per famiglia (CTF, CTL, …)")):

    if not q.strip():
        item = DEFAULT_FALLBACK
        return AskResponse(family=item.family, score=item.score, language=item.lang or "it",
                           best_answer=item.answer, best_question=item.question, tags=item.tags)

    user_lang = _detect_lang(q)
    cand: List[Tuple[float, QAItem]] = []

    for it in GOLD:
        if family and it.family and it.family.upper() != family.upper():
            continue
        s = _score(q, it)
        if s > 0:
            cand.append((s, it))

    if not cand:
        item = DEFAULT_FALLBACK
        return AskResponse(family=item.family, score=item.score, language=user_lang,
                           best_answer=item.answer, best_question=item.question, tags=item.tags)

    cand.sort(key=lambda x: x[0], reverse=True)
    s, best = cand[0]

    return AskResponse(
        family=best.family or None,
        score=float(s),
        language=user_lang,
        best_answer=best.answer,
        best_question=best.question,
        tags=best.tags,
    )

# -----------------------------
# Index minimale (per comodità)
# -----------------------------
@app.get("/")
def index():
    return {
        "title": APP_TITLE,
        "endpoints": {
            "health": "/health",
            "ask": "/qa/ask?q=MI%20PARLI%20DELLA%20P560%20%3F",
        },
        "data_file": str(GOLD_FILE),
        "items_loaded": len(GOLD),
    }
