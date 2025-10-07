# app.py
# Tecnaria – QA Bot (web-first, IT-only, sinapsi assist)

import os
import json
import re
import html
import time
from typing import List, Dict, Any, Optional

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------

def getenv_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}

def getenv_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None:
        return default
    try:
        return int(v)
    except:
        return default

APP_NAME = "Tecnaria – Assistente Tecnico"

BRAVE_API_KEYS = [k.strip() for k in os.getenv("BRAVE_API_KEY", "").split(",") if k.strip()]
PREFERRED_DOMAINS = [d.strip() for d in os.getenv("PREFERRED_DOMAINS", "tecnaria.com,www.tecnaria.com").split(",") if d.strip()]
LANG_PREFERRED = os.getenv("LANG_PREFERRED", "it").lower()

ACCEPT_EN_BACKFILL = getenv_bool("ACCEPT_EN_BACKFILL", False)
USE_SNIPPET_BACKFILL = getenv_bool("USE_SNIPPET_BACKFILL", False)

MIN_WEB_OK_CHARS = getenv_int("MIN_WEB_OK_CHARS", 80)
MIN_WEB_OK_SENTENCES = getenv_int("MIN_WEB_OK_SENTENCES", 1)

MAX_ANSWER_CHARS = getenv_int("MAX_ANSWER_CHARS", 2000)

SOURCES_MAX = getenv_int("SOURCES_MAX", 3)
SOURCES_SHOW_SNIPPETS = getenv_bool("SOURCES_SHOW_SNIPPETS", False)
SOURCES_COLLAPSED = getenv_bool("SOURCES_COLLAPSED", True)

SINAPSI_FILE = os.getenv("SINAPSI_FILE", "static/data/sinapsi_rules.json")
SINAPSI_MODE = os.getenv("SINAPSI_MODE", "assist").lower()  # off | assist | strict
ALLOW_SINAPSI_OVERRIDE = getenv_bool("ALLOW_SINAPSI_OVERRIDE", False)

DISAMBIG_STRICT = getenv_bool("DISAMBIG_STRICT", True)

WEB_RESULTS_COUNT_PREFERRED = getenv_int("WEB_RESULTS_COUNT_PREFERRED", 6)
REFINE_ALWAYS = getenv_bool("REFINE_ALWAYS", False)
DEBUG = getenv_bool("DEBUG", False)

EXCLUDE_ANY_Q = [
    r"\bprezz\w*", r"\bcost\w*", r"\bpreventiv\w*", r"\boffert\w*"
]

# -----------------------------------------------------------------------------
# SINAPSI RULES (ASSIST)
# -----------------------------------------------------------------------------

def load_sinapsi_rules(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "rules" in data:
                return data.get("rules", [])
            if isinstance(data, list):
                return data
    except Exception as e:
        if DEBUG:
            print("Sinapsi load error:", e)
    return []

SINAPSI_RULES = load_sinapsi_rules(SINAPSI_FILE)

def sinapsi_assist(q: str) -> Optional[str]:
    ql = q.lower()

    if "p560" in ql and ("patentino" in ql or "licenza" in ql):
        return "Per la SPIT P560 non è richiesto alcun patentino: è a tiro indiretto (classe A). Obbligatori DPI e rispetto del manuale."

    if ("ctf" in ql) and ("m2" in ql or "mq" in ql or "al m" in ql):
        return "La quantità di CTF deriva dal calcolo; ordine di grandezza ~6–8 connettori/m², più fitti agli appoggi."

    if ("ctf" in ql and "diapason" in ql) or ("differenza" in ql and ("ctf" in ql or "diapason" in ql)):
        return "CTF: travi in acciaio con lamiera grecata (posa a sparo). Diapason: laterocemento senza lamiera (fissaggi nei travetti; getto dall’alto)."

    for r in SINAPSI_RULES:
        try:
            kws = [k.lower() for k in r.get("keywords", [])]
            if kws and all(k in ql for k in kws):
                s = r.get("answer_short") or r.get("answer") or ""
                s = s.strip()
                if s:
                    return s
        except:
            pass

    return None

# -----------------------------------------------------------------------------
# WEB SEARCH (Brave)
# -----------------------------------------------------------------------------

def brave_headers() -> Dict[str, str]:
    key = BRAVE_API_KEYS[0] if BRAVE_API_KEYS else ""
    return {"Accept": "application/json", "X-Subscription-Token": key} if key else {}

def make_query(q: str) -> str:
    site_filter = " OR ".join([f"site:{d}" for d in PREFERRED_DOMAINS])
    lang = " lang:it"
    return f"({q}) ({site_filter}){lang}"

def search_brave_json(q: str, count: int = 6) -> Dict[str, Any]:
    if not BRAVE_API_KEYS:
        return {}
    url = "https://api.search.brave.com/res/v1/web/search"
    params = {"q": make_query(q), "count": count}
    try:
        r = requests.get(url, headers=brave_headers(), params=params, timeout=12)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        if DEBUG:
            print("Brave error:", e)
    return {}

def pick_results(json_payload: Dict[str, Any]) -> List[Dict[str, str]]:
    out = []
    web = json_payload.get("web", {})
    results = web.get("results", [])
    for item in results:
        url = item.get("url")
        title = item.get("title") or url
        snippet = item.get("description") or ""
        if url and any(url.startswith(f"https://{d}") or url.startswith(f"http://{d}") or (f".{d}/" in url) for d in PREFERRED_DOMAINS):
            out.append({"url": url, "title": title, "snippet": snippet})
    return out

def fetch_html(url: str, timeout: int = 10) -> str:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code == 200 and "text/html" in r.headers.get("Content-Type", ""):
            return r.text
    except:
        pass
    return ""

def html_to_text(html_str: str) -> str:
    soup = BeautifulSoup(html_str, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def split_sentences_it(text: str) -> List[str]:
    s = re.split(r"(?<=[\.\!\?])\s+", text)
    s = [x.strip() for x in s if len(x.strip()) >= 8]
    return s

def looks_italian(s: str) -> bool:
    it_words = [" il ", " la ", " lo ", " gli ", " delle ", " degli ", " che ", " con ", " per ", " su ", " tra ", " in ", " non "]
    s_low = f" {s.lower()} "
    return any(w in s_low for w in it_words)

def first_italian_paragraph(text: str, min_sentences: int = 1, max_chars: int = 600) -> Optional[str]:
    sents = split_sentences_it(text)
    bucket = []
    for st in sents:
        if looks_italian(st):
            bucket.append(st)
        if len(bucket) >= min_sentences and sum(len(x)+1 for x in bucket) >= 80:
            break
    if not bucket:
        return None
    para = " ".join(bucket)
    return para[:max_chars].strip()

# -----------------------------------------------------------------------------
# ANSWER BUILDER
# -----------------------------------------------------------------------------

def clean_text(s: str) -> str:
    s = s.replace("\u200b", "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

def compose_answer(q: str, web_paras: List[str], assist_line: Optional[str]) -> str:
    ql = q.lower()

    if "p560" in ql and ("patentino" in ql or "licenza" in ql):
        base = "Per la chiodatrice SPIT P560 non serve alcun patentino né autorizzazioni speciali: è a tiro indiretto (classe A) con propulsori a salve; restano obbligatori i DPI e il rispetto del manuale."
        if assist_line and assist_line not in base:
            return f"{base} {assist_line}"
        return base

    if ("ctf" in ql) and ("m2" in ql or "mq" in ql or "al m" in ql):
        base = "La quantità di connettori CTF deriva dal calcolo strutturale (luci, carichi, profilo lamiera, spessore soletta). Ordine di grandezza: circa 6–8 CTF/m², più fitti agli appoggi."
        if assist_line and assist_line not in base:
            return f"{base} {assist_line}"
        return base

    if ("ctf" in ql and "diapason" in ql) or ("differenza" in ql and ("ctf" in ql or "diapason" in ql)):
        base = "CTF: solai su travi in acciaio con lamiera grecata (posa a sparo). Diapason: laterocemento senza lamiera (fissaggi nei travetti; getto dall’alto). La scelta dipende dal tipo di solaio."
        if assist_line and assist_line not in base:
            return f"{base} {assist_line}"
        return base

    if web_paras:
        body = web_paras[0][:MAX_ANSWER_CHARS]
        if assist_line and assist_line not in body:
            body = body + " " + assist_line
        return body

    if assist_line:
        return assist_line

    return "Non ho trovato contenuti sufficienti su fonti Tecnaria. Prova a riformulare la domanda."

def unique_sources(items: List[Dict[str, str]], limit: int) -> List[Dict[str, str]]:
    seen = set()
    out = []
    for it in items:
        url = it.get("url", "")
        host = re.sub(r"^https?://", "", url).split("/")[0]
        if host in seen:
            continue
        seen.add(host)
        out.append(it)
        if len(out) >= limit:
            break
    return out

# -----------------------------------------------------------------------------
# FASTAPI
# -----------------------------------------------------------------------------

app = FastAPI(title=APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------ UI (JS separato) -----------------------------

JS_APP = """
async function ev(e) { 
  e.preventDefault();
  const qEl = document.getElementById('q');
  const q = (qEl.value || '').trim();
  if (!q) return;
  const btn = document.querySelector('#f button[type="submit"]');
  btn.disabled = true;
  try {
    const r = await fetch('/api/ask', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ q })
    });
    const j = await r.json();
    const out = document.getElementById('out');
    out.innerHTML = j.html || '<div class="card"><p>Nessuna risposta.</p></div>';
    out.scrollIntoView({ behavior: 'smooth' });
  } catch (e) {
    document.getElementById('out').innerHTML = '<div class="card"><p>Errore di rete.</p></div>';
    console.error(e);
  } finally {
    btn.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('f');
  if (form) form.addEventListener('submit', ev);
});
"""

CSS_APP = """
*{box-sizing:border-box} body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,'Helvetica Neue',Arial;line-height:1.45;margin:0;background:#f7faf7}
.container{max-width:1100px;margin:0 auto;padding:18px}
.card{background:#fff;border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.08);padding:16px}
h1{font-size:22px;margin:0 0 10px}
.brand{color:#147e43;font-weight:800;letter-spacing:.5px}
.topbar{display:flex;align-items:center;gap:16px}
#f{display:flex;gap:8px;margin:10px 0}
#q{width:100%;padding:12px;border:1px solid #d7e2d9;border-radius:10px}
.btn{background:#147e43;color:#fff;border:none;border-radius:10px;padding:10px 14px;cursor:pointer}
.btn:disabled{opacity:.6;cursor:not-allowed}
.nav{display:flex;gap:.5rem;flex-wrap:wrap;margin:.5rem 0 1rem 0}
.sources{margin-top:8px}
details summary{cursor:pointer;font-weight:600}
kbd.sugg{display:inline-block;background:#eef6ef;border:1px solid #cfe4d2;padding:6px 10px;border-radius:20px;margin-right:8px}
.small{font-size:12px;color:#556}
"""

@app.get("/", response_class=HTMLResponse)
def index():
    html_page = f"""
<!doctype html>
<html lang="it">
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>{html.escape(APP_NAME)}</title>
    <style>{CSS_APP}</style>
  </head>
  <body>
    <div class="container">
      <div class="topbar">
        <div class="brand">TECNARIA</div>
        <h1>{html.escape(APP_NAME)}</h1>
      </div>

      <form id="f">
        <input id="q" name="q" placeholder="Fai una domanda (es. Serve il patentino per la P560?)"/>
        <button type="submit" class="btn">Chiedi</button>
      </form>

      <div style="margin:6px 0 12px">
        <span class="kbd sugg">Differenza CTF e Diapason</span>
        <span class="kbd sugg">Quanti CTF al m²</span>
        <span class="kbd sugg">Serve il patentino per la P560?</span>
      </div>

      <div id="out" class="card"></div>

      <p class="small">© Tecnaria S.p.A. – Questo assistente sintetizza contenuti ufficiali e regole Sinapsi.</p>
    </div>
    <script>
{JS_APP}
    </script>
  </body>
</html>
"""
    return HTMLResponse(html_page)

# ------------------------------ HEALTH ---------------------------------------

@app.get("/health")
def health():
    return JSONResponse({
        "status": "ok",
        "web_enabled": bool(BRAVE_API_KEYS),
        "preferred_domains": PREFERRED_DOMAINS,
        "rules_loaded": len(SINAPSI_RULES),
        "exclude_any_q": EXCLUDE_ANY_Q,
        "sinapsi_file": SINAPSI_FILE,
        "lang_preferred": LANG_PREFERRED,
        "disambig_strict": DISAMBIG_STRICT,
        "answer_mode": "full",
        "max_answer_chars": MAX_ANSWER_CHARS,
        "fetch_tecnaria": True,
        "allow_sinapsi_override": ALLOW_SINAPSI_OVERRIDE,
        "sources_show_snippets": SOURCES_SHOW_SNIPPETS,
        "sinapsi_mode": SINAPSI_MODE,
        "min_web_ok_chars": MIN_WEB_OK_CHARS,
        "min_web_ok_sentences": MIN_WEB_OK_SENTENCES,
        "accept_en_backfill": ACCEPT_EN_BACKFILL,
        "use_snippet_backfill": USE_SNIPPET_BACKFILL,
        "sources_max": SOURCES_MAX,
        "sources_collapsed": SOURCES_COLLAPSED,
        "web_results_count_preferred": WEB_RESULTS_COUNT_PREFERRED,
        "refine_always": REFINE_ALWAYS,
        "debug": DEBUG,
        "app": "web->fetch_tecnaria->(sinapsi assist)->render"
    })

# ------------------------------ API/ASK --------------------------------------

def blocked_by_exclude(q: str) -> bool:
    for pat in EXCLUDE_ANY_Q:
        if re.search(pat, q, flags=re.I):
            return True
    return False

def render_sources(sources: List[Dict[str, str]]) -> str:
    if not sources:
        return ""
    lis = []
    for i, s in enumerate(sources, 1):
        t = html.escape(s.get("title") or s.get("url") or f"Fonte {i}")
        u = html.escape(s.get("url") or "#")
        item = f'<li><a href="{u}" target="_blank" rel="noopener">{t}</a></li>'
        if SOURCES_SHOW_SNIPPETS:
            sn = s.get("snippet", "")
            if sn:
                item += f"<br><small>{html.escape(sn)}</small>"
        lis.append(item)
    ol = "<ol class='list-decimal pl-5'>" + "".join(lis) + "</ol>"
    if SOURCES_COLLAPSED:
        return f"<details><summary><strong>Fonti</strong></summary><div style='margin:.5rem 0'><button type='button' onclick=\"this.closest('details').removeAttribute('open')\">Chiudi fonti</button></div>{ol}</details>"
    return "<h3>Fonti</h3>" + ol

def build_nav() -> str:
    # NIENTE graffe {}, così l'f-string è sicura
    return "<div class='nav'><button class='btn' onclick=\"history.back()\">⬅ Torna indietro</button> <a class='btn' href='/'>Home</a></div>"

@app.post("/api/ask")
async def api_ask(payload: Dict[str, Any]):
    t0 = time.time()
    q = clean_text(str(payload.get("q", "")))

    if not q:
        return JSONResponse({"ok": True, "html": "<div class='card'><p>Scrivi una domanda.</p></div>"})

    if blocked_by_exclude(q):
        return JSONResponse({"ok": True, "html": "<div class='card'><p>Per preventivi, prezzi o offerte rivolgersi al canale commerciale.</p></div>"})

    # 1) WEB SEARCH
    sources: List[Dict[str, str]] = []
    web_paragraphs: List[str] = []

    if BRAVE_API_KEYS:
        json_search = search_brave_json(q, count=WEB_RESULTS_COUNT_PREFERRED)
        results = pick_results(json_search)
        sources = unique_sources(results, SOURCES_MAX)

        for it in sources:
            if len(web_paragraphs) >= 2:
                break
            html_raw = fetch_html(it["url"])
            if not html_raw:
                continue
            text = html_to_text(html_raw)
            para = first_italian_paragraph(text, min_sentences=MIN_WEB_OK_SENTENCES, max_chars=600)
            if para:
                web_paragraphs.append(para)

    # 2) SINAPSI (assist)
    assist_line = None
    if SINAPSI_MODE in ("assist", "strict"):
        assist_line = sinapsi_assist(q)

    # 3) COMPOSIZIONE RISPOSTA
    answer = compose_answer(q, web_paragraphs, assist_line).strip()

    if len(answer) > MAX_ANSWER_CHARS:
        answer = answer[:MAX_ANSWER_CHARS].rsplit(" ", 1)[0] + "…"

    nav = build_nav()
    src_html = render_sources(sources)
    dt = int((time.time() - t0) * 1000)

    html_card = f"<div class='card'><h2>Risposta Tecnaria</h2>{nav}<p>{html.escape(answer)}</p>{src_html}{nav}<p><small>⏱ {dt} ms</small></p></div>"
    return JSONResponse({"ok": True, "html": html_card})

# -----------------------------------------------------------------------------
# MAIN (local)
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
