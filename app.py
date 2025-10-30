from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path
import json

app = FastAPI(title="Tecnaria Sinapsi — Q/A", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_PATH = Path("static/data/tecnaria_gold.json")
if DATA_PATH.exists():
    with DATA_PATH.open("r", encoding="utf-8") as f:
        TECNARIA_DATA = json.load(f)
    ITEMS = TECNARIA_DATA.get("items", [])
else:
    ITEMS = []

class AskRequest(BaseModel):
    question: str

class AskResponse(BaseModel):
    answer: str
    score: float | None = None
    family: str | None = None
    source_id: str | None = None
    mood: str | None = None

# ---------------------------
# 1. CAMILLA: capisce l’intento
# ---------------------------
def camilla_oracle(question: str) -> dict:
    q = question.lower()

    # default
    mood = "default"
    need_gold = False
    family_hint = None

    # cerca la famiglia
    if "ctf" in q or "p560" in q or "lamiera" in q or "chiodi" in q:
        family_hint = "CTF"
    elif "ctl" in q or "lamellare" in q:
        family_hint = "CTL"
    elif "ctcem" in q:
        family_hint = "CTCEM"
    elif "vcem" in q:
        family_hint = "VCEM"
    elif "diapason" in q:
        family_hint = "DIAPASON"
    elif "gts" in q:
        family_hint = "GTS"

    # casi di allerta / cantiere bloccato
    if "sbaglio" in q or "errore" in q or "strapp" in q or "rotto" in q or "blocca" in q:
        mood = "alert"
        need_gold = True

    # casi di posa vera
    elif "come si posa" in q or "come devo posare" in q or "posa" in q:
        mood = "explanatory"
        need_gold = True

    # casi di confronto
    elif "differenza" in q or "vs" in q or "meglio" in q:
        mood = "comparative"
        need_gold = True

    # casi commerciali
    elif "dove si trova tecnaria" in q or "sede tecnaria" in q or "indirizzo tecnaria" in q:
        mood = "institutional"
        need_gold = False  # qui basta risposta pulita

    # casi “ho posato ma non sono sicuro”
    elif "non sono sicuro" in q or "prima del getto" in q or "controllo" in q:
        mood = "check"
        need_gold = True

    return {
        "mood": mood,
        "need_gold": need_gold,
        "family_hint": family_hint
    }

# ---------------------------
# 2. FORMATTATORE GOLD
# ---------------------------
def format_gold(base_answer: str, mood: str, family: str | None, question: str) -> str:
    """
    Prende la risposta del JSON e la veste con lo stile narrativo.
    Se la risposta è già lunga, la lascia com'è.
    """
    # se già è lunga/narrativa non la tocchiamo
    if len(base_answer) > 450:
        return base_answer

    # blocco comune per famiglie
    family_block = ""
    if family in ("CTF", "CTL", "CTCEM", "VCEM", "P560"):
        family_block = f"\n\n**Famiglia coinvolta:** {family}"

    if mood == "alert":
        return (
            f"⚠️ ATTENZIONE\n{base_answer}\n\n"
            f"**Checklist immediata:**\n"
            f"- ferma il getto / la posa\n"
            f"- controlla utensile (potenza P560 / avvitatore)\n"
            f"- verifica che stai usando la famiglia corretta (CTF acciaio, CTL legno, CTCEM/VCEM laterocemento)\n"
            f"- annota su verbale DL con foto\n"
            f"{family_block}"
        )

    if mood == "explanatory":
        return (
            f"**Contesto**\nStai chiedendo una posa reale di cantiere. Ti do la procedura completa.\n\n"
            f"**Procedura Tecnaria suggerita**\n{base_answer}\n\n"
            f"**Note di cantiere:**\n"
            f"- rete sempre a metà spessore\n"
            f"- calcestruzzo ≥ C25/30 con vibrazione moderata\n"
            f"- se c'è lamiera: deve essere serrata\n"
            f"{family_block}"
        )

    if mood == "comparative":
        return (
            f"🔍 **Confronto richiesto**\n{base_answer}\n\n"
            f"**Regola veloce:**\n"
            f"- legno → CTL / CTL MAXI\n"
            f"- laterocemento → CTCEM / VCEM (a secco, con foro)\n"
            f"- acciaio → CTF + P560\n"
        )

    if mood == "check":
        return (
            f"**Check pre-getto / pre-consegna**\n{base_answer}\n\n"
            f"**Ricorda:** se manca uno di questi controlli → rinvia il getto e ripristina."
        )

    # institutional o default
    return base_answer

# ---------------------------
# 3. RICERCA NEL JSON (come prima)
# ---------------------------
def find_best_match(user_q: str, family_hint: str | None = None):
    user_q_low = user_q.lower()
    best = None
    best_score = 0.0

    for item in ITEMS:
        domanda = item.get("domanda", "") or item.get("question", "")
        domanda_low = domanda.lower()
        trigger = item.get("trigger", {})
        keywords = trigger.get("keywords", [])
        item_family = item.get("family", "")

        score = 0.0

        # match diretto
        if user_q_low in domanda_low or domanda_low in user_q_low:
            score += 0.6

        # match keyword
        for kw in keywords:
            if kw.lower() in user_q_low:
                score += 0.25

        # family hint da Camilla
        if family_hint and item_family.lower() == family_hint.lower():
            score += 0.2

        if score > best_score:
            best_score = score
            best = item

    return best, best_score

# ---------------------------
# 4. ENDPOINT
# ---------------------------

@app.get("/health")
def health():
    return {"status": "ok", "items_loaded": len(ITEMS)}

@app.post("/qa/ask", response_model=AskResponse)
def qa_ask(req: AskRequest):
    q = req.question.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Question is empty")

    # 1) Camilla capisce la domanda
    cam = camilla_oracle(q)

    # 2) Sinapsi cerca nel JSON
    item, score = find_best_match(q, cam.get("family_hint"))

    if not item:
        return AskResponse(
            answer="Non ho trovato una risposta adeguata in Tecnaria Gold. Specifica la famiglia (CTF, CTL, CTCEM, VCEM, P560) o il problema (posa, errore, dopo getto).",
            score=0.0,
            family=None,
            source_id=None,
            mood=cam.get("mood"),
        )

    base_answer = item.get("risposta") or item.get("answer") or "Risposta non disponibile."
    family = item.get("family")
    source_id = item.get("id")

    # 3) se Camilla dice “serve gold”, lo vestiamo
    if cam.get("need_gold", False):
        final_answer = format_gold(base_answer, cam.get("mood"), family, q)
    else:
        final_answer = base_answer

    return AskResponse(
        answer=final_answer,
        score=round(score, 3),
        family=family,
        source_id=source_id,
        mood=cam.get("mood"),
    )
