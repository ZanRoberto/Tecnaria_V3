# app.py — TECNARIA_GOLD · FastAPI Q/A (gold + compare) — FILE COMPLETO
# Requisiti (già noti e stabili): fastapi, uvicorn, jinja2 (opzionale)
# Start: uvicorn app:app --host 0.0.0.0 --port $PORT

import os, json, re, uuid, html
from pathlib import Path
from typing import List, Dict, Any, Tuple
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

APP_NAME = "Tecnaria Q/A Service"
BASE_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "static" / "data"))

GOLD_FILES = [
    "ctf_gold.json",
    "ctl_gold.json",
    "p560_gold.json",
]

ITEMS: List[Dict[str, Any]] = []     # tutto il corpus
TAGS_COMPARE = {"vs", "confronto", "differenza", "mix", "insieme", "compatibili", "comparazione"}

# ----------------------------- UTIL ---------------------------------

def read_json(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "items" in data:
            data = data["items"]
        if not isinstance(data, list):
            return []
        return data
    except Exception:
        return []

def norm_str(x: Any) -> str:
    return str(x or "").strip()

def ensure_id() -> str:
    return uuid.uuid4().hex[:8].upper()

def normalize_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    # accetta varianti di chiavi: question/ domanda, answer/ risposta, html
    q = norm_str(raw.get("question") or raw.get("domanda"))
    a = norm_str(raw.get("answer")   or raw.get("risposta"))
    h = norm_str(raw.get("html")     or raw.get("rich_html"))
    fam = norm_str(raw.get("family") or raw.get("famiglia") or raw.get("fam"))
    tags = raw.get("tags") or raw.get("etichette") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    # fallback fam da tag
    if not fam:
        for t in tags:
            tlow = t.lower()
            if "ctf" in tlow: fam = "CTF"; break
            if "ctl maxi" in tlow: fam = "CTL MAXI"; break
            if "ctl" in tlow: fam = "CTL"; break
            if "p560" in tlow: fam = "P560"; break
    return {
        "id": norm_str(raw.get("id")) or ensure_id(),
        "family": fam or "GEN",
        "question": q,
        "answer": a,
        "html": h,
        "tags": tags,
        "source": norm_str(raw.get("source") or "gold"),
        "score_boost": float(raw.get("score_boost") or 0.0),
        "title": norm_str(raw.get("title") or raw.get("titolo") or q[:120]),
    }

def tokenize(s: str) -> List[str]:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9àèéìòóùç/_\- ]+", " ", s)
    return [t for t in s.split() if t]

def simple_score(query: str, item: Dict[str, Any]) -> float:
    qtok = set(tokenize(query))
    text = " ".join([
        item.get("title",""), item.get("question",""),
        item.get("answer",""), " ".join(item.get("tags",[])).lower()
    ]).lower()
    itok = set(tokenize(text))
    inter = qtok & itok
    score = len(inter) + item.get("score_boost", 0.0)
    # leggero bonus se tutta la famiglia è evocata
    fam = item.get("family","").lower()
    if fam and fam in " ".join(qtok):
        score += 0.5
    return score

def is_compare_query(q: str) -> bool:
    ql = q.lower()
    if " vs " in ql: return True
    for w in TAGS_COMPARE:
        if w in ql: return True
    return False

def pick_best(query: str, top_k: int = 5) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    if not ITEMS:
        return {}, [], {"route":"empty", "hits":0}

    route = "compare" if is_compare_query(query) else "gold"
    # semplice filtro per famiglie in base a parole chiave
    prefer = []
    ql = query.lower()
    if "ctf" in ql or "p560" in ql: prefer = ["CTF", "P560"]
    if "ctl maxi" in ql: prefer = ["CTL MAXI"]
    elif "ctl" in ql and "maxi" not in ql: prefer = ["CTL"]

    scored = []
    for it in ITEMS:
        # piccolo routing: se compare → preferisci domande con tag “compare/confronto”
        if route == "compare":
            if not any(t.lower() in {"compare","confronto","vs","differenza","mix"} for t in it.get("tags",[])):
                # non scartare, ma senza boost
                pass
            else:
                it["score_boost"] = it.get("score_boost", 0.0) + 0.5
        # family prefer
        if prefer and it.get("family") in prefer:
            it["score_boost"] = it.get("score_boost", 0.0) + 0.3
        scored.append((simple_score(query, it), it))

    scored.sort(key=lambda x: x[0], reverse=True)
    hits = [(s,it) for s,it in scored if s > 0]
    top = [it for _,it in hits[:max(1, top_k)]]

    # dedup per question
    seenq = set()
    uniq_top = []
    for it in top:
        key = it.get("question","")
        if key in seenq: continue
        uniq_top.append(it)
        seenq.add(key)

    best = uniq_top[0] if uniq_top else {}
    return best, uniq_top, {"route": route, "hits": len(hits)}

# ----------------------------- BOOTSTRAP ---------------------------------

def bootstrap() -> Dict[str, Any]:
    ITEMS.clear()
    loaded = []
    for fname in GOLD_FILES:
        p = DATA_DIR / fname
        rows = read_json(p)
        for raw in rows:
            it = normalize_item(raw)
            # scarta righe vuote
            if not (it["question"] or it["title"] or it["answer"] or it["html"]):
                continue
            ITEMS.append(it)
        loaded.append(fname)
    return {
        "service": APP_NAME,
        "status": "ok",
        "items_loaded": len(ITEMS),
        "data_dir": str(DATA_DIR),
        "files": loaded
    }

status = bootstrap()

# ----------------------------- APP ---------------------------------

app = FastAPI(title=APP_NAME)

@app.get("/load_status")
def load_status():
    return status

@app.get("/qa/search")
def qa_search(q: str = Query(...), top_k: int = 5):
    best, top, dbg = pick_best(q, top_k=top_k)
    return {
        "best": best,
        "top": top,
        "debug": dbg
    }

@app.get("/ask")
def ask(q: str = Query(...), top_k: int = 5):
    best, top, dbg = pick_best(q, top_k=top_k)
    return {
        "best": best,
        "top": top,
        "debug": dbg
    }

# ----------------------------- UI ---------------------------------

HTML_PAGE = """
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/>
<title>Tecnaria Q/A Service</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu; margin:24px;}
h1{margin:0 0 8px;}
textarea{width:100%; min-height:140px; font-size:16px; padding:10px;}
button{padding:10px 16px; margin:10px 8px 10px 0; cursor:pointer;}
.badge{display:inline-block; padding:2px 8px; border-radius:12px; background:#eef; margin-right:6px; font-size:12px;}
.card{border:1px solid #ddd; border-radius:8px; padding:14px; margin-top:12px;}
.dim{color:#666; font-size:12px;}
.pill{display:inline-block; padding:2px 8px; border-radius:12px; background:#f3f3f3; margin-right:6px;}
pre{white-space:pre-wrap; word-break:break-word;}
.top-item{border-left:4px solid #eee; padding-left:10px; margin:8px 0;}
</style>
</head>
<body>
  <h1>Tecnaria Q/A Service</h1>
  <div class="dim">TECNARIA_GOLD · Test UI (routing confronto migliorato)</div>
  <textarea id="q" placeholder="Scrivi una domanda..."></textarea>
  <div>
    <button onclick="ask()">Chiedi</button>
    <button onclick="preset('CTL vs CTL MAXI')">CTL vs CTL MAXI</button>
    <button onclick="preset('CTL vs CTF')">CTL vs CTF</button>
    <button onclick="preset('Mix CTL/MAXI')">Mix CTL/MAXI</button>
    <span class="dim">Suggerimenti: “insieme”, “compatibili”, “mix”, “differenza”.</span>
  </div>
  <div id="best" class="card"></div>
  <div id="tops" class="card"></div>
  <div id="dbg" class="dim"></div>

<script>
function preset(t){ document.getElementById('q').value = t; ask(); }
function esc(s){ return s===undefined||s===null ? "" : (""+s); }
function renderAnswer(it){
  // preferisci html, poi answer (testo), poi fallback su title
  const h = esc(it.html);
  const a = esc(it.answer);
  const t = esc(it.title || it.question);
  let body = "";
  if(h.trim()) body = h;
  else if(a.trim()) body = "<pre>"+a+"</pre>";
  else body = "<pre>"+t+"</pre>";
  const tags = (it.tags||[]).map(t => "<span class='pill'>"+t+"</span>").join(" ");
  return "<div><div class='badge'>"+esc(it.family||'')+"</div><b>"+esc(it.question||it.title||'')+
         "</b><div style='margin-top:8px'>"+body+"</div><div style='margin-top:8px'>"+tags+"</div></div>";
}
async function ask(){
  const q = document.getElementById('q').value || "";
  const resp = await fetch("/ask?"+new URLSearchParams({q:q, top_k:5}));
  const data = await resp.json();
  const best = data.best || {};
  const top = data.top || [];
  document.getElementById('best').innerHTML = best.id ? renderAnswer(best) : "<i>Nessun risultato.</i>";
  const tops = top.filter(it => it.id !== best.id).slice(0,4)
                  .map(it => "<div class='top-item'>"+renderAnswer(it)+"</div>").join("");
  document.getElementById('tops').innerHTML = "<b>Top Risposte</b>"+ (tops ? tops : "<div class='dim'>—</div>");
  const dbg = data.debug || {};
  document.getElementById('dbg').innerText = "route: "+(dbg.route||'?')+" · hits: "+(dbg.hits||0);
}
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_PAGE

@app.get("/health")
def health():
    return {"service": APP_NAME, "status":"ok", "items": len(ITEMS)}

# Endpoint rapido per ispezionare il dataset (utile in test)
@app.get("/debug/datasets", response_class=PlainTextResponse)
def dbg_dataset():
    lines = [f"{it.get('family','?'):7s} | {it.get('id','?'):8s} | {it.get('question','')[:80]}" for it in ITEMS[:200]]
    return "\n".join(lines) + (f"\n... total={len(ITEMS)}" if len(ITEMS)>200 else "")
