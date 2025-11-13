import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os

app = FastAPI(title="Tecnaria Sinapsi – GOLD Engine")

# --------------------------------------------------
# CONFIG PATHS (tutto sotto /static/data)
# --------------------------------------------------
BASE_PATH = os.path.join("static", "data")

PATH_CONFIG = os.path.join(BASE_PATH, "config.runtime.json")
PATH_INDEX = os.path.join(BASE_PATH, "index_tecnaria.json")
PATH_ROUTER = os.path.join(BASE_PATH, "overlays", "tecnaria_router_gold.json")
PATH_CONTENT = os.path.join(BASE_PATH, "patches", "tecnaria_gold_consolidato.json")

# --------------------------------------------------
# LOADER UTILI
# --------------------------------------------------

def load_json(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"File mancante: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def normalize(text):
    return text.lower().strip()

# --------------------------------------------------
# LOAD ALL
# --------------------------------------------------

try:
    CONFIG = load_json(PATH_CONFIG)
    INDEX = load_json(PATH_INDEX)
    ROUTER = load_json(PATH_ROUTER)
    CONTENT = load_json(PATH_CONTENT)

    # mappa ID → item GOLD
    GOLD_MAP = {item["id"]: item for item in CONTENT.get("items", [])}

except Exception as e:
    print("FATAL ERROR DURING LOAD:", e)
    raise e

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------
# MODELLI INPUT/OUTPUT
# --------------------------------------------------

class Ask(BaseModel):
    q: str
    lang: str = "it"
    mode: str = "gold"

# --------------------------------------------------
# ENGINE
# --------------------------------------------------

def route_query(user_q: str):
    """Usa router + index per trovare l’ID GOLD corretto."""
    qn = normalize(user_q)

    # 1) ROUTER (regole secche)
    for rule in ROUTER.get("rules", []):
        tokens = rule.get("contains", [])
        if all(t in qn for t in tokens):
            return rule.get("id")

    # 2) INDEX (intent/keyword)
    for entry in INDEX.get("entries", []):
        intent = entry.get("intent", [])
        found = False
        for token in intent:
            if token.lower() in qn:
                found = True
                break
        if found:
            return entry.get("id")

    return None

def get_gold_answer(answer_id: str):
    item = GOLD_MAP.get(answer_id)
    if not item:
        return None
    gold = item.get("response_variants", {}).get("gold", {})
    return gold.get("it")

# --------------------------------------------------
# API ROUTES
# --------------------------------------------------

@app.get("/api/ping")
def ping():
    return {"ok": True, "message": "Tecnaria Sinapsi – GOLD attivo", "gold_mode": True}

@app.post("/api/ask")
def ask(req: Ask):
    if req.mode.lower() != "gold":
        raise HTTPException(status_code=400, detail="Modalità non supportata. Usa mode=gold.")

    # routing
    answer_id = route_query(req.q)
    if not answer_id:
        return {
            "ok": False,
            "answer": "Per questa domanda non trovo una risposta GOLD consolidata.",
            "family": "COMM",
            "id": "COMM-FALLBACK-NOANSWER-0001",
            "mode": "gold",
            "lang": req.lang
        }

    # risposta GOLD
    answer_txt = get_gold_answer(answer_id)
    if not answer_txt:
        return {
            "ok": False,
            "answer": "Risposta GOLD non trovata per ID specificato.",
            "family": "COMM",
            "id": "COMM-FALLBACK-NOANSWER-0002",
            "mode": "gold",
            "lang": req.lang
        }

    # family
    fam = GOLD_MAP[answer_id].get("family", "COMM")

    return {
        "ok": True,
        "answer": answer_txt,
        "family": fam,
        "id": answer_id,
        "mode": "gold",
        "lang": req.lang
    }

# --------------------------------------------------
# MAIN (solo locale)
# --------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
