import os, re, json, textwrap, html
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text as pdf_extract_text
from markdown_it import MarkdownIt
from mdurl import urlencode

# ------------------------------------------------------------
# Config
# ------------------------------------------------------------
PORT = int(os.getenv("PORT", "10000"))

PREFERRED_DOMAINS = os.getenv("PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com")
PREFERRED_DOMAINS = [d.strip().lower() for d in PREFERRED_DOMAINS.split(",") if d.strip()]

MIN_WEB_SCORE = float(os.getenv("MIN_WEB_SCORE", "0.35"))
WEB_TIMEOUT = float(os.getenv("WEB_TIMEOUT", "6"))

STATIC_DIR = os.getenv("STATIC_DIR", "static")
TEMPLATES_DIR = os.getenv("TEMPLATES_DIR", "templates")

CRITICI_DIR = os.getenv("CRITICI_DIR", os.path.join(STATIC_DIR, "data"))
SINAPSI_FILE = os.getenv("SINAPSI_FILE", os.path.join(CRITICI_DIR, "sinapsi_rules.json"))

# Se usi Brave/Bing davvero, leggi le chiavi qui (non implemento la chiamata API reale in questo file)
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "brave").lower()
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY")
BING_API_KEY = os.getenv("BING_API_KEY")

# ------------------------------------------------------------
# App & static
# ------------------------------------------------------------
app = FastAPI(title="Tecnaria QA Bot", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ------------------------------------------------------------
# Utils: dominio, pulizia testo, markdown->html, link cliccabili
# ------------------------------------------------------------
_md = MarkdownIt("commonmark").enable(["table", "strikethrough", "linkify"])

def norm_space(s: str) -> str:
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()

def is_allowed_domain(url: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower()
        return any(netloc.endswith(d) for d in PREFERRED_DOMAINS)
    except Exception:
        return False

def strip_pdf_garbage(raw: str) -> str:
    """
    Se lo snippet contiene header/binary di PDF, lo elimina brutalmente.
    """
    if "%PDF" in raw[:200] or "obj <<" in raw[:600]:
        # segnale tipico di contenuto binario; lo ignoriamo
        return ""
    # se per caso ci fossero caratteri di controllo strani:
    raw = re.sub(r"[\x00-\x08\x0B-\x0C\x0E-\x1F]+", " ", raw)
    return raw

def markdown_to_html(md: str) -> str:
    # Evita che \n rimangano letterali
    md = md.replace("\\n", "\n")
    # Rimuovi doppie spaziature viste in alcuni feed PDF
    md = norm_space(md)
    # Render Markdown -> HTML
    html_str = _md.render(md)
    return html_str

def make_links_clickable(text: str) -> str:
    """
    Se il testo è già markdown (es. - https://... ), dopo markdown_to_html i link saranno cliccabili.
    In aggiunta, sostituisce URL nudi rimasti nel plain-text con <a>.
    """
    url_pat = r'(?P<url>https?://[^\s\)\]]+)'
    return re.sub(url_pat, r'<a href="\g<url>" target="_blank">\g<url></a>', text)

def safe_truncate(s: str, limit: int = 1800) -> str:
    s = s.strip()
    if len(s) <= limit:
        return s
    # Troncamento “gentile” su frase
    cut = s.rfind(".", 0, limit)
    if cut == -1:
        cut = s.rfind(" ", 0, limit)
    if cut == -1:
        cut = limit
    return s[:cut].rstrip() + "…"

# ------------------------------------------------------------
# Web fetch (HTML o PDF) – solo domini consentiti
# ------------------------------------------------------------
def fetch_text_from_url(url: str, timeout: float = WEB_TIMEOUT) -> str:
    if not is_allowed_domain(url):
        return ""
    try:
        r = requests.get(url, timeout=timeout, allow_redirects=True)
        ctype = r.headers.get("Content-Type", "").lower()
        # PDF
        if "application/pdf" in ctype or url.lower().endswith(".pdf"):
            try:
                text = pdf_extract_text(io_bytes := r.content)
                return norm_space(safe_truncate(text, 4000))
            except Exception:
                return ""
        # HTML
        if "text/html" in ctype or "<html" in r.text.lower():
            soup = BeautifulSoup(r.text, "html.parser")
            # rimuove script/style/nav
            for t in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
                t.decompose()
            txt = soup.get_text("\n")
            txt = strip_pdf_garbage(txt)
            return norm_space(safe_truncate(txt, 3000))
        # altro: scarta
        return ""
    except Exception:
        return ""

def dummy_search(query: str) -> List[str]:
    """
    Finta ricerca: elenca alcune URL *consentite* tipiche per Tecnaria (finché non colleghi Brave).
    In produzione, sostituisci con la tua funzione che interroga Brave/Bing e filtra i risultati.
    """
    seeds = [
        "https://tecnaria.com/prodotto/connettore-per-acciaio-ctf/",
        "https://tecnaria.com/solai-in-acciaio/tipologie-consolidamento-solai-acciaio/",
        "https://tecnaria.com/download/acciaio/download/CT_F_CATALOGO_IT.pdf",
        "https://tecnaria.com/prodotto/sistema-diapason/",
        "https://spit.eu/it/prodotti/chiodatrici-a-gas/p560",
    ]
    return [u for u in seeds if is_allowed_domain(u)]

# ------------------------------------------------------------
# Sinapsi rules: override / augment / postscript
# ------------------------------------------------------------
class Rule:
    def __init__(self, rid: str, pattern: str, mode: str, lang: str, answer: str):
        self.id = rid
        self.pattern = re.compile(pattern, flags=re.IGNORECASE)
        self.mode = mode  # override | augment | postscript
        self.lang = lang
        self.answer = answer

def load_sinapsi(path: str) -> List[Rule]:
    rules: List[Rule] = []
    if not os.path.isfile(path):
        return rules
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for r in data:
            rid = r.get("id") or f"rule_{len(rules)+1}"
            pattern = r.get("pattern", ".")
            mode = r.get("mode", "augment")
            lang = r.get("lang", "it")
            answer = r.get("answer", "")
            rules.append(Rule(rid, pattern, mode, lang, answer))
    except Exception:
        pass
    return rules

SINAPSI_RULES: List[Rule] = load_sinapsi(SINAPSI_FILE)

def apply_sinapsi(q: str, web_answer_md: str) -> str:
    """
    Applica la prima regola che matcha. Modalità:
    - override: ignora web e usa solo answer
    - augment: appende answer dopo web
    - postscript: aggiunge un PS breve in coda
    """
    for r in SINAPSI_RULES:
        if r.pattern.search(q):
            if r.mode == "override":
                return r.answer
            elif r.mode == "augment":
                if web_answer_md.strip():
                    return web_answer_md.rstrip() + "\n\n" + r.answer.strip()
                else:
                    return r.answer
            elif r.mode == "postscript":
                return (web_answer_md.strip() + "\n\n" + r.answer.strip()) if web_answer_md.strip() else r.answer
    return web_answer_md

# ------------------------------------------------------------
# Sintesi “leggibile” da web (senza LLM), poi formattazione finale
# ------------------------------------------------------------
def synthesize_from_web(q: str) -> Dict[str, Any]:
    """Cerca su web (finto) e produce una mini-sintesi + lista fonti (solo allowed)."""
    urls = dummy_search(q)  # <--- sostituisci con il tuo search Brave filtrato
    snippets = []
    sources = []
    for u in urls[:4]:
        txt = fetch_text_from_url(u, timeout=WEB_TIMEOUT)
        if not txt:
            continue
        snippets.append(safe_truncate(txt, 700))
        sources.append(u)

    # Semplice bozza narrativa dal web (no asterischi; frasi vere)
    if snippets:
        body = "Di seguito una sintesi dei contenuti Tecnaria pertinenti alla tua domanda.\n\n"
        for i, s in enumerate(snippets, 1):
            body += f"{i}) {s}\n\n"
    else:
        body = "Non ho trovato una sintesi web affidabile tra le fonti preferite."

    return {"body_md": body.strip(), "sources": sources}

def compose_final_html(answer_md: str, sources: List[str]) -> str:
    """
    - Converte markdown -> HTML
    - Aggiunge sezione Fonti cliccabili (solo se presenti)
    """
    if sources:
        # In coda aggiungo una sezione fonti “pulita”
        src_lines = []
        for u in sources:
            # Titolo semplice dal dominio
            host = urlparse(u).netloc.replace("www.", "")
            src_lines.append(f'📎 <a href="{html.escape(u)}" target="_blank">{html.escape(host)}</a>')
        answer_md = answer_md.rstrip() + "\n\n" + "Fonti:\n" + "\n".join(f"- {line}" for line in src_lines)

    # markdown -> html e link nudi
    html_out = markdown_to_html(answer_md)
    html_out = make_links_clickable(html_out)
    return html_out

# ------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "web_search": {
            "provider": SEARCH_PROVIDER,
            "brave_key": bool(BRAVE_API_KEY),
            "bing_key": bool(BING_API_KEY),
            "preferred_domains": PREFERRED_DOMAINS,
            "min_web_score": MIN_WEB_SCORE
        },
        "critici": {
            "dir": os.path.abspath(CRITICI_DIR),
            "exists": os.path.isdir(CRITICI_DIR),
            "sinapsi_file": os.path.abspath(SINAPSI_FILE),
            "sinapsi_loaded": len(SINAPSI_RULES)
        }
    }

@app.get("/ping")
def ping():
    return {"ok": True, "pong": True}

@app.get("/")
def home():
    # Serve l’interfaccia se presente
    index_static = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(index_static):
        return FileResponse(index_static, media_type="text/html")
    return JSONResponse({"ok": True, "msg": "Use /ask or place static/index.html"})

@app.get("/ask")
def ask_get(q: str = Query(..., min_length=2)):
    return _ask_core(q)

@app.post("/api/ask")
async def ask_post(body: Dict[str, Any]):
    q = (body or {}).get("q", "")
    if not q or not isinstance(q, str) or len(q.strip()) < 2:
        return JSONResponse({"ok": False, "error": "Missing q"}, status_code=400)
    return _ask_core(q.strip())

# ------------------------------------------------------------
# Core pipeline: Web → Sinapsi → HTML pulito
# ------------------------------------------------------------
def _ask_core(q: str):
    # 1) Cerca e sintetizza (testo pulito, no bytes PDF)
    web = synthesize_from_web(q)
    web_md = web["body_md"]
    sources = web["sources"]

    # 2) Applica Sinapsi (override/augment/postscript)
    fused_md = apply_sinapsi(q, web_md)

    # 3) HTML finale (niente \n grezzi, link cliccabili, fonti pulite)
    html_answer = compose_final_html(fused_md, sources)

    return HTMLResponse(content=html_answer, status_code=200)
