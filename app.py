import os
import json
import hashlib
import html
import threading
import re
import math
import time
from collections import Counter, OrderedDict
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from openai import OpenAI

# ============================================================
# CONFIG BASE
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(STATIC_DIR, "data")
CATALOG_PDF_PATH = os.getenv(
    "CATALOG_PDF_PATH",
    os.path.join(STATIC_DIR, "catalogo.pdf"),
).strip()

MASTER_PATH = os.path.join(DATA_DIR, "ctf_system_COMPLETE_GOLD_master.json")
COMM_PATH = os.path.join(DATA_DIR, "COMM.json")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = (os.getenv("DEEPSEEK_MODEL", "deepseek-chat") or "deepseek-chat").strip()
DEEPSEEK_BASE_URL = (
    os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    or "https://api.deepseek.com"
).strip()

# Selettore del motore documentale:
# - deepseek_local: indice locale + DeepSeek
# - openai_vector: Vector Store + OpenAI
# - automatic: prova OpenAI Vector e, in caso di errore, passa a DeepSeek locale
SEARCH_ENGINE = (os.getenv("SEARCH_ENGINE", "deepseek_local") or "deepseek_local").strip().lower()
if SEARCH_ENGINE not in {"deepseek_local", "openai_vector", "automatic"}:
    print(f"[WARN] SEARCH_ENGINE non valido: {SEARCH_ENGINE}; uso deepseek_local")
    SEARCH_ENGINE = "deepseek_local"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_VECTOR_STORE_ID = os.getenv("OPENAI_VECTOR_STORE_ID", "").strip()
OPENAI_DOCUMENT_MODEL = (
    os.getenv("OPENAI_DOCUMENT_MODEL", "gpt-4o") or "gpt-4o"
).strip()

# Archivio locale indicizzato. Non dipende da OpenAI o da un Vector Store.
DOCUMENT_INDEX_PATH = os.path.join(DATA_DIR, "document_index_COMPLETO.txt")
# schede prodotto costruite dal PDF con le coordinate (build_schede.py): ogni valore e'
# legato alla sua colonna. Facoltative: senza, il motore usa le righe di testo.
PRODUCT_CARDS_PATH = os.path.join(DATA_DIR, "schede_prodotto.json")

# Il motore NAR/SUP resta universale. Questi valori descrivono soltanto
# l'azienda e il patrimonio documentale collegati alla singola installazione.
DOCUMENT_CONTEXT = os.getenv(
    "DOCUMENT_CONTEXT",
    "Catalogo LAGO ELEMENTS February 2024 IT/EN",
).strip()
DOCUMENT_DISCLAIMER = os.getenv(
    "DOCUMENT_DISCLAIMER",
    (
        "I dati appartengono al catalogo LAGO ELEMENTS February 2024 IT/EN; "
        "prezzi e condizioni devono essere verificati commercialmente prima "
        "di formulare un'offerta definitiva."
    ),
).strip()

# Interruttore commerciale: per impostazione predefinita la funzione non e'
# visibile. Su Render puo' essere attivata impostando il valore a "true".
ENABLE_COMMERCIAL_PROPOSAL = os.getenv(
    "ENABLE_COMMERCIAL_PROPOSAL", "false"
).strip().lower() in {"1", "true", "yes", "on"}

# Modello che SCRIVE la risposta (stesura, controllo vincoli, correzione documentale).
# Il pianificatore resta sul modello veloce DEEPSEEK_MODEL. Di default non cambia nulla.
#   GENERATION_PROVIDER = deepseek | openai
#   GENERATION_MODEL    = es. deepseek-chat, deepseek-reasoner, gpt-4o, gpt-5, o4-mini
GENERATION_PROVIDER = (os.getenv("GENERATION_PROVIDER", "deepseek") or "deepseek").strip().lower()
GENERATION_MODEL = (os.getenv("GENERATION_MODEL", "") or "").strip()

client: Optional[OpenAI] = None
if DEEPSEEK_API_KEY:
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

openai_client: Optional[OpenAI] = None
if OPENAI_API_KEY:
