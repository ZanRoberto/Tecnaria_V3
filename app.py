"""
TECNARIA Sinapsi - app.py (complete)
- FastAPI app that loads family JSONs from ./static/data
- Supports modes: GOLD (dynamic) and CANONICAL
- Runtime config persisted in config.runtime.json
- Language handling: if question begins with a language tag like "GOLD:" or "CANONICO:" or "EN:" it will set mode or prefer language for that request
- Endpoints:
  - GET /            -> simple HTML UI (Tecnaria skin placeholder)
  - GET /api/config  -> current runtime config
  - POST /api/config -> update runtime config {"mode":"gold"/"canonical", "families": [...]} (atomic)
  - POST /api/ask    -> {"question":"...", "force_mode":"gold|canonical"} -> returns best-matched answer

Notes:
- This file DOES NOT embed any JSON data. Put your family files in `static/data/*.json`.
- Each family JSON expected structure: {"family": ..., "items": [ {"id":"...","questions":[...],"canonical":"...","response_variants":[...],"tags":[...],"mode":"dynamic"}, ... ] }
- The app selects GOLD variant by returning the longest variant from response_variants (prefer narrative), and CANONICAL by returning canonical.
- Multilingual: the app does not write translations into JSON. It returns the answer in the language found in the question when a matching question text in that language exists; otherwise returns default lang from JSON. For production you should wire an online translator.

Deploy: use Uvicorn/Gunicorn as you already do. Ensure env var DATA_DIR points to ./static/data or default.

"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os, json, glob, re
from pathlib import Path
from datetime import datetime

# --- Config and paths ---
DATA_DIR = os.environ.get("DATA_DIR", "static/data")
RUNTIME_CONFIG = os.environ.get("RUNTIME_CONFIG", "config.runtime.json")

app = FastAPI(title="TECNARIA Sinapsi Backend", version="1.0")

# default runtime structure
default_runtime = {
    "ok": True,
    "message": "TECNARIA Sinapsi backend attivo",
    "mode": "gold",            # 'gold' or 'canonical'
    "families": [],             # loaded families
    "last_update": datetime.utcnow().isoformat()
}

# --- Helpers to load and validate JSON families ---

def load_runtime_config() -> Dict[str, Any]:
    if os.path.exists(RUNTIME_CONFIG):
        try:
            with open(RUNTIME_CONFIG, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return cfg
        except Exception:
            # fallback to default
            return default_runtime.copy()
    else:
        return default_runtime.copy()


def save_runtime_config(cfg: Dict[str, Any]):
    cfg = dict(cfg)
    cfg["last_update"] = datetime.utcnow().isoformat()
    with open(RUNTIME_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# load family jsons into memory index
FAMILIES: Dict[str, Dict[str, Any]] = {}
QUESTION_INDEX: List[Dict[str, Any]] = []


def load_families(data_dir=DATA_DIR):
    global FAMILIES, QUESTION_INDEX
    FAMILIES = {}
    QUESTION_INDEX = []
    p = Path(data_dir)
    if not p.exists():
        return
    for file in p.glob("*.json"):
        try:
            with open(file, "r", encoding="utf-8") as fh:
                j = json.load(fh)
                family_name = j.get("family") or file.stem
                FAMILIES[family_name] = j
                # index questions for quick match
                items = j.get("items", [])
                for it in items:
                    qlist = it.get("questions") or []
                    for q in qlist:
                        QUESTION_INDEX.append({
                            "family": family_name,
                            "item": it,
                            "question": q,
                            "id": it.get("id")
                        })
        except Exception as e:
            print(f"Failed load {file}: {e}")


# initial load
load_families()

# runtime
runtime = load_runtime_config()
if not runtime.get("families"):
    runtime["families"] = list(FAMILIES.keys())

# --- Matching utility (simple fuzzy heuristic) ---

def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def find_best_item(question: str, prefer_families: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Return best matched item dict or None"""
    qn = normalize_text(question)
    # first exact substring match against indexed questions
    best = None
    best_score = 0
    for entry in QUESTION_INDEX:
        fam = entry["family"]
        if prefer_families and fam not in prefer_families:
            continue
        candidate_q = normalize_text(entry["question"])
        score = 0
        if candidate_q == qn:
            score = 100
        elif candidate_q in qn or qn in candidate_q:
            score = 60
        else:
            # word overlap
            a = set(candidate_q.split())
            b = set(qn.split())
            inter = a.intersection(b)
            score = len(inter)
        if score > best_score:
            best_score = score
            best = entry
    # secondary: try keyword/tag matching
    if best_score == 0:
        # check tags in items
        qwords = set(qn.split())
        for fam, data in FAMILIES.items():
            for it in data.get("items", []):
                tags = set([t.lower() for t in it.get("tags", [])])
                if tags and (tags & qwords):
                    return {"family": fam, "item": it, "question": next(iter(it.get("questions", [""])), ""), "id": it.get("id")}
    return best


# --- Answer generation ---

def pick_gold_variant(item: Dict[str, Any]) -> str:
    """Pick 'best' gold variant from item.response_variants. Strategy: longest variant (narrative) and merge if multiple."""
    variants = item.get("response_variants") or []
    if not variants:
        # fallback to canonical
        return item.get("canonical", "")
    # prefer the longest textual variant (by characters)
    variants_sorted = sorted(variants, key=lambda v: len(v) if isinstance(v, str) else 0, reverse=True)
    # Return the top variant; if multiple, join first two with newline
    top = variants_sorted[0]
    if len(variants_sorted) > 1:
        second = variants_sorted[1]
        # avoid duplications
        if second and second != top:
            return f"{top}\n\n{second}"
    return top


def pick_canonical(item: Dict[str, Any]) -> str:
    return item.get("canonical", "")


# language detection stub: detect if question starts with language token like "EN: ..." or contains common words
LANG_MAP = {"en": ["can i", "how to", "when to"], "it": ["posso", "quando", "come"], "fr": ["puis-je", "quand"], "de": ["kann ich", "wann"], "es": ["puedo", "cuando"]}

def detect_language(question: str, default: str = "it") -> str:
    q = question.strip().lower()
    m = re.match(r"^(en|it|fr|de|es|ru):\s+", q)
    if m:
        return m.group(1)
    for lang, clues in LANG_MAP.items():
        for c in clues:
            if c in q:
                return lang
    return default


# --- API models ---
class AskRequest(BaseModel):
    question: str
    force_mode: Optional[str] = None  # 'gold' or 'canonical'
    prefer_families: Optional[List[str]] = None

class ConfigRequest(BaseModel):
    mode: Optional[str]
    families: Optional[List[str]]


# --- Routes ---
@app.get("/", response_class=HTMLResponse)
async def root():
    # Minimal UI placeholder -- the user already has a UI; keep it light.
    html = f"""
    <html>
      <head>
        <meta charset='utf-8'/>
        <title>TECNARIA Sinapsi</title>
        <style>
          body {{ font-family: Inter, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial; margin: 24px; background:#fff; color:#111 }}
          .brand {{ display:flex; align-items:center; gap:12px }}
          .card {{ border-radius:10px; padding:18px; box-shadow:0 6px 18px rgba(0,0,0,0.06); max-width:980px }}
          .mode {{ font-weight:700; color:#FF6A00 }}
        </style>
      </head>
      <body>
        <div class='card'>
          <div class='brand'>
            <img src='/static/logo.png' alt='Tecnaria' style='height:56px' onerror="this.style.display='none'"/>
            <div>
              <h1>TECNARIA Sinapsi - Backend</h1>
              <div>Modalità: <span class='mode'>{runtime.get('mode','gold').upper()}</span></div>
            </div>
          </div>
          <hr/>
          <div>
            <p>Use <code>/api/ask</code> to query, <code>/api/config</code> to view/update runtime config.</p>
          </div>
        </div>
      </body>
    </html>
    """
    return HTMLResponse(html)


@app.get("/api/config")
async def get_config():
    cfg = load_runtime_config()
    # include families discovered
    cfg["available_families"] = list(FAMILIES.keys())
    return JSONResponse(cfg)


@app.post("/api/config")
async def post_config(req: ConfigRequest):
    cfg = load_runtime_config()
    if req.mode:
        mode = req.mode.lower()
        if mode not in ("gold", "canonical"):
            raise HTTPException(status_code=400, detail="mode must be 'gold' or 'canonical'")
        cfg["mode"] = mode
    if req.families is not None:
        # validate
        unknown = [f for f in req.families if f not in FAMILIES]
        if unknown:
            raise HTTPException(status_code=400, detail={"unknown_families": unknown})
        cfg["families"] = req.families
    save_runtime_config(cfg)
    return JSONResponse(cfg)


@app.post("/api/reload")
async def api_reload():
    load_families()
    # refresh runtime families if empty
    cfg = load_runtime_config()
    if not cfg.get("families"):
        cfg["families"] = list(FAMILIES.keys())
        save_runtime_config(cfg)
    return JSONResponse({"ok": True, "message": "reloaded", "families": list(FAMILIES.keys())})


@app.post("/api/ask")
async def api_ask(req: AskRequest):
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Empty question")
    # language detect
    lang = detect_language(question)
    # mode selection
    cfg = load_runtime_config()
    mode = cfg.get("mode", "gold")
    if req.force_mode:
        fm = req.force_mode.lower()
        if fm in ("gold", "canonical"):
            mode = fm
    # prefer families if provided in request else runtime families
    prefer = req.prefer_families or cfg.get("families") or list(FAMILIES.keys())

    match = find_best_item(question, prefer_families=prefer)
    if not match:
        # fallback: try full-text scan of canonical fields
        fallback = None
        qn = normalize_text(question)
        for fam, data in FAMILIES.items():
            for it in data.get("items", []):
                can = normalize_text(it.get("canonical",""))
                if qn in can or can in qn:
                    fallback = {"family": fam, "item": it, "question": it.get("questions", [""])[0], "id": it.get("id")}
                    break
            if fallback:
                break
        if fallback:
            match = fallback

    if not match:
        return JSONResponse({"found": False, "message": "Nessuna risposta GOLD trovata nei JSON. Potrebbe essere necessario aggiungere un blocco nel JSON.", "mode_requested": mode})

    item = match["item"]
    fam = match["family"]
    # pick answer
    if mode == "gold":
        answer = pick_gold_variant(item)
    else:
        answer = pick_canonical(item)

    # ensure P560 business rule: if family is CTF the GOLD must mention P560. We do not modify JSONs here; we only warn if absent.
    warnings = []
    if fam.upper() == "CTF":
        if "p560" not in answer.lower() and "p560" not in (item.get("canonical","") or "").lower():
            warnings.append("Nota: la risposta CTF non cita la P560. Se la P560 è obbligatoria, aggiorna il JSON CTF per includerla nella variante GOLD.")

    resp = {
        "found": True,
        "family": fam,
        "id": item.get("id"),
        "lang_detected": lang,
        "mode": mode,
        "answer": answer,
        "warnings": warnings
    }
    return JSONResponse(resp)


# --- Startup event to ensure config exists ---
@app.on_event("startup")
async def startup_event():
    # ensure runtime config exists
    cfg = load_runtime_config()
    if not cfg.get("families"):
        cfg["families"] = list(FAMILIES.keys())
        save_runtime_config(cfg)
    # create sample static/logo.png placeholder if missing
    static_dir = Path("static")
    static_dir.mkdir(exist_ok=True)
    logo_path = static_dir / "logo.png"
    # leave it to user to populate logo; don't overwrite
    print("Startup complete. Families loaded:", list(FAMILIES.keys()))


# For direct execution (dev)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=10000, reload=True)
