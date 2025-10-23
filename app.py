# app.py — Tecnaria_V3 con UI web (/ui) pronta per Render

from typing import List, Dict, Any
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import time, re, csv, json

# -------------------------------------------------
# FastAPI
# -------------------------------------------------
app = FastAPI(title="Tecnaria_V3")

# CORS (aperto per semplicità; restringi se vuoi)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------
# Dati (cartella: static/data)
# -------------------------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "static" / "data"
OV_JSON = DATA_DIR / "tecnaria_overviews.json"   # panoramiche famiglie
CMP_JSON = DATA_DIR / "tecnaria_compare.json"    # confronti A vs B
FAQ_CSV = DATA_DIR / "faq.csv"                   # domande/risposte brevi multi-lingua

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

    # normalizza artefatti comuni (— ’ … accenti, euro, ecc.)
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

# Contatori esposti
JSON_BAG = {
    "overviews": OV_ITEMS,
    "compare": CMP_ITEMS,
    "faq": FAQ_ITEMS,
}
FAQ_ROWS = len(FAQ_ITEMS)

# -------------------------------------------------
# Indici + euristiche
# -------------------------------------------------
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

# Token famiglie (senza “traliccio/tralicciati” — NON Tecnaria)
FAM_TOKENS: Dict[str, List[str]] = {
    "CTF":   ["ctf","lamiera","p560","hsbr14","trave","chiodatrice","sparo"],
    "CTL":   ["ctl","soletta","calcestruzzo","collaborazione","legno"],
    "VCEM":  ["vcem","preforo","vite","legno","essenze","durezza","hardwood","predrill","pilot","70","80"],
    "CEM-E": ["ceme","laterocemento","secco","senza resine","cappello"],
    "CTCEM": ["ctcem","laterocemento","secco","senza resine","cappa"],
    "GTS":   ["gts","manicotto","filettato","giunzioni","secco","sleeve","threaded"],
    "P560":  ["p560","chiodatrice","propulsori","hsbr14","nailer"],
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

    # 1) Confronti A vs B
    fams = list(FAM_TOKENS.keys())
    for a in fams:
        for b in fams:
            if a >= b:
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
                    "ok": True,
                    "match_id": f"COMPARE::{a}_VS_{b}",
                    "lang": lang,
                    "family": f"{a}+{b}",
                    "intent": "compare",
                    "source": "compare" if found else "synthetic",
                    "score": 92.0,
                    "text": text,
                    "html": html,
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
        return {
            "ok": True,
            "json_loaded": list(JSON_BAG.keys()),
            "faq_rows": FAQ_ROWS
        }
    except Exception:
        return {"ok": True}

# -------------------------------------------------
# /api/ask (locale)
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
def api_ask_local(body: AskIn) -> AskOut:
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

# -------------------------------------------------
# UI Web (responsive) su /ui
# -------------------------------------------------
UI_HTML = r"""<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Tecnaria · Assistant</title>
  <style>
    :root{
      --bg:#0d0d0f; --card:#141418; --muted:#8a8ea3; --text:#f4f6ff;
      --brand:#ff6a00; --brand-2:#ffa000; --ok:#32d583; --err:#ff4d4f;
    }
    *{box-sizing:border-box}
    body{margin:0;background:linear-gradient(180deg,#0b0b0d 0%,#111218 100%);color:var(--text);font:16px/1.5 system-ui,Segoe UI,Roboto,Helvetica,Arial}
    .wrap{max-width:980px;margin:32px auto;padding:0 16px}
    header{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
    .logo{display:flex;gap:10px;align-items:center}
    .dot{width:10px;height:10px;border-radius:50%;background:var(--brand);box-shadow:0 0 16px var(--brand)}
    .title{font-size:20px;font-weight:700;letter-spacing:.4px}
    .chip{font-size:12px;color:#101114;background:linear-gradient(90deg,var(--brand),var(--brand-2));padding:4px 10px;border-radius:999px;font-weight:700}
    .card{background:rgba(255,255,255,.04);backdrop-filter:blur(6px);border:1px solid rgba(255,255,255,.08);
      border-radius:16px; padding:16px}
    .row{display:flex;gap:12px}
    .row.stack{flex-direction:column}
    @media (max-width:700px){ .row{flex-direction:column} }
    input[type=text]{flex:1;border:1px solid rgba(255,255,255,.12);background:#0f1117;color:var(--text);
      padding:14px 14px;border-radius:12px;outline:none}
    button{border:0;border-radius:12px;padding:14px 16px;font-weight:700;cursor:pointer}
    .primary{background:linear-gradient(90deg,var(--brand),var(--brand-2));color:#101114}
    .ghost{background:#0f1117;color:var(--muted);border:1px solid rgba(255,255,255,.08)}
    .status{display:flex;gap:8px;align-items:center;color:var(--muted);font-size:13px;margin:8px 0 16px}
    .pill{padding:4px 8px;border-radius:999px;border:1px solid rgba(255,255,255,.12);color:var(--muted);font-size:12px}
    .out{min-height:160px}
    .msg{border-left:3px solid var(--brand);padding-left:12px}
    .meta{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
    .meta .kv{padding:4px 8px;border-radius:8px;background:#0f1117;border:1px solid rgba(255,255,255,.08);font-size:12px;color:var(--muted)}
    .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:10px}
    @media (max-width:700px){ .grid{grid-template-columns:1fr} }
    .sample{border:1px dashed rgba(255,255,255,.15);border-radius:12px;padding:10px;cursor:pointer;color:var(--muted)}
    a{color:inherit}
    .footer{margin-top:14px;color:var(--muted);font-size:12px;text-align:right}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div class="logo">
        <div class="dot"></div>
        <div class="title">Tecnaria · Assistant</div>
      </div>
      <div class="chip">LIVE</div>
    </header>

    <div class="card">
      <div class="status">
        <div class="pill" id="health-pill">checking…</div>
        <div id="health-info"></div>
      </div>

      <div class="row">
        <input id="q" type="text" placeholder="Scrivi una domanda (es. ‘Differenza tra CTF e CTL?’)" />
        <button class="ghost" id="micBtn" title="microfono (opzionale/placeholder)">🎤</button>
        <button class="primary" id="sendBtn">Chiedi</button>
      </div>

      <div class="grid">
        <div class="sample" data-q="Differenza tra CTF e CTL?">Differenza tra CTF e CTL?</div>
        <div class="sample" data-q="P560: è un connettore o un'attrezzatura?">P560: connettore o attrezzatura?</div>
        <div class="sample" data-q="VCEM su essenze dure: serve preforo 70–80%?">VCEM su essenze dure</div>
      </div>

      <div class="out" id="out" style="margin-top:14px"></div>
      <div class="footer">UI v1 · arancio/nero · responsive</div>
    </div>
  </div>

<script>
const BASE = location.origin; // stesso dominio Render
const $ = (s)=>document.querySelector(s);
const out = $("#out");
const qEl = $("#q");

// health ping
(async ()=>{
  try{
    const r = await fetch(BASE + "/health");
    const j = await r.json();
    $("#health-pill").textContent = j.ok ? "OK" : "KO";
    $("#health-pill").style.borderColor = j.ok ? "rgba(50,213,131,.5)" : "rgba(255,77,79,.5)";
    $("#health-info").textContent = `json: ${ (j.json_loaded||[]).join(", ") } • faq: ${ j.faq_rows ?? 0 }`;
  }catch(e){
    $("#health-pill").textContent = "KO";
    $("#health-info").textContent = "server non raggiungibile";
  }
})();

async function ask(q){
  if(!q || !q.trim()) return;
  out.innerHTML = `<div class="msg">⏳ Elaboro…</div>`;
  try{
    const r = await fetch(BASE + "/api/ask", {
      method:"POST",
      headers:{ "Content-Type":"application/json; charset=utf-8" },
      body: JSON.stringify({ q })
    });
    const j = await r.json();
    const meta = `
      <div class="meta">
        <div class="kv">match_id: ${j.match_id||""}</div>
        <div class="kv">intent: ${j.intent||""}</div>
        <div class="kv">family: ${j.family||""}</div>
        <div class="kv">lang: ${j.lang||""}</div>
        <div class="kv">ms: ${j.ms||""}</div>
      </div>`;
    if (j.html && j.html.trim()){
      out.innerHTML = j.html + meta;
    } else {
      const safe = (j.text||"").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/\n/g,"<br>");
      out.innerHTML = `<div class="msg">${safe||"—"}</div>` + meta;
    }
  }catch(e){
    out.innerHTML = `<div class="msg" style="border-left-color:#ff4d4f">Errore: ${e}</div>`;
  }
}

$("#sendBtn").addEventListener("click", ()=> ask(qEl.value));
qEl.addEventListener("keydown", (ev)=>{ if(ev.key==="Enter") ask(qEl.value); });
document.querySelectorAll(".sample").forEach(el=>{
  el.addEventListener("click", ()=>{ qEl.value = el.dataset.q; ask(el.dataset.q); });
});
$("#micBtn").addEventListener("click", ()=> alert("Microfono opzionale (placeholder)"));
</script>
</body>
</html>
"""

@app.get("/ui", response_class=HTMLResponse)
def ui_page():
    return HTMLResponse(UI_HTML, status_code=200)
