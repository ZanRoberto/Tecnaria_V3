# app.py
# TECNARIA_GOLD v2.4 — best-always-populated, robust ranking, dedupe, UI stabile
# Start (Render): gunicorn -k uvicorn.workers.UvicornWorker app:app

import json, re, pathlib, html
from typing import List, Dict, Any, Tuple
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

# ---------------------------------------------------------------
# Config & Datasets
# ---------------------------------------------------------------
APP_DIR = pathlib.Path(__file__).parent
DATA_DIR = APP_DIR / "static" / "data"
GOLD_FILES = ["ctf_gold.json", "ctl_gold.json", "p560_gold.json"]

def _load_json(p: pathlib.Path) -> Dict[str, Any]:
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)

def _normalize_item(it: Dict[str, Any]) -> Dict[str, Any]:
    it.setdefault("qid", "")
    it.setdefault("family", "")
    it.setdefault("question", "")
    it.setdefault("answer", "")
    it.setdefault("tags", [])
    return it

def load_gold() -> Tuple[List[Dict[str,Any]], Dict[str,int]]:
    items = []
    counts = {}
    for fn in GOLD_FILES:
        obj = _load_json(DATA_DIR / fn)
        xs = obj["items"] if "items" in obj else obj
        xs = [_normalize_item(it) for it in xs]
        items.extend(xs)
        counts[fn] = len(xs)
    return items, counts

ALL_ITEMS, COUNTS = load_gold()

# ---------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------
FAMILY_HINTS = {
    "ctf": ["ctf","chiodo","hsbr14","lamiera","acciaio","piastra","trave","s235","s275","s355","p560"],
    "ctl": ["ctl","legno","viti","soletta","rete","tavolato","assito","ø10","10 mm"],
    "p560": ["p560","taratura","colpo a vuoto","cartucce","dpi","perimetro"]
}

TOKEN_WEIGHTS = {
    # comuni
    "sequenza": 1.2, "posa": 1.1, "istruzioni": 0.8, "checklist": 0.6, "errori": 0.8, "sicurezza": 0.8,
    # ctf
    "ctf": 1.6, "hsbr14": 1.2, "lamiera": 1.2, "1×1,5": 1.3, "1x1,5": 1.3, "2×1,0": 1.1, "2x1,0": 1.1,
    "s235": 0.5, "s275": 0.5, "s355": 0.5, "acciaio": 0.6,
    # ctl
    "ctl": 1.6, "maxi": 1.2, "tavolato": 1.2, "assito": 1.0, "soletta": 1.0, "ø10": 1.1,
    # p560
    "p560": 1.5, "taratura": 1.4, "colpo": 0.9, "vuoto": 0.9, "dpi": 1.0, "perimetro": 0.8, "cartucce": 0.9
}

def _family_bias(q: str) -> Dict[str,float]:
    ql = q.lower()
    bias = {"ctf":0.0, "ctl":0.0, "p560":0.0}
    for fam, toks in FAMILY_HINTS.items():
        bias[fam] = sum(1 for t in toks if t in ql) * 0.4
    return bias

def score_item(q: str, it: Dict[str,Any]) -> float:
    ql = q.lower()
    family = (it.get("family") or "").lower()
    txt = (" ".join([it.get("question",""), it.get("answer",""), " ".join(it.get("tags",[]))])).lower()
    s = 0.0

    if family and family in ql: s += 1.2
    fam_bias = _family_bias(q)
    if family in fam_bias: s += fam_bias[family]

    for tk, w in TOKEN_WEIGHTS.items():
        if tk in txt and tk in ql: s += w * 1.2
        elif tk in txt: s += w * 0.25

    for t in re.findall(r"[a-z0-9×.,/º°ø]+", ql):
        if len(t) >= 2 and t in txt: s += 0.08

    if ("1×1,5" in ql or "1x1,5" in ql) and ("1×1,5" in txt or "1x1,5" in txt): s += 0.8
    if ("2×1,0" in ql or "2x1,0" in ql) and ("2×1,0" in txt or "2x1,0" in txt): s += 0.5
    if "hsbr14" in ql and "hsbr14" in txt: s += 0.6
    if "ø10" in ql and "ø10" in txt: s += 0.5
    if "tavolato" in ql and "tavolato" in txt: s += 0.6
    return s

def topk(q: str, k: int=5) -> List[Dict[str,Any]]:
    ranked = sorted(ALL_ITEMS, key=lambda it: score_item(q, it), reverse=True)
    seen = set(); out = []
    for it in ranked:
        key = (it.get("family",""), re.sub(r"\s+"," ", (it.get("question","")[:85]).lower()))
        if key in seen: continue
        seen.add(key)
        out.append(it)
        if len(out) >= k: break
    return out

# ---------------------------------------------------------------
# Dedupe sezioni in Answer
# ---------------------------------------------------------------
HEADER_PATTERNS = [
    r"\*\*Contesto\*\*", r"\*\*Istruzioni[^\n]*\*\*", r"\*\*Parametri[^\n]*\*\*",
    r"\*\*Sicurezza[^\n]*\*\*", r"\*\*Errori[^\n]*\*\*", r"\*\*Checklist[^\n]*\*\*", r"\*\*Taratura[^\n]*\*\*"
]
HEADER_RX = re.compile("(" + "|".join(HEADER_PATTERNS) + ")")

def _normalize_headers(s: str) -> str:
    s = re.sub(r"(?m)^(Contesto)\s*$", r"**\1**", s)
    s = re.sub(r"(?m)^(Istruzioni[^\n]*)\s*$", r"**\1**", s)
    s = re.sub(r"(?m)^(Parametri[^\n]*)\s*$", r"**\1**", s)
    s = re.sub(r"(?m)^(Sicurezza)\s*$", r"**\1**", s)
    s = re.sub(r"(?m)^(Errori[^\n]*)\s*$", r"**\1**", s)
    s = re.sub(r"(?m)^(Checklist[^\n]*)\s*$", r"**\1**", s)
    s = re.sub(r"(?m)^(Taratura[^\n]*)\s*$", r"**\1**", s)
    return s

def dedupe_sections(ans: str) -> str:
    text = _normalize_headers(ans or "")
    lines, seen, buf = text.splitlines(), set(), []
    for ln in lines:
        m = HEADER_RX.search(ln)
        if m:
            header = re.sub(r"\*\*|\s+$","", m.group(1)).strip().lower()
            header = re.sub(r"\s*\(.*?\)\s*$","", header)
            if header in seen:  # salta header duplicati
                continue
            seen.add(header)
        buf.append(ln)
    return "\n".join(buf)

def make_best_from_list(cands: List[Dict[str,Any]]) -> Dict[str,Any]:
    if not cands: return {}
    best = cands[0].copy()
    best["answer"] = dedupe_sections(best.get("answer",""))
    tops = []
    seen = set()
    for it in cands:
        key = (it.get("family",""), it.get("qid",""))
        if key in seen: continue
        seen.add(key)
        tmp = it.copy()
        tmp["answer"] = dedupe_sections(tmp.get("answer",""))
        tops.append(tmp)
    return {"best": best, "tops": tops}

def compose_best(q: str, k: int=5) -> Dict[str,Any]:
    # 1) tenta ranking normale
    cands = topk(q, k)
    if cands:
        return make_best_from_list(cands)
    # 2) fallback: usa tokenizzazione “larga” (toglie punteggiatura)
    q2 = re.sub(r"[^a-zA-Z0-9ø×,/.\s]", " ", q)
    cands = topk(q2, k)
    if cands:
        return make_best_from_list(cands)
    # 3) nessun risultato
    return {"best": {}, "tops": []}

# ---------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------
app = FastAPI(title="Tecnaria Q/A Service", version="2.4")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"ok": True, "items_loaded": len(ALL_ITEMS)}

@app.get("/debug/datasets")
def debug_datasets():
    return {"service":"Tecnaria Q/A Service","status":"ok","items_loaded":len(ALL_ITEMS),
            "data_dir":str(DATA_DIR), "files": GOLD_FILES, "counts": COUNTS}

@app.get("/debug/classify")
def debug_classify(q: str):
    return {"q": q, "family_bias": _family_bias(q), "preview_qids": [it.get("qid") for it in topk(q, 5)]}

@app.get("/qa/search")
def qa_search(q: str = Query(..., min_length=1), k: int = 5):
    return {"q": q, "k": k, "items": topk(q, k)}

@app.get("/qa/ask")
def qa_ask(q: str = Query(..., min_length=1), k: int = 5):
    # Best-always-populated: se ranking “puro” è vuoto, fallback al top-1 di /qa/search
    data = compose_best(q, k)
    if data["best"]:
        return {"q": q, "k": k, **data}
    # Fallback finale al top-1 di search
    items = topk(q, 1)
    if items:
        return {"q": q, "k": k, **make_best_from_list(items)}
    return {"q": q, "k": k, "best": {}, "tops": []}

# ---------------------------------------------------------------
# UI (no f-strings)
# ---------------------------------------------------------------
HTML_UI = r"""<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Tecnaria Q/A — GOLD Semantic</title>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial,sans-serif;margin:0;background:#0b0f17;color:#e7eaf2}
    header{padding:16px 20px;background:#111827;position:sticky;top:0;z-index:2}
    h1{margin:0;font-size:18px}
    .wrap{max-width:1100px;margin:20px auto;padding:0 16px}
    .row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
    .card{background:#111827;border:1px solid #1f2937;border-radius:14px;padding:16px}
    .pill{display:inline-block;background:#1f2937;border:1px solid #374151;border-radius:999px;padding:2px 8px;margin-right:6px;font-size:12px}
    input,button{padding:10px 12px;border-radius:8px;border:1px solid #374151;background:#0b0f17;color:#e7eaf2}
    button{cursor:pointer}
    .q{font-weight:700;margin:6px 0 8px}
    .a{white-space:pre-wrap;line-height:1.42}
    .muted{opacity:.75}
    a{color:#93c5fd}
  </style>
</head>
<body>
  <header>
    <h1>Tecnaria Q/A — GOLD Semantic <span class="muted">• 1200</span></h1>
  </header>
  <div class="wrap">
    <div class="card" style="margin-bottom:12px">
      <div style="display:flex;gap:8px">
        <input id="q" placeholder="Fai una domanda (es. Sequenza posa CTF su lamiera 1×1,5 con P560)" style="flex:1"/>
        <button onclick="ask()">Chiedi</button>
        <button onclick="search()">Cerca</button>
      </div>
      <div class="muted" style="margin-top:8px">
        Health: <a href="/health">/health</a> · API: <a href="/qa/search">/qa/search</a>, <a href="/qa/ask">/qa/ask</a> · Debug: <a href="/debug/datasets">/debug/datasets</a> · Classify: /debug/classify?q=...
      </div>
      <div class="muted" style="margin-top:6px">Suggerimenti: “CTL MAXI tavolato 25–30 mm vite 120”, “P560 taratura colpo a vuoto”, “CTF lamiera 2×1,0 mm S355”.</div>
    </div>

    <div class="row">
      <div class="card">
        <h3>Top Risposte</h3>
        <div id="tops" class="muted">—</div>
      </div>
      <div class="card">
        <h3>Risposta Migliore</h3>
        <div id="best">—</div>
      </div>
    </div>
  </div>

<script>
function renderItem(it){
  return `
    <div style="margin-bottom:10px">
      <div class="q">Q: ${it.question||'—'}</div>
      <div class="a">${(it.answer||'').replace(/\n/g,'<br/>')}</div>
      <div style="margin-top:6px">
        <span class="pill">${it.family||'n/a'}</span>
        ${(it.tags||[]).map(t => `<span class='pill'>${t}</span>`).join(' ')}
        ${it.qid ? `<span class='pill'>${it.qid}</span>` : ''}
      </div>
    </div>`;
}

async function ask(){
  const q = document.getElementById('q').value.trim();
  if(!q){ alert('Scrivi una domanda'); return; }
  const r = await fetch(`/qa/ask?q=${encodeURIComponent(q)}&k=5`);
  const data = await r.json();
  // Sempre popola "best": se vuota, tenta un fallback lato client con /qa/search
  if(!data.best || !data.best.question){
    const r2 = await fetch(`/qa/search?q=${encodeURIComponent(q)}&k=5`);
    const d2 = await r2.json();
    if(d2.items && d2.items.length){
      document.getElementById('best').innerHTML = renderItem(d2.items[0]);
      document.getElementById('tops').innerHTML = d2.items.map(renderItem).join('');
      return;
    }else{
      document.getElementById('best').innerText = 'Nessun risultato';
      document.getElementById('tops').innerText = '—';
      return;
    }
  }
  document.getElementById('best').innerHTML = renderItem(data.best);
  document.getElementById('tops').innerHTML = (data.tops||[]).map(renderItem).join('');
}

async function search(){
  const q = document.getElementById('q').value.trim();
  if(!q){ alert('Scrivi una domanda'); return; }
  const r = await fetch(`/qa/search?q=${encodeURIComponent(q)}&k=5`);
  const data = await r.json();
  const items = data.items||[];
  document.getElementById('tops').innerHTML = items.length ? items.map(renderItem).join('') : '—';
  // NEW: popola sempre anche la “Risposta Migliore” col top-1 della ricerca
  if(items.length){
    document.getElementById('best').innerHTML = renderItem(items[0]);
  }else{
    document.getElementById('best').innerText = 'Nessun risultato';
  }
}
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def ui():
    return HTML_UI
