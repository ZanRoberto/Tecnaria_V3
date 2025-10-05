# app.py
# FastAPI + regole "SINAPSI" con normalizzazione robusta per ridurre i MISS
# Compatibile con:
#   - GET  /health
#   - POST /api/ask  { "q": "..." }  -> { ok: bool, html: "<div class='card'>...</div>" }
#   - GET  /         (serve l'index.html se presente)
#
# NOTE:
# - Percorso regole: static/data/sinapsi_rules.json
# - Allowed domains: tecnaria.com, spit.eu, spitpaslode.com
# - Nessuna dipendenza extra: usa solo librerie già in requirements.txt

import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import anyio
import httpx
from fastapi import FastAPI, Body
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = STATIC_DIR / "data"
INDEX_HTML = APP_DIR / "index.html"

# ----------------------------
# Config "di fabbrica" Tecnaria
# ----------------------------
ALLOWED_DOMAINS = ["tecnaria.com", "spit.eu", "spitpaslode.com"]
SINAPSI_PATH = str(DATA_DIR / "sinapsi_rules.json")
MODE = "web_then_sinapsi_refine_single_it_priority"  # etichetta visiva nell'health
STRICT_ON_OVERRIDE = True  # se una regola è "override" e matcha bene -> rispondi subito

# Soglie "soft" per il web (rimangono esposte in /health; non rompono nulla se non usi web)
WEB_PROVIDER = "brave"
WEB_MIN_SCORE = 0.35
WEB_MIN_QUALITY = 0.55

# ------------
# Utilities I/O
# ------------
def read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None

# --------------------------
# Normalizzazione intelligente
# --------------------------
MOJIBAKE_FIXES = {
    "Ã ": "à",
    "Ã¡": "á",
    "Ã¢": "â",
    "Ã¤": "ä",
    "Ã£": "ã",
    "Ã¨": "è",
    "Ã©": "é",
    "Ãª": "ê",
    "Ã«": "ë",
    "Ã¬": "ì",
    "Ã­": "í",
    "Ã®": "î",
    "Ã¯": "ï",
    "Ã²": "ò",
    "Ã³": "ó",
    "Ã´": "ô",
    "Ã¶": "ö",
    "Ã¹": "ù",
    "Ãº": "ú",
    "Ã»": "û",
    "Ã¼": "ü",
    "Ã±": "ñ",
    "â€™": "’",
    "â€“": "–",
    "â€”": "—",
    "â€œ": "“",
    "â€": "”",
    "Â°": "°",
}

def repair_mojibake(s: str) -> str:
    # ripara sequenze tipiche di accenti "rotti"
    for bad, good in MOJIBAKE_FIXES.items():
        if bad in s:
            s = s.replace(bad, good)
    return s

def strip_accents(s: str) -> str:
    # toglie i segni diacritici (è -> e) per match più robusti
    nkfd = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in nkfd if not unicodedata.combining(ch))

def canonical_spaces(s: str) -> str:
    # normalizza spazi/punteggiatura
    s = re.sub(r"[\r\n\t]+", " ", s)
    s = re.sub(r"[^\w\s/+-]", " ", s)  # lascia parole, numeri, slash, +,-
    s = re.sub(r"\s+", " ", s).strip()
    return s

def synonym_fold(s: str) -> str:
    """
    Harmonizza varianti frequenti (senza cambiare il testo originale mostrato al cliente).
    Esempi:
      SPIT P560 / P-560 / P 560 -> p560
      chiodatrice/sparachiodi/pistola a sparo -> chiodatrice
      sì può / si puo / posso / è possibile -> puo
    """
    s = s.lower()

    # P560 varianti
    s = re.sub(r"\bspit\s*p[\s\-]*560\b", "p560", s)
    s = re.sub(r"\bp[\s\-]*560\b", "p560", s)

    # chiodatrice/sparachiodi
    s = re.sub(r"\b(pistola(?:\s+a)?\s*sparo|sparachi*
