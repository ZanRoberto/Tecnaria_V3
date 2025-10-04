import os, re, json, math, time, html, urllib.parse, urllib.request
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from bs4 import BeautifulSoup  # beautifulsoup4 è leggero ed era già presente nei tuoi deploy
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

# =========================
# ====== CONFIG BASE ======
# =========================
APP_NAME = "Tecnaria QA Bot"

# ENV
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "brave").lower().strip()   # brave | bing | off
BRAVE_API_KEY   = os.getenv("BRAVE_API_KEY", "").strip()
BING_API_KEY    = os.getenv("BING_API_KEY", "").strip()

PREFERRED_DOMAINS = [d.strip().lower() for d in os.getenv(
    "PREFERRED_DOMAINS",
    "tecnaria.com, spit.eu, spitpaslode.com"
).split(",") if d.strip()]

MIN_WEB_SCORE = float(os.getenv("MIN_WEB_SCORE", "0.55"))   # alzata per evitare “spazzatura”
WEB_TIMEOUT   = float(os.getenv("WEB_TIMEOUT", "7.5"))
WEB_RETRIES   = int(os.getenv("WEB_RETRIES", "2"))

# Cartelle
BASE_DIR      = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR    = BASE_DIR / "static"
CRITICI_DIR   = os.getenv("CRITICI_DIR", str(BASE_DIR / "static" / "data" / "critici")).strip()
CRITICI_DIR   = Path(CRITICI_DIR)

# File Sinapsi
SINAPSI_FILE  = CRITICI_DIR / "sinapsi_rules.json"

# ================ UTILS ================
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

def _req(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = WEB_TIMEOUT) -> Tuple[int, Dict[str,str], bytes]:
    """urllib senza dipendenze esterne (requests/httpx)"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = resp.getcode()
        hdrs = {k.lower(): v for k, v in resp.headers.items()}
        data = resp.read()
        return status, hdrs, data

def allowed_domain(url: str, prefer: List[str]) -> bool:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return False
    # es: www.tecnaria.com → tecnaria.com
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return any(host.endswith(d) for d in prefer)

def safe_url(u: str) -> str:
    # pulizia minima per “Fonti”
    try:
        p = urllib.parse.urlparse(u)
        clean = urllib.parse.urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
        return clean
    except Exception:
        return u

def strip_html_get_text(html_bytes: bytes, content_type: str) -> str:
    """Evita di sputare HTML/JS. Se PDF → segnala e basta; altrimenti estrae testo leggibile."""
    ct = (content_type or "").lower()
    if "application/pdf" in ct or (len(html_bytes) > 4 and html_bytes[:4] == b"%PDF"):
        return "[PDF individuato: contenuto tecnico disponibile nella fonte; consultare il documento.]"
    # HTML
    text = html_bytes.decode(errors="ignore")
    soup = BeautifulSoup(text, "html.parser")
    # rimuovi script/style/nav/footer
    for bad in soup(["script", "style", "noscript"]):
        bad.decompose()
    # elimina elementi di navigazione comuni
    for sel in ["header", "nav", "footer", "form", "aside"]:
        for node in soup.select(sel):
            node.decompose()
    txt = soup.get_text(separator=" ", strip=True)
    # compatta spazi
    txt = re.sub(r"\s{2,}", " ", txt)
    return txt[:20000]  # limite sicurezza

def detect_english(text: str) -> bool:
    # euristica ultra-semplice: troppe parole inglesi comuni?
    en_hits = len(re.findall(r"\b(the|and|with|for|to|from|by|is|are|of|in|on|sheet|steel|concrete|connector|nail|gun)\b", text, re.I))
    it_hits = len(re.findall(r"\b(il|la|le|gli|con|per|senza|sui|nelle|degli|del|della|dei|una|un|sono|è)\b", text, re.I))
    return en_hits > it_hits

def refine_answer(q: str, web_bullets: List[str], sinapsi_add: List[str]) -> str:
    """Costruisce risposta in tono Tecnaria (tecnico ma morbido, chiaro, fluido)."""
    chunks = []
    if not web_bullets and not sinapsi_add:
        return "OK\n- **Non ho trovato una risposta affidabile** (o la ricerca non è configurata)."
    # Intro leggerissima solo se utile
    # Corregge bullet grezzi
    def clean_bullet(b: str) -> str:
        b = html.unescape(b)
        b = re.sub(r"\s+", " ", b).strip(" -•\u2022")
        # rimuove residui tipo "(function(html)...", "WP 3D Thingviewer..."
        b = re.sub(r"\b(function\(html\)|WP 3D Thingviewer.*|Schema\.org|cookies?|accept|policy|login|subscribe)\b.*", "", b, flags=re.I)
        # taglia righe rumorose
        if len(b) > 400:  # non incollare paragrafi interi
            b = b[:400].rstrip() + "…"
        return b.strip()

    cleaned = [clean_bullet(b) for b in web_bullets if b and len(clean_bullet(b)) >= 8]
    addenda = [clean_bullet(b) for b in sinapsi_add if b and len(clean_bullet(b)) >= 4]

    bullets = []
    seen = set()
    for b in cleaned + addenda:
        if b and b not in seen:
            seen.add(b)
            # normalizza inizia con trattino markdown
            if not b.startswith(("-", "•", "–", "—")):
                b = "- " + b
            bullets.append(b)

    if not bullets:
        return "OK\n- **Non ho trovato una risposta affidabile** (oppure le fonti contenevano solo elementi non testuali)."

    return "OK\n" + "\n".join(bullets)

# ============== SINAPSI ==============
class Rule:
    __slots__ = ("id","pattern","mode","lang","answer","rx")
    def __init__(self, d: Dict[str, Any]):
        self.id      = d.get("id") or f"rule_{int(time.time()*1000)}"
        self.pattern = d.get("pattern", "")
        self.mode    = (d.get("mode","augment")).lower()  # override | augment | postscript
        self.lang    = d.get("lang","it")
        self.answer  = d.get("answer","")
        self.rx      = re.compile(self.pattern, re.I)

def load_sinapsi() -> List[Rule]:
    try:
        if SINAPSI_FILE.exists():
            data = json.loads(SINAPSI_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [Rule(x) for x in data]
    except Exception:
        pass
    return []

def apply_sinapsi(q: str, rules: List[Rule]) -> Tuple[Optional[str], List[str], List[str]]:
    """
    Ritorna: (override_answer, augment_bullets, postscript_bullets)
    """
    ovr = None
    aug, ps = [], []
    for r in rules:
        if r.rx.search(q):
            if r.mode == "override" and not ovr:
                ovr = r.answer
            elif r.mode == "augment":
                aug.append(r.answer.strip())
            elif r.mode == "postscript":
                ps.append(r.answer.strip())
    return ovr, aug, ps

# ============== WEB SEARCH ==============
def brave_search(query: str, n: int = 5) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        return []
    url = f"https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote(query)}&count={n}"
    try:
        status, hdrs, data = _req(url, headers={"X-Subscription-Token": BRAVE_API_KEY})
        if status != 200:
            return []
        obj = json.loads(data.decode("utf-8", errors="ignore"))
        results = []
        for it in obj.get("web", {}).get("results", []):
            u = it.get("url","")
            if allowed_domain(u, PREFERRED_DOMAINS):
                score = float(it.get("languageScore", 0.6))  # campo “soft” → fallback
                if score >= MIN_WEB_SCORE:
                    results.append({"url": u, "title": it.get("title",""), "snippet": it.get("description","")})
        return results[:n]
    except Exception:
        return []

def bing_search(query: str, n: int = 5) -> List[Dict[str, Any]]:
    if not BING_API_KEY:
        return []
    url = f"https://api.bing.microsoft.com/v7.0/search?q={urllib.parse.quote(query)}&count={n}"
    try:
        status, hdrs, data = _req(url, headers={"Ocp-Apim-Subscription-Key": BING_API_KEY})
        if status != 200:
            return []
        obj = json.loads(data.decode("utf-8", errors="ignore"))
        results = []
        for it in obj.get("webPages", {}).get("value", []):
            u = it.get("url","")
            if allowed_domain(u, PREFERRED_DOMAINS):
                score = float(it.get("rankingResponse", {}).get("mainline", {}).get("items", [{}])[0].get("resultIndex", 1))  # super-greedy fallback
                if score >= 0:  # Bing non espone un vero score: filtriamo solo per dominio
                    results.append({"url": u, "title": it.get("name",""), "snippet": it.get("snippet","")})
        return results[:n]
    except Exception:
        return []

def web_search(query: str, n: int = 4) -> List[Dict[str, Any]]:
    if SEARCH_PROVIDER == "brave":
        return brave_search(query, n)
    if SEARCH_PROVIDER == "bing":
        return bing_search(query, n)
    return []  # “off”

def fetch_and_extract(url: str) -> str:
    try:
        st, hdrs, data = _req(url)
        ct = hdrs.get("content-type","")
        return strip_html_get_text(data, ct)
    except Exception:
        return ""

def synth_from_text(q: str, txt: str) -> List[str]:
    """Estrae 3–5 bullet utili dal testo (banalissimo estrattivo)"""
    if not txt:
        return []
    # prendi frasi corte che contengono parole chiave dal mondo Tecnaria
    keys = [
        r"ctf", r"diapason", r"lamiera", r"hi[- ]?bond", r"p560", r"hsbr",
        r"viti", r"legno", r"laterocemento", r"acciaio", r"calcestruzzo",
        r"eta", r"fissagg", r"dpi", r"chiod", r"connettore", r"soletta"
    ]
    rx = re.compile("|".join(keys), re.I)
    # split per punto fermo
    parts = re.split(r"(?<=[\.\!\?])\s+", txt)
    picked = []
    seen = set()
    for s in parts:
        s = s.strip()
        if 40 <= len(s) <= 220 and rx.search(s):
            s = re.sub(r"\s+", " ", s)
            if s not in seen:
                seen.add(s)
                picked.append("- " + s)
        if len(picked) >= 5:
            break
    # Se niente, prova estrarre 2 righe dal mezzo
    if not picked and len(txt) > 120:
        mids = txt[:800]
        mids = re.sub(r"\s+", " ", mids)
        spl = mids.split(". ")
        for s in spl[:4]:
            s = s.strip()
            if 30 <= len(s) <= 200:
                picked.append("- " + s)
            if len(picked) >= 4:
                break
    return picked

def ensure_italian(bullets: List[str]) -> List[str]:
    """Se appaiono frasi inglesi, le rendiamo italiane in modo semplice (parafrasi chiave)."""
    out = []
    for b in bullets:
        raw = b
        s = b
        # mapping super-semplice per parole frequenti
        repl = {
            "steel": "acciaio",
            "concrete": "calcestruzzo",
            "sheet": "lamiera",
            "nail gun": "chiodatrice",
            "nail": "chiodo",
            "shear connector": "connettore a taglio",
            "installation": "posa/Installazione",
            "fastening": "fissaggio",
            "safety": "sicurezza",
            "certificate": "certificazione",
            "ETA": "ETA",
            "advantages": "vantaggi",
        }
        for k,v in repl.items():
            s = re.sub(rf"\b{k}\b", v, s, flags=re.I)
        # se ancora molto inglese, premettiamo “(contenuto tradotto sinteticamente)”
        if detect_english(s):
            s = s.replace("- ", "- (trad.) ", 1)
        out.append(s)
    return out

# ============== LOGICA RISPOSTA ==============
def answer_for(q: str) -> Tuple[str, List[str]]:
    """
    Ritorna: testo risposta + elenco fonti (URL puliti)
    """
    rules = load_sinapsi()
    ovr, aug, ps = apply_sinapsi(q, rules)
    sources: List[str] = []

    if ovr:  # percorso deterministico
        # “ovr” può già essere in formato completo con "OK\n- ..."
        base = ovr.strip()
        # eventuali postscript
        if ps:
            base = base.rstrip() + "\n" + "\n".join(ps)
        return base, []

    # 1) Web search (solo domini preferiti)
    results = web_search(q, n=4)
    texts: List[str] = []
    for r in results:
        url = r["url"]
        if not allowed_domain(url, PREFERRED_DOMAINS):
            continue
        art = fetch_and_extract(url)
        if art:
            texts.append(art)
            sources.append(safe_url(url))
        if len(texts) >= 3:
            break

    web_bullets: List[str] = []
    for t in texts:
        web_bullets += synth_from_text(q, t)

    # pulizia + traduzione in italiano se necessario
    web_bullets = ensure_italian(web_bullets)

    # 2) Augment da Sinapsi (se presenti)
    sinapsi_aug = []
    for a in aug:
        # se l'addon è già a bullet, mantienilo; se è un paragrafo, pre-bullet
        if "\n" in a:
            sinapsi_aug += [ln for ln in a.splitlines() if ln.strip()]
        else:
            sinapsi_aug.append("- " + a.strip())

    # 3) Componi risposta
    txt = refine_answer(q, web_bullets, sinapsi_aug)

    # 4) Postscript (brevi note finali)
    if ps:
        txt = txt.rstrip() + "\n" + "\n".join(ps)

    return txt, sources[:4]

# ============== FASTAPI APP ==============
app = FastAPI(title=APP_NAME)

# static (se servono css/js)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# semplice interfaccia HTML (una pagina)
HTML_PAGE = f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{APP_NAME}</title>
<style>
:root {{ --bg:#0b0f14; --card:#0f1720; --txt:#e8f0ff; --muted:#8aa0b7; --pri:#3ea6ff; }}
* {{ box-sizing: border-box; }}
html,body {{ margin:0; background:var(--bg); color:var(--txt); font-family:system-ui,-apple-system,Segoe UI,Roboto,Inter,Helvetica,Arial,sans-serif; }}
.wrap {{ max-width: 980px; margin: 0 auto; padding: 24px; }}
h1 {{ margin:0 0 12px 0; font-size: 20px; color: var(--txt);}}
.card {{ background: var(--card); border: 1px solid #1c2733; border-radius: 14px; padding: 16px; }}
.row {{ display:flex; gap:10px; align-items:center; }}
input[type=text] {{ flex:1; padding:12px 14px; border-radius:10px; border:1px solid #203041; background:#0c1420; color:#e8f0ff; }}
button {{ padding:10px 14px; border:1px solid #284256; background:#112233; color:#e8f0ff; border-radius:10px; cursor:pointer; }}
button:hover {{ background:#14314a; }}
.small {{ color:var(--muted); font-size:12px; }}
pre {{ white-space: pre-wrap; word-wrap: break-word; }}
.kbd {{ display:inline-block; border:1px solid #334a61; padding:1px 6px; border-radius:6px; font-size:12px; background:#102030; color:#bcd; }}
.srcs a {{ color: var(--pri); text-decoration:none; }}
hr {{ border: none; border-top: 1px solid #223040; margin: 14px 0; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>{APP_NAME}</h1>
  <div class="card">
    <div class="row">
      <input id="q" type="text" placeholder="Scrivi la domanda (es. Devo usare la chiodatrice P560 per CTF: serve patentino?)" />
      <button onclick="ask()">Chiedi</button>
      <button onclick="demo('p560')">Esempio P560</button>
      <button onclick="demo('ctfvsdiap')">CTF vs Diapason</button>
      <button onclick="demo('dens')">Densità CTF</button>
    </div>
    <div class="small" style="margin-top:8px;">Endpoint: <span class="kbd">/ping</span> <span class="kbd">/health</span> <span class="kbd">/api/ask</span> (GET q=... | POST JSON &#123;q&#58;...&#125;)</div>
  </div>

  <div id="out" style="margin-top:12px;"></div>
</div>
<script>
async function ask() {{
  const q = document.getElementById('q').value.trim();
  if(!q) {{ alert('Domanda vuota'); return; }}
  const out = document.getElementById('out');
  out.innerHTML = '<div class="card"><div class="small">Sto cercando...</div></div>';
  try {{
    const r = await fetch('/api/ask', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ q }})
    }});
    const js = await r.json();
    let html = '<div class="card"><pre>'+ (js.answer || 'N/D') +'</pre>';
    if(js.sources && js.sources.length) {{
      html += '<hr><div class="srcs small"><b>Fonti</b><br/>' + js.sources.map(s => '<a href="'+s+'" target="_blank">'+s+'</a>').join('<br/>') + '</div>';
    }}
    html += '</div>';
    out.innerHTML = html;
  }} catch(e) {{
    out.innerHTML = '<div class="card"><pre>Errore: '+e+'</pre></div>';
  }}
}}

function demo(kind) {{
  const q = document.getElementById('q');
  if(kind==='p560') q.value = 'Devo usare la chiodatrice P560 per fissare i CTF. Serve un patentino o formazione speciale?';
  if(kind==='ctfvsdiap') q.value = 'Che differenza c’è tra connettore CTF e sistema Diapason? Quando conviene usare uno o l’altro?';
  if(kind==='dens') q.value = 'Su lamiera Hi-Bond 1 mm: quanti connettori CTF servono al m² e come avviene il fissaggio con P560?';
}}
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTML_PAGE

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
            "bing_key": bool(BING_API_KEY),
            "preferred_domains": PREFERRED_DOMAINS,
            "min_web_score": MIN_WEB_SCORE
        },
        "critici": {
            "dir": str(CRITICI_DIR),
            "exists": CRITICI_DIR.exists(),
            "sinapsi_file": str(SINAPSI_FILE),
            "sinapsi_loaded": len(load_sinapsi())
        }
    }

def _answer_payload(q: str) -> Dict[str, Any]:
    ans, srcs = answer_for(q)
    # Se la domanda è chiaramente “patentino P560” e il web ha parlato d'altro, Sinapsi (se presente) ha già “override”.
    # In ogni caso non mostriamo più JS/PDF in risposta: ans è già bullet puliti.
    return {"ok": True, "answer": ans, "sources": srcs}

@app.get("/ask")
def ask_get(q: Optional[str] = None):
    if not q or not q.strip():
        return {"ok": True, "answer": "OK\n- **Domanda vuota**: inserisci una richiesta valida."}
    return _answer_payload(q.strip())

@app.post("/api/ask")
async def ask_post(req: Request):
    js = await req.json()
    q  = (js.get("q") or "").strip()
    if not q:
        return {"ok": True, "answer": "OK\n- **Domanda vuota**: inserisci una richiesta valida."}
    return _answer_payload(q)
