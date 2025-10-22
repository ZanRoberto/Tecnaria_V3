# app.py
import csv
import os
import time
import unicodedata
from pathlib import Path
from typing import List, Dict, Optional
from fastapi import FastAPI
from pydantic import BaseModel

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
FAQ_CSV_PATH = DATA_DIR / "faq.csv"

def _norm(s: str) -> str:
    if not s:
        return ""
    s = s.strip().lower()
    s = "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(s.split())

def _tokenize(s: str) -> List[str]:
    return [t for t in _norm(s).replace("|", " ").replace(";", " ").split(" ") if t]

def _contains(hay: str, needle: str) -> bool:
    return _norm(needle) in _norm(hay)

class KBItem(BaseModel):
    id: str
    language: str   # it|en|fr|es|de
    intent: str
    question_patterns: List[str]
    answer_short: str
    answer_full: str
    must_include_tokens: List[str]

KB: List[KBItem] = []

def load_kb() -> None:
    KB.clear()
    if not FAQ_CSV_PATH.exists():
        # fallback minimo se il CSV non c'è
        KB.append(KBItem(
            id="CTF_P560_ONLY",
            language="it",
            intent="ctf_posa",
            question_patterns=["ctf chiodatrice", "posare ctf con chiodatrice normale", "ctf spit", "ctf p560"],
            answer_short="No: solo SPIT P560 con kit Tecnaria.",
            answer_full="**No.** I connettori **CTF Tecnaria** si posano **solo con chiodatrice SPIT P560** con **kit/adattatori Tecnaria**. **Chiodi:** 2×HSBR14. **Propulsori:** in base al supporto. Altre chiodatrici non ammesse.",
            must_include_tokens=["ctf","p560","hsbr14","propulsori"]
        ))
        return

    with open(FAQ_CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            KB.append(KBItem(
                id=row["id"].strip(),
                language=row["language"].strip().lower(),
                intent=row["intent"].strip(),
                question_patterns=[p.strip() for p in row["question_patterns"].split(";") if p.strip()],
                answer_short=row["answer_short"].strip(),
                answer_full=row["answer_full"].strip(),
                must_include_tokens=[t.strip() for t in row.get("must_include_tokens","").split("|") if t.strip()],
            ))

load_kb()

def detect_lang_hint(q: str) -> Optional[str]:
    qn = _norm(q)
    if any(w in qn for w in ["lamiera","soletta","propulsori","laterocemento","chiodatrice","trave","preforo"]):
        return "it"
    if any(w in qn for w in ["deck","slab","propellant","nailer","timber","threaded","coupler"]):
        return "en"
    if any(w in qn for w in ["bac acier","béton","bois","couple","manchon"]):
        return "fr"
    if any(w in qn for w in ["chapa","hormigon","madera","manguito","cargas"]):
        return "es"
    if any(w in qn for w in ["trapezblech","beton","holz","gewinde","kartuschen"]):
        return "de"
    return None

def jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / float(len(sa | sb))

def route(q: str, lang: Optional[str]) -> Optional[KBItem]:
    qn = _norm(q)
    if not lang:
        lang = detect_lang_hint(qn)

    pool = [it for it in KB if (lang is None or it.language == lang)]

    # 1) containment pieno
    for it in pool:
        for pat in it.question_patterns:
            if _contains(qn, pat):
                return it

    # 2) jaccard su pool
    qtok = _tokenize(qn)
    best, best_score = None, 0.0
    for it in pool:
        local_best = 0.0
        for pat in it.question_patterns:
            local_best = max(local_best, jaccard(qtok, _tokenize(pat)))
        if local_best > best_score:
            best_score, best = local_best, it
    if best and best_score >= 0.25:
        return best

    # 3) cross-lingua (ultima chance)
    best, best_score = None, 0.0
    for it in KB:
        local_best = 0.0
        for pat in it.question_patterns:
            local_best = max(local_best, jaccard(qtok, _tokenize(pat)))
        if local_best > best_score:
            best_score, best = local_best, it
    if best and best_score >= 0.3:
        return best

    return None

app = FastAPI(title="Tecnaria QA Router", version="3.1")

class AskReq(BaseModel):
    q: str
    lang: Optional[str] = None  # it|en|fr|es|de

class AskResp(BaseModel):
    ok: bool
    text: str
    match_id: str
    ms: int

@app.get("/api/health")
def health():
    return {"status": "ok", "kb_items": len(KB)}

@app.post("/api/ask", response_model=AskResp)
def ask(req: AskReq):
    t0 = time.time()
    item = route(req.q, req.lang)
    if not item:
        text = (
            "Non trovo una voce specifica. "
            "Indica famiglia (CTF/CTL/CEM-E/CTCEM/VCEM/GTS/P560), supporto (lamiera/trave/legno) e operazione (posa/checklist/confronto/‘quando non usare’)."
        )
        return AskResp(ok=False, text=text, match_id="<NULL>", ms=int((time.time()-t0)*1000))

    # Risposta tecnica pronta per gli stress test
    text = item.answer_full
    return AskResp(ok=True, text=text, match_id=item.id, ms=int((time.time()-t0)*1000))

@app.post("/api/reload")
def reload_kb():
    load_kb()
    return {"ok": True, "kb_items": len(KB)}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
