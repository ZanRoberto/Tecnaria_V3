import json
import re
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import os

# ===============================
#   SINAPSI TECH – GOLD ENGINE
# ===============================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = "static/data"
PATCHES_DIR = os.path.join(DATA_DIR, "patches")
OVERLAYS_DIR = os.path.join(DATA_DIR, "overlays")

# ===============================
# Loader
# ===============================

def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

config_runtime = load_json(os.path.join(DATA_DIR, "config.runtime.json"))
gold_data = load_json(os.path.join(PATCHES_DIR, "tecnaria_gold_consolidato.json"))
index_data = load_json(os.path.join(DATA_DIR, "index_tecnaria.json"))
router_data = load_json(os.path.join(OVERLAYS_DIR, "tecnaria_router_gold.json"))

if gold_data is None:
    gold_data = {"items": []}

if index_data is None:
    index_data = {"index": []}

if router_data is None:
    router_data = {"rules": []}

# normalizzazione
def norm(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[àáâä]", "a", s)
    s = re.sub(r"[èéêë]", "e", s)
    s = re.sub(r"[ìíîï]", "i", s)
    s = re.sub(r"[òóôö]", "o", s)
    s = re.sub(r"[ùúûü]", "u", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s

# ===============================
# Matching
# ===============================

def find_intent(q_norm):
    intents = []
    for item in index_data.get("index", []):
        for trig in item.get("triggers", []):
            if trig in q_norm:
                intents.append(item)
    return intents

def apply_router(intents, q_norm):
    # Regole CROSS
    for rule in router_data.get("rules", []):
        if any(key in q_norm for key in rule.get("keys", [])):
            return [{
                "family": rule.get("family"),
                "id": rule.get("id"),
                "score": 100
            }]
    return intents

def get_gold_by_id(item_id):
    for item in gold_data.get("items", []):
        if item.get("id") == item_id:
            return item
    return None

# ===============================
# Request Model
# ===============================

class Ask(BaseModel):
    q: str
    lang: str = "it"
    mode: str = "gold"

# ===============================
# Main API
# ===============================

@app.post("/api/ask")
def api_ask(req: Ask):

    q_raw = req.q
    q_norm = norm(q_raw)

    # 1 — Intent match
    intents = find_intent(q_norm)

    # 2 — Router
    intents = apply_router(intents, q_norm)

    # 3 — Scelta finale GOLD
    if not intents:
        return {
            "ok": False,
            "answer": "Per questa domanda non trovo una risposta GOLD consolidata.",
            "family": "COMM",
            "id": "COMM-FALLBACK-NOANSWER-0001",
            "mode": "gold",
            "lang": req.lang
        }

    best = sorted(intents, key=lambda x: -x.get("score", 0))[0]

    gold_item = get_gold_by_id(best["id"])
    if not gold_item:
        return {
            "ok": False,
            "answer": "Per questa domanda non trovo una risposta GOLD consolidata.",
            "family": "COMM",
            "id": "COMM-FALLBACK-NOANSWER-0001",
            "mode": "gold",
            "lang": req.lang
        }

    answer = gold_item["response_variants"]["gold"]["it"]

    return {
        "ok": True,
        "answer": answer,
        "family": gold_item.get("family", ""),
        "id": gold_item.get("id", ""),
        "mode": "gold",
        "lang": req.lang
    }

# ===============================
# Health
# ===============================

@app.get("/health")
def health():
    return {"ok": True, "message": "Tecnaria Sinapsi – GOLD attivo"}

# run local
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
