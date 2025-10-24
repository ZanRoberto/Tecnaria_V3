# app.py — Tecnaria_V3 (FastAPI) — versione migliorata per matching e compare
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
    def _read(enc: str):
        with path.open("r", encoding=enc, newline="") as f:
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

# Index faq by language
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
# Utility: normalizzazione tokens
# -----------------------------
_nonword_re = re.compile(r"[^\wÀ-ž]+", flags=re.UNICODE)
def tokenize(text: str) -> List[str]:
    t = (text or "").lower()
    # normalize punctuation to spaces, keep diacritics
    parts = _nonword_re.split(t)
    return [p for p in parts if p]

# -----------------------------
# Costruzione dinamica token per famiglia
# -----------------------------
# Base manuale (se vuoi aggiungere singole sigle utili)
BASE_FAMILY_ALIASES = {
    "CTF": ["ctf", "connectors", "connecteur", "verbinder"],
    "CTL": ["ctl", "timber", "legno", "soletta"],
    "VCEM": ["vcem", "predrill", "preforo", "hardwood"],
    "CEM-E": ["ceme", "cem-e", "laterocemento"],
    "CTCEM": ["ctcem", "laterocemento", "ctcem"],
    "GTS": ["gts", "manicotto", "threaded"],
    "P560": ["p560", "spit p560", "pistola", "chiodatrice", "powder", "nailer"],
}

# Start with base aliases, then extend from overviews and FAQ tags/questions
FAM_TOKENS: Dict[str, List[str]] = {}
# Initialize from overviews if present
for item in OV_ITEMS:
    fam = (item.get("family") or "").upper()
    if not fam:
        continue
    toks = list(BASE_FAMILY_ALIASES.get(fam, []))
    # include family name and family lower
    toks.append(fam.lower())
    # also include name/title if present
    for k in ("title","name","aliases","keywords"):
        v = item.get(k)
        if isinstance(v, str):
            toks += tokenize(v)
        elif isinstance(v, list):
            for s in v:
                toks += tokenize(str(s))
    FAM_TOKENS[fam] = sorted(set([t for t in toks if t]))

# Ensure families from BASE_FAMILY_ALIASES exist
for fam, aliases in BASE_FAMILY_ALIASES.items():
    if fam not in FAM_TOKENS:
        FAM_TOKENS[fam] = sorted(set(aliases + [fam.lower()]))

# Enrich tokens with FAQ content/tags
for r in FAQ_ITEMS:
    tags = (r.get("tags") or "").lower()
    lang = r.get("lang") or "it"
    # try to guess family from tags like "ctf,p560" or from id pattern "FAQ::CTF"
    potential = []
    for tok in re.split(r"[,\s;/\-]+", tags):
        tok = tok.strip()
        if not tok:
            continue
        # if token equals a family alias, assign
        for fam, toks in list(FAM_TOKENS.items()):
            if tok in toks or tok == fam.lower():
                potential.append((fam,tok))
    # fallback: check question text tokens for family names
    qtokens = tokenize(r.get("question") or "")
    for qt in qtokens:
        for fam, toks in list(FAM_TOKENS.items()):
            if qt in toks or qt == fam.lower():
                potential.append((fam,qt))
    for fam, _ in potential:
        # add question words as tokens for that family
        FAM_TOKENS[fam] = sorted(set(FAM_TOKENS.get(fam,[]) + qtokens + tokenize(tags)))

# Final small cleaning: remove empty tokens
for fam in list(FAM_TOKENS.keys()):
    FAM_TOKENS[fam] = [t for t in FAM_TOKENS[fam] if t and len(t)>0]

# Add extra P560 richness manually as requested (common variants)
if "P560" in FAM_TOKENS:
    extras = [
        "spit","hsbr14","hsbr","chiodi","chiodo","cartuccia","cartucce","cartuccie",
        "propulsore","propulsori","adattatore","kit","kit adattatore","pat","powder-actuated",
        "nailgun","nailer","tool","gerät","outil","herramienta","cloueur"
    ]
    FAM_TOKENS["P560"] = sorted(set(FAM_TOKENS["P560"] + extras))

# -----------------------------
# Detect family — improved (return top N)
# -----------------------------
def detect_family_scores(text: str) -> List[Tuple[str,int]]:
    tkns = tokenize(text)
    text_join = " " + " ".join(tkns) + " "
    scores: List[Tuple[str,int]] = []
    for fam, toks in FAM_TOKENS.items():
        hits = 0
        fam_lower = fam.lower()
        # exact acronym boost
        if re.search(r"\b" + re.escape(fam_lower) + r"\b", text_join):
            hits += 4
        # tokens
        for tok in toks:
            if not tok:
                continue
            # word boundary check
            if re.search(r"\b" + re.escape(tok) + r"\b", text_join):
                hits += 1
        scores.append((fam, hits))
    # sort desc by hits
    scores.sort(key=lambda x: x[1], reverse=True)
    # return only those with >0 hits (or top few)
    return [(fam,sc) for fam,sc in scores if sc>0]

# -----------------------------
# Find best FAQ given a family and query
# -----------------------------
def best_faq_for_query(q: str, fam: str, lang: str) -> Tuple[Optional[Dict[str,str]], int]:
    q_toks = set(tokenize(q))
    best, best_score = None, -1
    # search first in same language then cross-lang
    tries = []
    if lang and lang in FAQ_BY_LANG:
        tries.append(FAQ_BY_LANG.get(lang, []))
    tries.append(FAQ_ITEMS)
    for rows in tries:
        for r in rows:
            # quick family filter: if tag contains family
            tags = (r.get("tags") or "").lower()
            if fam and fam.lower() not in tags and fam.lower() not in (r.get("id") or "").lower():
                # still allow: but de-prioritize
                pass
            # compute overlap score with question+tags
            keys = ((r.get("tags") or "") + " " + (r.get("question") or "")).lower()
            k_toks = set(tokenize(keys))
            score = 0
            # token overlap
            score += len(q_toks & k_toks) * 2
            # bonus if family appears in tags or question
            if fam and fam.lower() in keys:
                score += 4
            # small bonus for exact id match
            if (r.get("id") or "").lower().endswith(fam.lower()):
                score += 3
            # bonus if language matches
            if r.get("lang") == lang:
                score += 1
            # keep best
            if score > best_score:
                best_score, best = score, r
    return best, best_score

# -----------------------------
# Overview helper
# -----------------------------
def _find_overview(fam: str) -> str:
    fam = (fam or "").upper()
    for it in OV_ITEMS:
        if (it.get("family") or "").upper() == fam:
            return (it.get("answer") or "").strip()
    # fallback simple synthetic
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
# Intent router (migliorato)
# -----------------------------
def intent_route(q: str) -> Dict[str, Any]:
    ql = (q or "").strip()
    qln = ql.lower()
    lang = detect_lang(ql)

    # detect family scores
    fam_scores = detect_family_scores(ql)
    # if two families with positive hits -> compare candidate
    if len(fam_scores) >= 2:
        (fam1, s1), (fam2, s2) = fam_scores[0], fam_scores[1]
        # require minimal evidence to treat as compare (e.g. sum hits >= 2)
        if (s1 + s2) >= 2:
            # check CMP_ITEMS for existing compare doc
            found = None
            for it in CMP_ITEMS:
                fa = (it.get("famA") or "").upper()
                fb = (it.get("famB") or "").upper()
                if {fa, fb} == {fam1, fam2}:
                    found = it
                    break
            if found:
                html = found.get("html") or ""
                text = found.get("answer") or ""
                source = "compare"
            else:
                ansA = _find_overview(fam1)
                ansB = _find_overview(fam2)
                html = _compare_html(fam1, fam2, ansA, ansB)
                text = ""
                source = "synthetic"

            return {
                "ok": True,
                "match_id": f"COMPARE::{fam1}_VS_{fam2}",
                "lang": lang,
                "family": f"{fam1}+{fam2}",
                "intent": "compare",
                "source": source,
                "score": float(90 + min(10, s1 + s2)),
                "text": text,
                "html": html,
            }

    # 1 family candidate (best)
    if len(fam_scores) >= 1:
        best_fam, best_hits = fam_scores[0]
        # try to match FAQ in best language
        best_row, best_score = best_faq_for_query(ql, best_fam, lang)
        # if best_score sufficiently high, return faq
        if best_row and best_score >= 2:
            return {
                "ok": True,
                "match_id": best_row.get("id") or f"FAQ::{best_fam}",
                "lang": best_row.get("lang") or lang,
                "family": best_fam,
                "intent": "faq",
                "source": "faq",
                "score": float(85 if best_hits>=2 else 72) + float(min(10,best_score)),
                "text": best_row.get("answer") or "",
                "html": ""
            }
        # else return overview for family
        ov = _find_overview(best_fam)
        return {
            "ok": True,
            "match_id": f"OVERVIEW::{best_fam}",
            "lang": lang,
            "family": best_fam,
            "intent": "overview",
            "source": "overview",
            "score": 70.0 + float(min(10,best_hits)),
            "text": ov,
            "html": ""
        }

    # Fallback: try to match any FAQ by pure token overlap (cross-family)
    # Try to find best FAQ globally
    best_row, best_score = None, -1
    q_toks = set(tokenize(ql))
    for r in FAQ_ITEMS:
        keys = ((r.get("tags") or "") + " " + (r.get("question") or "")).lower()
        k_toks = set(tokenize(keys))
        score = len(q_toks & k_toks)
        if score > best_score:
            best_score, best_row = score, r
    if best_row and best_score >= 2:
        return {
            "ok": True,
            "match_id": best_row.get("id") or "<NULL>",
            "lang": best_row.get("lang"),
            "family": "<inferred>",
            "intent": "faq",
            "source": "faq",
            "score": 60.0 + best_score,
            "text": best_row.get("answer") or "",
            "html": ""
        }

    # final fallback
    return {
        "ok": True,
        "match_id": "<NULL>",
        "lang": lang,
        "family": "",
        "intent": "fallback",
        "source": "fallback",
        "score": 0,
        "text": "Non ho trovato una risposta diretta nei metadati locali. Specifica meglio la famiglia/prodotto.",
        "html": ""
    }

# -----------------------------
# Endpoints
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
