import os
import json
import re
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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

# cartella dati reale del progetto
DATA_DIR = os.getenv("TECNARIA_DATA_DIR", "static/data")

# elenco file di famiglia runtime (come in RECUPERTOTALE)
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
# Caricamento KB GOLD (famiglie + consolidato)
# --------------------------------------------------

KB_GOLD: List[Dict[str, Any]] = []


def _load_json_items(path: str) -> List[Dict[str, Any]]:
    """Carica un file JSON e restituisce la lista di items, qualunque sia il formato:
    - {"items": [ ... ]}
    - [ ... ]
    """
    if not os.path.exists(path):
        print(f"[IMBUTO] WARNING: file non trova
