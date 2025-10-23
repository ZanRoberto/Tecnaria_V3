# app.py — Tecnaria_V3 (UI inclusa, pronta per Render)

from typing import List, Dict, Any
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import time, re, csv, json

# -------------------------------
# FastAPI
# -------------------------------
app = FastAPI(title="Tecnaria_V3")

# -------------------------------
# Dati (static/data)
# -------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "static" / "data"
OV_JSON = DATA_DIR / "tecnaria_overviews.json"
CMP_JSON = DATA_DIR / "tecnaria_compare.json"
FAQ_CSV = DATA_DIR / "faq.csv"

def load_json(path: Path, fallback: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f) or []
                if isinstance(data, list):
                    return data
    except Exception:
        pass
    return fallback or []

def load_faq_csv(path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    if not path.exists():
        return rows

    def _read(encoding: str):
        with path.open("r", encoding=encoding, newline="") as f:
            rdr = csv.DictReader(f)
            for r in rdr:
                rows.append({
                    "id": (r.get("id") or "").strip(),
                    "lang": (r.get("lang") or "").strip().lower() or "it",
                    "question": (r.get("question") or "").strip(),
                    "answer": (r.get("answer") or "").strip(),
                    "tags": (r.get("tags") or "").strip().lower(),
                })

    try:
        _read("utf-8-sig")
    except Exception:
        try:
            _read("cp1252")
        except Exception:
            return rows

    fixes = {
        "â€™": "’", "â€œ": "“", "â€\x9d": "”", "â€“": "–", "â€”": "—",
        "Ã ": "à", "Ã¨": "è", "Ã©": "é", "Ã¬": "ì", "Ã²": "ò", "Ã¹": "ù",
        "Â°": "°", "Â§": "§", "Â±": "±", "Â€": "€",
    }
    for r in rows:
        for k in ("question", "answer", "tags"):
            t = r[k]
            for bad, good in fixes.items():
                t = t.replace(bad, good)
            r[k] = t

    return rows

OV_ITEMS: List[Dict[str, Any]] = load_json(OV_JSON, [])
CMP_ITEMS: List[Dict[str, Any]] = load_json(CMP_JSON, [])
FAQ_ITEMS: List[Dict[str, str]] = load_faq_csv(FAQ_CSV)

JSON_BAG = {
    "overviews": OV_ITEMS,
    "compare": CMP_ITEMS,
    "faq": FAQ_ITEMS,
}
FAQ_ROWS = len(FAQ_ITEMS)

# -------------------------------
# Indici + euristiche
# -------------------------------
FAQ_BY_LANG: Dict[str, List[Dict[str, str]]] = {}
for r in FAQ_ITEMS:
    FAQ_BY_LANG.setdefault(r["lang"], []).append(r)

def detect_lang(q: str) -> str:
    s = (q or "").lower()
    if any(w in s for w in [" the ", " what ", " how ", " can ", " shall ", " should "]): return "en"
    if any(w in s for w in [" el ", " los ", " las ", "¿", "qué", "como", "cómo"]): return "es"
    if any(w in s for w in [" le ", " la ", " les ", " quelle", " comment"]): return "fr"
    if any(w in s for w in [" der ", " die ", " das ", " wie ", " was "]): return "de"
    return "it"

FAM_TOKENS: Dict[str, List[str]] = {
    "CTF":   ["ctf","lamiera","p560","hsbr14","trave","chiodatrice","sparo"],
    "CTL":   ["ctl","soletta","calcestruzzo","collaborazione","legno"],
    "VCEM":  ["vcem","preforo","vite","legno","essenze","durezza","hardwood","predrill","pilot","70","80"],
    "CEM-E": ["ceme","laterocemento","secco","senza resine","dry"],
    "CTCEM": ["ctcem","laterocemento","secco","senza resine"],
    "GTS":   ["gts","manicotto","filettato","giunzioni","secco","sleeve","threaded"],
    "P560":  ["p560","chiodatrice","propulsori","hsbr14","nailer","cartridges"],
}

def _score_tokens(text: str, tokens: List[str]) -> float:
    t = (" " + (text or "").lower() + " ")
    hits = sum(1 for tok in tokens if tok in t)
    return hits / max(1, len(tokens))

def _find_overview(fam: str) -> str:
    fam = (fam or "").upper()
    for it in OV_ITEMS:
        if (it.get("family") or "").upper() == fam:
            return (it.get("answer") or "").strip()
    return f"{fam}: descrizione, ambiti applicativi, posa, controlli e riferimenti."

def _compare_html(famA: str, famB: str, ansA: str, ansB: str) -> str:
    return (
        "<div><h2>Confronto</h2>"
        "<div style='display:flex;gap:24px;flex-wrap:wrap'>"
        f"<div class='side' style='flex:1;min-width:320px'><h3>{famA}</h3><p>{ansA}</p>"
        f"<p><small>Fonte: <b>OVERVIEW::{famA}</b></small></p></div>"
        f"<div class='side' style='flex:1;min-width:320px'><h3>{famB}</h3><p>{ansB}</p>"
        f"<p><small>Fonte: <b>OVERVIEW::{famB}</b></small></p></div>"
        "</div></div>"
    )

def intent_route(q: str) -> Dict[str, Any]:
    ql = (q or "").lower().strip()
    lang = detect_lang(ql)

    # 1) Confronti
    fams = list(FAM_TOKENS.keys())
    for a in fams:
        for b in fams:
            if a >= b:  # evita duplicati e auto-confronti
                continue
            if a.lower() in ql and b.lower() in ql:
                found = None
                for it in CMP_ITEMS:
                    fa = (it.get("famA") or "").upper()
                    fb = (it.get("famB") or "").upper()
                    if {fa, fb} == {a, b}:
                        found = it
                        break
                if found:
                    html = found.get("html") or ""
                    text = found.get("answer") or ""
                else:
                    ansA = _find_overview(a)
                    ansB = _find_overview(b)
                    html = _compare_html(a, b, ansA, ansB)
                    text = ""
                return {
                    "ok": True, "match_id": f"COMPARE::{a}_VS_{b}", "lang": lang,
                    "family": f"{a}+{b}", "intent": "compare",
                    "source": "compare" if found else "synthetic", "score": 92.0,
                    "text": text, "html": html,
                }

    # 2) Famiglia singola
    scored = [(fam, _score_tokens(ql, toks)) for fam, toks in FAM_TOKENS.items()]
    scored.sort(key=lambda x: x[1], reverse=True)
    fam, s = scored[0]
    if s >= 0.2:
        # 2a) FAQ
        for r in FAQ_BY_LANG.get(lang, []):
            keys = (r["tags"] or "") + " " + r["question"]
            if _score_tokens(ql, re.split(r"[,\s;/\-]+", keys.lower())) >= 0.25:
                return {
                    "ok": True, "match_id": r["id"] or f"FAQ::{fam}", "lang": lang,
                    "family": fam, "intent": "faq", "source": "faq", "score": 88.0,
                    "text": r["answer"], "html": ""
                }
        # 2b) overview
        ov = _find_overview(fam)
        return {
            "ok": True, "match_id": f"OVERVIEW::{fam}", "lang": lang,
            "family": fam, "intent": "overview", "source": "overview", "score": 75.0,
            "text": ov, "html": ""
        }

    # 3) Fallback
    return {
        "ok": True, "match_id": "<NULL>", "lang": lang,
        "family": "", "intent": "fallback", "source": "fallback", "score": 0,
        "text": "Non ho trovato una risposta diretta nei metadati locali. Specifica meglio la famiglia/prodotto.",
        "html": ""
    }

# -------------------------------
# Schemi I/O
# -------------------------------
class AskIn(BaseModel):
    q: str

class AskOut(BaseModel):
    ok: bool
    match_id: str
    ms: int
    text: str | None = ""
    html: str | None = ""
    lang: str | None = None
    family: str | None = None
    intent: str | None = None
    source: str | None = None
    score: float | int | None = None

# -------------------------------
# Endpoints service
# -------------------------------
@app.get("/")
def _root():
    try:
        return {
            "app": "Tecnaria_V3 (online)",
            "status": "ok",
            "data_dir": str(DATA_DIR),
            "json_loaded": list(JSON_BAG.keys()),
            "faq_rows": FAQ_ROWS
        }
    except Exception:
        return {"app": "Tecnaria_V3 (online)", "status": "ok"}

@app.get("/health")
def _health():
    return {
        "ok": True,
        "json_loaded": list(JSON_BAG.keys()),
        "faq_rows": FAQ_ROWS
    }

# -------------------------------
# API ask (POST e GET)
# -------------------------------
@app.post("/api/ask", response_model=AskOut)
def api_ask_post(body: AskIn) -> AskOut:
    t0 = time.time()
    routed = intent_route(body.q or "")
    ms = int((time.time() - t0) * 1000)
    return AskOut(
        ok=True,
        match_id=str(routed.get("match_id") or "<NULL>"),
        ms=ms if ms > 0 else 1,
        text=str(routed.get("text") or ""),
        html=str(routed.get("html") or ""),
        lang=routed.get("lang"),
        family=routed.get("family"),
        intent=routed.get("intent"),
        source=routed.get("source"),
        score=routed.get("score"),
    )

@app.get("/api/ask", response_model=AskOut)
def api_ask_get(q: str = Query("", description="Domanda")) -> AskOut:
    t0 = time.time()
    routed = intent_route(q or "")
    ms = int((time.time() - t0) * 1000)
    return AskOut(
        ok=True,
        match_id=str(routed.get("match_id") or "<NULL>"),
        ms=ms if ms > 0 else 1,
        text=str(routed.get("text") or ""),
        html=str(routed.get("html") or ""),
        lang=routed.get("lang"),
        family=routed.get("family"),
        intent=routed.get("intent"),
        source=routed.get("source"),
        score=routed.get("score"),
    )

# -------------------------------
# UI (HTML)
# -------------------------------
UI_HTML = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Tecnaria • Q&A</title>
<style>
  :root{
    --bg:#0f0f12; --card:#15161b; --muted:#8b8ea3; --text:#e9eaf0;
    --brand:#ff6b00; --brand2:#ffa149; --ok:#17c964; --warn:#f5a524;
  }
  *{box-sizing:border-box} body{margin:0;background:linear-gradient(180deg,#0d0e12,#141620 40%,#0d0e12);
  font-family:Inter,system-ui,Segoe UI,Roboto,Arial,sans-serif;color:var(--text)}
  .wrap{max-width:980px;margin:0 auto;padding:24px}
  header{display:flex;align-items:center;gap:12px;margin:8px 0 16px}
  .logo{width:40px;height:40px;border-radius:12px;background:linear-gradient(135deg,var(--brand),var(--brand2))}
  h1{font-size:20px;margin:0}
  .card{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.06);border-radius:16px;
        box-shadow:0 10px 30px rgba(0,0,0,.35)}
  .ask{padding:18px;display:flex;gap:10px;flex-wrap:wrap}
  input[type=text]{flex:1;min-width:220px;padding:14px 16px;border-radius:12px;background:#0f1116;
    border:1px solid #2a2d39;color:var(--text);font-size:16px;outline:none}
  button{padding:14px 18px;border-radius:12px;border:none;font-weight:600;cursor:pointer}
  .b1{background:linear-gradient(90deg,var(--brand),var(--brand2));color:#111}
  .b2{background:#222635;color:#d6d8e4;border:1px solid #2a2d39}
  .row{display:flex;gap:16px;flex-wrap:wrap;padding:18px}
  .col{flex:1;min-width:280px}
  .pill{display:inline-block;padding:4px 10px;border-radius:999px;background:#1b1e29;border:1px solid #2a2d39;color:#cfd1dc;font-size:12px}
  .pre{white-space:pre-wrap;word-wrap:break-word;background:#0c0e13;border:1px solid #22263a;border-radius:12px;padding:12px}
  footer{opacity:.6;font-size:12px;margin-top:10px}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo"></div>
    <h1>Tecnaria • Q&A</h1>
  </header>

  <div class="card">
    <div class="ask">
      <input id="q" type="text" placeholder="Fai una domanda (es. 'Differenza tra CTF e CTL?')" />
      <button class="b1" onclick="ask()">Chiedi</button>
      <button class="b2" onclick="demo()">Esempi</button>
    </div>
    <div class="row">
      <div class="col">
        <div class="pill">Testo</div>
        <div id="text" class="pre" style="min-height:120px"></div>
      </div>
      <div class="col">
        <div class="pill">HTML</div>
        <div id="html" class="pre" style="min-height:120px"></div>
      </div>
    </div>
    <div class="row" style="border-top:1px solid #212433">
      <div class="col">
        <div class="pill">Meta</div>
        <div id="meta" class="pre"></div>
      </div>
    </div>
  </div>

  <footer>Stato: <span id="status">pronto</span></footer>
</div>

<script>
const statusEl = document.getElementById('status');
const qEl = document.getElementById('q');
const textEl = document.getElementById('text');
const htmlEl = document.getElementById('html');
const metaEl = document.getElementById('meta');

async function ask() {
  const q = qEl.value.trim();
  if(!q){ qEl.focus(); return; }
  statusEl.textContent = 'invio…';
  try{
    const r = await fetch('/api/ask', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ q })
    });
    const j = await r.json();
    textEl.textContent = j.text || '';
    htmlEl.innerHTML = j.html || '';
    metaEl.textContent = JSON.stringify({
      ok:j.ok, match_id:j.match_id, lang:j.lang, family:j.family,
      intent:j.intent, source:j.source, ms:j.ms, score:j.score
    }, null, 2);
    statusEl.textContent = 'ok';
  }catch(e){
    statusEl.textContent = 'errore';
    metaEl.textContent = String(e);
  }
}

function demo(){
  const samples = [
    "Differenza tra CTF e CTL?",
    "Quando scegliere CTL invece di CEM-E?",
    "CTF su lamiera grecata: controlli in cantiere?",
    "VCEM su essenze dure: serve preforo 70–80%?",
    "GTS: che cos’è e come si usa?",
    "P560: è un connettore o un'attrezzatura?"
  ];
  qEl.value = samples[Math.floor(Math.random()*samples.length)];
  ask();
}

window.addEventListener('keydown', (ev)=>{
  if(ev.key === 'Enter') ask();
});
</script>
</body>
</html>
"""

@app.get("/ui", response_class=HTMLResponse)
def ui_page():
    return HTMLResponse(content=UI_HTML)

# -------------------------------
# Debug: elenco rotte
# -------------------------------
@app.get("/__routes")
def __routes():
    return JSONResponse({
        "routes": [
            {"path": r.path, "name": r.name, "methods": list(getattr(r, "methods", []) or [])}
            for r in app.routes
        ]
    })
