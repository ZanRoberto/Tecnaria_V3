import os, re, io, json, time
from typing import List, Tuple, Dict, Any

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import requests
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text as pdf_extract_text

# -------------------- CONFIG -------------------- #

APP_TITLE = "Tecnaria QA Bot"

PREFERRED_DOMAINS = os.getenv(
    "PREFERRED_DOMAINS",
    "tecnaria.com,spit.eu,spitpaslode.com"
).split(",")

MIN_WEB_SCORE = float(os.getenv("MIN_WEB_SCORE", "0.35"))

STATIC_DIR = os.getenv("STATIC_DIR", "static")
TEMPLATES_DIR = os.getenv("TEMPLATES_DIR", "templates")

# Directory e file dati (Sinapsi)
CRITICI_DIR = os.getenv("CRITICI_DIR", os.path.join(STATIC_DIR, "data"))
SINAPSI_FILE = os.path.join(CRITICI_DIR, "sinapsi_rules.json")

# Preload/Cache
FETCH_TIMEOUT = float(os.getenv("WEB_TIMEOUT", "6.0"))
PREWARM_SOURCES = [
    ("https://tecnaria.com/prodotto/connettore-per-acciaio-ctf/", "Scheda tecnica CTF – Tecnaria"),
    ("https://tecnaria.com/solai-in-acciaio/tipologie-consolidamento-solai-acciaio/", "Solai in acciaio – Tipologie – Tecnaria"),
    ("https://tecnaria.com/solai-in-legno/", "Solai in legno – Tecnaria"),
    ("https://tecnaria.com/solai-in-laterocemento/", "Solai in laterocemento – Tecnaria"),
]

# -------------------- APP -------------------- #

app = FastAPI(title=APP_TITLE)

os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(
    directory=TEMPLATES_DIR if os.path.isdir(TEMPLATES_DIR) else STATIC_DIR
)

# -------------------- UTILS HTML -------------------- #

def _clean_spaces(s: str) -> str:
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def _html_escape(s: str) -> str:
    return (s
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

def _make_link(url: str, label: str) -> str:
    return f"📎 <a href='{url}' target='_blank'>{_html_escape(label)}</a>"

def _simple_markdown_to_html(txt: str) -> str:
    """
    Converte un markdown 'leggerissimo' (usato nelle risposte Sinapsi) in HTML pulito:
    - **grassetto** -> <strong>
    - righe che iniziano con '- ' -> <ul><li>...
    - URL in stile [titolo](link) -> <a>
    - paragrafi -> <p>
    """
    if not txt:
        return ""

    # link [testo](url)
    txt = re.sub(
        r"\[([^\]]+)\]\((https?://[^\)]+)\)",
        lambda m: f"<a href='{m.group(2)}' target='_blank'>{_html_escape(m.group(1))}</a>",
        txt
    )
    # **grassetto**
    txt = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", txt)

    # blocchi a linee
    lines = [l.rstrip() for l in txt.splitlines()]
    html_parts = []
    list_open = False
    for line in lines:
        if not line.strip():
            if list_open:
                html_parts.append("</ul>")
                list_open = False
            continue

        if line.lstrip().startswith("- "):
            if not list_open:
                html_parts.append("<ul>")
                list_open = True
            content = line.lstrip()[2:].strip()
            html_parts.append(f"<li>{content}</li>")
        else:
            if list_open:
                html_parts.append("</ul>")
                list_open = False
            html_parts.append(f"<p>{line}</p>")

    if list_open:
        html_parts.append("</ul>")

    return "\n".join(html_parts)

def _paragraphize(text: str) -> str:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    return "\n".join(f"<p>{_html_escape(p)}</p>" for p in parts)

# -------------------- PDF / FETCH -------------------- #

def _pdf_to_text(url: str, timeout: float = FETCH_TIMEOUT) -> str:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        data = io.BytesIO(r.content)
        text = pdf_extract_text(data) or ""
        text = _clean_spaces(text)
        # rimuovi eventuali intestazioni tipo "%PDF-1.7..." o binary residue (già evitato da BytesIO)
        text = re.sub(r"^%PDF-[^\n]*\n", "", text, flags=re.IGNORECASE)
        return text
    except Exception:
        return ""

FETCH_CACHE: Dict[str, str] = {}

def _fetch_url(url: str, timeout: float = FETCH_TIMEOUT) -> str:
    if url in FETCH_CACHE:
        return FETCH_CACHE[url]
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        ctype = (r.headers.get("content-type") or "").lower()
        if "application/pdf" in ctype or url.lower().endswith(".pdf"):
            text = _pdf_to_text(url, timeout=timeout)
        else:
            soup = BeautifulSoup(r.text, "html.parser")
            for t in soup(["header", "footer", "nav", "script", "style", "noscript", "iframe", "form", "aside"]):
                t.decompose()
            text = _clean_spaces(soup.get_text("\n", strip=True))
        FETCH_CACHE[url] = text
        return text
    except Exception:
        return ""

# -------------------- LINGUA -------------------- #

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

# -------------------- SINAPSI -------------------- #

class CompiledRule:
    def __init__(self, raw: Dict[str, Any]):
        self.id = raw.get("id") or ""
        self.lang = raw.get("lang", "it")
        self.mode = raw.get("mode", "augment")
        self.answer_raw = raw.get("answer", "")
        pat = raw.get("pattern", "")
        try:
            self.regex = re.compile(pat, re.IGNORECASE)
        except Exception:
            self.regex = None

    def match(self, q: str) -> bool:
        return bool(self.regex and self.regex.search(q))

    def html_answer(self) -> str:
        # accetta già HTML oppure markdown “leggero”
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

def sinapsi_apply(q: str, base_html: str, lang: str) -> str:
    """
    Applica regole Sinapsi:
      - override → rimpiazza tutto
      - augment → aggiunge “Approfondimento”
      - postscript → aggiunge nota finale
    """
    matches = [r for r in SINAPSI if r.lang == lang and r.match(q)]
    if not matches:
        return base_html or ""

    # override ha priorità assoluta
    for r in matches:
        if r.mode == "override":
            return r.html_answer()

    html_blocks = [base_html] if base_html else []
    for r in matches:
        if r.mode == "augment":
            html_blocks.append(f"<hr><h4>Approfondimento</h4>\n{r.html_answer()}")
    for r in matches:
        if r.mode == "postscript":
            html_blocks.append(f"<hr><p><em>{r.html_answer()}</em></p>")

    return "\n".join([b for b in html_blocks if b.strip()])

# -------------------- WEB “GUIDATO” -------------------- #

def _in_preferred(url: str) -> bool:
    return any(d.strip().lower() in url.lower() for d in PREFERRED_DOMAINS)

def web_summarize(q: str) -> Tuple[str, List[Tuple[str, str]]]:
    """
    Recupero guidato (no motori esterni): seleziona 2–4 URL affidabili
    in base ai termini della query, estrae testo e produce:
      - un paragrafo narrativo
      - blocco Fonti con link cliccabili
    """
    ql = q.lower()
    candidates: List[Tuple[str, str]] = []

    # Instradamento semplice per temi ricorrenti
    if any(k in ql for k in ["ctf", "p560", "chiod", "hsbr", "lamiera", "hi-bond", "hibond", "grecata"]):
        candidates.extend([
            ("https://tecnaria.com/prodotto/connettore-per-acciaio-ctf/", "Connettore CTF – Tecnaria"),
            ("https://tecnaria.com/solai-in-acciaio/tipologie-consolidamento-solai-acciaio/", "Solai in acciaio – Tecnaria"),
            ("https://tecnaria.com/download/acciaio/download/CT_F_CATALOGO_IT.pdf", "Catalogo acciaio (PDF) – Tecnaria"),
        ])
    if any(k in ql for k in ["ctl", "legno", "tavolato", "maxi", "vite"]):
        candidates.extend([
            ("https://tecnaria.com/solai-in-legno/", "Solai in legno – Tecnaria"),
            ("https://tecnaria.com/download/legno/download/CT_L_CATALOGO_IT.pdf", "Catalogo legno (PDF) – Tecnaria"),
        ])
    if any(k in ql for k in ["diapason", "laterocemento"]):
        candidates.extend([
            ("https://tecnaria.com/solai-in-laterocemento/", "Solai in laterocemento – Tecnaria"),
            ("https://tecnaria.com/faq-recupero-di-solai-in-acciaio-solai-nuovi/", "FAQ solai – Tecnaria"),
        ])

    if not candidates:
        candidates = [
            ("https://tecnaria.com/faq-recupero-di-solai-in-acciaio-solai-nuovi/", "FAQ solai – Tecnaria")
        ]

    sources: List[Tuple[str, str]] = []
    for url, label in candidates[:4]:
        if not _in_preferred(url):
            continue
        txt = _fetch_url(url)
        if txt:
            sources.append((url, label))

    # Sintesi narrativa minima (il “tono tecnico-commerciale” lo rifiniamo in Sinapsi)
    bits = []
    if "ctf" in ql:
        bits.append("I CTF sono connettori per solai collaboranti acciaio–calcestruzzo su lamiera grecata.")
    if "p560" in ql or "chiod" in ql:
        bits.append("La posa avviene dall’alto con SPIT P560; per ogni connettore sono previsti due chiodi HSBR14.")
    if "hi-bond" in ql or "hibond" in ql or "lamiera" in ql:
        bits.append("Il sistema è compatibile con lamiere certificate (es. Hi-Bond) per garantire la piena collaborazione acciaio–calcestruzzo.")
    if "densit" in ql or "m2" in ql or "m²" in ql or "numero" in ql:
        bits.append("A livello indicativo si impiegano ~6–8 CTF per metro quadrato; la quantità esatta è definita dal calcolo strutturale.")
    if "diapason" in ql:
        bits.append("Il sistema Diapason è destinato ai solai in laterocemento esistenti e non utilizza la chiodatrice P560.")

    head = "<h3>Risposta Tecnaria</h3>"
    body = f"<p>{' '.join(bits)}</p>" if bits else ""
    links = "<br>".join([_make_link(u, lbl) for u, lbl in sources]) if sources else ""
    fonti = f"<h4>Fonti</h4><p>{links}</p>" if links else ""

    return f"{head}\n{body}\n{fonti}", sources

# -------------------- CORE ANSWER -------------------- #

def _answer_html(q: str) -> str:
    lang = _detect_lang(q)
    base_html, _ = web_summarize(q)
    final_html = sinapsi_apply(q, base_html, lang)
    if not final_html:
        final_html = (
            "<h3>OK</h3>"
            "<p>Non ho trovato una risposta adeguata. "
            "Posso passarti i contatti Tecnaria o cercare meglio.</p>"
        )
    return final_html

# -------------------- ENDPOINTS -------------------- #

@app.get("/ping")
def ping():
    return {"ok": True, "pong": True}

@app.get("/health")
def health():
    return {
        "status": "ok",
        "web_search": {
            "provider": "internal-lite",
            "preferred_domains": PREFERRED_DOMAINS,
            "min_web_score": MIN_WEB_SCORE,
            "cache_items": len(FETCH_CACHE),
        },
        "critici": {
            "dir": CRITICI_DIR,
            "exists": os.path.isdir(CRITICI_DIR),
            "sinapsi_file": SINAPSI_FILE,
            "sinapsi_loaded": len(SINAPSI),
        }
    }

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    # usa templates/index.html, poi static/index.html, altrimenti landing
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
    html = _answer_html(q)
    if debug:
        dbg = f"<div class='debug'><pre>{_html_escape(q)}</pre></div>"
        return HTMLResponse(dbg + html)
    return HTMLResponse(html)

@app.post("/api/ask")
def ask_post(payload: Dict[str, Any]):
    q = (payload or {}).get("q", "").strip()
    if not q:
        return JSONResponse({"ok": True, "answer": "<h3>OK</h3><p>Domanda vuota: inserisci una richiesta valida.</p>"})
    html = _answer_html(q)
    return JSONResponse({"ok": True, "answer": html})

# -------------------- STARTUP: PRE-WARM -------------------- #

@app.on_event("startup")
def _startup_load():
    # 1) Sinapsi
    global SINAPSI
    SINAPSI = load_sinapsi_rules(SINAPSI_FILE)

    # 2) Precarica qualche pagina chiave Tecnaria in cache
    t0 = time.time()
    for url, _ in PREWARM_SOURCES:
        try:
            if _in_preferred(url):
                _ = _fetch_url(url, timeout=min(FETCH_TIMEOUT, 5.0))
        except Exception:
            pass
    # (Log leggero su console: opzionale)
    print(f"[BOOT] Sinapsi={len(SINAPSI)} rules; Cache preload={len(FETCH_CACHE)} in {time.time()-t0:.2f}s")
