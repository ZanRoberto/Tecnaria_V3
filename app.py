# app.py
import os, re, json, time, html, textwrap
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup

APP_NAME = "Tecnaria QA Bot"
app = FastAPI(title=APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ---------- CONFIG ----------
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "brave").lower().strip()
BRAVE_API_KEY  = os.getenv("BRAVE_API_KEY")
BING_API_KEY   = os.getenv("BING_API_KEY")
PREFERRED_DOMAINS = [d.strip() for d in os.getenv(
    "PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com"
).split(",") if d.strip()]

MIN_WEB_SCORE = float(os.getenv("MIN_WEB_SCORE", "0.55"))  # soglia alta per qualità
CRITICI_DIR   = os.getenv("CRITICI_DIR", "Tecnaria_V3/static/static/data/critici").strip()

# ---------- SINAPSI ----------
@dataclass
class Rule:
    id: str
    pattern: re.Pattern
    mode: str  # override | augment | postscript
    lang: str
    answer: str

def load_sinapsi_rules() -> List[Rule]:
    paths = [
        os.path.join(CRITICI_DIR, "sinapsi_rules.json"),
        os.path.join(CRITICI_DIR, "sinapsi_brain.json"),
        os.path.join(CRITICI_DIR, "sinapsi.json"),
    ]
    rules: List[Rule] = []
    for p in paths:
        try:
            if os.path.isfile(p):
                data = json.loads(open(p, "r", encoding="utf-8").read())
                for item in data:
                    rules.append(
                        Rule(
                            id=item.get("id", f"rule_{len(rules)}"),
                            pattern=re.compile(item["pattern"], re.IGNORECASE),
                            mode=item.get("mode", "augment").lower(),
                            lang=item.get("lang", "it"),
                            answer=item.get("answer", "").strip()
                        )
                    )
        except Exception:
            continue
    return rules

SINAPSI_RULES = load_sinapsi_rules()

def sinapsi_apply(q: str, base_text: str) -> Tuple[str, Optional[str]]:
    """Ritorna testo e id regola usata (se applicata)."""
    applied_id = None
    for r in SINAPSI_RULES:
        if r.pattern.search(q):
            applied_id = r.id
            if r.mode == "override":
                return refine_text(r.answer), r.id
            elif r.mode == "augment":
                merged = base_text.strip()
                if merged:
                    merged = merged.rstrip() + "\n\n" + refine_text(r.answer).strip()
                else:
                    merged = refine_text(r.answer).strip()
                return merged, r.id
            elif r.mode == "postscript":
                merged = base_text.strip()
                merged += "\n\n*Nota tecnica (Sinapsi)*\n" + refine_text(r.answer).strip()
                return merged, r.id
    return base_text, applied_id

# ---------- WEB SEARCH ----------
def allowed_domain(url: str, prefer: List[str]) -> bool:
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return any(host.endswith(d.lower()) for d in prefer)

def brave_search(query: str, n: int = 5, timeout: float = 6.0) -> List[Dict]:
    if not BRAVE_API_KEY:
        return []
    headers = {"X-Subscription-Token": BRAVE_API_KEY}
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": n, "freshness": "month"},
            headers=headers, timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        out = []
        for item in data.get("web", {}).get("results", []):
            url = item.get("url","")
            if not allowed_domain(url, PREFERRED_DOMAINS):  # filtro domini
                continue
            out.append({
                "title": item.get("title",""),
                "url": url,
                "snippet": item.get("description",""),
                "score": item.get("language","") and 0.75 or 0.65
            })
        return out
    except Exception:
        return []

def bing_search(query: str, n: int = 5, timeout: float = 6.0) -> List[Dict]:
    if not BING_API_KEY:
        return []
    headers = {"Ocp-Apim-Subscription-Key": BING_API_KEY}
    try:
        r = requests.get(
            "https://api.bing.microsoft.com/v7.0/search",
            params={"q": query, "count": n, "mkt":"it-IT"},
            headers=headers, timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        out = []
        for item in data.get("webPages",{}).get("value",[]):
            url = item.get("url","")
            if not allowed_domain(url, PREFERRED_DOMAINS):
                continue
            out.append({
                "title": item.get("name",""),
                "url": url,
                "snippet": item.get("snippet",""),
                "score": 0.70
            })
        return out
    except Exception:
        return []

def fetch_url_text(url: str, timeout: float = 6.0) -> str:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
        r.raise_for_status()
        return r.text
    except Exception:
        return ""

def clean_html_text(raw: str) -> str:
    if not raw:
        return ""
    try:
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script","style","noscript"]):
            tag.decompose()
        text = soup.get_text(separator=" ")
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
    except Exception:
        # fallback rozzo
        txt = re.sub(r"<[^>]+>", " ", raw)
        txt = html.unescape(txt)
        return re.sub(r"\s+", " ", txt).strip()

def synthesize_snippets(snips: List[Dict], q: str) -> Tuple[str, List[str]]:
    """Crea una sintesi pulita con elenco fonti (sempre IT, tono morbido)."""
    if not snips:
        return "", []
    bodies = []
    sources = []
    for s in snips:
        page = fetch_url_text(s["url"])
        body = clean_html_text(page)
        if not body:
            # usa comunque lo snippet se significativo
            body = clean_html_text(s.get("snippet",""))
        if body:
            bodies.append(body[:1500])
            sources.append(s["url"])
    joined = " ".join(bodies)[:4000]
    if not joined:
        return "", []
    # Micro-sintesi “umana”
    # (Regole semplici: tieni frasi con parole chiave e ricompattale)
    key = ["CTF","lamiera","P560","HSBR","Diapason","ETA","connettore","soletta","laterocemento","Hi-Bond"]
    sentences = re.split(r"(?<=[\.\!\?])\s+", joined)
    keep = [s for s in sentences if any(k.lower() in s.lower() for k in key)]
    draft = " ".join(keep)[:1200]
    # ammorbidisci
    draft = re.sub(r"\s{2,}"," ", draft).strip()
    if not draft:
        draft = joined[:800]
    return draft, list(dict.fromkeys(sources))[:5]

# ---------- REFINER ----------
def refine_text(text_it: str) -> str:
    if not text_it:
        return ""
    # pulizia header "OK - Riferimento..."
    text_it = re.sub(r"^OK\s*-?\s*(Riferimento|Sintesi).*?\n", "", text_it, flags=re.I|re.M)
    # bullets coerenti
    # garantisci a capo prima di "**Fonti**"
    text_it = text_it.replace("**Fonti**", "\n\n**Fonti**")
    # tono Tecnaria
    lead = (
        "Ecco le informazioni richieste, in forma chiara e sintetica.\n"
        "Se servono verifiche di progetto, le facciamo su dati reali del cantiere."
    )
    body = text_it.strip()
    # evita doppio lead
    if body.lower().startswith("ecco le informazioni"):
        return body
    return f"{lead}\n\n{body}".strip()

def compose_final(answer: str, sources: List[str]) -> str:
    answer = answer.strip()
    if sources:
        src = "\n".join(f"- {u}" for u in sources)
        if "**Fonti**" in answer:
            # append se già presente
            if not answer.strip().endswith("**Fonti**"):
                if not answer.strip().endswith("\n"):
                    answer += "\n"
            answer += f"**Fonti**\n{src}\n"
        else:
            answer += f"\n\n**Fonti**\n{src}\n"
    return answer

# ---------- PIPELINE ----------
def web_answer(q: str) -> Tuple[str, List[str]]:
    # fai ricerca solo su domini preferiti
    results = []
    if SEARCH_PROVIDER == "brave":
        results = brave_search(q, n=6)
    elif SEARCH_PROVIDER == "bing":
        results = bing_search(q, n=6)
    else:
        # se provider non configurato, niente web
        results = []

    if not results:
        return "", []

    text, sources = synthesize_snippets(results, q)
    if not text:
        return "", sources

    # alza la qualità: riformula in bullet morbidi
    blocks = []
    # regolette di estrazione rapide
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
        # se poco strutturato, metti un riassunto unico
        blocks = [textwrap.shorten(text, 450, placeholder="…")]

    ans = "OK\n" + "\n".join(blocks)
    ans = refine_text(ans)
    return ans, sources

def answer_pipeline(q: str) -> str:
    # 1) web
    web_txt, web_sources = web_answer(q)

    # 2) se web scarso, forza Sinapsi override/augment
    if not web_txt or len(web_txt) < 60:
        base = ""
    else:
        base = web_txt

    final_txt, rule_id = sinapsi_apply(q, base)

    # 3) se anche qui vuoto, prova “domande classiche” fallback
    if not final_txt:
        # micro fallback sensato
        fallback = (
            "Per questa richiesta servono dettagli tecnici (tipo solaio, luci, carichi, profilo lamiera, "
            "spessori, vincoli). Possiamo stimare in modo prudente e poi validare col progetto."
        )
        final_txt = refine_text(fallback)

    # 4) comporre + fonti
    out = compose_final(final_txt, web_sources)
    return out

# ---------- HTTP ----------
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

def get_q_from_req(request: Request) -> str:
    try:
        data = {}
        if request.headers.get("content-type","").startswith("application/json"):
            data = json.loads((yield from request.body()))
        else:
            data = {}
    except Exception:
        data = {}
    return (data.get("q") or "").strip()

@app.get("/ask")
def ask_get(q: str=""):
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

# ---------- UI (dark) ----------
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
    <div class="tag" onclick="fill('Che differenza c’è tra un connettore CTF e il sistema Diapason?')">CTF vs Diapason</div>
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
