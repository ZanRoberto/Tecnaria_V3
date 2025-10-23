# app.py — Tecnaria_V3 (backend + UI) — pronto per Render
from __future__ import annotations
from typing import List, Dict, Any
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel
import time, re, csv, json

# -------------------------------------------------
# FastAPI
# -------------------------------------------------
app = FastAPI(title="Tecnaria_V3")

# -------------------------------------------------
# Dati (cartella: static/data)
# -------------------------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "static" / "data"
OV_JSON = DATA_DIR / "tecnaria_overviews.json"   # panoramiche famiglie
CMP_JSON = DATA_DIR / "tecnaria_compare.json"    # confronti A vs B
FAQ_CSV = DATA_DIR / "faq.csv"                   # domande/risposte brevi multi-lingua

def load_json(path: Path, fallback: List[Dict[str, Any]] | None = None) -> List[Dict[str, Any]]:
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f) or []
                if isinstance(data, list):
                    return data
    except Exception:
        pass
    return fallback or []

# === CSV robusto (UTF-8/CP1252 + fix mojibake) ===
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
        _read("utf-8-sig")      # preferito (gestisce anche BOM)
    except Exception:
        try:
            _read("cp1252")     # fallback per file salvati in Windows
        except Exception:
            return rows

    # normalizza artefatti comuni (— ’ … accenti, ecc.)
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

JSON_BAG = {"overviews": OV_ITEMS, "compare": CMP_ITEMS, "faq": FAQ_ITEMS}
FAQ_ROWS = len(FAQ_ITEMS)

# -------------------------------------------------
# Indici + euristiche
# -------------------------------------------------
FAQ_BY_LANG: Dict[str, List[Dict[str, str]]] = {}
for r in FAQ_ITEMS:
    FAQ_BY_LANG.setdefault(r["lang"], []).append(r)

def detect_lang(q: str) -> str:
    s = (q or "").lower()
    if any(w in s for w in [" the ", " what ", " how ", " can ", " shall ", " should ", " required?"]): return "en"
    if any(w in s for w in [" el ", " los ", " las ", "¿", "qué", "como", "cómo"]): return "es"
    if any(w in s for w in [" le ", " la ", " les ", " quelle", " comment"]): return "fr"
    if any(w in s for w in [" der ", " die ", " das ", " wie ", " was "]): return "de"
    return "it"

# Token famiglie
FAM_TOKENS: Dict[str, List[str]] = {
    "CTF":   ["ctf","lamiera","p560","hsbr14","trave","chiodatrice","sparo"],
    "CTL":   ["ctl","soletta","calcestruzzo","collaborazione","legno"],
    "VCEM":  ["vcem","preforo","vite","legno","essenze","durezza","hardwood","predrill","pilot","70","80"],
    "CEM-E": ["ceme","laterocemento","secco","senza resine","cappello","hollow-block","resin-free"],
    "CTCEM": ["ctcem","laterocemento","secco","senza resine","cappa"],
    "GTS":   ["gts","manicotto","filettato","giunzioni","secco","threaded","sleeve"],
    "P560":  ["p560","chiodatrice","propulsori","hsbr14","nailer","tool"],
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

# -------------------------------------------------
# Intent router
# -------------------------------------------------
def intent_route(q: str) -> Dict[str, Any]:
    ql = (q or "").lower().strip()
    lang = detect_lang(ql)

    # Confronti A vs B
    fams = list(FAM_TOKENS.keys())
    for i, a in enumerate(fams):
        for b in fams[i+1:]:
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
                    ansA = _find_overview(a); ansB = _find_overview(b)
                    html = _compare_html(a, b, ansA, ansB); text = ""
                return {
                    "ok": True, "match_id": f"COMPARE::{a}_VS_{b}", "lang": lang,
                    "family": f"{a}+{b}", "intent": "compare",
                    "source": "compare" if found else "synthetic",
                    "score": 92.0, "text": text, "html": html,
                }

    # Famiglia singola -> FAQ o Overview
    scored = [(fam, _score_tokens(ql, toks)) for fam, toks in FAM_TOKENS.items()]
    scored.sort(key=lambda x: x[1], reverse=True)
    fam, s = scored[0]
    if s >= 0.2:
        # FAQ per lingua
        for r in FAQ_BY_LANG.get(lang, []):
            keys = (r["tags"] or "") + " " + r["question"]
            # tokenizzo keys per robustezza
            tokens = re.split(r"[,\s;/\-]+", keys.lower())
            if _score_tokens(ql, tokens) >= 0.25:
                return {
                    "ok": True, "match_id": r["id"] or f"FAQ::{fam}", "lang": lang,
                    "family": fam, "intent": "faq", "source": "faq", "score": 88.0,
                    "text": r["answer"], "html": ""
                }
        # Overview fallback
        ov = _find_overview(fam)
        return {
            "ok": True, "match_id": f"OVERVIEW::{fam}", "lang": lang,
            "family": fam, "intent": "overview", "source": "overview", "score": 75.0,
            "text": ov, "html": ""
        }

    # Fallback
    return {
        "ok": True, "match_id": "<NULL>", "lang": lang,
        "family": "", "intent": "fallback", "source": "fallback", "score": 0,
        "text": "Non ho trovato una risposta diretta nei metadati locali. Specifica meglio la famiglia/prodotto.",
        "html": ""
    }

# -------------------------------------------------
# Endpoint di servizio
# -------------------------------------------------
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
    try:
        return {"ok": True, "json_loaded": list(JSON_BAG.keys()), "faq_rows": FAQ_ROWS}
    except Exception:
        return {"ok": True}

# -------------------------------------------------
# /api/ask (POST + GET)
# -------------------------------------------------
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

@app.post("/api/ask", response_model=AskOut)
def api_ask_post(body: AskIn) -> AskOut:
    t0 = time.time()
    routed = intent_route(body.q or "")
    ms = max(1, int((time.time() - t0) * 1000))
    return AskOut(
        ok=True,
        match_id=str(routed.get("match_id") or "<NULL>"),
        ms=ms,
        text=str(routed.get("text") or ""),
        html=str(routed.get("html") or ""),
        lang=routed.get("lang"),
        family=routed.get("family"),
        intent=routed.get("intent"),
        source=routed.get("source"),
        score=routed.get("score"),
    )

@app.get("/api/ask", response_model=AskOut)
def api_ask_get(q: str = Query("", description="User question")) -> AskOut:
    t0 = time.time()
    routed = intent_route(q or "")
    ms = max(1, int((time.time() - t0) * 1000))
    return AskOut(
        ok=True,
        match_id=str(routed.get("match_id") or "<NULL>"),
        ms=ms,
        text=str(routed.get("text") or ""),
        html=str(routed.get("html") or ""),
        lang=routed.get("lang"),
        family=routed.get("family"),
        intent=routed.get("intent"),
        source=routed.get("source"),
        score=routed.get("score"),
    )

# -------------------------------------------------
# UI (SPA minimale, responsive, dark+orange)
# -------------------------------------------------
UI_HTML = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Tecnaria · Assistant</title>
<style>
:root{
  --bg:#0b0b0d; --card:#141418; --muted:#767b8a; --txt:#e9eef7; --brand:#ff7a00; --brand2:#ffb600; --ok:#27d17f;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:linear-gradient(135deg,#0b0b0d,#121217);color:var(--txt);font:500 16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,Inter,Arial}
.container{max-width:980px;margin:0 auto;padding:24px}
.header{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.badge{font-weight:700;letter-spacing:.06em;background:linear-gradient(90deg,var(--brand),var(--brand2));-webkit-background-clip:text;background-clip:text;color:transparent}
.card{background:rgba(20,20,24,.9);backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,.06);border-radius:18px;box-shadow:0 10px 30px rgba(0,0,0,.35)}
.box{padding:18px 18px}
h1{font-size:22px;margin:0}
input,button,textarea{font:inherit}
.row{display:flex;gap:12px;flex-wrap:wrap}
#q{flex:1;min-width:260px;padding:14px 16px;border-radius:14px;border:1px solid rgba(255,255,255,.08);background:#0e0e12;color:var(--txt);outline:none}
#q:focus{border-color:var(--brand)}
button{padding:14px 18px;border-radius:12px;border:1px solid rgba(255,255,255,.08);background:linear-gradient(90deg,var(--brand),var(--brand2));color:#111;font-weight:800;cursor:pointer}
button:disabled{opacity:.5;cursor:not-allowed}
meta{color:var(--muted);font-size:13px}
hr{border:none;border-top:1px solid rgba(255,255,255,.06);margin:10px 0}
#out{display:grid;gap:10px}
.kv{display:grid;grid-template-columns:140px 1fr;gap:8px}
.key{color:var(--muted)}
.val{color:var(--txt);word-break:break-word}
@media (max-width:640px){.kv{grid-template-columns:110px 1fr}}
small.ok{color:var(--ok);font-weight:700}
a{color:var(--brand2);text-decoration:none}
</style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div style="width:12px;height:12px;border-radius:50%;background:var(--ok)"></div>
      <div class="badge">TECNARIA · ASSISTANT</div>
      <div style="flex:1"></div>
      <a href="/">/status</a>
    </div>

    <div class="card box">
      <h1>Chiedi qualcosa sui prodotti Tecnaria</h1>
      <div class="row" style="margin-top:10px">
        <input id="q" placeholder="Es. Differenza tra CTF e CTL?" />
        <button id="go">Chiedi</button>
      </div>
      <meta id="meta"></meta>
      <hr/>
      <div id="out"></div>
    </div>
  </div>

<script>
const $ = s => document.querySelector(s);
const out = $("#out"), meta = $("#meta"), go = $("#go"), q = $("#q");
const BASE = location.origin;

function render(res){
  out.innerHTML = "";
  const rows = [
    ["ok", String(res.ok)],
    ["match_id", res.match_id || ""],
    ["ms", String(res.ms||0)],
    ["intent", res.intent || ""],
    ["family", res.family || ""],
    ["lang", res.lang || ""],
    ["source", res.source || ""],
    ["score", String(res.score ?? "")],
    ["text", res.text || ""],
    ["html", res.html || ""]
  ];
  for (const [k,v] of rows){
    const line = document.createElement("div"); line.className="kv";
    const key = document.createElement("div"); key.className="key"; key.textContent=k;
    const val = document.createElement("div"); val.className="val";
    if (k==="html" && v){ val.innerHTML = v; } else { val.textContent = v; }
    line.appendChild(key); line.appendChild(val); out.appendChild(line);
  }
}

async function ask(){
  const txt = q.value.trim();
  if(!txt){ q.focus(); return; }
  go.disabled = true; meta.textContent = "Invio…";
  try{
    // POST preferito (UTF-8 pulito). Esiste anche GET /api/ask?q=...
    const r = await fetch(BASE + "/api/ask", {
      method:"POST",
      headers:{ "Content-Type":"application/json; charset=utf-8" },
      body: JSON.stringify({ q: txt })
    });
    const j = await r.json();
    render(j);
    meta.innerHTML = `<small class="ok">OK</small> — ${new Date().toLocaleTimeString()}`;
  }catch(e){
    meta.textContent = "Errore: " + e;
  }finally{
    go.disabled = false;
  }
}

go.addEventListener("click", ask);
q.addEventListener("keydown", e => { if(e.key==="Enter") ask(); });

// Esempio auto-fill
if (location.search.includes("demo=1")){
  q.value = "Differenza tra CTF e CTL?";
  ask();
}
</script>
</body>
</html>
"""

@app.get("/ui", response_class=HTMLResponse)
def ui_page():
    return HTMLResponse(UI_HTML, status_code=200)

# Shortcut: /?ui=1 apre la UI
@app.get("/app", response_class=HTMLResponse)
def app_page():
    return HTMLResponse(UI_HTML, status_code=200)

@app.get("/index.html", response_class=HTMLResponse)
def index_html():
    return HTMLResponse(UI_HTML, status_code=200)

@app.get("/favicon.ico")
def favicon():
    # favicon “vuoto” per evitare 404 nei log
    return PlainTextResponse("", status_code=204)
