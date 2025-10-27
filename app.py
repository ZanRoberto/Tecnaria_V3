# app.py — Tecnaria Q/A Service (TECNARIA_GOLD) — v3
# - Routing confronto migliorato: trigger "insieme/compatibile/mix/abbinare" + auto-route se la query contiene ≥2 famiglie (CTL, CTL MAXI, CTF, P560, CTCEM, VCEM)
# - Caricamento robusto JSON (ignora non-dict) + /validate
# - /refresh per ricaricare i dataset a caldo
# - Dedup Top Risposte, frontend minimal e pulito (senza ${} nelle f-string)
# - Nessuna modifica a requirements.txt

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import List, Dict, Any, Tuple, Iterable, Set

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

APP_NAME = "Tecnaria Q/A Service"
app = FastAPI(title=APP_NAME)

# -------------------- Utils ---------------------------------------------------

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
    q = norm_txt(query)
    t = norm_txt(text)
    score = 0.0
    for k, w in weights.items():
        if k in q and k in t:
            score += w
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
    return items[0] if items else {}

# -------------------- Config & Data ------------------------------------------

DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).parent / "static" / "data")))
FILES_PRIMARY = ["ctf_gold.json", "ctl_gold.json", "p560_gold.json"]
FILE_COMPARE = "tecnaria_compare.json"

def load_json_file(p: Path) -> Any:
    if not p.exists():
        print(f"[WARN] File mancante: {p}")
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] Errore lettura {p.name}: {e}")
        return None

BAD_ITEMS: List[Dict[str, Any]] = []  # elementi scartati (path, type, sample)

def iter_objects(value: Any, path: str = "") -> Iterable[Tuple[str, Dict[str, Any]]]:
    """Ritorna solo dict; scarta e traccia tutto il resto."""
    if isinstance(value, dict):
        yield (path or "$", value)
    elif isinstance(value, list):
        for idx, v in enumerate(value):
            new_path = f"{path}[{idx}]" if path else f"$[{idx}]"
            yield from iter_objects(v, new_path)
    else:
        BAD_ITEMS.append({
            "path": path or "$",
            "type": type(value).__name__,
            "sample": (value if isinstance(value, (str, int, float)) else str(value))[:200]
        })

def normalize_item_family(it: Dict[str, Any], default_family: str = "") -> Dict[str, Any]:
    out = dict(it)
    out["id"] = out.get("id") or out.get("code") or out.get("sku") or out.get("question", "")[:48]
    out["family"] = out.get("family") or out.get("famiglia") or default_family
    out["question"] = out.get("question") or out.get("domanda") or ""
    out["answer"] = out.get("answer") or out.get("risposta") or ""
    out["tags"] = out.get("tags") or []
    out["_search"] = norm_txt(
        " ".join([
            str(out["id"] or ""),
            out["family"] or "",
            out["question"] or "",
            out["answer"] or "",
            " ".join(out["tags"]) if isinstance(out["tags"], list) else ""
        ])
    )
    return out

GOLD_ITEMS: List[Dict[str, Any]] = []
COMPARE_ITEMS: List[Dict[str, Any]] = []

def bootstrap():
    global GOLD_ITEMS, COMPARE_ITEMS, BAD_ITEMS
    GOLD_ITEMS = []
    COMPARE_ITEMS = []
    BAD_ITEMS = []

    # Carica GOLD
    for fname in FILES_PRIMARY:
        p = DATA_DIR / fname
        data = load_json_file(p)
        if data is None:
            continue
        for path, obj in iter_objects(data):
            GOLD_ITEMS.append(normalize_item_family(obj))

    # Carica CONFRONTI
    pcmp = DATA_DIR / FILE_COMPARE
    cmp_data = load_json_file(pcmp)
    if cmp_data is not None:
        for path, obj in iter_objects(cmp_data):
            base = normalize_item_family(obj)
            base["famA"] = obj.get("famA", "")
            base["famB"] = obj.get("famB", "")
            base["html"] = obj.get("html", "")
            base["_search"] = norm_txt(
                base.get("_search","") + " " +
                base["famA"] + " " + base["famB"] + " " +
                obj.get("question","") + " " + obj.get("answer","") + " " + obj.get("html","")
            )
            COMPARE_ITEMS.append(base)

bootstrap()

# -------------------- Intent & Families --------------------------------------

# Famiglie e alias riconosciuti
FAMILY_ALIASES = {
    "ctl": ["ctl"],
    "ctl maxi": ["ctl maxi", "maxi"],
    "ctf": ["ctf"],
    "p560": ["p560", "spit p560", "spit"],
    "ctcem": ["ctcem"],
    "vcem": ["vcem"]
}

def detect_families(query: str) -> Set[str]:
    q = norm_txt(query)
    found = set()
    for fam, aliases in FAMILY_ALIASES.items():
        for a in aliases:
            if a in q:
                found.add(fam)
                break
    return found

# Trigger confronto estesi (inclusi “insieme/compatibile/mix/abbinare”)
COMPARE_TRIGGERS = [
    "confronto","confrontare","differenza","differenze","vs","contro","versus",
    "meglio tra","rispetto a","paragone","paragonare","compare","difference",
    "insieme","compatibile","compatibili","compatibilità","abbinare","abbinabili",
    "mix","misto","combinare","in combinazione","accoppiare","accoppiata",
    "usare insieme","posso usare","posso mettere insieme","puoi usare insieme"
]

WEIGHTS_COMPARE = {
    "confronto": 2.0, "differenza": 2.0, "differenze": 2.0, "vs": 1.5,
    "versus": 1.5, "contro": 1.2, "meglio": 1.0, "paragone": 1.2, "rispetto a": 1.2,
    "compare": 2.0, "difference": 2.0,
    "insieme": 2.0, "compatibile": 1.5, "compatibili": 1.5, "abbinare": 1.5,
    "mix": 1.2, "misto": 1.2, "combinare": 1.2, "accoppiare": 1.2,
    # famiglie
    "ctl": 1.0, "ctl maxi": 1.0, "ctf": 1.0, "p560": 1.0, "ctcem": 0.8, "vcem": 0.8,
    "lamiera": 0.6, "legno": 0.6, "acciaio": 0.6
}

WEIGHTS_GOLD = {
    "ctf": 1.2, "ctl": 1.2, "ctl maxi": 1.2, "p560": 1.0, "ctcem": 1.0, "vcem": 1.0,
    "lamiera": 0.8, "rete": 0.7, "soletta": 0.7, "hsbr14": 0.9,
    "vite": 0.8, "viti": 0.8, "taratura": 0.8, "trave": 0.8
}

def is_compare_intent(q: str) -> bool:
    # trigger espliciti
    if contains_any(q, COMPARE_TRIGGERS):
        return True
    # auto-route: se la query contiene ≥2 famiglie diverse, trattala come confronto
    fams = detect_families(q)
    if len(fams) >= 2:
        return True
    return False

def rank_items(query: str, items: List[Dict[str, Any]], weights: Dict[str, float], k: int = 6) -> List[Dict[str, Any]]:
    scored = []
    qn = norm_txt(query)
    fams_in_q = detect_families(query)
    for it in items:
        s = score_keyword(query, it.get("_search", ""), weights)
        fams = [it.get("family",""), it.get("famA",""), it.get("famB","")]
        # boost se la famiglia dell’item è menzionata nella query
        for fam in [f for f in fams if f]:
            if norm_txt(fam) in qn:
                s += 0.5
            # boost extra se l’item di confronto riguarda entrambe le famiglie presenti in query
            if fams_in_q and norm_txt(fam) in fams_in_q:
                s += 0.25
        scored.append((s, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [it for s, it in scored[:k] if s > 0]

# -------------------- API -----------------------------------------------------

@app.get("/healthz")
def healthz():
    return {"service": APP_NAME, "status": "ok"}

@app.get("/load_status")
def load_status():
    files = FILES_PRIMARY + [FILE_COMPARE]
    return {
        "service": APP_NAME,
        "status": "ok",
        "items_gold": len(GOLD_ITEMS),
        "items_compare": len(COMPARE_ITEMS),
        "bad_items": len(BAD_ITEMS),
        "data_dir": str(DATA_DIR),
        "files": files
    }

@app.get("/validate")
def validate():
    return {"bad_items_count": len(BAD_ITEMS), "sample": BAD_ITEMS[:50]}

@app.post("/refresh")
def refresh():
    bootstrap()
    return {"status": "reloaded", "items_gold": len(GOLD_ITEMS), "items_compare": len(COMPARE_ITEMS), "bad_items": len(BAD_ITEMS)}

@app.get("/ask")
def ask(q: str = Query(..., description="Domanda utente"),
        top_k: int = Query(5, ge=1, le=10)):
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Domanda vuota.")

    # Routing confronto -> compare, altrimenti gold
    if is_compare_intent(query) and len(COMPARE_ITEMS) > 0:
        ranked = rank_items(query, COMPARE_ITEMS, WEIGHTS_COMPARE, k=top_k+3)
        route = "compare"
    else:
        ranked = rank_items(query, GOLD_ITEMS, WEIGHTS_GOLD, k=top_k+3)
        route = "gold"

    if not ranked and GOLD_ITEMS:
        ranked = rank_items(query, GOLD_ITEMS, WEIGHTS_GOLD, k=top_k+3)
        route = route + "→fallback-gold"

    if not ranked:
        return {"best": None, "top": [], "debug": {"route": route, "why": "no hits"}}

    ranked = uniq(ranked, "id")
    best = pick_best(ranked)
    top = [it for it in ranked[1:top_k+1]]

    def clean(it: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": it.get("id"),
            "family": it.get("family") or (it.get("famA","") + (" vs " if it.get("famB") else "") + it.get("famB","")),
            "question": it.get("question"),
            "answer": it.get("answer"),
            "html": it.get("html", ""),
            "tags": it.get("tags", [])
        }

    return JSONResponse({
        "best": clean(best),
        "top": [clean(it) for it in top],
        "debug": {"route": route, "hits": len(ranked)}
    })

# -------------------- Frontend -----------------------------------------------

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
    .row { display: flex; gap: 12px; align-items: center; }
    textarea { width: 100%; height: 110px; font-size: 15px; padding: 10px; }
    input[type=text]{ width: 280px; padding: 8px; }
    button { padding: 10px 14px; font-size: 14px; cursor: pointer; }
    .card { border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px; margin: 12px 0; }
    .pill { display:inline-block; padding:2px 8px; border:1px solid #e5e7eb; border-radius:999px; margin-right:6px; font-size:12px; color:#374151;}
    .k { color:#6b7280; font-size:12px; }
    .best { border-color:#16a34a; }
    .debug { font-size: 12px; color:#64748b; }
    .warn { background:#fff8e1; padding:8px; border-radius:8px; font-size:13px; }
    .htmlblock { background:#fafafa; padding:10px; border-radius:8px; margin-top:8px; }
    .toolbar { display:flex; gap:8px; align-items:center; }
  </style>
</head>
<body>
  <h1>Tecnaria Q/A Service</h1>
  <div class="muted">TECNARIA_GOLD · Test UI (routing confronto migliorato)</div>

  <div class="row" style="margin-top:12px;">
    <textarea id="q" placeholder="Scrivi una domanda… es.: Posso usare CTL e CTL MAXI insieme?"></textarea>
  </div>
  <div class="toolbar" style="margin-top:8px;">
    <button onclick="ask()">Chiedi</button>
    <button onclick="quick('Qual è la differenza tra CTL e CTL MAXI?')">CTL vs CTL MAXI</button>
    <button onclick="quick('CTL vs CTF: quando usare l’uno rispetto all’altro?')">CTL vs CTF</button>
    <button onclick="quick('Posso usare CTL e CTL MAXI insieme nello stesso solaio?')">Mix CTL/MAXI</button>
    <span class="k">Suggerimenti: “insieme”, “compatibili”, “mix”, “differenza”.</span>
  </div>

  <div id="out"></div>

  <script>
    function quick(t){ document.getElementById('q').value = t; ask(); }
    async function ask() {
      const q = document.getElementById('q').value.trim();
      const out = document.getElementById('out');
      out.innerHTML = "<div class='muted'>…sto cercando…</div>";
      try {
        const res = await fetch("/ask?q="+encodeURIComponent(q));
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
        const dbg = "<div class='debug'>route: "+(data.debug && data.debug.route)+" · hits: "+(data.debug && data.debug.hits)+"</div>";
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

# Static
static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")
