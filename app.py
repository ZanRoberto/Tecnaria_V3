from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from pathlib import Path
import json
import re

# =========================================================
# TECNARIA SINAPSI — Q/A
# Intenzione → Target (famiglia) → Ricerca pesata → Formato GOLD
# =========================================================
app = FastAPI(title="Tecnaria Sinapsi — Q/A", version="4.1.0")

# se True mostra intent/target/score (laboratorio)
LAB_MODE = True

# CORS (per UI browser)
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
# 3. UTILI
# =========================================================
def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()

def contains_any(text: str, words: list[str]) -> bool:
    t = text.lower()
    return any(w in t for w in words)

# =========================================================
# 4. RICONOSCIMENTO INTENZIONE (PEZZO CHIAVE)
# =========================================================
def detect_intent_and_target(question: str) -> dict:
    q = question.lower().strip()

    # target/famiglia
    target = None
    if "p560" in q or "p 560" in q or "chiodatrice" in q or "spit" in q:
        target = "P560"
    elif "ctf" in q:
        target = "CTF"
    elif "ctl maxi" in q:
        target = "CTL MAXI"
    elif "ctl" in q:
        target = "CTL"
    elif "ctcem" in q:
        target = "CTCEM"
    elif "vcem" in q:
        target = "VCEM"
    elif "diapason" in q:
        target = "DIAPASON"
    elif "gts" in q:
        target = "GTS"
    elif "dove si trova" in q or "pecori" in q or "bassano" in q:
        target = "COMM"

    # intenzione
    if any(k in q for k in ["mi spieghi", "parlami", "cos'è", "a cosa serve", "descrivi", "descrivimi", "spiegami"]):
        intent = "descrittivo"
    elif any(k in q for k in ["come si posa", "come si montano", "istruzioni", "posa", "montaggio", "come fissare"]):
        intent = "posa"
    elif any(k in q for k in ["sbaglio se", "posso usare", "è corretto se", "va bene se", "si può usare", "posso fissare"]):
        intent = "errore"
    elif any(k in q for k in ["differenza", "vs", "meglio", "confronto"]):
        intent = "comparativo"
    elif any(k in q for k in ["ordine", "spedizione", "consegna", "azienda", "telefono", "preventivo"]):
        intent = "commerciale"
    elif any(k in q for k in ["hanno saldato", "hanno già gettato", "dopo il getto", "si è staccato", "non aderisce"]):
        intent = "problematiche"
    else:
        intent = "generico"

    # mood di base
    if intent in ["errore", "problematiche"]:
        mood = "alert"
    elif intent == "posa":
        mood = "explanatory"
    elif intent == "comparativo":
        mood = "comparative"
    elif intent == "commerciale":
        mood = "institutional"
    else:
        mood = "default"

    return {
        "intent": intent,
        "target": target,
        "mood": mood
    }

# =========================================================
# 5. FORMATTORE GOLD (STILE PERFEZIONE)
# =========================================================
def format_gold(base_answer: str, mood: str, family: str | None) -> str:
    # se il testo è già formattato non tocchiamo
    if any(k in base_answer for k in ["**Contesto**", "**Istruzioni di posa**", "⚠️", "**Checklist**"]):
        return base_answer

    fam_block = f"\n\n**Famiglia coinvolta:** {family}" if family else ""

    if mood == "alert":
        return (
            f"⚠️ **ATTENZIONE**\n{base_answer}\n\n"
            f"**Checklist immediata**\n"
            f"- fermare posa / getto\n"
            f"- controllare utensile e accessori (P560, adattatore, HSBR14)\n"
            f"- verificare che la famiglia sia corretta (CTF, CTL, CTCEM/VCEM)\n"
            f"- documentare con foto e DL\n"
            f"{fam_block}"
        )

    if mood == "explanatory":
        return (
            f"**Contesto**\nDomanda di posa su prodotto Tecnaria.\n\n"
            f"**Istruzioni di posa**\n{base_answer}\n\n"
            f"**Alternativa**\nSe la posa meccanica non è possibile, valutare staffe o saldatura secondo indicazioni Tecnaria.\n\n"
            f"**Checklist**\n- rete a metà spessore ✔︎\n- cls ≥ C25/30 ✔︎\n- lamiera serrata ✔︎\n- niente resine dove non previste ✔︎\n\n"
            f"**Nota RAG**: risposta filtrata su prodotti Tecnaria.\n"
            f"{fam_block}"
        )

    if mood == "comparative":
        return (
            f"🔍 **Confronto richiesto**\n{base_answer}\n\n"
            f"**Regola Tecnaria**\n"
            f"- Acciaio → CTF + P560\n"
            f"- Legno → CTL / CTL MAXI\n"
            f"- Laterocemento → CTCEM / VCEM\n"
            f"{fam_block}"
        )

    if mood == "institutional":
        return base_answer + fam_block

    if mood == "problematiche":
        return (
            f"**Problematiche di posa**\n{base_answer}\n\n"
            f"**Azione consigliata**\n- sospendere il getto\n- ripristinare il fissaggio\n- informare la DL\n"
            f"{fam_block}"
        )

    return base_answer + fam_block

# =========================================================
# 6. MOTORE DI RICERCA PESATA
# =========================================================
def find_best_item(question: str, items: list[dict]) -> tuple[dict | None, float, dict]:
    ctx = detect_intent_and_target(question)
    intent = ctx["intent"]
    target = ctx["target"]
    mood = ctx["mood"]
    q = question.lower()

    best = None
    best_score = 0.0

    for item in items:
        item_q = (item.get("domanda") or item.get("question") or "").lower()
        item_fam = (item.get("family") or "").lower()
        trig = item.get("trigger", {})
        kws = [k.lower() for k in trig.get("keywords", [])]

        score = 0.0

        # 1) match famiglia/target
        if target and item_fam == target.lower():
            score += 0.4

        # 2) match intenzione
        if intent == "descrittivo" and any(w in item_q for w in ["cos'è", "a cosa serve", "descrizione", "p560", "connettore ctf"]):
            score += 0.4
        elif intent == "posa" and any(w in item_q for w in ["posa", "istruzioni", "come si posa", "montaggio"]):
            score += 0.35
        elif intent == "errore" and any(w in item_q for w in ["errore", "non usare", "non ammesso", "sbaglio se", "attenzione"]):
            score += 0.45
        elif intent == "comparativo" and item_fam == "confronto":
            score += 0.5
        elif intent == "commerciale" and item_fam == "comm":
            score += 0.5

        # 3) match parole chiave del trigger
        for kw in kws:
            if kw and kw in q:
                score += 0.15

        # 4) match testo generico
        if q in item_q or item_q in q:
            score += 0.2

        if score > best_score:
            best = item
            best_score = score

    return best, best_score, ctx

# =========================================================
# 7. ENDPOINTS
# =========================================================
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
            "docs": "/docs",
        }
    }

@app.get("/health")
def health():
    return {
        "status": "ok",
        "items_loaded": len(ITEMS),
        "meta_version": META.get("version")
    }

@app.post("/qa/ask")
def qa_ask(req: AskRequest):
    q = req.question.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Question is empty")

    item, score, ctx = find_best_item(q, ITEMS)

    if not item:
        if LAB_MODE:
            return {
                "answer": "Non ho trovato una risposta in Tecnaria Gold.",
                "score": 0.0,
                "family": None,
                "mood": ctx.get("mood"),
                "intent": ctx.get("intent"),
                "target": ctx.get("target")
            }
        return {"answer": "Non ho trovato una risposta in Tecnaria Gold."}

    base_answer = item.get("risposta") or item.get("answer") or "Risposta non disponibile."
    family = item.get("family")
    mood = ctx.get("mood", "default")

    # se la domanda era di posa/errore/comparativa → applica PERFEZIONE
    if ctx["intent"] in ["posa", "errore", "comparativo", "problematiche"]:
        final_answer = format_gold(base_answer, mood, family)
    else:
        final_answer = base_answer

    if LAB_MODE:
        return {
            "answer": final_answer,
            "score": round(min(score, 1.0), 3),
            "family": family,
            "mood": mood,
            "intent": ctx.get("intent"),
            "target": ctx.get("target")
        }

    # PRODUZIONE: solo testo
    return {"answer": final_answer}

# =========================================================
# 8. INTERFACCIA /ui
# =========================================================
@app.get("/ui", response_class=HTMLResponse)
def ui():
    # in lab mostriamo anche family/score/mood
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
            <p>Fai domande naturali: <i>“Mi spieghi la P560?”, “Sbaglio se taro la P560 con un solo tiro?”, “Come si posano i CTF su lamiera?”</i></p>
            <input id="q" type="text" placeholder="Es. Mi spieghi la P560?"/>
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
                <div class="badge">Intent: ${data.intent || '-'}</div>
                <div class="badge">Target: ${data.target || '-'}</div>
                <div class="res-box">${data.answer}</div>
            `;
        }
        </script>
    </body>
    </html>
    """
