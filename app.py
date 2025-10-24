# app.py — Tecnaria_V3 (FastAPI) — versione one-file con UI integrata su /ui-html
from __future__ import annotations
from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import time, re, csv, json

app = FastAPI(title="Tecnaria_V3")

# -----------------------------
# Dati locali (static/data)
# -----------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "static" / "data"
OV_JSON = DATA_DIR / "tecnaria_overviews.json"
CMP_JSON = DATA_DIR / "tecnaria_compare.json"
FAQ_CSV = DATA_DIR / "faq.csv"

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
        "â€™":"’","â€œ":"“","â€\x9d":"”","â€“":"–","â€”":"—",
        "Ã ":"à","Ã¨":"è","Ã©":"é","Ã¬":"ì","Ã²":"ò","Ã¹":"ù",
        "Â°":"°","Â§":"§","Â±":"±","Â€":"€",
    }
    for r in rows:
        for k in ("question","answer","tags"):
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

FAQ_BY_LANG: Dict[str, List[Dict[str, str]]] = {}
for r in FAQ_ITEMS:
    FAQ_BY_LANG.setdefault(r["lang"], []).append(r)

# -----------------------------
# Rilevamento lingua (euristico)
# -----------------------------
_LANG_PATTERNS = {
    "it":[r"\bche\b", r"\bcome\b", r"\bquando\b", r"\bdifferenz[ae]\b"],
    "en":[r"\bwhat\b", r"\bhow\b", r"\bcan\b", r"\bshould\b", r"\bconnector(s)?\b"],
    "es":[r"¿", r"\bqué\b", r"\bcómo\b", r"\bconector(es)?\b"],
    "fr":[r"\bquoi\b", r"\bcomment\b", r"\bquel(le|s)?\b", r"\bconnecteur(s)?\b"],
    "de":[r"\bwas\b", r"\bwie\b", r"\bverbinder\b"],
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
# Token famiglie
# -----------------------------
FAM_TOKENS: Dict[str, List[str]] = {
    "CTF": [
        "ctf","connector","connecteur","verbinder","connettore",
        "lamiera","lamiera grecata","trave","acciaio",
        "chiodatrice","sparo","nailer","powder","cartridge",
        "deck","beam","cloueur","nagler","poutre","chapas","viga"
    ],
    "CTL": ["ctl","soletta","calcestruzzo","collaborazione","legno","timber","concrete","composito","trave legno","viti ø10","maxi"],
    "VCEM": ["vcem","preforo","predrill","pre-drill","pilot","hardwood","essenze dure","durezza","70","80","laterocemento"],
    "CEM-E": ["cem-e","ceme","laterocemento","dry","secco","senza resine","posa a secco","cappello"],
    "CTCEM": ["ctcem","laterocemento","dry","secco","senza resine","cappa","malta","alternativa resine"],
    "GTS": ["gts","manicotto","filettato","giunzione a secco","threaded","sleeve","joint","barra filettata"],
    "P560": [
        "p560","spit","spit p560","spit-p560","chiodatrice","pistola","utensile","attrezzatura",
        "propulsori","cartucce","gialle","verdi","rosse","dosaggio","taratura",
        "chiodi","chiodo","hsbr14","hsbr 14","adattatore","kit","sicura","trigger",
        "powder","powder-actuated","cartridge","magazine","tool",
        "gerät","nagler","werkzeug","outil","cloueur","herramienta","clavos",
        "acciaio","trave","lamiera","lamiera grecata","deck","beam","steel","eta"
    ],
}

def detect_family(text: str) -> Tuple[str, int]:
    t = " " + (text or "").lower() + " "
    best_fam, best_hits = "", 0
    for fam, toks in FAM_TOKENS.items():
        hits = 2 if fam.lower() in t else 0  # boost acronimo
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
# Golden rules IT (risposte certe)
# -----------------------------
GOLDEN_IT = [
    (re.compile(r"(ctf).*(p560|chiodatrice|spit|polvere|powder|hsbr)", re.I),
     "FAQ::CTF_P560_LIMITS",
     "Sì, ma non con una chiodatrice qualunque. Per i connettori CTF è ammessa solo la SPIT P560 con kit/adattatori Tecnaria; altri utensili non sono autorizzati. Ogni connettore va fissato con 2 chiodi HSBR14; scegliere le cartucce P560 in base al supporto e verificare la taratura. Condizioni tipiche: trave in acciaio ≥ 6 mm; lamiera grecata ≥ 0,75 mm. Riferimenti: ETA / Istruzioni di posa CTF."),
    (re.compile(r"(ctl).*(maxi|tavolato|assito|soletta\s*5\s*cm|40\s*mm)", re.I),
     "FAQ::CTL_MAXI_12_040",
     "Usa CTL MAXI 12/040 (gambo 40 mm) posato sull’assito, con 2 viti Ø10 (di norma 100 mm; se interposti >25–30 mm usa 120 mm). Con soletta 5 cm il 40 mm resta ben annegato e la testa supera la rete a metà spessore. Calcestruzzo ≥ C25/30, rete a metà spessore."),
    (re.compile(r"(ctcem).*(resin|resine|chimic)", re.I),
     "FAQ::CTCEM_NO_RESIN",
     "No: CTCEM non usa resine. Il fissaggio è meccanico (a secco): incisione per piastra dentata, preforo Ø11 mm prof. ~75 mm, pulizia polvere e avvitatura del piolo con avvitatore fino a battuta. Alternativa alle soluzioni con barre + resina su laterocemento."),
]

def guess_family_from_mid(mid:str)->str:
    for fam in ("CTF","CTL","CEM-E","CTCEM","VCEM","GTS","P560"):
        if fam in mid: return fam
    return ""

def try_golden_it(q:str)->Optional[Dict[str,Any]]:
    for rx, mid, ans in GOLDEN_IT:
        if rx.search(q):
            return {"ok":True,"match_id":mid,"lang":"it","family":guess_family_from_mid(mid),
                    "intent":"faq","source":"golden","score":99.0,"text":ans,"html":""}
    return None

# -----------------------------
# Intent routing
# -----------------------------
def intent_route(q: str) -> Dict[str, Any]:
    ql = (q or "").lower().strip()
    lang = detect_lang(ql)

    # Golden prima (IT)
    if lang == "it":
        hit = try_golden_it(ql)
        if hit:
            return hit

    # Confronti
    fams = list(FAM_TOKENS.keys())
    tokens_compare = [r"\bvs\b", r"\bcontro\b", r"\bversus\b", r"\bdifferenz[ae]\b", r"\bmeglio\b", r"\bquando scegliere\b"]
    if any(re.search(p, ql) for p in tokens_compare):
        present = [f for f in fams if f.lower() in ql]
        if len(present) >= 2:
            a, b = sorted(present)[:2]
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
                "source": "compare" if found else "synthetic",
                "score": 92.0, "text": text, "html": html
            }

    # Famiglia singola (FAQ/Overview)
    fam, hits = detect_family(ql)
    if hits >= 1:
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
        # lingua rilevata, poi cross-lingua
        try_rows(FAQ_BY_LANG.get(lang, []))
        if best_row is None or best_score <= 0:
            try_rows(FAQ_ITEMS)
        if best_row:
            return {
                "ok": True, "match_id": best_row.get("id") or f"FAQ::{fam}", "lang": lang,
                "family": fam, "intent": "faq", "source": "faq",
                "score": 90.0 if hits >= 2 else 82.0,
                "text": best_row.get("answer") or "", "html": ""
            }
        # overview
        ov = _find_overview(fam)
        return {
            "ok": True, "match_id": f"OVERVIEW::{fam}", "lang": lang, "family": fam,
            "intent": "overview", "source": "overview", "score": 75.0,
            "text": ov, "html": ""
        }

    # fallback
    return {
        "ok": True, "match_id": "<NULL>", "lang": lang, "family": "",
        "intent": "fallback", "source": "fallback", "score": 0,
        "text": "Non ho trovato una risposta diretta nei metadati locali. Specifica meglio la famiglia/prodotto (CTF, CTL, CEM-E, CTCEM, VCEM, GTS, P560) oppure riformula con parole chiave tecniche.",
        "html": ""
    }

# -----------------------------
# Endpoints di servizio
# -----------------------------
@app.get("/")
def _root():
    try:
        return {"app":"Tecnaria_V3 (online)","status":"ok","faq_rows":FAQ_ROWS}
    except Exception:
        return {"app":"Tecnaria_V3 (online)","status":"ok"}

@app.get("/health")
def _health():
    try:
        return {"ok": True, "faq_rows": FAQ_ROWS}
    except Exception:
        return {"ok": True}

@app.get("/ui")
def _ui():
    samples = [
        "Differenza tra CTF e CTL?",
        "Quando scegliere CTL invece di CEM-E?",
        "Differenza tra CEM-E e CTCEM?",
        "CTF su lamiera grecata: controlli in cantiere?",
        "Posso usare una chiodatrice qualsiasi per i CTF?",
        "CTL MAXI su tavolato 2 cm e soletta 5 cm: che modello?",
        "CTCEM su laterocemento: servono resine?",
    ]
    return {"title":"Tecnaria_V3 — UI minima","how_to":"GET /api/ask?q=... oppure POST /api/ask { q: \"...\" }","samples":samples}

# -----------------------------
# UI integrata (HTML) su /ui-html
# -----------------------------
_UI_HTML = '''<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>🟢 Tecnaria_V3</title>
  <style>
    body{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;background:#0b3d2e;color:#eafff4;margin:0}
    .wrap{max-width:980px;margin:32px auto;padding:16px}
    h1{margin:0 0 8px}
    .card{background:#115c47;border-radius:16px;padding:16px;margin:12px 0;box-shadow:0 4px 16px rgba(0,0,0,.25)}
    .row{display:flex;gap:12px;flex-wrap:wrap}
    input[type=text]{flex:1;min-width:280px;font-size:16px;padding:12px;border-radius:10px;border:0}
    button{background:#25c28a;border:0;color:#033024;padding:12px 16px;border-radius:10px;font-weight:700;cursor:pointer}
    button:hover{filter:brightness(1.05)}
    .samples button{background:#0fd39a;color:#052d23;margin:4px 6px}
    .pre{white-space:pre-wrap;background:#0e4a39;padding:12px;border-radius:10px}
    small{opacity:.8}
  </style>
</head>
<body>
<div class="wrap">
  <h1>🟢 Tecnaria_V3</h1>
  <p>Scrivi la domanda e premi <b>Chiedi</b>. Oppure scegli un esempio.</p>

  <div class="card row">
    <input id="q" type="text" placeholder="Esempio: Differenza tra CEM-E e CTCEM nella posa su laterocemento?" />
    <button id="ask">Chiedi</button>
  </div>

  <div class="card samples" id="samples"></div>

  <div class="card" id="out">
    <h3>Risposta</h3>
    <div id="meta" class="pre"></div>
    <div id="text" class="pre"></div>
  </div>

  <p><small>UI minima. Endpoint: <code>/api/ask</code> | Health: <code>/health</code></small></p>
</div>

<script>
async function fetchUI(){
  const ui = await fetch("/ui").then(r=>r.json()).catch(()=>null);
  const box = document.getElementById("samples");
  if(!ui || !ui.samples){ box.innerHTML = "<i>Nessun esempio</i>"; return; }
  box.innerHTML = "";
  ui.samples.forEach(s=>{
    const b = document.createElement("button");
    b.textContent = s;
    b.onclick = () => { document.getElementById("q").value = s; ask(); };
    box.appendChild(b);
  });
}
async function ask(){
  const q = document.getElementById("q").value||"";
  const url = "/api/ask?q="+encodeURIComponent(q);
  const t0 = performance.now();
  const r = await fetch(url).then(x=>x.json()).catch(()=>({ok:false}));
  const ms = Math.max(1, Math.round(performance.now()-t0));
  const meta = document.getElementById("meta");
  const text = document.getElementById("text");
  if(!r || r.ok!==true){ meta.textContent="Errore"; text.textContent=""; return; }
  meta.textContent = `match_id: ${r.match_id}\nintent: ${r.intent} | famiglia: ${r.family} | lang: ${r.lang}\nms: ${ms}`;
  text.textContent = r.text || (r.html ? r.html.replaceAll(/<[^>]+>/g,'') : "");
}
document.getElementById("ask").addEventListener("click", ask);
fetchUI();
</script>
</body>
</html>'''

@app.get("/ui-html", response_class=HTMLResponse)
def ui_html():
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
