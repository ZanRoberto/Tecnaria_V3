# app.py
# TECNARIA_GOLD — GOLD+INTELLIGENT
# UI sempre attiva + API Q/A GOLD + Debug conteggi + Fallback "codici"
# - UI su "/"
# - /health per stato JSON
# - /qa/search e /qa/ask (ask ora con fallback se query è "catalogo/codici/sigle")
# - /debug/datasets per contare item
# - Caricamento GOLD da static/data/*.json (ctf_gold.json, ctl_gold.json, p560_gold.json o *_gold.json)

from __future__ import annotations

import json
import pathlib
import re
from typing import List, Dict, Any, Optional
from functools import lru_cache

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# ---------------------------------------------------------------
# Config
# ---------------------------------------------------------------
APP_DIR = pathlib.Path(__file__).parent
DATA_DIR = APP_DIR / "static" / "data"
GOLD_FILES = ["ctf_gold.json", "ctl_gold.json", "p560_gold.json"]

# Parole chiave che attivano il fallback "catalogo/codici"
FALLBACK_CODE_TOKENS = [
    "codici", "codice", "sigle", "sigla", "catalogo", "modelli", "modello",
    "listino", "tabella", "scheda", "nomenclatura"
]

# ---------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------
class QAItem(BaseModel):
    qid: Optional[str] = None
    family: Optional[str] = None
    question: str
    answer: str
    tags: Optional[List[str]] = []
    level: Optional[str] = None
    source_hint: Optional[str] = None

class SearchResponse(BaseModel):
    query: str
    count: int
    results: List[QAItem]

class AskResponse(BaseModel):
    query: str
    result: Optional[QAItem] = None
    found: bool

# ---------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------
app = FastAPI(
    title="Tecnaria Q/A Service — TECNARIA_GOLD",
    version="1.1.0",
    description="UI sempre attiva + API su dataset GOLD (CTF/CTL/P560) da static/data/ con fallback intelligente per richieste 'codici/catalogo'."
)

# CORS aperto (limitabile se serve)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------
def _iter_candidate_files() -> List[pathlib.Path]:
    cand: List[pathlib.Path] = []
    # 1) canonici
    for name in GOLD_FILES:
        p = DATA_DIR / name
        if p.exists() and p.is_file():
            cand.append(p)
    # 2) fallback: qualsiasi *_gold.json
    for p in sorted(DATA_DIR.glob("*_gold.json")):
        if p not in cand:
            cand.append(p)
    if not cand:
        raise FileNotFoundError(
            f"Nessun dataset GOLD trovato. Attesi: {', '.join(GOLD_FILES)} "
            f"oppure qualsiasi *_gold.json in {DATA_DIR}"
        )
    return cand

def _normalize_records(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict) and "items" in raw and isinstance(raw["items"], list):
        return raw["items"]
    if isinstance(raw, list):
        return raw
    raise ValueError("Formato dataset non valido: atteso {'items':[...]} oppure lista di item.")

@lru_cache(maxsize=1)
def load_gold() -> List[QAItem]:
    items: List[QAItem] = []
    seen = set()
    for p in _iter_candidate_files():
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        for rec in _normalize_records(data):
            q = (rec.get("question") or "").strip()
            a = (rec.get("answer") or "").strip()
            if not q or not a:
                continue
            items.append(QAItem(
                qid=rec.get("qid"),
                family=rec.get("family"),
                question=q,
                answer=a,
                tags=rec.get("tags") or [],
                level=rec.get("level"),
                source_hint=rec.get("source_hint"),
            ))
    if not items:
        raise ValueError("Nessun item valido caricato.")
    return items

# ---------------------------------------------------------------
# Fallback "catalogo/codici"
# ---------------------------------------------------------------
CATALOGO_ANSWER = """\
Ecco una **scheda rapida codici/modelli** per le famiglie presenti:

**CTF (acciaio)**
• Modello: **CTF** (fissaggio meccanico con **2 chiodi HSBR14** per connettore).  
• Posa: a secco con **SPIT P560** + kit/adattatori Tecnaria.  
• Contesti ammessi: trave acciaio con anima ≥ **6 mm**; con lamiera grecata: **1×1,5 mm** oppure **2×1,0 mm** ben serrata all’ala.  
• Note: non richiede resine; rete a metà spessore; cls **≥ C25/30**.

**CTL (legno) — serie standard**
• **CTL 12/030**, **CTL 12/040**, **CTL 12/050**, **CTL 12/060**  
• Fissaggio: **2 viti Ø10** per connettore.  
• Viti tipiche: **100/120 mm** (in base a interposti/tavolato).

**CTL MAXI (legno su tavolato)**
• **CTL MAXI 12/040**, **CTL MAXI 12/050**, **CTL MAXI 12/060**  
• Fissaggio: **2 viti Ø10**; lunghezze più comuni **100/120/140 mm** (scegli in funzione dello spessore dell’assito/interposto: con **≥ 25–30 mm** preferisci la più lunga).

**P560 (utensile di posa)**
• Macchina: **SPIT P560** (nolo/vendita) con **kit/adattatori Tecnaria**.  
• Uso: taratura con 2–3 tiri di prova; doppia chiodatura; DPI e perimetro di sicurezza 3 m.

Se ti servono **codici articolo interni** (SKU) per ordine/offerta, dimmelo e ti preparo una **tabella pronta** con colonne: *Famiglia · Modello · Viti/Chiodi · Note di posa*.
"""

def needs_catalog_fallback(query: str) -> bool:
    q = (query or "").lower()
    return any(tok in q for tok in FALLBACK_CODE_TOKENS)

def make_catalog_item(query: str) -> QAItem:
    return QAItem(
        qid="CAT-001",
        family="CATALOGO",
        question="Quali sono i codici/modelli disponibili per i connettori Tecnaria (CTF, CTL, CTL MAXI) e l'utensile P560?",
        answer=CATALOGO_ANSWER,
        tags=["codici", "catalogo", "modelli", "sigle", "CTL", "CTF", "P560"],
        level="sintesi",
        source_hint="Sintesi operativa su famiglie CTF/CTL/CTL MAXI e utensile P560."
    )

# ---------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------
def _score(item: QAItem, ql: str) -> float:
    base = 0.0
    fam = (item.family or "").lower()
    if fam and fam in ql:
        base += 2.0
    for t in (item.tags or []):
        t0 = (t or "").lower()
        if t0 and t0 in ql:
            base += 1.0
    qtxt = (item.question or "").lower()
    atxt = (item.answer or "").lower()
    if ql and ql in qtxt:
        base += 1.5
    # piccoli boost per parole molto frequenti in queste tematiche
    tokens = {tok for tok in re.split(r"\W+", ql) if tok}
    for tok in tokens:
        if tok in qtxt:
            base += 0.40
        if tok in atxt:
            base += 0.20
    # micro-boost se la query cita P560/HSBR14/lamiera ecc.
    for key, bonus in [("p560", 0.5), ("hsbr14", 0.3), ("lamiera", 0.3), ("tavolato", 0.3)]:
        if key in ql:
            base += bonus
    return base

def _rank(query: str, k: int = 5) -> List[QAItem]:
    ql = (query or "").lower().strip()
    if not ql:
        return []
    items = load_gold()
    ranked = sorted(items, key=lambda it: _score(it, ql), reverse=True)
    return ranked[:max(1, k)]

# ---------------------------------------------------------------
# UI — sempre su "/"
# (HTML statico: niente f-string → nessun problema con parentesi JS)
# ---------------------------------------------------------------
HTML_UI = r"""<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Tecnaria Q/A — GOLD</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, 'Helvetica Neue', Arial, sans-serif; margin: 0; background: #0b0c10; color: #eaf0f6; }
    header { padding: 20px; background: #101219; border-bottom: 1px solid #1c2030; }
    h1 { margin: 0; font-size: 20px; letter-spacing: .5px; }
    main { max-width: 1100px; margin: 0 auto; padding: 20px; }
    .card { background: #111622; border: 1px solid #1c2030; border-radius: 14px; padding: 16px; margin-bottom: 16px; box-shadow: 0 2px 10px rgba(0,0,0,.3); }
    .row { display: grid; grid-template-columns: 1fr auto; gap: 12px; align-items: center; }
    input, button { height: 44px; border-radius: 10px; border: 1px solid #283049; background: #0f1420; color: #eaf0f6; }
    input { padding: 0 12px; width: 100%; }
    button { padding: 0 18px; cursor: pointer; }
    .pill { display:inline-block; padding: 2px 8px; border: 1px solid #2e3754; border-radius: 999px; margin-right: 6px; font-size: 12px; color: #a9b6d3; }
    .q { font-weight: 600; margin-bottom: 6px; }
    .a { white-space: pre-wrap; line-height: 1.45; }
    .meta { font-size: 12px; color: #93a2c8; margin-top: 6px; }
    .err { color: #ff6b6b; }
    .muted { color:#93a2c8; font-size:13px; }
    .footer { margin-top: 24px; font-size: 12px; color: #7f8bb0; }
    .split { display:grid; grid-template-columns: 1fr 1fr; gap:16px; }
    @media (max-width: 900px) { .split { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
<header>
  <h1> Tecnaria Q/A — GOLD · <span class="muted" id="count">—</span> </h1>
  <div class="muted">Files: <span id="files">—</span> <span class="err" id="err"></span></div>
</header>

<main>
  <div class="card">
    <div class="row">
      <input id="q" placeholder='Fai una domanda libera (es. “Che codici hanno i connettori?” o “Posso posare CTF su lamiera H55 con P560?”)' />
      <button onclick="ask()">Chiedi</button>
    </div>
    <div class="muted" style="margin-top:8px">
      Suggerimenti: “Che codici hanno i connettori?”, “CTL MAXI tavolato 25 mm vite 120”, “P560 taratura colpo a vuoto”, “CTF lamiera 2×1,0 mm S355”.
    </div>
  </div>

  <div class="split">
    <div class="card">
      <h3>Top Risposte</h3>
      <div class="row" style="margin-bottom:8px">
        <input id="qsearch" placeholder="Cerca (top-5)..." />
        <button onclick="search()">Cerca</button>
      </div>
      <div id="results"></div>
    </div>

    <div class="card">
      <h3>Risposta Migliore</h3>
      <div id="best"></div>
    </div>
  </div>

  <div class="footer">
    Health: <a href="/health" target="_blank">/health</a> · API: <code>/qa/search</code>, <code>/qa/ask</code> · Debug: <a href="/debug/datasets" target="_blank">/debug/datasets</a>
  </div>
</main>

<script>
async function hydrate() {
  try {
    const r = await fetch('/health');
    const d = await r.json();
    if (d.status === 'ok') {
      document.getElementById('count').textContent = d.items_loaded;
      document.getElementById('files').textContent = (d.files || []).join(', ');
    } else {
      document.getElementById('err').textContent = d.error || 'errore';
    }
  } catch (e) {
    document.getElementById('err').textContent = String(e);
  }
}

async function search() {
  const q = document.getElementById('qsearch').value.trim();
  if (!q) return;
  const r = await fetch(`/qa/search?q=${encodeURIComponent(q)}&k=5`);
  const data = await r.json();
  const root = document.getElementById('results');
  root.innerHTML = '';
  (data.results || []).forEach(it => {
    const el = document.createElement('div');
    el.className = 'card';
    el.innerHTML = `
      <div class="q">Q: ${it.question}</div>
      <div class="a">${it.answer.replace(/\\n/g,'<br/>')}</div>
      <div class="meta">
        <span class="pill">${it.family || 'n/a'}</span>
        ${(it.tags||[]).map(t => `<span class='pill'>${t}</span>`).join(' ')}
        ${it.qid ? `<span class='pill'>${it.qid}</span>` : ''}
      </div>`;
    root.appendChild(el);
  });
}

async function ask() {
  const q = document.getElementById('q').value.trim();
  if (!q) return;

  // Pulisce "Top Risposte" a ogni nuova domanda
  document.getElementById('results').innerHTML = '';

  const r = await fetch(`/qa/ask?q=${encodeURIComponent(q)}`);
  const data = await r.json();
  const best = document.getElementById('best');
  if (!data.found) {
    best.innerHTML = `<div class='err'>Nessun risultato.</div>`;
    return;
  }
  const it = data.result;
  best.innerHTML = `
    <div class="q">Q: ${it.question}</div>
    <div class="a">${it.answer.replace(/\\n/g,'<br/>')}</div>
    <div class="meta">
      <span class="pill">${it.family || 'n/a'}</span>
      ${(it.tags||[]).map(t => `<span class='pill'>${t}</span>`).join(' ')}
      ${it.qid ? `<span class='pill'>${it.qid}</span>` : ''}
    </div>`;
}

hydrate();
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def ui_root() -> HTMLResponse:
    return HTMLResponse(content=HTML_UI)

# ---------------------------------------------------------------
# Health + API
# ---------------------------------------------------------------
@app.get("/health", summary="Health JSON")
def health() -> Dict[str, Any]:
    try:
        n = len(load_gold())
        files = [p.name for p in _iter_candidate_files()]
        return {"service":"Tecnaria Q/A Service","status":"ok","items_loaded":n,"data_dir":str(DATA_DIR),"files":files}
    except Exception as e:
        return {"service":"Tecnaria Q/A Service","status":"error","error":str(e)}

@app.get("/qa/search", response_model=SearchResponse, summary="Top-k Q/A")
def qa_search(
    q: str = Query(..., min_length=2, description="Testo della ricerca"),
    k: int = Query(5, ge=1, le=25, description="Numero risultati")
) -> SearchResponse:
    try:
        results = _rank(q, k=k)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore durante la ricerca: {e}")
    return SearchResponse(query=q, count=len(results), results=results)

@app.get("/qa/ask", response_model=AskResponse, summary="Risposta migliore con fallback 'codici'")
def qa_ask(q: str = Query(..., min_length=2, description="Domanda libera")) -> AskResponse:
    try:
        # Fallback intelligente: domande "codici/catalogo/sigle"
        if needs_catalog_fallback(q):
            return AskResponse(query=q, result=make_catalog_item(q), found=True)
        # Altrimenti ranking classico
        best = _rank(q, k=1)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore durante la ricerca: {e}")
    if not best:
        return AskResponse(query=q, result=None, found=False)
    return AskResponse(query=q, result=best[0], found=True)

# ---------------------------------------------------------------
# Debug: conteggio item per ogni dataset GOLD caricato
# ---------------------------------------------------------------
@app.get("/debug/datasets", summary="Conteggi per file GOLD")
def debug_datasets() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        for p in _iter_candidate_files():
            try:
                with p.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
                    out[p.name] = len(data["items"])
                elif isinstance(data, list):
                    out[p.name] = len(data)
                else:
                    out[p.name] = None
            except Exception as e:
                out[p.name] = f"error: {e}"
        out["_total_items_loaded"] = len(load_gold())
    except Exception as e:
        out["error"] = str(e)
    return out

# ---------------------------------------------------------------
# Local run (opzionale). In produzione su Render usa gunicorn+uvicorn worker.
# ---------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
