# app.py — TECNARIA_GOLD · FastAPI Q/A (gold + compare) — FILE COMPLETO
# Requisiti (già noti e stabili): fastapi, uvicorn, jinja2 (opzionale)# app.py
import json
import re
import unicodedata
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

APP_NAME = "Tecnaria Q/A Service"
ROOT_DIR = Path(__file__).parent.resolve()
DATA_DIR = ROOT_DIR / "static" / "data"

# ---------- Utilities ----------

def norm_txt(s: Any) -> str:
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip()

def tokenize(s: str) -> List[str]:
    s = s.lower()
    s = re.sub(r"[^\wàèéìòóùç/.-]+", " ", s, flags=re.IGNORECASE)
    return [t for t in s.split() if t]

def safe_get(d: Dict[str, Any], keys: List[str], default="") -> str:
    for k in keys:
        if k in d and isinstance(d[k], str):
            return d[k]
    return default

def load_all_items(data_dir: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not data_dir.exists():
        return items
    for p in sorted(data_dir.glob("*.json")):
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        # Accept: list[...] OR {"items":[...]} OR any iterable of dicts
        payload = None
        if isinstance(data, list):
            payload = data
        elif isinstance(data, dict):
            if "items" in data and isinstance(data["items"], list):
                payload = data["items"]
            else:
                # try flatten dict-of-lists
                flat = []
                for v in data.values():
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        flat.extend(v)
                payload = flat if flat else None

        if not payload:
            continue

        for it in payload:
            if not isinstance(it, dict):
                continue
            q = norm_txt(
                safe_get(it, ["q", "question", "domanda", "title"], "")
            )
            a = norm_txt(
                safe_get(it, ["a", "answer", "risposta", "content", "text"], "")
            )
            fam = norm_txt(
                safe_get(it, ["family", "famiglia", "categoria"], "")
            )
            tags = it.get("tags") or it.get("tag") or []
            if isinstance(tags, str):
                tags = [tags]
            tags = [norm_txt(t) for t in tags if t]

            # Only keep rows with some content
            if not q and not a:
                continue

            items.append({
                "question": q,
                "answer": a,
                "family": fam or infer_family_from_filename(p.name),
                "tags": tags,
                "source_file": p.name
            })
    return items

def infer_family_from_filename(name: str) -> str:
    s = name.lower()
    if "ctf" in s:
        return "CTF"
    if "ctl" in s:
        return "CTL"
    if "p560" in s:
        return "P560"
    if "vcem" in s:
        return "VCEM"
    if "cem" in s or "ceme" in s or "cem-e" in s:
        return "CEM-E"
    if "gts" in s:
        return "GTS"
    if "diapason" in s:
        return "DIAPASON"
    if "accessori" in s or "accessory" in s:
        return "ACCESSORI"
    return ""

def score_candidate(query: str, item: Dict[str, Any]) -> float:
    """
    Semplice motore di rilevanza:
    - overlap termini (query vs question+answer+tags)
    - boost per famiglie/termini chiave
    - piccola penalità se risposta è troppo corta
    """
    q_tokens = set(tokenize(query))
    hay = " ".join([
        item.get("question", ""),
        item.get("answer", ""),
        " ".join(item.get("tags", [])),
        item.get("family", "")
    ])
    h_tokens = set(tokenize(hay))

    if not h_tokens:
        return 0.0

    overlap = len(q_tokens & h_tokens)
    ratio = overlap / max(1, len(q_tokens))

    # Boost keyword
    key_boost = 0.0
    keys = [
        ("ctf", 0.25), ("ctl", 0.25), ("maxi", 0.18), ("p560", 0.22),
        ("hsbr14", 0.18), ("lamiera", 0.15), ("rete", 0.10),
        ("soletta", 0.12), ("viti", 0.12), ("chiodi", 0.12),
        ("s275", 0.10), ("s355", 0.10)
    ]
    ht = " " + hay.lower() + " "
    for k, w in keys:
        if f" {k} " in ht:
            key_boost += w

    # Penalità risposte troppo corte (riduce il rischio “risposta scarna”)
    ans_len = len(item.get("answer", "")) if item.get("answer") else 0
    short_pen = 0.0
    if ans_len < 300:
        short_pen = 0.15
    if ans_len < 120:
        short_pen = 0.30

    return max(0.0, ratio + key_boost - short_pen)

def choose_best_answer(query: str, items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not items:
        return None
    # Se la query inizia con una famiglia (es. "CTF", "CTL"), leggero filtro
    fam_hint = None
    m = re.match(r"^\s*(ctf|ctl|p560|vcem|cem[- ]?e|gts|diapason)\b", query, flags=re.I)
    if m:
        fam_hint = m.group(1).upper().replace(" ", "").replace("-", "")
        fam_map = {"CEME": "CEM-E", "CEME": "CEM-E"}
        fam_hint = fam_map.get(fam_hint, fam_hint)

    candidates = items
    if fam_hint:
        filtered = [it for it in items if it.get("family", "").upper() == fam_hint]
        if filtered:
            candidates = filtered

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for it in candidates:
        s = score_candidate(query, it)
        if s > 0:
            scored.append((s, it))

    if not scored:
        # fallback: ritorna la più “ricca” (risposta più lunga) della collezione
        longest = max(items, key=lambda x: len(x.get("answer", "")) if x.get("answer") else 0, default=None)
        return longest

    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1]

# ---------- Bootstrap (load data) ----------

GOLD_ITEMS: List[Dict[str, Any]] = []

def bootstrap():
    GOLD_ITEMS.clear()
    loaded = load_all_items(DATA_DIR)
    # Normalizzazione minima
    for it in loaded:
        it["question"] = it.get("question") or ""
        it["answer"] = it.get("answer") or ""
        it["family"] = it.get("family") or ""
        it["tags"] = it.get("tags") or []
        GOLD_ITEMS.append(it)

bootstrap()

# ---------- FastAPI ----------

app = FastAPI(title=APP_NAME)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)

@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"

@app.get("/service")
def service():
    return {
        "service": APP_NAME,
        "status": "ok",
        "items_loaded": len(GOLD_ITEMS),
        "data_dir": str(DATA_DIR),
        "files": sorted({it.get("source_file","") for it in GOLD_ITEMS if it.get("source_file")})
    }

@app.get("/api/ask")
def api_ask(q: str = Query(..., description="Domanda libera")):
    query = norm_txt(q)
    if not query:
        return {"ok": False, "message": "Query vuota."}

    best = choose_best_answer(query, GOLD_ITEMS)
    if not best:
        return {"ok": False, "message": "Nessuna risposta disponibile nei file GOLD."}

    return {
        "ok": True,
        "best_answer": {
            "question": best.get("question", ""),
            "answer": best.get("answer", ""),
            "family": best.get("family", ""),
            "tags": best.get("tags", []),
            "source_file": best.get("source_file", "")
        }
    }

@app.get("/", response_class=HTMLResponse)
def home():
    html = """
<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Tecnaria Q/A – Risposta Migliore</title>
<style>
  :root{--bg:#0b0d10;--card:#14181d;--muted:#93a1b1;--fg:#e6edf3;--accent:#4cc9f0;--ok:#22c55e;--warn:#f59e0b;}
  body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Inter,Arial,sans-serif;background:var(--bg);color:var(--fg);}
  .wrap{max-width:980px;margin:40px auto;padding:0 16px;}
  .title{font-weight:700;font-size:28px;letter-spacing:.2px;}
  .bar{display:flex;gap:8px;margin:16px 0;}
  input[type=text]{flex:1;padding:14px 16px;border-radius:12px;border:1px solid #26313d;background:#0f141a;color:var(--fg);outline:none}
  button{padding:14px 18px;border-radius:12px;border:0;background:var(--accent);color:#081018;font-weight:700;cursor:pointer;}
  .card{background:var(--card);border:1px solid #26313d;border-radius:14px;padding:16px;margin-top:14px;}
  .muted{color:var(--muted);font-size:13px}
  .ans h3{margin:0 0 8px 0}
  .grid{display:grid;grid-template-columns:1fr;gap:6px;margin-top:8px}
  .pill{display:inline-block;padding:2px 8px;border-radius:999px;background:#0f141a;border:1px solid #26313d;color:var(--muted);font-size:12px;margin-right:4px}
  .meta{display:flex;justify-content:space-between;align-items:center;margin-top:8px}
  .small{font-size:12px;color:var(--muted)}
</style>
</head>
<body>
  <div class="wrap">
    <div class="title">Tecnaria Q/A — Risposta Migliore</div>
    <div class="muted">Solo una risposta. Niente duplicati. Usa i file GOLD in <code>static/data</code>.</div>

    <div class="bar">
      <input id="q" type="text" placeholder="Fai una domanda (es. 'Quali codici CTL MAXI?' o 'CTF su lamiera 1×1,5 mm')"/>
      <button id="go">Chiedi</button>
    </div>

    <div id="out" class="card">
      <div class="muted">Suggerimenti: “Codici CTF”, “CTL MAXI 12/050 viti”, “CTF lamiera 2×1,0 mm S355”, “P560 taratura”.</div>
    </div>

    <div class="card">
      <div class="small">/service → mostra quanti elementi sono caricati. /api/ask?q=… → API JSON.</div>
    </div>
  </div>

<script>
const $ = (sel) => document.querySelector(sel);
const out = $("#out");
const q = $("#q");
$("#go").addEventListener("click", ask);
q.addEventListener("keydown", (e)=>{ if(e.key==="Enter") ask(); });

async function ask(){
  const txt = q.value.trim();
  if(!txt){ return; }
  out.innerHTML = "<div class='muted'>Cerco la risposta migliore…</div>";
  try{
    const res = await fetch("/api/ask?q="+encodeURIComponent(txt));
    const data = await res.json();
    if(!data.ok){
      out.innerHTML = "<div class='muted'>"+(data.message||"Nessuna risposta trovata")+"</div>";
      return;
    }
    const it = data.best_answer || {};
    const fam = it.family ? "<span class='pill'>"+it.family+"</span>" : "";
    const tags = (it.tags||[]).map(t=>"<span class='pill'>"+t+"</span>").join(" ");
    const src = it.source_file ? "<span class='small'>Fonte: "+it.source_file+"</span>" : "";

    const qhtml = it.question ? ("<div class='muted'>Q: "+escapeHtml(it.question)+"</div>") : "";
    const ans = it.answer ? it.answer : "<i class='muted'>Nessuna risposta testuale nel file.</i>";

    out.innerHTML = `
      <div class="ans">
        ${qhtml}
        <h3>Risposta Migliore</h3>
        <div>${ans}</div>
        <div class="meta">
          <div>${fam} ${tags}</div>
          <div>${src}</div>
        </div>
      </div>
    `;
  }catch(e){
    out.innerHTML = "<div class='muted'>Errore di rete o servizio non raggiungibile.</div>";
  }
}

function escapeHtml(s){
  return s.replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
}
</script>
</body>
</html>
    """
    return HTMLResponse(html)

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
