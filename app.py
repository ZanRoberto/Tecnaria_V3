# app.py
import os, re, json, time, html
import requests
from typing import List, Dict, Optional, Tuple
from fastapi import FastAPI, Query, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from bs4 import BeautifulSoup

APP_NAME = "Tecnaria QA Bot"

# -------------------- Config --------------------
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()
PREFERRED_DOMAINS = [d.strip() for d in os.getenv("PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com").split(",") if d.strip()]
MIN_WEB_SCORE = float(os.getenv("MIN_WEB_SCORE", "0.35"))
CRITICI_DIR = os.getenv("CRITICI_DIR", "static/data/critici").rstrip("/")

FORCE_WEB = os.getenv("CRITICAL_ENRICH_FORCE_WEB", "0") == "1"

SINAPSI_FILE = os.path.join(CRITICI_DIR, "sinapsi_rules.json")

# -------------------- Utils: Cleaner --------------------
JS_GATE_PATTERNS = [
    r'Just a moment\.\.\.',
    r'Enable JavaScript and cookies to continue',
    r'WP 3D Thingviewer Lite need Javascript to work',
    r'Salta ai contenuti',
]

def strip_js_gate(text: str) -> str:
    if not text:
        return ""
    lines = []
    for ln in text.splitlines():
        if not any(re.search(p, ln, flags=re.I) for p in JS_GATE_PATTERNS):
            lines.append(ln)
    x = "\n".join(lines)
    x = re.sub(r'</?(strong|b|em|i|u)>', '', x, flags=re.I)
    x = re.sub(r'\s{2,}', ' ', x)
    return x.strip()

def html_to_text_safe(raw: str) -> str:
    if not raw:
        return ""
    # se sembra già testo
    if "<" not in raw and ">" not in raw:
        return strip_js_gate(raw)
    soup = BeautifulSoup(raw, "html.parser")
    ogd = soup.find("meta", {"property": "og:description"})
    if ogd and ogd.get("content"):
        return strip_js_gate(ogd["content"])
    desc = soup.find("meta", {"name": "description"})
    if desc and desc.get("content"):
        return strip_js_gate(desc["content"])
    txt = soup.get_text("\n", strip=True)
    return strip_js_gate(txt)

# -------------------- Utils: Web search (Brave) --------------------
def brave_search(q: str, count: int = 6) -> List[Dict]:
    if not BRAVE_API_KEY:
        return []
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    params = {"q": q, "count": count, "country": "it", "search_lang": "it", "ui_lang": "it"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=12)
        if r.status_code != 200:
            return []
        data = r.json()
        results = []
        for it in data.get("web", {}).get("results", []):
            host = it.get("site", "") or it.get("url", "")
            score = float(it.get("score", 0.0))
            # filtro domini preferiti
            if PREFERRED_DOMAINS and not any(dom in host for dom in PREFERRED_DOMAINS):
                continue
            if score < MIN_WEB_SCORE:
                continue
            title = it.get("title", "")
            snippet = it.get("description", "") or it.get("snippet", "") or it.get("page_facts", "")
            results.append({
                "url": it.get("url", ""),
                "title": title,
                "snippet": snippet,
                "score": score
            })
        return results
    except Exception:
        return []

def fetch_text(url: str, timeout: int = 10) -> str:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code != 200:
            return ""
        return r.text
    except Exception:
        return ""

# -------------------- Sinapsi --------------------
class Rule(BaseModel):
    id: Optional[str] = None
    pattern: str
    mode: str  # override | augment | postscript
    lang: str = "it"
    answer: str

def load_sinapsi() -> List[Rule]:
    try:
        if not os.path.exists(SINAPSI_FILE):
            return []
        with open(SINAPSI_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        rules = []
        for r in raw:
            try:
                rules.append(Rule(**r))
            except Exception:
                continue
        return rules
    except Exception:
        return []

SINAPSI = load_sinapsi()

def match_sinapsi(q: str) -> Tuple[Optional[Rule], List[Rule]]:
    """Ritorna (override_rule or None, list_of_augments)"""
    override = None
    augments: List[Rule] = []
    for r in SINAPSI:
        if re.search(r.pattern, q, flags=re.I):
            if r.mode.lower() == "override" and override is None:
                override = r
            elif r.mode.lower() in ("augment", "postscript"):
                augments.append(r)
    return override, augments

# -------------------- Refiner stile Tecnaria --------------------
def refine_it_tecnaria(user_q: str, body: str) -> str:
    body = strip_js_gate(body)
    body = re.sub(r'^\s*OK\s*[\r\n]+', '', body, flags=re.I)
    body = body.replace("**Fonti**", "Fonti")
    # compattare righe vuote ripetute
    body = re.sub(r'\n{3,}', '\n\n', body)
    return body.strip()

# -------------------- Answer builder --------------------
def build_answer(user_q: str) -> Dict:
    t0 = time.time()

    # 1) Sinapsi match (per sapere se dobbiamo fare override più tardi)
    ov_rule, aug_rules = match_sinapsi(user_q)

    # 2) Web pass (salvo override dichiarato e non forzato)
    web_chunks: List[str] = []
    sources: List[str] = []
    if FORCE_WEB or ov_rule is None:
        web = brave_search(user_q, count=6)
        for w in web:
            url = w["url"]
            title = strip_js_gate(w.get("title",""))
            snippet = strip_js_gate(w.get("snippet",""))
            if not snippet:
                html_page = fetch_text(url)
                snippet = html_to_text_safe(html_page)[:800]
            if not snippet:
                continue
            web_chunks.append(f"- {snippet}")
            sources.append(url)

    # 3) Se c’è override → risposta deterministica
    if ov_rule is not None:
        ans = ov_rule.answer.strip()
        # In alcuni casi vogliamo comunque aggiungere fonti web pulite, se presenti
        if web_chunks and sources:
            cleaned = "\n".join(web_chunks[:3])
            ans = ans + "\n" + refine_it_tecnaria(user_q, cleaned)
        return {
            "ok": True,
            "answer": refine_it_tecnaria(user_q, ans),
            "sources": sources[:5],
            "elapsed_ms": int((time.time()-t0)*1000)
        }

    # 4) Nessun override: compongo sintesi web + eventuali augment/postscript
    if not web_chunks:
        # Niente web → provo almeno augment di Sinapsi
        if aug_rules:
            addon = "\n".join([r.answer.strip() for r in aug_rules])
            return {
                "ok": True,
                "answer": refine_it_tecnaria(user_q, addon),
                "sources": [],
                "elapsed_ms": int((time.time()-t0)*1000)
            }
        # fallback
        return {
            "ok": True,
            "answer": "OK\n- **Non ho trovato una risposta affidabile** (o la ricerca non è configurata).",
            "sources": [],
            "elapsed_ms": int((time.time()-t0)*1000)
        }

    # Sintesi web
    web_block = "\n".join(web_chunks[:5])

    # Augment
    if aug_rules:
        web_block += "\n" + "\n".join([r.answer.strip() for r in aug_rules])

    final = refine_it_tecnaria(user_q, web_block)
    # Aggiungo fonti
    if sources:
        src_lines = "\n".join([f"- {u}" for u in sources[:5]])
        final += f"\n\nFonti\n{src_lines}"

    return {
        "ok": True,
        "answer": final,
        "sources": sources[:5],
        "elapsed_ms": int((time.time()-t0)*1000)
    }

# -------------------- FastAPI --------------------
app = FastAPI(title=APP_NAME)

INDEX_HTML = f"""
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{APP_NAME}</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<style>
 body{{background:#0b0f14;color:#d8e1e8;font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0}}
 .wrap{{max-width:1080px;margin:32px auto;padding:0 16px}}
 h1{{font-size:22px;margin:0 0 12px}}
 .row{{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:10px}}
 input[type=text]{{flex:1;padding:14px 16px;border:1px solid #1e2835;background:#0f141b;color:#e6eef5;border-radius:10px;outline:none}}
 button{{background:#1e88e5;color:#fff;border:0;border-radius:10px;padding:12px 16px;cursor:pointer}}
 pre{{background:#0f141b;border:1px solid #1e2835;border-radius:10px;padding:16px;white-space:pre-wrap;word-wrap:break-word}}
 .chips button{{background:#15202b}}
 .muted{{opacity:.75}}
</style>
</head>
<body>
<div class="wrap">
  <h1>{APP_NAME}</h1>
  <div class="chips row">
    <button onclick="ex('Devo usare la chiodatrice P560 per fissare i CTF. Serve un patentino?')">Esempio P560</button>
    <button onclick="ex('Che differenza c’è tra un connettore CTF e il sistema Diapason?')">CTF vs Diapason</button>
    <button onclick="ex('Quanti connettori CTF servono al m²? Serve preforare?')">Densità CTF</button>
  </div>
  <div class="row">
    <input id="q" type="text" placeholder="Scrivi la domanda…"/>
    <button onclick="ask()">Chiedi</button>
  </div>
  <div class="muted">Endpoint: <code>/ping</code> / <code>/health</code> / <code>/api/ask</code> (GET q=… | POST JSON {{q:…}})</div>
  <pre id="out">—</pre>
</div>
<script>
function ex(t){{document.getElementById('q').value=t;}}
async function ask(){{
  const q=document.getElementById('q').value.trim();
  if(!q) return;
  const out=document.getElementById('out');
  out.textContent='(in corso)…';
  const r=await fetch('/api/ask',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{q}})}});
  const j=await r.json();
  if(!j.ok){{out.textContent='Errore';return;}}
  let s = 'OK ('+j.elapsed_ms+'ms)\\n\\n'+j.answer;
  out.textContent=s;
}}
</script>
</body>
</html>
"""

class AskIn(BaseModel):
    q: str

@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML

@app.get("/ping")
def ping():
    return {"ok": True, "pong": True}

@app.get("/health")
def health():
    return {
        "status": "ok",
        "web_search": {
            "provider": "brave",
            "brave_key": bool(BRAVE_API_KEY),
            "preferred_domains": PREFERRED_DOMAINS,
            "min_web_score": MIN_WEB_SCORE
        },
        "critici": {
            "dir": CRITICI_DIR,
            "exists": os.path.exists(CRITICI_DIR),
            "sinapsi_file": SINAPSI_FILE,
            "sinapsi_loaded": len(SINAPSI)
        }
    }

@app.get("/api/ask")
def ask_get(q: str = Query(..., min_length=2)):
    return build_answer(q)

@app.post("/api/ask")
def ask_post(inp: AskIn = Body(...)):
    return build_answer(inp.q)
