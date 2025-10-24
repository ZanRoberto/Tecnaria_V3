# app.py — Tecnaria_V3 (FastAPI) — free-text matcher + compare robusto + risposte "golden"
from __future__ import annotations
from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path
from fastapi import FastAPI, Query
from pydantic import BaseModel
import time, re, csv, json, difflib, unicodedata

app = FastAPI(title="Tecnaria_V3")

# ---------------- Paths ----------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "static" / "data"
OV_JSON  = DATA_DIR / "tecnaria_overviews.json"
CMP_JSON = DATA_DIR / "tecnaria_compare.json"
FAQ_CSV  = DATA_DIR / "faq.csv"

# -------------- Utils ------------------
def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^\w\s\-\./+]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
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

# -------- Indice per lingua ----------
FAQ_BY_LANG: Dict[str, List[Dict[str, str]]] = {}
for r in FAQ_ITEMS:
    FAQ_BY_LANG.setdefault(r["lang"], []).append(r)

# --------- Lang detect (euristico) ----
_LANG_PATTERNS = {
    "en": [r"\bwhat\b", r"\bhow\b", r"\bcan\b", r"\bshould\b", r"\bconnector(s)?\b", r"\bdifference\b"],
    "es": [r"¿", r"\bqué\b", r"\bcómo\b", r"\bconector(es)?\b", r"\bdiferenc"],
    "fr": [r"\bquoi\b", r"\bcomment\b", r"\bquel(le|s)?\b", r"\bconnecteur(s)?\b", r"\bdiff[ée]rence"],
    "de": [r"\bwas\b", r"\bwie\b", r"\bverbinder\b", r"\bunterschied"],
    "it": [r"\bdifferenz", r"\bconfront", r"\bpos[ae]\b", r"\bchiodatric", r"\bresin"],
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

# --------- Famiglie (sinonimi) --------
FAM_TOKENS: Dict[str, List[str]] = {
    "CTF": ["ctf","connettore ctf","shear connector","connector","connectors","connecteur","verbinder",
            "lamiera","lamiera grecata","deck","trave","beam","acciaio","steel","chiodi hsbr14","hsbr14",
            "p560","spit"],
    "CTL": ["ctl","soletta","calcestruzzo","collaborazione","legno","timber","concrete","composito",
            "tavolato","assito","maxi","viti ø10","viti 10"],
    "VCEM": ["vcem","preforo","predrill","pre-drill","pilot","hardwood","essenze dure","durezza","70","80","foro pilota"],
    "CEM-E": ["ceme","cem-e","laterocemento","dry","secco","senza resine","resine no","cappello","posa a secco"],
    "CTCEM": ["ctcem","laterocemento","dry","secco","senza resine","cappa","malta","alternativa resine"],
    "GTS": ["gts","manicotto","filettato","giunzioni","threaded","sleeve","joint","barra"],
    "P560": [
        "p560","spit","spit p560","spit-p560",
        "chiodatrice","pistola","utensile","attrezzatura","nailer","nailgun","powder","powder-actuated","pat",
        "propulsori","cartucce","cartuccia","gialle","verdi","rosse","dosaggio","regolazione potenza",
        "hsbr14","adattatore","kit","kit adattatore","sicura","trigger","magazine",
        "cloueur","nagler","outil","werkzeug","herramienta"
    ],
}

def _families_in_text(q: str) -> List[str]:
    t = " " + _norm(q) + " "
    found = []
    for fam, toks in FAM_TOKENS.items():
        hits = 0
        if (" " + fam.lower() + " ") in t:
            hits += 2
        for tok in toks:
            tok = tok.lower().strip()
            if tok and (" " + tok + " ") in t:
                hits += 1
        if hits > 0:
            found.append((fam, hits))
    found.sort(key=lambda x: x[1], reverse=True)
    return [f for f, _ in found]

def _find_overview(fam: str) -> str:
    fam = (fam or "").upper()
    for it in OV_ITEMS:
        if (it.get("family") or "").upper() == fam:
            return (it.get("answer") or "").strip()
    return f"{fam}: descrizione, ambiti applicativi, posa, controlli e riferimenti."

# ---- Enrichment “golden” -------------
ENRICH: Dict[str, str] = {
    # 1) CTF su lamiera grecata — controlli in cantiere
    "FAQ::CTF_SITE_CHECK": (
        "🔍 **CTF su lamiera grecata — controlli in cantiere**\n"
        "**Pre-posa**: modello CTF corretto; lamiera integra; spessori minimi (lamiera ≥0,75 mm; trave ≥6 mm); "
        "compatibilità greca/fori piastra.\n"
        "**Posa**: chiodatrice **SPIT P560** con kit Tecnaria; **2 chiodi HSBR14** per connettore; testa a battuta; "
        "mai ri-sparare nello stesso foro; verticalità entro ±5°.\n"
        "**Post-posa (≥10%)**: numero chiodi, assenza gioco, allineamento; pulizia zona getto; verbale con foto.\n"
        "_Riferimenti_: ETA-18/0447, Manuale CTF, linee guida Tecnaria."
    ),
    # 2) P560 — “posso usare una chiodatrice qualsiasi?”
    "FAQ::P560": (
        "No: per i **CTF** è ammessa **solo la SPIT P560** con **kit/adattatori Tecnaria**. "
        "Ogni connettore si posa con **2 chiodi HSBR14**; scegli i **propulsori P560** in base al supporto. "
        "Utensili non approvati ⇒ procedura non valida."
    ),
    # 3) CTL MAXI su tavolato 2 cm + soletta 5 cm
    "FAQ::CTL_MAXI": (
        "Usa **CTL MAXI 12/040** (gambo 40 mm), posato **sull’assito** con **2 viti Ø10** (tip. 100 mm; "
        "se interposti/tavolato >25–30 mm valuta 120 mm). **Soletta 5 cm**, rete a metà spessore, testina sopra la rete."
    ),
    # 4) CTCEM — resine?
    "FAQ::CTCEM_DRY": (
        "**No resine**: i **CTCEM** sono fissaggi totalmente **meccanici** (‘a secco’). "
        "Incisione per piastra dentata → **preforo Ø11 mm** prof. ~75 mm → pulizia → avvitare il piolo fino a battuta."
    ),
}

def _maybe_enrich(match_id: str, text: str) -> str:
    return ENRICH.get(match_id, text or "")

# ---- Compare builder (synthetic) -----
def _compare_synthetic(a: str, b: str) -> str:
    ansA = _find_overview(a)
    ansB = _find_overview(b)
    # layout semplice: 2 colonne
    return (
        "<div><h2>Confronto</h2>"
        "<div style='display:flex;gap:24px;flex-wrap:wrap'>"
        f"<div class='side' style='flex:1;min-width:320px'><h3>{a}</h3><p>{ansA}</p>"
        f"<p><small>Fonte: <b>OVERVIEW::{a}</b></small></p></div>"
        f"<div class='side' style='flex:1;min-width:320px'><h3>{b}</h3><p>{ansB}</p>"
        f"<p><small>Fonte: <b>OVERVIEW::{b}</b></small></p></div>"
        "</div></div>"
    )

# ---- FAQ matcher (overlap + difflib) --
STOP = set("di del della delle degli dei da a al alla alle con per tra fra su the and or of en de le la los las der die das und".split())
def _score_faq(qnorm: str, row: Dict[str,str]) -> float:
    keys = _norm((row.get("tags") or "") + " " + (row.get("question") or ""))
    qw = [w for w in qnorm.split() if w not in STOP]
    kw = [w for w in keys.split() if w not in STOP]
    overlap = len(set(qw) & set(kw))
    sim = difflib.SequenceMatcher(None, qnorm, keys).ratio()  # 0..1
    return overlap*1.0 + sim*2.0   # più peso alla similarità testuale

def _best_faq(q: str, lang: str) -> Optional[Dict[str,str]]:
    qn = _norm(q)
    best = None
    best_s = -1.0
    def scan(rows):
        nonlocal best, best_s
        for r in rows:
            s = _score_faq(qn, r)
            if s > best_s:
                best_s, best = s, r
    scan(FAQ_BY_LANG.get(lang, []))
    if best is None or best_s < 0.9:
        scan(FAQ_ITEMS)
    return best if best_s >= 0.9 else None

# ---- Regole mirate (domande libere → ID) ----
_RULES = [
    # “posso usare una chiodatrice / powder tool per i CTF?”
    (r"(ctf).*(chiodatric|p560|spit|powder|nailgun|pat|tool|attrezzatura)", "FAQ::P560"),
    (r"(chiodatric|powder|nailgun|p560|spit).*(ctf)", "FAQ::P560"),
    # “ctl maxi su tavolato 2 cm e soletta 5 cm”
    (r"(ctl).*?(maxi|tavolato|assito).*(2\s*cm).*(5\s*cm)", "FAQ::CTL_MAXI"),
    # “ctcem usa resine?”
    (r"(ctcem).*(resin|resine)", "FAQ::CTCEM_DRY"),
    # “ctf su lamiera grecata: controlli”
    (r"(ctf).*(lamiera|grecat).*(controll|cantiere|verifiche)", "FAQ::CTF_SITE_CHECK"),
]

def _rule_match(q: str) -> Optional[str]:
    t = _norm(q)
    for pat, mid in _RULES:
        if re.search(pat, t):
            return mid
    return None

# ------------- Router ------------------
def intent_route(q: str) -> Dict[str, Any]:
    ql = (q or "").strip()
    lang = detect_lang(ql)

    # 0) regole mirate prima di tutto
    forced = _rule_match(ql)
    if forced:
        fams = _families_in_text(ql)
        fam = fams[0] if fams else ""
        text = _maybe_enrich(forced, "")
        return {"ok": True,"match_id": forced,"lang": lang,"family": fam,"intent": "faq",
                "source": "faq+rule","score": 93.0,"text": text,"html": ""}

    # 1) confronti — due famiglie o hint “vs/differenza”
    fams = _families_in_text(ql)
    compare_hint = re.search(r"\b(vs|versus|contro|confront|differen[cz]|vs\.)\b", _norm(ql))
    if len(fams) >= 2 or (compare_hint and len(fams) >= 1):
        a = fams[0]
        b = fams[1] if len(fams) > 1 else next((f for f in FAM_TOKENS if f != a), "CTL")
        found = None
        for it in CMP_ITEMS:
            fa = (it.get("famA") or "").upper()
            fb = (it.get("famB") or "").upper()
            if {fa, fb} == {a, b}:
                found = it; break
        if found:
            html = found.get("html") or _compare_synthetic(a,b)
            text = found.get("answer") or ""
            return {"ok": True,"match_id": f"COMPARE::{a}_VS_{b}","lang": lang,"family": f"{a}+{b}",
                    "intent": "compare","source": "compare","score": 92.0,"text": text,"html": html}
        else:
            return {"ok": True,"match_id": f"COMPARE::{a}_VS_{b}","lang": lang,"family": f"{a}+{b}",
                    "intent": "compare","source": "synthetic","score": 90.0,"text": "",
                    "html": _compare_synthetic(a,b)}

    # 2) famiglia singola → FAQ, poi overview
    fams = _families_in_text(ql)
    fam = fams[0] if fams else ""
    row = _best_faq(ql, lang)
    if row:
        mid = (row.get("id") or f"FAQ::{fam or 'GEN'}").strip()
        txt = _maybe_enrich(mid, row.get("answer") or "")
        return {"ok": True,"match_id": mid,"lang": lang,"family": fam,"intent": "faq",
                "source": "faq","score": 88.0,"text": txt,"html": ""}

    if fam:
        ov = _find_overview(fam)
        return {"ok": True,"match_id": f"OVERVIEW::{fam}","lang": lang,"family": fam,
                "intent": "overview","source": "overview","score": 75.0,"text": ov,"html": ""}

    return {"ok": True,"match_id": "<NULL>","lang": lang,"family": "","intent": "fallback",
            "source": "fallback","score": 0,
            "text": "Non ho trovato una risposta diretta nei metadati locali. Specifica famiglia/prodotto.",
            "html": ""}

# ------------- Service endpoints ---------------
@app.get("/")
def _root():
    return {"app": "Tecnaria_V3 (online)","status": "ok","data_dir": str(DATA_DIR),
            "json_loaded": list(JSON_BAG.keys()),"faq_rows": FAQ_ROWS}

@app.get("/health")
def _health():
    return {"ok": True, "json_loaded": list(JSON_BAG.keys()), "faq_rows": FAQ_ROWS}

@app.get("/ui")
def _ui():
    samples = [
        "Differenza tra CTF e CTL?",
        "Quando scegliere CTL invece di CEM-E?",
        "Differenza tra CEM-E e CTCEM?",
        "CTF su lamiera grecata: controlli in cantiere",
        "P560: posso usare una chiodatrice qualsiasi?",
        "VCEM su essenze dure: serve preforo 70–80%?",
        "CTL MAXI su tavolato 2 cm con soletta 5 cm: quale modello?",
        "CTCEM: è una posa a secco?",
        "What are Tecnaria CTF connectors?",
        "Can I install CTF with any powder-actuated tool?",
    ]
    return {"title":"Tecnaria_V3 — UI minima",
            "how_to":"GET /api/ask?q=... oppure POST /api/ask { q: \"...\" }",
            "samples":samples}

# ---------------- API principale ----------------
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

def _answer(q: str) -> Dict[str,Any]:
    return intent_route(q or "")

@app.get("/api/ask", response_model=AskOut)
def api_ask_get(q: str = Query(default="", description="Domanda")) -> AskOut:
    t0 = time.time()
    routed = _answer(q)
    ms = int((time.time() - t0) * 1000)
    return AskOut(ok=True, match_id=str(routed.get("match_id") or "<NULL>"), ms=max(ms,1),
                  text=str(routed.get("text") or ""), html=str(routed.get("html") or ""),
                  lang=routed.get("lang"), family=routed.get("family"),
                  intent=routed.get("intent"), source=routed.get("source"),
                  score=routed.get("score"))

@app.post("/api/ask", response_model=AskOut)
def api_ask_post(body: AskIn) -> AskOut:
    t0 = time.time()
    routed = _answer(body.q)
    ms = int((time.time() - t0) * 1000)
    return AskOut(ok=True, match_id=str(routed.get("match_id") or "<NULL>"), ms=max(ms,1),
                  text=str(routed.get("text") or ""), html=str(routed.get("html") or ""),
                  lang=routed.get("lang"), family=routed.get("family"),
                  intent=routed.get("intent"), source=routed.get("source"),
                  score=routed.get("score"))
