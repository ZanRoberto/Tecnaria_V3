from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
from pathlib import Path

# 1. istanza FastAPI — QUESTA è quella che Render non trovava
app = FastAPI(title="Tecnaria Sinapsi — Q/A", version="1.0.0")

# 2. CORS (così puoi chiamarlo dal frontend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. modelli
class AskRequest(BaseModel):
    question: str

class AskResponse(BaseModel):
    answer: str
    score: float | None = None
    family: str | None = None
    source_id: str | None = None

# 4. carico il JSON Tecnaria
DATA_PATH = Path("static/data/tecnaria_gold.json")
if not DATA_PATH.exists():
    # se non c'è, almeno non ti esplode l'app
    TECNARIA_DATA = {"items": []}
else:
    with DATA_PATH.open("r", encoding="utf-8") as f:
        TECNARIA_DATA = json.load(f)

ITEMS = TECNARIA_DATA.get("items", [])

# 5. health
@app.get("/health")
def health():
    return {"status": "ok", "items_loaded": len(ITEMS)}

# 6. funzione di ricerca molto semplice (semantica light)
def find_best_match(user_q: str):
    user_q_low = user_q.lower()
    best = None
    best_score = 0.0

    for item in ITEMS:
        domanda = item.get("domanda", "") or item.get("question", "")
        domanda_low = domanda.lower()
        trigger = item.get("trigger", {})
        keywords = trigger.get("keywords", [])

        score = 0.0

        # match diretto testo
        if user_q_low in domanda_low or domanda_low in user_q_low:
            score += 0.6

        # match keyword
        for kw in keywords:
            if kw.lower() in user_q_low:
                score += 0.3

        # family hint
        if "ctf" in user_q_low and item.get("family", "").lower() == "ctf":
            score += 0.15
        if "ctl" in user_q_low and "ctl" in item.get("family", "").lower():
            score += 0.15
        if "ctcem" in user_q_low and item.get("family", "").lower() == "ctcem":
            score += 0.15
        if "vcem" in user_q_low and item.get("family", "").lower() == "vcem":
            score += 0.15
        if "p560" in user_q_low and item.get("family", "").lower() == "p560":
            score += 0.15

        if score > best_score:
            best_score = score
            best = item

    return best, best_score

# 7. endpoint Q/A
@app.post("/qa/ask", response_model=AskResponse)
def qa_ask(req: AskRequest):
    q = req.question.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Question is empty")

    item, score = find_best_match(q)
    if not item:
        # fallback se non trova niente
        return AskResponse(
            answer="Non ho trovato una risposta adeguata in Tecnaria Gold. Specifica meglio la famiglia (CTF, CTL, CTCEM, VCEM, P560) o il problema di posa.",
            score=0.0,
            family=None,
            source_id=None,
        )

    answer = item.get("risposta") or item.get("answer") or "Risposta non disponibile."
    family = item.get("family")
    source_id = item.get("id")

    return AskResponse(
        answer=answer,
        score=round(score, 3),
        family=family,
        source_id=source_id,
    )
