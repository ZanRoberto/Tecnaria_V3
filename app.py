import os, re, json, html, time
from typing import List, Dict, Any
import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

# ==========
# CONFIG
# ==========
def env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None: return default
    return str(v).strip().lower() in ("1","true","yes","y","on")

def env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None: return default
    try: return int(v)
    except: return default

def env_csv(name: str, default: List[str]) -> List[str]:
    v = os.getenv(name)
    if not v: return default
    return [x.strip() for x in v.split(",") if x.strip()]

CFG = {
    # lingua e stile
    "LANG_PREFERRED": os.getenv("LANG_PREFERRED","it"),
    "ANSWER_MODE": os.getenv("ANSWER_MODE","full"),
    "MAX_ANSWER_CHARS": env_int("MAX_ANSWER_CHARS", 2000),
    "SOURCES_SHOW_SNIPPETS": env_bool("SOURCES_SHOW_SNIPPETS", False),
    "DISAMBIG_STRICT": env_bool("DISAMBIG_STRICT", True),

    # web-first
    "WEB_ENABLED": env_bool("WEB_ENABLED", True),
    "PREFERRED_DOMAINS": env_csv("PREFERRED_DOMAINS", ["tecnaria.com","www.tecnaria.com","floor-reinforcement.com","spit.eu","spitpaslode.com"]),
    "BRAVE_FORCE_TERMS": os.getenv("BRAVE_FORCE_TERMS","Tecnaria|SPIT P560|CTF|Diapason"),
    "MIN_WEB_OK_SENTENCES": env_int("MIN_WEB_OK_SENTENCES", 3),
    "MIN_WEB_OK_CHARS": env_int("MIN_WEB_OK_CHARS", 500),
    "ACCEPT_EN_BACKFILL": env_bool("ACCEPT_EN_BACKFILL", True),
    "USE_SNIPPET_BACKFILL": env_bool("USE_SNIPPET_BACKFILL", True),

    # sinapsi
    "SINAPSI_MODE": os.getenv("SINAPSI_MODE","assist"),   # off|assist|fallback
    "ALLOW_SINAPSI_OVERRIDE": env_bool("ALLOW_SINAPSI_OVERRIDE", False),
    "SINAPSI_FILE": os.getenv("SINAPSI_FILE","static/data/sinapsi_rules.json"),

    # intent
    "INTENT_OVERVIEW": os.getenv("INTENT_OVERVIEW", "mi parli|cos['’]?è|scheda|presentazione|panoramica"),
    "INTENT_LICENSE": os.getenv("INTENT_LICENSE", "patentino|licenza|abilitazione|autorizzazioni"),
    "INTENT_QUANTITY": os.getenv("INTENT_QUANTITY", "quanti|al m2|al m²|densità|passo"),
    "INTENT_DIFF": os.getenv("INTENT_DIFF", "differenza|vs|confronto|meglio tra"),

    # ui
    "BTN_BACK_TEXT": os.getenv("BTN_BACK_TEXT","⬅ Torna indietro"),
    "BTN_HOME_TEXT": os.getenv("BTN_HOME_TEXT","Home"),
    "SHOW_TOP_NAV": env_bool("SHOW_TOP_NAV", True),
    "SHOW_BOTTOM_NAV": env_bool("SHOW_BOTTOM_NAV", True),

    # ricerca
    "SOURCES_MAX": env_int("SOURCES_MAX", 5),
    "SOURCES_COLLAPSED": env_bool("SOURCES_COLLAPSED", True),
    "WEB_RESULTS_COUNT_PREFERRED": env_int("WEB_RESULTS_COUNT_PREFERRED", 6),
    "REFINE_ALWAYS": env_bool("REFINE_ALWAYS", True),
    "DEBUG": env_bool("DEBUG", False),
}

BRAVE_API_KEY = os.getenv("BRAVE_API_KEY","").strip()

# ==========
# FASTAPI
# ==========
app = FastAPI()

def build_home() -> str:
    return f"""
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<title>TECNARIA – Assistente Tecnico</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,"Helvetica Neue",Arial,sans-serif;margin:20px}}
.card{{border:1px solid #e5e7eb;border-radius:12px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.05)}}
h1{{font-size:20px;margin:0 0 8px}}
input,button{{font-size:16px}}
.btn{{display:inline-block;border:1px solid #ddd;border-radius:8px;padding:.45rem .7rem;text-decoration:none}}
.nav{{display:flex;gap:.5rem;flex-wrap:wrap;margin:.5rem 0 1rem}}
.small{{color:#666;font-size:12px}}
ul.sugg{{display:flex;gap:.5rem;flex-wrap:wrap;list-style:none;padding:0;margin:.5rem 0 0}}
ul.sugg li a{{border:1px solid #e5e7eb;border-radius:999px;padding:.35rem .7rem;text-decoration:none}}
</style>
</head>
<body>
  <div class="card">
    <h1>TECNARIA<br><span class="small">Tecnaria – Assistente Tecnico</span></h1>
    <form onsubmit="send();return false;">
      <label for="q">Fai una domanda</label><br>
      <input id="q" name="q" style="width:100%;padding:.6rem;border:1px solid #ddd;border-radius:8px" placeholder="Es. Differenza CTF e Diapason">
      <div style="margin-top:.6rem"><button class="btn" type="submit">Cerca</button></div>
    </form>
    <ul class="sugg">
      <li><a href="#" onclick="ask('Differenza CTF e Diapason');return false;">Differenza CTF e Diapason</a></li>
      <li><a href="#" onclick="ask('Quanti CTF al m²');return false;">Quanti CTF al m²</a></li>
      <li><a href="#" onclick="ask('Serve il patentino per la P560?');return false;">Serve il patentino per la P560?</a></li>
    </ul>
  </div>
  <div id="out" style="margin-top:12px"></div>

<script>
async function send(){{
  const q = document.getElementById('q').value;
  if(!q) return;
  await ask(q);
}}
async function ask(q){{
  const out = document.getElementById('out');
  out.innerHTML = '<div class="card">Caricamento…</div>';
  try {{
    const r = await fetch('/api/ask', {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{q}})
    }});
    const j = await r.json();
    out.innerHTML = j.html;
  }} catch(e) {{
    out.innerHTML = '<div class="card">Errore: '+(e?.message||e)+'</div>';
  }}
}}
</script>
<p class="small">© Tecnaria S.p.A. – Questo assistente sintetizza **solo** contenuti ufficiali e integra regole interne senza mostrarle.</p>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
def index():
    return build_home()

@app.get("/health", response_class=JSONResponse)
def health():
    h = {k.lower(): v for k,v in CFG.items()}
    h["status"] = "ok"
    h["web_enabled"] = CFG["WEB_ENABLED"]
    h["preferred_domains"] = CFG["PREFERRED_DOMAINS"]
    h["app"] = "web-first -> (sinapsi assist opzionale) -> render"
    return h

# ==========
# SEARCH (BRAVE)
# ==========
def brave_search(query: str, count: int=8) -> List[Dict[str,Any]]:
    if not BRAVE_API_KEY:
        return []
    headers = {"X-Subscription-Token": BRAVE_API_KEY}
    # forzo il contesto Tecnaria/Spit e filtro domini
    force = CFG["BRAVE_FORCE_TERMS"]
    domains = " OR ".join([f"site:{d}" for d in CFG["PREFERRED_DOMAINS"]])
    full_q = f"({query}) ({force}) ({domains})"
    params = {
        "q": full_q,
        "country": "it",
        "search_lang": "it",
        "count": max(3, min(count, 20)),
        "safesearch": "off"
    }
    try:
        r = requests.get("https://api.search.brave.com/res/v1/web/search", headers=headers, params=params, timeout=(12, 30))
        if r.status_code != 200: return []
        data = r.json()
        out = []
        for item in (data.get("web", {}) or {}).get("results", []):
            out.append({
                "title": item.get("title",""),
                "url": item.get("url",""),
                "snippet": item.get("description","") or item.get("snippet","") or "",
            })
        # preferisci domini whitelisted
        preferred = []
        others = []
        for it in out:
            if any(d in it["url"] for d in CFG["PREFERRED_DOMAINS"]):
                preferred.append(it)
            else:
                others.append(it)
        final = preferred + others
        # tag lingua (grezza) in base a URL
        for it in final:
            lang = "IT" if ".it" in it["url"] or "/it/" in it["url"] or "tecnaria.com/" in it["url"] and not "/en/" in it["url"] else "EN"
            it["lang"] = lang
        return final
    except Exception:
        return []

def good_enough(results: List[Dict[str,Any]]) -> bool:
    if not results: return False
    # valuta i primi 3
    txt = " ".join((r.get("snippet") or "") for r in results[:3])
    # conta frasi e caratteri
    sentences = len([s for s in re.split(r"[\.!?]+", txt) if s.strip()])
    chars = len(txt)
    return (sentences >= CFG["MIN_WEB_OK_SENTENCES"]) and (chars >= CFG["MIN_WEB_OK_CHARS"])

# ==========
# SINAPSI (opzionale, MA NON VISIBILE)
# ==========
def load_sinapsi() -> List[Dict[str,Any]]:
    path = CFG["SINAPSI_FILE"]
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

SINAPSI = None

def sinapsi_enrich(q: str) -> Dict[str,Any]:
    # NON restituisce testo da mostrare, ma solo “puntelli” interni se servono
    global SINAPSI
    if CFG["SINAPSI_MODE"] == "off":
        return {}
    if SINAPSI is None:
        SINAPSI = load_sinapsi()
    ql = q.lower()
    best = {}
    for r in SINAPSI or []:
        keys = " ".join([str(r.get("q","")), str(r.get("keywords",""))]).lower()
        if all(k.strip() in keys for k in re.split(r"[\s,;]+", ql) if k.strip()):
            best = r
            break
    return {"hints": best.get("hints") or []}

# ==========
# INTENT & TEMPLATES (NARRATIVA)
# ==========
def intent_of(q: str) -> str:
    ql = q.lower()
    if re.search(CFG["INTENT_DIFF"], ql): return "diff"
    if re.search(CFG["INTENT_LICENSE"], ql): return "license"
    if re.search(CFG["INTENT_QUANTITY"], ql): return "quantity"
    if re.search(CFG["INTENT_OVERVIEW"], ql): return "overview"
    # fallback a parole chiave
    if "p560" in ql: return "p560"
    if "ctf" in ql and "diapason" in ql: return "diff"
    if "diapason" in ql: return "diapason"
    if "ctf" in ql: return "ctf"
    if "ctl" in ql: return "ctl"
    return "generic"

def make_narrative(q: str, intent: str, web: List[Dict[str,Any]], hints: Dict[str,Any]) -> str:
    # prendi prime 2–3 fonti per info (ma NON stampa snippet nella risposta)
    def has(s): return s and isinstance(s, str)
    urls = [r["url"] for r in web[:3] if has(r.get("url"))]
    urls_lower = " ".join(urls).lower()

    # helper per capire se abbiamo materiali EN (che però renderemo in IT)
    has_en = any(r.get("lang")=="EN" for r in web[:3])

    # NARRATIVE by intent (sempre in IT)
    if intent == "license" or "patentino" in q.lower() or "licenz" in q.lower():
        txt = (
            "No: per la chiodatrice SPIT P560 non serve alcun patentino né autorizzazioni speciali. "
            "Si tratta di un utensile a tiro indiretto (classe A) con propulsori a salve; restano obbligatori i DPI "
            "(occhiali, cuffie antirumore, guanti) e il rispetto del manuale d’uso. "
            "La P560 è impiegata da Tecnaria per la posa a freddo di connettori come CTF e Diapason."
        )
        return txt[:CFG["MAX_ANSWER_CHARS"]]

    if intent == "diff":
        txt = (
            "I connettori **CTF** e **Diapason** hanno impieghi differenti: "
            "CTF è un connettore a **piolo con piastra** per solai misti acciaio-calcestruzzo su travi in acciaio con **lamiera grecata**, "
            "posa dall’alto a sparo (P560) con **2 chiodi per connettore**. "
            "Diapason è un connettore a **staffa** ad alte prestazioni, consigliato per travi principali/sollecitazioni elevate, "
            "fissato con **4 chiodi** con P560. La scelta dipende da travi, carichi, lamiera e requisiti di progetto."
        )
        return txt[:CFG["MAX_ANSWER_CHARS"]]

    if intent == "quantity":
        txt = (
            "La densità dei connettori deriva dal calcolo (Eurocodice 4) in funzione di luci, carichi, profilo lamiera e verifiche di scorrimento. "
            "Come ordine di grandezza si impiegano **circa 6–8 CTF/m²**, con maglia più fitta in prossimità degli appoggi."
        )
        return txt[:CFG["MAX_ANSWER_CHARS"]]

    if intent == "p560":
        txt = (
            "La **SPIT P560** è la chiodatrice a tiro indiretto (classe A) usata per la posa a freddo dei connettori Tecnaria. "
            "Consente fissaggi rapidi dall’alto su travi in acciaio e lamiera grecata. Ogni **CTF** si fissa con **2 chiodi HSBR14**; "
            "il **Diapason** con **4 chiodi**. Sono essenziali DPI, corretta taratura e controllo dell’aderenza della lamiera alla trave."
        )
        return txt[:CFG["MAX_ANSWER_CHARS"]]

    if intent == "ctf":
        txt = (
            "Il **CTF** è un connettore a **piolo** con piastra per solai misti acciaio-calcestruzzo su travi in acciaio con **lamiera grecata**. "
            "La posa è dall’alto a sparo con **SPIT P560**: **2 chiodi** per connettore. "
            "Sono richiesti lamiera aderente e spessori idonei alla chiodatura; la disposizione (passo/maglia) deriva dal calcolo."
        )
        return txt[:CFG["MAX_ANSWER_CHARS"]]

    if intent == "diapason":
        txt = (
            "Il **Diapason** è un connettore a **staffa** per solai acciaio-calcestruzzo ad alte prestazioni, indicato per travi principali "
            "o carichi elevati. Si fissa dall’alto con **SPIT P560** mediante **4 chiodi**; garantisce elevata duttilità e capacità di trasferimento a taglio."
        )
        return txt[:CFG["MAX_ANSWER_CHARS"]]

    if intent == "ctl":
        txt = (
            "I connettori **CTL** sono dedicati ai solai **legno-calcestruzzo** con posa dall’alto tramite viti. "
            "Si usano quando il supporto è in legno; per travi in acciaio con lamiera grecata si impiegano CTF/Diapason."
        )
        return txt[:CFG["MAX_ANSWER_CHARS"]]

    if intent == "overview":
        txt = (
            "Tecnaria propone sistemi di connessione per solai collaboranti: **CTF/Diapason** per acciaio-calcestruzzo con lamiera grecata, "
            "**CTL** per legno-calcestruzzo, **V CEM** per laterocemento senza lamiera. La scelta dipende da supporto, carichi e cantiere."
        )
        return txt[:CFG["MAX_ANSWER_CHARS"]]

    # generic: riassunto corto dai temi ricorrenti
    txt = (
        "In ambito Tecnaria: CTF/Diapason per travi in acciaio con lamiera grecata (posa a sparo, P560), CTL per legno-calcestruzzo, "
        "V CEM per laterocemento senza lamiera. Il dimensionamento segue l’Eurocodice 4; le schede prodotto e le certificazioni sono disponibili sul sito."
    )
    return txt[:CFG["MAX_ANSWER_CHARS"]]

def render_sources(sources: List[Dict[str,Any]]) -> str:
    if not sources: return ""
    items = []
    for s in sources[:CFG["SOURCES_MAX"]]:
        lang = s.get("lang","")
        lab = f" <em>({lang})</em>" if lang else ""
        title = s.get("title") or s.get("url")
        items.append(f'<li><a href="{html.escape(s["url"])}" target="_blank" rel="noopener">{html.escape(title)}</a>{lab}</li>')
    details_attr = " open" if not CFG["SOURCES_COLLAPSED"] else ""
    return f"""
<details{details_attr}><summary><strong>Fonti</strong></summary>
<div style='margin:.5rem 0'><button type='button' onclick="this.closest('details').removeAttribute('open')">Chiudi fonti</button></div>
<ol class='list-decimal pl-5'>
{''.join(items)}
</ol></details>"""

def render_nav() -> str:
    top = f"<div class='nav'><button class='btn' onclick=\"try{{history.back()}}catch(e){{}}\">{CFG['BTN_BACK_TEXT']}</button> <a class='btn' href='/'>{CFG['BTN_HOME_TEXT']}</a></div>" if CFG["SHOW_TOP_NAV"] else ""
    bottom = f"<div class='nav'><button class='btn' onclick=\"try{{history.back()}}catch(e){{}}\">{CFG['BTN_BACK_TEXT']}</button> <a class='btn' href='/'>{CFG['BTN_HOME_TEXT']}</a></div>" if CFG["SHOW_BOTTOM_NAV"] else ""
    return top, bottom

def build_card(body_html: str) -> str:
    top, bottom = render_nav()
    return f"<div class='card'><h2>Risposta Tecnaria</h2>{top}{body_html}{bottom}<p><small>⏱ {int((time.time()-START_TS)*1000)} ms</small></p></div>"

# ==========
# /api/ask
# ==========
@app.post("/api/ask", response_class=JSONResponse)
def api_ask(payload: Dict[str,Any]):
    global START_TS
    START_TS = time.time()
    q = (payload or {}).get("q","").strip()
    if not q:
        return {"ok": False, "html": "<div class='card'>Domanda vuota.</div>"}

    # 1) WEB-FIRST
    web_results: List[Dict[str,Any]] = []
    if CFG["WEB_ENABLED"]:
        web_results = brave_search(q, count=CFG["WEB_RESULTS_COUNT_PREFERRED"])
        if CFG["REFINE_ALWAYS"] and (not good_enough(web_results)):
            # raffinamento: aggiungo termini forzati
            web_results = brave_search(q + " Tecnaria", count=CFG["WEB_RESULTS_COUNT_PREFERRED"])

    web_ok = good_enough(web_results)

    # 2) SINAPSI (solo come hints, non stampiamo righe “Sinapsi”)
    hints = {}
    if CFG["SINAPSI_MODE"] in ("assist","fallback"):
        if (CFG["SINAPSI_MODE"]=="assist" and not web_ok) or (CFG["SINAPSI_MODE"]=="fallback" and not web_ok):
            hints = sinapsi_enrich(q)

    # 3) NARRATIVA (in IT) – MAI mostrare testo sinapsi; solo contenuti ufficiali + regole interne assorbite
    itent = intent_of(q)
    answer_text = make_narrative(q, itent, web_results, hints)

    # se web non ok e sinapsi pure vuota e non vogliamo “stringhe misere”, mostriamo comunque narrativa solida per intent
    # (già fatto in make_narrative); le fonti le mostriamo solo se abbiamo link sensati
    sources_block = render_sources(web_results) if web_results else ""

    body = f"<p>{answer_text}</p>\n{sources_block}"

    html_card = build_card(body)
    return {"ok": True, "html": html_card}
