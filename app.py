# app.py — Tecnaria Bot v3 (Golden + Compatta + Estesa)
# - UI semplice su "/"
# - /ask: auto / compact / both
# - Golden Q&A: risposte bloccate 100% identiche su pattern critici
# - P560-first opponibile, niente presence/frequency penalty
# - OpenAI Responses API con fallback modelli

import os, re, json, time
from typing import Optional, Tuple, List, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from openai import OpenAI
from openai._exceptions import APIConnectionError, APIStatusError, RateLimitError, APITimeoutError


# =========================
# Config
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata nelle Environment Variables.")

# Modello preferito + fallback (usa nomi esistenti)
PREFERRED_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-4.1").strip()
MODEL_FALLBACKS = []
for m in [PREFERRED_MODEL, "gpt-4o", "gpt-4.1", "gpt-4.1-mini"]:
    if m and m not in MODEL_FALLBACKS:
        MODEL_FALLBACKS.append(m)

DEFAULT_LANG = (os.getenv("DEFAULT_LANG") or "it").strip().lower()
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "1000"))

# Policy commerciale/tecnica (stringa autonoma, non dentro altre frasi)
def attrezzatura_clause() -> str:
    return ("Per garantire prestazioni ripetibili, tracciabilità e qualità, è ammessa la chiodatrice SPIT P560; "
            "si usano chiodi idonei secondo istruzioni Tecnaria. Alternative solo previa approvazione tecnica scritta "
            "di Tecnaria a seguito di prova di qualifica in sito.")

# =========================
# Prompt esteso (solo quando serve scheda+spiegazione)
# =========================
SYSTEM_KB = """
DOMINIO TECNARIA — REGOLE BASE (CTF e posa su lamiera):
• Attrezzatura: chiodatrice strutturale idonea (linea SPIT P560 nel per
