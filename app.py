# app.py
import os, re, json, asyncio
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse
from fastapi import FastAPI, Request, Query, Body
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

APP_NAME = "Tecnaria QA Bot"

# --------- Config da ENV ----------
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "brave").lower()  # brave | bing | none
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
BING_API_KEY  = os.getenv("BING_API_KEY", "")
PREFERRED_DOMAINS = [d.strip() for d in os.getenv(
    "PREFERRED_DOMAINS",
    "tecnaria.com, spit.eu, spitpaslode.com"
).split(",") if d.strip()]

MIN_WEB_SCORE = float(os.getenv("MIN_WEB_SCORE", "0.50"))  # alza/abbassa qui (0-1)
WEB_TIMEOUT   = float(os.getenv("WEB_TIMEOUT", "6.0"))
WEB_RETRIES   = int(os.getenv("WEB_RETRIES", "2"))

CRITICI_DIR   = os.getenv("CRITICI_DIR", "Tecnaria_V3/static/static/data/critici").strip()
# nomi supportati per sinapsi
SINAPSI_FILES = ["sinapsi_brain.json", "sinapsi_rules.json", "sinapsi.json"]

# --------- App ----------
app = FastAPI(title=APP_NAME)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], allow_credentials=False
)

# --------- Utility ----------
def allowed_domain(url: str, prefer: List[str]) -> bool:
    try:
        netloc = urlparse(url).netloc.lower()
        netloc = netloc[4:] if netloc.startswith("www.") else netloc
        for d in prefer:
            d = d.strip().lower()
            if not d:
                continue
            if netloc == d or netloc.endswith("." + d):
                return True
        return False
    except Exception:
        return False

def looks_like_binary_pdf(text: str) -> bool:
    # Se la “descrizione” inizia con intestazioni PDF o byte strani, scartiamo
    head = text[:15] if text else ""
    return "%PDF" in head or "obj <<" in text[:200]

# --------- Sinapsi ----------
class SinapsiRule:
    def __init__(self, raw: Dict[str, Any]):
        self.id   = raw.get("id") or ""
        self.lang = raw.get("lang") or "it"
        self.mode = (raw.get("mode") or "augment").lower()  # override | augment | postscript
        self.answer = raw.get("answer") or ""
        pat = raw.get("pattern") or ""
        flags = re.IGNORECASE | re.DOTALL
        try:
            self.pattern = re.compile(pat, flags)
        except re.error:
            self.pattern = re.compile(re.escape(pat), flags)

    def match(self, q: str) -> bool:
        return bool(self.pattern.search(q))

def load_sinapsi() -> List[SinapsiRule]:
    rules: List[SinapsiRule] = []
    if not CRITICI_DIR or not os.path.isdir(CRITICI_DIR):
        return rules
    for name in SINAPSI_FILES:
        p = os.path.join(CRITICI_DIR, name)
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        rules += [SinapsiRule(x) for x in data if isinstance(x, dict)]
            except Exception:
                continue
    return rules

SINAPSI_RULES = load_sinapsi()

def apply_sinapsi(q: str) -> Optional[Dict[str, str]]:
    for r in SINAPSI_RULES:
        if r.match(q):
            return {"mode": r.mode, "answer": r.answer}
    return None

# --------- Web Search ----------
async def brave_search(q: str) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        return []
    url = "https://api.search.brave.com/res/v1/web/search"
    params = {"q": q, "count": 6, "source": "web"}
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    for _ in range(WEB_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=WEB_TIMEOUT) as client:
                r = await client.get(url, params=params, headers=headers)
                if r.status_code != 200:
                    continue
                j = r.json()
                items = []
                for it in j.get("web", {}).get("results", []):
                    items.append({
                        "url": it.get("url"),
                        "title": it.get("title"),
                        "snippet": it.get("description") or "",
                        "score": float(it.get("v2_relevance_score") or 0.0),
                    })
                return items
        except Exception:
            await asyncio.sleep(0.15)
    return []

async def bing_search(q: str) -> List[Dict[str, Any]]:
    if not BING_API_KEY:
        return []
    url = "https://api.bing.microsoft.com/v7.0/search"
    params = {"q": q, "count": 6, "mkt": "it-IT", "responseFilter": "Webpages"}
    headers = {"Ocp-Apim-Subscription-Key": BING_API_KEY}
    for _ in range(WEB_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=WEB_TIMEOUT) as client:
                r = await client.get(url, params=params, headers=headers)
                if r.status_code != 200:
                    continue
                j = r.json()
                items = []
                for it in j.get("webPages", {}).get("value", []):
                    items.append({
                        "url": it.get("url"),
                        "title": it.get("name"),
                        "snippet": it.get("snippet") or "",
                        "score": float(it.get("rank") or 0.0),  # Bing non dà un vero score normalizzato
                    })
                return items
        except Exception:
            await asyncio.sleep(0.15)
    return []

async def web_search(q: str) -> List[Dict[str, Any]]:
    if SEARCH_PROVIDER == "brave":
        raw = await brave_search(q)
    elif SEARCH_PROVIDER == "bing":
        raw = await bing_search(q)
    else:
        return []

    # filtro dominio + soglia
    preferred = [d for d in PREFERRED_DOMAINS if d]
    filtered = [
        it for it in raw
        if it.get("url") and allowed_domain(it["url"], preferred)
           and (it.get("snippet") and not looks_like_binary_pdf(it["snippet"]))
           and float(it.get("score") or 0.0) >= MIN_WEB_SCORE
    ]
    # se troppo stretto, prova almeno dominio preferito ignorando soglia
    if not filtered:
        filtered = [
            it for it in raw
            if it.get("url") and allowed_domain(it["url"], preferred)
               and (it.get("snippet") and not looks_like_binary_pdf(it["snippet"]))
        ]
    return filtered[:5]

# --------- Sintesi risposta ----------
def synthesize_from_hits(q: str, hits: List[Dict[str, Any]]) -> str:
    if not hits:
        return ""
    bullets = []
    seen = set()
    for it in hits:
        title = (it.get("title") or "").strip()
        snip  = (it.get("snippet") or "").strip()
        url   = (it.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        # breve linea pulita
        line = f"- {title}: {snip}"
        # asciuga se lunghissimo
        if len(line) > 320:
            line = line[:317] + "…"
        bullets.append(line)
        if len(bullets) >= 4:
            break

    sources = [f"- {it['url']}" for it in hits[:4] if it.get("url")]
    out = "OK\n- **Riferimento**: contenuti tecnici su fonti ufficiali.\n"
    if bullets:
        out += "- **Sintesi**:\n" + "\n".join(bullets) + "\n\n"
    if sources:
        out += "**Fonti**\n" + "\n".join(sources) + "\n"
    return out

def merge_with_sinapsi(base: str, sinapsi: Dict[str,str]) -> str:
    mode = sinapsi["mode"]
    ans  = sinapsi["answer"].rstrip() + "\n"
    if mode == "override" or not base.strip():
        return ans
    if mode == "augment":
        # attacca prima il web, poi sinapsi come blocco integrativo
        return (base.rstrip() + "\n" +
                ("" if base.endswith("\n\n") else "\n") +
                ans)
    if mode == "postscript":
        return base.rstrip() + "\n\n**Nota (Sinapsi)**\n" + ans
    return base or ans

# --------- API ----------
@app.get("/ping")
async def ping():
    return {"ok": True, "pong": True}

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "web_search": {
            "provider": SEARCH_PROVIDER,
            "brave_key": bool(BRAVE_API_KEY),
            "bing_key": bool(BING_API_KEY),
            "preferred_domains": PREFERRED_DOMAINS,
            "min_web_score": MIN_WEB_SCORE
        },
        "critici": {"dir": CRITICI_DIR, "exists": os.path.isdir(CRITICI_DIR), "rules": len(SINAPSI_RULES)}
    }

async def _answer_core(q: str) -> str:
    q = (q or "").strip()
    if not q:
        return "OK\n- **Domanda vuota**: inserisci una richiesta valida.\n"

    # 1) web
    hits: List[Dict[str, Any]] = []
    if SEARCH_PROVIDER in ("brave", "bing"):
        hits = await web_search(q)

    web_text = synthesize_from_hits(q, hits)

    # 2) sinapsi
    s = apply_sinapsi(q)
    if s:
        return merge_with_sinapsi(web_text, s)

    # 3) fallback se il web non ha dato fonti utili
    if not web_text:
        return ("OK\n- **Non ho trovato una risposta affidabile sul web**. "
                "Puoi riformulare la domanda oppure ti fornisco i contatti Tecnaria.\n")

    return web_text

@app.get("/ask")
async def ask_get(q: str = Query("")):
    ans = await _answer_core(q)
    return {"ok": True, "answer": ans}

@app.post("/ask")
async def ask_post(payload: Dict[str, Any] = Body(...)):
    q = (payload or {}).get("q", "")
    ans = await _answer_core(q)
    return {"ok": True, "answer": ans}

# alias più “REST”
@app.get("/api/ask")
async def api_ask_get(q: str = Query("")):
    ans = await _answer_core(q)
    return {"ok": True, "answer": ans}

@app.post("/api/ask")
async def api_ask_post(payload: Dict[str, Any] = Body(...)):
    q = (payload or {}).get("q", "")
    ans = await _answer_core(q)
    return {"ok": True, "answer": ans}

# --------- UI (pagina scura) ----------
DARK_HTML = """
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tecnaria QA Bot</title>
<style>
  :root {
    --bg:#0f172a; --panel:#111827; --muted:#94a3b8; --text:#e5e7eb; --accent:#22c55e; --btn:#1f2937; --btn2:#0b2d18;
  }
  html,body{height:100%;}
  body{margin:0;background:var(--bg);color:var(--text);font:16px/1.4 system-ui,Segoe UI,Roboto,Helvetica,Arial}
  .wrap{max-width:1100px;margin:28px auto;padding:0 16px}
  h1{margin:0 0 14px 0;font-size:28px}
  .chips{display:flex;flex-wrap:wrap;gap:10px;margin:10px 0 18px}
  .chip{background:#0b1220;color:#cbd5e1;border:1px solid #1f2937;padding:8px 14px;border-radius:999px;cursor:pointer}
  .card{background:var(--panel);border:1px solid #1f2937;border-radius:16px;padding:16px;margin-bottom:14px;box-shadow:0 10px 30px rgba(0,0,0,.25)}
  textarea{width:100%;min-height:180px;background:#0b1020;color:var(--text);border:1px solid #1f2937;border-radius:12px;padding:12px;resize:vertical}
  .row{display:flex;align-items:center;gap:10px;margin-top:12px}
  .btn{background:var(--btn);color:#fff;border:1px solid #334155;padding:10px 16px;border-radius:10px;cursor:pointer}
  .btn:hover{border-color:#475569}
  .btn.primary{background:var(--btn2);border-color:#14532d}
  .muted{color:var(--muted);font-size:14px}
  pre{white-space:pre-wrap;background:#0b1020;border:1px solid #1f2937;padding:16px;border-radius:12px;min-height:160px}
  .foot{margin-top:8px;font-size:12px;color:#94a3b8}
</style>
</head>
<body>
<div class="wrap">
  <h1>Tecnaria QA Bot</h1>

  <div class="chips">
    <div class="chip" onclick="fill('Devo usare la chiodatrice P560 per fissare i CTF. Serve un patentino o formazione speciale?')">P560 + CTF</div>
    <div class="chip" onclick="fill('Che differenza c’è tra un connettore CTF e il sistema Diapason? Quando conviene usare uno o l’altro?')">CTF vs Diapason</div>
    <div class="chip" onclick="fill('Lamiera Hi-Bond 1 mm: quanti connettori CTF per m² e come si fissano?')">Densità CTF</div>
    <div class="chip" onclick="fill('Mi dai i contatti Tecnaria per assistenza tecnica e commerciale?')">Contatti</div>
  </div>

  <div class="card">
    <textarea id="q" placeholder="Scrivi la tua domanda…"></textarea>
    <div class="row">
      <button class="btn primary" onclick="ask()">Chiedi</button>
      <button class="btn" onclick="clearQ()">Pulisci</button>
      <button class="btn" onclick="copyOut()">Copia risposta</button>
      <label class="muted" style="margin-left:12px"><input id="useGet" type="checkbox"> usa GET (debug)</label>
    </div>
  </div>

  <div class="card">
    <pre id="out" class="muted">Risposte qui…</pre>
    <div class="foot">Endpoint: /ask (POST JSON { q }) oppure GET ?q=…</div>
  </div>
</div>

<script>
function fill(t){document.getElementById('q').value=t;}
function clearQ(){document.getElementById('q').value='';}
async function copyOut(){
  const txt=document.getElementById('out').innerText||'';
  try{await navigator.clipboard.writeText(txt);}catch(e){}
}
async function ask(){
  const q=document.getElementById('q').value.trim();
  const get=document.getElementById('useGet').checked;
  const out=document.getElementById('out');
  out.textContent='…';
  try{
    let res;
    if(get){
      const u='/api/ask?q='+encodeURIComponent(q);
      res = await fetch(u);
    }else{
      res = await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q})});
    }
    const j = await res.json();
    out.textContent = j && j.answer ? j.answer : JSON.stringify(j,null,2);
  }catch(e){
    out.textContent = 'Errore di risposta';
  }
}
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def ui():
    return HTMLResponse(DARK_HTML)
