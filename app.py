# app.py — Tecnaria_V3 (FastAPI) — “domande naturali” + confronti robusti
from __future__ import annotations

from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path
from fastapi import FastAPI, Query
from pydantic import BaseModel
import time, re, csv, json

app = FastAPI(title="Tecnaria_V3")

# =========================================
# Dati locali (static/data)
# =========================================
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

# Indice per lingua
FAQ_BY_LANG: Dict[str, List[Dict[str, str]]] = {}
for r in FAQ_ITEMS:
    FAQ_BY_LANG.setdefault(r["lang"], []).append(r)

# =========================================
# Rilevamento lingua (euristico)
# =========================================
_LANG_PATTERNS = {
    "en": [r"\bwhat\b", r"\bhow\b", r"\bcan\b", r"\bshould\b", r"\bconnector(s)?\b", r"\bvs\b", r"\bdifference\b"],
    "es": [r"¿", r"\bqué\b", r"\bcómo\b", r"\bconector(es)?\b", r"\bdiferenc"],
    "fr": [r"\bquoi\b", r"\bcomment\b", r"\bquel(le|s)?\b", r"\bconnecteur(s)?\b", r"\bdiff[ée]rence"],
    "de": [r"\bwas\b", r"\bwie\b", r"\bverbinder\b", r"\bunterschied"],
    "it": [r"\bdifferen", r"\bmeglio\b", r"\bconfronto\b"],
}
def detect_lang(q: str) -> str:
    s = (q or "").lower()
    for lang, pats in _LANG_PATTERNS.items():
        for p in pats:
            if re.search(p, s):
                return "it" if lang == "it" else lang
    if "¿" in s or "¡" in s:  # segni spagnoli
        return "es"
    return "it"

# =========================================
# Sinonimi/concetti → famiglie (domande naturali)
# =========================================
# NB: non solo parole secche; includiamo concetti tipici che l’utente usa “a voce”
FAM_SYNONYMS: Dict[str, List[str]] = {
    # Acciaio/lamiera grecata → CTF
    "CTF": [
        r"\blamiera( grecata)?\b", r"\bacciaio\b", r"\bdeck\b", r"\bprofil(ato|ati)\b",
        r"\btrave in acciaio\b", r"\bsteel\b",
        r"\bconnett(or|eur|or|or)s?\b.*(acciaio|steel|lamiera|deck)",
    ],
    # Collaborazione legno-calcestruzzo → CTL
    "CTL": [
        r"\bcollaborazion", r"\bsoletta\b", r"\blegno\b", r"\btimber\b", r"\bcalcestruzzo\b",
        r"\btrave (in )?legno\b", r"\bcomposito\b",
    ],
    # Viti su legno duro / preforo → VCEM
    "VCEM": [
        r"\bvcem\b", r"\bessenze dure\b", r"\bhardwood(s)?\b", r"\bprefor(o|are)\b", r"\bpre-?drill",
        r"\b70\s*–?\s*80\b", r"\b70-?80\b",
    ],
    # Posa “a secco”, senza resine → CEM-E
    "CEM-E": [
        r"\b(cem[- ]?e|ceme)\b", r"\bposa a secco\b", r"\bdry\b", r"\bsenza resine?\b", r"\blaterocemento\b",
    ],
    # Chimico/meccanico su laterocemento → CTCEM
    "CTCEM": [
        r"\bctcem\b", r"\bancoraggi\b", r"\bresine?\b", r"\bcappa\b", r"\blaterocemento\b",
    ],
    # Manicotto filettato / giunti a secco → GTS
    "GTS": [
        r"\bgts\b", r"\bmanicott[oi]\b", r"\bfilettat[oi]\b", r"\bthread(ed)?\b", r"\bsleeve\b",
        r"\bgiunt", r"\bjoint\b",
    ],
    # Attrezzatura a polvere → P560
    "P560": [
        r"\bp560\b", r"\bspit\b", r"\b(spit[- ]?)?p560\b", r"\bchiodatric[ae]\b", r"\bpistola\b",
        r"\butensile\b", r"\battrezzatura\b", r"\bpowder[- ]?actuated\b", r"\bcartridge(s)?\b",
        r"\bpropulsor[ei]\b", r"\bhsb ?r? ?14\b", r"\badattator[ei]\b",
    ],
}

# Token “secchi” per punteggio (in più dei sinonimi sopra)
FAM_TOKENS: Dict[str, List[str]] = {
    "CTF": ["ctf","connector","connectors","connecteur","verbinder","lamiera","acciaio","deck","beam","chiodatrice","sparo","nagler"],
    "CTL": ["ctl","soletta","calcestruzzo","collaborazione","legno","timber","concrete","composito","trave legno"],
    "VCEM": ["vcem","preforo","predrill","pre-drill","pilot","hardwood","essenze","durezza","70","80"],
    "CEM-E": ["ceme","cem-e","laterocemento","dry","secco","senza","resine","cappello","posa a secco"],
    "CTCEM": ["ctcem","laterocemento","dry","secco","senza","resine","cappa","malta","chimico"],
    "GTS": ["gts","manicotto","filettato","giunto","secco","threaded","sleeve","joint","barra"],
    "P560": [
        "p560","spit","spit p560","spit-p560","chiodatrice","pistola","utensile","attrezzatura",
        "propulsori","propulsore","cartucce","cartuccia","gialle","verdi","rosse","dosaggio",
        "regolazione","chiodi","chiodo","hsbr14","hsbr 14","adattatore","kit","spari","sparo",
        "colpo","sicura","magazine","trigger","powder","powder-actuated","cartridge","tool",
        "gerät","nagler","werkzeug","outil","cloueur","herramienta","clavos","steel","lamiera","deck"
    ],
}

# =========================================
# Utility punteggi + mapping
# =========================================
def _hit(text: str, pat: str) -> bool:
    try:
        return re.search(pat, text, flags=re.IGNORECASE) is not None
    except re.error:
        return pat.lower() in text.lower()

def detect_family_free(text: str) -> Tuple[str, int]:
    """
    Rilevamento “naturale”:
    - boost se l'acronimo compare;
    - punti per sinonimi (regex) e per token secchi.
    """
    t = " " + (text or "").lower() + " "
    best_fam, best = "", -1
    for fam in FAM_TOKENS.keys():
        score = 0
        if fam.lower() in t: score += 3  # boost acronimo
        # sinonimi/concetti
        for pat in FAM_SYNONYMS.get(fam, []):
            if _hit(t, pat): score += 2
        # token secchi
        for tok in FAM_TOKENS.get(fam, []):
            tok = (tok or "").strip().lower()
            if tok and tok in t: score += 1
        if score > best:
            best, best_fam = score, fam
    return best_fam, max(best, 0)

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

# =========================================
# Confronti naturali (X vs Y)
# =========================================
COMPARE_TRIGGERS = [
    r"\bdifferen", r"\bconfront", r"\bvs\b", r"\bcontro\b", r"\bversus\b",
    r"\bmeglio\b", r"\bscegliere\b", r"\bpreferir", r"\bwhen to choose\b", r"\bcompare\b", r"\bdifference\b",
    r"\bcu[aá]ndo\b.*(elegir|preferir)", r"\bquand\b.*(choisir|pr[ée]f[ée]rer)"
]

def _maybe_compare(q: str) -> Optional[Tuple[str, str]]:
    ql = (q or "").lower()
    if not any(re.search(p, ql) for p in COMPARE_TRIGGERS):
        return None
    # mappa concetti a famiglie per trovare le due più forti presenti
    fam_scores = {}
    for fam in FAM_TOKENS.keys():
        s = 0
        if fam.lower() in ql: s += 3
        for pat in FAM_SYNONYMS.get(fam, []):
            if _hit(ql, pat): s += 2
        for tok in FAM_TOKENS.get(fam, []):
            if tok in ql: s += 1
        fam_scores[fam] = s
    top = sorted(fam_scores.items(), key=lambda x: x[1], reverse=True)
    if len(top) >= 2 and top[1][1] > 0:
        a, b = top[0][0], top[1][0]
        if a != b:
            # ordina per nome per avere chiave stabile COMPARE::A_VS_B
            famA, famB = sorted([a, b])
            return (famA, famB)
    return None

# =========================================
# Intent router
# =========================================
def intent_route(q: str) -> Dict[str, Any]:
    ql = (q or "").lower().strip()
    lang = detect_lang(ql)

    # 0) Regola di priorità: se la domanda parla chiaramente di ATTREZZO a polvere → P560
    tool_hints = ["powder", "powder-actuated", "attrezzatura", "utensile", "pistola", "chiodatrice", "cartridge", "propulsor", "spit", "p560"]
    if any(h in ql for h in tool_hints):
        fam_hint = "P560"
        # …ma se è un confronto tra famiglie, lo gestiamo dopo.
        cmp_pair = _maybe_compare(ql)
        if not cmp_pair:
            fam = fam_hint
            return _route_family(fam, lang, ql, hits_boost=3)

    # 1) Confronti A vs B (naturali)
    cmp_pair = _maybe_compare(ql)
    if cmp_pair:
        a, b = cmp_pair
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
        famA, famB = sorted([a, b])
        return {
            "ok": True,
            "match_id": f"COMPARE::{famA}_VS_{famB}",
            "lang": lang,
            "family": f"{famA}+{famB}",
            "intent": "compare",
            "source": "compare" if found else "synthetic",
            "score": 92.0,
            "text": text,
            "html": html,
        }

    # 2) Famiglia singola (domande naturali)
    fam, _ = detect_family_free(ql)
    if fam:
        return _route_family(fam, lang, ql, hits_boost=0)

    # 3) Fallback
    return {
        "ok": True, "match_id": "<NULL>", "lang": lang,
        "family": "", "intent": "fallback", "source": "fallback", "score": 0,
        "text": "Non ho trovato una risposta diretta nei metadati locali. Specifica meglio la famiglia/prodotto.",
        "html": ""
    }

def _route_family(fam: str, lang: str, ql: str, hits_boost: int = 0) -> Dict[str, Any]:
    # 2a) Cerca FAQ nella lingua, poi cross-lingua. Punteggio: conteggio token presenti.
    best_row: Optional[Dict[str, str]] = None
    best_score: int = -1

    def try_rows(rows: List[Dict[str, str]]):
        nonlocal best_row, best_score
        for r in rows:
            # preferisci la stessa famiglia se taggata
            key_base = ((r.get("tags") or "") + " " + (r.get("question") or "")).lower()
            score = 0
            for tok in re.split(r"[,\s;/\-]+", key_base):
                tok = tok.strip()
                if tok and tok in ql:
                    score += 1
            # mini-boost se i tag contengono il nome famiglia
            if fam.lower() in (r.get("tags") or "").lower():
                score += 2 + hits_boost
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
            "score": 90.0 if best_score >= 2 else 82.0,
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

# =========================================
# Endpoints di servizio
# =========================================
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

# Mini-UI HTML (semplice ma “grafica”, già usata)
@app.get("/ui", response_model=None)
def _ui_html():
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
    html = f"""
<!doctype html><html lang="it"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tecnaria_V3</title>
<style>
 body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#f5f7fb;margin:0}}
 header{{display:flex;align-items:center;gap:.6rem;padding:18px 20px;background:#0b8b3e;color:#fff;box-shadow:0 2px 6px rgba(0,0,0,.1)}}
 .dot{{width:14px;height:14px;border-radius:50%;background:#7CFC00;box-shadow:0 0 0 4px rgba(255,255,255,.3)}}
 main{{max-width:1100px;margin:24px auto;padding:0 16px}}
 .row{{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}}
 input[type=text]{{flex:1;min-width:260px;padding:14px;border:1px solid #cfd6e4;border-radius:10px;font-size:16px;background:#fff}}
 button{{background:#11a44c;color:#fff;border:0;border-radius:10px;padding:12px 18px;font-weight:600;cursor:pointer}}
 .pill{{background:#e9eef7;border:1px solid #d8e0ef;color:#23324d;border-radius:999px;padding:8px 12px;font-size:14px;cursor:pointer}}
 .card{{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:18px}}
 .meta{{color:#4a5568;font-size:14px;margin-bottom:10px}}
 .cmp{{display:flex;gap:24px;flex-wrap:wrap}}
 .side{{flex:1;min-width:280px}}
 small b{{font-weight:700}}
</style>
</head><body>
<header><div class="dot"></div><h1 style="font-size:22px;margin:0">Tecnaria_V3</h1></header>
<main>
 <div class="row">
   <input id="q" type="text" placeholder="Scrivi la domanda e premi Chiedi…" value="Differenza tra CTF e CTL?"/>
   <button onclick="ask()">Chiedi</button>
 </div>
 <div class="row" style="margin-top:10px;">
   {"".join([f'<span class="pill" onclick="q.value=`{s}`;ask()">{s}</span>' for s in samples])}
 </div>
 <h2>Risposta</h2>
 <div id="out" class="card"><div class="meta">—</div><div id="payload">Nessuna richiesta.</div></div>
</main>
<script>
async function ask(){{
  const qs = document.getElementById('q').value||"";
  const r = await fetch(`/api/ask?q=`+encodeURIComponent(qs));
  const j = await r.json();
  const meta = `match_id: <b>${{j.match_id}}</b><br/>intent: ${{j.intent}} | famiglia: ${{j.family}} | lang: ${{j.lang}}<br/>ms: ${{j.ms||"-"}}`;
  document.querySelector("#out .meta").innerHTML = meta;
  let html = j.html||"";
  if(!html) html = `<p>${{(j.text||"").replaceAll('\\n','<br/>')}}</p>`;
  document.getElementById("payload").innerHTML = html||"<i>—</i>";
}}
</script>
</body></html>
"""
    return html

# =========================================
# API principale
# =========================================
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
