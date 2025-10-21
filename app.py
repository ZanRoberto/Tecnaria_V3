import os
import json
import time
import re
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

from fastapi import FastAPI, HTTPException, Query, Body
from pydantic import BaseModel
from starlette.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles

# =========================================
# TECNARIA app.py — precision mode + OVERVIEWS
# - Path fix portabile
# - Famiglie & sinonimi (VCEM separato da CEM-E, GTS pulito)
# - Confronto a due colonne + filtro "overview"
# - Boost per match esatto/quasi-esatto
# - NUOVO: carico tecnaria_overviews.json e preferenza overview
# =========================================

UI_TITLE = os.getenv("UI_TITLE", "Tecnaria – QA Bot")

# --- Base path portabile ---
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = (BASE_DIR / "static").resolve()
DATA_DIR = Path(os.getenv("DATA_DIR", str(STATIC_DIR / "data"))).resolve()
ROUTER_FILE = DATA_DIR / os.getenv("ROUTER_FILE", "tecnaria_router_index.json")
OVERVIEWS_FILE = DATA_DIR / "tecnaria_overviews.json"

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

_Q_KEYS: Tuple[str, ...]     = ("q", "question", "prompt", "domanda")
_A_KEYS: Tuple[str, ...]     = ("a", "answer", "risposta", "text")
_CAT_KEYS: Tuple[str, ...]   = ("category", "categoria", "section", "cat")
_ID_KEYS: Tuple[str, ...]    = ("id", "code", "_id", "match_id")

def _get_first(d: Dict[str, Any], keys: Tuple[str, ...], default: str = "") -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return default

def _normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

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

# ---------- Scoperta file QA ----------
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

# ---------- Overviews loader ----------
def _load_overviews() -> List[Dict[str, Any]]:
    """
    Formati accettati:
    - lista di {id?, q, a, category?="overview"}
    - oppure { items: [...] } / { qa: [...] } ecc.
    """
    if not OVERVIEWS_FILE.exists():
        return []
    data = _read_json(OVERVIEWS_FILE)
    rows = extract_qa_entries(data)
    out: List[Dict[str, Any]] = []
    for r in rows:
        r = dict(r)
        if not r.get("category"):
            r["category"] = "overview"
        r["is_overview"] = True
        if not r.get("id"):
            r["id"] = f"OVERVIEW::{abs(hash(r.get('q','')))}"
        out.append(r)
    return out

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
            rid = r.get("id") or f"{p.name}::{abs(hash(r.get('q','')))}"
            if rid in seen_ids:
                continue
            r["id"] = rid
            r["is_overview"] = bool(r.get("category","").lower() == "overview")
            items.append(r)
            seen_ids.add(rid)

    # Append curated overviews (se presenti)
    ov = _load_overviews()
    for r in ov:
        if r["id"] not in seen_ids:
            items.append(r)
            seen_ids.add(r["id"])

    # Traccia file sorgenti (aggiungo overviews “virtuale” se esiste)
    files_out = list(files)
    if OVERVIEWS_FILE.exists():
        files_out.append(OVERVIEWS_FILE)
    return items, files_out

# ---------- Normalizzazione & intent ----------
def _normalize_text(s: str) -> List[str]:
    buf = []
    s = (s or "").lower()
    for ch in s:
        if ch.isalnum() or ch.isspace():
            buf.append(ch)
        else:
            buf.append(" ")
    tokens = [t for t in "".join(buf).split() if t]
    STOP = {"il","lo","la","i","gli","le","un","una","uno","di","a","da","in","con","su","per","tra","fra","e","o","ed","oppure","del","della","dello","dei","degli","delle"}
    return [t for t in tokens if t not in STOP]

def _bigrams(tokens: List[str]) -> List[str]:
    return [tokens[i] + " " + tokens[i+1] for i in range(len(tokens)-1)]

# =======================
# Sinonimi famiglie
# =======================
FAMILY_SYNONYMS = {
    "CTF": {"CTF"},
    "CTL": {"CTL"},
    "CEME": {"CEME", "CEM-E", "CTCEM"},
    "VCEM": {"VCEM"},
    "DIAPASON": {"DIAPASON", "DIA"},
    "GTS": {"GTS"},
    "MINI-CEM-E": {"MINI-CEM-E", "MINICEME", "MINI", "MINI CEM-E"},
}
COMPARE_HINTS = {" vs ", "vs", "contro", "differenza", "differenze", "meglio", "oppure", " o "}

def _detect_families(q: str) -> List[str]:
    qU = q.upper()
    found = []
    for fam, syns in FAMILY_SYNONYMS.items():
        if any((" " + s + " ") in (" " + qU + " ") for s in syns) or any(s in qU for s in syns):
            found.append(fam)
    out = []
    for f in found:
        if f not in out:
            out.append(f)
    return out

def _is_comparative(q: str, fams: List[str]) -> bool:
    qL = " " + q.lower() + " "
    hint = any(h in qL for h in COMPARE_HINTS)
    return hint and len(set(fams)) >= 2

# Query “overview”?
_GENERIC_OV = re.compile(r"\b(parlami|overview|descrizione|spiega|che\s+cos[’']?e|cos[’']?e|introduzione)\b", re.I)
_BLOCK_SPEC = ("codici","consumi","prezzo","eta","tabella","dimensioni","resine","preforo","propulsori","hsbr14","p560")

def _is_overview_query(q: str) -> bool:
    ql = (q or "").lower()
    if _GENERIC_OV.search(ql):
        if any(k in ql for k in _BLOCK_SPEC):
            return False
        return True
    return False

# ---------- Scoring (con bonus overview opzionale) ----------
def _score(query: str,
           candidate_q: str,
           candidate_a: str,
           boost_tokens: Optional[List[str]] = None,
           require_tokens: Optional[List[str]] = None,
           is_overview_candidate: bool = False,
           prefer_overview: bool = False) -> float:
    qtok = _normalize_text(query)
    if not qtok:
        return 0.0
    cq = _normalize_text(candidate_q)
    ca = _normalize_text(candidate_a)
    if not (cq or ca):
        return 0.0

    if require_tokens:
        req = {t.lower() for t in require_tokens}
        cand = set(cq) | set(ca)
        if not (req & cand):
            return 0.0

    qt = set(qtok)
    ct = set(cq) | set(ca)
    unigram = len(qt & ct) / max(1, len(qt))

    qb = set(_bigrams(qtok))
    cb = set(_bigrams(cq)) | set(_bigrams(ca))
    bigram = len(qb & cb) / max(1, len(qb)) if qb else 0.0

    bonus = 0.0
    if boost_tokens:
        bt = {t.lower() for t in boost_tokens}
        if bt & set(cq):
            bonus += 0.15
        if bt & set(ca):
            bonus += 0.10

    # Boost domanda≈Q candidate
    q_lower = query.lower().strip()
    cq_lower = (candidate_q or "").lower().strip()
    if cq_lower == q_lower:
        bonus += 0.25
    elif q_lower in cq_lower:
        bonus += 0.15

    # Boost se la query è “overview” e la candidata è “overview”
    if prefer_overview and is_overview_candidate:
        bonus += 0.20  # spinge le sintetiche curate

    score = 0.65 * unigram + 0.35 * bigram + bonus
    return min(1.0, score)

def kb_search(query: str, k: int = 5,
              boost_tokens: Optional[List[str]] = None,
              require_tokens: Optional[List[str]] = None,
              prefer_overview: bool = False,
              with_scores: bool = False) -> List[Dict[str, Any]]:
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for it in KB:
        s = _score(query,
                   it.get("q", ""),
                   it.get("a", ""),
                   boost_tokens=boost_tokens,
                   require_tokens=require_tokens,
                   is_overview_candidate=bool(it.get("is_overview")),
                   prefer_overview=prefer_overview)
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
    return {"ok": True, "items_loaded": len(KB), "files": [p.name if isinstance(p, Path) else str(p) for p in KB_FILES]}

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
    items = kb_search(q, k=k, prefer_overview=_is_overview_query(q))
    return {"ok": True, "count": len(items), "items": items}

# SOLO POST
@app.post("/api/ask")
async def api_ask(payload: AskIn = Body(...)):
    q = (payload.q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail={"error": "Campo 'q' mancante o vuoto"})

    fams = _detect_families(q)
    is_overview = _is_overview_query(q)

    # ============================
    # MODALITÀ COMPARATIVA (con filtro overview)
    # ============================
    if _is_comparative(q, fams):
        fams = list(dict.fromkeys(fams))[:2]
        left_fam, right_fam = fams[0], fams[1]

        left_tokens  = list(FAMILY_SYNONYMS.get(left_fam, {left_fam}))
        right_tokens = list(FAMILY_SYNONYMS.get(right_fam, {right_fam}))

        left_hits = kb_search(q, k=3, boost_tokens=left_tokens, require_tokens=left_tokens, prefer_overview=False)
        right_hits = kb_search(q, k=3, boost_tokens=right_tokens, require_tokens=right_tokens, prefer_overview=False)

        # evita overview nei confronti
        def _drop_overview(hits: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
            filtered = [h for h in hits if not bool(h.get("is_overview")) and "overview" not in (h.get("category","").lower())]
            return filtered or hits

        left_hits  = _drop_overview(left_hits)
        right_hits = _drop_overview(right_hits)

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

    # Ricerca normale (richiede tokens se famiglie presenti)
    require = None
    boost = None
    if fams:
        require = sorted({t for f in fams for t in FAMILY_SYNONYMS.get(f, {f})})
        boost = require

    t0 = time.perf_counter()
    hits = kb_search(q, k=5, boost_tokens=boost, require_tokens=require, prefer_overview=is_overview)

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
