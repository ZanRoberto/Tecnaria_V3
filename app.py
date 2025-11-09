import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

# === CONFIGURAZIONE BASE ===
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

# === CACHE INTERNA ===
_family_cache: Dict[str, List[Dict[str, Any]]] = {}

# === FUNZIONI DI SUPPORTO ===
def safe_read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def extract_blocks(data: Any) -> List[Dict[str, Any]]:
    """Estrae i blocchi validi da un file JSON."""
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
        name = f.name.lower()
        if "config.runtime" in name:
            continue
        fams.append(f.stem.upper())
    return sorted(set(fams))

# === MATCHING DOMANDE / RISPOSTE ===
def norm(s: str) -> str:
    return " ".join(s.lower().strip().split())

def extract_queries(block: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for key in ("q", "question", "domanda", "title", "label"):
        v = block.get(key)
        if isinstance(v, str):
            out.append(v.strip())
    for key in ("questions", "paraphrases", "variants", "triggers"):
        v = block.get(key)
        if isinstance(v, list):
            for e in v:
                if isinstance(e, str):
                    out.append(e.strip())
    return out

def extract_answer(block: Dict[str, Any], lang: str = "it") -> Optional[str]:
    if "answers" in block and isinstance(block["answers"], dict):
        for key in (lang, lang.lower(), lang.upper()):
            if key in block["answers"]:
                return block["answers"][key]
        for v in block["answers"].values():
            if isinstance(v, str):
                return v
    for key in ("answer", "risposta", "text", "content"):
        v = block.get(key)
        if isinstance(v, str):
            return v.strip()
    return None

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
        if q in c or c in q:
            best = 1.0
            break
        sq, sc = set(q.split()), set(c.split())
        j = len(sq & sc) / len(sq | sc) if sc else 0.0
        if j > best:
            best = j
    return best

def detect_lang(query: str) -> str:
    q = query.lower()
    if any(x in q for x in [" connettore", "soletta", "cemento armato", "calcestruzzo"]):
        return "it"
    if any(x in q for x in ["beam", "steel", "can i", "use", "connector"]):
        return "en"
    if any(x in q for x in ["quel", "béton", "connecteur"]):
        return "fr"
    if any(x in q for x in ["conectores", "hormigón"]):
        return "es"
    if any(x in q for x in ["verbinder", "beton"]):
        return "de"
    return "it"

def find_best_block(query: str, families: Optional[List[str]] = None, lang: str = "it") -> Optional[Dict[str, Any]]:
    if families is None:
        families = list_all_families()
    best, fam_name, best_score = None, None, 0.0
    for fam in families:
        try:
            blocks = load_family(fam)
        except HTTPException:
            continue
        for b in blocks:
            ans = extract_answer(b, lang)
            if not ans:
                continue
            s = score_block(query, b)
            if s > best_score:
                best, fam_name, best_score = b, fam, s
    if not best:
        return None
    bb = dict(best)
    bb["_family"] = fam_name
    bb["_score"] = best_score
    return bb

# === NARRAZIONE (OpenAI opzionale) ===
def generate_gold_answer(question: str, base: str, block: Dict[str, Any], family: str, lang: str) -> str:
    if not USE_OPENAI or openai_client is None:
        return base
    try:
        resp = openai_client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            messages=[
                {"role": "system", "content": (
                    "Sei Sinapsi, assistente tecnico-commerciale di Tecnaria. "
                    "Rispondi in modo completo, naturale, professionale, "
                    "usando il tono tecnico-narrativo GOLD. "
                    "Non usare abbreviazioni: scrivi sempre 'cemento armato', "
                    "e usa sempre 'chiodi idonei Tecnaria', mai 'perni'."
                )},
                {"role": "user", "content": (
                    f"LINGUA: {lang}\nFAMIGLIA: {family}\n"
                    f"DOMANDA: {question}\n\nCONTESTO: {json.dumps(block, ensure_ascii=False)}\n\n"
                    f"RISPOSTA BASE: {base}"
                )}
            ],
            temperature=0.35,
            max_tokens=400,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return base

# === API ENDPOINTS ===
@app.get("/api/config")
def api_config():
    return {
        "app": "Tecnaria Sinapsi — Q/A",
        "status": "OK",
        "families_dir": str(DATA_DIR),
        "families": list_all_families(),
        "nlm": bool(openai_client is not None and USE_OPENAI)
    }

@app.post("/api/ask")
async def api_ask(request: Request):
    raw = await request.body()
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Body JSON non valido. Atteso: {\"q\":..., \"family\":...}")

    q = str(data.get("q", "")).strip()
    family = str(data.get("family", "")).strip().upper() if data.get("family") else None
    if not q:
        raise HTTPException(status_code=400, detail="Campo 'q' mancante o vuoto.")

    lang = detect_lang(q)
    fams = [family] if family else None
    best = find_best_block(q, fams, lang)

    if not best:
        return {"ok": False, "q": q, "lang": lang, "family": family, "text": "Nessuna risposta trovata."}

    base = extract_answer(best, lang) or extract_answer(best, "it") or extract_answer(best, "en")
    if not base:
        return {"ok": False, "q": q, "family": family, "id": best.get("id"), "text": "Blocco trovato ma senza risposta valida."}

    text = generate_gold_answer(q, base, best, family or "", lang)
    return {"ok": True, "family": best["_family"], "id": best.get("id"), "text": text, "lang": lang}

@app.get("/", response_class=HTMLResponse)
def root():
    if INDEX_HTML.exists():
        return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Tecnaria Sinapsi — Q/A</h1>", status_code=200)
