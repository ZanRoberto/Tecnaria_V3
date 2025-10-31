# app.py
# TECNARIA_GOLD — Sinapsi + Camilla + Override Killer + UI
# unico file dati: static/data/tecnaria_gold.json

import os
import json
import unicodedata
import re
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel

# -------------------------------------------------------
# CONFIG
# -------------------------------------------------------
DATA_PATH = os.getenv("TECNARIA_DATA_PATH", "static/data/tecnaria_gold.json")

app = FastAPI(
    title="Tecnaria Sinapsi — Q/A",
    version="1.0.0",
    description="Router Q/A per prodotti e servizi Tecnaria (CTF, CTL, CTL MAXI, CTCEM, VCEM, P560, DIAPASON, GTS, ACCESSORI, COMM, CONFRONTO, PROBLEMATICHE, KILLER)."
)

# -------------------------------------------------------
# MODELLI
# -------------------------------------------------------
class AskRequest(BaseModel):
    question: str

class AskResponse(BaseModel):
    answer: str
    score: float
    family: str
    mood: str = "default"
    intent: str = "descrittivo"
    target: Optional[str] = None

# -------------------------------------------------------
# UTILITY
# -------------------------------------------------------
def normalize(text: str) -> str:
    if not text:
        return ""
    text = text.strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\s+", " ", text)
    return text

# -------------------------------------------------------
# CARICAMENTO DATI
# -------------------------------------------------------
def load_data(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"File dati non trovato: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

try:
    DATA = load_data(DATA_PATH)
    ITEMS: List[Dict[str, Any]] = DATA.get("items", [])
    META: Dict[str, Any] = DATA.get("_meta", {})
except Exception as e:
    print(f"[ERRORE] Caricamento dati: {e}")
    DATA = {"items": [], "_meta": {}}
    ITEMS = []
    META = {}

# -------------------------------------------------------
# OVERRIDE KILLER (PRIMA DI TUTTO)
# -------------------------------------------------------
OVERRIDE_RULES = [
    {
        "id": "OVR-CTF-CHIODATRICE-001",
        "family": "CTF",
        "patterns": [
            r"chiodatrice( a sparo)?( generica)?",
            r"normale chiodatrice",
            r"posare .*ctf.* con chiodatrice",
            r"sparare ctf senza p560"
        ],
        "answer": "⚠️ **NO.** I connettori **CTF Tecnaria** vanno posati **solo** con chiodatrice **SPIT P560** dotata di **kit/adattatore Tecnaria** e con **2 chiodi HSBR14 per connettore**.\n\n**Perché no chiodatrici generiche?**\n- non garantiscono energia di sparo costante;\n- non assicurano perpendicolarità;\n- con lamiera non serrata causano rimbalzo e chiodi non a filo piastra.\n\n**Procedura corretta (riassunto)**: serrare lamiera → appoggiare CTF in squadra → sparare con P560 + kit → controllare teste a filo (±0,5 mm).",
        "mood": "alert",
        "intent": "errore",
        "priority": 100
    },
    {
        "id": "OVR-P560-TARATURA-001",
        "family": "P560",
        "patterns": [
            r"sbaglio se taro la p560",
            r"tarare la p560 con un solo tiro",
            r"p560.*1 tiro",
            r"p560.*un tiro"
        ],
        "answer": "⚠️ **ATTENZIONE**\nSì, è un errore tarare la P560 con un solo tiro. La procedura Tecnaria prevede **2–3 tiri di prova consecutivi** su supporto equivalente e con le stesse cartucce. Verifica che le teste HSBR14 siano **a filo piastra (±0,5 mm)** e registra la taratura sul verbale di cantiere.",
        "mood": "alert",
        "intent": "errore",
        "priority": 100
    },
    {
        "id": "OVR-VCEM-NOP560-001",
        "family": "VCEM",
        "patterns": [
            r"p560.*vcem",
            r"usare la p560 per i vcem",
            r"posso usare la p560 per vcem",
            r"pistola.*vcem"
        ],
        "answer": "🔴 **NO.** I **VCEM** non si fissano con P560. Sono connettori meccanici per laterocemento con foro piccolo (Ø8–9 mm) e si avvitano. La P560 è esclusa.\n\n**Procedura VCEM**: foro → pulizia → avvitatura → rete a metà → CLS ≥ C25/30.",
        "mood": "alert",
        "intent": "errore",
        "priority": 100
    }
]

def find_override(q: str) -> Optional[dict]:
    qn = normalize(q)
    matches = []
    for rule in OVERRIDE_RULES:
        for pat in rule.get("patterns", []):
            try:
                if re.search(pat, qn, flags=re.IGNORECASE):
                    matches.append((rule.get("priority", 50), rule))
                    break
            except re.error:
                continue
    if not matches:
        return None
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]

# -------------------------------------------------------
# CAMILLA — DETECTION INTENTO
# -------------------------------------------------------
def detect_intent(q: str) -> str:
    qn = normalize(q)
    if qn.startswith("mi spieghi") or "spiegami" in qn:
        return "intro"
    if "sbaglio se" in qn or "errore" in qn or "non aderisce" in qn or "non funziona" in qn or "non spara" in qn:
        return "errore"
    if "differenza" in qn or " vs " in qn or "confronto" in qn or "meglio" in qn:
        return "confronto"
    if "codici" in qn or "ordine" in qn or "come ordino" in qn:
        return "commerciale"
    return "descrittivo"

# -------------------------------------------------------
# SINAPSI GREZZA
# -------------------------------------------------------
def base_candidates(user_q: str) -> List[Any]:
    qn = normalize(user_q)
    tokens = set(qn.split())
    cands = []
    for item in ITEMS:
        domanda = normalize(item.get("domanda", ""))
        family = normalize(item.get("family", ""))
        trig = item.get("trigger", {})
        kw = [normalize(k) for k in trig.get("keywords", [])]
        score = 0.0

        if qn == domanda:
            score += 3.0

        for t in tokens:
            if t and t in domanda:
                score += 0.4
            if t and t in family:
                score += 0.3
            if any(t in k for k in kw):
                score += 0.4

        score += float(trig.get("peso", 0.0)) * 0.5

        if score > 0:
            cands.append((score, item))

    if not cands:
        for it in ITEMS:
            cands.append((0.1, it))

    cands.sort(key=lambda x: x[0], reverse=True)
    return cands

# -------------------------------------------------------
# CAMILLA — RIPESO
# -------------------------------------------------------
def camilla_rescore(user_q: str, intent: str, candidates: List[Any]) -> List[Any]:
    qn = normalize(user_q)
    rescored = []
    for base_score, item in candidates:
        bonus = 0.0
        dom = normalize(item.get("domanda", ""))
        fam = item.get("family", "")
        trig = item.get("trigger", {})
        trig_peso = float(trig.get("peso", 0.0))

        if dom == qn:
            bonus += 2.5

        if intent == "intro":
            if dom.startswith("mi spieghi") or trig_peso >= 0.95:
                bonus += 1.5

        if intent == "errore":
            if "sbaglio" in dom or "errore" in dom or fam in ("KILLER", "PROBLEMATICHE"):
                bonus += 1.5

        if intent == "confronto" and fam == "CONFRONTO":
            bonus += 1.4

        if intent == "commerciale" and (fam == "COMM" or "codici" in dom or "ordine" in dom):
            bonus += 1.4

        if "p560" in qn and fam == "P560":
            bonus += 1.0
        if "ctf" in qn and fam == "CTF":
            bonus += 1.0
        if "ctl maxi" in qn and fam == "CTL MAXI":
            bonus += 1.0
        if "ctl" in qn and fam == "CTL":
            bonus += 0.8
        if "ctcem" in qn and fam == "CTCEM":
            bonus += 0.8
        if "vcem" in qn and fam == "VCEM":
            bonus += 0.8

        bonus += trig_peso * 0.8

        rescored.append((base_score + bonus, item))

    rescored.sort(key=lambda x: x[0], reverse=True)
    return rescored

# -------------------------------------------------------
# FORMAT GOLD
# -------------------------------------------------------
def format_gold(item: Dict[str, Any], intent: str) -> str:
    testo = item.get("risposta", "").strip()
    fam = item.get("family", "")
    if "\n" in testo:
        return testo
    return f"**{fam}**\n{testo}"

# -------------------------------------------------------
# ENDPOINTS
# -------------------------------------------------------
@app.get("/health")
def health():
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

@app.post("/qa/ask", response_model=AskResponse)
def qa_ask(req: AskRequest):
    q = req.question
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Domanda vuota")

    # 0) override killer
    override = find_override(q)
    if override:
        return AskResponse(
            answer=override["answer"],
            score=float(override.get("priority", 100)),
            family=override.get("family", "COMM"),
            mood=override.get("mood", "alert"),
            intent=override.get("intent", "errore"),
            target=override.get("family", "COMM")
        )

    # 1) intento
    intent = detect_intent(q)

    # 2) candidati
    base = base_candidates(q)

    # 3) ripeso
    rescored = camilla_rescore(q, intent, base)

    best_score, best_item = rescored[0]
    answer = format_gold(best_item, intent)
    family = best_item.get("family", "COMM")

    mood = "default"
    if intent == "errore" or family in ("KILLER", "PROBLEMATICHE"):
        mood = "alert"

    return AskResponse(
        answer=answer,
        score=round(float(best_score), 3),
        family=family,
        mood=mood,
        intent=intent,
        target=family
    )

@app.get("/ui")
def ui():
    # interfaccia semplice stile Tecnaria
    html = """
    <!doctype html>
    <html lang="it">
    <head>
      <meta charset="utf-8" />
      <title>Tecnaria Sinapsi — Q/A</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <style>
        body {
          margin: 0;
          font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: #f5f5f5;
        }
        header {
          background: linear-gradient(90deg, #ff7a00 0%, #000 70%);
          color: #fff;
          padding: 18px 32px 12px 32px;
        }
        .title {
          font-size: 1.4rem;
          font-weight: 600;
        }
        .subtitle {
          font-size: 0.85rem;
          opacity: 0.8;
        }
        .container {
          display: flex;
          gap: 20px;
          padding: 18px 32px;
        }
        .left {
          flex: 0 0 280px;
        }
        .right {
          flex: 1;
        }
        .pill {
          background: #fff;
          border: 1px solid rgba(0,0,0,0.05);
          border-left: 4px solid #ff7a00;
          border-radius: 10px;
          padding: 10px 12px;
          margin-bottom: 10px;
          cursor: pointer;
          font-size: 0.82rem;
        }
        .card {
          background: #fff;
          border-radius: 12px;
          box-shadow: 0 4px 16px rgba(0,0,0,0.04);
          padding: 20px;
        }
        #question {
          width: 100%;
          padding: 16px 16px;
          border-radius: 12px;
          border: 1px solid rgba(0,0,0,0.08);
          font-size: 1rem;
          margin-bottom: 12px;
        }
        #sendBtn {
          background: #ff7a00;
          border: none;
          color: #fff;
          padding: 10px 16px;
          border-radius: 10px;
          cursor: pointer;
          font-weight: 600;
        }
        #answerBox {
          margin-top: 14px;
          white-space: pre-wrap;
          line-height: 1.4;
          font-size: 0.9rem;
        }
        .badge {
          display: inline-block;
          background: rgba(255,122,0,0.12);
          color: #ff7a00;
          padding: 2px 8px;
          border-radius: 999px;
          font-size: 0.7rem;
          margin-right: 6px;
        }
      </style>
    </head>
    <body>
      <header>
        <div class="title">Tecnaria Sinapsi — Q/A</div>
        <div class="subtitle">CTF · CTL · CTL MAXI · CTCEM · VCEM · P560 · DIAPASON · GTS · ACCESSORI · COMM · CONFRONTO</div>
      </header>
      <div class="container">
        <div class="left">
          <div class="pill" onclick="fill('Mi spieghi la P560?')">Mi spieghi la P560?</div>
          <div class="pill" onclick="fill('Come si posano i CTF su lamiera grecata?')">CTF su lamiera grecata</div>
          <div class="pill" onclick="fill('Posso usare la P560 per i VCEM?')">P560 su VCEM</div>
          <div class="pill" onclick="fill('Qual è la differenza tra CTL e CTL MAXI?')">CTL vs CTL MAXI</div>
          <div class="pill" onclick="fill('Mi dai i codici dei connettori CTF?')">Codici CTF</div>
          <div class="pill" onclick="fill('con riferimento ai connettori CTF Tecnaria si possono posare i connettori usando una normale chiodatrice a sparo?')">CTF con chiodatrice normale</div>
        </div>
        <div class="right">
          <div class="card">
            <input id="question" placeholder="Fai una domanda su un prodotto Tecnaria…" />
            <button id="sendBtn" onclick="ask()">Chiedi a Sinapsi</button>
            <div id="answerBox"></div>
          </div>
        </div>
      </div>
      <script>
        function fill(t) {
          document.getElementById('question').value = t;
          ask();
        }
        async function ask() {
          const q = document.getElementById('question').value;
          if (!q) return;
          const res = await fetch('/qa/ask', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question: q })
          });
          const data = await res.json();
          const box = document.getElementById('answerBox');
          if (data && data.answer) {
            box.innerHTML = '<div class="badge">'+ (data.family || '') +'</div>' +
                            '<div class="badge">'+ (data.intent || '') +'</div>' +
                            (data.mood === 'alert' ? '<div class="badge" style="background:#ffe9e9;color:#b00020;">ALERT</div>' : '') +
                            '<p style="margin-top:10px;">' + data.answer.replace(/\\n/g, '<br/>') + '</p>';
          } else {
            box.innerText = 'Nessuna risposta';
          }
        }
      </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)
