# app.py — Tecnaria_V3 con UI integrata e redirect dal root a /ui

from typing import List, Dict, Any
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
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

    # normalizza artefatti comuni
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

# Token famiglie
FAM_TOKENS: Dict[str, List[str]] = {
    "CTF":   ["ctf","lamiera","p560","hsbr14","trave","chiodatrice","sparo"],
    "CTL":   ["ctl","soletta","calcestruzzo","collaborazione","legno"],
    "VCEM":  ["vcem","preforo","vite","legno","essenze","durezza","hardwood","predrill","pre-drill","pilot","70","80"],
    "CEM-E": ["ceme","laterocemento","secco","senza resine","cappello","resin-free","dry"],
    "CTCEM": ["ctcem","laterocemento","secco","senza resine","cappa"],
    "GTS":   ["gts","manicotto","filettato","giunzioni","secco","threaded"],
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

    # — regola esplicita: domanda EN su VCEM/hardwoods -> FAQ::VCEM
    if "vcem" in ql and any(k in ql for k in ["hardwood","hardwoods","predrill","pre-drill","pilot","70","80"]):
        # trova la prima FAQ VCEM in EN, altrimenti usa qualsiasi VCEM
        for r in FAQ_BY_LANG.get("en", []):
            if "vcem" in (r.get("tags","") + " " + r.get("question","")).lower():
                return {"ok": True,"match_id": r.get("id") or "FAQ::VCEM","lang":"en","family":"VCEM","intent":"faq",
                        "source":"faq","score": 90.0,"text": r.get("answer",""),"html": ""}

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

    # 2) Famiglia singola (faq -> overview)
    scored = [(fam, _score_tokens(ql, toks)) for fam, toks in FAM_TOKENS.items()]
    scored.sort(key=lambda x: x[1], reverse=True)
    fam, s = scored[0]
    if s >= 0.2:
        for r in FAQ_BY_LANG.get(lang, []):
            keys = (r["tags"] or "") + " " + r["question"]
            if _score_tokens(ql, re.split(r"[,\s;/\-]+", keys.lower())) >= 0.25:
                return {
                    "ok": True, "match_id": r["id"] or f"FAQ::{fam}", "lang": lang,
                    "family": fam, "intent": "faq", "source": "faq", "score": 88.0,
                    "text": r["answer"], "html": ""
                }
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
# Endpoint di servizio (root -> redirect a /ui)
# -------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def _root():
    # se vuoi lasciare il JSON diagnostico, commenta la riga sotto e riporta il JSON
    return RedirectResponse(url="/ui", status_code=302)

@app.get("/health")
def _health():
    try:
        return {"ok": True, "json_loaded": list(JSON_BAG.keys()), "faq_rows": FAQ_ROWS}
    except Exception:
        return {"ok": True}

# comodo per capire cosa c'è registrato
@app.get("/__routes")
def __routes():
    routes = []
    for r in app.router.routes:
        try:
            routes.append({"path": r.path, "name": getattr(r, "name", None), "methods": list(r.methods or [])})
        except Exception:
            pass
    return JSONResponse(routes)

# -------------------------------------------------
# /api/ask
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
# UI (HTML singola pagina)
# -------------------------------------------------
@app.get("/ui", response_class=HTMLResponse)
def ui():
    html = """
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tecnaria • Q&A</title>
<style>
:root{
  --bg:#0b0f15; --card:#121826; --muted:#99a3b3; --text:#e9eef7;
  --accent:#ff7a00; --accent2:#fb4d3d; --ok:#19c37d;
}
*{box-sizing:border-box} body{margin:0;background:linear-gradient(180deg,#0b0f15,#0e1422 60%,#0b0f15);
font-family:Inter,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--text);}
.container{max-width:980px;margin:40px auto;padding:0 16px;}
.header{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.logo{width:40px;height:40px;border-radius:10px;background:linear-gradient(135deg,var(--accent),var(--accent2));}
.title h1{font-size:20px;margin:0} .title small{color:var(--muted)}
.card{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.06);backdrop-filter: blur(6px);
border-radius:16px;padding:16px;box-shadow:0 8px 30px rgba(0,0,0,.35);}
.row{display:flex;gap:12px;flex-wrap:wrap}
.input{flex:1;min-width:220px;background:#0f1524;border:1px solid #1f2a44;border-radius:12px;padding:12px 14px;color:var(--text)}
.btn{background:linear-gradient(135deg,var(--accent),var(--accent2));color:white;border:none;border-radius:12px;padding:12px 18px;font-weight:600;cursor:pointer}
.btn:disabled{opacity:.6;cursor:not-allowed}
.meta{display:flex;gap:12px;color:var(--muted);font-size:12px;margin-top:10px;flex-wrap:wrap}
.badge{background:#0f1524;border:1px solid #1f2a44;border-radius:999px;padding:6px 10px;color:#d6deeb}
.answer{margin-top:16px;background:#0f1422;border:1px solid #1c253f;border-radius:12px;padding:14px}
.answer h3{margin:0 0 8px 0;font-size:14px;color:#c0cadb}
pre{white-space:pre-wrap;word-wrap:break-word;}
.footer{margin-top:26px;color:var(--muted);font-size:12px;text-align:center}
@media (max-width:640px){ .input{width:100%} .row{flex-direction:column} }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="logo"></div>
    <div class="title">
      <h1>Tecnaria • Q&A</h1>
      <small>Interfaccia rapida su /api/ask – dati: compare/faq/overviews</small>
    </div>
  </div>

  <div class="card">
    <div class="row">
      <input id="q" class="input" placeholder="Scrivi la domanda… es: Differenza tra CTF e CTL?">
      <button id="go" class="btn">Chiedi</button>
    </div>
    <div class="meta">
      <span class="badge" id="status">Pronto</span>
      <span class="badge" id="timing"></span>
      <span class="badge" id="match"></span>
    </div>
    <div class="answer" id="answer" style="display:none">
      <h3>Risposta</h3>
      <div id="html"></div>
      <pre id="text"></pre>
      <div class="meta" id="meta2"></div>
    </div>
  </div>

  <div class="footer">© Tecnaria • demo UI</div>
</div>

<script>
async function ask(q){
  const t0 = performance.now();
  const r = await fetch("/api/ask", {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ q })
  });
  const data = await r.json();
  const ms = Math.max(1, Math.round(performance.now()-t0));
  return {data, ms};
}

const $ = (s)=>document.querySelector(s);
$("#go").addEventListener("click", async ()=>{
  const q = $("#q").value.trim();
  if(!q) return;
  $("#go").disabled = true; $("#status").textContent = "In corso…";
  try{
    const {data, ms} = await ask(q);
    $("#status").textContent = data.ok ? "OK" : "Errore";
    $("#timing").textContent = `~${ms} ms`;
    $("#match").textContent = data.match_id || "<NULL>";
    $("#answer").style.display = "block";
    $("#html").innerHTML = data.html || "";
    $("#text").textContent = data.text || "";
    $("#meta2").textContent = `intent=${data.intent || ""} • family=${data.family || ""} • lang=${data.lang || ""} • score=${data.score ?? ""}`;
  }catch(e){
    $("#status").textContent = "Errore";
    $("#answer").style.display = "block";
    $("#text").textContent = String(e);
  }finally{
    $("#go").disabled = false;
  }
});
document.addEventListener("keydown", (e)=>{ if(e.key==="Enter"){ $("#go").click(); }});
</script>
</body>
</html>
    """
    return HTMLResponse(content=html, status_code=200)
