from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import HTMLResponse

# =========================
# CONFIGURAZIONE BASE
# =========================

# Se c'è OPENAI_API_KEY usa il modello per rifinire lo stile (senza cambiare i contenuti).
# Se non c'è, usa solo i testi GOLD dai JSON.
USE_OPENAI = True

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

_family_cache: Dict[str, List[Dict[str, Any]]] = {}

FAMILY_KEYWORDS = {
    "ctf": "CTF",
    "ctl maxi": "CTL_MAXI",
    "ctl_maxi": "CTL_MAXI",
    "ctl": "CTL",
    "vcem": "VCEM",
    "ctcem": "CTCEM",
    "p560": "P560",
    "diapason": "DIAPASON",
    "comm": "COMM",
}

# =========================
# LETTURA JSON / FAMIGLIE
# =========================

def safe_read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def extract_blocks(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        blocks = [b for b in data if isinstance(b, dict)]
    elif isinstance(data, dict):
        blocks = []
        for key in ("items", "blocks", "data"):
            v = data.get(key)
            if isinstance(v, list):
                blocks = [b for b in v if isinstance(b, dict)]
                break
    else:
        blocks = []

    for i, b in enumerate(blocks):
        if "id" not in b:
            b["id"] = f"AUTO-{i:04d}"
    return blocks

def load_family(family: str) -> List[Dict[str, Any]]:
    fam = family.upper()
    if fam in _family_cache:
        return _family_cache[fam]

    candidates = [
        DATA_DIR / f"{fam}.json",
        DATA_DIR / f"{fam}.gold.json",
        DATA_DIR / f"{fam}.golden.json",
    ]

    path: Optional[Path] = next((p for p in candidates if p.exists()), None)

    if path is None and DATA_DIR.exists():
        # fallback per file tipo P560 (5).json etc.
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
# MATCHING / HEURISTICHE
# =========================

def norm(s: str) -> str:
    return " ".join(s.lower().strip().split())

def extract_queries(block: Dict[str, Any]) -> List[str]:
    out: List[str] = []

    for key in ("q", "question", "domanda", "title", "label"):
        v = block.get(key)
        if isinstance(v, str):
            v = v.strip()
            if v:
                out.append(v)

    for key in ("questions", "paraphrases", "variants", "triggers"):
        v = block.get(key)
        if isinstance(v, list):
            for e in v:
                if isinstance(e, str):
                    e = e.strip()
                    if e:
                        out.append(e)

    tags = block.get("tags")
    if isinstance(tags, list):
        for t in tags:
            if isinstance(t, str):
                t = t.strip()
                if t:
                    out.append(t)

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

        if q == c:
            return 1.0

        if q in c or c in q:
            best = max(best, 0.9)
            continue

        sc = set(c.split())
        if not sc:
            continue

        inter = len(sq & sc)
        if inter == 0:
            continue

        j = inter / float(len(sq | sc))
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
    base = base_similarity(query, block)
    if base <= 0:
        return 0.0

    fam_u = fam.upper()
    q_low = query.lower()

    if explicit_fams:
        if fam_u in explicit_fams:
            base *= 8.0
        else:
            base *= 0.05
        return base

    if any(k in q_low for k in ["p560", "pistola", "chiodatrice", "sparo", "cartuccia", "cartucce"]):
        if fam_u == "P560":
            base *= 5.0
        else:
            base *= 0.4

    if "legno" in q_low or "trave in legno" in q_low:
        if fam_u in ["CTL", "CTL_MAXI"]:
            base *= 3.0
        elif fam_u in ["CTF", "VCEM", "CTCEM", "P560", "DIAPASON"]:
            base *= 0.4

    if any(k in q_low for k in ["laterocemento", "travetto", "travetti"]):
        if fam_u in ["VCEM", "CTCEM", "DIAPASON"]:
            base *= 3.0
        elif fam_u in ["CTF", "CTL", "CTL_MAXI", "P560"]:
            base *= 0.4

    return base

# =========================
# GOLD: NIENTE PIÙ CANONICA SECCA
# =========================

def extract_answer(block: Dict[str, Any], lang: str = "it") -> Optional[str]:
    """
    Costruisce SEMPRE la risposta più ricca possibile.
    Regola:
    - raccogliamo tutte le fonti (answers, answer_it, canonical, response_variants, fallback)
    - scegliamo il testo PIÙ LUNGO e strutturato.
    - se esistono varianti GOLD, NON torniamo una canonica secca.
    """
    candidates: List[str] = []

    # 1) answers multilingua
    answers = block.get("answers")
    if isinstance(answers, dict):
        # priorità alla lingua richiesta
        for key in (lang, lang.lower(), lang.upper()):
            v = answers.get(key)
            if isinstance(v, str) and v.strip():
                candidates.append(v.strip())
                break
        # se ancora vuoto, prima disponibile
        if not candidates:
            for v in answers.values():
                if isinstance(v, str) and v.strip():
                    candidates.append(v.strip())
                    break

    # 2) answer_it
    answer_it = block.get("answer_it")
    if isinstance(answer_it, str) and answer_it.strip():
        candidates.append(answer_it.strip())

    # 3) response_variants (lista o dict)
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

    # 4) canonical (aggiunta solo se non rimaniamo secchi)
    canonical = block.get("canonical")
    if isinstance(canonical, str) and canonical.strip():
        candidates.append(canonical.strip())

    # 5) aggiungi varianti dopo canonical così hanno spazio di essere le più lunghe
    candidates.extend(variants)

    # 6) fallback legacy
    if not candidates:
        for key in ("answer", "risposta", "text", "content"):
            v = block.get(key)
            if isinstance(v, str) and v.strip():
                candidates.append(v.strip())
                break

    if not candidates:
        return None

    # preferisci testi già "ricchi"
    rich = [t for t in candidates if len(t) >= 160]
    source = rich if rich else candidates

    # scegli il più lungo = GOLD
    best = max(source, key=len).strip()
    return best or None

def generate_gold_answer(question: str,
                         base: str,
                         block: Dict[str, Any],
                         family: str,
                         lang: str) -> str:
    """
    Se OpenAI è attivo:
    - rifinisce in tono GOLD,
    - NON deve accorciare in modo sostanziale,
    - non cambia i vincoli tecnici (es. P560 unica, campi d'impiego).
    Se non attivo: restituisce la base.
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
                        "Usa tono GOLD: completo, chiaro, tecnico, narrativo. "
                        "NON accorciare in modo eccessivo il contenuto fornito. "
                        "Rispetta rigorosamente il blocco dati: campi di impiego, vincoli (es. solo P560), "
                        "nessuna invenzione o mitigazione delle esclusioni."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"LINGUA: {lang}\n"
                        f"FAMIGLIA: {family}\n"
                        f"DOMANDA: {question}\n\n"
                        f"BLOCCO DATI (JSON): {json.dumps(block, ensure_ascii=False)}\n\n"
                        f"TESTO BASE GOLD (da rifinire SENZA accorciare o cambiare regole):\n{base}"
                    ),
                },
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        # se per qualche motivo il modello restituisce meno del base, tieni il base
        if len(text) < len(base) * 0.8:
            return base
        return text
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

    # soglia: più permissivi se l'utente cita una famiglia esplicita
    min_score = 0.05 if explicit_fams else 0.25

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
async def api_ask_post(request: Request):
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

    # regola di stile: mai 'perni'
    text = re.sub(r"\bperni?\b", "chiodi idonei Tecnaria", text, flags=re.IGNORECASE)

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
def api_ask_get(
    q: str = Query(..., description="Domanda"),
    family: Optional[str] = Query(None),
):
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

    text = re.sub(r"\bperni?\b", "chiodi idonei Tecnaria", text, flags=re.IGNORECASE)

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
