import json
from pathlib import Path
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ============================================================
# CONFIG
# ============================================================
DATA_PATH = Path("static/data/tecnaria_gold.json")
INDEX_PATH = Path("static/index.html")

app = FastAPI(title="Tecnaria GOLD — Sinapsi")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# CARICAMENTO DATASET (senza toccarlo)
# ============================================================
def load_dataset():
    if not DATA_PATH.exists():
        # file mancante: facciamo partire lo stesso
        return {
            "_meta": {
                "version": "NO-FILE",
                "note": "static/data/tecnaria_gold.json non trovato"
            },
            "items": []
        }
    try:
        with DATA_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if "items" not in data:
            data["items"] = []
        return data
    except json.JSONDecodeError as e:
        # file c'è ma è sporco → NON lo sistemiamo qui
        return {
            "_meta": {
                "version": "BROKEN",
                "note": f"JSON non valido: {e}"
            },
            "items": []
        }

DATA = load_dataset()

# ============================================================
# MODELLI
# ============================================================
class AskIn(BaseModel):
    question: str

class AskOut(BaseModel):
    answer: str
    source_id: str | None = None
    matched: float | None = None

# ============================================================
# DETECTOR DI FAMIGLIA (per non far rispondere COMM quando è legno)
# ============================================================
FAMILY_HINTS = {
    "CTL": [
        "legno", "trave in legno", "tavolato", "soletta su legno",
        "solaio in legno", "recupero legno", "connettore per legno",
        "ctl maxi", "tecnaria maxi", "maxi per legno"
    ],
    "CTF": [
        "lamiera", "p560", "sparato", "chiodatrice", "hsbr14",
        "trave in acciaio", "profilato", "ala della trave"
    ],
    "VCEM": [
        "laterocemento", "non posso sparare", "foro", "resina",
        "ctcem", "vbit", "foratura", "tassello"
    ]
}

def guess_family(question: str) -> str | None:
    q = question.lower()
    for fam, words in FAMILY_HINTS.items():
        for w in words:
            if w in q:
                return fam
    return None

# ============================================================
# MATCHER
# ============================================================
def score(question: str, item: dict) -> float:
    q = question.lower()
    trig = item.get("trigger") or {}
    peso = float(trig.get("peso", 0.5))
    kws = trig.get("keywords") or []
    hits = 0
    for kw in kws:
        if kw and kw.lower() in q:
            hits += 1
    fam = (item.get("family") or "").lower()
    fam_bonus = 0.05 if fam and fam in q else 0.0
    return peso + hits * 0.12 + fam_bonus

def find_best_answer(question: str):
    items = DATA.get("items", [])
    if not items:
        return ("Dataset vuoto o JSON non valido. Controlla tecnaria_gold.json.", None, 0.0)

    forced_family = guess_family(question)

    best = None
    best_s = -1.0

    # 1) provo dentro la famiglia forzata
    if forced_family:
        for it in items:
            fam = (it.get("family") or "").upper()
            if fam != forced_family.upper():
                continue
            s = score(question, it)
            if s > best_s:
                best_s = s
                best = it

    # 2) se non ho trovato nulla nella famiglia → cerco globalmente
    if not best:
        for it in items:
            s = score(question, it)
            if s > best_s:
                best_s = s
                best = it

    if not best:
        return ("Nessuna risposta trovata nel dataset Tecnaria GOLD.", None, 0.0)

    return (best.get("risposta", "…"), best.get("id"), best_s)

# ============================================================
# ROUTES
# ============================================================
@app.get("/")
def root():
    if INDEX_PATH.exists():
        return Response(content=INDEX_PATH.read_text(encoding="utf-8"), media_type="text/html")
    # fallback se non c'è l'html
    return {
        "status": "ok",
        "message": "Tecnaria GOLD pronto (manca static/index.html)",
        "items": len(DATA.get("items", []))
    }

@app.get("/ping")
def ping():
    return {
        "status": "ok",
        "message": "Tecnaria GOLD pronto",
        "items": len(DATA.get("items", [])),
        "note": DATA.get("_meta", {}).get("note", "")
    }

@app.post("/ask", response_model=AskOut)
def ask(body: AskIn):
    ans, src, m = find_best_answer(body.question)
    return AskOut(answer=ans, source_id=src, matched=m)

@app.post("/api/ask", response_model=AskOut)
def ask_alias(body: AskIn):
    ans, src, m = find_best_answer(body.question)
    return AskOut(answer=ans, source_id=src, matched=m)

# opzionale per run locale
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
