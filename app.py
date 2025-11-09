import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import HTMLResponse
import re

# =========================
# CONFIGURAZIONE BASE
# =========================

USE_OPENAI = True  # Sinapsi attivo se c'è la chiave OPENAI_API_KEY

try:
    from openai import OpenAI
    if USE_OPENAI and os.getenv("OPENAI_API_KEY"):
        openai_client = OpenAI()
    else:
        openai_client = None
except Exception:
    openai_client = None

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = STATIC_DIR / "data"
INDEX_HTML = STATIC_DIR / "index.html"

app = FastAPI(title="Tecnaria Sinapsi — Q/A")

# Cache famiglie
_family_cache: Dict[str, List[Dict[str, Any]]] = {}

# Mappa famiglie per lock esplicito
FAMILY_KEYWORDS = {
    "ctf": "CTF",
    "ctl maxi": "CTL_MAXI",
    "ctl_maxi": "CTL_MAXI",
    "ctl": "CTL",
    "vcem": "VCEM",
    "ctcem": "CTCEM",
    "p560": "P560",
    "diapason": "DIAPASON",
}

# =========================
# UTILS LETTURA / KB
# =========================

def safe_read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def extract_blocks(data: Any) -> List[Dict[str, Any]]:
    """
    Estrae blocchi informativi dai JSON:
    - { "items": [...] } o { "blocks": [...] } o { "data": [...] }
    - oppure lista diretta di blocchi.
    """
    blocks: List[Dict[str, Any]] = []

    if isinstance(data, list):
        blocks = [b for b in data if isinstance(b, dict)]
    elif isinstance(data, dict):
        for key in ("items", "blocks", "data"):
            v = data.get(key)
            if isinstance(v, list):
                blocks = [b for b in v if isinstance(b, dict)]
                break

    for i, b in enumerate(blocks):
        if "id" not in b:
            b["id"] = f"AUTO-{i:04d}"

    return blocks

def load_family(family: str) -> List[Dict[str, Any]]:
    """
    Carica il dataset di una famiglia.
    Cerca il file principale:
      FAM.json, FAM.gold.json, FAM.golden.json.
    Se non trovati, prende il primo FAM*.json valido (fallback).
    """
    fam = family.upper()
    if fam in _family_cache:
        return _family_cache[fam]

    # 1) nomi standard
    candidates = [
        DATA_DIR / f"{fam}.json",
        DATA_DIR / f"{fam}.gold.json",
        DATA_DIR / f"{fam}.golden.json",
    ]
    path: Optional[Path] = next((p for p in candidates if p.exists()), None)

    # 2) fallback: un qualsiasi FAM*.json (utile se hai P560 (2).json ecc.)
    if path is None and DATA_DIR.exists():
        for f in DATA_DIR.glob(f"{fam}*.json"):
            name_up = f.name.upper()
            if "CONFIG.RUNTIME" in name_up:
                continue
            path = f
            break

    if path is None:
        raise HTTPException(status_code=404, detail=f"File JSON per famiglia '{family}' non trovato.")

    data = safe_read_json(path)
    blocks = extract_blocks(data)
    _family_cache[fam] = blocks
    return blocks

def list_all_families() -> List[str]:
    fams: List[str] = []
    if not DATA_DIR.exists():
        return fams

    for f in DATA_DIR.glob("*.json"):
        name = f.stem.upper()
        if "CONFIG.RUNTIME" in name:
            continue
        if name.endswith(".GOLD"):
            name = name[:-5]
        fams.append(name)

    return sorted(set(fams))

# =========================
# MATCHING / SCORING
# =========================

def norm(s: str) -> str:
    return " ".join(s.lower().strip().split())

def extract_queries(block: Dict[str, Any]) -> List[str]:
    """
    Testi usati per il matching semantico.
    """
    out: List[str] = []

    # Campi singoli
    for key in ("q", "question", "domanda", "title", "label"):
        v = block.get(key)
        if isinstance(v, str):
            v = v.strip()
            if v:
                out.append(v)

    # Liste di frasi
    for key in ("questions", "paraphrases", "variants", "triggers"):
        v = block.get(key)
        if isinstance(v, list):
            for e in v:
                if isinstance(e, str):
                    e = e.strip()
                    if e:
                        out.append(e)

    # Tag
    tags = block.get("tags")
    if isinstance(tags, list):
        for t in tags:
            if isinstance(t, str):
                t = t.strip()
                if t:
                    out.append(t)

    # Canonical come contesto (accorciato)
    canon = block.get("canonical")
    if isinstance(canon, str):
        c = canon.strip()
        if c:
            out.append(c[:220])

    return out

def base_similarity(query: str, block: Dict[str, Any]) -> float:
    q = norm(query)
    if not q:
        return 0.0

    queries = extract_queries(block)
    if not queries:
        return 0.0

    sq = set(q.split())
    if not sq:
        return 0.0

    best = 0.0

    for cand in queries:
        c = norm(cand)
        if not c:
            continue

        # match forte
        if q == c:
            return 1.0

        # contenimento
        if q in c or c in q:
            best = max(best, 0.9)
            continue

        sc = set(c.split())
        if not sc:
            continue

        inter = len(sq & sc)
        if inter == 0:
            continue

        j = inter / len(sq | sc)
        if j > best:
            best = j

    return float(best)

def detect_lang(query: str) -> str:
    q = query.lower()
    if any(x in q for x in [" soletta", "connettore", "trave", "calcestruzzo", "lamiera", "pistola", "cartucce"]):
        return "it"
    if any(x in q for x in ["beam", "steel", "composite", "connector"]):
        return "en"
    if any(x in q for x in ["béton", "connecteur"]):
        return "fr"
    if any(x in q for x in ["conectores", "hormigón"]):
        return "es"
    if any(x in q for x in ["verbinder", "beton"]):
        return "de"
    return "it"

def detect_explicit_families(query: str) -> List[str]:
    """
    Se l'utente scrive CTF, VCEM, CTL, P560 ecc. → lock su quelle famiglie.
    """
    q = query.lower()
    hits: List[str] = []

    if "ctl maxi" in q or "ctl_maxi" in q:
        hits.append("CTL_MAXI")

    for key, fam in FAMILY_KEYWORDS.items():
        if key in ("ctl maxi", "ctl_maxi"):
            continue
        if re.search(r"\b" + re.escape(key) + r"\b", q):
            if fam not in hits:
                hits.append(fam)

    return hits

def score_block_routed(query: str,
                       block: Dict[str, Any],
                       fam: str,
                       explicit_fams: List[str]) -> float:
    """
    Applica:
    - base_similarity
    - family lock se citata esplicitamente
    - heuristiche P560/legno/laterocemento
    """
    base = base_similarity(query, block)
    if base <= 0:
        return 0.0

    fam_u = fam.upper()
    q_low = query.lower()

    # Lock esplicito
    if explicit_fams:
        if fam_u in explicit_fams:
            base *= 8.0
        else:
            base *= 0.05
        return base

    # Heuristica P560
    if any(k in q_low for k in ["p560", "pistola", "chiodatrice", "sparo", "cartuccia", "cartucce"]):
        if fam_u == "P560":
            base *= 5.0
        else:
            base *= 0.4

    # Heuristica CTL / CTL_MAXI → legno
    if "legno" in q_low or "trave in legno" in q_low:
        if fam_u in ["CTL", "CTL_MAXI"]:
            base *= 3.0
        elif fam_u in ["CTF", "VCEM", "CTCEM", "P560", "DIAPASON"]:
            base *= 0.4

    # Heuristica VCEM / CTCEM / DIAPASON → laterocemento
    if any(k in q_low for k in ["laterocemento", "travetto", "travetti"]):
        if fam_u in ["VCEM", "CTCEM", "DIAPASON"]:
            base *= 3.0
        elif fam_u in ["CTF", "CTL", "CTL_MAXI", "P560"]:
            base *= 0.4

    return base

# =========================
# COSTRUZIONE RISPOSTA GOLD
# =========================

def extract_answer(block: Dict[str, Any], lang: str = "it") -> Optional[str]:
    """
    Costruisce una base GOLD:
    - mai solo canonical secco,
    - combina answers[lang], answer_it, canonical, response_variants.
    """
    pieces: List[str] = []

    # answers multilingua
    answers = block.get("answers")
    if isinstance(answers, dict):
        for key in (lang, lang.lower(), lang.upper()):
            v = answers.get(key)
            if isinstance(v, str) and v.strip():
                pieces.append(v.strip())
                break
        if not pieces:
            for v in answers.values():
                if isinstance(v, str) and v.strip():
                    pieces.append(v.strip())
                    break

    # answer_it
    answer_it = block.get("answer_it")
    if isinstance(answer_it, str) and answer_it.strip():
        if all(answer_it.strip() not in p for p in pieces):
            pieces.append(answer_it.strip())

    # canonical
    canonical = block.get("canonical")
    if isinstance(canonical, str) and canonical.strip():
        if all(canonical.strip() not in p for p in pieces):
            pieces.append(canonical.strip())

    # response_variants
    variants_raw = block.get("response_variants")
    variants: List[str] = []

    if isinstance(variants_raw, list):
        variants = [v.strip() for v in variants_raw if isinstance(v, str) and v.strip()]
    elif isinstance(variants_raw, dict):
        for v in variants_raw.values():
            if isinstance(v, list):
                for e in v:
                    if isinstance(e, str) and e.strip():
                        variants.append(e.strip())
            elif isinstance(v, str) and v.strip():
                variants.append(v.strip())

    if variants:
        variants_sorted = sorted(variants, key=len, reverse=True)
        for v in variants_sorted:
            if not any(v in p or p in v for p in pieces):
                pieces.append(v)
                break

    # fallback legacy
    if not pieces:
        for key in ("answer", "risposta", "text", "content"):
            v = block.get(key)
            if isinstance(v, str) and v.strip():
                pieces.append(v.strip())
                break

    if not pieces:
        return None

    base = " ".join(pieces[:2]).strip()
    return base if base else None

def generate_gold_answer(question: str,
                         base: str,
                         block: Dict[str, Any],
                         family: str,
                         lang: str) -> str:
    """
    Se OpenAI è attivo: rifinisce base GOLD.
    Se no: restituisce base così com'è.
    """
    if not USE_OPENAI or openai_client is None:
        return base

    try:
        resp = openai_client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            temperature=0.35,
            max_tokens=1500,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Sei Sinapsi, assistente tecnico-commerciale di Tecnaria. "
                        "Rispondi in modo completo, naturale e professionale, "
                        "tono GOLD dinamico, tecnico chiaro, esempi di cantiere, "
                        "nessuna frase generica vuota. "
                        "Rispetta rigorosamente il campo di impiego della famiglia "
                        "e i contenuti del blocco dati fornito. "
                        "Non inventare prodotti o usi non previsti."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"LINGUA: {lang}\n"
                        f"FAMIGLIA: {family}\n"
                        f"DOMANDA: {question}\n\n"
                        f"BLOCCO DATI (JSON): {json.dumps(block, ensure_ascii=False)}\n\n"
                        f"BASE GOLD (da rifinire senza stravolgere): {base}"
                    ),
                },
            ],
        )
        text = resp.choices[0].message.content.strip()
        return text or base
    except Exception:
        return base

# =========================
# SELEZIONE MIGLIOR BLOCCO
# =========================

def find_best_block(query: str,
                    families: Optional[List[str]] = None,
                    lang: str = "it") -> Optional[Dict[str, Any]]:
    explicit_fams = detect_explicit_families(query)
    forced_fams = [f.upper() for f in families] if families else None

    # quali famiglie valutare
    if explicit_fams:
        if forced_fams:
            fams = [f for f in forced_fams if f in explicit_fams] or explicit_fams
        else:
            fams = explicit_fams
    else:
        fams = forced_fams or list_all_families()

    best_block: Optional[Dict[str, Any]] = None
    best_family: Optional[str] = None
    best_score: float = 0.0

    for fam in fams:
        try:
            blocks = load_family(fam)
        except HTTPException:
            continue

        for b in blocks:
            ans = extract_answer(b, lang)
            if not ans:
                continue

            s = score_block_routed(query, b, fam, explicit_fams)
            if s > best_score:
                best_score = s
                best_block = b
                best_family = fam

    # Soglia dinamica:
    # - se l'utente ha indicato una famiglia (P560, CTF, ecc.) → bastano match bassi pur di non dire NO GOLD
    # - altrimenti usiamo soglia più alta per evitare abbinamenti sbagliati
    if explicit_fams:
        min_score = 0.05
    else:
        min_score = 0.25

    if not best_block or best_score < min_score:
        return None

    bb = dict(best_block)
    bb["_family"] = best_family
    bb["_score"] = best_score
    return bb

# =========================
# ENDPOINTS
# =========================

@app.get("/api/config")
def api_config():
    return {
        "app": "Tecnaria Sinapsi — Q/A",
        "status": "OK",
        "families_dir": str(DATA_DIR),
        "families": list_all_families(),
        "nlm": bool(openai_client is not None and USE_OPENAI),
    }

@app.post("/api/ask")
async def api_ask(request: Request):
    raw = await request.body()
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Body JSON non valido. Atteso: {\"q\":..., \"family\":...}",
        )

    q = str(data.get("q", "")).strip()
    family = str(data.get("family", "")).strip().upper() if data.get("family") else None

    if not q:
        raise HTTPException(status_code=400, detail="Campo 'q' mancante o vuoto.")

    lang = detect_lang(q)
    fams = [family] if family else None

    best = find_best_block(q, fams, lang)

    if not best:
        return {
            "ok": False,
            "q": q,
            "lang": lang,
            "family": family,
            "text": "Nessuna risposta trovata per questa domanda con i dati disponibili.",
        }

    base = (
        extract_answer(best, lang)
        or extract_answer(best, "it")
        or extract_answer(best, "en")
    )

    if not base:
        return {
            "ok": False,
            "q": q,
            "family": best.get("_family", family),
            "id": best.get("id"),
            "text": "Blocco trovato ma privo di contenuto utilizzabile.",
        }

    text = generate_gold_answer(
        q,
        base,
        best,
        best.get("_family", family) or "",
        lang,
    )

    return {
        "ok": True,
        "q": q,
        "lang": lang,
        "family": best.get("_family", family),
        "id": best.get("id"),
        "score": best.get("_score", 0.0),
        "text": text,
    }

@app.get("/api/ask")
def api_ask_get(q: str = Query(..., description="Domanda"),
                family: Optional[str] = Query(None)):
    """
    GET per test veloce:
    /api/ask?q=...&family=CTF
    """
    lang = detect_lang(q)
    fams = [family.upper()] if family else None

    best = find_best_block(q, fams, lang)

    if not best:
        return {
            "ok": False,
            "q": q,
            "lang": lang,
            "family": family,
            "text": "Nessuna risposta trovata per questa domanda con i dati disponibili.",
        }

    base = (
        extract_answer(best, lang)
        or extract_answer(best, "it")
        or extract_answer(best, "en")
    )

    if not base:
        return {
            "ok": False,
            "q": q,
            "family": best.get("_family", family),
            "id": best.get("id"),
            "text": "Blocco trovato ma privo di contenuto utilizzabile.",
        }

    text = generate_gold_answer(
        q,
        base,
        best,
        best.get("_family", family) or "",
        lang,
    )

    return {
        "ok": True,
        "q": q,
        "lang": lang,
        "family": best.get("_family", family),
        "id": best.get("id"),
        "score": best.get("_score", 0.0),
        "text": text,
    }

@app.get("/", response_class=HTMLResponse)
def root():
    if INDEX_HTML.exists():
        return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Tecnaria Sinapsi — Q/A</h1>", status_code=200)
