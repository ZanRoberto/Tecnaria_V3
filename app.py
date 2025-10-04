# app.py
# -----------------------------------------------------------------------------
# Tecnaria QA Bot – Web-first + Sinapsi (override/augment/postscript) + UI
# FastAPI + Brave/Bing web search + PDF-aware + IT/EN smoothing
# -----------------------------------------------------------------------------

import os, re, json, time, unicodedata
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse

# -----------------------------------------------------------------------------
# ENV / CONFIG
# -----------------------------------------------------------------------------
SEARCH_PROVIDER     = os.getenv("SEARCH_PROVIDER", "brave").strip().lower()  # brave|bing
BRAVE_API_KEY       = os.getenv("BRAVE_API_KEY", "").strip()
BING_API_KEY        = os.getenv("BING_API_KEY", "").strip() or os.getenv("AZURE_BING_KEY", "").strip()
SEARCH_API_ENDPOINT = os.getenv("SEARCH_API_ENDPOINT", "").strip()  # opzionale per Bing Enterprise
PREFERRED_DOMAINS   = [d.strip().lower() for d in os.getenv("PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com").split(",") if d.strip()]
MIN_WEB_SCORE       = float(os.getenv("MIN_WEB_SCORE", "0.35"))
WEB_TIMEOUT         = float(os.getenv("WEB_TIMEOUT", "6"))
WEB_RETRIES         = int(os.getenv("WEB_RETRIES", "2"))
CRITICI_DIR         = os.getenv("CRITICI_DIR", "static/static/data/critici").rstrip("/")

DEBUG = os.getenv("DEBUG", "0") == "1"

print("[BOOT] -----------------------------------------------")
print(f"[BOOT] SEARCH_PROVIDER={SEARCH_PROVIDER}; PREFERRED_DOMAINS={PREFERRED_DOMAINS}")
print(f"[BOOT] MIN_WEB_SCORE={MIN_WEB_SCORE} TIMEOUT={WEB_TIMEOUT}s RETRIES={WEB_RETRIES}")
print(f"[BOOT] CRITICI_DIR={CRITICI_DIR}")
print("[BOOT] ------------------------------------------------")

# -----------------------------------------------------------------------------
# UTILS
# -----------------------------------------------------------------------------
UI_NOISE_PREFIXES = (
    "chiedi", "pulisci", "copia risposta", "risposta",
    "connettori ctf", "p560", "contatti", "—"
)

def normalize(s: str) -> str:
    if not s: return ""
    t = unicodedata.normalize("NFKD", s)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t

def clean_ui_noise(text: str) -> str:
    if not text: return ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    keep = []
    for l in lines:
        low = l.strip().lower()
        if any(low.startswith(p) for p in UI_NOISE_PREFIXES):
            continue
        keep.append(l)
    return " ".join(keep).strip()

def domain_of(u: str) -> str:
    try:
        return urlparse(u).netloc.lower()
    except:
        return ""

def prefer_score(url: str) -> float:
    d = domain_of(url)
    return 0.25 if any(pd in d for pd in PREFERRED_DOMAINS) else 0.0

def short_text(text: str, n: int = 1000) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return (t[:n] + "…") if len(t) > n else t

def strip_html_noise(txt: str) -> str:
    # rimuove <strong> & co rimasti dagli snippet
    txt = re.sub(r"<\s*/?\s*(strong|b|em|i)\s*>", "", txt, flags=re.I)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt

def looks_italian(s: str) -> bool:
    return bool(re.search(r"\b(che|quando|con|per|non|solo|anche|serve|devo|differenza|quanti|come)\b", normalize(s)))

def looks_english(s: str) -> bool:
    return bool(re.search(r"\b(the|and|with|for|when|without|it|is|are|composite|floor|sheet)\b", s.lower()))

# -----------------------------------------------------------------------------
# SINAPSI
# -----------------------------------------------------------------------------
SINAPSI: List[Dict] = []
SINAPSI_PATTERNS: List[Tuple[re.Pattern, Dict]] = []

def load_sinapsi() -> None:
    global SINAPSI, SINAPSI_PATTERNS
    SINAPSI, SINAPSI_PATTERNS = [], []
    path = os.path.join(CRITICI_DIR, "sinapsi_brain.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            SINAPSI = json.load(f)
        for item in SINAPSI:
            pat = re.compile(item.get("pattern", ".*"), re.I)
            SINAPSI_PATTERNS.append((pat, item))
        print(f"[SINAPSI] loaded {len(SINAPSI)} items from {path}")
    except FileNotFoundError:
        print(f"[SINAPSI] no file at {path} (optional)")
    except Exception as e:
        print("[SINAPSI][ERR]", e)

load_sinapsi()

def apply_sinapsi(question: str, base_answer: str) -> str:
    qn = normalize(question)
    chosen: List[Dict] = []
    for pat, item in SINAPSI_PATTERNS:
        if pat.search(question) or pat.search(qn):
            chosen.append(item)

    if not chosen:
        return base_answer

    # priorità: override > augment > postscript
    overrides = [it for it in chosen if it.get("mode") == "override"]
    augments  = [it for it in chosen if it.get("mode") == "augment"]
    posts     = [it for it in chosen if it.get("mode") == "postscript"]

    if overrides:
        # se c'è override, prendo il primo override e (se presenti) attacco postscript
        ans = overrides[0].get("answer", "").strip()
        for ps in posts:
            ps_txt = ps.get("answer", "").strip()
            if ps_txt:
                ans += ("\n\n" if not ans.endswith("\n") else "") + ps_txt
        return ans

    # altrimenti: parto dal base, attacco tutti gli augment in ordine + i postscript
    ans = base_answer.strip()
    if augments:
        # se base_answer vuota, creo un'introduzione
        if not ans:
            ans = "OK\n- **Sintesi**: informazioni tecniche pertinenti.\n"
        for ag in augments:
            add = ag.get("answer", "").strip()
            if add:
                if not ans.endswith("\n"):
                    ans += "\n"
                # se l'augment non inizia con bullet, anteponi "- "
                if not re.match(r"^(-|\n-)", add):
                    add = "- " + add
                ans += add + ("\n" if not ans.endswith("\n") else "")
    for ps in posts:
        ps_txt = ps.get("answer", "").strip()
        if ps_txt:
            ans += ("\n" if not ans.endswith("\n") else "") + ps_txt + ("\n" if not ans.endswith("\n") else "")
    return ans

# -----------------------------------------------------------------------------
# WEB SEARCH / FETCH
# -----------------------------------------------------------------------------
def brave_search(q: str, topk: int = 6, timeout: float = WEB_TIMEOUT) -> List[Dict]:
    if not BRAVE_API_KEY:
        return []
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    params  = {"q": q, "count": topk}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        items = []
        for it in data.get("web", {}).get("results", []):
            items.append({
                "title": it.get("title") or "",
                "url": it.get("url") or "",
                "snippet": it.get("description") or ""
            })
        return items
    except Exception as e:
        if DEBUG: print("[BRAVE][ERR]", e)
        return []

def bing_search(q: str, topk: int = 6, timeout: float = WEB_TIMEOUT) -> List[Dict]:
    key = BING_API_KEY
    if not key:
        return []
    endpoint = SEARCH_API_ENDPOINT or "https://api.bing.microsoft.com/v7.0/search"
    headers = {"Ocp-Apim-Subscription-Key": key}
    params  = {"q": q, "count": topk, "responseFilter": "Webpages"}
    try:
        r = requests.get(endpoint, headers=headers, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        items = []
        for it in data.get("webPages", {}).get("value", []):
            items.append({
                "title": it.get("name") or "",
                "url": it.get("url") or "",
                "snippet": it.get("snippet") or ""
            })
        return items
    except Exception as e:
        if DEBUG: print("[BING][ERR]", e)
        return []

def web_search(q: str, topk: int = 6) -> List[Dict]:
    if SEARCH_PROVIDER == "bing":
        return bing_search(q, topk=topk)
    return brave_search(q, topk=topk)

def rank_results(q: str, res: List[Dict]) -> List[Dict]:
    nq = normalize(q)
    ranked = []
    for it in res:
        sn = normalize((it.get("title") or "") + " " + (it.get("snippet") or ""))
        score = 0.0
        # keyword overlap grezza
        for w in set(nq.split()):
            if w and w in sn:
                score += 0.35
        score += prefer_score(it.get("url",""))
        # boost se pare pertinente (CTF/P560/Diapason…)
        if re.search(r"\b(ctf|p\s*560|diapason|hi[- ]?bond|lamiera|composite|connector|tecnaria)\b", sn, re.I):
            score += 0.4
        it["score"] = score
        ranked.append(it)
    ranked.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    return ranked

def head_content_type(url: str) -> str:
    try:
        r = requests.head(url, timeout=WEB_TIMEOUT, allow_redirects=True)
        return r.headers.get("Content-Type", "").lower()
    except:
        return ""

def fetch_text(url: str, timeout: float = WEB_TIMEOUT) -> Tuple[str, bool]:
    """
    Ritorna (testo_pulito, is_pdf)
    """
    ctype = head_content_type(url)
    if "pdf" in ctype:
        # non scarico il blob; segnalo che è PDF
        return "", True
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
        r.raise_for_status()
        html = r.text
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["script", "style", "noscript"]): t.decompose()
        text = soup.get_text("\n")
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
        return text, False
    except Exception as e:
        if DEBUG: print("[FETCH][ERR]", url, e)
        return "", False

def web_lookup(q: str) -> Tuple[str, List[str]]:
    """
    Tenta: filtra per domini preferiti; gestisce PDF; sintetizza bullets + fonti.
    """
    for attempt in range(WEB_RETRIES + 1):
        results = web_search(q, topk=8)
        if not results:
            continue
        # preferisci i domini preferiti, ma non eliminare tutto se vuoto
        pref = [r for r in results if any(pd in domain_of(r["url"]) for pd in PREFERRED_DOMAINS)]
        ranked = rank_results(q, pref or results)
        if not ranked:
            continue
        top = ranked[0]
        if top.get("score", 0.0) < MIN_WEB_SCORE:
            continue

        url = top["url"]
        text, is_pdf = fetch_text(url, timeout=WEB_TIMEOUT)

        if is_pdf:
            ans = (
                "OK\n"
                "- **Riferimento**: documento tecnico (PDF) pertinente alla richiesta.\n"
                "- **Sintesi**: uso di connettori/sistema correlati alla domanda; consultare il PDF per dettagli (schemi, tabelle, posa, limiti).\n"
            )
            return ans, [url]

        if not text:
            continue

        # Sintesi grezza + ripulita
        snippet = short_text(text, 800)
        snippet = strip_html_noise(snippet)

        # produzione risposta "tipo bot"
        ans = (
            "OK\n"
            "- **Riferimento**: contenuti tecnici pertinenti trovati su fonte ufficiale.\n"
            "- **Sintesi**: " + snippet + "\n"
        )

        # se domanda IT e snippet in EN → nota + riscrittura minima in IT
        if looks_italian(q) and looks_english(snippet):
            ans = (
                "OK\n"
                "- **Nota**: contenuto originale in inglese; segue sintesi in italiano.\n"
                "- **Sintesi (IT)**: " + snippet + "\n"
            )
        return ans, [url]

    # fallito
    return "", []

# -----------------------------------------------------------------------------
# BOT PIPELINE
# -----------------------------------------------------------------------------
def answer_pipeline(raw_q: str) -> str:
    if not raw_q or not raw_q.strip():
        return "OK\n- **Domanda vuota**: inserisci una richiesta valida.\n"

    q = clean_ui_noise(raw_q).strip()
    if not q:
        return "OK\n- **Domanda vuota**: inserisci una richiesta valida.\n"

    # 1) web-first
    base, sources = web_lookup(q)

    # 2) se niente dal web, minimo fallback “educato” (poi Sinapsi può override)
    if not base:
        base = ("OK\n- **Non ho trovato una risposta affidabile sul web ora**. "
                "Posso provare a formulare indicazioni base e citare la documentazione.\n")

    # 3) applica Sinapsi
    augmented = apply_sinapsi(q, base)

    # 4) attacca le fonti web, se non già presenti e se sensate
    if sources:
        if "**Fonti**" not in augmented:
            augmented += "\n**Fonti**\n" + "\n".join(f"- {u}" for u in sources) + "\n"

    return augmented

# -----------------------------------------------------------------------------
# API
# -----------------------------------------------------------------------------
app = FastAPI(title="Tecnaria QA Bot", version="3.0")

@app.get("/ping")
def ping():
    return {"ok": True, "pong": True}

@app.get("/health")
def health():
    sinapsi_path = os.path.join(CRITICI_DIR, "sinapsi_brain.json")
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
            "dir": CRITICI_DIR,
            "sinapsi_exists": os.path.exists(sinapsi_path)
        }
    }

@app.get("/api/ask")
def api_ask_get(q: Optional[str] = None):
    if not q:
        return JSONResponse({"ok": False, "error": "Missing q"}, status_code=400)
    ans = answer_pipeline(q)
    return {"ok": True, "answer": ans}

@app.post("/api/ask")
async def api_ask_post(req: Request):
    try:
        data = await req.json()
    except:
        data = {}
    q = (data.get("q") or data.get("question") or "").strip()
    if not q:
        return JSONResponse({"ok": False, "error": "Missing q"}, status_code=400)
    ans = answer_pipeline(q)
    return {"ok": True, "answer": ans}

# Alias comodo: /ask (GET)
@app.get("/ask")
def ask_alias(q: Optional[str] = None):
    if not q:
        return JSONResponse({"ok": False, "error": "Missing q"}, status_code=400)
    ans = answer_pipeline(q)
    return {"ok": True, "answer": ans}

# -----------------------------------------------------------------------------
# HOMEPAGE (UI)
# -----------------------------------------------------------------------------
HOME_HTML = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Tecnaria QA Bot</title>
<style>
  body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; background:#0b1220; color:#e6edf3; }
  .wrap { max-width: 980px; margin: 0 auto; padding: 24px; }
  .card { background:#0f172a; border:1px solid #1f2937; border-radius:16px; padding:20px; box-shadow: 0 0 0 1px rgba(255,255,255,0.02) inset; }
  h1 { font-size: 22px; margin: 0 0 12px; }
  .row { display:flex; gap:10px; flex-wrap:wrap; }
  input[type=text] { flex:1; min-width: 260px; padding:12px 14px; border-radius:12px; border:1px solid #334155; background:#111827; color:#e6edf3; }
  button { padding:12px 16px; border-radius:12px; border:1px solid #334155; background:#1f2937; color:#e6edf3; cursor:pointer; }
  button:hover { background:#243143; }
  pre { white-space:pre-wrap; background:#0b1020; border:1px solid #1f2937; padding:14px; border-radius:12px; overflow:auto; }
  .hint { color:#9ca3af; font-size:13px; margin-top:8px; }
  .ep { margin-top: 14px; font-size: 13px; color: #94a3b8; }
  a { color:#93c5fd; text-decoration:none; }
  a:hover { text-decoration:underline; }
</style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Tecnaria QA Bot</h1>
      <div class="row">
        <input id="q" type="text" placeholder="Scrivi una domanda (es. 'Devo usare la P560 per i CTF: serve patentino?')"/>
        <button onclick="ask()">Chiedi</button>
        <button onclick="clearOut()">Pulisci</button>
        <button onclick="copyOut()">Copia risposta</button>
      </div>
      <div class="hint">Endpoint: <code>/ping</code> · <code>/health</code> · <code>/api/ask</code> (GET q=... | POST {"q":"..."})</div>
    </div>

    <div class="card" style="margin-top:16px;">
      <strong>Risposta</strong>
      <pre id="out">—</pre>
      <div class="ep">Suggerimento: per migliori risultati, assicurati che i tuoi <em>Environment</em> su Render includano <code>SEARCH_PROVIDER</code> + chiave (<code>BRAVE_API_KEY</code> o <code>BING_API_KEY</code>) e che il file <code>sinapsi_brain.json</code> sia in <code>""" + CRITICI_DIR + """/sinapsi_brain.json</code>.</div>
    </div>
  </div>
<script>
async function ask() {
  const q = document.getElementById('q').value.trim();
  const out = document.getElementById('out');
  if(!q){ out.textContent = "OK\\n- **Domanda vuota**: inserisci una richiesta valida."; return; }
  out.textContent = "…";
  try{
    const r = await fetch('/api/ask', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ q })
    });
    const j = await r.json();
    out.textContent = j && j.answer ? j.answer : JSON.stringify(j, null, 2);
  }catch(e){
    out.textContent = "Errore: " + e;
  }
}
function clearOut(){ document.getElementById('out').textContent = '—'; }
async function copyOut(){
  const t = document.getElementById('out').textContent || '';
  try{ await navigator.clipboard.writeText(t); }catch(e){}
}
</script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(HOME_HTML)

# -----------------------------------------------------------------------------
# MAIN (local)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT","8000")))
