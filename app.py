# app.py
#
# TECNARIA_GOLD — Sinapsi + Camilla (ripeso intenzione)
# un solo file dati: static/data/tecnaria_gold.json
# endpoint: /health, /qa/ask, /ui, /docs

import json
import os
import unicodedata
from typing import List, Any, Dict, Optional

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
    """minuscole, niente accenti, spazi puliti"""
    if not text:
        return ""
    text = text.strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    while "  " in text:
        text = text.replace("  ", " ")
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
    DATA = {"items": [], "_meta": {}}
    ITEMS = []
    META = {}
    print(f"[ERRORE] Caricamento dati: {e}")

# -------------------------------------------------------
# CAMILLA — DETECTION INTENTO
# -------------------------------------------------------
def detect_intent(q: str) -> str:
    qn = normalize(q)
    if qn.startswith("mi spieghi") or "spiegami" in qn:
        return "intro"
    if "sbaglio se" in qn or "errore" in qn or "non aderisce" in qn or "non spara" in qn or "non funziona" in qn:
        return "errore"
    if "differenza" in qn or " vs " in qn or "confronto" in qn or "meglio" in qn:
        return "confronto"
    if "codici" in qn or "codice" in qn or "come ordino" in qn or "ordine" in qn:
        return "commerciale"
    return "descrittivo"

# -------------------------------------------------------
# MATCH DI BASE (SINAPSI GREZZO)
# -------------------------------------------------------
def base_candidates(user_q: str) -> List[Dict[str, Any]]:
    """
    Recupero semplice: cerca parole della domanda
    in domanda, family, keywords.
    """
    qn = normalize(user_q)
    tokens = set(qn.split())
    cands = []

    for item in ITEMS:
        domanda = normalize(item.get("domanda", ""))
        family = normalize(item.get("family", ""))
        trig = item.get("trigger", {})
        kw = [normalize(k) for k in trig.get("keywords", [])]

        score = 0.0

        # match diretto domanda
        if qn == domanda:
            score += 3.0

        # match parziale tokens
        for t in tokens:
            if t and t in domanda:
                score += 0.4
            if t and t in family:
                score += 0.3
            if any(t in k for k in kw):
                score += 0.4

        # peso trigger
        score += float(trig.get("peso", 0.0)) * 0.5

        if score > 0:
            cands.append((score, item))

    # se proprio non ha trovato niente, restituisco tutti con score minimo
    if not cands:
        for it in ITEMS:
            cands.append((0.1, it))

    # ordina decrescente (grezzo)
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

        # 1) match esatto testo
        if dom == qn:
            bonus += 2.5

        # 2) Intent specifici
        if intent == "intro":
            # “mi spieghi …” → prendi quelli nati per spiegare
            if dom.startswith("mi spieghi") or trig_peso >= 0.95:
                bonus += 1.5

        if intent == "errore":
            # prendi killer / problematiche
            if "sbaglio" in dom or "errore" in dom or fam in ("KILLER", "PROBLEMATICHE"):
                bonus += 1.5

        if intent == "confronto":
            if fam == "CONFRONTO":
                bonus += 1.4

        if intent == "commerciale":
            if fam == "COMM" or "codici" in dom or "ordine" in dom:
                bonus += 1.4

        # 3) famiglia esplicita nella domanda
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

        # 4) usa davvero il peso trigger
        bonus += trig_peso * 0.8

        rescored.append((base_score + bonus, item))

    # ordina per score finale
    rescored.sort(key=lambda x: x[0], reverse=True)
    return rescored

# -------------------------------------------------------
# FORMAT GOLD (stile tuo)
# -------------------------------------------------------
def format_gold(item: Dict[str, Any], intent: str) -> str:
    testo = item.get("risposta", "").strip()
    fam = item.get("family", "")
    dom = item.get("domanda", "")

    # se già è narrativo lo lasciamo
    if "\n" in testo and "**" in testo:
        return testo

    # fallback breve
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

    # 1) Camilla capisce cosa vuoi
    intent = detect_intent(q)

    # 2) Sinapsi prende i candidati grezzi
    base = base_candidates(q)

    # 3) Camilla li ripesa
    rescored = camilla_rescore(q, intent, base)

    # 4) prendo il migliore
    best_score, best_item = rescored[0]
    answer = format_gold(best_item, intent)
    family = best_item.get("family", "COMM")

    # mood
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
    html = """
    <html>
      <head><title>Tecnaria Sinapsi — Q/A</title></head>
      <body style="font-family: sans-serif; padding: 20px;">
        <h1>Tecnaria Sinapsi — Q/A</h1>
        <p>Prova una domanda:</p>
        <ul>
          <li>Mi spieghi la P560?</li>
          <li>Come si posano i CTF su lamiera grecata?</li>
          <li>Posso usare la P560 per i VCEM?</li>
          <li>Qual è la differenza tra CTL e CTL MAXI?</li>
          <li>Mi dai i codici dei connettori CTF?</li>
        </ul>
        <p>Vai su <a href="/docs">/docs</a> per provare l'API.</p>
      </body>
    </html>
    """
    return HTMLResponse(content=html)

# FastAPI già espone /docs


# -------------------------------------------------------
# NOTE PER RENDER
# -------------------------------------------------------
# gunicorn/uvicorn command deve puntare a:  app:app
# e il file DEVE chiamarsi app.py
# requirements.txt deve avere: fastapi==0.115.0, uvicorn==0.30.6, orjson==3.10.7, pydantic==2.8.0, gunicorn==21.2.0
