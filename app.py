# app.py — Tecnaria_V3 (pulito, pronto per Render)

from typing import List, Dict, Any
from pathlib import Path
from fastapi import FastAPI
from pydantic import BaseModel
import time, re, csv, json

# -------------------------------------------------
# FastAPI
# -------------------------------------------------
app = FastAPI(title="Tecnaria_V3")

# -------------------------------------------------
# Dati (cartella: static/data)
# -------------------------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "static" / "data"
OV_JSON = DATA_DIR / "tecnaria_overviews.json"   # panoramiche famiglie
CMP_JSON = DATA_DIR / "tecnaria_compare.json"    # confronti A vs B
FAQ_CSV = DATA_DIR / "faq.csv"                   # domande/risposte brevi multi-lingua

def load_json(path: Path, fallback: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f) or []
                if isinstance(data, list):
                    return data
    except Exception:
        pass
    return fallback or []

# === CSV robusto (UTF-8/CP1252 + fix mojibake) ===
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
                    "lang": (r.get("lang") or "").strip().lower() or "it",
                    "question": (r.get("question") or "").strip(),
                    "answer": (r.get("answer") or "").strip(),
                    "tags": (r.get("tags") or "").strip().lower(),
                })

    try:
        _read("utf-8-sig")      # preferito (gestisce anche BOM)
    except Exception:
        try:
            _read("cp1252")     # fallback per file salvati in Windows
        except Exception:
            return rows

    # normalizza artefatti comuni (— ’ … accenti, euro, ecc.)
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

# Contatori esposti
JSON_BAG = {
    "overviews": OV_ITEMS,
    "compare": CMP_ITEMS,
    "faq": FAQ_ITEMS,
}
FAQ_ROWS = len(FAQ_ITEMS)

# -------------------------------------------------
# Indici + euristiche
# -------------------------------------------------
FAQ_BY_LANG: Dict[str, List[Dict[str, str]]] = {}
for r in FAQ_ITEMS:
    FAQ_BY_LANG.setdefault(r["lang"], []).append(r)

def detect_lang(q: str) -> str:
    s = (q or "").lower()
    if any(w in s for w in [" the ", " what ", " how ", " can ", " shall ", " should ", "?"]): return "en"
    if any(w in s for w in [" el ", " los ", " las ", "¿", "qué", "como", "cómo"]): return "es"
    if any(w in s for w in [" le ", " la ", " les ", " quelle", " comment", "qu’est"]): return "fr"
    if any(w in s for w in [" der ", " die ", " das ", " wie ", " was "]): return "de"
    return "it"

# Token famiglie (ITA + EN)
FAM_TOKENS: Dict[str, List[str]] = {
    "CTF": [
        "ctf","lamiera","p560","hsbr14","trave","chiodatrice","sparo",
        "steel deck","profiled sheet","beam","nailer","nailing"
    ],
    "CTL": [
        "ctl","soletta","calcestruzzo","collaborazione","legno",
        "timber","concrete topping","tcc","composite timber"
    ],
    "VCEM": [
        "vcem","preforo","vite","legno","essenze","durezza",
        "hardwood","hardwoods","predrill","pre-drill","pilot","screw","70","80"
    ],
    "CEM-E": [
        "ceme","laterocemento","secco","senza resine","cappello",
        "hollow-block","dry","resin-free"
    ],
    "CTCEM": [
        "ctcem","laterocemento","secco","senza resine","cappa",
        "hollow-block","dry","resin-free"
    ],
    "GTS": [
        "gts","manicotto","filettato","giunzioni","secco",
        "threaded","sleeve","joint","mechanical"
    ],
    "P560": [
        "p560","chiodatrice","propulsori","hsbr14",
        "nailer","cartridge","cartridges","propellants","tool"
    ],
}

def _score_tokens(text: str, tokens: List[str]) -> float:
    t = (" " + (text or "").lower() + " ")
    hits = sum(1 for tok in tokens if tok in t)
    return hits / max(1, len(tokens))

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

# -------------------------------------------------
# Intent router
# -------------------------------------------------
def intent_route(q: str) -> Dict[str, Any]:
    ql = (q or "").lower().strip()
    lang = detect_lang(ql)

    # Regola esplicita per EN: VCEM + hardwoods/predrill -> FAQ::VCEM
    if "vcem" in ql and any(k in ql for k in ["hardwood","hardwoods","predrill","pre-drill","pilot","70","80"]):
        lang_pref = "en" if any(k in ql for k in ["hardwood","hardwoods","predrill","pre-drill","pilot"]) else lang
        pool = FAQ_BY_LANG.get(lang_pref) or FAQ_BY_LANG.get("it") or []
        ans = ""
        for r in pool:
            if (r.get("id") or "").upper().startswith("FAQ::VCEM"):
                ans = r.get("answer") or ""
                break
        return {
            "ok": True, "match_id": "FAQ::VCEM", "lang": lang_pref,
            "family": "VCEM", "intent": "faq", "source": "faq", "score": 95.0,
            "text": ans, "html": ""
        }

    # 1) Confronti A vs B
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
    scored = [(fam, _score_tokens(ql, toks)) for fam, toks in FAM_TOKENS.items()]
    scored.sort(key=lambda x: x[1], reverse=True)
    fam, s = scored[0]
    if s >= 0.2:
        # 2a) FAQ
        for r in FAQ_BY_LANG.get(lang, []):
            keys = (r["tags"] or "") + " " + r["question"]
            if _score_tokens(ql, re.split(r"[,\s;/\-]+", keys.lower())) >= 0.25:
                return {
                    "ok": True, "match_id": r["id"] or f"FAQ::{fam}", "lang": lang,
                    "family": fam, "intent": "faq", "source": "faq", "score": 88.0,
                    "text": r["answer"], "html": ""
                }
        # 2b) overview
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

# -------------------------------------------------
# Endpoint di servizio
# -------------------------------------------------
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
        return {
            "ok": True,
            "json_loaded": list(JSON_BAG.keys()),
            "faq_rows": FAQ_ROWS
        }
    except Exception:
        return {"ok": True}

# -------------------------------------------------
# /api/ask locale (utile anche su Render)
# -------------------------------------------------
class AskIn(BaseModel):
    q: str

class AskOut(BaseModel):
    ok: bool
    match_id: str
    ms: int
    text: str | None = ""
    html: str | None = ""
    lang: str | None = None
    family: str | None = None
    intent: str | None = None
    source: str | None = None
    score: float | int | None = None

@app.post("/api/ask", response_model=AskOut)
def api_ask_local(body: AskIn) -> AskOut:
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
