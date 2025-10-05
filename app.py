import os, json, re, asyncio, time
from typing import List, Dict, Any, Optional
from urllib.parse import quote_plus, urlparse

import requests
from fastapi import FastAPI, Query, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
TEMPLATES_DIR = os.path.join(APP_DIR, "templates")
DATA_DIR = os.path.join(STATIC_DIR, "data")

ALLOWED_DOMAINS = {"tecnaria.com", "spit.eu", "spitpaslode.com"}

BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "brave").lower()

SINAPSI_FILE = os.path.join(DATA_DIR, "sinapsi_rules.json")
SINAPSI: List[Dict[str, Any]] = []

app = FastAPI(title="Tecnaria QA Bot")

# --- TEMPLATES / STATIC
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=True)

def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
ensure_dirs()

# ---------- UTIL ----------
def is_allowed(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
        return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)
    except:
        return False

def render_source(title: str, url: str) -> str:
    safe_title = title or "Documento"
    return f"📎 <a href='{url}' target='_blank'>{safe_title}</a>"

def clean_pdf_noise(text: str) -> str:
    # Se accidentalmente arriva blob PDF, tagliamo tutto
    if "%PDF" in text[:100] or "\x00" in text[:200]:
        return ""
    return text

def load_sinapsi() -> List[Dict[str, Any]]:
    if not os.path.isfile(SINAPSI_FILE):
        return []
    try:
        with open(SINAPSI_FILE, "r", encoding="utf-8") as f:
            rules = json.load(f)
        # normalizza
        for r in rules:
            r.setdefault("mode", "augment")
            r.setdefault("lang", "it")
        return rules
    except Exception as e:
        print("[SINAPSI] load error:", e)
        return []

def apply_sinapsi(q: str, base_html: str, lang: str = "it") -> str:
    """Applica override/augment/postscript in base a regex."""
    text = base_html
    for r in SINAPSI:
        pat = r.get("pattern", "")
        mode = r.get("mode", "augment")
        ans = r.get("answer", "")
        try:
            if re.search(pat, q, flags=re.I):
                if mode == "override":
                    return ans
                elif mode == "augment":
                    # attacca l’aggiunta alla fine, separatore pulito
                    return text + ("\n\n" if text else "") + ans
                elif mode == "postscript":
                    return text + ("\n\n—\n" + ans)
        except re.error:
            continue
    return text

def brave_site_search(query: str, domains: List[str], count: int = 5) -> List[Dict[str, str]]:
    """
    Ricerca site:dominio con Brave (se key presente), altrimenti ritorna vuoto.
    NB: filtriamo a livello di query + filtriamo di nuovo i risultati.
    """
    results = []
    if not BRAVE_API_KEY or SEARCH_PROVIDER != "brave":
        return results
    try:
        # costruiamo una query “site:dom OR site:dom …”
        site_q = " OR ".join([f"site:{d}" for d in domains])
        full_q = f"{query} ({site_q})"
        headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
        params = {"q": full_q, "count": count, "country": "it"}
        resp = requests.get("https://api.search.brave.com/res/v1/web/search", headers=headers, params=params, timeout=6)
        if resp.status_code != 200:
            return results
        data = resp.json()
        for item in data.get("web", {}).get("results", []):
            url = item.get("url")
            title = item.get("title") or item.get("description") or url
            if url and is_allowed(url):
                results.append({"title": title, "url": url})
        return results
    except Exception as e:
        print("[BRAVE] search error:", e)
        return results

def synthesize_answer(q: str, lang: str = "it") -> str:
    """
    Risposta tecnico-commerciale breve sintetizzata dalle fonti consentite.
    1) Cerca con Brave limitato ai domini ammessi (se KEY presente)
    2) Compone una “risposta sintetica” + lista fonti (solo link cliccabili)
    3) Lascia spazio a Sinapsi per perfezionare (augment)
    """
    sources = brave_site_search(q, sorted(ALLOWED_DOMAINS))
    # risposta base minima (se niente fonti)
    if not sources:
        base = "Ho cercato nei contenuti ufficiali Tecnaria (e partner ammessi), ma non ho trovato una pagina perfettamente aderente alla richiesta. Posso riformulare la ricerca o proporti i contatti Tecnaria."
        return base

    # “titolo”: risposta sintetica neutra
    base = "Ho cercato nei contenuti ufficiali Tecnaria (e partner) e ho selezionato le fonti pertinenti.\n\n"
    base += "Fonti\n"
    for s in sources[:6]:
        base += render_source(s["title"], s["url"]) + "<br>"
    return base

async def answer_query(q: str, lang: str = "it") -> str:
    # 1) base web-limitata
    base = synthesize_answer(q, lang)
    base = clean_pdf_noise(base)

    # 2) sinapsi refine
    refined = apply_sinapsi(q, base, lang)

    # 3) ultima pulizia (no doppioni “Fonti” / righe vuote)
    refined = refined.replace("\r\n", "\n").strip()
    return refined

# ---------- PREWARM ----------
async def prewarm():
    warm = [
        "Differenza tra CTF e Diapason",
        "Densità connettori CTF su lamiera Hi-Bond",
        "Serve patentino per SPIT P560?",
        "Contatti Tecnaria",
    ]
    for q in warm:
        try:
            await answer_query(q)
        except Exception as e:
            print("[PREWARM]", q, e)
    print("[PREWARM] done")

# ---------- ROUTES ----------
@app.on_event("startup")
async def on_startup():
    global SINAPSI
    SINAPSI[:] = load_sinapsi()
    asyncio.create_task(prewarm())

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
            "preferred_domains": sorted(ALLOWED_DOMAINS),
        },
        "critici": {
            "dir": DATA_DIR,
            "exists": os.path.isdir(DATA_DIR),
            "sinapsi_file": SINAPSI_FILE,
            "sinapsi_loaded": len(SINAPSI),
        }
    }

@app.get("/", response_class=HTMLResponse)
def home():
    # se esiste templates/index.html lo rendiamo, altrimenti landing minima
    tpl_path = os.path.join(TEMPLATES_DIR, "index.html")
    alt_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(tpl_path):
        return env.get_template("index.html").render()
    elif os.path.isfile(alt_path):
        with open(alt_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    else:
        return HTMLResponse("<pre>{\"ok\":true,\"msg\":\"Use /ask or place static/index.html\"}</pre>")

@app.get("/ask", response_class=HTMLResponse)
async def ask_get(q: str = Query(..., description="Domanda")):
    ans = await answer_query(q)
    # Ritorniamo HTML pulito
    html = f"""
    <article style="max-width:900px;margin:24px auto;font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,Ubuntu;">
      <h1 style="font-size:20px;margin:0 0 8px;">Risposta Tecnaria</h1>
      <div>{ans}</div>
    </article>
    """
    return HTMLResponse(html)

@app.post("/api/ask")
async def ask_post(payload: Dict[str, Any] = Body(...)):
    q = payload.get("q", "").strip()
    lang = payload.get("lang", "it")
    if not q:
        return {"ok": True, "answer": "Inserisci una domanda valida."}
    ans = await answer_query(q, lang)
    return {"ok": True, "answer": ans}

@app.get("/sinapsi/reload")
def sinapsi_reload():
    global SINAPSI
    SINAPSI[:] = load_sinapsi()
    return {"ok": True, "loaded": len(SINAPSI)}
