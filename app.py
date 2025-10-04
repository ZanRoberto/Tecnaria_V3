# app.py
# Tecnaria QA Bot – versione “blindata”
# - Interfaccia web inclusa
# - Ricerca web (Brave/Bing) con filtro domini ufficiali
# - Pulizia HTML/PDF
# - Refiner ITA tecnico/morbido
# - Sinapsi: override / augment / postscript

import os
import re
import json
import time
import html
import math
import urllib.parse
import urllib.request
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel

# -----------------------------
# CONFIG
# -----------------------------
APP_NAME = os.getenv("SERVICE_NAME", "Tecnaria QA Bot")
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "brave").lower().strip()  # brave|bing|none
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()
BING_API_KEY = os.getenv("BING_API_KEY", "").strip()

PREFERRED_DOMAINS = [d.strip().lower() for d in os.getenv(
    "PREFERRED_DOMAINS",
    "tecnaria.com, spit.eu, spitpaslode.com"
).split(",") if d.strip()]

MIN_WEB_SCORE = float(os.getenv("MIN_WEB_SCORE", "0.55"))

CRITICI_DIR = os.getenv("CRITICI_DIR", "Tecnaria_V3/static/static/data/critici").strip()
SINAPSI_FILE = os.path.join(CRITICI_DIR, "sinapsi_rules.json")

WEB_TIMEOUT = float(os.getenv("WEB_TIMEOUT", "7.0"))
WEB_RETRIES = int(os.getenv("WEB_RETRIES", "1"))

# -----------------------------
# UTILS
# -----------------------------
def now_ms() -> int:
    return int(time.time() * 1000)

def allowed_domain(url: str, prefer: List[str]) -> bool:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return False
    for dom in prefer:
        if dom and host.endswith(dom):
            return True
    return False

TAG_RE = re.compile(r"<[^>]+>")
MULTI_WS_RE = re.compile(r"\s+")
STRONG_RE = re.compile(r"</?strong[^>]*>", re.I)

def strip_html(text: str) -> str:
    if not text:
        return ""
    # rimuovi strong/bold per primi
    text = STRONG_RE.sub("", text)
    # elimina tutto l’HTML
    text = TAG_RE.sub(" ", text)
    # unescape entità
    text = html.unescape(text)
    # compattazione spazi
    text = MULTI_WS_RE.sub(" ", text).strip()
    return text

def safe_cut(s: str, limit: int = 600) -> str:
    s = (s or "").strip()
    if len(s) <= limit:
        return s
    # taglia a fine frase se possibile
    cut = s[:limit]
    last_dot = max(cut.rfind(". "), cut.rfind("·"), cut.rfind(";"))
    if last_dot > 120:
        return cut[:last_dot+1].strip()
    return cut + "…"

def is_probably_english(s: str) -> bool:
    # euristica light: molte parole con lettere non accentate + stopword ENG
    if not s:
        return False
    eng_hits = len(re.findall(r"\b(the|and|with|for|of|to|from|by|on|in)\b", s.lower()))
    ita_hits = len(re.findall(r"\b(il|lo|la|gli|le|con|per|dal|della|in)\b", s.lower()))
    return eng_hits > ita_hits

# -----------------------------
# SINAPSI
# -----------------------------
def load_sinapsi_rules() -> List[Dict[str, Any]]:
    try:
        with open(SINAPSI_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # supporta anche file che contengono più array appiccicati (tolleranza “storica”)
        if isinstance(data, list):
            return data
        # se è un dizionario con chiave "rules"
        if isinstance(data, dict) and isinstance(data.get("rules"), list):
            return data["rules"]
    except FileNotFoundError:
        return []
    except Exception:
        # se corrotto o JSON non valido → ignora
        return []
    return []

def apply_sinapsi(q: str, rules: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Restituisce: { mode: 'override'|'augment'|'postscript'|None, text: str|None }"""
    if not q or not rules:
        return {"mode": None, "text": None}
    qnorm = q.strip()
    for r in rules:
        pattern = r.get("pattern") or ""
        mode = (r.get("mode") or "").lower().strip() or "augment"
        lang = (r.get("lang") or "it").lower()
        ans = r.get("answer") or ""
        try:
            if re.search(pattern, qnorm, flags=re.I):
                # Sinapsi risponde sempre in ITA
                cleaned = strip_html(ans)
                return {"mode": mode, "text": cleaned}
        except re.error:
            # pattern non valido → ignora
            continue
    return {"mode": None, "text": None}

# -----------------------------
# WEB SEARCH
# -----------------------------
def brave_search(query: str) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        return []
    url = "https://api.search.brave.com/res/v1/web/search?{}".format(
        urllib.parse.urlencode({"q": query, "count": 8, "country": "it"})
    )
    req = urllib.request.Request(url, headers={
        "X-Subscription-Token": BRAVE_API_KEY,
        "Accept": "application/json"
    })
    for attempt in range(WEB_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=WEB_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            items = []
            web = data.get("web", {})
            for blk in web.get("results", []):
                u = blk.get("url") or ""
                t = blk.get("title") or ""
                s = blk.get("description") or blk.get("snippet") or ""
                sc = float(blk.get("score", 0.7))  # se assente, assume buon punteggio
                items.append({"url": u, "title": t, "snippet": s, "score": sc})
            return items
        except Exception:
            if attempt >= WEB_RETRIES:
                return []
            time.sleep(0.2)
    return []

def bing_search(query: str) -> List[Dict[str, Any]]:
    if not BING_API_KEY:
        return []
    url = "https://api.bing.microsoft.com/v7.0/search?{}".format(
        urllib.parse.urlencode({"q": query, "count": 8, "mkt": "it-IT"})
    )
    req = urllib.request.Request(url, headers={
        "Ocp-Apim-Subscription-Key": BING_API_KEY,
        "Accept": "application/json"
    })
    for attempt in range(WEB_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=WEB_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            items = []
            for blk in (data.get("webPages", {}) or {}).get("value", []):
                u = blk.get("url") or ""
                t = blk.get("name") or ""
                s = blk.get("snippet") or ""
                sc = float(blk.get("rank", 0.7))  # Bing non espone "score": usa rank fallback
                items.append({"url": u, "title": t, "snippet": s, "score": sc})
            return items
        except Exception:
            if attempt >= WEB_RETRIES:
                return []
            time.sleep(0.2)
    return []

def web_search(query: str) -> List[Dict[str, Any]]:
    if SEARCH_PROVIDER == "brave":
        raw = brave_search(query)
    elif SEARCH_PROVIDER == "bing":
        raw = bing_search(query)
    else:
        raw = []

    # filtro domini + punteggio
    out = []
    for it in raw:
        u = it.get("url", "")
        if not u:
            continue
        if not allowed_domain(u, PREFERRED_DOMAINS):
            continue
        sc = float(it.get("score", 0.0) or 0.0)
        if sc < MIN_WEB_SCORE:
            continue
        out.append({
            "url": u,
            "title": strip_html(it.get("title", "")),
            "snippet": strip_html(it.get("snippet", "")),
            "score": sc
        })
    return out[:5]

# -----------------------------
# FETCH & CLEAN (HTML / PDF HEAD)
# -----------------------------
def head_content_type(url: str) -> str:
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=WEB_TIMEOUT) as resp:
            return resp.headers.get("Content-Type", "").lower()
    except Exception:
        return ""

def fetch_and_clean(url: str) -> str:
    """Scarica contenuto testuale; se PDF, non scarica il binario: restituisce solo indicazione+URL."""
    ctype = head_content_type(url)
    if "pdf" in ctype:
        # niente PDF blob in risposta – solo citazione
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=WEB_TIMEOUT) as resp:
            raw = resp.read()
        text = raw.decode("utf-8", "ignore")
        return safe_cut(strip_html(text), 1200)
    except Exception:
        return ""

# -----------------------------
# REFINER (riscrittura ITA fluida)
# -----------------------------
def refine_answer(q: str, web_points: List[str], sinapsi_text: Optional[str]) -> str:
    bullets = []

    # Se Sinapsi in override → restituisci quello già ben formattato
    if sinapsi_text and sinapsi_text.strip().lower().startswith("ok"):
        # è già nel formato OK\n- ...
        return sinapsi_text.strip()

    # Base “OK” + punti tecnici puliti
    bullets.extend([f"- {p}" for p in web_points if p])

    # Se Sinapsi è in augment/postscript → fondilo con eleganza
    if sinapsi_text:
        # Togli eventuale “OK” iniziale di sinapsi
        s = sinapsi_text.strip()
        s = re.sub(r"^ok\s*", "", s, flags=re.I).strip()
        # se ha già punti, non duplicare “- ” all’inizio
        if s.startswith("-"):
            bullets.append(s)
        else:
            bullets.append(f"- {s}")

    # Tono tecnico, morbido, fluido (semplice euristica)
    # Evita frasi stock tipo “Ecco le informazioni…”
    # Inizio con “OK” come stile richiesto
    body = "OK\n" + "\n".join(bullets).strip() + "\n"
    return body

def build_sources(urls: List[str]) -> str:
    if not urls:
        return ""
    unique = []
    for u in urls:
        if u not in unique:
            unique.append(u)
    return "\n**Fonti**\n" + "\n".join(f"- {u}" for u in unique) + "\n"

# -----------------------------
# ORCHESTRAZIONE RISPOSTA
# -----------------------------
def answer_q(q: str) -> Dict[str, Any]:
    if not q or not q.strip():
        return {"ok": True, "answer": "OK\n- **Domanda vuota**: inserisci una richiesta valida.\n"}

    rules = load_sinapsi_rules()
    sinapsi_hit = apply_sinapsi(q, rules)  # {mode, text}
    mode = sinapsi_hit["mode"]
    sin_text = sinapsi_hit["text"]

    # Se mode == override → Sinapsi prevale completamente
    if mode == "override" and sin_text:
        ans = sin_text
        # Aggiungi etichetta fonti in coda, sobria
        ans += "\n\n**Fonti**\n- Sinapsi (curated)\n"
        return {"ok": True, "answer": ans}

    # Altrimenti prova web
    web = web_search(q)
    used_urls = []
    web_points: List[str] = []

    if web:
        for item in web:
            u = item["url"]
            used_urls.append(u)
            snippet = item.get("snippet") or ""
            text = fetch_and_clean(u)
            # Se non troviamo testo (PDF o fallimento), usa snippet
            base = text if text else snippet
            base = strip_html(base)

            # Estrai poche frasi sensate, in ITA; se sembra inglese → riassumi senza anglicismi
            if is_probably_english(base):
                base = re.sub(r"\b(steel|concrete|nail|gun|shear|connector|composite|floor|beam|manual)\b", 
                              lambda m: {
                                  "steel": "acciaio", "concrete":"calcestruzzo", "nail":"chiodo",
                                  "gun":"chiodatrice", "shear":"taglio", "connector":"connettore",
                                  "composite":"collaborante", "floor":"solaio", "beam":"trave", "manual":"manuale"
                              }[m.group(0)], base, flags=re.I)
            # taglio breve e pulito
            base = safe_cut(base, 300)

            # Filtra rumori generici
            if base and len(base) > 50:
                web_points.append(base)

            if len(web_points) >= 4:
                break

    # Se niente web e niente sinapsi → rispondi in modo onesto ma utile
    if not web and not sin_text:
        ans = "OK\n- **Non ho trovato una risposta affidabile su fonti ufficiali** al momento. Prova a riformulare oppure indicami il contesto (prodotto, solaio, spessori). In alternativa, posso fornirti i contatti Tecnaria.\n"
        return {"ok": True, "answer": ans}

    # Fusione (augment/postscript/default)
    final = refine_answer(q, web_points, sin_text if mode in ("augment", "postscript", None) else None)
    final = final.rstrip() + "\n" + build_sources(used_urls or (["Sinapsi (curated)"] if sin_text else []))
    return {"ok": True, "answer": final}

# -----------------------------
# FASTAPI
# -----------------------------
app = FastAPI(title=APP_NAME)

class AskBody(BaseModel):
    q: str

@app.get("/ping")
def ping():
    return {"ok": True, "pong": True}

@app.get("/health")
def health():
    sinapsi_exists = os.path.isfile(SINAPSI_FILE)
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
            "sinapsi_file": SINAPSI_FILE,
            "exists": sinapsi_exists
        }
    }

@app.get("/api/ask")
def ask_get(q: str = Query(..., description="Domanda")):
    return JSONResponse(answer_q(q))

@app.post("/api/ask")
def ask_post(body: AskBody):
    q = body.q
    return JSONResponse(answer_q(q))

# -----------------------------
# INTERFACCIA WEB
# -----------------------------
HOME_HTML = f"""
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{APP_NAME}</title>
<style>
:root {{
  --bg: #0b1020; --card: #11182e; --ink: #e9edf7; --ink2:#b9c2d9;
  --pri:#3aa0ff; --acc:#18d6a9; --mut:#70819a;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 0; background: linear-gradient(180deg,#0b1020,#0a0f1d);
  color: var(--ink); font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
}}
.wrap {{ max-width: 960px; margin: 0 auto; padding: 28px; }}
.header {{ display:flex; align-items:center; gap:14px; }}
.logo {{
  width: 42px; height: 42px; border-radius: 10px; background: linear-gradient(135deg,#1b2a52, #24335f);
  display:flex; align-items:center; justify-content:center; font-weight:700; color:#8fd7ff; border:1px solid #1f2b4a;
}}
h1 {{ font-size: 22px; margin: 0; }}
.desc {{ color: var(--ink2); margin-top: 4px; font-size: 14px; }}
.card {{
  margin-top: 18px; background: var(--card); border: 1px solid #1a2342; border-radius: 14px; padding: 16px;
}}
label {{ font-size: 13px; color: var(--ink2); }}
textarea {{
  width: 100%; min-height: 120px; resize: vertical; margin-top: 8px;
  background: #0e1530; color: var(--ink); border: 1px solid #223160; border-radius: 10px; padding: 12px; line-height: 1.4;
}}
.controls {{ display:flex; gap: 10px; margin-top: 12px; flex-wrap: wrap; }}
button {{
  background: linear-gradient(135deg,#1f6fe0,#2aa7ff);
  color:#fff; border: none; padding: 10px 14px; border-radius: 10px; cursor: pointer; font-weight: 600;
}}
button.sec {{ background: #1e2747; color: var(--ink2); border: 1px solid #2a386b; }}
.out {{ white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }}
.small {{ font-size: 12px; color: var(--mut); }}
footer {{ margin-top: 18px; color: var(--mut); font-size: 12px; }}
kbd {{ background:#0e1530; padding:2px 6px; border-radius:6px; border:1px solid #223160; }}
.badge {{ display:inline-block; background:#0e1530; border:1px solid #223160; color:#8fd7ff; padding:3px 8px; border-radius:999px; font-size:12px; }}
.row {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
</style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <div class="logo">TQ</div>
      <div>
        <h1>{APP_NAME}</h1>
        <div class="desc">Risposte tecniche su prodotti e sistemi Tecnaria, con ricerca web ufficiale + Sinapsi.</div>
      </div>
    </div>

    <div class="card">
      <label for="q">Domanda</label>
      <textarea id="q" placeholder="Es: Devo usare la chiodatrice P560 per fissare i CTF. Serve un patentino?"></textarea>
      <div class="controls">
        <button onclick="ask()">Chiedi</button>
        <button class="sec" onclick="sample(1)">Esempio P560</button>
        <button class="sec" onclick="sample(2)">CTF vs Diapason</button>
        <button class="sec" onclick="sample(3)">Densità CTF</button>
        <span class="badge" id="prov"></span>
      </div>
    </div>

    <div class="card">
      <div id="answer" class="out">Risposta…</div>
    </div>

    <footer>
      <div class="row">
        <div class="small">Endpoint: <kbd>/ping</kbd> <kbd>/health</kbd> <kbd>/api/ask</kbd></div>
      </div>
    </footer>
  </div>

<script>
async function ask() {{
  const t0 = performance.now();
  const q = document.getElementById('q').value || '';
  const prov = document.getElementById('prov');
  prov.textContent = 'Invio…';
  try {{
    const r = await fetch('/api/ask', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ q }})
    }});
    const j = await r.json();
    document.getElementById('answer').textContent = j.answer || JSON.stringify(j, null, 2);
  }} catch (e) {{
    document.getElementById('answer').textContent = 'Errore di rete: ' + e;
  }} finally {{
    const t1 = performance.now();
    prov.textContent = 'OK (' + Math.round(t1 - t0) + 'ms)';
  }}
}}
function sample(k) {{
  if (k===1) document.getElementById('q').value = 'Devo usare la chiodatrice P560 per fissare i CTF. Serve un patentino?';
  if (k===2) document.getElementById('q').value = 'Che differenza c’è tra connettore CTF e sistema Diapason? Quando conviene usare uno o l’altro?';
  if (k===3) document.getElementById('q').value = 'Devo utilizzare i connettori CTF su lamiera Hi-Bond da 1 mm: quanti connettori servono a m² e come avviene il fissaggio con P560?';
  document.getElementById('answer').textContent = 'Risposta…';
}}
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(HOME_HTML)
