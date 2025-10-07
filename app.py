# app.py – Tecnaria QA Bot (WEB-first IT, snippet/EN backfill, filtro P560 forte, fallback Sinapsi)
import os, json, re, html, time
from typing import List, Dict, Any, Optional
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ------------------------------ UTIL ------------------------------
def getenv_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None: return default
    return str(v).strip().lower() in {"1","true","yes","y","on"}

def getenv_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None: return default
    try: return int(v)
    except: return default

# ------------------------------ CONFIG ----------------------------
APP_NAME = "Tecnaria – Assistente Tecnico"

BRAVE_API_KEYS = [k.strip() for k in os.getenv("BRAVE_API_KEY","").split(",") if k.strip()]
PREFERRED_DOMAINS = [d.strip() for d in os.getenv("PREFERRED_DOMAINS","tecnaria.com,www.tecnaria.com").split(",") if d.strip()]
LANG_PREFERRED = os.getenv("LANG_PREFERRED","it").lower()

ACCEPT_EN_BACKFILL = getenv_bool("ACCEPT_EN_BACKFILL", True)
USE_SNIPPET_BACKFILL = getenv_bool("USE_SNIPPET_BACKFILL", True)

MIN_WEB_OK_CHARS = getenv_int("MIN_WEB_OK_CHARS", 80)
MIN_WEB_OK_SENTENCES = getenv_int("MIN_WEB_OK_SENTENCES", 1)

MAX_ANSWER_CHARS = getenv_int("MAX_ANSWER_CHARS", 2000)

SOURCES_MAX = getenv_int("SOURCES_MAX", 2)
SOURCES_SHOW_SNIPPETS = getenv_bool("SOURCES_SHOW_SNIPPETS", False)
SOURCES_COLLAPSED = getenv_bool("SOURCES_COLLAPSED", True)

SINAPSI_FILE = os.getenv("SINAPSI_FILE","static/data/sinapsi_rules.json")
SINAPSI_MODE = os.getenv("SINAPSI_MODE","assist").lower()     # off | assist | strict
ALLOW_SINAPSI_OVERRIDE = getenv_bool("ALLOW_SINAPSI_OVERRIDE", False)

DISAMBIG_STRICT = getenv_bool("DISAMBIG_STRICT", True)
WEB_RESULTS_COUNT_PREFERRED = getenv_int("WEB_RESULTS_COUNT_PREFERRED", 10)
REFINE_ALWAYS = getenv_bool("REFINE_ALWAYS", False)
DEBUG = getenv_bool("DEBUG", False)

EXCLUDE_ANY_Q = [r"\bprezz\w*", r"\bcost\w*", r"\bpreventiv\w*", r"\boffert\w*"]

# ------------------------------ SINAPSI ---------------------------
def load_sinapsi_rules(path: str) -> List[Dict[str,Any]]:
    try:
        with open(path,"r",encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "rules" in data: return data["rules"]
            if isinstance(data, list): return data
    except Exception as e:
        if DEBUG: print("Sinapsi load error:", e)
    return []

SINAPSI_RULES = load_sinapsi_rules(SINAPSI_FILE)

def sinapsi_assist(q: str) -> Optional[str]:
    ql = q.lower()

    if "p560" in ql and ("patentino" in ql or "licenza" in ql or "autorizz"):
        return ("Per la chiodatrice SPIT P560 non serve alcun patentino né autorizzazioni speciali: "
                "è a tiro indiretto (classe A) con propulsori a salve; restano obbligatori i DPI "
                "e l’uso conforme al manuale.")

    if "p560" in ql:
        return ("SPIT P560: chiodatrice a tiro indiretto per posa a freddo di connettori Tecnaria "
                "(CTF/Diapason) su travi in acciaio, anche con lamiera grecata. "
                "Uso dall’alto, produttività elevata, kit/adattatori dedicati. "
                "DPI obbligatori e taratura propulsori in funzione degli spessori.")

    if "ctf" in ql and ("m2" in ql or "mq" in ql or "al m" in ql):
        return ("La quantità di connettori CTF deriva dal calcolo (luci, carichi, profilo lamiera, spessore soletta). "
                "Ordine di grandezza: ~6–8 CTF/m², più fitti agli appoggi.")

    if ("ctf" in ql and "diapason" in ql) or ("differenza" in ql and ("ctf" in ql or "diapason" in ql)):
        return ("CTF: solai su travi in acciaio con lamiera grecata (posa a sparo). "
                "Diapason: laterocemento senza lamiera (fissaggi nei travetti; getto dall’alto). "
                "La scelta dipende dal tipo di solaio.")
    # fallback rules
    for r in SINAPSI_RULES:
        try:
            kws = [k.lower() for k in r.get("keywords",[])]
            if kws and all(k in ql for k in kws):
                s = (r.get("answer_short") or r.get("answer") or "").strip()
                if s: return s
        except: pass
    return None

# ------------------------------ BRAVE --------------------------------
def brave_headers() -> Dict[str,str]:
    key = BRAVE_API_KEYS[0] if BRAVE_API_KEYS else ""
    return {"Accept":"application/json","X-Subscription-Token":key} if key else {}

def make_query(q: str) -> str:
    site_filter = " OR ".join([f"site:{d}" for d in PREFERRED_DOMAINS])
    return f"({q}) ({site_filter}) lang:it OR lang:en"

def search_brave_json(q: str, count: int = 10) -> Dict[str,Any]:
    if not BRAVE_API_KEYS: return {}
    url = "https://api.search.brave.com/res/v1/web/search"
    params = {"q": make_query(q), "count": count}
    try:
        r = requests.get(url, headers=brave_headers(), params=params, timeout=12)
        if r.status_code == 200: return r.json()
    except Exception as e:
        if DEBUG: print("Brave error:", e)
    return {}

def looks_italian(s: str) -> bool:
    s2 = f" {s.lower()} "
    for w in [" il "," la "," lo "," gli "," delle "," degli "," che "," con "," per "," su "," tra "," in "," non "," è "," sono "]:
        if w in s2: return True
    return False

def fetch_html(url: str, timeout: int = 10) -> str:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code == 200 and "text/html" in r.headers.get("Content-Type",""):
            return r.text
    except: pass
    return ""

def meta_description(markup: str) -> Optional[str]:
    try:
        soup = BeautifulSoup(markup, "html.parser")
        tag = soup.find("meta", attrs={"name":"description"})
        if tag and tag.get("content"):
            return tag["content"].strip()
    except: pass
    return None

def html_to_text(markup: str) -> str:
    soup = BeautifulSoup(markup, "html.parser")
    for tag in soup(["script","style","nav","footer","header"]): tag.decompose()
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+"," ",text).strip()

def split_sentences(text: str) -> List[str]:
    s = re.split(r"(?<=[\.\!\?])\s+", text)
    return [x.strip() for x in s if len(x.strip())>=8]

def first_paragraph(text: str, want_it: bool, min_sent: int, max_chars: int) -> Optional[str]:
    sents = split_sentences(text)
    bucket: List[str] = []
    for st in sents:
        if want_it and not looks_italian(st):
            continue
        bucket.append(st)
        if len(bucket)>=min_sent and sum(len(x)+1 for x in bucket)>=MIN_WEB_OK_CHARS:
            break
    if not bucket: return None
    para = " ".join(bucket)
    return para[:max_chars].strip()

_P560_RE = re.compile(r"\bp\s*560\b", flags=re.I)

def is_preferred_domain(url: str) -> bool:
    host = re.sub(r"^https?://","",url).split("/")[0]
    return any(host.endswith(d) or host==d for d in PREFERRED_DOMAINS)

def pick_results(payload: Dict[str,Any], q: str) -> List[Dict[str,str]]:
    out: List[Dict[str,str]] = []
    web = payload.get("web",{})
    results = web.get("results",[])
    ql = q.lower()
    force_p560 = ("p560" in ql)
    force_ctf = ("ctf" in ql)
    force_diap = ("diapason" in ql)

    for item in results:
        url = item.get("url","")
        title = item.get("title") or url
        snippet = item.get("description") or ""
        if not url or not is_preferred_domain(url): 
            continue

        low = (title+" "+url).lower()

        # filtro "ordini" se non pertinente
        if "ordini.tecnaria.com" in url and not _P560_RE.search(low):
            continue

        # filtro forte P560: titolo o path devono matchare p560
        if force_p560 and not _P560_RE.search(low):
            continue

        if force_ctf and ("ctf" not in low) and not force_p560:
            continue
        if force_diap and ("diapason" not in low) and not force_p560:
            continue

        out.append({"url":url,"title":title,"snippet":snippet})
    return out

def unique_sources(items: List[Dict[str,str]], limit: int) -> List[Dict[str,str]]:
    seen = set(); out=[]
    for it in items:
        host = re.sub(r"^https?://","",it["url"]).split("/")[0]
        key = host + "|" + it["title"]
        if key in seen: continue
        seen.add(key)
        out.append(it)
        if len(out)>=limit: break
    return out

# ------------------------------ ANSWER --------------------------------
def clean_text(s: str) -> str:
    return re.sub(r"\s+"," ", s.replace("\u200b","")).strip()

def compose_answer(q: str, web_paras: List[str], assist: Optional[str], snippets_it: List[str], snippets_en: List[str], strong_match: bool) -> str:
    ql = q.lower()

    if "p560" in ql and ("patentino" in ql or "licenza" in ql or "autorizz"):
        return ("Per la chiodatrice SPIT P560 non serve alcun patentino né autorizzazioni speciali: "
                "è a tiro indiretto (classe A) con propulsori a salve; restano obbligatori i DPI e il rispetto del manuale.")

    if "p560" in ql:
        if web_paras: return web_paras[0][:MAX_ANSWER_CHARS]
        if snippets_it: return snippets_it[0][:MAX_ANSWER_CHARS]
        if ACCEPT_EN_BACKFILL and snippets_en: return snippets_en[0][:MAX_ANSWER_CHARS]
        if assist: return assist
        # Se il match è forte (es. URL P560 trovato) ma non è emerso testo → fallback descrittivo
        if strong_match:
            return ("SPIT P560: chiodatrice a tiro indiretto per fissaggio a freddo dei connettori Tecnaria "
                    "su travi in acciaio, anche con lamiera grecata. DPI obbligatori; taratura propulsori in base agli spessori.")
    if ("ctf" in ql) and ("m2" in ql or "mq" in ql or "al m" in ql):
        return ("La quantità di connettori CTF deriva dal calcolo strutturale (luci, carichi, profilo lamiera, spessore soletta). "
                "Ordine di grandezza: circa 6–8 CTF/m², più fitti agli appoggi.")

    if ("ctf" in ql and "diapason" in ql) or ("differenza" in ql and ("ctf" in ql or "diapason" in ql)):
        return ("CTF: solai su travi in acciaio con lamiera grecata (posa a sparo). "
                "Diapason: laterocemento senza lamiera (fissaggi nei travetti; getto dall’alto). "
                "La scelta dipende dal tipo di solaio.")

    if web_paras: return web_paras[0][:MAX_ANSWER_CHARS]
    if snippets_it: return snippets_it[0][:MAX_ANSWER_CHARS]
    if ACCEPT_EN_BACKFILL and snippets_en: return snippets_en[0][:MAX_ANSWER_CHARS]
    if assist: return assist
    return "Non ho trovato contenuti sufficienti su fonti Tecnaria. Prova a riformulare la domanda."

def render_sources(sources: List[Dict[str,str]]) -> str:
    if not sources: return ""
    lis=[]
    for i,s in enumerate(sources,1):
        t = html.escape(s.get("title") or s.get("url") or f"Fonte {i}")
        u = html.escape(s.get("url") or "#")
        item = f'<li><a href="{u}" target="_blank" rel="noopener">{t}</a></li>'
        if SOURCES_SHOW_SNIPPETS:
            sn = s.get("snippet","")
            if sn: item += "<br><small>"+html.escape(sn)+"</small>"
        lis.append(item)
    ol = "<ol class='list-decimal pl-5'>"+"".join(lis)+"</ol>"
    if SOURCES_COLLAPSED:
        return "<details><summary><strong>Fonti</strong></summary><div style='margin:.5rem 0'><button type='button' onclick=\"this.closest('details').removeAttribute('open')\">Chiudi fonti</button></div>"+ol+"</details>"
    return "<h3>Fonti</h3>"+ol

def build_nav() -> str:
    return "<div class='nav'><button class='btn' onclick=\"history.back()\">⬅ Torna indietro</button> <a class='btn' href='/'>Home</a></div>"

def blocked_by_exclude(q: str) -> bool:
    for pat in EXCLUDE_ANY_Q:
        if re.search(pat, q, flags=re.I): return True
    return False

# ------------------------------ FASTAPI UI ---------------------------
JS_APP = """
async function ev(e){
  e.preventDefault();
  const q = (document.getElementById('q').value||'').trim();
  if(!q) return;
  const btn = document.querySelector('#f button[type="submit"]'); btn.disabled = true;
  try{
    const r = await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q})});
    const j = await r.json();
    document.getElementById('out').innerHTML = j.html || '<div class="card"><p>Nessuna risposta.</p></div>';
  }catch(err){
    document.getElementById('out').innerHTML = '<div class="card"><p>Errore di rete.</p></div>';
    console.error(err);
  }finally{ btn.disabled = false; }
}
document.addEventListener('DOMContentLoaded',()=>{ const f=document.getElementById('f'); if(f) f.addEventListener('submit',ev);});
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
details summary{cursor:pointer;font-weight:600}
.kbd.sugg{display:inline-block;background:#eef6ef;border:1px solid #cfe4d2;padding:6px 10px;border-radius:20px;margin-right:8px}
.small{font-size:12px;color:#556}
"""

app = FastAPI(title=APP_NAME)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/", response_class=HTMLResponse)
def index():
    page = f"""<!doctype html><html lang="it"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(APP_NAME)}</title><style>{CSS_APP}</style></head><body>
<div class="container">
  <div class="topbar"><div class="brand">TECNARIA</div><h1>{html.escape(APP_NAME)}</h1></div>
  <form id="f"><input id="q" name="q" placeholder="Fai una domanda (es. Serve il patentino per la P560?)"/><button type="submit" class="btn">Chiedi</button></form>
  <div style="margin:6px 0 12px"><span class="kbd sugg">Differenza CTF e Diapason</span><span class="kbd sugg">Quanti CTF al m²</span><span class="kbd sugg">Serve il patentino per la P560?</span></div>
  <div id="out" class="card"></div>
  <p class="small">© Tecnaria S.p.A. – Questo assistente sintetizza contenuti ufficiali e regole Sinapsi.</p>
</div><script>{JS_APP}</script></body></html>"""
    return HTMLResponse(page)

@app.get("/health")
def health():
    return JSONResponse({
        "status":"ok",
        "web_enabled": bool(BRAVE_API_KEYS),
        "preferred_domains": PREFERRED_DOMAINS,
        "rules_loaded": len(SINAPSI_RULES),
        "exclude_any_q": EXCLUDE_ANY_Q,
        "sinapsi_file": SINAPSI_FILE,
        "lang_preferred": LANG_PREFERRED,
        "disambig_strict": DISAMBIG_STRICT,
        "answer_mode":"full",
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
        "app": "web->fetch_tecnaria->(assist)->render"
    })

# ------------------------------ /api/ask -------------------------------------
def collect_backfills(results: List[Dict[str,str]]) -> (List[str], List[str]):
    it_snips, en_snips = [], []
    for r in results:
        sn = (r.get("snippet") or "").strip()
        if not sn: continue
        if looks_italian(sn): it_snips.append(sn)
        else: en_snips.append(sn)
    return it_snips, en_snips

@app.post("/api/ask")
async def api_ask(payload: Dict[str,Any]):
    t0 = time.time()
    q = clean_text(str(payload.get("q","")))
    if not q:
        return JSONResponse({"ok":True,"html":"<div class='card'><p>Scrivi una domanda.</p></div>"})
    if blocked_by_exclude(q):
        return JSONResponse({"ok":True,"html":"<div class='card'><p>Per preventivi, prezzi o offerte rivolgersi al canale commerciale.</p></div>"})

    sources: List[Dict[str,str]] = []
    web_paragraphs: List[str] = []
    it_snippets: List[str] = []
    en_snippets: List[str] = []
    strong_match = False

    if BRAVE_API_KEYS:
        payload_search = search_brave_json(q, count=WEB_RESULTS_COUNT_PREFERRED)
        picked = pick_results(payload_search, q)
        strong_match = any(_P560_RE.search((p.get("title","")+p.get("url","")).lower()) for p in picked) if "p560" in q.lower() else False
        sources = unique_sources(picked, SOURCES_MAX)

        # backfill snippets
        it_snippets, en_snippets = collect_backfills(picked)

        # fetch pagine + meta description
        for it in sources:
            if len(web_paragraphs) >= 2: break
            raw = fetch_html(it["url"])
            if not raw: continue

            # meta description prima
            md = meta_description(raw)
            if md and looks_italian(md) and len(md) >= 60:
                web_paragraphs.append(md[:600])
                continue

            text = html_to_text(raw)
            para = first_paragraph(text, want_it=True, min_sent=MIN_WEB_OK_SENTENCES, max_chars=600)
            if not para and ACCEPT_EN_BACKFILL:
                para = first_paragraph(text, want_it=False, min_sent=1, max_chars=600)
            if para: web_paragraphs.append(para)

    assist_line = sinapsi_assist(q) if SINAPSI_MODE in ("assist","strict") else None
    answer = compose_answer(q, web_paragraphs, assist_line, it_snippets, en_snippets, strong_match).strip()

    if len(answer) > MAX_ANSWER_CHARS:
        answer = answer[:MAX_ANSWER_CHARS].rsplit(" ",1)[0] + "…"

    nav = build_nav()
    src_html = render_sources(sources)
    dt = int((time.time()-t0)*1000)

    html_card = f"<div class='card'><h2>Risposta Tecnaria</h2>{nav}<p>{html.escape(answer)}</p>{src_html}{nav}<p><small>⏱ {dt} ms</small></p></div>"
    return JSONResponse({"ok":True,"html":html_card})

# ------------------------------ MAIN (local) ---------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
