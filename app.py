import os
import json
import re
import unicodedata
from pathlib import Path
from typing import List, Dict, Tuple

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from openai import OpenAI

# ------------------- App setup -------------------
app = FastAPI(title="Tecnaria Bot - Web+Local")
templates = Jinja2Templates(directory="templates")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata.")
client = OpenAI(api_key=OPENAI_API_KEY)

OPENAI_MODEL    = os.getenv("OPENAI_MODEL", "gpt-4o")
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "brave")  # 'brave' o altro/vuoto per disattivare
FETCH_WEB_FIRST = os.getenv("FETCH_WEB_FIRST", "1") == "1"
SEARCH_TOPK     = int(os.getenv("SEARCH_TOPK", "6"))
DEBUG           = os.getenv("DEBUG", "0") == "1"

# ------------------- Input model (tollerante) -------------------
class AskRequest(BaseModel):
    question: str | None = None
    message:  str | None = None
    Domanda:  str | None = None
    q:        str | None = None
    def text(self) -> str:
        for k in (self.question, self.message, self.Domanda, self.q):
            if k:
                return k
        return ""

# ------------------- Paths e config locali -------------------
CRITICI_DIR = os.environ.get("CRITICI_DIR", "static/static/data/critici")
LOCAL_DOCS_DIR = "static/docs"

# Hotfix CTF (risposta deterministica)
CTF_HOTFIX = os.environ.get("CTF_HOTFIX", "0") == "1"
CTF_CODES_INLINE = os.environ.get(
    "CTF_CODES_INLINE",
    "CTF020,CTF025,CTF030,CTF040,CTF060,CTF070,CTF080,CTF090,CTF105,CTF125,CTF135"
)

# ------------------- Normalizzazione testo -------------------
def _norma_txt(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))
    s = s.lower()
    return re.sub(r"[^a-z0-9/ ]+", "", s)

# ------------------- SENSITIVE/LOCAL DATA (solo file) -------------------
# Mappatura parole chiave -> file locale (coerente con la tua cartella "critici")
SENSITIVE_FILES = {
    # Contatti
    "contatti": "contatti.json", "contatto": "contatti.json", "telefono": "contatti.json",
    "tel ": "contatti.json", "email": "contatti.json", "mail": "contatti.json",
    "pec": "contatti.json", "sdi": "contatti.json", "p.iva": "contatti.json",
    "partita iva": "contatti.json", "indirizzo": "contatti.json", "sede": "contatti.json",
    "dove siamo": "contatti.json",
    # Banca (nel tuo repo è "bancari.json")
    "iban": "bancari.json", "bonifico": "bancari.json", "conto": "bancari.json",
    "coordinate bancarie": "bancari.json",
    # Pagamenti
    "pagamento": "pagamenti.json", "pagamenti": "pagamenti.json",
    "metodi di pagamento": "pagamenti.json",
}

_SENSITIVE_KWS = re.compile(
    r"\b(contatt\w*|telefono|tel\.?|mail|email|pec|sdi|p\.?\s*iva|partita\s*iva|indirizzo|sede|dove\s+siamo|iban|bonifico|conto|banc\w*|pagamento|pagamenti|metod[ei]\s+di\s+pagamento)\b",
    re.IGNORECASE
)

def find_sensitive_file_for_question(q: str) -> str | None:
    if not q:
        return None
    ql = q.lower()
    for kw, fname in SENSITIVE_FILES.items():
        if kw in ql:
            fp = Path(CRITICI_DIR) / fname
            if fp.exists():
                return str(fp)  # file trovato
            else:
                return ""       # richiesto ma file mancante
    if _SENSITIVE_KWS.search(ql):
        return "" if not Path(CRITICI_DIR).exists() else None
    return None

def load_json_file(path: str) -> dict | None:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        if not raw.strip():
            if DEBUG: print(f"[CRITICI] File vuoto: {path}")
            return None
        return json.loads(raw)
    except Exception as e:
        if DEBUG: print(f"[CRITICI] JSON non valido in {path}: {e}")
        return None

def format_from_contatti(data: dict) -> str:
    rs   = data.get("ragione_sociale") or "TECNARIA S.p.A."
    piva = data.get("piva") or data.get("partita_iva") or "Dato non disponibile"
    sdi  = data.get("sdi")  or "Dato non disponibile"
    tel  = data.get("telefono") or "Dato non disponibile"
    email= data.get("email") or "Dato non disponibile"
    pec  = data.get("pec") or "—"
    ind  = data.get("indirizzo") or {}
    addr = ", ".join([ind.get("via",""), ind.get("cap",""), ind.get("citta",""),
                      ind.get("provincia",""), ind.get("stato","")]).replace(" ,","").strip(" ,")
    return (
        f"- **Ragione sociale**: {rs}\n"
        f
