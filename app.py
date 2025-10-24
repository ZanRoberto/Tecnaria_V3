# app.py — Tecnaria_V3 (FastAPI) — routing migliorato (confronti & FAQ)
from __future__ import annotations

from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
import time, re, csv, json

app = FastAPI(title="Tecnaria_V3")

# -----------------------------
# Dati locali (static/data)
# -----------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "static" / "data"
UI_DIR   = BASE_DIR / "static" / "ui"

OV_JSON = DATA_DIR / "tecnaria_overviews.json"   # panoramiche famiglie
CMP_JSON = DATA_DIR / "tecnaria_compare.json"    # confronti A vs B (opzionali)
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

FAQ_BY_LANG: Dict[str, List[Dict[str, str]]] = {}
for r in FAQ_ITEMS:
    FAQ_BY_LANG.setdefault(r["lang"], []).append(r)

# -----------------------------
# Rilevamento lingua
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
# Famiglie & sinonimi
# -----------------------------
# Nota: aggiunti sinonimi “forti” per P560 e segnali di confronto
FAM_SYNONYMS: Dict[str, List[str]] = {
    "CTF": [
        "ctf","connettore ctf","connecteur ctf","ctf connector","ctf connectors",
        "lamiera grecata","deck","grecata","profili grecati","trave acciaio","steel beam",
        "chiodi hsbr14","hsbr14"
    ],
    "CTL": [
        "ctl","connettori ctl","soletta collaborante legno-calcestruzzo","legno calcestruzzo",
        "timber concrete","viti ø10","rete a metà spessore","trave legno"
    ],
    "VCEM": [
        "vcem","preforo","predrill","pre-drill","pilot hole","essenze dure","hardwood",
        "70-80%","70–80%","pietrificati"
    ],
    "CEM-E": [
        "cem-e","ceme","laterocemento","posa a secco","dry install","senza resine",
        "solaio laterocemento","travetti","pignatte"
    ],
    "CTCEM": [
        "ctcem","laterocemento","senza resine","dry install","piastra dentata",
        "foro 11 mm","incisione","piolo a secco"
    ],
    "GTS": [
        "gts","manicotto filettato","threaded sleeve","giunzioni a secco","barra filettata"
    ],
    "P560": [
        "p560","spit p560","spit-p560","spit",
        "chiodatrice","pistola a sparo","utensile a polvere","pat","powder actuated",
        "propulsori","cartucce","cartridges","dosaggio","regolazione potenza",
        "adattatore","kit adattatore","magazine","sicura","trigger","safety","marcatura ce",
        "hsbr14","chiodi hsbr14","cloueur","nagler","nailer","tool","gerät","herramienta","outil"
    ],
}

COMPARE_MARKERS = [
    # IT
    "differenza", "differenze", "confronto", "vs", "contro", "meglio di", "meglio del", "quando scegliere",
    # EN
    "difference", "differences", "compare", "versus", "vs.", "better than", "when to choose",
    # FR/ES/DE (segnali principali)
    "différence", "comparaison", "comparar", "comparación", "vergleich",
]

def contains_any(text: str, terms: List[str]) -> bool:
    t = " " + (text or "").lower() + " "
    return any(term in t for term in terms)

def detect_families(text: str) -> List[str]:
    """Ritorna famiglie presenti nella query in base ad acronimi o sinonimi."""
    t = " " + (text or "").lower() + " "
    found = []
    for fam, syns in FAM_SYNONYMS.items():
        score = 0
        if f" {fam.lower()} " in t:
            score += 2
        for s in syns:
            if s and s in t:
                score += 1
        if score >= 2:  # soglia: almeno acronimo oppure 2 sinonimi
            found.append(fam)
    return found

def detect_one_family(text: str) -> Tuple[str, int]:
    """Per domande non di confronto: ritorna la famiglia più probabile + punteggio."""
    t = " " + (text or "").lower() + " "
    best, score = "", 0
    for fam, syns in FAM_SYNONYMS.items():
        s = 0
        if f" {fam.lower()} " in t:
            s += 3
        for w in syns:
            if w and w in t:
                s += 1
        if s > score:
            best, score = fam, s
    return best, score

def _simple_stem(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^\wàèéìòùç]+", " ", s)
    return re.sub(r"\b(di|de|del|della|la|il|lo|le|gli|the|a|an|and|or|que|de|der|die)\b", " ", s).strip()

def faq_score(query: str, row: Dict[str, str], fam_hint: Optional[str], lang: str) -> float:
    """Bag-of-words molto semplice con boost per lingua e famiglia."""
    q = _simple_stem(query)
    keys = _simple_stem((row.get("question") or "") + " " + (row.get("tags") or ""))
    # match count
    q_tokens = set(q.split())
    k_tokens = set(keys.split())
    inter = q_tokens & k_tokens
    score = float(len(inter))
    # boost lingua
    if (row.get("lang") or "").lower() == lang:
        score *= 1.4
    # boost famiglia (se presente nei tag)
    if fam_hint and fam_hint.lower() in (row.get("tags") or ""):
        score *= 1.5
    return score

def _find_overview(fam: str) -> str:
    fam = (fam or "").upper()
    for it in OV_ITEMS:
        if (it.get("family") or "").upper() == fam:
            return (it.get("answer") or "").strip()
    # fallback sintetico
    return f"{fam}: descrizione, ambiti applicativi, posa, controlli e riferimenti."

def _find_compare_block(a: str, b: str) -> Tuple[str, str, str]:
    """Ritorna (source, text, html). Se non c'è nel JSON, crea un confronto sintetico con le 2 overview."""
    for it in CMP_ITEMS:
        fa = (it.get("famA") or "").upper()
        fb = (it.get("famB") or "").upper()
        if {fa, fb} == {a, b}:
            return "compare", (it.get("answer") or ""), (it.get("html") or "")
    # sintetico
    ansA = _find_overview(a)
    ansB = _find_overview(b)
    html = (
        "<div><h2>Confronto</h2>"
        "<div style='display:flex;gap:24px;flex-wrap:wrap'>"
        f"<div class='side' style='flex:1;min-width:320px'><h3>{a}</h3><p>{ansA}</p>"
        f"<p><small>Fonte: <b>OVERVIEW::{a}</b></small></p></div>"
        f"<div class='side' style='flex:1;min-width:320px'><h3>{b}</h3><p>{ansB}</p>"
        f"<p><small>Fonte: <b>OVERVIEW::{b}</b></small></p></div>"
        "</div></div>"
    )
    return "synthetic", "", html

# -----------------------------
# Intent router
# -----------------------------
def intent_route(q: str) -> Dict[str, Any]:
    ql = (q or "").lower().strip()
    lang = detect_lang(ql)

    # 1) CONFRONTO: attivo solo se ci sono marker espliciti + due famiglie riconosciute
    if contains_any(ql, COMPARE_MARKERS):
        fams = detect_families(ql)
        fams = list(dict.fromkeys(fams))  # unique & order
        if len(fams) >= 2:
            a, b = fams[0], fams[1]
            source, text, html = _find_compare_block(a, b)
            return {
                "ok": True,
                "match_id": f"COMPARE::{a}_VS_{b}",
                "lang": lang,
                "family": f"{a}+{b}",
                "intent": "compare",
                "source": source,
                "score": 93.0,
                "text": text,
                "html": html,
            }

    # 2) FAMIGLIA SINGOLA: scegli la migliore
    fam, fam_score = detect_one_family(ql)
    if fam and fam_score >= 2:
        rows = FAQ_BY_LANG.get(lang, []) + FAQ_ITEMS  # prima lingua corretta, poi cross
        best_row, best = None, 0.0
        for r in rows:
            s = faq_score(ql, r, fam, lang)
            if s > best:
                best, best_row = s, r
        if best_row and best >= 1.0:
            return {
                "ok": True,
                "match_id": (best_row.get("id") or f"FAQ::{fam}"),
                "lang": lang,
                "family": fam,
                "intent": "faq",
                "source": "faq",
                "score": round(80.0 + min(20.0, best * 5.0), 1),
                "text": best_row.get("answer") or "",
                "html": "",
            }
        # nessuna FAQ “convincente”: dai overview
        ov = _find_overview(fam)
        return {
            "ok": True,
            "match_id": f"OVERVIEW::{fam}",
            "lang": lang,
            "family": fam,
            "intent": "overview",
            "source": "overview",
            "score": 75.0,
            "text": ov,
            "html": "",
        }

    # 3) Fallback
    return {
        "ok": True,
        "match_id": "<NULL>",
        "lang": lang,
        "family": "",
        "intent": "fallback",
        "source": "fallback",
        "score": 0,
        "text": "Non ho trovato una risposta diretta. Indica la famiglia (CTF, CTL, CEM-E, CTCEM, VCEM, GTS, P560) o riformula la domanda.",
        "html": ""
    }

# -----------------------------
# Endpoints di servizio
# -----------------------------
@app.get("/")
def _root():
    try:
        return {
            "ok": True,
            "faq_rows": FAQ_ROWS,
        }
    except Exception:
        return {"ok": True}

@app.get("/health")
def _health():
    try:
        return {"ok": True, "faq_rows": FAQ_ROWS}
    except Exception:
        return {"ok": True}

# Piccola “UI” JSON come check rapido
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
        "Mi spieghi la P560?",
        "Can I install CTF with any powder-actuated tool?",
        "What are Tecnaria CTF connectors?",
        "Que sont les connecteurs CTF Tecnaria ?",
        "¿Qué son los conectores CTF de Tecnaria?",
        "Was sind Tecnaria CTF-Verbinder?",
    ]
    return {
        "title": "Tecnaria_V3 — UI minima",
        "how_to": "GET /api/ask?q=... oppure POST /api/ask { q: \"...\" }",
        "samples": samples
    }

# UI statica (HTML molto semplice)
@app.get("/static/ui/index.html")
def _serve_ui():
    html = f"""
<!doctype html><html lang="it"><meta charset="utf-8"/>
<title>Tecnaria_V3 — Chatbot Tecnico</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
  body{{background:#eef7ef;font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#123;}}
  .wrap{{max-width:1100px;margin:24px auto;padding:12px;}}
  .title{{background:#247a31;color:#fff;border-radius:10px;padding:10px 14px;font-weight:700}}
  textarea{{width:100%;min-height:110px;font-size:18px;padding:12px;border:2px solid #247a31;border-radius:10px;background:#f6fff6}}
  button{{background:#247a31;color:#fff;border:0;padding:10px 18px;border-radius:10px;font-weight:700;cursor:pointer}}
  .pill{{display:inline-block;background:#e7f5ea;border-radius:999px;padding:10px 12px;margin:6px 8px 0 0;border:1px solid #cfead5}}
  .resp{{background:#fff;border:1px solid #cfead5;border-left:6px solid #247a31;border-radius:10px;padding:14px;min-height:48px}}
  .meta span{{display:inline-block;background:#eaf5ee;border:1px solid #d7e9dc;border-radius:999px;padding:3px 8px;margin-right:6px;font-size:12px}}
</style>
<div class="wrap">
  <div class="title">🟢 Tecnaria_V3 — Chatbot Tecnico</div>
  <p>Scrivi la tua domanda e premi <b>Chiedi</b>:</p>
  <textarea id="q" placeholder="Esempio: Differenza tra CEM-E e CTCEM nella posa su laterocemento?"></textarea>
  <p><button id="go">Chiedi</button></p>
  <div id="examples"></div>
  <h3>Risposta</h3>
  <div class="meta" id="meta"></div>
  <div class="resp" id="out"></div>
</div>
<script>
async function ask(q){{
  const r = await fetch("/api/ask?q="+encodeURIComponent(q));
  return await r.json();
}}
async function loadSamples(){{
  const r = await fetch("/ui"); const j = await r.json();
  const div = document.getElementById("examples");
  (j.samples||[]).forEach(s=>{{
    const a=document.createElement("span");
    a.className="pill"; a.textContent=s; a.onclick=()=>{{document.getElementById('q').value=s}};
    div.appendChild(a);
  }});
}}
document.getElementById("go").onclick = async ()=>{
  const q = document.getElementById("q").value||"";
  document.getElementById("out").textContent="Attendere...";
  const j = await ask(q);
  document.getElementById("meta").innerHTML =
    `<span>match_id: ${j.match_id}</span><span>intent: ${j.intent}</span>`+
    `<span>famiglia: ${j.family||""}</span><span>lang: ${j.lang||""}</span>`+
    `<span>ms: ${j.ms||"?"}</span>`;
  const html = (j.html||"").trim();
  document.getElementById("out").innerHTML = html ? html : (j.text||"");
};
loadSamples();
</script>
"""
    return HTMLResponse(content=html)

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

def _route_and_time(q: str) -> Tuple[Dict[str, Any], int]:
    t0 = time.time()
    routed = intent_route(q or "")
    ms = max(1, int((time.time() - t0) * 1000))
    routed["ms"] = ms
    return routed, ms

@app.get("/api/ask", response_model=AskOut)
def api_ask_get(q: str = Query(default="", description="Domanda")) -> AskOut:
    routed, ms = _route_and_time(q)
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

@app.post("/api/ask", response_model=AskOut)
def api_ask_post(body: AskIn) -> AskOut:
    routed, ms = _route_and_time(body.q or "")
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
