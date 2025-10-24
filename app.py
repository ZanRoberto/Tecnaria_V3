# app.py — Tecnaria_V3 (FastAPI)
from __future__ import annotations

from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path
from fastapi import FastAPI, Query
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
    "CTL": ["ctl","soletta","calcestruzzo","collaborazione","legno","timber","concrete","composito","trave legno"],
    "VCEM": ["vcem","preforo","predrill","pre-drill","pilot","hardwood","essenze","durezza","70","80"],
    "CEM-E": ["ceme","cem-e","laterocemento","dry","secco","senza","resine","cappello","posa a secco"],
    "CTCEM": ["ctcem","laterocemento","dry","secco","senza","resine","cappa","malta"],
    "GTS": ["gts","manicotto","filettato","giunzioni","secco","threaded","sleeve","joint","barra"],
    "P560": [
        "p560","spit","spit p560","spit-p560",
        "chiodatrice","pistola","utensile","attrezzatura","propulsori","propulsore",
        "cartucce","cartuccia","gialle","verdi","rosse","dosaggio","regolazione potenza",
        "chiodi","chiodo","hsbr14","hsbr 14","adattatore","kit adattatore",
        "spari","sparo","colpo","tiro","sicura","marcatura","marcatura ce",
        "powder","powder-actuated","powder actuated","pat","nailer","nailgun",
        "cartridge","cartridges","mag","magazine","trigger","safety","tool",
        "gerät","nagler","werkzeug","outil","cloueur","outil à poudre","herramienta","clavos",
        "acciaio","trave","lamiera","lamiera grecata","deck","beam","steel",
        "supporto","supporti","spessori minimi","eta"
    ],
}

# parole indicative di "tema attrezzatura"
TOOL_TOKENS = [
    "chiodatrice","pistola","utensile","attrezzatura","powder","powder-actuated","powder actuated",
    "nailer","nailgun","propulsori","propulsore","cartucce","cartridge","cartridges","kit","adattatore",
    "hsbr14","hsbr 14","spit","p560","outil","cloueur","werkzeug","gerät","herramienta","tool","safety"
]

# -----------------------------
# Utility detection
# -----------------------------
def text_has_any(t: str, toks: List[str]) -> bool:
    t = " " + t.lower() + " "
    for x in toks:
        x = (x or "").strip().lower()
        if x and x in t:
            return True
    return False

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

# ---- FAQ selector per una famiglia ----
def best_faq_for_family(q: str, fam: str, lang: str) -> Optional[Dict[str, str]]:
    fam = fam.upper().strip()
    ql = (q or "").lower()
    def score_row(r: Dict[str,str]) -> int:
        keys = ((r.get("tags") or "") + " " + (r.get("question") or "") + " " + (r.get("id") or "")).lower()
        sc = 0
        for tok in re.split(r"[,\s;/\-]+", keys):
            tok = tok.strip()
            if tok and tok in ql:
                sc += 1
        # bonus se l'id è della famiglia attesa
        rid = (r.get("id") or "").upper()
        if fam in rid:
            sc += 2
        return sc

    # priorità: stessa lingua → tutte
    candidates = FAQ_BY_LANG.get(lang, []) + FAQ_ITEMS
    # filtra prima per famiglia (id/tags)
    filtered = []
    for r in candidates:
        rid = (r.get("id") or "").upper()
        tgs = (r.get("tags") or "").lower()
        if fam in rid or fam.lower() in tgs:
            filtered.append(r)
    pool = filtered if filtered else candidates

    best, best_sc = None, -1
    for r in pool:
        sc = score_row(r)
        if sc > best_sc:
            best, best_sc = r, sc
    return best

# -----------------------------
# Intent router
# -----------------------------
def intent_route(q: str) -> Dict[str, Any]:
    ql = (q or "").lower().strip()
    lang = detect_lang(ql)

    has_ctf = ("ctf" in ql) or text_has_any(ql, FAM_TOKENS["CTF"])
    has_p560 = ("p560" in ql) or text_has_any(ql, FAM_TOKENS["P560"])
    has_tool = text_has_any(ql, TOOL_TOKENS)

    # 0) REGOLA SPECIALE: domande su utensile per CTF -> P560 FAQ (no compare)
    if has_ctf and (has_p560 or has_tool):
        r = best_faq_for_family(ql, "P560", lang)
        if r:
            return {
                "ok": True,
                "match_id": r.get("id") or "FAQ::P560",
                "lang": lang,
                "family": "P560",
                "intent": "faq",
                "source": "faq",
                "score": 93.0,
                "text": r.get("answer") or "",
                "html": ""
            }

    # 1) Confronti A vs B (se compaiono entrambi i token famiglia e NON scatta la regola speciale)
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
    fam, hits = detect_family(ql)
    if hits >= 1:
        # 2a) FAQ per famiglia rilevata
        r = best_faq_for_family(ql, fam, lang)
        if r:
            return {
                "ok": True,
                "match_id": r.get("id") or f"FAQ::{fam}",
                "lang": lang,
                "family": fam,
                "intent": "faq",
                "source": "faq",
                "score": 90.0 if hits >= 2 else 82.0,
                "text": r.get("answer") or "",
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

# Piccola "UI" JSON per prove rapide da browser
@app.get("/ui")
def _ui():
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
    return {
        "title": "Tecnaria_V3 — UI minima",
        "how_to": "Usa GET /api/ask?q=... oppure POST /api/ask con body { q: \"...\" }",
        "samples": samples
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
