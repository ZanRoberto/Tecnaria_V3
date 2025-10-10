import os
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple

from fastapi import FastAPI, HTTPException, Query, Body, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

# ==============================
# TECNARIA app.py — versione sicura (no HTML/JS inline, no CORS *)
# Carica SOLO i file QA ufficiali elencati nel router o per pattern tecnaria_*_qa*.json
# Nessun uso di static/data/critici per famiglie.
# ==============================
UI_TITLE = os.getenv("UI_TITLE", "Tecnaria – QA Bot")
DATA_DIR = Path(os.getenv("DATA_DIR", ".")).resolve()
ROUTER_FILE = DATA_DIR / os.getenv("ROUTER_FILE", "tecnaria_router_index.json")

KB: List[Dict[str, Any]] = []
KB_FILES: List[Path] = []


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Permissions-Policy"] = "geolocation=()"
        resp.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        return resp


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


def _normalize_text(s: str) -> List[str]:
    buf = []
    for ch in s.lower():
        if ch.isalnum() or ch.isspace():
            buf.append(ch)
        else:
            buf.append(" ")
    return [t for t in "".join(buf).split() if t]


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
            "q": " ".join(q.split()),
            "a": a.strip(),
        })
    return out


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
    if not files:
        for p in DATA_DIR.glob("tecnaria_*_qa*.json"):
            files.append(p)
    # dedup preservando ordine
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


def _score(query: str, candidate_q: str, candidate_a: str) -> float:
    qt = set(_normalize_text(query))
    if not qt:
        return 0.0
    ct = set(_normalize_text(candidate_q)) | set(_normalize_text(candidate_a))
    if not ct:
        return 0.0
    overlap = len(qt & ct)
    return overlap / max(1, len(qt))


def kb_search(query: str, k: int = 5) -> List[Dict[str, Any]]:
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for it in KB:
        s = _score(query, it.get("q", ""), it.get("a", ""))
        if s > 0:
            scored.append((s, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [it for _, it in scored[:k]]


app = FastAPI(title=UI_TITLE)
app.add_middleware(SecurityHeadersMiddleware)


class AskIn(BaseModel):
    q: str


@app.on_event("startup")
async def startup_event():
    global KB, KB_FILES
    KB, KB_FILES = _load_kb()


@app.get("/health")
async def health():
    return {"ok": True, "items_loaded": len(KB), "files": [p.name for p in KB_FILES]}


@app.get("/")
async def root():
    return PlainTextResponse(f"{UI_TITLE} — pronto")


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


@app.post("/api/ask")
async def api_ask(payload: AskIn = Body(...)):
    q = (payload.q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail={"error": "Campo 'q' mancante o vuoto"})

    t0 = time.perf_counter()
    hits = kb_search(q, k=5)

    if hits:
        best = hits[0]
        html = "<div><h2>Risposta Tecnaria</h2><p>" + best.get("a", "") + "</p><p>Fonte: " + best.get("id", "") + "</p></div>"
        dt = int((time.perf_counter() - t0) * 1000)
        return JSONResponse({"ok": True, "html": html, "ms": dt, "match_id": best.get("id")})

    html = "<div><h2>Risposta Tecnaria</h2><p>Nessuna corrispondenza nei dataset QA ufficiali caricati. Aggiorna il router o il file di famiglia corretto.</p></div>"
    dt = int((time.perf_counter() - t0) * 1000)
    return JSONResponse({"ok": True, "html": html, "ms": dt, "match_id": None})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8010")), reload=False)
