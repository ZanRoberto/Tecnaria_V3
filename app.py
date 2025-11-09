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
    """
    Heuristica robusta ma semplice:
    - Italiano riconosciuto da lessico tecnico tipico.
    - Inglese se ASCII + parole chiave english.
    - FR / ES / DE con marker di lingua.
    - Fallback: se ASCII → en, altrimenti it.
    """
    q = query.strip()
    q_low = q.lower()

    # italiano: termini tecnici tipici
    it_markers = [
        " soletta", "connettore", "connettori", "trave", "travetto",
        "calcestruzzo", "laterocemento", "lamiera", "pistola", "cartucce",
        "chiodatrice", "posa", "cemento armato"
    ]
    if any(m in q_low for m in it_markers):
        return "it"

    # inglese: solo caratteri ASCII + marker tipici
    en_markers = [
        " beam", " beams", "steel", "timber", "composite", "deck",
        "slab", "connector", "connectors", "use", "which", "what",
        "how many", "can i", "design", "load", "capacity"
    ]
    if all(ord(c) < 128 for c in q) and any(m in q_low for m in en_markers):
        return "en"

    # francese
    fr_markers = ["béton", "connecteur", "plancher", "poutre", "acier"]
    if any(m in q_low for m in fr_markers):
        return "fr"

    # spagnolo
    es_markers = ["hormigón", "forjado", "viga de madera", "conectores", "losa"]
    if any(m in q_low for m in es_markers):
        return "es"

    # tedesco
    de_markers = ["verbinder", "beton", "holz", "decken", "stahlträger"]
    if any(m in q_low for m in de_markers):
        return "de"

    # fallback: se tutto ASCII → probabile EN, altrimenti IT
    if all(ord(c) < 128 for c in q):
        return "en"
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
    Restituisce SEMPRE la versione più ricca disponibile.
    Logica:
    - raccoglie tutte le sorgenti (answers, answer_it, canonical, response_variants, fallback)
    - privilegia i testi lunghi (>=160 char) se esistono
    - sceglie il testo più lungo e strutturato
    - non si limita alla canonical se esistono varianti GOLD sensate
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

    # 4) canonical
    canonical = block.get("canonical")
    if isinstance(canonical, str) and canonical.strip():
        candidates.append(canonical.strip())

    # 5) aggiungi le varianti tra i candidati
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

    # privilegia GOLD: testi con una certa lunghezza
    rich = [t for t in candidates if len(t) >= 160]
    source = rich if rich else candidates

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
    - NON cambia vincoli tecnici (P560, campi d'impiego, ecc.).
    Se non attivo: restituisce il base.
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
                        "Rispondi in tono GOLD: completo, chiaro, tecnico, concreto. "
                        "Non accorciare in modo eccessivo il testo fornito. "
                        "Rispetta rigorosamente il blocco dati: campi di impiego, vincoli (es. solo P560), "
                        "nessuna invenzione, nessuna apertura dove il blocco è chiuso."
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

    # regola di stile globale
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
