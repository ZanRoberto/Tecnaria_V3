import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# OpenAI client (usa OPENAI_API_KEY dall'ambiente)
USE_OPENAI = True  # se vuoi spegnere NLM metti False

try:
    from openai import OpenAI
    _openai_client = OpenAI() if USE_OPENAI and os.getenv("OPENAI_API_KEY") else None
except Exception:
    _openai_client = None

# -------------------------------------------------
# PATH
# -------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = STATIC_DIR / "data"
INDEX_HTML = STATIC_DIR / "index.html"

app = FastAPI(title="Tecnaria Sinapsi — Q/A")

# -------------------------------------------------
# MODELLO REQUEST
# -------------------------------------------------
class AskPayload(BaseModel):
    q: str
    family: Optional[str] = None  # opzionale; se presente, forza famiglia


# -------------------------------------------------
# CACHE FAMIGLIE
# -------------------------------------------------
_family_cache: Dict[str, List[Dict[str, Any]]] = {}


def _safe_read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _extract_blocks(data: Any) -> List[Dict[str, Any]]:
    """
    Supporta:
    - lista diretta di blocchi
    - { "items": [...] }
    - { "blocks": [...] }
    - { "data": [...] }
    - { "ID1": {...}, "ID2": {...} }
    """
    blocks: List[Dict[str, Any]] = []

    if isinstance(data, list):
        blocks = [b for b in data if isinstance(b, dict)]

    elif isinstance(data, dict):
        for key in ("items", "blocks", "data"):
            if key in data and isinstance(data[key], list):
                blocks = [b for b in data[key] if isinstance(b, dict)]
                break
        else:
            vals = list(data.values())
            if vals and all(isinstance(v, dict) for v in vals):
                blocks = vals

    for i, b in enumerate(blocks):
        if "id" not in b:
            b["id"] = b.get("ID", f"BLK-{i:04d}")

    return blocks


def load_family(family: str) -> List[Dict[str, Any]]:
    fam = family.upper()
    if fam in _family_cache:
        return _family_cache[fam]

    candidates = [
        DATA_DIR / f"{fam}.json",
        DATA_DIR / f"{fam}.golden.json",
        DATA_DIR / f"{fam}.gold.json",
    ]

    path = next((p for p in candidates if p.exists()), None)
    if not path:
        raise HTTPException(status_code=404, detail=f"File JSON per famiglia '{family}' non trovato.")

    data = _safe_read_json(path)
    blocks = _extract_blocks(data)
    _family_cache[fam] = blocks
    return blocks


def list_all_families() -> List[str]:
    fams: List[str] = []
    if not DATA_DIR.exists():
        return fams
    for f in DATA_DIR.glob("*.json"):
        if f.name.lower() == "config.runtime.json":
            continue
        fams.append(f.stem.upper())
    return fams


# -------------------------------------------------
# ESTRAZIONE RISPOSTA DAL BLOCCO
# -------------------------------------------------
def extract_answer(block: Dict[str, Any], lang: str = "it") -> Optional[str]:
    """
    Cerca una risposta GOLD robusta.
    Ordine:
    - answers[lang]
    - answer_{lang}
    - answer
    - text
    - campo lingua diretto (it/en)
    - response_variants (concat)
    """
    # 1) answers.{lang}
    answers = block.get("answers")
    if isinstance(answers, dict):
        for key in (lang, lang.lower(), lang.upper()):
            v = answers.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for v in answers.values():
            if isinstance(v, str) and v.strip():
                return v.strip()

    # 2) answer_{lang}
    for k, v in block.items():
        if isinstance(v, str) and v.strip() and k.lower() == f"answer_{lang}".lower():
            return v.strip()

    # 3) answer singolo
    v = block.get("answer")
    if isinstance(v, str) and v.strip():
        return v.strip()

    # 4) text generico
    v = block.get("text")
    if isinstance(v, str) and v.strip():
        return v.strip()

    # 5) campo lingua diretto
    for key in (lang, lang.lower(), lang.upper(), "it", "en", "IT", "EN"):
        v = block.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()

    # 6) response_variants: unione tecnica/narrativa/cantiere
    rv = block.get("response_variants")
    if isinstance(rv, dict):
        parts = [txt for txt in rv.values() if isinstance(txt, str) and txt.strip()]
        if parts:
            return " ".join(parts).strip()

    return None


# -------------------------------------------------
# ESTRAZIONE QUERY / PATTERN
# -------------------------------------------------
def extract_queries(block: Dict[str, Any]) -> List[str]:
    out: List[str] = []

    scalar_keys = ["q", "question", "domanda", "title", "label"]
    list_keys = ["questions", "q_list", "patterns", "triggers", "variants", "synonyms", "paraphrases"]

    for k in scalar_keys:
        v = block.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())

    for k in list_keys:
        v = block.get(k)
        if isinstance(v, list):
            for e in v:
                if isinstance(e, str) and e.strip():
                    out.append(e.strip())

    return out


def _norm(s: str) -> str:
    return " ".join(s.lower().strip().split())


# -------------------------------------------------
# MATCHING DOMANDA -> BLOCCO
# -------------------------------------------------
def score_block(query: str, block: Dict[str, Any]) -> float:
    q = _norm(query)
    if not q:
        return 0.0

    queries = extract_queries(block)
    if not queries:
        return 0.0

    best = 0.0

    for cand in queries:
        c = _norm(cand)
        if not c:
            continue

        # Match forte: substring
        if c in q or q in c:
            s = min(len(q), len(c)) / max(len(q), len(c))
            best = max(best, 0.9 + 0.1 * s)
            continue

        # Overlap parole
        sq = set(q.split())
        sc = set(c.split())
        inter = len(sq & sc)
        if inter > 0:
            union = len(sq | sc)
            j = inter / union if union else 0.0
            if j > best:
                best = j

    # Boost keyword tecniche
    full = _norm(json.dumps(block, ensure_ascii=False))
    for kw in ("vcem", "p560", "ctf", "ctl", "ctl maxi", "ctcem", "diapason", "lamiera", "calcestruzzo", "soletta"):
        if kw in q and kw in full:
            best += 0.15

    return best


def find_best_block(query: str, families: Optional[List[str]] = None, lang: str = "it") -> Optional[Dict[str, Any]]:
    if families is None:
        families = list_all_families()

    best_block = None
    best_family = None
    best_score = 0.0

    for fam in families:
        try:
            blocks = load_family(fam)
        except HTTPException:
            continue

        for b in blocks:
            ans = extract_answer(b, lang=lang)
            if not ans:
                continue

            s = score_block(query, b)
            if s > best_score:
                best_score = s
                best_block = b
                best_family = fam

    if not best_block or best_score <= 0.0:
        return None

    best_block = dict(best_block)
    best_block["_family"] = best_family
    best_block["_score"] = round(best_score, 3)
    return best_block


# -------------------------------------------------
# LINGUA (semplice)
# -------------------------------------------------
def detect_lang(query: str) -> str:
    q = query.lower()
    if any(w in q for w in [" il ", " lo ", " la ", " connettore", "soletta", "calcestruzzo", "trave", "travi"]):
        return "it"
    if any(w in q for w in [" the ", " can i ", " beam", "steel", "slab"]):
        return "en"
    if any(w in q for w in [" ¿", " qué ", " conectores", "hormigón"]):
        return "es"
    if any(w in q for w in [" quel ", " béton", "connecteurs"]):
        return "fr"
    if any(w in q for w in [" welche ", "verbinder", "beton"]):
        return "de"
    return "it"


# -------------------------------------------------
# GENERAZIONE NLM (ibrida)
# -------------------------------------------------
def generate_gold_answer(
    question: str,
    base_answer: str,
    block: Dict[str, Any],
    family: str,
    lang: str
) -> str:
    """
    Usa OpenAI per riscrivere la risposta in modo tecnico-narrativo,
    restando fedele al contenuto del blocco.
    Se qualcosa va storto, torna base_answer.
    """
    if not USE_OPENAI or _openai_client is None:
        return base_answer

    # Context: tutto quello che sappiamo dal blocco
    context = json.dumps(block, ensure_ascii=False)

    if lang not in ("it", "en", "fr", "de", "es"):
        lang = "en"

    system_msg = (
        "Sei SINAPSI, assistente tecnico-commerciale ufficiale Tecnaria. "
        "Rispondi SOLO usando le informazioni fornite nel CONTENUTO GOLD, "
        "senza inventare dati nuovi. "
        "Stile: chiaro, completo, professionale, con tono umano, "
        "coerente con manuali Tecnaria e assistenza tecnica. "
        "Non usare sigle non spiegate (scrivi 'cemento armato', non 'c.a.'). "
        "Se il contenuto non copre la domanda, dillo esplicitamente."
    )

    # Prompt utente per il modello
    user_msg = (
        f"LINGUA RISPOSTA: {lang}\n"
        f"FAMIGLIA: {family}\n"
        f"DOMANDA UTENTE: {question}\n\n"
        f"CONTENUTO GOLD (da rispettare, base_answer inclusa):\n{context}\n\n"
        f"BASE_ANSWER (risposta sintetica):\n{base_answer}\n\n"
        "Scrivi una risposta unica, completa e fluida, usando SOLO queste informazioni. "
        "Includi indicazioni pratiche e, se utili, note di sicurezza o riferimento a casi tipici. "
        "Non menzionare il processo interno o il fatto che esistono 'blocchi' o 'JSON'."
    )

    try:
        resp = _openai_client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.35,
            max_tokens=420,
        )
        txt = (resp.choices[0].message.content or "").strip()
        if not txt:
            return base_answer
        return txt
    except Exception:
        return base_answer


# -------------------------------------------------
# API: CONFIG
# -------------------------------------------------
@app.get("/api/config")
def api_config():
    return {
        "ok": True,
        "app": "Tecnaria Sinapsi — Q/A",
        "families_dir": str(DATA_DIR),
        "nlm": bool(_openai_client is not None and USE_OPENAI),
    }


# -------------------------------------------------
# API: ASK
# -------------------------------------------------
@app.post("/api/ask")
def api_ask(payload: AskPayload):
    q = (payload.q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Domanda vuota.")

    lang = detect_lang(q)

    families = None
    fam = None
    if payload.family:
        fam = payload.family.upper()
        families = [fam]

    best = find_best_block(q, families=families, lang=lang)

    if not best:
        return {
            "ok": False,
            "q": q,
            "lang": lang,
            "family": fam,
            "text": "Per questa domanda non è ancora presente una risposta GOLD nei contenuti Tecnaria."
        }

    base = extract_answer(best, lang=lang) or extract_answer(best, lang="it") or extract_answer(best, lang="en")
    if not base:
        return {
            "ok": False,
            "q": q,
            "lang": lang,
            "family": best.get("_family"),
            "id": best.get("id"),
            "text": "Blocco GOLD individuato ma senza testo risposta valido. Controllare il file JSON."
        }

    family = best.get("_family", fam or "")
    score = best.get("_score", 0.0)

    # Generazione NLM ibrida
    final_text = generate_gold_answer(q, base, best, family, lang)

    return {
        "ok": True,
        "q": q,
        "lang": lang,
        "family": family,
        "id": best.get("id"),
        "mode": "dynamic_nlm" if USE_OPENAI and _openai_client else "dynamic",
        "score": score,
        "text": final_text,
    }


# -------------------------------------------------
# UI ROOT
# -------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def root():
    if INDEX_HTML.exists():
        return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Tecnaria Sinapsi — Q/A</h1>", status_code=200)
