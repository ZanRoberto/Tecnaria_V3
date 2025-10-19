import os
import json
import time
import re
from pathlib import Path
from typing import List, Dict, Any, Tuple

from fastapi import FastAPI, HTTPException, Query, Body
from pydantic import BaseModel
from starlette.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles

# =========================================
# TECNARIA app.py — precision mode
# - /static montata (serve la tua UI)
# - "/" -> redirect a /static/ui/index_bolle.html se presente, altrimenti 404
# - /api/ask SOLO POST
# - /kb/diag diagnostica ranking
# =========================================

UI_TITLE = os.getenv("UI_TITLE", "Tecnaria – QA Bot")

# --- PATCH corretta per Render / locale ---
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "static" / "data"))).resolve()
ROUTER_FILE = DATA_DIR / os.getenv("ROUTER_FILE", "tecnaria_router_index.json")
# ------------------------------------------

KB: List[Dict[str, Any]] = []
KB_FILES: List[Path] = []

# ---------- Security middleware ----------
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Permissions-Policy"] = "geolocation=()"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'"
        )
        return resp

# ---------- Helpers JSON/estrazione QA ----------
def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

def _as_iter(obj: Any):
    if obj is None:
        return []
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in ("items", "qa", "data", "rows"):
            val = obj.get(key)
            if isinstance(val, list):
                return val
        return [obj]
    return []

_Q_KEYS: Tuple[str, ...] = ("q", "question", "prompt", "domanda")
_A_KEYS: Tuple[str, ...] = ("a", "answer", "risposta")
_CAT_KEYS: Tuple[str, ...] = ("category", "categoria", "section")
_ID_KEYS: Tuple[str, ...] = ("id", "code", "_id")

def _get_first(d: Dict[str, Any], keys: Tuple[str, ...], default: str = "") -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return default

def _normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def extract_qa_entries(data: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in _as_iter(data):
        if not isinstance(row, dict):
            continue
        q = _get_first(row, _Q_KEYS)
        a = _get_first(row, _A_KEYS)
        if not q or not a:
            continue
        out.append({
            "id": _get_first(row, _ID_KEYS, ""),
            "category": _get_first(row, _CAT_KEYS, ""),
            "q": _normalize_spaces(q),
            "a": a.strip(),
        })
    return out

# ---------- Scoperta file QA (router first, poi pattern) ----------
def _discover_qa_files() -> List[Path]:
    files: List[Path] = []
    if ROUTER_FILE.exists():
        router = _read_json(ROUTER_FILE)
        if isinstance(router, dict):
            qa_list = router.get("qa_files") or router.get("files") or router.get("datasets")
            if isinstance(qa_list, list):
                for name in qa_list:
                    p = DATA_DIR / str(name)
                    if p.exists():
                        files.append(p)
            if not files and isinstance(router.get("products"), list):
                for prod in router["products"]:
                    if isinstance(prod, dict) and prod.get("file"):
                        p = DATA_DIR / str(prod["file"])
                        if p.exists():
                            files.append(p)
    if not files:
        for p in DATA_DIR.glob("tecnaria_*_qa*.json"):
            files.append(p)

    global_file = DATA_DIR / "SINAPSI_GLOBAL_TECNARIA_EXT.json"
    if global_file.exists():
        files.append(global_file)

    seen = set()
    unique: List[Path] = []
    for p in files:
        if p not in seen:
            unique.append(p)
            seen.add(p)
    return unique

def _load_kb() -> Tuple[List[Dict[str, Any]], List[Path]]:
    files = _discover_qa_files()
    items: List[Dict[str, Any]] = []
    seen_ids = set()
    for p in files:
        data = _read_json(p)
        if data is None:
            continue
        rows = extract_qa_entries(data)
        for r in rows:
            rid = r.get("id")
            if not rid:
                rid = f"{p.name}::{abs(hash(r.get('q','')))}"
            if rid in seen_ids:
                continue
            r["id"] = rid
            items.append(r)
            seen_ids.add(rid)
    return items, files

# ---------- Normalizzazione & intent ----------
def _normalize_text(s: str) -> List[str]:
    buf = []
    s = (s or "").lower()
    # normalizza caratteri speciali/segni
    for ch in s:
        if ch.isalnum() or ch.isspace():
            buf.append(ch)
        else:
            buf.append(" ")
    tokens = [t for t in "".join(buf).split() if t]
    # rimozione stopword semplici italiane (minima, non distruttiva)
    STOP = {"il","lo","la","i","gli","le","un","una","uno","di","a","da","in","con","su","per","tra","fra","e","o","ed","oppure","del","della","dello","dei","degli","delle"}
    return [t for t in tokens if t not in STOP]

def _bigrams(tokens: List[str]) -> List[str]:
    return [tokens[i] + " " + tokens[i+1] for i in range(len(tokens)-1)]

FAMILY_SYNONYMS = {
    "CTF": {"CTF"},
    "CTL": {"CTL"},
    "CEME": {"CEME", "CEM-E", "CTCEM", "VCEM"},
    "DIAPASON": {"DIAPASON", "DIA"},
    "GTS": {"GTS"},
    "MINI-CEM-E": {"MINI-CEM-E", "MINICEME", "MINI", "MINI CEM-E"},
}

COMPARE_HINTS = {" vs ", "vs", "contro", "differenza", "differenze", "meglio", "oppure", "o "}

def _detect_families(q: str) -> List[str]:
    qU = q.upper()
    found = []
    for fam, syns in FAMILY_SYNONYMS.items():
        if any((" " + s + " ") in (" " + qU + " ") for s in syns) or any(s in qU for s in syns):
            found.append(fam)
    # preserva ordine di apparizione
    out = []
    for f in found:
        if f not in out:
            out.append(f)
    return out

def _is_comparative(q: str, fams: List[str]) -> bool:
    qL = " " + q.lower() + " "
    hint = any(h in qL for h in COMPARE_HINTS)
    return hint and len(set(fams)) >= 2

# ---------- Scoring migliorato ----------
def _score(query: str, candidate_q: str, candidate_a: str,
           boost_tokens: List[str] | None = None,
           require_tokens: List[str] | None = None) -> float:
    qtok = _normalize_text(query)
    if not qtok:
        return 0.0
    cq = _normalize_text(candidate_q)
    ca = _normalize_text(candidate_a)
    if not (cq or ca):
        return 0.0

    # filtro hard: se query cita famiglie, il candidato deve contenere almeno uno dei token richiesti
    if require_tokens:
        req = {t.lower() for t in require_tokens}
        cand = set(cq) | set(ca)
        if not (req & cand):
            return 0.0

    # feature overlap unigrams
    qt = set(qtok)
    ct = set(cq) | set(ca)
    unigram = len(qt & ct) / max(1, len(qt))

    # feature overlap bigrammi
    qb = set(_bigrams(qtok))
    cb = set(_bigrams(cq)) | set(_bigrams(ca))
    bigram = 0.0
    if qb and cb:
        bigram = len(qb & cb) / max(1, len(qb))

    # boost se compaiono token "famiglia"
    bonus = 0.0
    if boost_tokens:
        bt = {t.lower() for t in boost_tokens}
        if bt & set(cq):
            bonus += 0.15
        if bt & set(ca):
            bonus += 0.10

    # combinazione pesata
    score = 0.65 * unigram + 0.35 * bigram + bonus
    return min(1.0, score)

def kb_search(query: str, k: int = 5,
              boost_tokens: List[str] | None = None,
              require_tokens: List[str] | None = None,
              with_scores: bool = False) -> List[Dict[str, Any]]:
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for it in KB:
        s = _score(query, it.get("q", ""), it.get("a", ""),
                   boost_tokens=boost_tokens, require_tokens=require_tokens)
        if s > 0:
            scored.append((s, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:k]
    if with_scores:
        return [{"score": round(s, 4), **it} for s, it in top]
    return [it for s, it in top]

# ---------- FastAPI ----------
app = FastAPI(title=UI_TITLE)
app.add_middleware(SecurityHeadersMiddleware)

STATIC_DIR = Path(os.path.join(os.path.dirname(__file__), "static")).resolve()
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

class AskIn(BaseModel):
    q: str

@app.on_event("startup")
async def startup_event():
    global KB, KB_FILES
    KB, KB_FILES = _load_kb()

@app.get("/")
async def root():
    ui_file = STATIC_DIR / "ui" / "index_bolle.html"
    if ui_file.exists():
        return RedirectResponse(url="/static/ui/index_bolle.html")
    raise HTTPException(status_code=404, detail="UI non trovata: static/ui/index_bolle.html mancante")

@app.get("/health")
async def health():
    return {"ok": True, "items_loaded": len(KB), "files": [p.name for p in KB_FILES]}

@app.get("/kb/ids")
async def kb_ids():
    return [it.get("id") for it in KB]

@app.get("/kb/files")
async def kb_files():
    return {"ok": True, "files": [str(p) for p in KB_FILES]}

@app.get("/kb/search")
async def kb_search_endpoint(q: str = Query(""), k: int = Query(5, ge=1, le=20)):
    if not q:
        return {"ok": True, "count": 0, "items": []}
    items = kb_search(q, k=k)
    return {"ok": True, "count": len(items), "items": items}

# 🔎 Endpoint diagnostico: spiega come è stato scelto il match
@app.get("/kb/diag")
async def kb_diag(q: str = Query(...), k: int = Query(5, ge=1, le=20)):
    fams = _detect_families(q)
    require = None
    boost = None
    if fams:
        # richiedi almeno un token di una delle famiglie citate
        require = sorted({t for f in fams for t in FAMILY_SYNONYMS.get(f, {f})})
        boost = require
    items = kb_search(q, k=k, boost_tokens=boost, require_tokens=require, with_scores=True)
    return {
        "ok": True,
        "query": q,
        "families_detected": fams,
        "require_tokens": require or [],
        "boost_tokens": boost or [],
        "items": items
    }

# SOLO POST
@app.post("/api/ask")
async def api_ask(payload: AskIn = Body(...)):
    q = (payload.q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail={"error": "Campo 'q' mancante o vuoto"})

    fams = _detect_families(q)

    # Modalità comparativa: es. "CTF vs CTL", "differenze CTF e CTL", "meglio CTL o CTF"
    if _is_comparative(q, fams):
        fams = list(dict.fromkeys(fams))[:2]
        left_fam, right_fam = fams[0], fams[1]
        left_tokens = sorted(FAMILY_SYNONYMS.get(left_fam, {left_fam}))
        right_tokens = sorted(FAMILY_SYNONYMS.get(right_fam, {right_fam}))

        left_hits = kb_search(q, k=3, boost_tokens=left_tokens, require_tokens=left_tokens)
        right_hits = kb_search(q, k=3, boost_tokens=right_tokens, require_tokens=right_tokens)

        if left_hits or right_hits:
            def _fmt_side(title: str, hits: List[Dict[str,Any]]) -> str:
                if not hits:
                    return f"<div class='side'><h3>{title}</h3><p><i>Nessun riferimento trovato.</i></p></div>"
                best = hits[0]
                src = best.get('id','')
                cat = best.get('category','')
                meta = f"{' — cat: '+cat if cat else ''}"
                return (
                    f"<div class='side'><h3>{title}</h3>"
                    f"<p>{best.get('a','')}</p>"
                    f"<p><small>Fonte: <b>{src}</b>{meta}</small></p></div>"
                )

            html = (
                "<div><h2>Confronto</h2>"
                "<div style='display:flex;gap:24px;flex-wrap:wrap'>"
                f"{_fmt_side(left_fam, left_hits)}"
                f"{_fmt_side(right_fam, right_hits)}"
                "</div></div>"
            )
            return JSONResponse({
                "ok": True,
                "text": "",
                "html": html,
                "ms": 0,
                "match_id": f"COMPARE::{left_fam}_VS_{right_fam}"
            })
        # se non trova nulla, continua con ricerca normale

    # Ricerca normale con filtro semantico se la domanda cita famiglie
    require = None
    boost = None
    if fams:
        require = sorted({t for f in fams for t in FAMILY_SYNONYMS.get(f, {f})})
        boost = require

    t0 = time.perf_counter()
    hits = kb_search(q, k=5, boost_tokens=boost, require_tokens=require)

    if hits:
        best = hits[0]
        html = (
            "<div><h2>Risposta Tecnaria</h2>"
            f"<p>{best.get('a','')}</p>"
            f"<p><small>Fonte: <b>{best.get('id','')}</b>"
            f"{' — cat: '+best.get('category','') if best.get('category') else ''}</small></p>"
            "</div>"
        )
        dt = int((time.perf_counter() - t0) * 1000)
        return JSONResponse({
            "ok": True,
            "text": best.get("a", ""),
            "html": html,
            "ms": dt,
            "match_id": best.get("id")
        })

    html = (
        "<div><h2>Risposta Tecnaria</h2>"
        "<p>Nessuna corrispondenza nei dataset QA ufficiali caricati. "
        "Aggiorna il router o il file di famiglia corretto.</p></div>"
    )
    dt = int((time.perf_counter() - t0) * 1000)
    return JSONResponse({"ok": True, "text": "", "html": html, "ms": dt, "match_id": None})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8010")), reload=False)
