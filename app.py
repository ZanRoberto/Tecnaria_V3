from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path
import json

app = FastAPI(title="Tecnaria Sinapsi — Q/A", version="1.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------
# 1) CARICAMENTO DATI
# -------------------------------------------------
DATA_PATH = Path("static/data/tecnaria_gold.json")

ITEMS: list[dict] = []
META: dict = {}

if DATA_PATH.exists():
    with DATA_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    # il tuo file ha questa forma: { "_meta": {...}, "items": [ ... ] }
    META = raw.get("_meta", {})
    ITEMS = raw.get("items", [])
else:
    # se proprio non c'è, non esplode: parte vuoto
    META = {"version": "EMPTY"}
    ITEMS = []

# -------------------------------------------------
# 2) MODELLI
# -------------------------------------------------
class AskRequest(BaseModel):
    question: str

class AskResponse(BaseModel):
    answer: str
    score: float | None = None
    family: str | None = None
    source_id: str | None = None
    mood: str | None = None

# -------------------------------------------------
# 3) HOME (per non avere 503)
# -------------------------------------------------
@app.get("/")
def root():
    return {
        "service": "Tecnaria Sinapsi — Q/A",
        "status": "ok",
        "items_loaded": len(ITEMS),
        "meta": META,
        "endpoints": {
            "health": "/health",
            "ask": "/qa/ask",
            "docs": "/docs"
        }
    }

# -------------------------------------------------
# 4) CAMILLA: capisce intenzione
# -------------------------------------------------
def camilla_oracle(question: str) -> dict:
    q = question.lower()
    mood = "default"
    need_gold = False
    family_hint = None

    # famiglie
    if "ctf" in q or "p560" in q or "lamiera" in q or "chiod" in q:
        family_hint = "CTF"
    elif "ctl" in q and "maxi" in q:
        family_hint = "CTL MAXI"
    elif "ctl" in q:
        family_hint = "CTL"
    elif "ctcem" in q:
        family_hint = "CTCEM"
    elif "vcem" in q:
        family_hint = "VCEM"
    elif "diapason" in q:
        family_hint = "DIAPASON"
    elif "gts" in q:
        family_hint = "GTS"
    elif "ordine" in q or "spedizione" in q or "azienda" in q or "sede" in q:
        family_hint = "COMM"

    # stati
    if "sbaglio" in q or "errore" in q or "si è strappata" in q or "blocca" in q or "non funziona" in q:
        mood = "alert"
        need_gold = True
    elif "come si posa" in q or "come devo posare" in q or "posa" in q:
        mood = "explanatory"
        need_gold = True
    elif "differenza" in q or "vs" in q or "meglio" in q or "confronto" in q:
        mood = "comparative"
        need_gold = True
    elif "non sono sicuro" in q or "prima del getto" in q or "check" in q:
        mood = "check"
        need_gold = True
    elif "dove si trova tecnaria" in q or "sede tecnaria" in q or "pecori giraldi" in q:
        mood = "institutional"

    return {
        "mood": mood,
        "need_gold": need_gold,
        "family_hint": family_hint
    }

# -------------------------------------------------
# 5) FORMATTORE GOLD (PERFEZIONE)
# -------------------------------------------------
def format_gold(base_answer: str, mood: str, family: str | None) -> str:
    # se hai già scritto tu in formato bello, non tocco
    if "**Contesto**" in base_answer or "Checklist" in base_answer or "⚠️" in base_answer:
        return base_answer

    blocco_fam = f"\n\n**Famiglia coinvolta:** {family}" if family else ""

    if mood == "alert":
        return (
            f"⚠️ ATTENZIONE\n{base_answer}\n\n"
            f"**Checklist immediata:**\n"
            f"- ferma posa / getto\n"
            f"- controlla utensile (P560 / avvitatore)\n"
            f"- verifica famiglia corretta (CTF acciaio, CTL legno, CTCEM/VCEM laterocemento)\n"
            f"- annota su verbale DL con foto\n"
            f"{blocco_fam}"
        )

    if mood == "explanatory":
        return (
            f"**Contesto**\nDomanda di posa reale in cantiere. Ti do la sequenza completa.\n\n"
            f"**Istruzioni di posa**\n{base_answer}\n\n"
            f"**Nota RAG**: risposte filtrate su prodotti Tecnaria, niente marchi terzi."
            f"{blocco_fam}"
        )

    if mood == "comparative":
        return (
            f"🔍 **Confronto richiesto**\n{base_answer}\n\n"
            f"**Regola veloce Tecnaria**\n"
            f"- acciaio → CTF + P560\n"
            f"- legno → CTL / CTL MAXI\n"
            f"- laterocemento → CTCEM / VCEM\n"
            f"{blocco_fam}"
        )

    if mood == "check":
        return (
            f"**Check pre-getto / pre-consegna**\n{base_answer}\n\n"
            f"Se manca un punto, rimanda il getto e ripristina."
            f"{blocco_fam}"
        )

    return base_answer

# -------------------------------------------------
# 6) MATCH SUL TUO FILE
# -------------------------------------------------
def find_best_match(user_q: str, family_hint: str | None = None):
    user_q_low = user_q.lower()
    best_item = None
    best_score = 0.0

    for item in ITEMS:
        domanda = item.get("domanda", "") or item.get("question", "")
        domanda_low = domanda.lower()
        trigger = item.get("trigger", {})
        keywords = trigger.get("keywords", [])
        item_family = item.get("family", "")

        score = 0.0

        if user_q_low == domanda_low:
            score = 1.0
        elif user_q_low in domanda_low or domanda_low in user_q_low:
            score += 0.65

        for kw in keywords:
            if kw.lower() in user_q_low:
                score += 0.2

        if family_hint and item_family.lower() == family_hint.lower():
            score += 0.15

        if score > best_score:
            best_score = score
            best_item = item

    return best_item, best_score

# -------------------------------------------------
# 7) ENDPOINTS
# -------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "items_loaded": len(ITEMS),
        "meta_version": META.get("version")
    }

@app.post("/qa/ask", response_model=AskResponse)
def qa_ask(req: AskRequest):
    q = req.question.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Question is empty")

    cam = camilla_oracle(q)
    item, score = find_best_match(q, cam.get("family_hint"))

    if not item:
        return AskResponse(
            answer="Non ho trovato una risposta in Tecnaria Gold. Specifica la famiglia (CTF, CTL, CTL MAXI, CTCEM, VCEM, P560) o il problema (posa, errore, dopo getto).",
            score=0.0,
            family=None,
            source_id=None,
            mood=cam.get("mood")
        )

    base_answer = item.get("risposta") or item.get("answer") or "Risposta non disponibile."
    family = item.get("family")
    source_id = item.get("id")

    if cam.get("need_gold", False):
        final_answer = format_gold(base_answer, cam.get("mood"), family)
    else:
        final_answer = base_answer

    return AskResponse(
        answer=final_answer,
        score=round(score, 3),
        family=family,
        source_id=source_id,
        mood=cam.get("mood")
    )
