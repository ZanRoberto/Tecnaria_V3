import os, re, html, time, textwrap, io
from typing import List, Dict, Tuple
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from openai import OpenAI

# ─────────────── ENV / MODELLI ───────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata.")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
if OPENAI_MODEL.startswith("gpt-5"):
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL_COMPAT", "gpt-4o")

# WEB → LOCALE (web first)
WEB_MAX_RESULTS   = int(os.environ.get("WEB_MAX_RESULTS", "8"))
WEB_MAX_PAGES     = int(os.environ.get("WEB_MAX_PAGES", "4"))
WEB_FETCH_TIMEOUT = float(os.environ.get("WEB_FETCH_TIMEOUT", "10"))
SAFE_DOMAINS = [d.strip().lower() for d in os.environ.get(
    "WEB_SAFE_DOMAINS",
    "tecnaria.com,www.tecnaria.com,spitpaslode.it,spit.eu,eta.europa.eu,cstb.fr"
).split(",") if d.strip()]

# provider (ne basta uno)
TAVILY_API_KEY  = os.environ.get("TAVILY_API_KEY", "")
SERPAPI_API_KEY = os.environ.get("SERPAPI_API_KEY", "")
BRAVE_API_KEY   = os.environ.get("BRAVE_API_KEY", "")

# Knowledge base interna
KB_PATH = os.environ.get("KB_PATH", "KB_FAQ.md")
KB_MIN_OVERLAP = int(os.environ.get("KB_MIN_OVERLAP", "1"))  # parole chiave in comune minime per mostrare nota
KB_TOPK = int(os.environ.get("KB_TOPK", "2"))                # quante note interne mostrare al massimo

client = OpenAI(api_key=OPENAI_API_KEY)

# ─────────────── PROMPT LOCALE ───────────────
PROMPT = """
Agisci come TECNICO-COMMERCIALE SENIOR di TECNARIA S.p.A. (Bassano del Grappa).
Obiettivo: risposte corrette, sintetiche e utili alla decisione d’acquisto/posa. ZERO invenzioni.

Ambito: connettori CTF (lamiera grecata), CTL (legno-calcestruzzo), CTCEM/VCEM (acciaio-calcestruzzo),
accessori/posa (SPIT P560, chiodi/propulsori, kit/adattatori), utilizzi, compatibilità, vantaggi/limiti,
note su certificazioni/ETA e documentazione.

Regole:
1) Domanda semplice/commerciale → risposta BREVE (2–5 righe).
2) Domanda tecnica → risposta DETTAGLIATA ma concisa; punti elenco solo se utili.
3) Domanda ambigua → risposta STANDARD e proponi documento/contatto tecnico.
4) Mai inventare codici, PRd, ETA o combinazioni di lamiera: “Dato non disponibile in questa sede; fornibile su scheda/ETA su richiesta”.
5) P560: fissaggi su acciaio/lamiera (CTF, travi metalliche); per legno puro (CTL) si usano viti/bulloni, non la P560.
Tono: tecnico, professionale, concreto. Italiano.
""".strip()

# ─────────────── FASTAPI ───────────────
app = FastAPI(title="Tecnaria Bot — WEB → LOCALE + Note interne")

class AskPayload(BaseModel):
    question: str

# ─────────────── UI VERDE (no f-string) ───────────────
@app.get("/", response_class=HTMLResponse)
def ui():
    html_page = """
<!doctype html><meta charset="utf-8"><title>Tecnaria Bot</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--g:#1aa35b;--bg:#0b0f19;--card:#0f1527;--mut:#9fb3c8}
body{margin:0;background:var(--bg);color:#e6e6e6;font-family:system-ui,Segoe UI,Roboto,Arial}
.wrap{max-width:1080px;margin:24px auto;padding:0 16px}
.header{display:flex;align-items:center;gap:12px}
.badge{background:#0e1c2f;border:1px solid #27405c;border-radius:999px;padding:6px 10px;font-size:12px;color:#cfe1ff}
.panel{display:grid;grid-template-columns:320px 1fr;gap:20px;margin-top:14px}
.left{background:var(--card);border:1px solid #273047;border-radius:16px;padding:14px}
.right{background:#111833;border:1px solid #273047;border-radius:16px;padding:14px;min-height:180px}
h1{margin:.2rem 0 0;font-size:22px}
.label{font-size:12px;color:var(--mut);margin:10px 0 6px}
textarea{width:100%;height:320px;background:#0f1426;border:1px solid #26314a;border-radius:12px;color:#e6e6e6;padding:10px;resize:vertical}
.btn{display:inline-block;background:var(--g);border:0;color:#07130d;font-weight:700;padding:10px 14px;border-radius:10px;cursor:pointer}
.btn:disabled{opacity:.6;cursor:not-allowed}
.tag{display:inline-block;border:1px solid #2a3a56;color:#bcd0ef;border-radius:999px;padding:4px 10px;font-size:12px;margin-right:6px}
.code{white-space:pre-wrap;line-height:1.5}
.small{font-size:12px;color:#aab7c7;margin-top:6px}
</style>
<div class="wrap">
  <div class="header">
    <div class="badge">pronto</div>
    <div class="badge">web→locale</div>
    <div class="badge">note interne: ON</div>
  </div>
  <h1>Tecnaria Bot</h1>
  <div class="small">Prima Web (domini ufficiali), poi Locale. In coda aggiunge la <b>Nota integrativa (interno)</b> dal tuo file.</div>

  <div class="panel">
    <div class="left">
      <div class="label">Domanda</div>
      <textarea id="q" placeholder="Es.: “Se i chiodi si piegano o non entrano?”"></textarea>
      <div style="margin-top:10px">
        <button class="btn" id="ask">Chiedi</button>
        <span class="tag">P560</span><span class="tag">Connettori CTF</span><span class="tag">Contatti</span>
      </div>
    </div>
    <div class="right">
      <div class="label">Risposta</div>
      <div id="out" class="code">OK</div>
    </div>
  </div>
</div>
<script>
const q = document.getElementById('q');
const out = document.getElementById('out');
document.getElementById('ask').addEventListener('click', async ()=>{
  const question = q.value.trim();
  if(!question){ out.textContent = "Scrivi una domanda."; return; }
  out.textContent = "Cerco sul web…";
  const r = await fetch('/api/ask', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question})});
  const d = await r.json();
  if(!r.ok){ out.textContent = "Errore: " + (d.detail || r.statusText); return; }
  out.textContent = d.answer || "(nessuna risposta)";
});
</script>
"""
    return HTMLResponse(html_page)

@app.get("/health")
def health():
    return JSONResponse({"status":"ok","mode":"web_first_then_local","kb_path":KB_PATH,"model":OPENAI_MODEL})

# ─────────────── KB INTERNA: parser + match ───────────────
KB_ENTRIES: List[Dict] = []

def _load_kb(path: str) -> List[Dict]:
    entries: List[Dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = f.read()
        # Cerca blocchi tipo:
        # Q8: testo domanda
        # A8: testo risposta (anche su più righe) ... fino alla prossima Qxx: o EOF
        pattern = re.compile(r"(?mi)^Q(\d+):\s*(.+?)\s*\nA\1:\s*(.+?)(?=\nQ\d+:|\Z)", re.S)
        for m in pattern.finditer(data):
            idx = m.group(1)
            qtext = m.group(2).strip()
            atext = m.group(3).strip()
            entries.append({"id": idx, "q": qtext, "a": atext})
    except Exception:
        pass
    return entries

def _tokenize(s: str) -> List[str]:
    return [w for w in re.split(r"[^a-z0-9àèéìòóù]+", s.lower()) if len(w) >= 3]

def _kb_notes_for(question: str, topk: int = KB_TOPK, min_overlap: int = KB_MIN_OVERLAP) -> List[Dict]:
    if not KB_ENTRIES:
        return []
    qtok = set(_tokenize(question))
    scored = []
    for e in KB_ENTRIES:
        etok = set(_tokenize(e["q"] + " " + e["a"]))
        overlap = len(qtok & etok)
        if overlap >= min_overlap:
            scored.append((overlap, e))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:topk]]

# Carica KB all’avvio
KB_ENTRIES = _load_kb(KB_PATH)

# ─────────────── WEB ───────────────
def _allowed(url: str) -> bool:
    u = url.lower()
    return any(u.startswith("https://" + d) or u.startswith("http://" + d) or ("://" + d in u) for d in SAFE_DOMAINS)

def _search_web(query: str, k: int) -> List[Dict]:
    out: List[Dict] = []
    try:
        import httpx
        qq = query
        ql = qq.lower()
        if "p560" in ql or "p 560" in ql:
            qq += " SPIT P560 Tecnaria connettori CTF lamiera grecata chiodatrice"
        # Tavily
        if TAVILY_API_KEY:
            r = httpx.post("https://api.tavily.com/search", json={"api_key":TAVILY_API_KEY,"query":qq,"max_results":k}, timeout=10)
            for it in (r.json().get("results") or []):
                u = it.get("url")
                if u and _allowed(u): out.append({"title": it.get("title",""), "url": u})
        # SerpAPI
        if len(out) < k and SERPAPI_API_KEY:
            r = httpx.get("https://serpapi.com/search.json", params={"q":qq,"api_key":SERPAPI_API_KEY,"num":k}, timeout=10)
            for it in (r.json().get("organic_results") or []):
                u = it.get("link")
                if u and _allowed(u) and u not in [x["url"] for x in out]:
                    out.append({"title": it.get("title",""), "url": u})
        # Brave
        if len(out) < k and BRAVE_API_KEY:
            r = httpx.get("https://api.search.brave.com/res/v1/web/search",
                          params={"q":qq,"count":k}, headers={"X-Subscription-Token":BRAVE_API_KEY,"Accept":"application/json"}, timeout=10)
            for it in (r.json().get("web",{}).get("results") or []):
                u = it.get("url")
                if u and _allowed(u) and u not in [x["url"] for x in out]:
                    out.append({"title": it.get("title",""), "url": u})
    except Exception:
        pass
    return out[:k]

def _fetch_text(url: str) -> str:
    try:
        import httpx
        r = httpx.get(url, timeout=WEB_FETCH_TIMEOUT, follow_redirects=True, headers={"User-Agent":"Mozilla/5.0"})
        ctype = (r.headers.get("Content-Type","").lower())
        if "application/pdf" in ctype or url.lower().endswith(".pdf"):
            try:
                from pdfminer.high_level import extract_text
                return (extract_text(io.BytesIO(r.content)) or "")[:20000].strip()
            except Exception:
                return ""
        text = r.text
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S|re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        return re.sub(r"\s+", " ", text).strip()[:20000]
    except Exception:
        return ""

def _answer_from_web(question: str) -> Tuple[str, List[str]]:
    results = _search_web(question, WEB_MAX_RESULTS)
    sources, contents = [], []
    for r in results:
        if len(sources) >= WEB_MAX_PAGES: break
        body = _fetch_text(r["url"])
        if len(body) < 400:
            continue
        sources.append(r["url"])
        contents.append(f"{r['url']}\n{body}")
        time.sleep(0.25)
    if not sources:
        return "", []
    sources_block = "\n\n".join(textwrap.shorten(c, width=3000, placeholder=" …") for c in contents)
    sys = {"role":"system","content":"Rispondi SOLO usando le fonti fornite. Se un dato non c'è, scrivi: 'Dato non disponibile in queste fonti.' Cita con [1],[2],... in fondo."}
    usr = {"role":"user","content": f"Domanda: {question}\n\nFonti:\n{sources_block}"}
    try:
        resp = client.chat.completions.create(model=OPENAI_MODEL, messages=[sys, usr], temperature=0.0, max_tokens=900)
        txt = (resp.choices[0].message.content or "").strip()
        if txt:
            cite_block = "Fonti:\n" + "\n".join(f"[{i+1}] {u}" for i,u in enumerate(sources))
            return f"{txt}\n\n{cite_block}", sources
    except Exception:
        pass
    return "", sources

# ─────────────── LOCALE (fallback) ───────────────
def _answer_local_generic(question: str) -> str:
    msgs = [{"role":"system","content":PROMPT}, {"role":"user","content":question}]
    resp = client.chat.completions.create(model=OPENAI_MODEL, messages=msgs, temperature=0.0, top_p=1.0, max_tokens=750)
    txt = (resp.choices[0].message.content or "").strip()
    return txt or "Dato non disponibile in questa sede. Possiamo inviare la scheda tecnica/ETA su richiesta."

def _answer_local_p560() -> str:
    return (
        "La SPIT P560 è una chiodatrice a propulsore per fissaggi su acciaio e lamiera grecata. "
        "Impiego tipico con i connettori CTF su lamiere grecate o su travi metalliche; "
        "consente posa rapida senza foratura tradizionale. "
        "Per i sistemi su legno puro (CTL) non si usa la P560: si impiegano viti/bulloni. "
        "La scelta di chiodi/propulsori e adattatori dipende da lamiera/profilo e va verificata su scheda tecnica. "
        "PRd/codici specifici non sono forniti qui: li inviamo su richiesta."
    )

# ─────────────── API ───────────────
@app.post("/api/ask")
def api_ask(p: AskPayload):
    q = (p.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="La domanda è vuota.")

    # 1) WEB FIRST
    web_answer, sources = _answer_from_web(q)
    ok_web = web_answer and "Dato non disponibile in queste fonti." not in web_answer
    if ok_web:
        final = web_answer
    else:
        # 2) FALLBACK LOCALE (P560 guard-rail)
        qlow = q.lower()
        if "p560" in qlow or "p 560" in qlow or ("spit" in qlow and "p560" in qlow):
            final = _answer_local_p560()
        else:
            final = _answer_local_generic(q)
        if sources:
            final += "\n\nFonti utili (web):\n" + "\n".join(f"- {u}" for u in sources)

    # 3) NOTA INTEGRATIVA (INTERNO) — sempre se c’è match nel file KB
    notes = _kb_notes_for(q)
    if notes:
        final += "\n\nNota integrativa (interno):\n" + "\n".join([f"- Q{n['id']}: {n['a']}" for n in notes])

    return JSONResponse({"answer": final, "model": OPENAI_MODEL})
