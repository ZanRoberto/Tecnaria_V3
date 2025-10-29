import json, os, re
from pathlib import Path
from typing import Any, Dict, List, Tuple
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

APP_TITLE = "Tecnaria Sinapsi — Q/A"
DATA_FILE = os.getenv("TEC_DATA_FILE", "static/data/tecnaria_gold.json")

app = FastAPI(title=APP_TITLE)

# ------------------ DATA LOADING ------------------
GOLD_ITEMS: List[Dict[str, Any]] = []
DATASET_EXISTS = False
DATASET_SIZE = 0

def load_dataset() -> Tuple[bool, int]:
    global GOLD_ITEMS, DATASET_EXISTS, DATASET_SIZE
    p = Path(DATA_FILE)
    if not p.exists():
        GOLD_ITEMS = []
        DATASET_EXISTS = False
        DATASET_SIZE = 0
        return False, 0
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = []
        norm = []
        for it in items:
            if not isinstance(it, dict):
                continue
            fam = (it.get("family") or it.get("famiglia") or "").strip()
            q  = (it.get("q") or it.get("question") or "").strip()
            a  = (it.get("a") or it.get("answer") or "").strip()
            tags = it.get("tags") or it.get("keywords") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            norm.append({"family": fam, "question": q, "answer": a, "tags": tags})
        GOLD_ITEMS = norm
        DATASET_EXISTS = True
        DATASET_SIZE = p.stat().st_size
        return True, DATASET_SIZE
    except Exception:
        GOLD_ITEMS = []
        DATASET_EXISTS = False
        DATASET_SIZE = 0
        return False, 0

load_dataset()

# ------------------ MATCHING ------------------
def family_hint(q: str) -> str:
    ql = q.lower()
    if "p560" in ql: return "P560"
    if "ctf" in ql or "sparachiod" in ql or "trave acciaio" in ql: return "CTF"
    if "ctl maxi" in ql or "assito" in ql or "tavolato" in ql: return "CTL MAXI"
    if "ctl" in ql: return "CTL"
    if "ctcem" in ql: return "CTCEM"
    if "vcem"  in ql: return "VCEM"
    if "diapason" in ql: return "DIAPASON"
    if "gts" in ql: return "GTS"
    if "accessori" in ql or "codici" in ql: return "ACCESSORI"
    return ""

def score_item(q: str, it: Dict[str, Any]) -> float:
    ql = q.lower()
    s = 0.0
    fam = (it.get("family") or "").lower()
    if fam and fam in ql: s += 4.0
    hint = family_hint(q)
    if hint and hint.lower() == fam: s += 3.0
    text = (" ".join([it.get("question",""), it.get("answer","")])).lower()
    for kw, w in [
        ("p560",3.0),("hsbr14",2.2),("lamiera",1.5),("rete",1.2),
        ("viti",1.6),("soletta",1.4),("c25/30",1.0),
        ("taratura",1.8),("chiodi",1.6),("sparo",1.4),
        ("resina",1.2),("a secco",1.2),("tecnaria",1.0),
    ]:
        if kw in ql and kw in text: s += w
    q_tok = set(re.findall(r"[a-z0-9]+", ql))
    t_tok = set(re.findall(r"[a-z0-9]+", text))
    s += min(len(q_tok & t_tok)*0.2, 3.0)
    return s

def pick_answer(q: str) -> Tuple[Dict[str, Any], float]:
    if not GOLD_ITEMS: return {}, 0.0
    it, sc = max(((it, score_item(q, it)) for it in GOLD_ITEMS), key=lambda x: x[1])
    return it, sc

def is_tecnaria_domain(q: str) -> bool:
    ql = q.lower()
    banned = ["bitcoin","ricetta","calcio","meteo","borsa","binance","android","iphone","film","politica","elezioni"]
    return not any(b in ql for b in banned)

# ------------------ ROUTES ------------------
@app.get("/health")
def health():
    fam_count: Dict[str,int] = {}
    for it in GOLD_ITEMS:
        fam = it.get("family") or ""
        fam_count[fam] = fam_count.get(fam, 0) + 1
    top3 = sorted(fam_count.items(), key=lambda x: x[1], reverse=True)[:3]
    return {
        "title": APP_TITLE,
        "endpoints": {
            "health": "/health",
            "ask": "/qa/ask?q=MI%20PARLI%20DELLA%20P560%20%3F",
            "ask_alias": "/ask?q=MI%20PARLI%20DELLA%20P560%20%3F",
        },
        "data_file": str(Path(DATA_FILE).resolve()),
        "dataset_exists": DATASET_EXISTS,
        "dataset_size_bytes": DATASET_SIZE,
        "items_loaded": len(GOLD_ITEMS),
        "top_families": top3,
        "ui": "root /",
        "note": "Alias /ask attivo. RAG Tecnaria-only."
    }

@app.get("/qa/ask")
def qa_ask(q: str = Query(..., min_length=2)):
    if not is_tecnaria_domain(q):
        return {"family":"RAG","score":0.0,
                "answer":"Sono il bot ufficiale Tecnaria (Bassano del Grappa). Rispondo solo su prodotti e sistemi Tecnaria.",
                "query": q}
    it, sc = pick_answer(q)
    if not it:
        return {"family":"RAG","score":0.0,
                "answer":"Dataset non disponibile o vuoto. Caricare static/data/tecnaria_gold.json.",
                "query": q}
    return {"family": it.get("family") or "N/A",
            "score": round(sc,2),
            "answer": it.get("answer") or "Risposta non disponibile.",
            "query": q}

@app.get("/ask")
def ask_alias(q: str = Query(..., min_length=2)):
    return qa_ask(q)

# ------------------ STATIC & UI ------------------
if Path("static").exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")

HTML_PAGE = r"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Tecnaria Sinapsi — Q/A</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:linear-gradient(135deg,#ff7a18,#1f1f1f);margin:0;color:#111}
.wrap{max-width:980px;margin:48px auto;padding:0 16px}
.card{background:#fff;border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.15);overflow:hidden}
.header{padding:24px 24px 8px 24px;background:linear-gradient(90deg,#ff9a3c,#ff7a18);color:#fff}
.title{font-size:24px;margin:0;font-weight:800}
.subtitle{opacity:.9;margin-top:6px}
.content{padding:20px 24px}
.bar{display:flex;gap:8px;margin-top:8px}
input[type=text]{flex:1;padding:12px 14px;border:1px solid #ddd;border-radius:10px;font-size:16px;outline:none}
button{padding:12px 16px;border:none;border-radius:10px;background:#111;color:#fff;font-weight:700;cursor:pointer}
.pills{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.pill{background:#f3f3f3;border-radius:100px;padding:8px 12px;font-size:13px;cursor:pointer}
.meta{font-size:12px;color:#666;margin-top:8px}
.answer{white-space:pre-wrap;line-height:1.45;border:1px solid #eee;border-radius:12px;padding:14px;margin-top:12px;background:#fafafa}
.badge{display:inline-block;background:#111;color:#fff;border-radius:999px;font-size:11px;padding:4px 8px;margin-right:6px}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="header">
      <div class="title">Tecnaria Sinapsi — Q/A</div>
      <div class="subtitle">Bot ufficiale: CTF, CTL/CTL MAXI, P560, CTCEM/VCEM, DIAPASON, GTS, ACCESSORI • Stile GOLD • RAG Tecnaria-only</div>
    </div>
    <div class="content">
      <div class="bar">
        <input id="q" type="text" placeholder="Scrivi la tua domanda (es. “Mi parli della P560?”)"/>
        <button onclick="ask()">Chiedi a Sinapsi</button>
      </div>
      <div class="pills">
        <div class="pill" onclick="prefill('Mi parli della P560?')">P560 (istruzioni)</div>
        <div class="pill" onclick="prefill('Posso usare una chiodatrice qualsiasi per i CTF?')">CTF (chiodatrice)</div>
        <div class="pill" onclick="prefill('Differenza tra CTL e CTL MAXI?')">CTL vs CTL MAXI</div>
        <div class="pill" onclick="prefill('I CTCEM usano resine?')">CTCEM (resine?)</div>
        <div class="pill" onclick="prefill('Codici connettori CTF?')">Codici / ordini</div>
      </div>
      <div class="meta">Endpoint <span class="badge">/qa/ask</span> Health: <span id="hstatus">…</span></div>
      <div id="out" class="answer">Scrivi una domanda e premi “Chiedi a Sinapsi”.</div>
    </div>
  </div>
</div>
<script>
async function health(){
  try{
    const r = await fetch('/health');
    document.getElementById('hstatus').innerText = r.ok ? 'ok' : 'ko';
  }catch(e){
    document.getElementById('hstatus').innerText = 'ko';
  }
}
function prefill(t){ document.getElementById('q').value = t; }
async function ask(){
  const v = document.getElementById('q').value.trim();
  if(!v){ return; }
  const r = await fetch('/qa/ask?q='+encodeURIComponent(v));
  const js = await r.json();
  const fam = js.family || 'N/A';
  const sc = js.score !== undefined ? js.score : '';
  const ans = js.answer || '—';
  document.getElementById('out').innerText = 'Famiglia: '+fam+'  |  Score: '+sc+'\\n\\n'+ans;
}
health();
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def root():
    return HTML_PAGE

@app.get("/reload", response_class=PlainTextResponse)
def reload_data():
    ok, _ = load_dataset()
    return "reloaded: " + ("ok" if ok else "fail")
