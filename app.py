# app.py
# TECNARIA_GOLD v2.3 — robust ranking + dedupe sezioni + UI stabile
# Avvio su Render: gunicorn -k uvicorn.workers.UvicornWorker app:app

import json, re, pathlib, html
from typing import List, Dict, Any, Tuple
from fastapi import FastAPI, Query, HTTPException
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
    # campi minimi
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
        p = DATA_DIR / fn
        obj = _load_json(p)
        xs = obj["items"] if "items" in obj else obj
        xs = [_normalize_item(it) for it in xs]
        items.extend(xs)
        counts[fn] = len(xs)
    return items, counts

ALL_ITEMS, COUNTS = load_gold()

# ---------------------------------------------------------------
# Lexicon (intenti & pesi)
# ---------------------------------------------------------------
FAMILY_HINTS = {
    "ctf": ["ctf","chiodo","hsbr14","lamiera","acciaio","piastra","trave","s235","s275","s355","p560"],
    "ctl": ["ctl","legno","viti","soletta","rete","tavolato","assito","Ø10","10 mm"],
    "p560": ["p560","taratura","colpo a vuoto","cartucce","dpi","perimetro"]
}

TOKEN_WEIGHTS = {
    # comuni
    "sequenza": 1.2, "posa": 1.1, "istruzioni": 0.8, "checklist": 0.6, "errori": 0.8, "sicurezza": 0.8,
    # ctf
    "ctf": 1.6, "hsbr14": 1.2, "lamiera": 1.2, "1×1,5": 1.3, "1x1,5": 1.3, "2×1,0": 1.1, "2x1,0": 1.1,
    "s235": 0.5, "s275": 0.5, "s355": 0.5, "acciaio": 0.6,
    # ctl
    "ctl": 1.6, "maxi": 1.2, "tavolato": 1.2, "assito": 1.0, "soletta": 1.0, "ø10": 1.1, "10": 0.2,
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

    # family match
    if family and family in ql: s += 1.2
    # bias per famiglia dedotto dalla query
    fam_bias = _family_bias(q)
    if family in fam_bias: s += fam_bias[family]

    # token weights
    for tk, w in TOKEN_WEIGHTS.items():
        if tk in txt and tk in ql: s += w * 1.2
        elif tk in txt: s += w * 0.25

    # aderenza lessicale generale
    for t in re.findall(r"[a-z0-9×.,/º°ø]+", ql):
        if len(t) >= 2 and t in txt: s += 0.08

    # booster specifici
    if ("1×1,5" in ql or "1x1,5" in ql) and ("1×1,5" in txt or "1x1,5" in txt): s += 0.8
    if ("2×1,0" in ql or "2x1,0" in ql) and ("2×1,0" in txt or "2x2,0" in txt): s += 0.5
    if "hsbr14" in ql and "hsbr14" in txt: s += 0.6
    if "ø10" in ql and "ø10" in txt: s += 0.5
    if "tavolato" in ql and "tavolato" in txt: s += 0.6

    return s

def topk(q: str, k: int=5) -> List[Dict[str,Any]]:
    ranked = sorted(ALL_ITEMS, key=lambda it: score_item(q, it), reverse=True)
    # dedupe per domanda “quasi uguale”
    seen = set(); out = []
    for it in ranked:
        key = (it.get("family",""), re.sub(r"\s+"," ", (it.get("question","")[:80]).lower()))
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

def dedupe_sections(ans: str) -> str:
    # Normalizza intestazioni se serve
    text = ans
    # Se mancano i **bold**, prova ad aggiungerli su parole-chiave in inizio riga
    def add_bold_headers(s: str) -> str:
        s = re.sub(r"(?m)^(Contesto)\s*$", r"**\1**", s)
        s = re.sub(r"(?m)^(Istruzioni[^\n]*)\s*$", r"**\1**", s)
        s = re.sub(r"(?m)^(Parametri[^\n]*)\s*$", r"**\1**", s)
        s = re.sub(r"(?m)^(Sicurezza)\s*$", r"**\1**", s)
        s = re.sub(r"(?m)^(Errori[^\n]*)\s*$", r"**\1**", s)
        s = re.sub(r"(?m)^(Checklist[^\n]*)\s*$", r"**\1**", s)
        s = re.sub(r"(?m)^(Taratura[^\n]*)\s*$", r"**\1**", s)
        return s
    text = add_bold_headers(text)

    # Taglia doppioni: mantieni la prima occorrenza di ciascun header
    lines = text.splitlines()
    seen = set()
    buf = []
    for ln in lines:
        m = HEADER_RX.search(ln)
        if m:
            header = m.group(1)
            base = re.sub(r"\*\*|\s+$","",header).strip().lower()
            base = re.sub(r"\s*\(.*?\)\s*$","",base)  # es. "Istruzioni (step)"
            if base in seen:
                # salta header duplicato e qualsiasi ripetizione immediata
                continue
            seen.add(base)
        buf.append(ln)
    return "\n".join(buf)

def compose_best(q: str, k: int=5) -> Dict[str,Any]:
    candidates = topk(q, k)
    if not candidates:
        raise HTTPException(status_code=404, detail="Nessun risultato")
    best = candidates[0].copy()
    best["answer"] = dedupe_sections(best.get("answer",""))
    # dedupe anche la lista top
    tops = []
    seen = set()
    for it in candidates:
        key = (it.get("family",""), it.get("qid",""))
        if key in seen: continue
        seen.add(key)
        tmp = it.copy()
        tmp["answer"] = dedupe_sections(tmp.get("answer",""))
        tops.append(tmp)
    return {"best": best, "tops": tops}

# ---------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------
app = FastAPI(title="Tecnaria Q/A Service", version="2.3")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

@app.get("/debug/datasets")
def debug_datasets():
    return {"service":"Tecnaria Q/A Service","status":"ok","items_loaded":len(ALL_ITEMS),"data_dir":str(DATA_DIR), "files": GOLD_FILES, "counts": COUNTS}

@app.get("/qa/search")
def qa_search(q: str = Query(..., min_length=1), k: int = 5):
    return {"q": q, "k": k, "items": topk(q, k)}

@app.get("/qa/ask")
def qa_ask(q: str = Query(..., min_length=1), k: int = 5):
    return {"q": q, "k": k, **compose_best(q, k)}

@app.get("/debug/classify")
def debug_classify(q: str):
    return {"q": q, "family_bias": _family_bias(q), "preview_qids": [it.get("qid") for it in topk(q, 5)]}

# ---------------------------------------------------------------
# UI
# ---------------------------------------------------------------
HTML_UI = r"""<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Tecnaria GOLD</title>
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
    .muted{opacity:.7}
  </style>
</head>
<body>
  <header>
    <h1>TECNARIA_GOLD — Interfaccia</h1>
  </header>
  <div class="wrap">
    <div class="card" style="margin-bottom:12px">
      <div style="display:flex;gap:8px">
        <input id="q" placeholder="Fai una domanda (es. Sequenza posa CTF su lamiera 1×1,5 con P560)" style="flex:1"/>
        <button onclick="ask()">Chiedi</button>
        <button onclick="search()">Cerca (top-5)</button>
      </div>
      <div class="muted" style="margin-top:8px">Suggerimenti: “CTL MAXI tavolato 25–30 mm vite 120”, “P560 taratura colpo a vuoto”, “CTF lamiera 2×1,0 mm”.</div>
    </div>

    <div class="row">
      <div class="card">
        <h3>Risposta Migliore</h3>
        <div id="best">—</div>
      </div>
      <div class="card">
        <h3>Top Risposte</h3>
        <div id="tops" class="muted">—</div>
      </div>
    </div>
  </div>

<script>
async function ask(){
  const q = document.getElementById('q').value.trim();
  if(!q){ alert('Scrivi una domanda'); return; }
  const r = await fetch(`/qa/ask?q=${encodeURIComponent(q)}&k=5`);
  const data = await r.json();
  if(!data.best){ document.getElementById('best').innerText='Nessun risultato'; return; }
  const it = data.best;
  document.getElementById('best').innerHTML = `
    <div class="q">Q: ${it.question}</div>
    <div class="a">${it.answer.replace(/\\n/g,'<br/>')}</div>
    <div style="margin-top:8px">
      <span class="pill">${it.family || 'n/a'}</span>
      ${(it.tags||[]).map(t => `<span class='pill'>${t}</span>`).join(' ')}
      ${it.qid ? `<span class='pill'>${it.qid}</span>` : ''}
    </div>`;
  // tops
  const tops = data.tops.map(it => `
    <div style="margin-bottom:10px">
      <div class="q">Q: ${it.question}</div>
      <div class="a">${it.answer.replace(/\\n/g,'<br/>')}</div>
      <div style="margin-top:6px">
        <span class="pill">${it.family || 'n/a'}</span>
        ${(it.tags||[]).map(t => `<span class='pill'>${t}</span>`).join(' ')}
        ${it.qid ? `<span class='pill'>${it.qid}</span>` : ''}
      </div>
    </div>`).join('');
  document.getElementById('tops').innerHTML = tops;
}

async function search(){
  const q = document.getElementById('q').value.trim();
  if(!q){ alert('Scrivi una domanda'); return; }
  const r = await fetch(`/qa/search?q=${encodeURIComponent(q)}&k=5`);
  const data = await r.json();
  const tops = data.items.map(it => `
    <div style="margin-bottom:10px">
      <div class="q">Q: ${it.question}</div>
      <div class="a">${it.answer.replace(/\\n/g,'<br/>')}</div>
      <div style="margin-top:6px">
        <span class="pill">${it.family || 'n/a'}</span>
        ${(it.tags||[]).map(t => `<span class='pill'>${t}</span>`).join(' ')}
        ${it.qid ? `<span class='pill'>${it.qid}</span>` : ''}
      </div>
    </div>`).join('');
  document.getElementById('tops').innerHTML = tops;
}
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def ui():
    return HTML_UI
