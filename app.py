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

# Hotfix manuale (DISATTIVO di default). Rimane disponibile solo come ultimo paracadute opzionale.
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
SENSITIVE_FILES = {
    # Contatti
    "contatti": "contatti.json", "contatto": "contatti.json", "telefono": "contatti.json",
    "tel ": "contatti.json", "email": "contatti.json", "mail": "contatti.json",
    "pec": "contatti.json", "sdi": "contatti.json", "p.iva": "contatti.json",
    "partita iva": "contatti.json", "indirizzo": "contatti.json", "sede": "contatti.json",
    "dove siamo": "contatti.json",
    # Banca
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
        f"- **P.IVA**: {piva}   **SDI**: {sdi}\n"
        f"- **Indirizzo**: {addr or 'Dato non disponibile'}\n"
        f"- **Telefono**: {tel}\n"
        f"- **Email**: {email}\n"
        f"- **PEC**: {pec}\n\n"
        "**Fonti**\n- file locale · contatti.json"
    )

def format_from_bank(data: dict) -> str:
    iban  = data.get("iban") or data.get("conto") or "Dato non disponibile"
    intes = data.get("intestatario", "Dato non disponibile")
    banca = data.get("banca", "Dato non disponibile")
    bic   = data.get("bic", data.get("swift", "Dato non disponibile"))
    note  = data.get("note", "-")
    return (
        f"- **Intestatario**: {intes}\n"
        f"- **Banca**: {banca}\n"
        f"- **IBAN**: {iban}\n"
        f"- **BIC/SWIFT**: {bic}\n"
        f"- **Note**: {note or '-'}\n\n"
        "**Fonti**\n- file locale · bancari.json"
    )

def format_from_pagamenti(data: dict) -> str:
    lines = []
    for m in data.get("metodi", []):
        lines.append(f"- **{m.get('tipo','metodo')}**: {m.get('istruzioni','')}")
    if not lines:
        lines = ["- (nessun metodo di pagamento presente)"]
    note = data.get("note")
    if note:
        lines.append(f"- **Note**: {note}")
    return "\n".join(lines) + "\n\n**Fonti**\n- file locale · pagamenti.json"

def format_sensitive_answer_from_file(path: str) -> str:
    data = load_json_file(path)
    if not data:
        return "- Dato non disponibile nel file locale.\n\n**Fonti**\n- file locale"
    name = Path(path).name
    if name == "contatti.json":
        return format_from_contatti(data)
    if name == "bancari.json":
        return format_from_bank(data)
    if name == "pagamenti.json":
        return format_from_pagamenti(data)
    pretty = json.dumps(data, indent=2, ensure_ascii=False)
    return f"- Dati:\n```\n{pretty[:1000]}\n```\n\n**Fonti**\n- file locale · {name}"

# ------------------- CRITICI: FAQ deterministiche (trigger) -------------------
def load_critici_faq(dir_path: str = CRITICI_DIR):
    qa_entries = []
    files = []
    if not os.path.isdir(dir_path):
        return qa_entries, files
    for p in Path(dir_path).glob("*.json"):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            for item in doc.get("faq", []):
                triggers = [_norma_txt(q) for q in item.get("q", [])]
                qa_entries.append({"triggers": triggers, "answer": item.get("a","")})
            files.append(p.name)
        except Exception as e:
            if DEBUG: print(f"[critici] errore su {p}: {e}")
    return qa_entries, files

CRITICI_QA, CRITICI_FILES = load_critici_faq()

def answer_from_critici(user_q: str) -> str | None:
    if not user_q:
        return None
    nq = _norma_txt(user_q)
    for qa in CRITICI_QA:
        for trig in qa["triggers"]:
            if nq == trig or trig in nq:
                return qa["answer"]
    # micro-fallback per “CTF 12/xx”
    if "ctf" in nq and "12/" in nq:
        return ("La notazione “CTF 12/xx” è equivalente al codice con altezza xx: "
                "12/40→CTF040, 12/60→CTF060, 12/105→CTF105, 12/125→CTF125, 12/135→CTF135.")
    return None

# ------------------- Lettura diretta codici da CRITICI (senza trigger) ------
def get_codes_from_critici(family: str = "ctf") -> List[str]:
    """
    Legge i codici direttamente dai file in CRITICI (schema consigliato: { data:{ codici:[...] } }).
    Usa prima codici_<fam>.json, poi qualunque .json che contenga array di codici compatibili.
    """
    dirp = Path(CRITICI_DIR)
    if not dirp.exists():
        return []
    prefer = dirp / f"codici_{family}.json"
    candidates = [prefer] if prefer.exists() else list(dirp.glob("*.json"))

    codes: set[str] = set()
    for p in candidates:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        data = (doc.get("data") or {})
        arr = data.get("codici") or []
        if not arr:
            # fallback: qualsiasi lista di stringhe presente
            for k, v in doc.items():
                if isinstance(v, list) and any(isinstance(x, str) for x in v):
                    arr = v
                    break
        for c in arr:
            s = str(c).upper().strip()
            if not s:
                continue
            if family and not s.startswith(family.upper()):
                continue
            if re.match(rf"^{family.upper()}\d{{3,4}}$", s):
                codes.add(s)

    def _num_key(c: str) -> int:
        try:
            return int(re.findall(r"(\d{2,4})", c)[0])
        except Exception:
            return 0
    return sorted(codes, key=_num_key)

def _looks_like_ask_codes(q: str) -> bool:
    nq = _norma_txt(q)
    return ("codic" in nq or "lista codici" in nq or "codes" in nq) and any(
        fam in nq for fam in ["ctf", "ctl", "ctcem", "vcem", "diapason"]
    )

# ------------------- Hotfix CTF opzionale (ultimo paracadute) ---------------
def ctf_hotfix_answer(user_q: str) -> str | None:
    if not CTF_HOTFIX:
        return None
    nq = _norma_txt(user_q)
    trigger_codici = ("ctf" in nq) and any(k in nq for k in ["codici", "codice", "codes", "lista"])
    trigger_formato = ("ctf" in nq) and ("12/" in nq or "12 " in nq)
    if trigger_codici:
        return (f"I codici ufficiali dei connettori CTF Tecnaria sono: {CTF_CODES_INLINE}. "
                "Ø12 mm; fissaggio con chiodatrice SPIT P560.")
    if trigger_formato:
        return ("La notazione “CTF 12/xx” è equivalente al codice con altezza xx: "
                "12/40→CTF040, 12/60→CTF060, 12/105→CTF105, 12/125→CTF125, 12/135→CTF135.")
    return None

# ------------------- Helpers (web/local generali) -------------------
def expand_query_if_needed(query: str) -> str:
    q = query.lower()
    if "p560" in q and "spit" not in q:
        return query + " SPIT chiodatrice pistola sparachiodi Tecnaria HSBR14"
    return query

def _prefer_score(url: str) -> int:
    preferred = [d.strip().lower() for d in (os.getenv("PREFERRED_DOMAINS") or "tecnaria.com,spit.eu,spitpaslode.com,ordini.tecnaria.com").split(",") if d.strip()]
    return 1 if any(d in url.lower() for d in preferred) else 0

def _looks_like_p560_misclassified(text: str) -> bool:
    t = (text or "").lower()
    return ("p560" in t or "spit p560" in t) and ("connettor" in t) and not any(w in t for w in ["chiodatrice","pistola","sparachiodi"])

def _force_bullets_and
