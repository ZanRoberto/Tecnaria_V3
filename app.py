# app.py — Tecnaria QA Bot (versione completa, robusta)
# Copia questo file in app.py nel repo e fai il deploy.
import os
import re
import json
import time
import html
import textwrap
import urllib.request
import urllib.parse

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# Import opzionali (non bloccanti: se mancanti il server funziona lo stesso)
try:
    import requests  # più comodo per fetch
except Exception:
    requests = None

try:
    from bs4 import BeautifulSoup  # miglior pulizia HTML
except Exception:
    BeautifulSoup = None

APP_NAME = "Tecnaria QA Bot"
app = FastAPI(title=APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- CONFIG ----------
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "brave").lower().strip()
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY")
BING_API_KEY = os.getenv("BING_API_KEY")
PREFERRED_DOMAINS = [d.strip() for d in os.getenv(
    "PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com"
).split(",") if d.strip()]
MIN_WEB_SCORE = float(os.getenv("MIN_WEB_SCORE", "0.55"))  # soglia ricerca web (più alta = più filtrata)
CRITICI_DIR = os.getenv("CRITICI_DIR", "Tecnaria_V3/static/static/data/critici").strip()

# ---------- SINAPSI ----------
@dataclass
class Rule:
    id: str
    pattern: re.Pattern
    mode: str  # override | augment | postscript
    lang: str
    answer: str

def load_sinapsi_rules() -> List[Rule]:
    """
    Cerca file sinapsi_rules.json / sinapsi_brain.json / sinapsi.json nella cartella CRITICI_DIR
    e carica le regole. Se non trova nulla, ritorna lista vuota.
    """
    paths = [
        os.path.join(CRITICI_DIR, "sinapsi_rules.json"),
        os.path.join(CRITICI_DIR, "sinapsi_brain.json"),
        os.path.join(CRITICI_DIR, "sinapsi.json"),
    ]
    rules: List[Rule] = []
    for p in paths:
        try:
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                for item in data:
                    pat = item.get("pattern", None)
                    if not pat:
                        continue
                    rules.append(
                        Rule(
                            id=item.get("id", f"rule_{len(rules)}"),
                            pattern=re.compile(pat, re.IGNORECASE),
                            mode=item.get("mode", "augment").lower(),
                            lang=item.get("lang", "it"),
                            answer=item.get("answer", "").strip()
                        )
                    )
        except Exception:
            # non interrompo il caricamento se un file è corrotto
            continue
    return rules

SINAPSI_RULES = load_sinapsi_rules()

def sinapsi_apply(q: str, base_text: str) -> Tuple[str, Optional[str]]:
    """
    Applica la prima regola matchata (puoi modificare per applicarne più d'una).
    Mode:
      - override: usa solo Sinapsi
      - augment: aggiunge Sinapsi dopo il testo web
      - postscript: aggiunge un PS tecnico breve
    Ritorna (testo_finale, applied_rule_id)
    """
    applied_id = None
    for r in SINAPSI_RULES:
        if r.pattern.search(q):
            applied_id = r.id
            if r.mode == "override":
                return refine_text(r.answer), r.id
            elif r.mode == "augment":
                merged = base_text.strip()
                addon = refine_text(r.answer).strip()
                if merged:
                    merged = merged.rstrip() + "\n\n" + addon
                else:
                    merged = addon
                return merged, r.id
            elif r.mode == "postscript":
                merged = base_text.strip()
                merged += "\n\n*Nota tecnica (Sinapsi)*\n" + refine_text(r.answer).strip()
                return merged, r.id
    return base_text, applied_id

# ---------- HTTP UTILS ----------
def allowed_domain(url: str, prefer: List[str]) -> bool:
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return any(host.endswith(d.lower()) for d in prefer)

def http_get(url: str, headers: Optional[Dict] = None, timeout: float = 6.0) -> Tuple[int, str]:
    """
    GET robusto: usa requests se presente, altrimenti urllib.
    Ritorna (status_code, body_text)
    """
    headers = headers or {}
    if requests:
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            return r.status_code, r.text
        except Exception:
            pass
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as res:
            status = getattr(res, "status", 200)
            body = res.read().decode("utf-8", "ignore")
            return status, body
    except Exception:
        return 599, ""

# ---------- WEB SEARCH (Brave / Bing) ----------
def brave_search(query: str, n: int = 5, timeout: float = 6.0) -> List[Dict]:
    """
    Usa Brave Web Search API se BRAVE_API_KEY è presente.
    Filtra solo domini in PREFERRED_DOMAINS.
    """
    if not BRAVE_API_KEY:
        return []
    url = "https://api.search.brave.com/res/v1/web/search"
    params = {"q": query, "count": n, "freshness": "month"}
    full = url + "?" + urllib.parse.urlencode(params)
    status, txt = http_get(full, headers={"X-Subscription-Token": BRAVE_API_KEY}, timeout=timeout)
    if status != 200:
        return []
    try:
        data = json.loads(txt)
        out = []
        for item in data.get("web", {}).get("results", []):
            u = item.get("url", "")
            if not allowed_domain(u, PREFERRED_DOMAINS):
                continue
            out.append({
                "title": item.get("title", ""),
                "url": u,
                "snippet": item.get("description", ""),
                "score": 0.75
            })
        return out
    except Exception:
        return []

def bing_search(query: str, n: int = 5, timeout: float = 6.0) -> List[Dict]:
    if not BING_API_KEY:
        return []
    url = "https://api.bing.microsoft.com/v7.0/search"
    params = {"q": query, "count": n, "mkt": "it-IT"}
    full = url + "?" + urllib.parse.urlencode(params)
    status, txt = http_get(full, headers={"Ocp-Apim-Subscription-Key": BING_API_KEY}, timeout=timeout)
    if status != 200:
        return []
    try:
        data = json.loads(txt)
        out = []
        for item in data.get("webPages", {}).get("value", []):
            u = item.get("url", "")
            if not allowed_domain(u, PREFERRED_DOMAINS):
                continue
            out.append({
                "title": item.get("name", ""),
                "url": u,
                "snippet": item.get("snippet", ""),
                "score": 0.70
            })
        return out
    except Exception:
        return []

def fetch_url_text(url: str, timeout: float = 6.0) -> str:
    status, body = http_get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    return body if status == 200 else ""

# ---------- HTML CLEANER ----------
def clean_html_text(raw: str) -> str:
    if not raw:
        return ""
    if BeautifulSoup:
        try:
            soup = BeautifulSoup(raw, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text(separator=" ")
            text = html.unescape(text)
            text = re.sub(r"\s+", " ", text).strip()
            return text
        except Exception:
            pass
    # fallback regex
    txt = re.sub(r"<[^>]+>", " ", raw)
    txt = html.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()

# ---------- SNIPPET SYNTHESIS ----------
def synthesize_snippets(snips: List[Dict], q: str) -> Tuple[str, List[str]]:
    """
    Scarica le pagine filtrate e crea un sommario ridotto.
    Cerca parole chiave tecniche per restituire un contenuto utile.
    """
    if not snips:
        return "", []
    bodies, sources = [], []
    for s in snips:
        page = fetch_url_text(s["url"])
        body = clean_html_text(page) or clean_html_text(s.get("snippet", ""))
        if body:
            bodies.append(body[:1500])
            sources.append(s["url"])
    if not bodies:
        return "", sources
    joined = " ".join(bodies)[:4000]
    key = ["CTF", "lamiera", "P560", "HSBR", "Diapason", "ETA", "connettore", "soletta", "laterocemento", "Hi-Bond", "Tecnaria", "SPIT"]
    sentences = re.split(r"(?<=[\.\!\?])\s+", joined)
    keep = [s for s in sentences if any(k.lower() in s.lower() for k in key)]
    draft = " ".join(keep)[:1200] or joined[:800]
    draft = re.sub(r"\s{2,}", " ", draft).strip()
    return draft, list(dict.fromkeys(sources))[:5]

# ---------- REFINER (tono, pulizia) ----------
def refine_text(text_it: str) -> str:
    """
    Uniforma il testo in un tono 'Tecnaria' chiaro e sintetico.
    Rimuove intestazioni superflue e aggiunge lead se mancante.
    """
    if not text_it:
        return ""
    # rimuovi eventuali intestazioni tipo "OK - Riferimento..."
    text_it = re.sub(r"^OK\s*-?\s*(Riferimento|Sintesi).*?\n", "", text_it, flags=re.I | re.M)
    text_it = text_it.replace("**Fonti**", "\n\n**Fonti**")
    lead = (
        "Ecco le informazioni richieste, in modo chiaro e sintetico.\n"
        "Per verifiche di progetto approfondite possiamo lavorare sui dati reali del cantiere."
    )
    body = text_it.strip()
    if body.lower().startswith("ecco le informazioni"):
        return body
    return f"{lead}\n\n{body}".strip()

def compose_final(answer: str, sources: List[str]) -> str:
    answer = answer.strip()
    if sources:
        src = "\n".join(f"- {u}" for u in sources)
        if "**Fonti**" in answer:
            if not answer.strip().endswith("\n"):
                answer += "\n"
            answer += f"**Fonti**\n{src}\n"
        else:
            answer += f"\n\n**Fonti**\n{src}\n"
    return answer

# ---------- PIPELINE ----------
def web_answer(q: str) -> Tuple[str, List[str]]:
    results = []
    if SEARCH_PROVIDER == "brave":
        results = brave_search(q, n=6)
    elif SEARCH_PROVIDER == "bing":
        results = bing_search(q, n=6)
    else:
        results = []
    if not results:
        return "", []
    text, sources = synthesize_snippets(results, q)
    if not text:
        return "", sources
    blocks = []
    patterns = [
        (r"(P560.*?HSBR.*?)(?:\.|;)", "- **Fissaggio**: \\1."),
        (r"(ETA|European Technical Assessment.*?)(?:\.|;)", "- **Normativa**: \\1."),
        (r"(lamiera.*?Hi[- ]?Bond.*?)(?:\.|;)", "- **Compatibilità**: \\1."),
    ]
    used = 0
    for pat, lab in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            blocks.append(lab.replace("\\1", m.group(1)))
            used += 1
    if used < 2:
        blocks = [textwrap.shorten(text, 450, placeholder="…")]
    ans = "OK\n" + "\n".join(blocks)
    ans = refine_text(ans)
    return ans, sources

def answer_pipeline(q: str) -> str:
    web_txt, web_sources = web_answer(q)
    base = web_txt if (web_txt and len(web_txt) >= 60) else ""
    final_txt, applied = sinapsi_apply(q, base)
    if not final_txt:
        final_txt = refine_text(
            "Per questa richiesta servono alcuni dettagli (tipo solaio, luci, carichi, profili, spessori, vincoli). "
            "Possiamo dare un indirizzo prudente e poi validare col calcolo."
        )
    return compose_final(final_txt, web_sources)

# ---------- HTTP Endpoints ----------
@app.get("/ping")
def ping():
    return {"ok": True, "pong": True}

@app.get("/health")
def health():
    exists = os.path.isdir(CRITICI_DIR)
    return {
        "status": "ok",
        "web_search": {
            "provider": SEARCH_PROVIDER,
            "brave_key": bool(BRAVE_API_KEY),
            "bing_key": bool(BING_API_KEY),
            "preferred_domains": PREFERRED_DOMAINS,
            "min_web_score": MIN_WEB_SCORE,
        },
        "critici": {"dir": CRITICI_DIR, "exists": exists, "rules": len(SINAPSI_RULES)},
    }

@app.get("/")
def home():
    return HTMLResponse(UI_HTML)

@app.get("/ask")
def ask_get(q: str = ""):
    q = (q or "").strip()
    if not q:
        return JSONResponse({"ok": True, "answer": "OK\n- **Domanda vuota**: inserisci una richiesta valida."})
    ans = answer_pipeline(q)
    return JSONResponse({"ok": True, "answer": ans})

@app.post("/api/ask")
async def ask_post(req: Request):
    try:
        body = await req.json()
        q = (body.get("q") or "").strip()
    except Exception:
        q = ""
    if not q:
        return JSONResponse({"ok": False, "error": "Missing q"})
    ans = answer_pipeline(q)
    return JSONResponse({"ok": True, "answer": ans})

# ---------- UI HTML (semplice) ----------
UI_HTML = """
<!doctype html><html lang="it"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tecnaria QA Bot</title>
<style>
:root { --bg:#0f172a; --panel:#111827; --txt:#e5e7eb; --muted:#9ca3af; --acc:#16a34a; }
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--txt);font:16px/1.45 system-ui,Segoe UI,Roboto,Arial}
.wrap{max-width:1100px;margin:28px auto;padding:0 20px}
h1{font-size:22px;margin:0 0 14px}
.topbar{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0 18px}
.tag{background:#0b1220;border:1px solid #1f2937;color:#d1d5db;padding:10px 14px;border-radius:26px;cursor:pointer}
.tag:hover{border-color:#374151}
.card{background:var(--panel);border:1px solid #1f2937;border-radius:14px;padding:14px 16px;margin:14px 0}
textarea{width:100%;min-height:160px;background:#0b1220;border:1px solid #1f2937;border-radius:10px;color:var(--txt);padding:12px}
.controls{display:flex;gap:10px;align-items:center;margin-top:12px}
.btn{background:#0b1220;border:1px solid #1f2937;color:#d1d5db;padding:10px 16px;border-radius:10px;cursor:pointer}
.btn:hover{border-color:#374151}
.btn.primary{background:#052e16;border-color:#14532d;color:#d1fae5}
.chk{display:flex;gap:8px;align-items:center;color:var(--muted);font-size:14px;margin-top:8px}
pre{white-space:pre-wrap;word-wrap:break-word}
small{color:var(--muted)}
</style>
</head><body>
<div class="wrap">
  <h1>Tecnaria QA Bot</h1>
  <div class="topbar">
    <div class="tag" onclick="fill('Devo usare la chiodatrice P560 per fissare i CTF. Serve un patentino?')">P560 + CTF</div>
    <div class="tag" onclick="fill('Che differenza c\\'è tra un connettore CTF e il sistema Diapason?')">CTF vs Diapason</div>
    <div class="tag" onclick="fill('Quanti connettori CTF servono per m² su lamiera Hi-Bond e come si fissano?')">Densità CTF</div>
    <div class="tag" onclick="fill('Mi dai i contatti Tecnaria per assistenza tecnica?')">Contatti</div>
  </div>

  <div class="card">
    <textarea id="q" placeholder="Scrivi la domanda..."></textarea>
    <div class="controls">
      <button class="btn primary" onclick="ask()">Chiedi</button>
      <button class="btn" onclick="clr()">Pulisci</button>
      <button class="btn" onclick="copyAns()">Copia risposta</button>
    </div>
    <label class="chk"><input type="checkbox" id="useget"> usa GET (debug)</label>
  </div>

  <div class="card">
    <pre id="ans"> </pre>
    <small>Endpoint: <code>/ask</code> (POST JSON { q }) oppure GET ?q=...</small>
  </div>
</div>

<script>
function fill(t){document.getElementById('q').value=t;}
function clr(){document.getElementById('q').value='';document.getElementById('ans').textContent='';}
async function ask(){
  const q = document.getElementById('q').value.trim();
  if(!q){ document.getElementById('ans').textContent='OK\\n- **Domanda vuota**: inserisci una richiesta valida.'; return; }
  const useget = document.getElementById('useget').checked;
  try{
    let resp;
    if(useget){
      const r = await fetch('/ask?q=' + encodeURIComponent(q));
      resp = await r.json();
    }else{
      const r = await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q})});
      resp = await r.json();
    }
    if(resp.ok){ document.getElementById('ans').textContent = resp.answer; }
    else{ document.getElementById('ans').textContent = 'Errore: ' + (resp.error||''); }
  }catch(e){
    document.getElementById('ans').textContent = 'Errore di risposta';
  }
}
function copyAns(){
  const t = document.getElementById('ans').textContent;
  navigator.clipboard.writeText(t || '');
}
</script>
</body></html>
"""

# ---- end of file
