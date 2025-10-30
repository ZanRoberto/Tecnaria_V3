from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path
from fastapi.responses import HTMLResponse
import json

# =========================================================
#  TECNARIA SINAPSI — Q/A (VERSIONE PERFEZIONE)
# =========================================================
app = FastAPI(title="Tecnaria Sinapsi — Q/A", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------
# 1. CARICAMENTO FILE TECNARIA GOLD
# ---------------------------------------------------------
DATA_PATH = Path("static/data/tecnaria_gold.json")
ITEMS: list[dict] = []
META: dict = {}

if DATA_PATH.exists():
    with DATA_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    META = raw.get("_meta", {})
    ITEMS = raw.get("items", [])
else:
    META = {"version": "EMPTY"}
    ITEMS = []

# ---------------------------------------------------------
# 2. MODELLI
# ---------------------------------------------------------
class AskRequest(BaseModel):
    question: str

class AskResponse(BaseModel):
    answer: str
    score: float | None = None
    family: str | None = None
    source_id: str | None = None
    mood: str | None = None

# ---------------------------------------------------------
# 3. HOME / HEALTH
# ---------------------------------------------------------
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
            "ui": "/ui",
            "docs": "/docs"
        }
    }

@app.get("/health")
def health():
    return {
        "status": "ok",
        "items_loaded": len(ITEMS),
        "meta_version": META.get("version")
    }

# ---------------------------------------------------------
# 4. CAMILLA — INTERPRETAZIONE DOMANDA
# ---------------------------------------------------------
def camilla_oracle(question: str) -> dict:
    q = question.lower()
    mood = "default"
    need_gold = False
    family_hint = None

    if "ctf" in q or "p560" in q or "lamiera" in q or "chiod" in q:
        family_hint = "CTF"
    elif "ctl maxi" in q:
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

    if "errore" in q or "sbaglio" in q or "rotto" in q or "strappata" in q or "blocca" in q:
        mood = "alert"; need_gold = True
    elif "come si posa" in q or "posa" in q or "posare" in q:
        mood = "explanatory"; need_gold = True
    elif "differenza" in q or "vs" in q or "meglio" in q or "confronto" in q:
        mood = "comparative"; need_gold = True
    elif "check" in q or "non sono sicuro" in q or "prima del getto" in q:
        mood = "check"; need_gold = True
    elif "tecnaria" in q or "pecori" in q or "bassano" in q:
        mood = "institutional"; need_gold = False

    return {"mood": mood, "need_gold": need_gold, "family_hint": family_hint}

# ---------------------------------------------------------
# 5. FORMATTORE GOLD (STILE PERFEZIONE)
# ---------------------------------------------------------
def format_gold(base_answer: str, mood: str, family: str | None) -> str:
    if any(k in base_answer for k in ["**Contesto**", "Checklist", "⚠️"]):
        return base_answer

    blocco_fam = f"\n\n**Famiglia coinvolta:** {family}" if family else ""

    if mood == "alert":
        return (
            f"⚠️ **ATTENZIONE**\n{base_answer}\n\n"
            f"**Checklist immediata:**\n"
            f"- Ferma la posa o il getto\n"
            f"- Controlla utensile (P560 / avvitatore)\n"
            f"- Verifica la famiglia corretta (CTF acciaio, CTL legno, CTCEM/VCEM laterocemento)\n"
            f"- Annotazione su verbale DL con foto\n"
            f"{blocco_fam}"
        )

    if mood == "explanatory":
        return (
            f"**Contesto**\nDomanda di posa reale in cantiere.\n\n"
            f"**Istruzioni di posa**\n{base_answer}\n\n"
            f"**Alternativa**\nSistema saldato o con staffe se non possibile fissaggio meccanico.\n\n"
            f"**Checklist**\n- rete metà spessore ✔︎\n- cls ≥ C25/30 ✔︎\n- lamiera serrata ✔︎\n\n"
            f"**Nota RAG**: risposte filtrate su prodotti Tecnaria, senza marchi terzi."
            f"{blocco_fam}"
        )

    if mood == "comparative":
        return (
            f"🔍 **Confronto richiesto**\n{base_answer}\n\n"
            f"**Regola Tecnaria**\n"
            f"- Acciaio → CTF + P560\n"
            f"- Legno → CTL / CTL MAXI\n"
            f"- Laterocemento → CTCEM / VCEM\n"
            f"{blocco_fam}"
        )

    if mood == "check":
        return (
            f"**Check pre-getto / pre-consegna**\n{base_answer}\n\n"
            f"Se manca un controllo → rinvia il getto e ripristina."
            f"{blocco_fam}"
        )

    return base_answer

# ---------------------------------------------------------
# 6. MOTORE DI RICERCA (SINAPSI)
# ---------------------------------------------------------
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
            score += 0.6

        for kw in keywords:
            if kw.lower() in user_q_low:
                score += 0.25

        if family_hint and item_family.lower() == family_hint.lower():
            score += 0.15

        if score > best_score:
            best_score = score
            best_item = item

    return best_item, best_score

# ---------------------------------------------------------
# 7. ENDPOINT Q/A
# ---------------------------------------------------------
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

    final_answer = (
        format_gold(base_answer, cam.get("mood"), family)
        if cam.get("need_gold", False)
        else base_answer
    )

    return AskResponse(
        answer=final_answer,
        score=round(score, 3),
        family=family,
        source_id=source_id,
        mood=cam.get("mood")
    )

# ---------------------------------------------------------
# 8. INTERFACCIA /ui (STILE TECNARIA)
# ---------------------------------------------------------
@app.get("/ui", response_class=HTMLResponse)
def ui():
    return """
    <!DOCTYPE html>
    <html lang="it">
    <head>
        <meta charset="utf-8"/>
        <title>Tecnaria Sinapsi — Q/A</title>
        <style>
            body {font-family: Arial, sans-serif; background:#f6f2ee; margin:0;}
            .wrap{max-width:900px;margin:40px auto;background:#fff;border-radius:16px;
                  padding:28px 32px;box-shadow:0 10px 40px rgba(0,0,0,0.06);}
            h1{color:#c6511d;margin-top:0;}
            input[type=text]{width:100%;padding:12px;font-size:16px;border:1px solid #ddd;
                             border-radius:8px;margin-top:10px;margin-bottom:16px;}
            button{background:#000;color:#fff;padding:10px 18px;border:none;border-radius:8px;
                    cursor:pointer;font-size:15px;}
            pre{background:#faf4ef;padding:16px;border-radius:10px;white-space:pre-wrap;}
            .badge{background:#eee;display:inline-block;padding:2px 8px;border-radius:6px;
                   margin-right:6px;font-size:12px;}
        </style>
    </head>
    <body>
        <div class="wrap">
            <h1>Tecnaria Sinapsi — Q/A</h1>
            <p>Domande su CTF, CTL, CTCEM, VCEM, P560, Diapason, GTS e accessori. Stile <b>PERFEZIONE</b>.</p>
            <input id="q" type="text" placeholder="Es. Sbaglio se taro la P560 con un solo tiro?"/>
            <button onclick="ask()">Chiedi a Sinapsi</button>
            <div id="res" style="margin-top:24px;"></div>
        </div>
        <script>
        async function ask(){
            const q=document.getElementById('q').value;
            const resEl=document.getElementById('res');
            resEl.innerHTML="Sto chiedendo a Sinapsi...";
            const resp=await fetch('/qa/ask',{
                method:'POST',headers:{'Content-Type':'application/json'},
                body:JSON.stringify({question:q})
            });
            const data=await resp.json();
            resEl.innerHTML=`
                <div class="badge">Famiglia: ${data.family||'-'}</div>
                <div class="badge">Score: ${data.score||'-'}</div>
                <div class="badge">Mood: ${data.mood||'-'}</div>
                <pre>${data.answer}</pre>
            `;
        }
        </script>
    </body>
    </html>
    """
