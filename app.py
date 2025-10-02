import os
import re
import json
import unicodedata
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# OpenAI SDK (opzionale)
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

# -----------------------------------------------------------------------------
# APP SETUP
# -----------------------------------------------------------------------------
app = FastAPI(title="Tecnaria Bot - Web+Local")
templates = Jinja2Templates(directory="templates")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
client = OpenAI(api_key=OPENAI_API_KEY) if (OPENAI_API_KEY and OpenAI is not None) else None

# Web-search
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "brave")  # "brave" usa Brave Search API
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
SEARCH_TOPK = int(os.getenv("SEARCH_TOPK", "6"))
DEBUG = os.getenv("DEBUG", "0") == "1"

# Percorsi locali
CRITICI_DIR = os.environ.get("CRITICI_DIR", "static/static/data/critici")
LOCAL_DOCS_DIR = "static/docs"

# Hotfix manuale (disattivo di default)
CTF_HOTFIX = os.environ.get("CTF_HOTFIX", "0") == "1"
CTF_CODES_INLINE = os.environ.get(
    "CTF_CODES_INLINE",
    "CTF020,CTF025,CTF030,CTF040,CTF060,CTF070,CTF080,CTF090,CTF105,CTF125,CTF135"
)

# -----------------------------------------------------------------------------
# INPUT MODEL
# -----------------------------------------------------------------------------
class AskRequest(BaseModel):
    question: Optional[str] = None
    message: Optional[str] = None
    Domanda: Optional[str] = None
    q: Optional[str] = None
    def text(self) -> str:
        for k in (self.question, self.message, self.Domanda, self.q):
            if k:
                return k
        return ""

# -----------------------------------------------------------------------------
# NORMALIZZAZIONE / ALIAS
# -----------------------------------------------------------------------------
def _norma_txt(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))
    s = s.lower()
    return re.sub(r"[^a-z0-9/ ]+", "", s)

def _alias_canon(s: str) -> str:
    """Corregge i typo più comuni e normalizza '12 / 60' -> '12/60'."""
    if not s:
        return ""
    out = s
    # CFT -> CTF
    out = re.sub(r"\bCFT\b", "CTF", out, flags=re.IGNORECASE)
    # 'CT F' -> 'CTF' (spazi in mezzo, eventuali)
    out = re.sub(r"\bC\s*T\s*F\b", "CTF", out, flags=re.IGNORECASE)
    # normalizza '12 / 60' -> '12/60'
    out = re.sub(r"(\b\d+)\s*/\s*(\d+\b)", r"\1/\2", out)
    return out

# -----------------------------------------------------------------------------
# SCOPE (limita il dominio del bot a Tecnaria) – include typo 'cft'
# -----------------------------------------------------------------------------
SCOPE_KWS = re.compile(
    r"\b(tecnaria|ctf|cft|ctl|ctcem|vcem|diapason|lamiera|grecata|solai?|p560|spit|connettor\w+|"
    r"acciaio|legno|laterocemento|collaborant\w+|eta|posa|chiodi|hsbr14)\b",
    re.IGNORECASE
)

# -----------------------------------------------------------------------------
# INTENT E FAMIGLIE DI CODICI
# -----------------------------------------------------------------------------
CODICI_INTENT = re.compile(
    r"\b(codic[io]|\bcod\.|\blista\s+codici|\btutti\s+i\s+codici|\bcodici\s+prodotti?)\b",
    re.IGNORECASE
)

def _detect_family_in_text(t: str) -> Optional[str]:
    t = (t or "").lower()
    if "ctf" in t or "cft" in t: return "ctf"
    if "ctl" in t:               return "ctl"
    if "ctcem" in t:             return "ctcem"
    if "vcem" in t:              return "vcem"
    if "diapason" in t:          return "diapason"
    return None

def _looks_like_ask_codes(q: str) -> bool:
    nq = _norma_txt(q)
    return ("codic" in nq or "lista codici" in nq or "codes" in nq) and any(
        fam in nq for fam in ["ctf", "cft", "ctl", "ctcem", "vcem", "diapason"]
    )

# -----------------------------------------------------------------------------
# SENSITIVE FILES (contatti / bancari / pagamenti) SOLO DA FILE LOCALI
# -----------------------------------------------------------------------------
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

def find_sensitive_file_for_question(q: str) -> Optional[str]:
    if not q:
        return None
    ql = q.lower()
    for kw, fname in SENSITIVE_FILES.items():
        if kw in ql:
            fp = Path(CRITICI_DIR) / fname
            if fp.exists():
                return str(fp)
            else:
                return ""
    if _SENSITIVE_KWS.search(ql):
        return "" if not Path(CRITICI_DIR).exists() else None
    return None

def load_json_file(path: str) -> Optional[dict]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        if not raw.strip():
            return None
        return json.loads(raw)
    except Exception:
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

# -----------------------------------------------------------------------------
# CRITICI: FAQ deterministiche + lettura diretta codici
# -----------------------------------------------------------------------------
def load_critici_faq(dir_path: str = CRITICI_DIR) -> Tuple[List[dict], List[str]]:
    qa_entries: List[dict] = []
    files: List[str] = []
    if not os.path.isdir(dir_path):
        return qa_entries, files
    for p in Path(dir_path).glob("*.json"):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            for item in doc.get("faq", []):
                triggers = [_norma_txt(q) for q in item.get("q", [])]
                qa_entries.append({"triggers": triggers, "answer": item.get("a", "")})
            files.append(p.name)
        except Exception as e:
            if DEBUG:
                print(f"[critici] errore su {p}: {e}")
    return qa_entries, files

CRITICI_QA, CRITICI_FILES = load_critici_faq()

def answer_from_critici(user_q: str) -> Optional[str]:
    if not user_q:
        return None
    nq = _norma_txt(user_q)
    for qa in CRITICI_QA:
        for trig in qa["triggers"]:
            if nq == trig or trig in nq:
                return qa["answer"]
    if "ctf" in nq and "12/" in nq:
        return ("La notazione “CTF 12/xx” è equivalente al codice con altezza xx: "
                "12/40→CTF040, 12/60→CTF060, 12/105→CTF105, 12/125→CTF125, 12/135→CTF135.")
    return None

def get_codes_from_critici(family: str = "ctf") -> List[str]:
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

# -----------------------------------------------------------------------------
# SYSTEM PROMPT (per risposte generali via LLM)
# -----------------------------------------------------------------------------
SYSTEM_PROMPT = """
Parli come il MIGLIOR TECNICO-COMMERCIALE di Tecnaria S.p.A. (Bassano del Grappa):
competente, chiaro, autorevole, propositivo e vicino al cliente.

TASSONOMIA OBBLIGATORIA (NON VIOLARE)
- P560 / SPIT P560 = chiodatrice a sparo (pistola) per fissaggi su ACCIAIO/LAMIERA. NON è un connettore.
  • Usata nei sistemi CTF (lamiera grecata + calcestruzzo) con chiodi HSBR14.
  • NON per connettori su LEGNO (CTL/CTCEM: viti/bulloni).
- CTF = connettori per solai collaboranti ACCIAIO-CALCESTRUZZO su lamiera grecata; fissaggio a sparo (HSBR14) con P560.
- CTL = connettori per solai collaboranti LEGNO-CALCESTRUZZO; fissaggio con VITI, NO P560.
- DIAPASON (laterocemento) = fissaggi meccanici, NO P560.

REGOLE
- Ordine fonti: WEB → poi documenti locali. Se il web è vuoto, usa i locali; se comunque non trovi, dillo.
- 5–7 bullet sintetici e pratici. Chiudi con **Fonti** (URL o 'file locale').
""".strip()

def _force_bullets_and_sources(text: str, web_hits: List[Dict], local_docs: Dict[str, str]) -> str:
    txt = (text or "").strip()
    has_sources = ("**Fonti**" in txt) or ("\nFonti\n" in txt) or ("\nFonti:" in txt)
    if not has_sources:
        sources = [h["url"] for h in web_hits] if web_hits else list(local_docs.keys())
        if sources:
            txt += "\n\n**Fonti**\n" + "\n".join(f"- {s}" for s in sources)
        else:
            txt += "\n\n**Fonti**\n- (nessuna fonte trovata)"
    return txt

# -----------------------------------------------------------------------------
# WEB SEARCH (Brave)
# -----------------------------------------------------------------------------
_ALLOWED_DOMAINS = ("tecnaria.com", "ordini.tecnaria.com")

async def web_search(query: str, topk: int = 6) -> List[Dict]:
    if SEARCH_PROVIDER.lower() != "brave" or not BRAVE_API_KEY:
        return []
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"X-Subscription-Token": BRAVE_API_KEY, "User-Agent": "Mozilla/5.0"}
    params = {"q": query, "count": topk}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=18.0) as ac:
            r = await ac.get(url, headers=headers, params=params)
            if r.status_code != 200:
                return []
            data = r.json()
            results = []
            for item in (data.get("web", {}).get("results") or [])[:topk]:
                u = item.get("url")
                if not u:
                    continue
                results.append({"name": item.get("title", "") or u, "url": u})
            results.sort(key=lambda x: 1 if any(d in x["url"].lower() for d in _ALLOWED_DOMAINS) else 0, reverse=True)
            return results
    except Exception as e:
        if DEBUG:
            print("[BRAVE] errore:", e)
        return []

async def _fetch_text_for_regex(url: str, timeout: float = 18.0) -> str:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as ac:
            r = await ac.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                return ""
            ct = (r.headers.get("content-type") or "").lower()
            if "pdf" in ct or url.lower().endswith(".pdf"):
                try:
                    return r.content.decode("latin-1", errors="ignore")
                except Exception:
                    return r.content.decode("utf-8", errors="ignore")
            return r.text or ""
    except Exception:
        return ""

_CODE_PATTERNS = {
    "ctf": re.compile(r"\bCTF\d{3}\b", re.IGNORECASE),
    "ctl": re.compile(r"\bCTL\d{3}\b", re.IGNORECASE),
    "ctcem": re.compile(r"\bCTCEM\d{3}\b", re.IGNORECASE),
    "vcem": re.compile(r"\bVCEM\d{3}\b", re.IGNORECASE),
    "diapason": re.compile(r"\bDIAPASON\d{2,4}\b", re.IGNORECASE),
}
GENERIC_CODE = re.compile(r"\b[A-Z]{2,8}\d{2,4}\b")
FAMILIES = ["ctf", "ctl", "ctcem", "vcem", "diapason"]

async def _collect_hits_from_queries(base_query: str, max_links: int) -> List[str]:
    queries = [
        f'site:tecnaria.com {base_query}',
        f'site:tecnaria.com "scheda tecnica" {base_query}',
        f'site:tecnaria.com filetype:pdf {base_query}',
        f'site:tecnaria.com CTF OR CTL OR CTCEM OR VCEM OR DIAPASON {base_query}',
    ]
    urls: List[str] = []
    seen = set()
    for q in queries:
        hits = await web_search(q, topk=max_links) or []
        for h in hits:
            url = (h.get("url") or "").strip()
            if not url:
                continue
            if not any(dom in url.lower() for dom in _ALLOWED_DOMAINS):
                continue
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
    return urls[:max_links]

async def find_product_codes_from_web(query: str, family: str, max_links: int = 12) -> Tuple[List[str], List[str]]:
    synonyms = {
        "ctf": "CTF connettori Tecnaria lamiera grecata chiodi HSBR14 P560",
        "ctl": "CTL connettori legno calcestruzzo viti",
        "ctcem": "CTCEM connettori legno calcestruzzo",
        "vcem": "VCEM connettori acciaio calcestruzzo",
        "diapason": "Diapason laterocemento",
    }
    base_q = synonyms.get(family.lower(), family)
    urls = await _collect_hits_from_queries(base_q, max_links)
    pattern = _CODE_PATTERNS.get(family.lower()) or GENERIC_CODE

    codes: set[str] = set()
    used_sources: List[str] = []

    for url in urls:
        text = await _fetch_text_for_regex(url)
        if not text:
            continue
        found = set(m.upper() for m in pattern.findall(text))
        if found:
            codes |= found
            used_sources.append(url)

    def _num_key(c: str) -> int:
        try:
            return int(re.findall(r"(\d{2,4})", c)[0])
        except Exception:
            return 0
    sorted_codes = sorted(codes, key=_num_key)
    return sorted_codes, used_sources[:8]

# -----------------------------------------------------------------------------
# LOCAL DOCS (txt facoltativi)
# -----------------------------------------------------------------------------
def load_local_docs() -> Dict[str, str]:
    docs: Dict[str, str] = {}
    base = LOCAL_DOCS_DIR
    if not os.path.isdir(base):
        return docs
    for fn in os.listdir(base):
        if fn.lower().endswith(".txt"):
            try:
                with open(os.path.join(base, fn), "r", encoding="utf-8") as f:
                    docs[fn] = f.read()
            except Exception:
                continue
    return docs

# -----------------------------------------------------------------------------
# ROUTES
# -----------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/ping")
async def ping():
    return {"status": "ok", "service": "Tecnaria Bot - Web+Local", "model": OPENAI_MODEL}

@app.get("/health_critici")
async def health_critici():
    return {
        "dir": CRITICI_DIR,
        "files": CRITICI_FILES,
        "faq_loaded": sum(len(x["triggers"]) for x in CRITICI_QA) if CRITICI_QA else 0,
        "hotfix_ctf": CTF_HOTFIX,
        "ctf_codes_inline": CTF_CODES_INLINE
    }

@app.get("/debug_codes")
async def debug_codes(family: str = "ctf", q: str = ""):
    fam = (family or "ctf").lower().strip()
    base_q = q.strip() or fam
    codes, srcs = await find_product_codes_from_web(base_q, fam)
    local_codes = get_codes_from_critici(fam)
    return {"family": fam, "query": base_q, "web_codes": codes, "local_codes": local_codes, "sources": srcs}

@app.get("/api/ask")
async def ask_get(request: Request, q: str = ""):
    return await ask(AskRequest(q=q))

@app.post("/api/ask")
async def ask(req: AskRequest):
    try:
        raw_q = (req.text() or "").strip()
        if not raw_q:
            return JSONResponse({"ok": False, "error": "Domanda mancante"}, status_code=400)

        # normalizza typo e formati
        user_q = _alias_canon(raw_q)

        # 0) Dati sensibili: SOLO file locali
        sensitive_path = find_sensitive_file_for_question(user_q)
        if sensitive_path is not None:
            if sensitive_path == "":
                return {"ok": True, "answer": "- Informazione sensibile richiesta ma il file locale non è presente.\n\n**Fonti**\n- (file locale mancante)"}
            return {"ok": True, "answer": format_sensitive_answer_from_file(sensitive_path)}

        # 1) Ambito aziendale (su query normalizzata)
        if not SCOPE_KWS.search(user_q):
            msg = (
                "- Ambito del bot: prodotti/soluzioni Tecnaria (connettori CTF/CTL/Diapason, posa, P560, documentazione, contatti, pagamenti).\n"
                "- La tua domanda non rientra in questo ambito.\n"
                "- Esempi utili: «Come si fissano i CTF su lamiera TR60?» · «Qual è il nostro IBAN?» · «Mi dai i contatti aziendali?»\n\n"
                "**Fonti**\n- policy interna (ambito Tecnaria)"
            )
            return {"ok": True, "answer": msg}

        # 2) Intent CODICI (WEB → poi locali → (opz.) hotfix ENV)
        ql = user_q.lower()
        if CODICI_INTENT.search(ql) or _looks_like_ask_codes(user_q) or "ctf 12/" in ql:
            fam = _detect_family_in_text(ql) or "ctf"

            # A) WEB FIRST
            codes, srcs = await find_product_codes_from_web(user_q, fam)
            if codes:
                lines = [f"- **{c}**" for c in codes]
                fontes = "\n".join(f"- {u}" for u in (srcs or [])) if srcs else "- tecnaria.com"
                return {"ok": True, "answer": f"OK · Codici {fam.upper()}\n" + "\n".join(lines) + f"\n\n**Fonti**\n{fontes}"}

            # B) LOCALE (critici diretti)
            local_codes = get_codes_from_critici(fam)
            if local_codes:
                lines = [f"- **{c}**" for c in local_codes]
                fonte = f"- file locale · critici/codici_{fam}.json" if (Path(CRITICI_DIR)/f"codici_{fam}.json").exists() else "- file locale · critici"
                return {"ok": True, "answer": f"OK · Codici {fam.upper()}\n" + "\n".join(lines) + f"\n\n**Fonti**\n{fonte}"}

            # C) (OPZ.) hotfix ENV
            if CTF_HOTFIX and fam == "ctf":
                return {"ok": True, "answer": (
                    f"I codici ufficiali dei connettori CTF Tecnaria sono: {CTF_CODES_INLINE}. "
                    "Ø12 mm; fissaggio con chiodatrice SPIT P560.\n\n**Fonti**\n- fallback manuale (env)"
                )}

            # D) Nessun risultato
            tried = "\n".join(f"- {u}" for u in (srcs or [])) or "- (nessuna pagina rilevante trovata su tecnaria.com)"
            return {"ok": True, "answer": f"Non ho trovato codici {fam.upper()} su tecnaria.com.\n\n**Pagine controllate**\n{tried}\n\n**Fonti**\n- tecnaria.com"}

        # 3) Flusso generale: WEB → locali → LLM (se disponibile)
        web_hits: List[Dict] = await web_search(user_q, topk=SEARCH_TOPK)
        local_docs: Dict[str, str] = load_local_docs()

        parts: List[str] = []
        if web_hits:
            parts.append("Fonti web:\n" + "\n".join(f"- {h['name']} {h['url']}" for h in web_hits))
        if local_docs:
            parts.append("Documenti locali:\n" + "\n".join(f"- {k}" for k in local_docs.keys()))
        context_blob = "\n\n".join(parts) if parts else "(nessun contesto disponibile)"

        if client is None:
            answer = (
                "- Modalità senza LLM attivo (manca OPENAI_API_KEY o SDK):\n"
                + context_blob
                + "\n\n**Fonti**\n- vedi elenco sopra"
            )
            return {"ok": True, "answer": answer}

        user_prompt = (
            "Rispondi in 5–7 bullet tecnico-commerciali (vantaggi, posa, sicurezza/certificazioni quando rilevanti). "
            "Chiudi con sezione **Fonti** (URL se web, altrimenti 'file locale'). "
            f"\n\nDomanda: {user_q}\n\nSorgenti disponibili:\n{context_blob}"
        )

        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2,
            max_tokens=900,
        )
        text = (resp.choices[0].message.content or "").strip()
        text = _force_bullets_and_sources(text, web_hits, local_docs)
        return {"ok": True, "answer": text}

    except Exception as e:
        if DEBUG:
            print("[ERROR] /api/ask:", repr(e))
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
