import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

# ============================================================
# CONFIG OPENAI (NLM IBRIDO)
# ============================================================

USE_OPENAI = True  # metti False se vuoi disattivare la parte generativa

try:
    from openai import OpenAI
    if USE_OPENAI and os.getenv("OPENAI_API_KEY"):
        openai_client = OpenAI()
    else:
        openai_client = None
except Exception:
    openai_client = None

# ============================================================
# PATH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = STATIC_DIR / "data"
INDEX_HTML = STATIC_DIR / "index.html"

app = FastAPI(title="Tecnaria Sinapsi — Q/A")

_family_cache: Dict[str, List[Dict[str, Any]]] = {}

# ============================================================
# LETTURA JSON FAMIGLIE
# ============================================================

def safe_read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def extract_blocks(data: Any) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []

    if isinstance(data, list):
        blocks = [b for b in data if isinstance(b, dict)]

    elif isinstance(data, dict):
        # contenitori standard
        for key in ("items", "blocks", "data"):
            if key in data and isinstance(data[key], list):
                blocks = [b for b in data[key] if isinstance(b, dict)]
                break
        else:
            # dict di blocchi
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
        name = f.name.lower()
        if "config.runtime" in name:
            continue
        fams.append(f.stem.upper())
    return sorted(set(fams))


# ============================================================
# ESTRAZIONE RISPOSTA GOLD
# ============================================================

def extract_answer(block: Dict[str, Any], lang: str = "it") -> Optional[str]:
    answers = block.get("answers")
    if isinstance(answers, dict):
        for key in (lang, lang.lower(), lang.upper()):
            v = answers.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for v in answers.values():
            if isinstance(v, str) and v.strip():
                return v.strip()

    for k, v in block.items():
        if isinstance(v, str) and v.strip() and k.lower() == f"answer_{lang}".lower():
            return v.strip()

    v = block.get("answer")
    if isinstance(v, str) and v.strip():
        return v.strip()

    v = block.get("text")
    if isinstance(v, str) and v.strip():
        return v.strip()

    for key in (lang, lang.lower(), lang.upper(), "it", "en", "IT", "EN"):
        v = block.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()

    rv = block.get("response_variants")
    if isinstance(rv, dict):
        parts = [txt for txt in rv.values() if isinstance(txt, str) and txt.strip()]
        if parts:
            return " ".join(parts).strip()

    return None


# ============================================================
# DOMANDE / TRIGGER
# ============================================================

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


def norm(s: str) -> str:
    return " ".join(s.lower().strip().split())


# ============================================================
# MATCHING DOMANDA → BLOCCO
# ============================================================

def score_block(query: str, block: Dict[str, Any]) -> float:
    q = norm(query)
    if not q:
        return 0.0

    queries = extract_queries(block)
    if not queries:
        return 0.0

    best = 0.0
    for cand in queries:
        c = norm(cand)
        if not c:
            continue

        # match forte: substring
        if c in q or q in c:
            s = min(len(q), len(c)) / max(len(q), len(c))
            best = max(best, 0.9 + 0.1 * s)
            continue

        # overlap parole (Jaccard)
        sq = set(q.split())
        sc = set(c.split())
        inter = len(sq & sc)
        if inter > 0:
            union = len(sq | sc)
            j = inter / union if union else 0.0
            if j > best:
                best = j

    full = norm(json.dumps(block, ensure_ascii=False))
    for kw in ("vcem", "ctf", "ctl", "ctl maxi", "ctcem", "diapason", "p560",
               "lamiera", "soletta", "calcestruzzo", "cemento armato"):
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

    bb = dict(best_block)
    bb["_family"] = best_family
    bb["_score"] = round(best_score, 3)
    return bb


# ============================================================
# LINGUA
# ============================================================

def detect_lang(query: str) -> str:
    q = query.lower()
    if any(w in q for w in [" il ", " lo ", " la ", " connettore", "soletta", "calcestruzzo", "cemento armato", "trave", "travetto"]):
        return "it"
    if any(w in q for w in [" can i ", " beam", "steel", "slab", "use ", "connector"]):
        return "en"
    if any(w in q for w in [" ¿", " qué ", " hormigón", "conectores"]):
        return "es"
    if any(w in q for w in [" quel ", " béton", "connecteurs"]):
        return "fr"
    if any(w in q for w in [" welche ", "verbinder", "beton", "träger"]):
        return "de"
    return "it"


# ============================================================
# GENERAZIONE GOLD (NLM)
# ============================================================

def generate_gold_answer(
    question: str,
    base_answer: str,
    block: Dict[str, Any],
    family: str,
    lang: str,
) -> str:
    if not USE_OPENAI or openai_client is None:
        return base_answer

    if lang not in ("it", "en", "fr", "de", "es"):
        lang = "en"

    context = json.dumps(block, ensure_ascii=False)

    system_msg = (
        "Sei SINAPSI, assistente tecnico-commerciale ufficiale Tecnaria. "
        "Rispondi solo con le informazioni presenti nel CONTENUTO fornito. "
        "Nessuna invenzione. Stile chiaro, completo, professionale, umano. "
        "Scrivi sempre 'cemento armato', non 'c.a.'. "
        "Usa 'chiodi idonei Tecnaria' per la P560, mai 'perni'. "
    )

    user_msg = (
        f"LINGUA: {lang}\n"
        f"FAMIGLIA: {family}\n"
        f"DOMANDA: {question}\n\n"
        f"CONTENUTO GOLD (blocchi rilevanti):\n{context}\n\n"
        f"RISPOSTA BASE:\n{base_answer}\n\n"
        "Riscrivi una risposta unica, fluida, coerente con Tecnaria, senza citare JSON o sistemi interni."
    )

    try:
        resp = openai_client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.35,
            max_tokens=420,
        )
        txt = (resp.choices[0].message.content or "").strip()
        return txt or base_answer
    except Exception:
        return base_answer


# ============================================================
# API: CONFIG
# ============================================================

@app.get("/api/config")
def api_config():
    return {
        "app": "Tecnaria Sinapsi — Q/A",
        "status": "OK",
        "families_dir": str(DATA_DIR),
        "nlm": bool(openai_client is not None and USE_OPENAI),
        "families": list_all_families(),
    }


# ============================================================
# API: ASK (PARSING MANUALE ROBUSTO)
# ============================================================

@app.post("/api/ask")
async def api_ask(request: Request):
    # 👇 NIENTE request.json(): leggo il body grezzo e faccio json.loads io
    raw = await request.body()
    try:
        text = raw.decode("utf-8").strip()
        if not text:
            raise ValueError("Body vuoto")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("Body JSON non è un oggetto.")
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Body JSON non valido. Atteso: {\"q\": \"domanda\", \"family\": \"FACOLTATIVO\"}."
        )

    q = str(payload.get("q", "")).strip()
    if not q:
        raise HTTPException(status_code=400, detail="Campo 'q' (domanda) mancante o vuoto.")

    family_req = payload.get("family")
    fams = None
    if isinstance(family_req, str) and family_req.strip():
        fams = [family_req.strip().upper()]

    lang = detect_lang(q)

    best = find_best_block(q, families=fams, lang=lang)

    if not best:
        return {
            "ok": False,
            "q": q,
            "lang": lang,
            "family": family_req,
            "text": "Per questa domanda non è ancora presente una risposta GOLD nei contenuti Tecnaria."
        }

    family = best.get("_family") or (family_req.strip().upper() if isinstance(family_req, str) else None)

    base = (
        extract_answer(best, lang=lang)
        or extract_answer(best, lang="it")
        or extract_answer(best, lang="en")
    )

    if not base:
        return {
            "ok": False,
            "q": q,
            "lang": lang,
            "family": family,
            "id": best.get("id"),
            "text": "Blocco GOLD individuato ma senza testo risposta valido. Controllare il file JSON."
        }

    final_text = generate_gold_answer(q, base, best, family or "", lang)

    return {
        "ok": True,
        "q": q,
        "lang": lang,
        "family": family,
        "id": best.get("id"),
        "mode": "dynamic_nlm" if (USE_OPENAI and openai_client is not None) else "dynamic",
        "score": best.get("_score", 0.0),
        "text": final_text,
    }


# ============================================================
# ROOT: INTERFACCIA
# ============================================================

@app.get("/", response_class=HTMLResponse)
def root():
    if INDEX_HTML.exists():
        return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Tecnaria Sinapsi — Q/A</h1>", status_code=200)
