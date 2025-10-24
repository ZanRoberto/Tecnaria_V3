# app.py — Tecnaria_V3 (FastAPI, UI embedded)
# -------------------------------------------
# Endpoints:
#   GET  /            -> info base JSON
#   GET  /health      -> stato + righe FAQ caricate
#   GET  /ui          -> interfaccia HTML integrata (no static)
#   GET  /api/ask?q=  -> risposta
#   POST /api/ask     -> body { q: "..." } -> risposta

from __future__ import annotations

from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import time, re, csv, json

app = FastAPI(title="Tecnaria_V3")

# -----------------------------
# Dati locali (static/data) – opzionali
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
    """Legge faq.csv con tolleranza (UTF-8/UTF-8-BOM/CP1252) e sistema mojibake comuni."""
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
        "lamiera","lamiera grecata","trave","acciaio",
        "deck","beam","steel",
        "chiodatrice","nailer","powder","cartridge","sparo","spari","cloueur","nagler",
        "bac","poutre","chapas","viga"
    ],
    "CTL": [
        "ctl","soletta","calcestruzzo","collaborazione","legno","timber","concrete","composito","trave legno",
        "maxi","micro","viti","vite","ø10","diametro 10"
    ],
    "VCEM": [
        "vcem","preforo","predrill","pre-drill","pilot","hardwood","essenze dure","durezza","70","80","laterocemento"
    ],
    "CEM-E": [
        "ceme","cem-e","laterocemento","dry","secco","senza","resine","cappello","posa a secco","cls","soletta"
    ],
    "CTCEM": [
        "ctcem","laterocemento","dry","secco","senza","resine","cappa","malta","piolo","preforo 11","percussione"
    ],
    "GTS": [
        "gts","manicotto","filettato","giunzioni","secco","threaded","sleeve","joint","barra","barra filettata"
    ],
    "P560": [
        # sigle / marchio / varianti
        "p560","spit","spit p560","spit-p560",
        # IT: utensile & concetti
        "chiodatrice","pistola","utensile","attrezzatura",
        "propulsori","propulsore","cartucce","cartuccia","dosaggio","regolazione",
        "gialle","verdi","rosse",
        "chiodi","chiodo","hsbr14","hsbr 14","adattatore","kit adattatore",
        "spari","sparo","colpo","tiro","sicura","marcatura","marcatura ce",
        # EN/DE/FR/ES
        "powder","powder-actuated","powder actuated","pat","nailer","nailgun",
        "cartridge","cartridges","mag","magazine","trigger","safety","tool",
        "gerät","nagler","werkzeug","outil","cloueur","outil à poudre","herramienta","clavos",
        # contesti
        "acciaio","trave","lamiera","lamiera grecata","deck","beam","steel","supporto","eta"
    ],
}

# Piccole regole “di buon senso” per domande frequenti (es. CTF vs P560)
def _forced_compare(ql: str) -> Optional[Tuple[str, str]]:
    s = " " + ql.lower() + " "
    # confronto CTF vs P560 quando si chiede se si possono usare "altre chiodatrici"
    if ("ctf" in s) and any(w in s for w in ["chiodatrice", "powder", "pistola", "pat", "nailgun"]):
        return ("CTF", "P560")
    return None

# Conteggio hit con boost acronimo
def detect_family(text: str) -> Tuple[str, int]:
    t = " " + (text or "").lower() + " "
    best_fam, best_hits = "", 0
    for fam, toks in FAM_TOKENS.items():
        hits = 0
        if fam.lower() in t:  # boost acronimo
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
    # testo sintetico di ripiego
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

    # 0) regole forzate (es. CTF vs P560)
    forced = _forced_compare(ql)
    if forced:
        a, b = forced
        ansA = _find_overview(a)
        ansB = _find_overview(b)
        html = _compare_html(a, b, ansA, ansB)
        return {
            "ok": True,
            "match_id": f"COMPARE::{a}_VS_{b}",
            "lang": lang,
            "family": f"{a}+{b}",
            "intent": "compare",
            "source": "synthetic",
            "score": 92.0,
            "text": "",
            "html": html,
        }

    # 1) Confronti A vs B se compaiono entrambi i token famiglia
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
        # 2a) FAQ – prima lingua rilevata, poi cross-lingua
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
# Endpoints di servizio
# -----------------------------
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

# -----------------------------
# UI embedded (no static)
# -----------------------------
_UI_HTML = r"""
<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <title>Tecnaria_V3 — Chatbot Tecnico</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root{
      --green:#2e7d32; --light:#eaf6ec; --text:#0b3010; --chip:#e9f5ea; --chip-h:#d7edd9;
      --card:#ffffff; --muted:#5a6b5d; --ring:#a5d6a7;
    }
    *{box-sizing:border-box;}
    body{margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,'Noto Sans',sans-serif;
         background:#eef6f0; color:var(--text);}
    .wrap{max-width:980px; margin:24px auto; padding:0 16px;}
    .bar{background:var(--green); color:#fff; padding:14px 18px; border-radius:12px;
         display:flex; align-items:center; gap:12px; box-shadow:0 2px 8px rgba(0,0,0,.15);}
    .dot{width:14px;height:14px;border-radius:50%;background:#69f382;box-shadow:0 0 0 3px rgba(255,255,255,.35) inset;}
    h1{font-size:22px;margin:0;}
    .card{background:var(--light); border:1px solid var(--ring); border-radius:14px; padding:12px 14px; margin-top:16px;}
    .row{display:flex; gap:10px; align-items:center; flex-wrap:wrap;}
    #q{width:100%; padding:14px; border:2px solid var(--ring); border-radius:10px; background:#fff; font-size:16px;}
    button{background:var(--green); color:#fff; border:0; padding:10px 16px; border-radius:10px; font-weight:600; cursor:pointer;}
    button:disabled{opacity:.6; cursor:not-allowed;}
    .chips{display:flex; flex-wrap:wrap; gap:10px; margin-top:12px;}
    .chip{background:var(--chip); border:1px solid var(--ring); padding:10px 12px; border-radius:999px; cursor:pointer;}
    .chip:hover{background:var(--chip-h);}
    .resp{background:#fff; border:1px solid var(--ring); border-radius:14px; padding:16px; margin-top:16px;}
    .tags{display:flex; gap:8px; flex-wrap:wrap; margin:8px 0 12px 0;}
    .tag{background:#eef7ef; border:1px solid var(--ring); color:#1b4d1e; padding:4px 8px; border-radius:999px; font-size:12px;}
    pre{white-space:pre-wrap; word-wrap:break-word; font-family:ui-monospace,Consolas,monospace;}
    small{color:var(--muted);}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="bar"><div class="dot"></div><h1>Tecnaria_V3 — Chatbot Tecnico</h1></div>

    <div class="card">
      <label for="q">Scrivi la tua domanda e premi <b>Chiedi</b>:</label>
      <textarea id="q" rows="4" placeholder="Es: Posso sparare i connettori CTF con una chiodatrice a polvere qualsiasi?"></textarea>
      <div class="row" style="margin-top:8px;">
        <button id="askBtn">Chiedi</button>
        <small id="stat"></small>
      </div>
      <div class="chips" id="chips"></div>
    </div>

    <div class="resp" id="resp"><small>Pronto.</small></div>
  </div>

<script>
const samples = [
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
  "Was sind Tecnaria CTF-Verbinder?"
];
const chipsEl = document.getElementById('chips');
samples.forEach(s=>{
  const c=document.createElement('div');
  c.className='chip'; c.textContent=s;
  c.onclick=()=>{ document.getElementById('q').value=s; ask(); };
  chipsEl.appendChild(c);
});

document.getElementById('askBtn').onclick=ask;
document.getElementById('q').addEventListener('keydown', (e)=>{
  if(e.key==='Enter' && (e.ctrlKey || e.metaKey)){ ask(); }
});

async function ask(){
  const q = document.getElementById('q').value.trim();
  if(!q){ return; }
  setStat('Attendere…'); setBtn(true);
  try{
    const r = await fetch('/api/ask?q='+encodeURIComponent(q));
    const js = await r.json();
    render(js);
  }catch(e){
    document.getElementById('resp').innerHTML='<small>Errore: '+(e&&e.message?e.message:'network')+'</small>';
  }finally{
    setBtn(false); setStat('');
  }
}
function setBtn(dis){ document.getElementById('askBtn').disabled=dis; }
function setStat(t){ document.getElementById('stat').textContent=t||''; }

function esc(s){ return (s||'').replace(/[&<>]/g, m=>({ '&':'&amp;','<':'&lt;','>':'&gt;' }[m])); }

function render(js){
  const meta = `
    <div class="tags">
      <span class="tag">match_id: ${esc(js.match_id||'')}</span>
      <span class="tag">intent: ${esc(js.intent||'')}</span>
      <span class="tag">famiglia: ${esc(js.family||'')}</span>
      <span class="tag">lang: ${esc(js.lang||'')}</span>
      <span class="tag">ms: ${esc(String(js.ms||''))}</span>
    </div>`;
  const text = js.text ? `<pre>${esc(js.text)}</pre>` : '';
  const html = js.html ? `<div>${js.html}</div>` : '';
  document.getElementById('resp').innerHTML = meta + text + html;
}
</script>
</body>
</html>
"""

@app.get("/ui", response_class=HTMLResponse)
def _ui():
    return HTMLResponse(content=_UI_HTML, status_code=200)

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

def _route_and_pack(q: str) -> AskOut:
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

@app.get("/api/ask", response_model=AskOut)
def api_ask_get(q: str = Query(default="", description="Domanda")) -> AskOut:
    return _route_and_pack(q)

@app.post("/api/ask", response_model=AskOut)
def api_ask_post(body: AskIn) -> AskOut:
    return _route_and_pack(body.q or "")
