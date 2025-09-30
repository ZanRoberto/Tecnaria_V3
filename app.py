import os, time, re, json, html, textwrap
from typing import List, Dict, Tuple, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from openai import OpenAI

# =========================
# ENV & CONFIG
# =========================
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or ""
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata.")

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.0")
OPENAI_MODEL_COMPAT = os.environ.get("OPENAI_MODEL_COMPAT", OPENAI_MODEL)  # per chat.completions
WEB_FIRST = os.environ.get("WEB_FIRST", "1") == "1"   # 1=web prima, 0=solo locale
MAX_RESULTS = int(os.environ.get("WEB_MAX_RESULTS", "5"))       # risultati ricerca
MAX_PAGES = int(os.environ.get("WEB_MAX_PAGES", "3"))           # pagine da aprire
FETCH_TIMEOUT = float(os.environ.get("WEB_FETCH_TIMEOUT", "8")) # sec per pagina
SAFE_DOMAINS = os.environ.get("WEB_SAFE_DOMAINS", "")           # es: "tecnaria.com,spitpaslode.it"
# Provider opzionali (usa quello disponibile)
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
SERPAPI_API_KEY = os.environ.get("SERPAPI_API_KEY", "")
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")

client = OpenAI(api_key=OPENAI_API_KEY)

# Parser robusto (usa bs4 se presente)
try:
    from bs4 import BeautifulSoup  # type: ignore
    HAVE_BS4 = True
except Exception:
    HAVE_BS4 = False

# =========================
# PROMPT “TECNICO-COMMERCIALE SENIOR” (no invenzioni)
# =========================
PROMPT = """
Agisci come TECNICO-COMMERCIALE SENIOR di TECNARIA S.p.A. (Bassano del Grappa).
Obiettivo: risposte corrette, sintetiche, utili alla decisione d’acquisto/posa. ZERO invenzioni.

Regole ferree:
- Usa SOLO le informazioni presenti nelle “Fonti web” fornite qui sotto o in ciò che l’utente ha scritto.
- Se un dato NON è nelle fonti (es. PRd numerici, codici, ETA specifici, combinazioni lamiera), dichiara: “Dato non disponibile in queste fonti”.
- P560: fissaggi su acciaio/lamiera (CTF, travi metalliche); per legno puro (CTL) si usano viti/bulloni, non la P560.
- Tieni il tono tecnico-professionale; italiano; niente enfasi. Elenchi puntati solo se servono.

Formato risposta:
1) Risposta (breve/standard/dettagliata in base alla domanda).
2) “Fonti:” con citazioni in forma [1], [2], … corrispondenti agli URL forniti.
""".strip()

# =========================
# FASTAPI
# =========================
app = FastAPI(title="Tecnaria Bot — WEB-FIRST no-compromessi")

class AskPayload(BaseModel):
    question: str

@app.get("/", response_class=HTMLResponse)
def ui():
    return HTMLResponse("""
<!doctype html><meta charset="utf-8"><title>Tecnaria Bot</title>
<style>
body{font-family:system-ui,Segoe UI,Roboto; background:#0b0f19; color:#e6e6e6; margin:0}
.wrap{max-width:900px;margin:32px auto;padding:0 16px}
h1{margin:0 0 8px}.sub{opacity:.75;margin:0 0 16px}
form{display:flex;gap:8px;margin:12px 0}
input{flex:1;padding:12px 14px;border-radius:12px;border:1px solid #273047;background:#12182b;color:#e6e6e6}
button{padding:12px 16px;border:0;border-radius:12px;background:#3a5bfd;color:#fff;font-weight:600;cursor:pointer}
.card{background:#0f1527;border:1px solid #273047;border-radius:14px;padding:16px;white-space:pre-wrap;margin-top:12px}
.small{font-size:12px;opacity:.7;margin-top:6px}
</style>
<div class="wrap">
  <h1>Tecnaria Bot</h1>
  <p class="sub">Modalità: WEB-FIRST = """ + ("ON" if WEB_FIRST else "OFF") + """</p>
  <form id="f"><input id="q" placeholder="Scrivi la tua domanda (es. Mi parli della P560?)" required><button>Chiedi</button></form>
  <div id="out" class="card" style="display:none"></div>
  <div id="meta" class="small"></div>
</div>
<script>
const f=document.getElementById('f'), q=document.getElementById('q'), out=document.getElementById('out'), meta=document.getElementById('meta');
f.addEventListener('submit', async e=>{
  e.preventDefault(); out.style.display='block'; out.textContent='Sto cercando sul web...'; meta.textContent='';
  const r = await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q.value})});
  const d = await r.json();
  if(!r.ok){ out.textContent = 'Errore: ' + (d.detail || r.statusText); return; }
  out.textContent = d.answer || '(nessuna risposta)';
  meta.textContent = (d.sources && d.sources.length ? 'Fonti: ' + d.sources.map((s,i)=>`[${i+1}] ${s.url}`).join('  •  ') : '');
});
</script>
""")

@app.get("/health")
def health():
    return JSONResponse({"status":"ok","web_first": WEB_FIRST})

# =========================
# WEB SEARCH
# =========================
def _allow_url(url: str) -> bool:
    if not SAFE_DOMAINS:
        return True
    allowed = [d.strip().lower() for d in SAFE_DOMAINS.split(",") if d.strip()]
    return any(("://" + d in url.lower()) or (url.lower().startswith("https://" + d)) or (url.lower().startswith("http://" + d)) for d in allowed)

def search_web(query: str, max_results: int) -> List[Dict]:
    # Provider 1: Tavily
    if TAVILY_API_KEY:
        try:
            import httpx
            r = httpx.post(
                "https://api.tavily.com/search",
                json={"api_key": TAVILY_API_KEY, "query": query, "max_results": max_results, "include_domains": None},
                timeout=10,
            )
            j = r.json()
            out = []
            for item in (j.get("results") or [])[:max_results]:
                if "url" in item and _allow_url(item["url"]):
                    out.append({"title": item.get("title",""), "url": item["url"], "snippet": item.get("content","")})
            if out: return out
        except Exception:
            pass
    # Provider 2: SerpAPI (Google)
    if SERPAPI_API_KEY:
        try:
            import httpx
            r = httpx.get("https://serpapi.com/search.json", params={"q": query, "api_key": SERPAPI_API_KEY, "num": max_results})
            j = r.json()
            out = []
            for item in (j.get("organic_results") or [])[:max_results]:
                url = item.get("link")
                if url and _allow_url(url):
                    out.append({"title": item.get("title",""), "url": url, "snippet": item.get("snippet","")})
            if out: return out
        except Exception:
            pass
    # Provider 3: Brave
    if BRAVE_API_KEY:
        try:
            import httpx
            r = httpx.get("https://api.search.brave.com/res/v1/web/search",
                          params={"q": query, "count": max_results},
                          headers={"Accept":"application/json","X-Subscription-Token":BRAVE_API_KEY}, timeout=10)
            j = r.json()
            out = []
            for item in (j.get("web",{}).get("results") or [])[:max_results]:
                url = item.get("url")
                if url and _allow_url(url):
                    out.append({"title": item.get("title",""), "url": url, "snippet": item.get("description","")})
            if out: return out
        except Exception:
            pass
    # Nessun provider → nessun risultato (sarà gestito a valle)
    return []

def fetch_page(url: str) -> str:
    try:
        import httpx
        r = httpx.get(url, timeout=FETCH_TIMEOUT, follow_redirects=True, headers={"User-Agent":"Mozilla/5.0"})
        content = r.text
        if HAVE_BS4:
            soup = BeautifulSoup(content, "lxml") if "lxml" in globals() else BeautifulSoup(content, "html.parser")
            # rimuovi script/style
            for t in soup(["script","style","noscript"]): t.decompose()
            text = soup.get_text(separator=" ")
        else:
            # fallback molto semplice
            text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", content, flags=re.S|re.I)
            text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        # limitiamo dimensione
        return text[:20000]
    except Exception:
        return ""

def collect_sources(query: str) -> List[Dict]:
    results = search_web(query, MAX_RESULTS)
    sources = []
    seen = set()
    for r in results:
        if len(sources) >= MAX_PAGES: break
        url = r["url"]
        if url in seen: continue
        seen.add(url)
        body = fetch_page(url)
        if len(body) < 400:  # scarta pagine vuote
            continue
        sources.append({"title": r["title"], "url": url, "snippet": r.get("snippet",""), "content": body})
        time.sleep(0.4)  # rate-limit gentile
    return sources

# =========================
# OPENAI CALLS (Responses → fallback Chat Completions)
# =========================
def call_with_citations(question: str, sources: List[Dict]) -> str:
    # Costruisci contesto fonti
    bullets = []
    for i, s in enumerate(sources, start=1):
        bullets.append(f"[{i}] {s['title'] or s['url']}\nURL: {s['url']}\nTESTO:\n{textwrap.shorten(s['content'], width=3000, placeholder=' …')}")
    sources_block = "\n\n".join(bullets) if bullets else "Nessuna fonte web disponibile."

    system_msg = {"role":"system","content": PROMPT}
    user_msg = {"role":"user","content": f"Domanda: {question}\n\nFonti web (usa SOLO queste per rispondere e cita [1],[2],...):\n\n{sources_block}"}

    # 1) Responses
    try:
        create = getattr(client, "responses").create
        resp = create(model=OPENAI_MODEL, input=[system_msg, user_msg], temperature=0.0, max_output_tokens=800)
        text = ""
        if hasattr(resp, "output") and resp.output:
            chunks = []
            for item in resp.output:
                if getattr(item, "type", None) == "message" and getattr(item, "message", None):
                    for c in getattr(item.message, "content", []) or []:
                        if isinstance(c, dict) and c.get("type")=="output_text":
                            t=c.get("text",""); 
                            if t: chunks.append(t)
            text = "\n".join(chunks).strip()
        if not text:
            text = (getattr(resp, "output_text", None) or "").strip()
        return text or "Dato non disponibile in queste fonti."
    except Exception:
        pass

    # 2) Chat Completions
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL_COMPAT,
            messages=[system_msg, user_msg],
            temperature=0.0,
            max_tokens=800,
        )
        return (resp.choices[0].message.content or "").strip() or "Dato non disponibile in queste fonti."
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore modello: {e}")

# =========================
# API
# =========================
@app.post("/api/ask")
def api_ask(p: AskPayload):
    q = (p.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="La domanda è vuota.")

    sources: List[Dict] = []
    if WEB_FIRST:
        sources = collect_sources(q)

    answer = call_with_citations(q, sources if sources else [])
    # Prepara lista fonti “pulita” per UI
    src_out = [{"title": s["title"] or s["url"], "url": s["url"]} for s in sources]

    return JSONResponse({"answer": answer, "sources": src_out, "web_used": bool(sources)})
