import os, re, io, json, time, threading
from typing import List, Tuple, Dict, Any
from collections import OrderedDict

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import requests
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text as pdf_extract_text

# ============ CONFIG ============
APP_TITLE = "Tecnaria QA Bot"

PREFERRED_DOMAINS = os.getenv(
    "PREFERRED_DOMAINS",
    "tecnaria.com,spit.eu,spitpaslode.com"
).split(",")

MIN_WEB_SCORE = float(os.getenv("MIN_WEB_SCORE", "0.35"))

STATIC_DIR = os.getenv("STATIC_DIR", "static")
TEMPLATES_DIR = os.getenv("TEMPLATES_DIR", "templates")

CRITICI_DIR = os.getenv("CRITICI_DIR", os.path.join(STATIC_DIR, "data"))
SINAPSI_FILE = os.path.join(CRITICI_DIR, "sinapsi_rules.json")

FETCH_TIMEOUT = float(os.getenv("WEB_TIMEOUT", "3.0"))  # più aggressivo
MAX_WEB_CANDIDATES = 4

# Pre-warm di pagine chiave (HTML)
PREWARM_PAGES = [
    ("https://tecnaria.com/prodotto/connettore-per-acciaio-ctf/", "Connettore CTF – Tecnaria"),
    ("https://tecnaria.com/solai-in-acciaio/tipologie-consolidamento-solai-acciaio/", "Solai acciaio – Tecnaria"),
    ("https://tecnaria.com/solai-in-legno/", "Solai legno – Tecnaria"),
    ("https://tecnaria.com/solai-in-laterocemento/", "Solai laterocemento – Tecnaria"),
    ("https://tecnaria.com/faq-recupero-di-solai-in-acciaio-solai-nuovi/", "FAQ – Tecnaria"),
]

# Pre-warm di PDF (costosi → meglio farli una volta sola al boot)
PREWARM_PDFS = [
    ("https://tecnaria.com/download/acciaio/download/CT_F_CATALOGO_IT.pdf", "Catalogo acciaio (PDF)"),
    ("https://tecnaria.com/download/legno/download/CT_L_CATALOGO_IT.pdf", "Catalogo legno (PDF)"),
]

# ============ APP ============
app = FastAPI(title=APP_TITLE)

os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(
    directory=TEMPLATES_DIR if os.path.isdir(TEMPLATES_DIR) else STATIC_DIR
)

# ============ HTML UTILS ============
def _clean_spaces(s: str) -> str:
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))

def _make_link(url: str, label: str) -> str:
    return f"📎 <a href='{url}' target='_blank'>{_html_escape(label)}</a>"

def _simple_markdown_to_html(txt: str) -> str:
    if not txt:
        return ""
    # link [titolo](url)
    txt = re.sub(
        r"\[([^\]]+)\]\((https?://[^\)]+)\)",
        lambda m: f"<a href='{m.group(2)}' target='_blank'>{_html_escape(m.group(1))}</a>",
        txt
    )
    # **grassetto**
    txt = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", txt)

    # liste e paragrafi
    lines = [l.rstrip() for l in txt.splitlines()]
    parts = []
    in_ul = False
    for line in lines:
        if not line.strip():
            if in_ul:
                parts.append("</ul>")
                in_ul = False
            continue
        if line.lstrip().startswith("- "):
            if not in_ul:
                parts.append("<ul>")
                in_ul = True
            parts.append(f"<li>{line.lstrip()[2:].strip()}</li>")
        else:
            if in_ul:
                parts.append("</ul>")
                in_ul = False
            parts.append(f"<p>{line}</p>")
    if in_ul:
        parts.append("</ul>")
    return "\n".join(parts)

# ============ FETCH & PDF ============
FETCH_CACHE: Dict[str, str] = {}

def _pdf_to_text_bytes(pdf_bytes: bytes) -> str:
    try:
        data = io.BytesIO(pdf_bytes)
        text = pdf_extract_text(data) or ""
        text = _clean_spaces(text)
        text = re.sub(r"^%PDF-[^\n]*\n", "", text, flags=re.IGNORECASE)
        return text
    except Exception:
        return ""

def _fetch_url(url: str, timeout: float = FETCH_TIMEOUT) -> str:
    # cache
    if url in FETCH_CACHE:
        return FETCH_CACHE[url]
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        ctype = (r.headers.get("content-type") or "").lower()
        if "application/pdf" in ctype or url.lower().endswith(".pdf"):
            text = _pdf_to_text_bytes(r.content)
        else:
            soup = BeautifulSoup(r.text, "html.parser")
            for t in soup(["header", "footer", "nav", "script", "style", "noscript", "iframe", "form", "aside"]):
                t.decompose()
            text = _clean_spaces(soup.get_text("\n", strip=True))
        FETCH_CACHE[url] = text
        return text
    except Exception:
        return ""

def _in_preferred(url: str) -> bool:
    return any(d.strip().lower() in url.lower() for d in PREFERRED_DOMAINS if d.strip())

# ============ LINGUA ============
def _detect_lang(q: str) -> str:
    ql = q.lower()
    if re.search(r"[àèéìòù]| che | qual", ql):
        return "it"
    if re.search(r"\bwhat|how|when|which|do i|should i|can i\b", ql):
        return "en"
    if re.search(r"\bwas|wie|wann|welche|sollte ich|kann ich\b", ql):
        return "de"
    if re.search(r"\bquoi|comment|quand|lequel|devrais-je|puis-je\b", ql):
        return "fr"
    return "it"

# ============ SINAPSI ============
class CompiledRule:
    def __init__(self, raw: Dict[str, Any]):
        self.id = raw.get("id") or ""
        self.lang = raw.get("lang", "it")
        self.mode = raw.get("mode", "augment")  # override/augment/postscript
        self.answer_raw = raw.get("answer", "")
        pat = raw.get("pattern", "")
        try:
            self.regex = re.compile(pat, re.IGNORECASE)
        except Exception:
            self.regex = None

    def match(self, q: str) -> bool:
        return bool(self.regex and self.regex.search(q))

    def html_answer(self) -> str:
        a = self.answer_raw.strip()
        if "<p" in a or "<ul" in a or "<br" in a or "<h" in a:
            return a
        return _simple_markdown_to_html(a)

def load_sinapsi_rules(path: str) -> List[CompiledRule]:
    rules: List[CompiledRule] = []
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw_list = json.load(f)
                for raw in raw_list:
                    rules.append(CompiledRule(raw))
        except Exception:
            pass
    return rules

SINAPSI: List[CompiledRule] = []

def sinapsi_fastpath_override(q: str, lang: str) -> str:
    for r in SINAPSI:
        if r.lang == lang and r.mode == "override" and r.match(q):
            return r.html_answer()
    return ""

def sinapsi_enrich(q: str, base_html: str, lang: str) -> str:
    matches = [r for r in SINAPSI if r.lang == lang and r.match(q)]
    if not matches:
        return base_html
    blocks = [base_html] if base_html else []
    for r in matches:
        if r.mode == "augment":
            blocks.append(f"<hr><h4>Approfondimento</h4>\n{r.html_answer()}")
    for r in matches:
        if r.mode == "postscript":
            blocks.append(f"<hr><p><em>{r.html_answer()}</em></p>")
    return "\n".join([b for b in blocks if b and b.strip()])

# ============ WEB “GUIDATO” SOLO-CACHE ============
def web_summarize_from_cache(q: str) -> Tuple[str, List[Tuple[str, str]]]:
    """
    NON fa nuove richieste: usa la cache popolata dal pre-warm.
    Se la cache è vuota, restituisce comunque un corpo breve + (eventuali) link conosciuti.
    """
    ql = q.lower()
    candidates: List[Tuple[str, str]] = []

    if any(k in ql for k in ["ctf", "p560", "chiod", "hsbr", "lamiera", "hibond", "hi-bond", "grecata"]):
        candidates.extend([
            ("https://tecnaria.com/prodotto/connettore-per-acciaio-ctf/", "Connettore CTF – Tecnaria"),
            ("https://tecnaria.com/solai-in-acciaio/tipologie-consolidamento-solai-acciaio/", "Solai acciaio – Tecnaria"),
            ("https://tecnaria.com/download/acciaio/download/CT_F_CATALOGO_IT.pdf", "Catalogo acciaio (PDF) – Tecnaria"),
        ])
    if any(k in ql for k in ["ctl", "legno", "tavolato", "maxi", "vite"]):
        candidates.extend([
            ("https://tecnaria.com/solai-in-legno/", "Solai legno – Tecnaria"),
            ("https://tecnaria.com/download/legno/download/CT_L_CATALOGO_IT.pdf", "Catalogo legno (PDF) – Tecnaria"),
        ])
    if any(k in ql for k in ["diapason", "laterocemento"]):
        candidates.extend([
            ("https://tecnaria.com/solai-in-laterocemento/", "Solai laterocemento – Tecnaria"),
            ("https://tecnaria.com/faq-recupero-di-solai-in-acciaio-solai-nuovi/", "FAQ – Tecnaria"),
        ])
    if not candidates:
        candidates = [("https://tecnaria.com/faq-recupero-di-solai-in-acciaio-solai-nuovi/", "FAQ – Tecnaria")]

    # filtra e limita
    chosen = []
    for url, label in candidates:
        if _in_preferred(url) and url in FETCH_CACHE:
            chosen.append((url, label))
        if len(chosen) >= MAX_WEB_CANDIDATES:
            break

    # micro-sintesi
    bits = []
    if "ctf" in ql:
        bits.append("I CTF sono connettori per solai collaboranti acciaio–calcestruzzo su lamiera grecata.")
    if "p560" in ql or "chiod" in ql:
        bits.append("La posa è dall’alto con SPIT P560; ogni connettore richiede due chiodi HSBR14.")
    if "lamiera" in ql or "hibond" in ql or "hi-bond" in ql:
        bits.append("Compatibile con lamiere certificate (es. Hi-Bond) per piena collaborazione acciaio–calcestruzzo.")
    if "densit" in ql or "m2" in ql or "m²" in ql or "numero" in ql:
        bits.append("Indicativamente ~6–8 CTF/m²; quantità esatta da calcolo strutturale.")
    if "diapason" in ql:
        bits.append("Diapason è per solai in laterocemento esistenti; non utilizza la chiodatrice P560.")

    head = "<h3>Risposta Tecnaria</h3>"
    body = f"<p>{' '.join(bits)}</p>" if bits else ""
    links = "<br>".join([_make_link(u, lbl) for u, lbl in chosen]) if chosen else ""
    fonti = f"<h4>Fonti</h4><p>{links}</p>" if links else ""

    return f"{head}\n{body}\n{fonti}", chosen

# ============ CACHE RISPOSTE (LRU) ============
class AnswerCache(OrderedDict):
    def __init__(self, maxsize=500):
        super().__init__()
        self.maxsize = maxsize

    def getset(self, key: str, compute_fn):
        if key in self:
            val = self.pop(key)
            self[key] = val
            return val
        val = compute_fn()
        self[key] = val
        if len(self) > self.maxsize:
            self.popitem(last=False)
        return val

ANSWER_CACHE = AnswerCache(maxsize=500)

def _norm_q(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip().lower())

# ============ CORE ============
def _answer_html(q: str) -> str:
    lang = _detect_lang(q)

    # 1) Fast-path: override Sinapsi → niente rete
    override_html = sinapsi_fastpath_override(q, lang)
    if override_html:
        return override_html

    # 2) Sintesi SOLO da cache (istantanea) + enrich Sinapsi
    base_html, _ = web_summarize_from_cache(q)
    final_html = sinapsi_enrich(q, base_html, lang)

    if final_html and final_html.strip():
        return final_html

    # 3) Fallback minimale (mai vuoto)
    return (
        "<h3>OK</h3>"
        "<p>Non ho trovato una risposta adeguata al volo. "
        "Posso passarti i contatti Tecnaria o consultare la documentazione ufficiale.</p>"
    )

# ============ ENDPOINTS ============
@app.get("/ping")
def ping():
    return {"ok": True, "pong": True}

@app.get("/health")
def health():
    return {
        "status": "ok",
        "web_search": {
            "provider": "internal-cache",
            "preferred_domains": PREFERRED_DOMAINS,
            "min_web_score": MIN_WEB_SCORE,
            "cache_pages": len(FETCH_CACHE),
        },
        "critici": {
            "dir": CRITICI_DIR,
            "exists": os.path.isdir(CRITICI_DIR),
            "sinapsi_file": SINAPSI_FILE,
            "sinapsi_loaded": len(SINAPSI),
        },
        "answers_cache": len(ANSWER_CACHE),
    }

@app.get("/warmup")
def warmup():
    # permette di rilanciare il prewarm a runtime
    _prewarm_async()
    return {"ok": True, "msg": "Warmup avviato in background."}

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    idx = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.isfile(idx):
        return templates.TemplateResponse("index.html", {"request": request, "service": APP_TITLE})
    idx = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(idx):
        with open(idx, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse(
        f"<h2>{APP_TITLE}</h2><p>Usa <code>/ask?q=...</code> oppure aggiungi <code>static/index.html</code>.</p>"
    )

@app.get("/ask", response_class=HTMLResponse)
def ask_get(q: str = Query(""), debug: bool = Query(False)):
    q = (q or "").strip()
    if not q:
        return HTMLResponse("<h3>OK</h3><p>Domanda vuota: inserisci una richiesta valida.</p>")

    key = _norm_q(q)
    def _compute():
        return _answer_html(q)

    html = ANSWER_CACHE.getset(key, _compute)
    if debug:
        dbg = f"<div class='debug'><pre>{_html_escape(q)}</pre></div>"
        return HTMLResponse(dbg + html)
    return HTMLResponse(html)

@app.post("/api/ask")
def ask_post(payload: Dict[str, Any]):
    q = (payload or {}).get("q", "").strip()
    if not q:
        return JSONResponse({"ok": True, "answer": "<h3>OK</h3><p>Domanda vuota: inserisci una richiesta valida.</p>"})

    key = _norm_q(q)
    def _compute():
        return _answer_html(q)

    html = ANSWER_CACHE.getset(key, _compute)
    return JSONResponse({"ok": True, "answer": html})

# ============ PRE-WARM ============
def _prewarm_worker(urls: List[str]):
    for url in urls:
        try:
            if _in_preferred(url):
                _ = _fetch_url(url, timeout=min(FETCH_TIMEOUT, 5.0))
        except Exception:
            pass

def _prewarm_async():
    urls = [u for u, _ in PREWARM_PAGES] + [u for u, _ in PREWARM_PDFS]
    # thread per non bloccare l'avvio
    t = threading.Thread(target=_prewarm_worker, args=(urls,), daemon=True)
    t.start()

@app.on_event("startup")
def _startup():
    # carica Sinapsi
    global SINAPSI
    SINAPSI = load_sinapsi_rules(SINAPSI_FILE)
    # prewarm cache
    _prewarm_async()
