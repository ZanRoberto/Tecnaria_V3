from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from pathlib import Path
import json
import re

# =========================================================
# TECNARIA SINAPSI — Q/A
# Gold style: Contesto → Istruzioni di posa → Alternativa → Checklist → Nota RAG
# =========================================================
app = FastAPI(title="Tecnaria Sinapsi — Q/A", version="4.0.0")

# CORS per UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================================================
# 1. CARICAMENTO DATI
# =========================================================
DATA_PATH = Path("static/data/tecnaria_gold.json")

ITEMS: list[dict] = []
META: dict = {}

if DATA_PATH.exists():
    with DATA_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    # tuo formato: {_meta:{...}, "items":[...]}
    META = raw.get("_meta", {})
    ITEMS = raw.get("items", [])
else:
    META = {"version": "EMPTY"}
    ITEMS = []

# =========================================================
# 2. MODELLI
# =========================================================
class AskRequest(BaseModel):
    question: str

class AskResponse(BaseModel):
    answer: str
    score: float
    family: str | None = None
    source_id: str | None = None
    mood: str | None = None


# =========================================================
# 3. UTILI PULIZIA TESTO
# =========================================================
def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()

def contains_any(text: str, words: list[str]) -> bool:
    t = text.lower()
    return any(w in t for w in words)


# =========================================================
# 4. CAMILLA — INTENZIONE / A2SR LEGGERO
# =========================================================
def camilla_oracle(question: str) -> dict:
    q = norm(question)

    # default
    mood = "default"
    need_gold = False
    family_hint = None
    intent = "generic"
    force_penalty_others = 0.0  # lo usiamo per abbassare roba che non c'entra

    # 4.1 domande chiaramente P560 / chiodatrice
    if (
        "p560" in q
        or "p 560" in q
        or "chiodatrice" in q
        or "spit" in q
        or "sparare" in q
        or "tiro di prova" in q
        or "cartucc" in q
    ):
        family_hint = "P560"
        need_gold = True
        # se sta chiedendo "posso usare un'altra" allora è alert
        if "posso" in q and ("normale" in q or "altra" in q or "diversa" in q):
            mood = "alert"
            intent = "attrezzatura_non_ammessa"
        else:
            mood = "explanatory"
            intent = "attrezzatura"
        # in ogni caso: le altre famiglie devono perdere punti
        force_penalty_others = 0.4
        return {
            "mood": mood,
            "need_gold": need_gold,
            "family_hint": family_hint,
            "intent": intent,
            "force_penalty_others": force_penalty_others
        }

    # 4.2 domande CTF / lamiera / chiodatura
    if "ctf" in q or "lamiera" in q or ("chiod" in q and "trave" in q):
        family_hint = "CTF"
        need_gold = True
        intent = "posa"
        if "errore" in q or "sbaglio" in q or "non aderente" in q or "non serrata" in q:
            mood = "alert"
            intent = "errore_lamiera"
        else:
            mood = "explanatory"

    # 4.3 CTL / legno
    elif "ctl maxi" in q:
        family_hint = "CTL MAXI"; need_gold = True; mood = "explanatory"; intent = "posa"
    elif "ctl" in q:
        family_hint = "CTL"; need_gold = True; mood = "explanatory"; intent = "posa"

    # 4.4 CTCEM / VCEM
    elif "ctcem" in q:
        family_hint = "CTCEM"; need_gold = True; mood = "explanatory"; intent = "posa"
    elif "vcem" in q:
        family_hint = "VCEM"; need_gold = True; mood = "explanatory"; intent = "posa"

    # 4.5 confronto
    if "vs" in q or "differenza" in q or "meglio" in q or "confronto" in q:
        mood = "comparative"; need_gold = True; intent = "confronto"

    # 4.6 commerciale / sede
    if "dove si trova tecnaria" in q or "pecori giraldi" in q or "bassano del grappa" in q:
        family_hint = "COMM"; mood = "institutional"; need_gold = False; intent = "azienda"

    # 4.7 problemi di posa generali
    if "cosa succede se" in q or "si è staccato" in q or "non aderisce" in q:
        mood = "alert"; need_gold = True; intent = "problematiche"

    return {
        "mood": mood,
        "need_gold": need_gold,
        "family_hint": family_hint,
        "intent": intent,
        "force_penalty_others": force_penalty_others
    }


# =========================================================
# 5. FORMATTORE GOLD (PERFEZIONE)
# =========================================================
def format_gold(base_answer: str, mood: str, family: str | None) -> str:
    # se è già in formato bello non tocco
    if any(k in base_answer for k in ["**Contesto**", "**Istruzioni di posa**", "⚠️", "**Checklist**"]):
        return base_answer

    blocco_fam = f"\n\n**Famiglia coinvolta:** {family}" if family else ""

    if mood == "alert":
        return (
            f"⚠️ **ATTENZIONE**\n{base_answer}\n\n"
            f"**Checklist immediata**\n"
            f"- ferma posa / getto\n"
            f"- controlla utensile e accessori (P560, adattatore, HSBR14)\n"
            f"- verifica che la famiglia sia quella corretta\n"
            f"- fotografa e informa DL\n"
            f"{blocco_fam}"
        )

    if mood == "explanatory":
        return (
            f"**Contesto**\nDomanda di posa su prodotto Tecnaria.\n\n"
            f"**Istruzioni di posa**\n{base_answer}\n\n"
            f"**Alternativa**\nSe non è possibile la posa meccanica, valutare sistema saldato o staffe Tecnaria.\n\n"
            f"**Checklist**\n- rete a metà spessore ✔︎\n- cls ≥ C25/30 ✔︎\n- lamiera serrata (se presente) ✔︎\n- niente resine sui CTCEM/VCEM ✔︎\n\n"
            f"**Nota RAG**: risposte filtrate su prodotti Tecnaria, no marchi terzi.\n"
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

    if mood == "institutional":
        return base_answer + blocco_fam

    if mood == "problematiche":
        return (
            f"**Problematiche di posa**\n{base_answer}\n\n"
            f"**Azione consigliata**\n- non procedere col getto finché il fissaggio non è conforme\n- documentare e inviare a Tecnaria\n"
            f"{blocco_fam}"
        )

    return base_answer + blocco_fam


# =========================================================
# 6. MOTORE DI RICERCA OLISTICO
#    (intenzione → famiglia → trigger → testo)
# =========================================================
def find_best_match(user_q: str, cam: dict):
    user_q_low = norm(user_q)
    family_hint = cam.get("family_hint")
    intent = cam.get("intent")
    force_penalty_others = cam.get("force_penalty_others", 0.0)

    best_item = None
    best_score = 0.0

    for item in ITEMS:
        domanda = item.get("domanda", "") or item.get("question", "")
        domanda_low = norm(domanda)
        trigger = item.get("trigger", {})
        keywords = trigger.get("keywords", [])
        item_family = item.get("family", "")

        score = 0.0

        # 1) se la domanda coincide o è molto simile
        if user_q_low == domanda_low:
            score = 1.0
        elif user_q_low in domanda_low or domanda_low in user_q_low:
            score += 0.6

        # 2) se le parole chiave combaciano
        for kw in keywords:
            if kw and kw.lower() in user_q_low:
                score += 0.25

        # 3) se la famiglia è quella suggerita da Camilla
        if family_hint and item_family.lower() == family_hint.lower():
            score += 0.25

        # 4) se è una domanda di "attrezzature non ammesse"
        if intent == "attrezzatura_non_ammessa":
            if contains_any(domanda_low, ["chiodatrice", "non ammesso", "non usare", "p560 obbligatoria"]):
                score += 0.4

        # 5) penalizza famiglie non richieste (serve per NON prendere CTF quando vuoi P560)
        if family_hint and item_family and family_hint.lower() != item_family.lower():
            score -= force_penalty_others

        if score > best_score:
            best_score = score
            best_item = item

    return best_item, best_score


# =========================================================
# 7. ENDPOINT Q/A
# =========================================================
@app.post("/qa/ask", response_model=AskResponse)
def qa_ask(req: AskRequest):
    q = req.question.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Question is empty")

    cam = camilla_oracle(q)
    item, score = find_best_match(q, cam)

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

    # se Camilla ha detto “serve gold”, lo formatto
    if cam.get("need_gold", False):
        final_answer = format_gold(base_answer, cam.get("mood"), family)
    else:
        final_answer = base_answer

    return AskResponse(
        answer=final_answer,
        score=round(max(min(score, 1.0), 0.0), 3),
        family=family,
        source_id=source_id,
        mood=cam.get("mood")
    )


# =========================================================
# 8. INTERFACCIA WEB /ui
# =========================================================
@app.get("/ui", response_class=HTMLResponse)
def ui():
    return """
    <!DOCTYPE html>
    <html lang="it">
    <head>
        <meta charset="utf-8"/>
        <title>Tecnaria Sinapsi — Q/A</title>
        <style>
            body {font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; background:#f6f2ee; margin:0;}
            .wrap{max-width:1000px;margin:40px auto;background:#fff;border-radius:16px;
                  padding:28px 32px;box-shadow:0 10px 40px rgba(0,0,0,0.06);}
            h1{color:#c6511d;margin-top:0;font-size:32px;}
            input[type=text]{width:100%;padding:14px;font-size:17px;border:1px solid #ddd;
                             border-radius:10px;margin-top:10px;margin-bottom:16px;background:#edf3ff;}
            button{background:#000;color:#fff;padding:11px 20px;border:none;border-radius:10px;
                    cursor:pointer;font-size:15px;}
            .res-box{
                background:#fff4ec;
                border-radius:16px;
                padding:18px 20px;
                margin-top:20px;
                border:1px solid #ffe5d2;
                white-space:pre-wrap;
                line-height:1.5;
            }
            .badge{background:#eee;display:inline-block;padding:4px 10px;border-radius:999px;
                   margin-right:6px;font-size:12px;}
        </style>
    </head>
    <body>
        <div class="wrap">
            <h1>Tecnaria Sinapsi — Q/A</h1>
            <p>Domande su <b>CTF, CTL/CTL MAXI, CTCEM, VCEM, P560, Diapason, GTS e accessori</b>. Stile <b>PERFEZIONE</b>.</p>
            <input id="q" type="text" placeholder="Es. Posso posare i CTF con una chiodatrice normale?"/>
            <button onclick="ask()">Chiedi a Sinapsi</button>
            <div id="res" style="margin-top:24px;"></div>
        </div>
        <script>
        async function ask(){
            const q = document.getElementById('q').value;
            const resEl = document.getElementById('res');
            resEl.innerHTML = "Sto chiedendo a Sinapsi...";
            const resp = await fetch('/qa/ask', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({question: q})
            });
            const data = await resp.json();
            resEl.innerHTML = `
                <div class="badge">Famiglia: ${data.family || '-'}</div>
                <div class="badge">Score: ${data.score || '-'}</div>
                <div class="badge">Mood: ${data.mood || '-'}</div>
                <div class="res-box">${data.answer}</div>
            `;
        }
        </script>
    </body>
    </html>
    """
