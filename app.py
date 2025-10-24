# app.py — Tecnaria_V3 (FastAPI) — matcher robusto + GUI embedded
# Compatibilità: Python 3.11.x • fastapi==0.103.x • pydantic==1.10.x • uvicorn==0.22.x • starlette==0.27.x

from __future__ import annotations
from typing import List, Dict, Any, Tuple, Optional, Set
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import time, re, csv, json, unicodedata

app = FastAPI(title="Tecnaria_V3")

# --------------------------------
# Percorsi dati locali
# --------------------------------
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

    # Fix mojibake più comuni
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

FAQ_ROWS = len(FAQ_ITEMS)
FAQ_BY_LANG: Dict[str, List[Dict[str, str]]] = {}
for r in FAQ_ITEMS:
    FAQ_BY_LANG.setdefault(r["lang"], []).append(r)

# --------------------------------
# Utilità testo
# --------------------------------
_punct_re = re.compile(r"[^\w\s]+", re.UNICODE)
_ws_re = re.compile(r"\s+", re.UNICODE)

def norm(s: str) -> str:
    """Lower + senza accenti + senza punteggiatura + spazi singoli."""
    s = (s or "").lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = _punct_re.sub(" ", s)
    s = _ws_re.sub(" ", s).strip()
    return s

def contains_any(hay: str, needles: List[str]) -> bool:
    h = " " + norm(hay) + " "
    return any((" " + norm(n) + " ") in h for n in needles if n)

# --------------------------------
# Lingua (euristico leggero)
# --------------------------------
_LANG_PATTERNS = {
    "en": [r"\bwhat\b", r"\bhow\b", r"\bcan\b", r"\bshould\b", r"\bbetween\b", r"\bdifference\b"],
    "es": [r"¿", r"\bque\b", r"\bcomo\b", r"\bdiferencia\b"],
    "fr": [r"\bquoi\b", r"\bcomment\b", r"\bdifference\b", r"\bentre\b"],
    "de": [r"\bwas\b", r"\bwie\b", r"\bunterschied\b", r"\bzwischen\b"],
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

# --------------------------------
# Famiglie: sinonimi multilingua
# --------------------------------
FAM_SYNS: Dict[str, List[str]] = {
    "CTF": ["ctf","connettori ctf","deck connector","connecteur ctf","verbinder ctf","lamiera grecata","grecata","deck","trave acciaio","beam","hsbr14","p560"],
    "CTL": ["ctl","connettore legno calcestruzzo","soletta su legno","timber concrete","maxi","assito","tavolato","viti"],
    "VCEM": ["vcem","laterocemento","preforo","essenze dure","hardwood","predrill","forati","pignatte"],
    "CEM-E": ["cem e","ceme","cappello a secco","posa a secco","senza resine","laterocemento"],
    "CTCEM": ["ctcem","piastra dentata","foro 11","senza resine","laterocemento"],
    "GTS": ["gts","manicotto","filettato","threaded sleeve","tirante","giunzione a secco"],
    "P560": ["p560","spit p560","chiodatrice a sparo","powder actuated","pat","propulsori","hsbr14"],
}
FAM_LIST = list(FAM_SYNS.keys())

COMPARE_CUES = [
    # IT
    "differenza", "differenze", "confronto", "meglio", "quando scegliere", "vs", "contro",
    # EN
    "difference", "compare", "versus", "vs", "better", "when to choose", "between",
    # ES
    "diferencia", "comparar", "versus", "entre",
    # FR
    "difference", "comparer", "versus", "entre",
    # DE
    "unterschied", "vergleich", "zwischen"
]

def detect_families(text: str) -> Set[str]:
    t = " " + norm(text) + " "
    found: Set[str] = set()
    for fam, syns in FAM_SYNS.items():
        for s in syns + [fam]:
            ss = " " + norm(s) + " "
            if ss in t:
                found.add(fam)
                break
    return found

def detect_family_primary(text: str) -> Tuple[str, int]:
    """Ritorna la famiglia più probabile con punteggio (hits)."""
    t = " " + norm(text) + " "
    best_fam, best_hits = "", 0
    for fam, syns in FAM_SYNS.items():
        hits = 0
        if (" " + norm(fam) + " ") in t:
            hits += 2
        for s in syns:
            ss = norm(s)
            if ss and (" " + ss + " ") in t:
                hits += 1
        if hits > best_hits:
            best_fam, best_hits = fam, hits
    return best_fam, best_hits

def _find_overview(fam: str) -> str:
    f = (fam or "").upper()
    for it in OV_ITEMS:
        if (it.get("family") or "").upper() == f:
            return (it.get("answer") or "").strip()
    return ""

def _compare_block(famA: str, famB: str) -> Tuple[str, str, str]:
    # prova a leggere dai confronti forniti
    found = None
    for it in CMP_ITEMS:
        fa = (it.get("famA") or "").upper()
        fb = (it.get("famB") or "").upper()
        if {fa, fb} == {famA, famB}:
            found = it
            break
    if found:
        return found.get("answer") or "", found.get("html") or "", "compare"
    # sintetico da overview
    ansA = _find_overview(famA)
    ansB = _find_overview(famB)
    html = (
        "<div><h2>Confronto</h2>"
        "<div style='display:flex;gap:24px;flex-wrap:wrap'>"
        f"<div class='side' style='flex:1;min-width:320px'><h3>{famA}</h3><p>{ansA}</p>"
        f"<p><small>Fonte: OVERVIEW::{famA}</small></p></div>"
        f"<div class='side' style='flex:1;min-width:320px'><h3>{famB}</h3><p>{ansB}</p>"
        f"<p><small>Fonte: OVERVIEW::{famB}</small></p></div>"
        "</div></div>"
    )
    return "", html, "synthetic"

def _faq_best_row(query_norm: str, lang: str, fam_hint: str) -> Optional[Dict[str, str]]:
    """Seleziona la riga FAQ con un punteggio un po’ più furbo."""
    def score_row(r: Dict[str,str]) -> int:
        keys = norm((r.get("tags") or "") + " " + (r.get("question") or ""))
        qn = query_norm
        score = 0
        # match parole intere
        for tok in set(keys.split()):
            if tok and (" " + tok + " ") in (" " + qn + " "):
                score += 2
        # phrase overlap semplice
        if len(keys) > 0 and keys in qn:
            score += 3
        # bonus per famiglia suggerita
        if fam_hint and (" " + fam_hint.lower() + " ") in (" " + keys + " "):
            score += 2
        return score

    rows_pref = FAQ_BY_LANG.get(lang, []) or []
    rows_all = rows_pref + [r for r in FAQ_ITEMS if r not in rows_pref]

    best, best_s = None, -1
    for r in rows_all:
        s = score_row(r)
        if s > best_s:
            best, best_s = r, s
    return best if best_s > 0 else None

# --------------------------------
# Intent/Router principale
# --------------------------------
def intent_route(q: str) -> Dict[str, Any]:
    q_raw = q or ""
    qn = norm(q_raw)
    lang = detect_lang(q_raw)

    fams_in_q = list(detect_families(q_raw))
    looks_like_compare = contains_any(q_raw, COMPARE_CUES) or len(fams_in_q) >= 2

    # 1) CONFRONTO
    if looks_like_compare and len(fams_in_q) >= 2:
        fams_in_q.sort(key=lambda x: FAM_LIST.index(x) if x in FAM_LIST else 99)
        a, b = fams_in_q[0], fams_in_q[1]
        text, html, source = _compare_block(a, b)
        return {
            "ok": True,
            "match_id": f"COMPARE::{a}_VS_{b}",
            "lang": lang,
            "family": f"{a}+{b}",
            "intent": "compare",
            "source": source,
            "score": 92.0,
            "text": text,
            "html": html
        }

    # 2) FAMIGLIA SINGOLA: FAQ -> OVERVIEW
    fam, hits = detect_family_primary(q_raw)
    if hits >= 1:
        best = _faq_best_row(qn, lang, fam)
        if best:
            answer = (best.get("answer") or "").strip()
            # Se troppo corta, prova ad arricchire con overview
            if len(answer) < 180:
                ov = _find_overview(fam)
                if ov:
                    # aggiunge un capoverso di contesto (non inventa nulla)
                    answer = answer + ("\n\n" if answer else "") + ov[:600]
            return {
                "ok": True,
                "match_id": best.get("id") or f"FAQ::{fam}",
                "lang": lang,
                "family": fam,
                "intent": "faq",
                "source": "faq",
                "score": 88.0 if hits >= 2 else 80.0,
                "text": answer,
                "html": ""
            }
        # fallback a overview della famiglia
        ov = _find_overview(fam)
        if ov:
            return {
                "ok": True,
                "match_id": f"OVERVIEW::{fam}",
                "lang": lang,
                "family": fam,
                "intent": "overview",
                "source": "overview",
                "score": 72.0,
                "text": ov,
                "html": ""
            }

    # 3) FALLBACK
    return {
        "ok": True,
        "match_id": "<NULL>",
        "lang": lang,
        "family": "",
        "intent": "fallback",
        "source": "fallback",
        "score": 0,
        "text": "Non ho trovato una risposta diretta nei dati locali. Specifica meglio la famiglia o il contesto (es. CTF, CTL, CEM-E, CTCEM, VCEM, GTS, P560).",
        "html": ""
    }

# --------------------------------
# GUI embedded su "/"
# --------------------------------
@app.get("/")
def gui_home():
    html = """
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>🟢 Tecnaria_V3</title>
<style>
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,'Helvetica Neue',Arial; background:#0b1; color:#042; margin:0}
  .wrap{max-width:980px;margin:32px auto;padding:16px}
  h1{font-size:22px;margin:0 0 12px}
  .card{background:#fff;border-radius:14px;box-shadow:0 6px 18px rgba(0,0,0,.12);padding:16px;margin:12px 0}
  .row{display:flex;gap:8px;flex-wrap:wrap}
  input[type=text]{flex:1;min-width:260px;border:1px solid #cfd; border-radius:12px;padding:12px 14px;font-size:16px}
  button{background:#0a5;border:0;color:#fff;border-radius:12px;padding:12px 18px;font-size:16px;cursor:pointer}
  button:hover{filter:brightness(0.95)}
  .pill{display:inline-block;background:#e9fff0;color:#084;padding:6px 10px;border-radius:999px;margin:6px 6px 0 0;font-size:14px;cursor:pointer;border:1px solid #bdf}
  .muted{color:#567}
  pre{white-space:pre-wrap;word-wrap:break-word}
  .meta b{color:#024}
</style>
</head>
<body>
<div class="wrap">
  <h1>🟢 Tecnaria_V3</h1>
  <div class="card">
    <div class="row">
      <input id="q" type="text" placeholder="Scrivi la domanda: es. “Differenza tra CEM-E e CTCEM su laterocemento?”">
      <button id="btn">Chiedi</button>
    </div>
    <div class="muted" style="margin-top:8px">Suggerimenti: “Con P560 posso posare i CTF?”, “CTL MAXI su tavolato 2 cm con soletta 5 cm?”.</div>
  </div>

  <div class="card">
    <div id="examples" class="muted"></div>
  </div>

  <div class="card">
    <div class="meta" id="meta"></div>
    <pre id="out"></pre>
    <div id="html"></div>
  </div>
</div>
<script>
async function loadUI(){
  try{
    const r = await fetch("/ui");
    const ui = await r.json();
    const ex = ui.samples || [];
    const box = document.getElementById("examples");
    box.innerHTML = "<b>Esempi:</b><br/>" + ex.map(s => "<span class='pill' onclick='setQ(`"+s.replace(/`/g,"\\`")+"`)'>"+s+"</span>").join(" ");
  }catch(e){}
}
function setQ(v){ document.getElementById("q").value = v; ask(); }

async function ask(){
  const q = document.getElementById("q").value||"";
  const out = document.getElementById("out");
  const meta = document.getElementById("meta");
  const html = document.getElementById("html");
  out.textContent = "⏳ elaboro…";
  meta.textContent = "";
  html.innerHTML = "";
  try{
    const r = await fetch("/api/ask?q="+encodeURIComponent(q));
    const j = await r.json();
    const m = [];
    m.push("match_id: "+(j.match_id||""));
    m.push("intent: "+(j.intent||"")+" | famiglia: "+(j.family||"")+" | lang: "+(j.lang||""));
    m.push("ms: "+(j.ms||0));
    meta.innerHTML = "<b>Meta</b><br>"+m.join("<br>");
    out.textContent = j.text || "";
    if(j.html){ html.innerHTML = j.html; }
  }catch(e){
    out.textContent = "Errore: "+e;
  }
}

document.getElementById("btn").addEventListener("click", ask);
document.addEventListener("keydown", (ev)=>{ if(ev.key==="Enter") ask(); });
loadUI();
</script>
</body>
</html>
    """
    return HTMLResponse(html)

# --------------------------------
# Service endpoints
# --------------------------------
@app.get("/health")
def _health():
    return {"ok": True, "faq_rows": FAQ_ROWS}

@app.get("/ui")
def _ui():
    samples = [
        "Differenza tra CTF e CTL?",
        "Quando scegliere CTL invece di CEM-E?",
        "Differenza tra CEM-E e CTCEM?",
        "CTF su lamiera grecata: controlli in cantiere?",
        "Con P560 posso posare i CTF?",
        "VCEM su essenze dure: serve preforo 70–80%?",
        "GTS: che cos’è e come si usa?",
        "CTL MAXI su tavolato 2 cm con soletta 5 cm: quale modello?",
        "VCEM on hardwoods: is predrilling required?",
        "What are Tecnaria CTF connectors?",
        "Can I install CTF with any powder-actuated tool?",
        "Que sont les connecteurs CTF Tecnaria ?",
        "¿Qué son los conectores CTF de Tecnaria?",
        "Was sind Tecnaria CTF-Verbinder?",
    ]
    return JSONResponse({
        "title": "Tecnaria_V3 — UI minima",
        "how_to": "Scrivi la domanda e premi Chiedi.",
        "samples": samples
    })

# --------------------------------
# API principale
# --------------------------------
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
