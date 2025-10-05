# app.py
import os, re, json, time, html, unicodedata, asyncio
from typing import List, Dict, Any, Optional, Tuple
from fastapi import FastAPI, Request, Query, Body
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text as pdf_extract_text

APP_TITLE = "Tecnaria QA Bot"
app = FastAPI(title=APP_TITLE)

# ---- Static & templates (serve index.html if present) ------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
os.makedirs(STATIC_DIR, exist_ok=True)
try:
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
except Exception:
    pass
templates = Jinja2Templates(directory=TEMPLATES_DIR) if os.path.isdir(TEMPLATES_DIR) else None

# ---- ENV & defaults ---------------------------------------------------------
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "brave").strip().lower()
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()
PREFERRED_DOMAINS = [d.strip().lower() for d in os.getenv(
    "PREFERRED_DOMAINS",
    "tecnaria.com,spit.eu,spitpaslode.com"
).split(",") if d.strip()]

MIN_WEB_SCORE = float(os.getenv("MIN_WEB_SCORE", "0.35"))

SINAPSI_FILE = os.getenv("SINAPSI_FILE", os.path.join(STATIC_DIR, "data", "sinapsi_rules.json"))

# ---- Utilities --------------------------------------------------------------
def normalize_spaces(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s*\n\s*\n\s*", "\n\n", s)
    return s.strip()

def strip_markdown(s: str) -> str:
    # molto conservativo: togli asterischi/trattini iniziali, lascia testo
    s = re.sub(r"^[\-\*\·]\s+", "", s.strip(), flags=re.MULTILINE)
    s = s.replace("**", "").replace("__", "").replace("*", "")
    return s

def guess_lang(text: str) -> str:
    t = text.lower()
    # rozza euristica sufficiente per routing
    if re.search(r"[äöüß]|(der|die|das|und)\b", t): return "de"
    if re.search(r"\b(the|and|with|without|sheet|steel|deck)\b", t): return "en"
    if re.search(r"\b(el|la|los|las|con|hormigón)\b", t): return "es"
    if re.search(r"\b(le|la|avec|sans|béton)\b", t): return "fr"
    return "it"

def translate_to(text: str, lang: str) -> str:
    # placeholder: qui potresti innestare un traduttore LLM o API; per ora ritorna com'è
    return text

def title_for(url: str) -> str:
    if "tecnaria.com" in url: 
        if "/prodotto/" in url or "/en/prodotto/" in url: return "Scheda tecnica (tecnaria.com)"
        if "/download/" in url: return "Download Tecnaria"
        if "/faq" in url: return "FAQ Tecnaria"
        return "Tecnaria"
    if "spit.eu" in url or "spitpaslode.com" in url: return "SPIT (partner tecnico)"
    return "Riferimento"

# ---- Sinapsi loader (pre-warm) ---------------------------------------------
class SinapsiRule:
    def __init__(self, rid: str, pattern: str, mode: str, lang: str, answer: str):
        self.id = rid
        self.pattern = re.compile(pattern, flags=re.IGNORECASE)
        self.mode = mode  # override | augment | postscript
        self.lang = lang
        self.answer = answer

class SinapsiBrain:
    def __init__(self):
        self.rules: List[SinapsiRule] = []
        self.loaded_file: Optional[str] = None

    def load(self, path: str) -> int:
        self.rules.clear()
        self.loaded_file = None
        if not os.path.isfile(path):
            return 0
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        count = 0
        for obj in data:
            rid = obj.get("id") or f"rule_{count}"
            patt = obj.get("pattern") or ""
            mode = obj.get("mode") or "augment"
            lang = obj.get("lang") or "it"
            answ = obj.get("answer") or ""
            try:
                self.rules.append(SinapsiRule(rid, patt, mode, lang, answ))
                count += 1
            except re.error:
                continue
        self.loaded_file = path
        return count

    def match(self, q: str) -> Tuple[List[SinapsiRule], Optional[SinapsiRule]]:
        # ritorna (augment/postscript list, override single or None)
        override = None
        addons: List[SinapsiRule] = []
        for r in self.rules:
            if r.pattern.search(q):
                if r.mode == "override" and override is None:
                    override = r
                elif r.mode in ("augment", "postscript"):
                    addons.append(r)
        return addons, override

SINAPSI = SinapsiBrain()
os.makedirs(os.path.join(STATIC_DIR, "data"), exist_ok=True)
preload_count = SINAPSI.load(SINAPSI_FILE)

# ---- Web search (Brave) limited to whitelist --------------------------------
async def brave_search(q: str, session: httpx.AsyncClient, lang: str) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        return []
    # Costruiamo query con site:dominio OR ...
    site_filter = " OR ".join([f"site:{d}" for d in PREFERRED_DOMAINS])
    full_q = f"{q} {site_filter}"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    params = {"q": full_q, "count": 8, "country": "it", "search_lang": lang, "safesearch": "strict"}
    try:
        resp = await session.get("https://api.search.brave.com/res/v1/web/search", headers=headers, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        out = []
        for it in (data.get("web", {}).get("results", []) or []):
            url = it.get("url", "")
            domain_ok = any(d in url.lower() for d in PREFERRED_DOMAINS)
            score = float(it.get("score", 0.0))
            if domain_ok and score >= MIN_WEB_SCORE:
                out.append({"url": url, "title": it.get("title") or title_for(url), "score": score})
        return out[:6]
    except Exception:
        return []

async def fetch_text(url: str, session: httpx.AsyncClient) -> str:
    if not any(d in url.lower() for d in PREFERRED_DOMAINS):
        return ""
    try:
        r = await session.get(url, timeout=20)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "pdf" in ctype or url.lower().endswith(".pdf"):
            # scarica e parsifica PDF in memoria
            text = pdf_extract_text(r.content)
        else:
            soup = BeautifulSoup(r.text, "html.parser")
            # rimuovi nav/script/style
            for tag in soup(["nav", "script", "style", "footer", "header", "noscript"]):
                tag.decompose()
            text = soup.get_text("\n")
        text = normalize_spaces(text)
        text = re.sub(r"(%PDF-[\d\.].*?)$", "", text, flags=re.DOTALL)  # sicurezza extra
        return text[:12000]  # taglia
    except Exception:
        return ""

# ---- Composer: costruisce risposta narrativa --------------------------------
def compose_answer(q: str, lang: str, snippets: List[Tuple[str, str]], sinapsi_parts: Dict[str, str]) -> str:
    """
    snippets: list of (url, extracted_text)
    sinapsi_parts: {"override": str|None, "augment": str, "postscript": str}
    """
    # Se override presente: priorità assoluta
    if sinapsi_parts.get("override"):
        body = strip_markdown(sinapsi_parts["override"])
        return render_card(body, origin="Sinapsi (override)")

    # Semplifica testi web in 4-6 bullet con intro
    intro = {
        "it": "Ecco una sintesi tecnico-commerciale basata su documentazione ufficiale Tecnaria:",
        "en": "Here is a concise technical-commercial summary from official Tecnaria documentation:",
        "de": "Technisch-kaufmännische Kurzfassung aus offizieller Tecnaria-Dokumentation:",
        "fr": "Synthèse technico-commerciale issue de la documentation officielle Tecnaria :",
        "es": "Síntesis técnico-comercial basada en documentación oficial de Tecnaria:"
    }.get(lang, "Sintesi:")

    bullets: List[str] = []
    keymap = [
        (r"\bP560\b|\bchiodatrice\b|\bHSBR14\b", {"it": "Fissaggio: SPIT P560 dall’alto, 2 chiodi HSBR14 per connettore.", "en":"Fixing: SPIT P560 top-side, 2 HSBR14 nails per connector."}),
        (r"\bHi[- ]?Bond\b|\blamiera\b|grecata", {"it": "Compatibilità: lamiera grecata certificata (es. Hi-Bond) con piena collaborazione acciaio-calcestruzzo.", "en":"Compatibility: certified steel decking (e.g., Hi-Bond) for full steel-concrete composite action."}),
        (r"\bdensit[aà]|m2|m²|numero|pezzi", {"it":"Densità indicativa: ~6–8 CTF/m²; la quantità esatta è definita da calcolo strutturale.", "en":"Indicative density: ~6–8 CTF/m²; exact amount from structural design."}),
        (r"\bETA\b|certific", {"it":"Normativa: prodotti con ETA e tracciabilità di lotto; seguire le istruzioni di posa ufficiali.", "en":"Compliance: ETA and batch traceability; follow official installation guides."}),
        (r"\bDiapason\b|laterocemento", {"it":"Alternativa: Diapason per solai in laterocemento senza lamiera, con fissaggi meccanici e getto collaborante.", "en":"Alternative: Diapason for clay-concrete slabs without decking; mechanical fasteners plus composite topping."}),
    ]

    merged_text = " ".join(t for _, t in snippets).lower()
    for rx, line_by_lang in keymap:
        if re.search(rx, merged_text):
            bullets.append(line_by_lang.get(lang, line_by_lang["it"]))
    # Se troppo poche evidenze, aggiungi 2 righe generiche utili
    if len(bullets) < 3:
        bullets.extend([
            {"it":"Prestazioni: maggiore rigidezza e capacità portante con deformazioni ridotte.", "en":"Performance: higher stiffness and load capacity with reduced deflections."}.get(lang),
            {"it":"Cantiere: posa rapida e dall’alto, riducendo tempi e disagi.", "en":"Site: fast top-down installation, minimizing time and disruption."}.get(lang)
        ])

    # Sinapsi augment
    augment = strip_markdown(sinapsi_parts.get("augment","")).strip()
    postscript = strip_markdown(sinapsi_parts.get("postscript","")).strip()

    # Fonti (titoli cliccabili)
    links_html = []
    for url, _txt in snippets[:5]:
        ttl = html.escape(title_for(url))
        url_esc = html.escape(url, quote=True)
        links_html.append(f"📎 <a href='{url_esc}' target='_blank'>{ttl}</a>")
    sources = "<br>".join(links_html) if links_html else "—"

    # Monta HTML finale (card), niente tempi/⏱
    bullet_html = "".join([f"<li>{html.escape(b)}</li>" for b in bullets])
    aug_html = f"<p>{html.escape(augment)}</p>" if augment else ""
    ps_html = f"<p><em>{html.escape(postscript)}</em></p>" if postscript else ""
    body = f"""
<p>{html.escape(intro)}</p>
<ul>{bullet_html}</ul>
{aug_html}
{ps_html}
<p><strong>Fonti</strong><br>{sources}</p>
"""
    body = normalize_spaces(body)
    return render_card(body, origin="Web + Sinapsi")

def render_card(inner_html: str, origin: str) -> str:
    # già HTML; non aggiungiamo timing debug
    return f"""
    <div class="card">
      <h2>Risposta Tecnaria</h2>
      {inner_html}
      <p><small>{html.escape(origin)}.</small></p>
    </div>
    """

# ---- Core QA ----------------------------------------------------------------
async def answer_question(q: str) -> str:
    q_clean = normalize_spaces(q)
    lang = guess_lang(q_clean)

    # Sinapsi match
    addons, override = SINAPSI.match(q_clean)
    sinapsi = {"override":"", "augment":"", "postscript":""}
    if override:
        sinapsi["override"] = translate_to(override.answer, lang)
    for r in addons:
        if r.mode == "augment":
            sinapsi["augment"] += "\n" + r.answer
        elif r.mode == "postscript":
            sinapsi["postscript"] += "\n" + r.answer
    if sinapsi["augment"]:
        sinapsi["augment"] = translate_to(strip_markdown(sinapsi["augment"]).strip(), lang)
    if sinapsi["postscript"]:
        sinapsi["postscript"] = translate_to(strip_markdown(sinapsi["postscript"]).strip(), lang)

    # Se override → salta web
    if sinapsi["override"]:
        return compose_answer(q_clean, lang, [], sinapsi)

    # Web search solo domini whitelisted
    snippets: List[Tuple[str,str]] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        if SEARCH_PROVIDER == "brave" and BRAVE_API_KEY:
            results = await brave_search(q_clean, client, lang)
        else:
            results = []  # nessun provider -> ci affidiamo a Sinapsi/risposta base

        # fetch & parse
        for item in results:
            url = item["url"]
            txt = await fetch_text(url, client)
            if not txt: 
                continue
            # anti-sporco PDF/binario: filtra sequenze binarie improbabili
            if "%PDF-" in txt[:20]:
                continue
            snippets.append((url, txt))
            if len(snippets) >= 5:
                break

    # Se niente web utile ma abbiamo augment/postscript, usiamo solo quello
    if not snippets and (sinapsi["augment"] or sinapsi["postscript"]):
        return compose_answer(q_clean, lang, [], sinapsi)

    # Se niente di niente, ritorna “nota” pulita (ma senza scarabocchi)
    if not snippets:
        msg = {
            "it": "Non ho trovato contenuti ufficiali sufficienti nei domini consentiti. Prova a riformulare o chiedi assistenza Tecnaria.",
            "en": "I couldn’t find enough official content within the allowed domains. Try rephrasing or contact Tecnaria support.",
            "de": "Ich habe nicht genügend offizielle Inhalte innerhalb der zulässigen Domains gefunden. Bitte umformulieren oder den Tecnaria-Support kontaktieren."
        }.get(lang, "Non ho trovato contenuti sufficienti nei domini consentiti.")
        return render_card(f"<p>{html.escape(msg)}</p>", origin="Fallback")

    return compose_answer(q_clean, lang, snippets, sinapsi)

# ---- Routes -----------------------------------------------------------------
@app.get("/ping")
def ping():
    return {"ok": True, "pong": True}

@app.get("/health")
def health():
    return {
        "status": "ok",
        "web_search": {
            "provider": SEARCH_PROVIDER,
            "brave_key": bool(BRAVE_API_KEY),
            "preferred_domains": PREFERRED_DOMAINS,
            "min_web_score": MIN_WEB_SCORE
        },
        "critici": {
            "dir": os.path.join(STATIC_DIR, "data"),
            "exists": os.path.isdir(os.path.join(STATIC_DIR, "data")),
            "sinapsi_file": SINAPSI.loaded_file,
            "sinapsi_loaded": len(SINAPSI.rules)
        }
    }

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    # Se esiste templates/index.html usalo; altrimenti una mini UI
    if templates and os.path.isfile(os.path.join(TEMPLATES_DIR, "index.html")):
        return templates.TemplateResponse("index.html", {"request": request, "title": APP_TITLE})
    # mini interfaccia
    return HTMLResponse(f"""
<!doctype html><html lang="it"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{APP_TITLE}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet">
<style>
body{{font-family:Inter,system-ui,Segoe UI,Arial,sans-serif;background:#0b1020;color:#e9eefc;margin:0}}
.wrap{{max-width:980px;margin:0 auto;padding:24px}}
h1{{font-weight:600;letter-spacing:.2px}}
.card{{background:#121936;border:1px solid #22305f;border-radius:14px;padding:18px 20px;box-shadow:0 6px 16px rgba(0,0,0,.25)}}
input[type=text]{{width:100%;padding:14px;border-radius:10px;border:1px solid #22305f;background:#0f1630;color:#e9eefc}}
button{{background:#3c7cff;border:none;color:white;padding:12px 16px;border-radius:10px;cursor:pointer;font-weight:600}}
small{{color:#aab6e8}}
a{{color:#7ab5ff;text-decoration:none}}
a:hover{{text-decoration:underline}}
.row{{display:flex;gap:10px;margin-top:10px}}
</style>
</head><body><div class="wrap">
  <h1>🇮🇹 {APP_TITLE}</h1>
  <div class="card">
    <label for="q">La tua domanda</label>
    <input id="q" type="text" placeholder="Es. Devo usare la P560 per i CTF: serve patentino?">
    <div class="row">
      <button onclick="ask()">Chiedi</button>
      <button onclick="document.getElementById('q').value='';document.getElementById('out').innerHTML=''">Pulisci</button>
    </div>
  </div>
  <div id="out" style="margin-top:14px"></div>
</div>
<script>
async function ask(){{
  const q = document.getElementById('q').value.trim();
  if(!q) return;
  const res = await fetch('/ask?q='+encodeURIComponent(q));
  const html = await res.text();
  document.getElementById('out').innerHTML = html;
}}
</script>
</body></html>
    """)

@app.get("/ask", response_class=HTMLResponse)
async def ask_get(q: str = Query(..., description="Question")):
    html_ans = await answer_question(q)
    return HTMLResponse(html_ans)

@app.post("/api/ask")
async def ask_post(payload: Dict[str, Any] = Body(...)):
    q = (payload.get("q") or "").strip()
    if not q:
        return JSONResponse({"ok": False, "error": "empty question"}, status_code=400)
    html_ans = await answer_question(q)
    return JSONResponse({"ok": True, "html": html_ans})

# ---- Run locally (optional) -------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
