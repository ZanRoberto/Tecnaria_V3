import os, re, json, textwrap
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import requests
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text as pdf_extract_text

APP_TITLE = "Tecnaria QA Bot"
PREFERRED_DOMAINS = os.getenv("PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com").split(",")
MIN_WEB_SCORE = float(os.getenv("MIN_WEB_SCORE", "0.35"))
STATIC_DIR = os.getenv("STATIC_DIR", "static")
TEMPLATES_DIR = os.getenv("TEMPLATES_DIR", "templates")
CRITICI_DIR = os.getenv("CRITICI_DIR", os.path.join(STATIC_DIR, "data"))
SINAPSI_FILE = os.path.join(CRITICI_DIR, "sinapsi_rules.json")

app = FastAPI(title=APP_TITLE)

# static & templates
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR if os.path.isdir(TEMPLATES_DIR) else STATIC_DIR)

# -------------------- UTIL -------------------- #

def _clean_spaces(s: str) -> str:
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))

def _make_link(url: str, label: str) -> str:
    label = _html_escape(label)
    return f"📎 <a href='{url}' target='_blank'>{label}</a>"

def _pdf_to_text(url: str, timeout: float = 6.0) -> str:
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        # estrai SOLO testo; niente oggetti binari → niente "%PDF…"
        text = pdf_extract_text(io=r.content) if hasattr(pdf_extract_text, "__call__") else ""
    except Exception:
        # fallback: scarica su disco e passa path (alcuni ambienti lo richiedono)
        import tempfile
        text = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
                tmp.write(requests.get(url, timeout=timeout).content)
                tmp.flush()
                text = pdf_extract_text(tmp.name)
        except Exception:
            pass
    if not text:
        return ""
    text = _clean_spaces(text)
    # rimuovi eventuali “rumori” tipici PDF
    text = re.sub(r"[%][A-Z]{3,5}-\d\.\d.*?\n", "", text)
    return text

def _htmlize_paragraphs(text: str) -> str:
    # niente asterischi/markdown → tag HTML puliti
    parts = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    html = []
    for p in parts:
        # micro-bullet in frasi → mantieni come <p>, non <li>, per look commerciale
        html.append(f"<p>{_html_escape(p)}</p>")
    return "\n".join(html)

def _detect_lang(q: str) -> str:
    # euristica semplice: it/en/de/fr
    ql = q.lower()
    if re.search(r"[àèéìòù]|[a-z]+[àèéìòù]", ql) or "che " in ql or "qual" in ql:
        return "it"
    if re.search(r"\bwhat|how|when|which|do i|should i|can i\b", ql):
        return "en"
    if re.search(r"\bwas|wie|wann|welche|sollte ich|kann ich\b", ql):
        return "de"
    if re.search(r"\bquoi|comment|quand|lequel|devrais-je|puis-je\b", ql):
        return "fr"
    return "it"

# -------------------- SINAPSI -------------------- #

def load_sinapsi():
    rules = []
    if os.path.isfile(SINAPSI_FILE):
        try:
            with open(SINAPSI_FILE, "r", encoding="utf-8") as f:
                rules = json.load(f)
        except Exception:
            rules = []
    return rules

SINAPSI = load_sinapsi()

def sinapsi_apply(q: str, base_html: str, lang: str) -> str:
    """
    Applica regole:
      - override: ignora web e restituisce answer
      - augment: aggiunge box “Approfondimento”
      - postscript: aggiunge nota finale
    Tutto in HTML, senza asterischi.
    """
    applied = []
    for r in SINAPSI:
        try:
            if r.get("lang", "it") != lang:
                continue
            pat = r.get("pattern", "")
            if not pat:
                continue
            if re.search(pat, q, flags=re.IGNORECASE):
                mode = r.get("mode", "augment")
                ans = r.get("answer", "").strip()
                if mode == "override":
                    applied.append(("override", ans))
                    break
                elif mode == "augment":
                    applied.append(("augment", ans))
                elif mode == "postscript":
                    applied.append(("postscript", ans))
        except Exception:
            continue

    if not applied:
        return base_html

    # se esiste override → prendi solo quello
    for t, ans in applied:
        if t == "override":
            return ans

    # altrimenti assembla
    html = [base_html] if base_html else []
    for t, ans in applied:
        if t == "augment":
            html.append(f"<hr><h4>Approfondimento</h4>\n{ans}")
    for t, ans in applied:
        if t == "postscript":
            html.append(f"<hr><p><em>{ans}</em></p>")
    return "\n".join([h for h in html if h.strip()])

# -------------------- WEB SCRAPE LEGGERO (FOCUS TECNARIA) -------------------- #

def _fetch_url(url: str, timeout: float = 6.0) -> str:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
        r.raise_for_status()
        ctype = r.headers.get("content-type","").lower()
        if "application/pdf" in ctype or url.lower().endswith(".pdf"):
            return _pdf_to_text(url)
        # html
        soup = BeautifulSoup(r.text, "html.parser")
        # togli nav/footer/aside
        for t in soup(["header","footer","nav","script","style","noscript","iframe"]):
            t.decompose()
        text = soup.get_text("\n", strip=True)
        return _clean_spaces(text)
    except Exception:
        return ""

def web_summarize(q: str) -> (str, list):
    """
    Molto semplice: cerca 2–4 pagine note (Tecnaria) per topic noti.
    Evita d’inserire direttamente il testo grezzo → produci un box Fonti + breve sintesi.
    """
    ql = q.lower()
    candidates = []

    # routing basilare per evitare risultati “strani”
    if any(k in ql for k in ["ctf","lamiera","hi-bond","p560","chiodi","hsbr"]):
        candidates.extend([
            ("https://tecnaria.com/prodotto/connettore-per-acciaio-ctf/", "Scheda tecnica CTF – Tecnaria"),
            ("https://tecnaria.com/solai-in-acciaio/tipologie-consolidamento-solai-acciaio/", "Solai in acciaio – Tipologie – Tecnaria"),
            ("https://tecnaria.com/download/acciaio/download/CT_F_CATALOGO_IT.pdf", "Catalogo acciaio (PDF) – Tecnaria"),
        ])
    if any(k in ql for k in ["ctl","legno","tavolato","vite","maxi"]):
        candidates.extend([
            ("https://tecnaria.com/solai-in-legno/", "Solai in legno – Tecnaria"),
            ("https://tecnaria.com/download/legno/download/CT_L_CATALOGO_IT.pdf", "Catalogo legno (PDF) – Tecnaria"),
        ])
    if any(k in ql for k in ["diapason","laterocemento"]):
        candidates.extend([
            ("https://tecnaria.com/solai-in-laterocemento/", "Solai in laterocemento – Tecnaria"),
            ("https://tecnaria.com/faq-recupero-di-solai-in-acciaio-solai-nuovi/", "FAQ solai in acciaio – Tecnaria"),
        ])
    if not candidates:
        # fallback neutro, solo domini preferiti
        candidates = [
            ("https://tecnaria.com/faq-recupero-di-solai-in-acciaio-solai-nuovi/", "FAQ solai – Tecnaria")
        ]

    texts = []
    sources = []
    for url, label in candidates[:4]:
        txt = _fetch_url(url)
        if txt:
            texts.append((label, txt))
            sources.append((url, label))

    # sintesi minimal (completa poi con Sinapsi)
    bullets = []
    if "ctf" in ql:
        bullets.append("I CTF sono connettori per solai collaboranti acciaio–calcestruzzo su lamiera grecata.")
    if "p560" in ql or "chiod" in ql:
        bullets.append("La posa avviene con SPIT P560 e 2 chiodi HSBR14 per connettore (senza preforatura).")
    if "hi-bond" in ql:
        bullets.append("Compatibile con lamiere certificate (es. Hi-Bond), con piena collaborazione acciaio–cls.")
    if "densit" in ql or "m2" in ql or "m²" in ql:
        bullets.append("Densità indicativa: ~6–8 CTF/m² (più fitta agli appoggi, più rada in mezzeria).")
    if "diapason" in ql:
        bullets.append("Diapason è per laterocemento: fissaggi meccanici e getto collaborante dall’alto (senza P560).")

    head = "<h3>Risposta Tecnaria</h3>"
    body = ""
    if bullets:
        body = "<p>" + " ".join(bullets) + "</p>"

    if sources:
        links = "<br>".join([_make_link(u, lbl) for u, lbl in sources])
        fonti = f"<h4>Fonti</h4><p>{links}</p>"
    else:
        fonti = ""

    return f"{head}\n{body}\n{fonti}", sources

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
        },
        "critici": {
            "dir": CRITICI_DIR,
            "exists": os.path.isdir(CRITICI_DIR),
            "sinapsi_file": SINAPSI_FILE,
            "sinapsi_loaded": len(SINAPSI),
        }
    }

def _answer_html(q: str) -> str:
    lang = _detect_lang(q)
    base_html, _ = web_summarize(q)
    final_html = sinapsi_apply(q, base_html, lang)
    if not final_html:
        final_html = "<h3>OK</h3><p>Non ho trovato una risposta adeguata. Posso passarti i contatti Tecnaria o cercare meglio.</p>"
    return final_html

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    # se c’è templates/index.html lo usa, altrimenti static/index.html, altrimenti landing minima
    idx_path = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.isfile(idx_path):
        return templates.TemplateResponse("index.html", {"request": request, "service": APP_TITLE})
    idx_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(idx_path):
        with open(idx_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse(f"<h2>{APP_TITLE}</h2><p>Usa <code>/ask?q=...</code> oppure metti <code>static/index.html</code>.</p>")

@app.get("/ask", response_class=HTMLResponse)
def ask_get(q: str = Query(""), debug: bool = Query(False)):
    q = (q or "").strip()
    if not q:
        return HTMLResponse("<h3>OK</h3><p>Domanda vuota: inserisci una richiesta valida.</p>")
    html = _answer_html(q)
    if debug:
        return HTMLResponse(f"<div class='debug'><pre>{_html_escape(q)}</pre></div>{html}")
    return HTMLResponse(html)

@app.post("/api/ask")
async def ask_post(payload: dict):
    q = (payload or {}).get("q", "").strip()
    if not q:
        return JSONResponse({"ok": True, "answer": "OK\nDomanda vuota: inserisci una richiesta valida."})
    html = _answer_html(q)
    # client legacy vuole “answer” come testo → restituisco già HTML
    return JSONResponse({"ok": True, "answer": html})
