# app.py  — Tecnaria Q/A Service (TECNARIA_GOLD)
# FastAPI minimal, senza dipendenze nuove. Nessuna modifica a requirements.txt.
# Routing intelligente: "confronto/differenze" -> tecnaria_compare.json, altrimenti gold (CTL/CTF/P560).
# Dedup Top Risposte. Fix per f-string + template JS (niente ${} dentro f-string).

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import List, Dict, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

APP_NAME = "Tecnaria Q/A Service"
app = FastAPI(title=APP_NAME)

# -------- Utils ---------------------------------------------------------------

def norm_txt(s: str) -> str:
    if not isinstance(s, str):
        s = str(s)
    s = s.lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s).strip()
    return s

def contains_any(text: str, keywords: List[str]) -> bool:
    t = norm_txt(text)
    return any(kw in t for kw in keywords)

def score_keyword(query: str, text: str, weights: Dict[str, float]) -> float:
    """Semplice key-score: somma i pesi delle parole chiave presenti (robusto e senza librerie)."""
    q = norm_txt(query)
    t = norm_txt(text)
    score = 0.0
    for k, w in weights.items():
        if k in q and k in t:
            score += w
        elif k in q and k not in t:
            # leggera spinta se la chiave è in query (per non azzerare)
            score += 0.0
        elif k not in q and k in t:
            score += 0.0
    # piccolo boost se tutte le parole della query compaiono (bag-of-words grezzo)
    q_terms = [w for w in re.split(r"[^\w]+", q) if w]
    if q_terms and all(term in t for term in q_terms[:5]):
        score += 1.0
    return score

def uniq(items: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for it in items:
        k = it.get(key, "")
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out

def pick_best(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Sceglie la prima come Best (lista già ordinata per score)."""
    return items[0] if items else {}

# -------- Caricamento dati ----------------------------------------------------

DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).parent / "static" / "data")))

FILES_PRIMARY = [
    "ctf_gold.json",
    "ctl_gold.json",
    "p560_gold.json",
]
FILE_COMPARE = "tecnaria_compare.json"  # nuovo indice confronti (CTF↔CTL, CTL↔MAXI, ecc.)

def load_json_file(p: Path) -> Any:
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] Impossibile leggere {p.name}: {e}")
        return None

GOLD_ITEMS: List[Dict[str, Any]] = []
COMPARE_ITEMS: List[Dict[str, Any]] = []

def normalize_item_family(it: Dict[str, Any], default_family: str = "") -> Dict[str, Any]:
    out = dict(it)
    # normalizza alcuni campi tipici
    out["id"] = out.get("id") or out.get("code") or out.get("sku") or out.get("question", "")[:48]
    out["family"] = out.get("family") or out.get("famiglia") or default_family
    out["question"] = out.get("question") or out.get("domanda") or ""
    out["answer"] = out.get("answer") or out.get("risposta") or ""
    out["tags"] = out.get("tags") or []
    out["_search"] = norm_txt(" ".join([str(out["id"]), out["family"], out["question"], out["answer"], " ".join(out["tags"]) if isinstance(out["tags"], list) else ""]))
    return out

def bootstrap():
    global GOLD_ITEMS, COMPARE_ITEMS
    GOLD_ITEMS = []
    for fname in FILES_PRIMARY:
        data = load_json_file(DATA_DIR / fname)
        if not data:
            continue
        if isinstance(data, list):
            for it in data:
                GOLD_ITEMS.append(normalize_item_family(it))
        elif isinstance(data, dict):
            for _, it in data.items():
                GOLD_ITEMS.append(normalize_item_family(it))
    # compare
    cmp_data = load_json_file(DATA_DIR / FILE_COMPARE)
    COMPARE_ITEMS = []
    if cmp_data:
        if isinstance(cmp_data, list):
            for it in cmp_data:
                # per i confronti, fondi anche famA/famB/html se presenti
                base = normalize_item_family(it)
                base["famA"] = it.get("famA", "")
                base["famB"] = it.get("famB", "")
                base["html"] = it.get("html", "")
                # arricchisci testo ricerca con famA/famB/html
                base["_search"] = norm_txt(base["_search"] + " " + base["famA"] + " " + base["famB"] + " " + it.get("question","") + " " + it.get("answer",""))
                COMPARE_ITEMS.append(base)
        elif isinstance(cmp_data, dict):
            for _, it in cmp_data.items():
                base = normalize_item_family(it)
                base["famA"] = it.get("famA", "")
                base["famB"] = it.get("famB", "")
                base["html"] = it.get("html", "")
                base["_search"] = norm_txt(base["_search"] + " " + base["famA"] + " " + base["famB"] + " " + it.get("question","") + " " + it.get("answer",""))
                COMPARE_ITEMS.append(base)

bootstrap()

# -------- Intent detection per "confronto" ------------------------------------

COMPARE_TRIGGERS = [
    "confronto", "confrontare", "differenza", "differenze",
    "vs", "contro", "meglio tra", "quando usare", "rispetto a",
    "comparare", "paragone", "paragonare",
    "choose between", "difference", "compare", "versus"
]

# keyword weights (italiano+inglese) per ranking semplice
WEIGHTS_COMPARE = {
    "confronto": 2.0, "differenza": 2.0, "differenze": 2.0, "vs": 1.5,
    "contro": 1.2, "meglio": 1.0, "paragone": 1.2, "rispetto a": 1.2,
    "compare": 2.0, "difference": 2.0, "versus": 1.5,
    # famiglie/oggetti
    "ctl": 1.0, "ctl maxi": 1.0, "ctf": 1.0, "p560": 1.0, "lamiera": 0.8, "legno": 0.8, "acciaio": 0.8
}

WEIGHTS_GOLD = {
    "ctf": 1.2, "ctl": 1.2, "ctl maxi": 1.2, "p560": 1.0,
    "lamiera": 0.8, "rete": 0.7, "soletta": 0.7, "hsbr14": 0.9,
    "vite": 0.8, "viti": 0.8, "taratura": 0.8, "trave": 0.8
}

def is_compare_intent(q: str) -> bool:
    return contains_any(q, COMPARE_TRIGGERS)

def rank_items(query: str, items: List[Dict[str, Any]], weights: Dict[str, float], k: int = 6) -> List[Dict[str, Any]]:
    scored = []
    for it in items:
        s = score_keyword(query, it.get("_search", ""), weights)
        # boost leggero se le famiglie della query sono entrambe nel confronto
        qn = norm_txt(query)
        fams = [it.get("family",""), it.get("famA",""), it.get("famB","")]
        fams = [f for f in fams if f]
        for fam in fams:
            if fam and norm_txt(fam) in qn:
                s += 0.5
        scored.append((s, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [it for s, it in scored[:k] if s > 0]

# -------- API ----------------------------------------------------------------

@app.get("/healthz")
def healthz():
    return {"service": APP_NAME, "status": "ok"}

@app.get("/load_status")
def load_status():
    files = []
    for f in FILES_PRIMARY + [FILE_COMPARE]:
        p = DATA_DIR / f
        files.append(f)
        if not p.exists():
            print(f"[WARN] Manca il file: {p}")
    return {
        "service": APP_NAME,
        "status": "ok",
        "items_gold": len(GOLD_ITEMS),
        "items_compare": len(COMPARE_ITEMS),
        "data_dir": str(DATA_DIR),
        "files": files
    }

@app.get("/ask")
def ask(q: str = Query(..., description="Domanda utente"),
        top_k: int = Query(5, ge=1, le=10)):
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Domanda vuota.")

    # 1) instradamento
    if is_compare_intent(query) and COMPARE_ITEMS:
        ranked = rank_items(query, COMPARE_ITEMS, WEIGHTS_COMPARE, k=top_k+3)
    else:
        ranked = rank_items(query, GOLD_ITEMS, WEIGHTS_GOLD, k=top_k+3)

    # fallback se nulla
    if not ranked and GOLD_ITEMS:
        ranked = rank_items(query, GOLD_ITEMS, WEIGHTS_GOLD, k=top_k+3)

    if not ranked:
        return {"best": None, "top": [], "debug": {"route": "none", "why": "no hits"}}

    # 2) dedup per id
    ranked = uniq(ranked, "id")
    best = pick_best(ranked)
    top = [it for it in ranked[1:top_k+1]]

    # prepara payload “pulito”
    def clean(it: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": it.get("id"),
            "family": it.get("family") or (it.get("famA","") + (" vs " if it.get("famB") else "") + it.get("famB","")),
            "question": it.get("question"),
            "answer": it.get("answer"),
            "html": it.get("html", ""),
            "tags": it.get("tags", [])
        }

    payload = {
        "best": clean(best),
        "top": [clean(it) for it in top],
        "debug": {
            "route": "compare" if is_compare_intent(query) else "gold",
            "items_considered": len(ranked),
        }
    }
    return JSONResponse(payload)

# -------- Frontend minimale ---------------------------------------------------

INDEX_HTML = """
<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <title>Tecnaria Q/A Service</title>
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <style>
    body { font-family: system-ui, Arial, sans-serif; margin: 24px; line-height: 1.35; }
    h1 { margin: 0 0 8px 0; }
    .muted { color: #666; }
    .row { display: flex; gap: 16px; }
    textarea { width: 100%; height: 110px; font-size: 15px; padding: 10px; }
    button { padding: 10px 14px; font-size: 14px; cursor: pointer; }
    .card { border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px; margin: 12px 0; }
    .pill { display:inline-block; padding:2px 8px; border:1px solid #e5e7eb; border-radius:999px; margin-right:6px; font-size:12px; color:#374151;}
    .k { color:#6b7280; font-size:12px; }
    .best { border-color:#16a34a; }
    .debug { font-size: 12px; color:#64748b; }
    .warn { background:#fff8e1; padding:8px; border-radius:8px; font-size:13px; }
    .htmlblock { background:#fafafa; padding:10px; border-radius:8px; margin-top:8px; }
  </style>
</head>
<body>
  <h1>Tecnaria Q/A Service</h1>
  <div class="muted">TECNARIA_GOLD · Interfaccia di test (Compare routing attivo)</div>

  <div class="row" style="margin-top:12px;">
    <textarea id="q" placeholder="Scrivi una domanda… es.: Qual è la differenza tra CTL e CTL MAXI?"></textarea>
  </div>
  <div style="margin-top:8px;">
    <button onclick="ask()">Chiedi</button>
    <span class="k">Suggerimenti: “differenza tra CTL e CTL MAXI”, “CTL vs CTF”, “P560 taratura colpo a vuoto”.</span>
  </div>

  <div id="out"></div>

  <script>
    async function ask() {
      const q = document.getElementById('q').value.trim();
      const out = document.getElementById('out');
      out.innerHTML = "<div class='muted'>…sto cercando…</div>";
      try {
        const res = await fetch(`/ask?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        function pillList(tags) {
          if (!tags || !tags.length) return "";
          return "<div>" + tags.map(function(t){ return "<span class='pill'>"+t+"</span>"; }).join(" ") + "</div>";
        }
        function card(item, cls) {
          if (!item) return "";
          const hasHtml = item.html && item.html.length > 0;
          return "<div class='card "+(cls||"")+"'>"
            + (item.family ? "<div class='muted'>"+item.family+"</div>": "")
            + (item.question ? "<h3>"+item.question+"</h3>": "")
            + (item.answer ? "<div>"+item.answer.replace(/\\n/g,"<br/>")+"</div>": "")
            + (hasHtml ? "<div class='htmlblock'>"+item.html+"</div>": "")
            + pillList(item.tags)
            + "</div>";
        }
        const best = card(data.best, "best");
        const tops = (data.top||[]).map(function(it){ return card(it, ""); }).join("");
        const dbg = "<div class='debug'>route: "+(data.debug && data.debug.route)+" · hits: "+(data.debug && data.debug.items_considered)+"</div>";
        out.innerHTML = best + tops + dbg;
      } catch (e) {
        out.innerHTML = "<div class='warn'>Errore: "+(e && e.message ? e.message : e)+"</div>";
      }
    }
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(INDEX_HTML)

# -------- Static (se serve) ---------------------------------------------------
# Monta /static per eventuali asset
static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")
