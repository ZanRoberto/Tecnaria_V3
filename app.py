# app.py — Tecnaria_V3 (FastAPI)
# --------------------------------
# Endpoints:
#   GET  /                 -> info base
#   GET  /health           -> stato + righe FAQ caricate
#   GET  /ui               -> "interfaccia" JSON con esempi e help
#   GET  /api/ask?q=...    -> risposta (stessa logica del POST)
#   POST /api/ask          -> body { q: "..." } -> risposta

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

    # Fix mojibake frequenti (troncate al minimo indispensabile)
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
    "es": [r"¿", r"\bqué\b", r"\bque\b", r"\bcómo\b", r"\bcomo\b", r"\bconector(es)?\b"],
    "fr": [r"\bquoi\b", r"\bcomment\b", r"\bqu['’]?(?:est[- ]ce|estce)\b", r"\bquel(le|s)?\b", r"\bconnecteur(s)?\b"],
    "de": [r"\bwas\b", r"\bwie\b", r"\bist\b", r"\bverbinder\b"],
}
def detect_lang(q: str) -> str:
    s = (q or "").lower()
    for lang, pats in _LANG_PATTERNS.items():
        for p in pats:
            if re.search(p, s):
                return lang
    # segni di punteggiatura iniziale spagnola
    if "¿" in s or "¡" in s:
        return "es"
    return "it"

# -----------------------------
# Token famiglie (multilingua, più ricchi)
# -----------------------------
FAM_TOKENS: Dict[str, List[str]] = {
    "CTF": [
        # IT
        "ctf","connettore","connettori","trave acciaio","lamiera grecata","acciaio-calcestruzzo",
        "solaio collaborante","shear","collaborante","chiodatrice","sparo","deck","decking",
        # EN
        "connector","connectors","steel beam","steel deck","composite slab","shear connector",
        # FR
        "connecteur","connecteurs","poutrelle acier","tôle nervurée","dalle mixte","connecteur de cisaillement",
        # ES
        "conector","conectores","viga de acero","chapa colaborante","forjado colaborante","conector de cortante",
        # DE
        "verbinder","schubverbinder","stahlträger","trapezblech","verbunddecke","schub"
    ],
    "CTL": [
        # IT
        "ctl","soletta","calcestruzzo","collaborazione","legno","solaio legno-calcestruzzo","trave legno",
        # EN
        "timber-concrete composite","tcc","timber beam","concrete topping","composite slab",
        # FR
        "mixte bois-béton","poutre bois","dalle collaborative",
        # ES
        "mixto madera-hormigón","viga de madera","losa colaborante",
        # DE
        "holz-beton-verbund","holzträger","verbundplatte"
    ],
    "VCEM": [
        # IT
        "vcem","vite","viti","legno","essenze dure","preforo","preforo 70","preforo 80","70%","80%","durezza",
        # EN
        "predrill","pre-drill","pilot hole","hardwood","dense timber","pre-drilling 70 80",
        # FR
        "pré-perçage","bois dur","avant-trou",
        # ES
        "pretaladro","madera dura","taladro previo",
        # DE
        "vorbohren","hartes holz","vorbohrung"
    ],
    "CEM-E": [
        # IT
        "cem-e","ceme","posa a secco","senza resine","laterocemento","cappello","ripristino",
        # EN
        "dry installation","no resin","hollow brick","clay block","rc slab",
        # FR
        "pose à sec","sans résine","brique creuse","bloc hourdis",
        # ES
        "colocación en seco","sin resinas","ladrillo hueco","bloque cerámico",
        # DE
        "trockene montage","ohne harz","lochziegel","hohlblock"
    ],
    "CTCEM": [
        # IT
        "ctcem","malta","malte","cappa","cappa collaborante","laterocemento","ripristino","integrazione",
        # EN
        "mortar","cement mortar","topping","composite topping","rc slab",
        # FR
        "mortier","chape","dalle","renforcement","béton",
        # ES
        "mortero","capa de compresión","forjado","refuerzo",
        # DE
        "mörtel","estrich","beton","verbund"
    ],
    "GTS": [
        # IT
        "gts","manicotto","manicotto filettato","giunzione","giunzioni a secco","barra filettata",
        # EN
        "threaded sleeve","sleeve","threaded coupling","dry joint","rod",
        # FR
        "manchon fileté","manchon","raccord fileté","assemblage à sec",
        # ES
        "manguito roscado","manguito","acoplamiento roscado","ensamble en seco",
        # DE
        "gewindehülse","kupplung","hülse","trockenverbindung"
    ],
    "P560": [
        # sigle / marchio / varianti
        "p560","spit","spit p560","spit-p560",
        # IT: utensile & concetti
        "chiodatrice","pistola","utensile","attrezzatura","propulsori","propulsore",
        "cartucce","cartuccia","gialle","verdi","rosse","dosaggio","regolazione potenza",
        "chiodi","chiodo","hsbr14","hsbr 14","adattatore","kit adattatore",
        "spari","sparo","colpo","tiro","sicura","marcatura","marcatura ce",
        # EN
        "powder","powder-actuated","powder actuated","pat","nailer","nailgun",
        "cartridge","cartridges","magazine","trigger","safety","tool",
        # FR / ES / DE
        "gerät","nagler","werkzeug","outil","cloueur","outil à poudre",
        "herramienta","clavos",
        # contesto tipico
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

    # 1) Confronti A vs B (se compaiono entrambi i token famiglia)
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
        # 2a) FAQ prima nella lingua rilevata, poi cross-lingua
        best_row: Optional[Dict[str, str]] = None
        best_score: int = -1

        def try_rows(rows: List[Dict[str, str]]):
            nonlocal best_row, best_score
            for r in rows:
                keys = ((r.get("tags") or "") + " " + (r.get("question") or "")).lower()
                # punteggio semplice = numero di "token" che compaiono nella query
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
