from __future__ import annotations
import os, json, re
from pathlib import Path
from typing import Dict, Any, List, Tuple
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

APP_TITLE = "Tecnaria Sinapsi — Q/A"
DATA_PATH = Path(__file__).parent / "static" / "data" / "tecnaria_gold.json"

app = FastAPI(title=APP_TITLE)

# --- Static (se servono asset futuri) ---
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
(app.mount("/static", StaticFiles(directory=str(static_dir)), name="static"))

# --- Caricamento dataset ---
GOLD: List[Dict[str, Any]] = []
def load_gold() -> Tuple[int, str]:
    if not DATA_PATH.exists():
        return 0, f"Data file not found at: {DATA_PATH}"
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Normalizza voci (accettiamo sia lista piatta che {items:[...]})
        items = data.get("items") if isinstance(data, dict) else data
        out: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict): 
                continue
            fam = (it.get("family") or it.get("famiglia") or "").strip().upper()
            q   = (it.get("q") or it.get("question") or "").strip()
            a   = (it.get("a") or it.get("answer") or "").strip()
            tag = it.get("tags") or it.get("keywords") or []
            code = it.get("code") or it.get("id") or ""
            if not q or not a: 
                continue
            out.append({
                "family": fam,
                "question": q,
                "answer": a,
                "tags": tag if isinstance(tag, list) else [],
                "code": code,
            })
        # Salva global
        GOLD.clear()
        GOLD.extend(out)
        return len(GOLD), "ok"
    except Exception as e:
        return 0, f"load error: {e}"

count, status = load_gold()

# --- Scoring molto robusto (no AI esterna) ---
WORD_SPLIT = re.compile(r"[^\w]+", flags=re.UNICODE)

def tokenize(s: str) -> List[str]:
    return [t for t in WORD_SPLIT.split(s.lower()) if t]

def score_item(q: str, it: Dict[str, Any]) -> float:
    # punteggi: match su domanda originale, answer, tags, family
    q_tokens = set(tokenize(q))
    score = 0.0
    # boost parole chiave dure
    boosts = {
        "ctf": 2.5, "ctl": 2.3, "maxi": 1.8, "p560": 2.5, "hsbr14": 1.7,
        "lamiera": 1.6, "grecata": 1.6, "taratura": 1.5, "vite": 1.5,
        "laterocemento": 1.8, "ctcem": 1.9, "vcem": 1.9, "codici": 2.2, "ordine": 1.6
    }
    # campo question
    for t in q_tokens:
        if t and t in it["question"].lower():
            score += 1.0 + boosts.get(t, 0.0)
    # campo answer
    for t in q_tokens:
        if t and t in it["answer"].lower():
            score += 0.6 + boosts.get(t, 0.0) * 0.5
    # tags
    for tag in it["tags"]:
        tagl = str(tag).lower()
        for t in q_tokens:
            if t and t in tagl:
                score += 0.8 + boosts.get(t, 0.0)
    # family
    fam = (it["family"] or "").lower()
    for t in q_tokens:
        if t and t in fam:
            score += 0.5 + boosts.get(t, 0.0)*0.3
    # penalità se famiglie non Tecnaria note
    if fam and fam not in {"ctf","ctl","ctl maxi","p560","ctcem","vcem","diapason","gts","accessori"}:
        score -= 1.0
    return score

def best_answer(query: str) -> Dict[str, Any]:
    if not GOLD:
        return {"family": "", "question": "", "answer": "Dataset vuoto. Caricare tecnaria_gold.json.", "score": 0.0}
    scored = [(score_item(query, it), it) for it in GOLD]
    scored.sort(key=lambda x: x[0], reverse=True)
    top_score, top_it = scored[0]
    return {
        "family": top_it["family"],
        "question": top_it["question"],
        "answer": top_it["answer"],
        "score": round(top_score, 3),
    }

# --- API ---
@app.get("/health")
def health():
    return {
        "title": APP_TITLE,
        "endpoints": {"health": "/health", "ask": "/qa/ask?q=MI%20PARLI%20DELLA%20P560%20%3F"},
        "data_file": str(DATA_PATH),
        "items_loaded": len(GOLD)
    }

@app.get("/qa/ask")
def qa_ask(q: str = Query(..., description="Domanda utente")):
    q_clean = q.strip()
    if not q_clean:
        msg = (
            "Domanda vuota. Esempi: "
            "«Mi parli della P560?» · «Posso usare CTL e CTL MAXI insieme?» · "
            "«CTF: codici disponibili?» · «CTCEM serve resina?»"
        )
        return JSONResponse({"family": "", "question": "", "answer": msg, "score": 0.0})
    ans = best_answer(q_clean)
    # Hard-guard: limitiamo all’universo Tecnaria
    blocklist = ["H75 ", " H75", "h75 "]
    if any(b in q_clean.upper() for b in blocklist):
        ans["answer"] += "\n\nNota: H75 è un’altezza profilo lamiera, non un prodotto Tecnaria; il focus resta sui prodotti Tecnaria."
    return JSONResponse(ans)

# --- UI: Homepage con interfaccia scelta (gradevole) ---
HTML_PAGE = """
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Tecnaria Sinapsi — Q/A</title>
<style>
:root{
  --bg:#0b0b0c; --card:#121214; --muted:#9aa0a6; --text:#e8eaed;
  --brand1:#ff7a00; --brand2:#0a0a0a; --ok:#21c77a; --chip:#1f1f22;
}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(120deg,var(--brand1),#cc4d00 12%,#1a1a1c 12%,#0b0b0c 100%);color:var(--text);font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,"Helvetica Neue",Arial}
.container{max-width:1100px;margin:48px auto;padding:0 16px}
.header{display:flex;align-items:center;gap:12px;margin-bottom:24px}
.logo{width:42px;height:42px;border-radius:10px;background:#ff7a00;display:grid;place-items:center;font-weight:900;color:#111}
.hgroup h1{margin:0;font-size:26px}
.hgroup p{margin:2px 0 0;color:var(--muted)}
.card{background:var(--card);border-radius:18px;padding:18px;box-shadow:0 10px 30px rgba(0,0,0,.35)}
.grid{display:grid;grid-template-columns:1.2fr .8fr;gap:18px}
@media(max-width:900px){.grid{grid-template-columns:1fr;}}
.search{display:flex;gap:12px}
.search input{flex:1;padding:16px 14px;border-radius:14px;border:1px solid #252528;background:#121216;color:var(--text);outline:none}
.search button{padding:0 20px;border:0;border-radius:14px;background:#ff7a00;color:#111;font-weight:800;cursor:pointer}
.pills{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.pill{background:var(--chip);border:1px solid #2a2a2e;border-radius:30px;padding:6px 10px;font-size:13px;color:#c7cad1;cursor:pointer}
.aside .card{height:100%}
.kv{display:grid;grid-template-columns:140px 1fr;gap:6px;font-size:14px;color:#cfd1d6}
.kv div b{color:#fff}
.answer{white-space:pre-wrap;margin-top:8px}
.small{color:#9aa0a6;font-size:13px}
.ok{color:var(--ok)}
</style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="logo">T</div>
      <div class="hgroup">
        <h1>Trova la soluzione, in linguaggio Tecnaria.</h1>
        <p>Bot ufficiale — risposte su <b>CTF</b>, <b>CTL/CTL MAXI</b>, <b>P560</b>, <b>CTCEM/VCEM</b>, confronti, codici, ordini.</p>
      </div>
    </div>
    <div class="grid">
      <div class="main">
        <div class="card">
          <div class="search">
            <input id="q" placeholder="Chiedi a Sinapsi (es. “Mi parli della P560?”)" />
            <button onclick="ask()">Chiedi a Sinapsi</button>
          </div>
          <div class="pills">
            <span class="pill" onclick="fill('Mi parli della P560?')">P560 (istruzioni)</span>
            <span class="pill" onclick="fill('CTL MAXI su tavolato 30 mm e soletta 50 mm: quali viti?')">CTL MAXI + viti</span>
            <span class="pill" onclick="fill('Posso usare CTL e CTL MAXI insieme nello stesso solaio?')">Mix CTL/MAXI</span>
            <span class="pill" onclick="fill('CTCEM per laterocemento: serve resina?')">CTCEM resine?</span>
            <span class="pill" onclick="fill('Dammi i codici disponibili per i CTF')">Codici CTF</span>
          </div>
        </div>

        <div id="result" class="card" style="margin-top:14px;">
          <div class="small">Endpoint <b>/qa/ask</b>: <span id="api" class="ok">ok</span></div>
          <div class="kv" style="margin-top:8px">
            <div><b>Famiglia</b></div><div id="fam">—</div>
            <div><b>Score</b></div><div id="score">—</div>
          </div>
          <div style="margin-top:10px"><b>Risposta</b></div>
          <div class="answer" id="ans">—</div>
        </div>
      </div>

      <div class="aside">
        <div class="card">
          <div class="kv">
            <div><b>Titolo</b></div><div>Tecnaria Sinapsi — Q/A</div>
            <div><b>Salute</b></div><div><span id="health">checking…</span></div>
            <div><b>Dati</b></div><div><code>/static/data/tecnaria_gold.json</code></div>
            <div><b>Items</b></div><div id="items">—</div>
          </div>
          <div class="small" style="margin-top:10px">
            Risposte esclusivamente su prodotti Tecnaria. Quando utile, la UI aggiunge note (es. H75 ≠ prodotto Tecnaria).
          </div>
        </div>
      </div>
    </div>
  </div>
<script>
async function health(){
  try{
    const r = await fetch('/health');
    const j = await r.json();
    document.getElementById('health').textContent = 'ok';
    document.getElementById('items').textContent = j.items_loaded ?? '—';
  }catch(e){
    document.getElementById('health').textContent = 'errore';
  }
}
function fill(t){ document.getElementById('q').value = t; ask(); }
async function ask(){
  const q = document.getElementById('q').value.trim();
  if(!q){ return; }
  const r = await fetch('/qa/ask?q='+encodeURIComponent(q));
  const j = await r.json();
  document.getElementById('fam').textContent = (j.family||'—');
  document.getElementById('score').textContent = (j.score!=null? j.score:'—');
  document.getElementById('ans').textContent = (j.answer||'—');
}
health();
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTML_PAGE
