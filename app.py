# app.py — Tecnaria_V3 (FastAPI) — GOLDEN ANSWERS
# ------------------------------------------------
# /               -> UI HTML (pagina verde semplice)
# /ui.json        -> JSON con esempi
# /health         -> stato
# /api/ask (GET)  -> risposta: /api/ask?q=...
# /api/ask (POST) -> body {"q":"..."}
#
# Logica:
# 1) Rileva lingua (it/en/fr/es/de) + normalizza testo
# 2) Se la domanda contiene due famiglie note -> "compare"
# 3) Altrimenti rileva la famiglia singola
# 4) Cerca "golden answer" prima nella lingua, poi cross-lingua
# 5) Se non trova, usa panoramica OVERVIEW della famiglia
# 6) Fallback controllato
#
# NOTE: si appoggia a:
#   static/data/faq.csv
#   static/data/tecnaria_overviews.json
#   static/data/tecnaria_compare.json

from __future__ import annotations

from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import time, re, csv, json, unicodedata

app = FastAPI(title="Tecnaria_V3")

# -----------------------------
# Dati locali (static/data)
# -----------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "static" / "data"
OV_JSON = DATA_DIR / "tecnaria_overviews.json"   # panoramiche famiglie
CMP_JSON = DATA_DIR / "tecnaria_compare.json"    # confronti A vs B
FAQ_CSV = DATA_DIR / "faq.csv"                   # domande/risposte golden multi-lingua

def _norm(s: str) -> str:
    s = (s or "").strip()
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00A0", " ")
    return s

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
                    "id": _norm(r.get("id") or ""),
                    "lang": (_norm(r.get("lang") or "").lower() or "it"),
                    "family": _norm(r.get("family") or ""),
                    "question": _norm(r.get("question") or ""),
                    "answer": _norm(r.get("answer") or ""),
                    "tags": _norm(r.get("tags") or "").lower(),
                })

    try:
        _read("utf-8-sig")          # gestisce anche BOM
    except Exception:
        try:
            _read("cp1252")         # fallback tipico file salvati da Excel su Windows
        except Exception:
            return rows

    # Fix mojibake frequenti (minimo indispensabile)
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

# Indice per lingua/famiglia
FAQ_BY_LANG: Dict[str, List[Dict[str, str]]] = {}
FAQ_BY_FAM: Dict[str, List[Dict[str, str]]] = {}
for r in FAQ_ITEMS:
    FAQ_BY_LANG.setdefault(r["lang"], []).append(r)
    fam = (r.get("family") or "").upper()
    if fam:
        FAQ_BY_FAM.setdefault(fam, []).append(r)

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
# Famiglie + sinonimi (multilingua)
# -----------------------------
FAMS = ["CTF", "CTL", "VCEM", "CEM-E", "CTCEM", "GTS", "P560"]

FAM_TOKENS: Dict[str, List[str]] = {
    "CTF": [
        "ctf","connettore ctf","tecnaria ctf",
        "connector","connectors","connecteur","verbinder",
        "lamiera","lamiera grecata","grecata","deck","trapezoidal sheet",
        "trave","beam","acciaio","steel",
        "chiodatrice","nailer","powder","cartridge","propulsori","hsbr14","hsbr 14",
        "spit p560","p560","kit adattatore","adapters"
    ],
    "CTL": [
        "ctl","connettori legno calcestruzzo","timber concrete",
        "soletta","composito","collaborazione","trave legno","timber","concrete",
        "viti ø10","viti 10","maxi","mini"
    ],
    "VCEM": [
        "vcem","laterocemento legno","predrill","pre-drill","pilot",
        "preforo","hardwood","essenze dure","durezza","70","80","foro pilota",
        "no resine","a secco"
    ],
    "CEM-E": [
        "ceme","cem-e","laterocemento acciaio","posa a secco","dry","senza resine",
        "cappello di calcestruzzo","travi in acciaio","profili"
    ],
    "CTCEM": [
        "ctcem","laterocemento","posa a secco","dry","senza resine",
        "cappa","malta","piastra dentata","preforo 11"
    ],
    "GTS": [
        "gts","manicotto","filettato","threaded sleeve","joint","giunzioni a secco",
        "barra","tirante","collegamento"
    ],
    "P560": [
        "p560","spit","spit p560","spit-p560","tecnaria p560",
        "chiodatrice","pistola","utensile","attrezzatura",
        "propulsori","cartucce","cartuccia","gialle","verdi","rosse",
        "dosaggio","taratura","potenza",
        "chiodi","chiodo","hsbr14","hsbr 14","adattatore","kit adattatore",
        "spari","sparo","colpo","sicura","marcatura ce",
        "powder","powder-actuated","pat","nailer","nailgun",
        "cartridge","cartridges","mag","magazine","trigger","safety","tool",
        "gerät","nagler","werkzeug","outil","cloueur","outil à poudre","herramienta","clavos",
        "acciaio","trave","lamiera","lamiera grecata","deck","beam","steel",
        "supporto","supporti","spessori minimi","eta"
    ],
}

def detect_families(text: str) -> List[str]:
    t = " " + (text or "").lower() + " "
    found: List[Tuple[str,int]] = []
    for fam, toks in FAM_TOKENS.items():
        hits = 0
        if fam.lower() in t:
            hits += 2  # boost acronimo
        for tok in toks:
            tok = tok.strip().lower()
            if tok and tok in t:
                hits += 1
        if hits > 0:
            found.append((fam, hits))
    found.sort(key=lambda x: x[1], reverse=True)
    return [f for f,_ in found]

def best_family(text: str) -> Tuple[str,int]:
    lst = detect_families(text)
    if not lst: return "", 0
    # riporta anche score grezzo
    fam = lst[0]
    # ricontiamo per score
    t = " " + (text or "").lower() + " "
    hits = 0
    if fam.lower() in t: hits += 2
    for tok in FAM_TOKENS[fam]:
        tok = tok.strip().lower()
        if tok and tok in t:
            hits += 1
    return fam, hits

# -----------------------------
# Overviews & Compare helpers
# -----------------------------
def _find_overview(fam: str) -> str:
    fam = (fam or "").upper()
    for it in OV_ITEMS:
        if (it.get("family") or "").upper() == fam:
            return _norm(it.get("answer") or "")
    return f"{fam}: descrizione generale, ambiti applicativi, posa, controlli e riferimenti."

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

def try_compare(text: str) -> Optional[Dict[str, Any]]:
    # Cerca due famiglie nel testo (qualunque lingua)
    fams = detect_families(text)
    fams = [f for f in fams if f in FAMS]
    if len(fams) < 2:
        return None
    a, b = sorted(fams[:2])
    # Se abbiamo JSON compare, usalo; altrimenti sintetico da overview
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
        "family": f"{a}+{b}",
        "intent": "compare",
        "source": "compare" if found else "synthetic",
        "score": 92.0,
        "text": text,
        "html": html,
    }

# -----------------------------
# Matching FAQ "golden"
# -----------------------------
def score_row(row: Dict[str,str], ql: str, fam_hint: str) -> int:
    score = 0
    tags = (row.get("tags") or "").lower()
    question = (row.get("question") or "").lower()
    keys = f"{tags} {question}"
    # token grezzi
    for tok in re.split(r"[,\s;/\-]+", keys):
        tok = tok.strip()
        if tok and tok in ql:
            score += 1
    # boost se famiglia allineata
    fam_row = (row.get("family") or "").upper()
    if fam_row and fam_row == (fam_hint or "").upper():
        score += 2
    return score

def find_best_faq(ql: str, lang: str, fam_hint: str) -> Optional[Dict[str, str]]:
    best, best_s = None, -1
    # 1) lingua rilevata
    for r in FAQ_BY_LANG.get(lang, []):
        s = score_row(r, ql, fam_hint)
        if s > best_s:
            best, best_s = r, s
    # 2) cross-lingua se niente di forte
    if best_s <= 0:
        for r in FAQ_ITEMS:
            s = score_row(r, ql, fam_hint)
            if s > best_s:
                best, best_s = r, s
    return best

# -----------------------------
# Intent router
# -----------------------------
def intent_route(q: str) -> Dict[str, Any]:
    q_raw = _norm(q)
    ql = q_raw.lower()
    lang = detect_lang(ql)

    # 1) Confronti A vs B
    cmp_hit = try_compare(ql)
    if cmp_hit:
        cmp_hit["lang"] = lang
        return cmp_hit

    # 2) Famiglia singola
    fam, hits = best_family(ql)
    if hits >= 1 and fam:
        row = find_best_faq(ql, lang, fam)
        if row and (row.get("answer") or "").strip():
            return {
                "ok": True,
                "match_id": row.get("id") or f"FAQ::{fam}",
                "lang": lang,
                "family": fam,
                "intent": "faq",
                "source": "faq",
                "score": 90.0 if hits >= 2 else 82.0,
                "text": row.get("answer") or "",
                "html": ""
            }
        # Se non ho una golden, fornisco overview
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
        "text": "Non trovo una risposta diretta nei metadati locali. Specifica meglio la famiglia/prodotto.",
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
# UI: pagina HTML semplice (verde)
# -----------------------------
HTML_PAGE = """
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Tecnaria_V3</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#f6fff7;margin:0;padding:24px}
.container{max-width:960px;margin:0 auto}
h1{color:#0a7f40;margin:0 0 8px}
.sub{color:#155; margin:0 0 16px}
.card{background:#fff;border:1px solid #e2f2e5;border-radius:16px;padding:16px;box-shadow:0 4px 14px rgba(0,0,0,.05)}
.row{display:flex;gap:12px;flex-wrap:wrap}
input[type=text]{flex:1;padding:12px 14px;border:1px solid #bfe3c6;border-radius:12px;font-size:16px}
button{background:#0a7f40;color:#fff;border:0;border-radius:12px;padding:12px 18px;font-size:16px;cursor:pointer}
button:hover{background:#096c37}
pre{white-space:pre-wrap;background:#f8fffa;border-radius:12px;padding:12px;border:1px dashed #bfe3c6}
.badge{display:inline-block;background:#e9fbef;color:#084; border:1px solid #bfe3c6;border-radius:999px;padding:3px 8px;margin-right:6px;font-size:12px}
.samples{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:8px;margin-top:8px}
.sample{background:#f2fff5;border:1px solid #ccf0d5;border-radius:12px;padding:10px;cursor:pointer}
.meta{color:#066;margin:8px 0}
small{color:#466}
</style>
</head>
<body>
<div class="container">
  <h1>🟢 Tecnaria_V3</h1>
  <p class="sub">Scrivi la domanda e premi <b>Chiedi</b>. Oppure scegli un esempio.</p>
  <div class="card">
    <div class="row">
      <input id="q" type="text" placeholder="Es: CTF su lamiera grecata: controlli in cantiere?"/>
      <button onclick="ask()">Chiedi</button>
    </div>
    <div class="samples" id="samples"></div>
    <h3>Risposta</h3>
    <div id="meta" class="meta"></div>
    <pre id="text"></pre>
    <div id="html"></div>
  </div>
</div>
<script>
async function loadSamples(){
  const r = await fetch('/ui.json'); const j = await r.json();
  const box = document.getElementById('samples');
  box.innerHTML = '';
  (j.samples||[]).forEach(s=>{
    const d = document.createElement('div'); d.className='sample'; d.textContent=s;
    d.onclick=()=>{ document.getElementById('q').value=s; ask(); };
    box.appendChild(d);
  });
}
async function ask(){
  const q = document.getElementById('q').value || '';
  const r = await fetch('/api/ask?q=' + encodeURIComponent(q));
  const j = await r.json();
  document.getElementById('meta').innerHTML =
    '<span class="badge">match_id: '+(j.match_id||'')+'</span>'+
    '<span class="badge">intent: '+(j.intent||'')+'</span>'+
    '<span class="badge">famiglia: '+(j.family||'')+'</span>'+
    '<span class="badge">lang: '+(j.lang||'')+'</span>'+
    '<span class="badge">ms: '+(j.ms||'')+'</span>';
  document.getElementById('text').textContent = j.text || '';
  document.getElementById('html').innerHTML = j.html || '';
}
loadSamples();
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def ui_html():
    return HTML_PAGE

@app.get("/ui.json")
def ui_json():
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
    return {"title":"Tecnaria_V3 — UI minima","how_to":"Usa GET /api/ask?q=... oppure POST /api/ask con body { q: \"...\" }","samples":samples}

# -----------------------------
# Service
# -----------------------------
@app.get("/health")
def _health():
    try:
        return {"ok": True, "json_loaded": list(JSON_BAG.keys()), "faq_rows": FAQ_ROWS}
    except Exception:
        return {"ok": True}
