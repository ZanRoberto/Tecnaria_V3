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
    "ctl maxi": "CTL MAXI",
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
            if key in data and isinstance(data[key], list):
                blocks = [b for b in data[key] if isinstance(b, dict)]
                break

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

    path = next((p for p in candidates if p.exists()), None)
    if not path:
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

    # Liste
    for key in ("questions", "paraphrases", "variants", "triggers"):
        v = block.get(key)
        if isinstance(v, list):
            for e in v:
                if isinstance(e, str):
                    e = e.strip()
                    if e:
                        out.append(e)

    # Tags
    tags = block.get("tags")
    if isinstance(tags, list):
        for t in tags:
            if isinstance(t, str):
                t = t.strip()
                if t:
                    out.append(t)

    # Canonical come contesto (pezzo)
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
    q = query.lower()
    hits: List[str] = []

    if "ctl maxi" in q:
        hits.append("CTL MAXI")

    for key, fam in FAMILY_KEYWORDS.items():
        if key == "ctl maxi":
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
        if fam_u in ["CTL", "CTL MAXI"]:
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
# COSTRUZIONE RISPOSTA GOLD
# =========================

def extract_answer(block: Dict[str, Any], lang: str = "it") -> Optional[str]:
    pieces: List[str] = []
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

    answer_it = block.get("answer_it")
    if isinstance(answer_it, str) and answer_it.strip():
        if all(answer_it.strip() not in p for p in pieces):
            pieces.append(answer_it.strip())

    canonical = block.get("canonical")
    if isinstance(canonical, str) and canonical.strip():
        if all(canonical.strip() not in p for p in pieces):
            pieces.append(canonical.strip())

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
    if not USE_OPENAI or openai_client is None:
        return base

    try:
        resp = openai_client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            temperature=0.35,
            max_tokens=1500,  # 🔥 aumento limite per risposte GOLD lunghe
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Sei Sinapsi, assistente tecnico-commerciale di Tecnaria. "
                        "Rispondi in modo completo, naturale e professionale, "
                        "tono GOLD dinamico, tecnico chiaro, esempi di cantiere, "
                        "nessuna frase generica vuota. "
                        "Rispetta rigorosamente il campo di impiego della famiglia "
                        "e i contenuti del blocco dati fornito."
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
