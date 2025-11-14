import os
import json
import re
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from openai import OpenAI

# --------------------------------------------------
# Config generale
# --------------------------------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata nelle variabili d'ambiente.")

client = OpenAI()

MODEL_CHAT = os.getenv("TECNARIA_MODEL_CHAT", "gpt-4.1-mini")

DATA_DIR = os.getenv("TECNARIA_DATA_DIR", "static/data")

FAMILY_FILES = [
    "CTF.json",
    "VCEM.json",
    "CTCEM.json",
    "CTL.json",
    "CTL_MAXI.json",
    "DIAPASON.json",
    "P560.json",
    "COMM.json",
]

CONSOLIDATO_PATH = os.path.join(DATA_DIR, "patches", "tecnaria_gold_consolidato.json")

# --------------------------------------------------
# FastAPI
# --------------------------------------------------

app = FastAPI(title="TECNARIA-IMBUTO", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restringi in produzione
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------
# Modelli Pydantic
# --------------------------------------------------

class AskRequest(BaseModel):
    question: str = Field(..., description="Domanda grezza dell'utente")
    session_id: Optional[str] = Field(
        None, description="ID sessione chat lato frontend"
    )
    lang: Optional[str] = Field(
        "it", description="Lingua preferita per la risposta (it/en/fr/de/es)"
    )
    debug: bool = Field(False, description="Se true, restituisce anche info di match/imbuto")
    force_family: Optional[str] = Field(
        None, description="Forza una famiglia (es: CTF, CTL, VCEM, ecc.)"
    )
    force_stage: Optional[str] = Field(
        None,
        description=(
            "Forza lo stadio imbuto: top, middle, bottom, post. "
            "Se None, viene classificato in automatico."
        ),
    )


class AskResponse(BaseModel):
    answer: str
    family: Optional[str] = None
    stage: Optional[str] = None
    lang: str = "it"
    debug: Optional[Dict[str, Any]] = None


class ConfigResponse(BaseModel):
    model_chat: str
    gold_items: int
    imbuto_stages: List[str]
    data_dir: str
    family_files: List[str]
    consolidato_loaded: bool


# --------------------------------------------------
# Caricamento KB GOLD
# --------------------------------------------------

KB_GOLD: List[Dict[str, Any]] = []


def _load_json_items(path: str) -> List[Dict[str, Any]]:
    """
    Carica un file JSON e restituisce la lista di items, qualunque sia il formato:
    - {"items": [ ... ]}
    - [ ... ]

    In caso di JSON corrotto NON blocca l'app:
    logga l'errore e restituisce lista vuota.
    """
    if not os.path.exists(path):
        print(f"[IMBUTO] WARNING: file non trovato: {path}")
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[IMBUTO] ERRORE JSON in {path}: {e} — FILE IGNORATO.")
        return []

    if isinstance(data, dict) and "items" in data:
        items = data["items"]
    elif isinstance(data, list):
        items = data
    else:
        items = []

    return [it for it in items if isinstance(it, dict)]


def load_kb() -> List[Dict[str, Any]]:
    all_items: List[Dict[str, Any]] = []

    for fname in FAMILY_FILES:
        full = os.path.join(DATA_DIR, fname)
        items = _load_json_items(full)
        all_items.extend(items)

    if os.path.exists(CONSOLIDATO_PATH):
        items = _load_json_items(CONSOLIDATO_PATH)
        all_items.extend(items)

    # filtra doppioni per id
    seen_ids = set()
    unique_items: List[Dict[str, Any]] = []
    for it in all_items:
        iid = it.get("id")
        if iid and iid in seen_ids:
            continue
        if iid:
            seen_ids.add(iid)
        unique_items.append(it)

    print(f"[IMBUTO] Caricati {len(unique_items)} blocchi GOLD (famiglie + consolidato se presente).")
    return unique_items


@app.on_event("startup")
def _startup_event():
    global KB_GOLD
    KB_GOLD = load_kb()


# --------------------------------------------------
# Utilità LLM (classificazione imbuto)
# --------------------------------------------------

IMBUTO_STAGES = ["top", "middle", "bottom", "post"]

FAMILIES_ALLOWED = {
    "CTF", "CTL", "CTL_MAXI", "VCEM", "CTCEM", "P560", "DIAPASON", "GTS", "COMM", "ALTRO"
}


def call_chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 500,
) -> str:
    if model is None:
        model = MODEL_CHAT

    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


def classify_imbuto(question: str, lang: str = "it") -> Dict[str, Any]:
    system_prompt = f"""
Sei il modulo IMBUTO di un bot Tecnaria.
Devi classificare la domanda dell'utente nello stadio del funnel commerciale e tecnico.

Stadi imbuto (usa SEMPRE uno di questi, minuscolo):
- "top"    : curiosità generali, che prodotto usare, confronto famiglie, concetti base.
- "middle" : dettagli tecnici su una famiglia già abbastanza chiara (posa, limiti, verifiche).
- "bottom" : domande molto specifiche e operative (quantità, codici ordine, tempi consegna, casi limite).
- "post"   : assistenza post-vendita, problemi in cantiere, varianti in corso d'opera.

Famiglie disponibili (usa esattamente queste stringhe o "ALTRO"):
- CTF, CTL, CTL_MAXI, VCEM, CTCEM, P560, DIAPASON, GTS, COMM, ALTRO

Rispondi in JSON valido nel formato:
{{
  "stage": "top|middle|bottom|post",
  "family": "CTF|CTL|CTL_MAXI|VCEM|CTCEM|P560|DIAPASON|GTS|COMM|ALTRO",
  "short_context": "riassunto telegrafico del caso (max 25 parole, nella lingua dell'utente)"
