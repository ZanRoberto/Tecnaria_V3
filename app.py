# app.py — Tecnaria_V3 (FastAPI) — build fix UI + compare
from __future__ import annotations

from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
import time, re, csv, json

app = FastAPI(title="Tecnaria_V3")

# -----------------------------
# Dati locali (static/data)
# -----------------------------
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
                    "lang": ((r.get("lang") or "").strip().lower()) or "it",
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

JSON_BAG = {"overviews": OV_ITEMS, "compare": CMP_ITEMS, "faq": FAQ_ITEMS}
FAQ_ROWS = len(FAQ_ITEMS)

# Indice per lingua
FAQ_BY_LANG: Dict[str, List[Dict[str, str]]] = {}
for r in FAQ_ITEMS:
    FAQ_BY_LANG.setdefault(r["lang"], []).append(r)

# -----------------------------
# Rilevamento lingua (euristico)
# -----------------------------
_LANG_PATTERNS = {
    "en": [r"\bwhat\b", r"\bhow\b", r"\bcan\b", r"\bshould\b", r"\bconnector(s)?\b"],
    "es": [r"¿", r"\bqué\b", r"\bcómo\b", r"\bconector(es)?\b"],
    "fr": [r"\bquoi\b", r"\bcomment\b", r"\bquel(le|s)?\b", r"\bconnecteur(s)?\b"],
    "de": [r"\bwas\b", r"\bwie\b", r"\bverbinder\b"],
}
def detect_lang(q: str) -> str:
    s = (q or "").lower()
    for lang, pats in _LANG_PATTERNS.items():
        for p in pats:
            if re.search(p, s):
                return lang
    if "¿" in s or "¡" in s:
        return "es"
    return "it"

# -----------------------------
# Token famiglie (multilingua)
# -----------------------------
FAM_TOKENS: Dict[str, List[str]] = {
    "CTF": [
        "ctf","connector","connectors","connecteur","verbinder",
        "lamiera","trave","chiodatrice","sparo",
        "deck","beam","nailer","powder","cartridge",
        "bac","poutre","cloueur","chapas","viga","nagler",
        "acciaio","lamiera grecata"
    ],
    "CTL": ["ctl","soletta","calcestruzzo","collaborazione","legno","timber","concrete","composito","trave legno","maxi"],
    "VCEM": ["vcem","preforo","predrill","pre-drill","pilot","hardwood","essenze","durezza","70","80","laterocemento"],
    "CEM-E": ["ceme","cem-e","laterocemento","dry","secco","senza","resine","cappello","posa a secco"],
    "CTCEM": ["ctcem","laterocemento","dry","secco","senza","resine","cappa","malta","foro 11"],
    "GTS": ["gts","manicotto","filettato","giunzioni","secco","threaded","sleeve","joint","barra"],
    "P560": [
        "p560","spit","spit p560","spit-p560",
        "chiodatrice","pistola","utensile","attrezzatura","propulsori","propulsore",
        "cartucce","cartuccia","gialle","verdi","rosse","dosaggio","regolazione",
        "chiodi","chiodo","hsbr14","hsbr 14","adattatore","kit","spari","sparo","sicura",
        "powder","powder-actuated","pat","nailer","nailgun","cartridge","magazine",
        "gerät","nagler","werkzeug","outil","cloueur","herramienta","clavos",
        "acciaio","trave","lamiera","lamiera grecata","deck","beam","steel","eta"
    ],
}

def detect_family(text: str) -> Tuple[str, int]:
    t = " " + (text or "").lower() + " "
    best_fam, best_hits = "", 0
    for fam, toks in FAM_TOKENS.items():
        hits = 0
        if fam.lower() in t:
            hits += 2
        for tok in toks:
            tok = (tok or "").strip().lower()
            if tok and tok in t:
                hits += 1
        if hits > best_hits:
            best_fam, best_hits = fam, hits
    return best_fam, best_hits

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

# -----------------------------
# Intent router
# -----------------------------
def intent_route(q: str) -> Dict[str, Any]:
    ql = (q or "").lower().strip()
    lang = detect_lang(ql)

    # 1) Confronti A vs B (se compaiono entrambi i token famiglia)
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
    fam, hits = detect_family(ql)
    if hits >= 1:
        # 2a) FAQ prima nella lingua rilevata, poi cross-lingua
        best_row: Optional[Dict[str, str]] = None
        best_score: int = -1

        def try_rows(rows: List[Dict[str, str]]):
            nonlocal best_row, best_score
            for r in rows:
                keys = ((r.get("tags") or "") + " " + (r.get("question") or "")).lower()
                score = 0
                for tok in re.split(r"[,\s;/\-]+", keys):
                    tok = tok.strip()
                    if tok and tok in ql:
                        score += 1
                if score > best_score:
                    best_score, best_row = score, r

        try_rows(FAQ_BY_LANG.get(lang, []))
        if best_row is None or best_score <= 0:
            try_rows(FAQ_ITEMS)

        if best_row:
            return {
                "ok": True,
                "match_id": best_row.get("id") or f"FAQ::{fam}",
                "lang": lang,
                "family": fam,
                "intent": "faq",
                "source": "faq",
                "score": 90.0 if hits >= 2 else 82.0,
                "text": best_row.get("answer") or "",
                "html": ""
            }

        # 2b) overview di famiglia
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

# -----------------------------
# API principale
# -----------------------------
class AskIn(BaseModel):
    q: str

class AskOut(BaseModel):
    ok: bool
    match_id: str
    ms: int
    text: Optional[str] = ""
    html: Optional[str] = ""
    lang: Optional[str] = None
    family: Optional[str] = None
    intent: Optional[str] = None
    source: Optional[str] = None
    score: Optional[float] = None

@app.get("/api/ask", response_model=AskOut)
def api_ask_get(q: str = Query(default="", description="Domanda")) -> AskOut:
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

# -----------------------------
# Endpoints di servizio + UI
# -----------------------------
@app.get("/health")
def _health():
    try:
        return {"ok": True, "json_loaded": list(JSON_BAG.keys()), "faq_rows": FAQ_ROWS}
    except Exception:
        return {"ok": True}

@app.get("/ui")
def _ui_json():
    samples = [
        "Differenza tra CTF e CTL?",
        "Quando scegliere CTL invece di CEM-E?",
        "Differenza tra CEM-E e CTCEM?",
        "CTF su lamiera grecata: controlli in cantiere?",
        "VCEM su essenze dure: serve preforo 70–80%?",
        "GTS: che cos’è e come si usa?",
        "P560: è un connettore o un'attrezzatura?",
        "CEM-E: è una posa a secco?",
        "CTCEM: quando preferirlo alle resine?",
        "VCEM on hardwoods: is predrilling required?",
        "What are Tecnaria CTF connectors?",
        "Can I install CTF with any powder-actuated tool?",
        "Que sont les connecteurs CTF Tecnaria ?",
        "¿Qué son los conectores CTF de Tecnaria?",
        "Was sind Tecnaria CTF-Verbinder?",
    ]
    return JSONResponse({
        "title": "Tecnaria_V3 — UI minima",
        "how_to": "Usa GET /api/ask?q=... oppure POST /api/ask con body { q: \"...\" }",
        "samples": samples
    })

UI_HTML = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Tecnaria_V3</title>
<style>
body{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;background:#0c1f13;color:#eaf7ef;margin:0}
.wrap{max-width:980px;margin:0 auto;padding:24px}
h1{display:flex;align-items:center;gap:8px;font-size:22px;margin:0 0 16px}
.badge{width:10px;height:10px;border-radius:50%;background:#22c55e;display:inline-block}
.card{background:#12281a;border:1px solid #184a2d;border-radius:14px;padding:16px;margin:12px 0;box-shadow:0 1px 8px rgba(0,0,0,.2)}
input[type=text]{width:100%;padding:12px 14px;border-radius:10px;border:1px solid #215a38;background:#0f2418;color:#eaf7ef}
button{background:#22c55e;border:none;color:#07270f;padding:10px 14px;border-radius:10px;cursor:pointer;font-weight:600}
button:disabled{opacity:.6;cursor:not-allowed}
.small{opacity:.8;font-size:12px}
.row{display:flex;gap:12px;flex-wrap:wrap}
.col{flex:1;min-width:260px}
pre{white-space:pre-wrap;word-break:break-word}
.kv{font-size:12px;opacity:.85}
.sample{display:inline-block;background:#0f2418;border:1px solid #184a2d;color:#bff1cd;border-radius:999px;padding:6px 10px;margin:4px 6px 0 0;cursor:pointer}
footer{margin-top:24px;opacity:.7}
</style>
</head>
<body>
<div class="wrap">
  <h1><span class="badge"></span> Tecnaria_V3</h1>
  <div class="card">
    <div class="small">Scrivi la domanda e premi <b>Chiedi</b>. Oppure clicca un esempio.</div>
    <div class="row" style="margin-top:10px">
      <div class="col"><input id="q" type="text" placeholder="Esempio: Differenza tra CEM-E e CTCEM nella posa su laterocemento?"></div>
      <div><button id="askBtn">Chiedi</button></div>
    </div>
    <div id="samples" style="margin-top:6px"></div>
  </div>

  <div class="card">
    <div class="kv" id="meta"></div>
    <pre id="answer">—</pre>
    <div id="html"></div>
  </div>

  <footer class="small">UI locale — usa /api/ask lato server</footer>
</div>
<script>
const samples = [
  "Differenza tra CTF e CTL?",
  "Quando scegliere CTL invece di CEM-E?",
  "Differenza tra CEM-E e CTCEM?",
  "CTF su lamiera grecata: controlli in cantiere?",
  "P560: è un connettore o un'attrezzatura?"
];
const $s = document.getElementById("samples");
$s.innerHTML = samples.map(s => '<span class="sample" data-q="'+s.replace(/"/g,'&quot;')+'">'+s+'</span>').join('');

$s.addEventListener('click', (e) => {
  const t = e.target.closest('.sample');
  if (!t) return;
  document.getElementById('q').value = t.dataset.q || '';
  ask();
});

document.getElementById('askBtn').addEventListener('click', ask);
document.getElementById('q').addEventListener('keydown', (e) => { if(e.key==='Enter') ask(); });

async function ask(){
  const q = document.getElementById("q").value || "";
  const btn = document.getElementById("askBtn");
  btn.disabled = true; btn.textContent = "…";
  try{
    const r = await fetch("/api/ask?q=" + encodeURIComponent(q));
    const j = await r.json();
    document.getElementById("meta").textContent =
      "match_id: " + j.match_id + " | intent: " + j.intent + " | famiglia: " + (j.family||"") + " | lang: " + (j.lang||"") + " | ms: " + j.ms;
    document.getElementById("answer").textContent = j.text || "";
    document.getElementById("html").innerHTML = j.html || "";
  }catch(err){
    document.getElementById("answer").textContent = "Errore: " + err;
    document.getElementById("html").innerHTML = "";
  }finally{
    btn.disabled = false; btn.textContent = "Chiedi";
  }
}
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def ui_root():
    # redirectabile in futuro, ma qui serviamo direttamente l'HTML
    return HTMLResponse(UI_HTML)
