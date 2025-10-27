# app.py — Tecnaria_V3 (CTF + CTL, semantico + confronti + UI inline)
# Compatibile con: fastapi==0.103.2, pydantic==1.10.13, starlette==0.27.0, uvicorn==0.22.0, gunicorn==22.0.0
# Endpoint principali:
#   GET  /            -> info base
#   GET  /health      -> stato
#   GET  /ui          -> interfaccia HTML (embedded, nessun file statico)
#   GET  /api/ask?q=  -> risposta
#   POST /api/ask     -> body { "q": "..." }

from __future__ import annotations

import re, csv, json, math, time
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional
from fastapi import FastAPI, Query
from fastapi.responses import PlainTextResponse, JSONResponse, HTMLResponse
from pydantic import BaseModel

app = FastAPI(title="Tecnaria_V3")

# -------------------------------------------------------
# Dati / percorsi
# -------------------------------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "static" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)  # non esplode se manca

CSV_FILES = {
    "CTF": DATA_DIR / "tecnaria_ctf.csv",
    "CTL": DATA_DIR / "tecnaria_ctl.csv",
}

# -------------------------------------------------------
# Sinonimi / alias famiglie (per intent + confronti)
# -------------------------------------------------------
FAMILY_ALIASES: Dict[str, List[str]] = {
    "CTF": [
        "ctf", "connettori ctf", "shear connector ctf", "connettore a sparo",
        "connettore lamiera grecata", "connettori acciaio-calcestruzzo",
        "connector ctf", "ctf connectors", "connettore per trave in acciaio",
        "sparafissaggio", "a sparo", "powder-actuated", "spit p560"
    ],
    "CTL": [
        "ctl", "connettori ctl", "connettore legno-calcestruzzo", "connettori per legno",
        "shear connector timber", "collaborazione legno calcestruzzo", "soletta su legno",
        "ctl maxi", "ctl standard", "tecnaria ctl"
    ],
}

# -------------------------------------------------------
# Golden embedded — base sicura se i CSV non ci sono
# (ridotti ma tecnici e narrativi; puoi ampliare via CSV)
# -------------------------------------------------------
EMBEDDED_QA: List[Dict[str, str]] = [
    # ===== CTF =====
    {
        "id": "CTF_001",
        "family": "CTF",
        "lang": "it",
        "question": "I CTF si possono posare con una normale chiodatrice a sparo?",
        "answer": (
            "No: per i CTF è autorizzata la sola chiodatrice SPIT P560 con kit/adattatori Tecnaria. "
            "Ogni connettore usa 2 chiodi HSBR14 e propulsori P560 scelti in base al supporto. "
            "Utensili non approvati non sono ammessi e invalidano la procedura. "
            "Verifica spessori minimi (trave acciaio ≥ 6 mm; con lamiera: tipicamente 1×1,5 mm o 2×1,0 mm ben aderenti)."
        ),
        "tags": "ctf a sparo spit p560 hsbr14 cartucce propulsori lamiera grecata trave acciaio"
    },
    {
        "id": "CTF_002",
        "family": "CTF",
        "lang": "it",
        "question": "CTF su lamiera grecata: quali controlli in cantiere?",
        "answer": (
            "Prima della posa: identifica il connettore (CTF020/025/030…), verifica marcatura e conformità ETA; "
            "controlla integrità e spessori della lamiera e l’aderenza alla trave. "
            "Durante la posa: centratura nel solco, 2 chiodi HSBR14 a completa penetrazione con SPIT P560; mai risparare nello stesso foro. "
            "Dopo la posa: controlli a campione (verticalità, numero chiodi, fissaggio solido), pulizia zona getto, "
            "registrazione fotografica e, se richieste, prove di trazione."
        ),
        "tags": "controlli posa cantiere lamiera grecata p560 hsbr14 verticalità prove trazione"
    },
    {
        "id": "CTF_003",
        "family": "CTF",
        "lang": "it",
        "question": "Qual è la logica d’uso dei propulsori P560 con i CTF?",
        "answer": (
            "I propulsori P560 si scelgono in funzione della durezza e dello spessore del supporto (trave + eventuale lamiera). "
            "Scopo: ottenere completa infissione dei chiodi HSBR14 senza eccesso di energia. "
            "Regola pratica: partire da potenze medio-basse ed aumentare fino a infissione corretta e ripetibile."
        ),
        "tags": "propulsori p560 cartucce energia durezza spessore supporto infissione hsbr14"
    },

    # ===== CTL =====
    {
        "id": "CTL_001",
        "family": "CTL",
        "lang": "it",
        "question": "Devo usare connettori Tecnaria per travi in legno su tavolato 2 cm con soletta 5 cm: quale modello?",
        "answer": (
            "Usa CTL MAXI 12/040 (gambo 40 mm), posato sopra il tavolato con 2 viti Ø10 (tipicamente 100 mm; "
            "se interposti/tavolato > 25–30 mm, usa 120 mm). Con soletta 5 cm, il 40 mm resta ben annegato e la testa va sopra la rete a metà spessore. "
            "Alternativa 12/030 se interferisce con armature superiori; scegli l’altezza per avere la testa sopra la rete ma sotto il filo superiore del getto."
        ),
        "tags": "ctl maxi 12/040 tavolato soletta 5 cm viti ø10 100 120 rete metà spessore"
    },
    {
        "id": "CTL_002",
        "family": "CTL",
        "lang": "it",
        "question": "Posa CTL: requisiti minimi della soletta e accorgimenti di getto",
        "answer": (
            "Soletta ≥ 5 cm (C25/30 o leggero strutturale) con rete a metà spessore. "
            "Garantire copriferro e continuità di getto attorno alle teste dei connettori. "
            "Armonizzare interasse e allineamento con il progetto, curando l’aderenza tavolato/trave "
            "e la corretta lunghezza viti (Ø10 x 100/120/140 mm)."
        ),
        "tags": "soletta minima rete metà spessore c25/30 leggero interasse viti ø10 posa legno"
    },
    {
        "id": "CTL_003",
        "family": "CTL",
        "lang": "it",
        "question": "Quando ha senso preferire CTL MAXI a CTL standard?",
        "answer": (
            "CTL MAXI è indicato quando si posa sopra assito/tavolato e serve gambo più alto per una migliore presa nel calcestruzzo "
            "e corretta posizione della testa rispetto alla rete. CTL standard è adatto a situazioni con ingombri ridotti o "
            "quando si vuole limitare l’altezza del connettore sopratutto in presenza di interferenze."
        ),
        "tags": "ctl maxi vs standard assito altezza gambo rete interferenze ingombri"
    },
]

# -------------------------------------------------------
# Linguaggi semplici (euristico)
# -------------------------------------------------------
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

# -------------------------------------------------------
# Util: normalizzazione e tokenizzazione
# -------------------------------------------------------
_ws = re.compile(r"[^\w]+", re.UNICODE)
def norm(s: str) -> str:
    return _ws.sub(" ", (s or "").lower()).strip()

def tokenize(s: str) -> List[str]:
    return [t for t in norm(s).split() if t]

def contains_any(text: str, variants: List[str]) -> bool:
    t = " " + norm(text) + " "
    return any((" " + norm(v) + " ") in t for v in variants)

# -------------------------------------------------------
# Caricamento CSV (tollerante) + merge con embedded
# CSV attesi: id,lang,question,answer,tags  (+family opzionale)
# -------------------------------------------------------
def load_csv_if_exists(path: Path, fallback_family: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    if not path.exists():
        return rows
    def _read(enc: str):
        with path.open("r", encoding=enc, newline="") as f:
            rdr = csv.DictReader(f)
            for r in rdr:
                rows.append({
                    "id": (r.get("id") or "").strip() or f"{fallback_family}_auto_{len(rows)+1}",
                    "family": (r.get("family") or fallback_family).strip().upper(),
                    "lang": (r.get("lang") or "it").strip().lower(),
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
            return []
    return rows

def build_knowledge() -> List[Dict[str, str]]:
    items = list(EMBEDDED_QA)  # base sicura
    # CSV CTF / CTL se presenti
    items += load_csv_if_exists(CSV_FILES["CTF"], "CTF")
    items += load_csv_if_exists(CSV_FILES["CTL"], "CTL")
    # normalizza family
    for r in items:
        r["family"] = (r.get("family") or "").upper() or "CTF"
        r["lang"] = (r.get("lang") or "it").lower()
    return items

KB: List[Dict[str, str]] = build_knowledge()

# indice per lingua
KB_BY_LANG: Dict[str, List[Dict[str, str]]] = {}
for r in KB:
    KB_BY_LANG.setdefault(r["lang"], []).append(r)

# -------------------------------------------------------
# Scoring semantico leggero (no dipendenze esterne)
# - token overlap
# - boost famiglia se presente in query
# - match su tags/alias
# -------------------------------------------------------
def which_families_in_query(q: str) -> List[str]:
    hits = []
    for fam, aliases in FAMILY_ALIASES.items():
        if contains_any(q, [fam] + aliases):
            hits.append(fam)
    return hits

def score_item(q_tokens: List[str], item: Dict[str, str]) -> float:
    # token overlap su question+tags
    ref = tokenize(item.get("question", "") + " " + item.get("tags", ""))
    if not ref:
        return 0.0
    inter = len(set(q_tokens) & set(ref))
    base = inter / math.sqrt(len(ref))
    # piccolo boost se la family è citata in query
    fam = item.get("family", "")
    alias_hit = 1.0 if fam in which_families_in_query(" ".join(q_tokens)) else 0.0
    return base + 0.5 * alias_hit

def best_match(q: str, lang: str) -> Tuple[Optional[Dict[str, str]], float]:
    q_tokens = tokenize(q)
    pool = KB_BY_LANG.get(lang, KB) or KB
    best, best_s = None, 0.0
    for r in pool:
        s = score_item(q_tokens, r)
        if s > best_s:
            best, best_s = r, s
    # se il punteggio è troppo basso, prova cross-lingua
    if (best is None or best_s < 0.3) and lang != "it":
        for r in KB:
            s = score_item(q_tokens, r)
            if s > best_s:
                best, best_s = r, s
    return best, best_s

# -------------------------------------------------------
# OVERVIEW sintetiche (fallback confronto)
# -------------------------------------------------------
OVERVIEW = {
    "CTF": (
        "CTF: connettori a taglio per solai misti acciaio–calcestruzzo, posa meccanica con SPIT P560 e chiodi HSBR14. "
        "Indicati su travi in acciaio con o senza lamiera grecata; controllo di infissione e spessori minimi secondo ETA."
    ),
    "CTL": (
        "CTL: connettori per sistemi legno–calcestruzzo; prevedono soletta ≥ 5 cm con rete a metà spessore e fissaggi con viti Ø10. "
        "La variante MAXI offre maggiore gambo per posa su assito/tavolato."
    ),
}

def build_compare_html(a: str, b: str, qa_a: Optional[Dict[str,str]], qa_b: Optional[Dict[str,str]]) -> str:
    ans_a = qa_a["answer"] if qa_a else OVERVIEW.get(a, "")
    ans_b = qa_b["answer"] if qa_b else OVERVIEW.get(b, "")
    return (
        "<div style='display:flex;gap:24px;flex-wrap:wrap'>"
        f"<div style='flex:1;min-width:320px'><h3>{a}</h3><p>{ans_a}</p>"
        f"<p><small>Fonte: {'FAQ' if qa_a else 'OVERVIEW'}::{a}</small></p></div>"
        f"<div style='flex:1;min-width:320px'><h3>{b}</h3><p>{ans_b}</p>"
        f"<p><small>Fonte: {'FAQ' if qa_b else 'OVERVIEW'}::{b}</small></p></div>"
        "</div>"
    )

# -------------------------------------------------------
# Intent routing
# -------------------------------------------------------
def route_intent(q: str) -> Dict[str, Any]:
    q_clean = (q or "").strip()
    lang = detect_lang(q_clean)
    fam_in_q = which_families_in_query(q_clean)

    # 1) confronto CTF vs CTL se compaiono entrambi o parole chiave "differenza/versus/vs"
    is_compare_kw = bool(re.search(r"\b(differenz|differenza|vs|versus|confront|compare)\b", q_clean.lower()))
    if ("CTF" in fam_in_q and "CTL" in fam_in_q) or (is_compare_kw and ("ctf" in q_clean.lower() and "ctl" in q_clean.lower())):
        qa_ctf, _ = best_match(q_clean + " CTF", lang)
        qa_ctl, _ = best_match(q_clean + " CTL", lang)
        html = "<h2>Confronto: CTF vs CTL</h2>" + build_compare_html("CTF","CTL", qa_ctf, qa_ctl)
        return {
            "ok": True,
            "match_id": "COMPARE::CTF_V
