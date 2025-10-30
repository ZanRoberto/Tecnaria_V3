# app.py
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import json
import re
from typing import List, Dict, Any, Optional

# =========================
# CONFIG
# =========================
DATA_FILE = Path("static/data/tecnaria_gold.json")  # nome fisso come hai detto tu

# =========================
# LOAD DATA
# =========================
def load_tecnaria_data(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"File JSON non trovato: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # normalizzo
    data.setdefault("items", [])
    return data

try:
    TECNARIA_DATA = load_tecnaria_data(DATA_FILE)
    TECNARIA_ITEMS: List[Dict[str, Any]] = TECNARIA_DATA.get("items", [])
except Exception as e:
    # se proprio all'avvio non c'è il file, parto lo stesso ma avviso
    TECNARIA_DATA = {"_meta": {}, "items": []}
    TECNARIA_ITEMS = []
    print(f"[WARN] impossibile caricare {DATA_FILE}: {e}")

# =========================
# FASTAPI APP
# =========================
app = FastAPI(
    title="Tecnaria Sinapsi — Q/A",
    description="Bot ufficiale Tecnaria (CTF, CTL/CTL MAXI, CTCEM/VCEM, P560, DIAPASON, GTS, ACCESSORI) — stile GOLD — RAG Tecnaria-only",
    version="1.0.0",
)

# CORS aperto (va bene per test, poi puoi chiuderlo)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# UTILS DI MATCHING
# =========================
def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())

def match_by_id(q: str) -> Optional[Dict[str, Any]]:
    q_norm = normalize(q)
    for item in TECNARIA_ITEMS:
        if normalize(item.get("id", "")) == q_norm:
            return item
    return None

def match_by_family(q: str) -> Optional[Dict[str, Any]]:
    q_norm = normalize(q)
    # se l'utente scrive solo "ctf" o "ctl maxi" ecc.
    for item in TECNARIA_ITEMS:
        fam = normalize(item.get("family", ""))
        if fam and fam in q_norm:
            return item
    return None

def match_by_triggers(q: str) -> Optional[Dict[str, Any]]:
    q_norm = normalize(q)
    best_item = None
    best_weight = 0.0
    for item in TECNARIA_ITEMS:
        trig = item.get("trigger", {})
        peso = trig.get("peso", 0)
        keywords = trig.get("keywords", [])
        for kw in keywords:
            if normalize(kw) in q_norm:
                # prendo quello col peso più alto
                if peso > best_weight:
                    best_weight = peso
                    best_item = item
    return best_item

def build_fallback(q: str) -> Dict[str, Any]:
    return {
        "family": "COMM",
        "domanda": "La tua domanda non ha trovato una corrispondenza diretta nel file Tecnaria.",
        "risposta": (
            "Non trovo un trigger GOLD per: «" + q + "».\n"
            "Verifica che la domanda riguardi una di queste famiglie: CTF, CTL, CTL MAXI, CTCEM, VCEM, P560, DIAPASON, GTS, ACCESSORI, CONFRONTO, PROBLEMATICHE, KILLER.\n"
            "Se è una domanda di cantiere speciale (foto, posa fatta male, saldature, lamiera non serrata), invia tutto a **info@tecnaria.com** con foto e metti nell’oggetto: «Richiesta verifica posa connettori Tecnaria – cantiere».\n"
            "Sede: Viale Pecori Giraldi, 55 – 36061 Bassano del Grappa (VI). Tel. +39 0424 502029."
        ),
        "matched": False
    }

# =========================
# ROUTES
# =========================
@app.get("/health")
def health():
    return {
        "status": "ok",
        "items": len(TECNARIA_ITEMS),
        "families": sorted(list({it.get("family", "") for it in TECNARIA_ITEMS if it.get("family")})),
        "source": str(DATA_FILE)
    }

@app.get("/")
def root():
    return {
        "app": "Tecnaria Sinapsi — Q/A",
        "message": "Usa POST /qa/ask con {\"question\": \"...\"}",
        "health": "/health",
        "doc": "/docs"
    }

@app.post("/qa/ask")
async def qa_ask(payload: Dict[str, Any], request: Request):
    """
    Payload atteso:
    {
        "question": "testo della domanda",
        "lang": "it"  (opzionale: per ora solo it → risponde it)
    }
    """
    question = payload.get("question", "")
    if not question:
        raise HTTPException(status_code=400, detail="Campo 'question' mancante")

    q_norm = normalize(question)

    # 1) match per id (se uno scrive COMM-0001)
    item = match_by_id(q_norm)
    if item:
        return {
            "matched": True,
            "match_type": "id",
            "family": item.get("family"),
            "domanda": item.get("domanda"),
            "risposta": item.get("risposta"),
            "trigger": item.get("trigger", {})
        }

    # 2) match per trigger/keywords (il caso principale)
    item = match_by_triggers(q_norm)
    if item:
        return {
            "matched": True,
            "match_type": "trigger",
            "family": item.get("family"),
            "domanda": item.get("domanda"),
            "risposta": item.get("risposta"),
            "trigger": item.get("trigger", {})
        }

    # 3) match per family nel testo
    item = match_by_family(q_norm)
    if item:
        return {
            "matched": True,
            "match_type": "family",
            "family": item.get("family"),
            "domanda": item.get("domanda"),
            "risposta": item.get("risposta"),
            "trigger": item.get("trigger", {})
        }

    # 4) Fallback GOLD
    return build_fallback(question)

# =========================
# RUN (solo locale)
# =========================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
