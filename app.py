# app.py — Tecnaria_V3 (FastAPI) con UI incorporata
# -------------------------------------------------
# Endpoints:
#   GET  /            -> redirect a /ui
#   GET  /ui          -> interfaccia HTML (embedded)
#   GET  /health      -> stato + righe FAQ caricate
#   GET  /api/ask     -> risposta (stessa logica del POST)
#   POST /api/ask     -> body { q: "..." } -> risposta

from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
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

# Monta /static se presente (comodo per assets futuri)
STATIC_DIR = BASE_DIR / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

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
        _read("utf-8-sig")          # gestisce anche BOM
    except Exception:
        try:
            _read("cp1252")         # fallback tipico file salvati da Excel su Windows
        except Exception:
            return rows

    # Fix mojibake frequenti
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
    "CTL": ["ctl","soletta","calcestruzzo","collaborazione","legno","timber","concrete","composito","trave legno","maxi","ctl maxi"],
    "VCEM": ["vcem","preforo","predrill","pre-drill","pilot","hardwood","essenze","durezza","70","80","laterocemento"],
    "CEM-E": ["ceme","cem-e","laterocemento","dry","secco","senza","resine","cappello","posa a secco"],
    "CTCEM": ["ctcem","laterocemento","dry","secco","senza","resine","cappa","malta"],
    "GTS": ["gts","manicotto","filettato","giunzioni","secco","threaded","sleeve","joint","barra"],
    "P560": [
        # sigle / marchio / varianti
        "p560","spit","spit p560","spit-p560",
        # IT
        "chiodatrice","pistola","utensile","attrezzatura","propulsori","propulsore",
        "cartucce","cartuccia","gialle","verdi","rosse","dosaggio","regolazione potenza",
        "chiodi","chiodo","hsbr14","hsbr 14","adattatore","kit adattatore",
        "spari","sparo","colpo","tiro","sicura","marcatura","marcatura ce",
        # EN
        "powder","powder-actuated","powder actuated","pat","nailer","nailgun",
        "cartridge","cartridges","mag","magazine","trigger","safety","tool",
        # DE/FR/ES
        "gerät","nagler","werkzeug","outil","cloueur","outil à poudre","herramienta","clavos",
        # contesto
        "acciaio","trave","lamiera","lamiera grecata","deck","beam","steel",
        "supporto","supporti","spessori minimi","eta"
    ],
}

# Conteggio hit con boost acronimo
def detect_family(text: str) -> Tuple[str, int]:
    t = " " + (text or "").lower() + " "
    best_fam, best_hits = "", 0
    for fam, toks in FAM_TOKENS.items():
        hits = 0
        if fam.lower() in t:  # boost se compare l'acronimo
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

    # Normalizzazione minima per confronti ("differenza tra X e Y", "vs", "contro")
    qn = ql.replace(" vs ", " ").replace(" contro ", " ").replace("/", " ")

    # 1) Confronti A vs B (se compaiono entrambi i token famiglia)
    fams = list(FAM_TOKENS.keys())
    for a in fams:
        for b in fams:
            if a >= b:
                continue
            if a.lower() in qn and b.lower() in qn:
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
# UI incorporata
# -----------------------------
@app.get("/", include_in_schema=False)
def _redirect_home():
    return RedirectResponse(url="/ui")

@app.get("/ui", response_class=HTMLResponse)
def _ui_page():
    return HTMLResponse("""
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Tecnaria_V3 — UI</title>
<style>
  :root { --bg:#0e1f12; --card:#17351d; --accent:#2ecc71; --muted:#a6e3b7; --text:#e9ffef; }
  html,body{margin:0;padding:0;background:var(--bg);color:var(--text);font:16px/1.45 system-ui,Segoe UI,Roboto,Arial}
  .wrap{max-width:980px;margin:32px auto;padding:0 16px}
  .card{background:var(--card);border:1px solid #255a33;border-radius:14px;box-shadow:0 8px 24px rgba(0,0,0,.35)}
  header{display:flex;align-items:center;justify-content:space-between;padding:18px 20px}
  h1{margin:0;font-size:20px}
  .ok{display:inline-flex;gap:8px;align-items:center;color:var(--accent);font-weight:600}
  .grid{display:grid;grid-template-columns:1fr 360px;gap:16px;padding:16px}
  @media (max-width:900px){.grid{grid-template-columns:1fr}}
  .inputbar{display:flex;gap:10px}
  input[type=text]{flex:1;padding:12px 14px;border-radius:10px;border:1px solid #2a6b3c;background:#0f2415;color:var(--text)}
  button{padding:12px 16px;border-radius:10px;border:1px solid #2a6b3c;background:var(--accent);color:#0a140d;font-weight:700;cursor:pointer}
  button:disabled{opacity:.6;cursor:not-allowed}
  small.hint{color:var(--muted)}
  .examples{display:flex;flex-direction:column;gap:8px}
  .pill{display:inline-block;padding:8px 10px;border-radius:999px;background:#0f2415;border:1px solid #2a6b3c;color:var(--muted);cursor:pointer}
  .resp{padding:16px;border-top:1px solid #255a33}
  .meta{font-size:13px;color:var(--muted);margin-bottom:6px}
  pre{white-space:pre-wrap;word-break:break-word;background:#0f2415;border:1px solid #2a6b3c;border-radius:10px;padding:12px}
  a{color:var(--muted)}
</style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <header>
        <h1>🟢 Tecnaria_V3</h1>
        <div id="health" class="ok">checking…</div>
      </header>

      <div class="grid">
        <div>
          <div class="inputbar">
            <input id="q" type="text" placeholder="Scrivi la domanda (es. 'Differenza tra CEM-E e CTCEM su laterocemento?')"/>
            <button id="askBtn">Chiedi</button>
          </div>
          <small class="hint">Suggerimento: puoi anche usare <code>/api/ask?q=…</code> direttamente.</small>
          <div id="resp" class="resp">
            <div class="meta">Risposta</div>
            <pre id="out">(qui appare la risposta)</pre>
          </div>
        </div>

        <aside>
          <div class="resp">
            <div class="meta">Esempi rapidi</div>
            <div class="examples" id="exlist"></div>
          </div>
        </aside>
      </div>
    </div>
  </div>

<script>
const examples = [
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

async function pingHealth(){
  try{
    const r = await fetch('/health');
    const j = await r.json();
    document.getElementById('health').textContent = j.ok ? `ok • faq_rows=${j.faq_rows}` : 'errore';
  }catch(e){
    document.getElementById('health').textContent = 'errore';
  }
}

function renderExamples(){
  const box = document.getElementById('exlist');
  examples.forEach(t=>{
    const a = document.createElement('span');
    a.className='pill';
    a.textContent=t;
    a.onclick=()=>{ document.getElementById('q').value=t; ask(); };
    box.appendChild(a);
  });
}

async function ask(){
  const btn = document.getElementById('askBtn');
  const out = document.getElementById('out');
  const q = (document.getElementById('q').value||'').trim();
  if(!q){ out.textContent='(scrivi una domanda)'; return; }
  btn.disabled=true; out.textContent='…';
  try{
    const r = await fetch('/api/ask', {
      method:'POST',
      headers:{'content-type':'application/json'},
      body: JSON.stringify({ q })
    });
    const j = await r.json();
    const meta = `match_id: ${j.match_id}\nintent: ${j.intent} | famiglia: ${j.family} | lang: ${j.lang}\nms: ${j.ms}`;
    const txt = (j.text && j.text.trim()) ? j.text.trim() : '(nessun testo)';
    out.textContent = meta + "\\n\\n" + txt;
  }catch(e){
    out.textContent = 'Errore di rete o server.';
  }finally{
    btn.disabled=false;
  }
}

document.getElementById('askBtn').onclick = ask;
document.getElementById('q').addEventListener('keydown', (ev)=>{ if(ev.key==='Enter') ask(); });
renderExamples();
pingHealth();
</script>
</body>
</html>
""")

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

@app.get("/health")
def _health():
    try:
        return {"ok": True, "json_loaded": list(JSON_BAG.keys()), "faq_rows": FAQ_ROWS}
    except Exception:
        return {"ok": True}

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
