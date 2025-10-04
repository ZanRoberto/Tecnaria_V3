import os, re, json, time, html
from typing import List, Dict, Any, Optional
import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse

# ---------------------------------------------------------
# Config via ENV (valori di default ragionevoli)
# ---------------------------------------------------------
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()
PREFERRED_DOMAINS = os.getenv("PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com").split(",")
MIN_WEB_SCORE = float(os.getenv("MIN_WEB_SCORE", "0.35"))  # soglia "morbida"
CRITICI_DIR = os.getenv("CRITICI_DIR", "static/data/critici").strip()
CRITICAL_ENRICH_FORCE_WEB = os.getenv("CRITICAL_ENRICH_FORCE_WEB", "0").strip() in ("1", "true", "True")
DEBUG = os.getenv("DEBUG", "0").strip() in ("1", "true", "True")

# ---------------------------------------------------------
# Utilities
# ---------------------------------------------------------
def allowed_domain(url: str, prefer: List[str]) -> bool:
    return any(d.strip().lower() in url.lower() for d in prefer if d.strip())

def clean_html_blob(txt: str) -> str:
    """Toglie script/css, tag, boilerplate JS, comprime spazi, decodifica entità."""
    if not txt:
        return ""
    # Togli blocchi <script> e <style>
    txt = re.sub(r"(?is)<script.*?>.*?</script>", " ", txt)
    txt = re.sub(r"(?is)<style.*?>.*?</style>", " ", txt)
    # Togli tutto l’HTML
    txt = re.sub(r"(?is)<[^>]+>", " ", txt)
    # Togli boilerplate JS/cookie
    txt = re.sub(r"(?i)\b(function|var|let|const|\$|cookie|consent|gtag|google|dataLayer)\b.*", " ", txt)
    # Decodifica entità
    txt = html.unescape(txt)
    # comprimi spazi
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt

def looks_like_pdf(content_type: str, url: str) -> bool:
    if content_type and "pdf" in content_type.lower():
        return True
    return url.lower().endswith(".pdf")

def brave_search(q: str, count: int = 6) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        return []
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": q, "count": count, "source": "web"},
            headers={"X-Subscription-Token": BRAVE_API_KEY},
            timeout=12,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        items = []
        for block in data.get("web", {}).get("results", []):
            url = block.get("url", "")
            title = block.get("title", "") or ""
            desc = block.get("description", "") or ""
            # micro-score: priorità ad allowed domains + descrizione non vuota
            score = 1.0 if allowed_domain(url, PREFERRED_DOMAINS) else 0.4
            if desc and len(desc) > 40:
                score += 0.2
            items.append({
                "url": url,
                "title": title,
                "snippet": desc,
                "score": score
            })
        # ordina: allowed domains e poi punteggio
        items.sort(key=lambda x: (not allowed_domain(x["url"], PREFERRED_DOMAINS), -x["score"]))
        # filtra con soglia
        items = [it for it in items if it["score"] >= MIN_WEB_SCORE]
        return items
    except Exception:
        return []

def fetch_and_clean(url: str) -> str:
    """Scarica la pagina, evita testo binario, pulisce HTML."""
    try:
        r = requests.get(url, timeout=12)
        ctype = r.headers.get("content-type", "")
        if looks_like_pdf(ctype, url):
            # Non buttiamo blob PDF; restituiamo descrizione pulita
            return "Scheda tecnica (PDF)."
        # Evita binari
        if "text" not in ctype.lower() and "json" not in ctype.lower() and "html" not in ctype.lower():
            return ""
        return clean_html_blob(r.text)
    except Exception:
        return ""

# ---------------------------------------------------------
# Sinapsi: regole override/augment/postscript
# ---------------------------------------------------------
class Rule:
    def __init__(self, rid: str, pattern: str, mode: str, lang: str, answer: str):
        self.id = rid
        self.pattern = pattern
        self.regex = re.compile(pattern, flags=re.IGNORECASE)
        self.mode = (mode or "augment").lower()
        self.lang = lang or "it"
        self.answer = answer

def load_sinapsi_rules(dir_path: str) -> List[Rule]:
    path = os.path.join(dir_path, "sinapsi_rules.json")
    rules: List[Rule] = []
    try:
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            return []
        for obj in raw:
            rid = obj.get("id") or f"rule_{len(rules)+1}"
            pat = obj.get("pattern", ".*")
            mode = obj.get("mode", "augment")
            lang = obj.get("lang", "it")
            ans = obj.get("answer", "")
            rules.append(Rule(rid, pat, mode, lang, ans))
    except Exception:
        return []
    return rules

SINAPSI_RULES: List[Rule] = load_sinapsi_rules(CRITICI_DIR)

def apply_sinapsi(q: str, base_answer: str) -> str:
    """Applica le regole: override/augment/postscript."""
    out = base_answer or ""
    for r in SINAPSI_RULES:
        if r.regex.search(q):
            if r.mode == "override":
                out = r.answer.strip()
            elif r.mode == "augment":
                # appiccica prima dei Fonti (se presenti) o in coda
                if "**Fonti**" in out:
                    out = out.replace("**Fonti**", r.answer.strip() + "\n\n**Fonti**", 1)
                else:
                    out = (out + ("\n\n" if out else "") + r.answer.strip()).strip()
            elif r.mode == "postscript":
                out = (out + "\n\n" + r.answer.strip()).strip()
    return out

# ---------------------------------------------------------
# Answer composer
# ---------------------------------------------------------
def compose_answer(q: str) -> Dict[str, Any]:
    """
    1) WEB prima (Brave) con filtri dominio/soglia e pulizia HTML
    2) Se vuoto → fallback Sinapsi (override)
    3) Se pieno → riassunto pulito + Fonti
    4) Sempre → Sinapsi augment/postscript
    """
    t0 = time.time()
    # 1) WEB
    web_hits = brave_search(q, count=6)

    bullets: List[str] = []
    fontes: List[str] = []

    if web_hits:
        for hit in web_hits[:4]:
            url, title = hit["url"], hit["title"] or "Sorgente"
            if not allowed_domain(url, PREFERRED_DOMAINS):
                continue
            body = fetch_and_clean(url)
            if not body:
                # usa snippet se body vuoto
                body = clean_html_blob(hit.get("snippet", ""))
            if not body:
                continue
            # stringa corta, niente JS
            brief = body
            if len(brief) > 220:
                brief = brief[:220].rsplit(" ", 1)[0] + "…"
            bullets.append(f"- **{title}**: {brief}")
            fontes.append(url)

    # 2) se web nullo, prova comunque a costruire risposta + Sinapsi
    if not bullets:
        base = ""
    else:
        # paragrafo introduttivo sintetico, tono "tecnico ma fluido"
        base = "Risposta sintetica basata su fonti ufficiali.\n\n" + "\n".join(bullets)
        if fontes:
            uniq = []
            for f in fontes:
                if f not in uniq: uniq.append(f)
            base += "\n\n**Fonti**\n" + "\n".join(f"- {u}" for u in uniq)

    # 3) SINAPSI: può fare override o arricchire
    out = apply_sinapsi(q, base)

    # Se dopo tutto è ancora vuoto → messaggio unico, non “bollettino”
    if not out.strip():
        out = "Mi baso solo su fonti ufficiali Tecnaria/SPIT. Su questa domanda non ho estratto dati affidabili. Se vuoi, riformuliamo o posso rispondere con le indicazioni curate (Sinapsi)."

    return {
        "ok": True,
        "answer": out.strip(),
        "elapsed_ms": int((time.time() - t0) * 1000),
    }

# ---------------------------------------------------------
# FastAPI + UI
# ---------------------------------------------------------
app = FastAPI(title="Tecnaria QA Bot")

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
            "dir": os.path.abspath(CRITICI_DIR),
            "exists": os.path.isdir(CRITICI_DIR),
            "sinapsi_file": os.path.abspath(os.path.join(CRITICI_DIR, "sinapsi_rules.json")),
            "sinapsi_loaded": len(SINAPSI_RULES),
        },
    }

@app.get("/api/ask")
def api_ask_get(q: str):
    return compose_answer(q)

@app.post("/api/ask")
async def api_ask_post(req: Request):
    data = await req.json()
    q = (data.get("q") or "").strip()
    return compose_answer(q)

# Alias breve
@app.get("/ask")
def ask_alias(q: str):
    return compose_answer(q)

# ---------------------------------------------------------
# HTML (UI semplice, scura, con pulsanti)
# ---------------------------------------------------------
HTML_PAGE = """
<!doctype html>
<html lang="it"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tecnaria QA Bot</title>
<style>
:root{
  --bg:#0f172a; --card:#111827; --ink:#e5e7eb; --muted:#9ca3af; --accent:#2563eb; --chip:#1f2937;
  --ok:#16a34a; --danger:#ef4444;
}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui, -apple-system, Segoe UI, Roboto, Arial}
.app{max-width:1100px;margin:28px auto;padding:0 16px}
h1{margin:0 0 14px;font-size:28px}
.toolbar{display:flex; gap:10px; flex-wrap:wrap; margin:10px 0 18px}
.chip{background:var(--chip);color:var(--ink);border-radius:999px;padding:10px 14px;border:1px solid #1f2937; cursor:pointer}
.card{background:var(--card); border:1px solid #1f2937; border-radius:14px; padding:16px}
textarea{width:100%; min-height:130px; background:#0b1220; color:var(--ink); border:1px solid #1f2937; border-radius:10px; padding:12px}
.btn{background:var(--accent); color:#fff; border:0; padding:10px 16px; border-radius:10px; cursor:pointer}
.btn:disabled{opacity:.6}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px}
.small{color:var(--muted); font-size:13px}
pre{white-space:pre-wrap;word-break:break-word}
kbd{background:#0b1220;border:1px solid #1f2937;border-radius:6px;padding:3px 6px}
a{color:#93c5fd}
</style>
</head>
<body>
<div class="app">
  <h1>Tecnaria QA Bot</h1>

  <div class="toolbar">
    <button class="chip" onclick="fill('Devo usare la chiodatrice P560 per fissare i CTF. Serve un patentino?')">Esempio P560</button>
    <button class="chip" onclick="fill('Che differenza c’è tra un connettore CTF e il sistema Diapason? Quando conviene usare uno o l’altro?')">CTF vs Diapason</button>
    <button class="chip" onclick="fill('Devo utilizzare i connettori CTF su lamiera Hi-Bond da 1 mm. Quanti connettori servono al m² e come avviene il fissaggio con la P560?')">Densità CTF</button>
  </div>

  <div class="card">
    <textarea id="q" placeholder="Scrivi la domanda…"></textarea>
    <div class="row">
      <button class="btn" id="go" onclick="ask()">Chiedi</button>
      <span class="small">Endpoint: <kbd>/ping</kbd> <kbd>/health</kbd> <kbd>/api/ask</kbd> (GET q=… | POST JSON {q:…})</span>
    </div>
  </div>

  <div id="out" class="card" style="margin-top:16px;min-height:120px"><span class="small">—</span></div>
</div>

<script>
async function ask(){
  const q = document.getElementById('q').value.trim();
  const out = document.getElementById('out');
  if(!q){ out.innerHTML = "<span class='small'>Scrivi una domanda.</span>"; return; }
  document.getElementById('go').disabled = true;
  out.innerHTML = "<span class='small'>Sto cercando su fonti ufficiali…</span>";
  try{
    const res = await fetch('/api/ask', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({q})});
    const j = await res.json();
    const ms = j.elapsed_ms ? ` <span class='small'>(${j.elapsed_ms}ms)</span>` : "";
    out.innerHTML = `<pre>OK${ms}\n` + j.answer + `</pre>`;
  }catch(e){
    out.innerHTML = "<pre>Errore di risposta</pre>";
  }finally{
    document.getElementById('go').disabled = false;
  }
}
function fill(t){ document.getElementById('q').value = t; }
</script>
</body></html>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTML_PAGE
