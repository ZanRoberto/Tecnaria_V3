import os
import re
import json
import time
import math
from typing import List, Dict, Any, Optional

import requests
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# -----------------------------
# Config & helpers
# -----------------------------
APP_NAME = "Tecnaria QA Bot"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CRITICI_DIR = os.getenv("CRITICI_DIR", os.path.join(BASE_DIR, "static", "data"))
SINAPSI_FILE_ENV = os.getenv("SINAPSI_FILE", "sinapsi_rules.json")
SINAPSI_PATH = SINAPSI_FILE_ENV if os.path.isabs(SINAPSI_FILE_ENV) else os.path.join(CRITICI_DIR, SINAPSI_FILE_ENV)

SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "brave").lower().strip()
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()
BING_API_KEY = os.getenv("BING_API_KEY", "").strip()

PREFERRED_DOMAINS = [d.strip() for d in os.getenv("PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com").split(",") if d.strip()]
MIN_WEB_SCORE = float(os.getenv("MIN_WEB_SCORE", "0.35"))
WEB_TIMEOUT = float(os.getenv("WEB_TIMEOUT", "6"))

STATIC_DIR = os.path.join(BASE_DIR, "static")
INDEX_HTML = os.path.join(STATIC_DIR, "index.html")

# -----------------------------
# App
# -----------------------------
app = FastAPI(title=APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_headers=["*"],
    allow_methods=["*"],
    allow_credentials=False,
)

# Mount /static
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# -----------------------------
# Sinapsi rules loader
# -----------------------------
class SynRule:
    def __init__(self, rid: str, pattern: str, mode: str, lang: str, answer: str):
        self.id = rid
        self.pattern_raw = pattern
        self.re = re.compile(pattern, re.IGNORECASE)
        self.mode = (mode or "augment").lower()
        self.lang = lang or "it"
        self.answer = answer

def load_sinapsi(path: str) -> List[SynRule]:
    rules: List[SynRule] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            for i, r in enumerate(data):
                try:
                    rules.append(SynRule(
                        rid=r.get("id", f"rule_{i}"),
                        pattern=r.get("pattern", ".^"),
                        mode=r.get("mode", "augment"),
                        lang=r.get("lang", "it"),
                        answer=r.get("answer", "")
                    ))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return rules

SINAPSI_RULES: List[SynRule] = load_sinapsi(SINAPSI_PATH)

# -----------------------------
# Web search
# -----------------------------
def _score_for(idx: int) -> float:
    # Simple decay by rank
    return max(0.0, 1.0 - 0.12 * idx)

def _domain(url: str) -> str:
    try:
        return url.split("//", 1)[1].split("/", 1)[0].lower()
    except Exception:
        return ""

def _is_pdf(result: Dict[str, Any]) -> bool:
    url = result.get("url") or ""
    return url.lower().endswith(".pdf")

def brave_search(query: str, limit: int = 6) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        return []
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"X-Subscription-Token": BRAVE_API_KEY}
    params = {"q": query, "count": max(3, min(10, limit))}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=WEB_TIMEOUT)
        r.raise_for_status()
        js = r.json()
        items = []
        for block in js.get("web", {}).get("results", []):
            items.append({
                "title": block.get("title") or "",
                "url": block.get("url") or "",
                "snippet": block.get("description") or ""
            })
        return items
    except Exception:
        return []

def bing_search(query: str, limit: int = 6) -> List[Dict[str, Any]]:
    if not BING_API_KEY:
        return []
    url = "https://api.bing.microsoft.com/v7.0/search"
    headers = {"Ocp-Apim-Subscription-Key": BING_API_KEY}
    params = {"q": query, "count": max(3, min(10, limit)), "textDecorations": False, "textFormat": "Raw"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=WEB_TIMEOUT)
        r.raise_for_status()
        js = r.json()
        items = []
        for d in js.get("webPages", {}).get("value", []):
            items.append({
                "title": d.get("name") or "",
                "url": d.get("url") or "",
                "snippet": d.get("snippet") or ""
            })
        return items
    except Exception:
        return []

def search_web(query: str) -> List[Dict[str, Any]]:
    results = []
    if SEARCH_PROVIDER == "brave":
        results = brave_search(query)
        if not results and BING_API_KEY:
            results = bing_search(query)
    else:
        results = bing_search(query)
        if not results and BRAVE_API_KEY:
            results = brave_search(query)
    # Re-rank: preferred domains up, then by rank score
    rescored = []
    for i, r in enumerate(results):
        url = r.get("url", "")
        dom = _domain(url)
        bonus = 0.3 if any(dom.endswith(p) or dom == p for p in PREFERRED_DOMAINS) else 0.0
        score = _score_for(i) + bonus
        rescored.append({**r, "score": round(score, 3)})
    rescored.sort(key=lambda x: x["score"], reverse=True)
    # Filter by minimal score
    rescored = [r for r in rescored if r["score"] >= MIN_WEB_SCORE]
    return rescored[:6]

# -----------------------------
# Compose answer
# -----------------------------
def apply_sinapsi(question: str) -> Dict[str, Any]:
    """Returns {'override': str|None, 'addons': [str]}"""
    override_text: Optional[str] = None
    addons: List[str] = []
    for rule in SINAPSI_RULES:
        if rule.re.search(question or ""):
            if rule.mode == "override" and not override_text:
                override_text = rule.answer.strip()
            elif rule.mode in ("augment", "postscript"):
                addons.append(rule.answer.strip())
    return {"override": override_text, "addons": addons}

def build_summary_from_web(results: List[Dict[str, Any]]) -> str:
    """
    Produce una sintesi breve e leggibile SOLO testuale (niente blob PDF).
    """
    if not results:
        return ""
    # Prendi 1–2 snippet utili delle fonti preferite (tecnaria.com prima)
    primary: List[str] = []
    for r in results:
        snip = (r.get("snippet") or "").strip()
        if not snip:
            continue
        # Evita contenuti troppo tecnici in inglese: accorcia
        snip = re.sub(r"\s+", " ", snip)
        primary.append(snip)
        if len(primary) >= 2:
            break
    if not primary:
        return ""
    if len(primary) == 1:
        return primary[0]
    return f"{primary[0]} {primary[1]}"

def collect_sources(results: List[Dict[str, Any]]) -> List[str]:
    seen = set()
    out = []
    for r in results:
        u = r.get("url", "")
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= 6:
            break
    return out

def format_answer(question: str, web_syn: str, addons: List[str], sources: List[str], override: Optional[str]) -> str:
    """
    Stile commerciale tecnico:
    - 1 riga di stato "OK"
    - Paragrafo chiaro
    - Punti concisi
    - Fonti sempre in fondo (link)
    """
    lines: List[str] = []
    lines.append("OK")

    if override:
        # Quando Sinapsi decide di rispondere al posto del web
        lines.append(override.strip())
    else:
        # Corpo principale
        if web_syn:
            lines.append(web_syn.strip())

        # Addons (Sinapsi) come integrazione
        for add in addons:
            if add:
                lines.append(add.strip())

    # Fonti in coda (mai prima)
    if sources:
        lines.append("")
        lines.append("Fonti:")
        for u in sources[:6]:
            lines.append(f"- {u}")

    return "\n".join(lines).strip() + "\n"

# -----------------------------
# API
# -----------------------------
@app.get("/ping")
def ping():
    return {"ok": True, "pong": True}

@app.get("/health")
def health():
    info = {
        "status": "ok",
        "web_search": {
            "provider": SEARCH_PROVIDER,
            "brave_key": bool(BRAVE_API_KEY),
            "bing_key": bool(BING_API_KEY),
            "preferred_domains": PREFERRED_DOMAINS,
            "min_web_score": MIN_WEB_SCORE
        },
        "critici": {
            "dir": CRITICI_DIR,
            "exists": os.path.isdir(CRITICI_DIR),
            "sinapsi_file": SINAPSI_PATH,
            "sinapsi_loaded": len(SINAPSI_RULES)
        }
    }
    return JSONResponse(info)

@app.get("/")
def root():
    if os.path.isfile(INDEX_HTML):
        return FileResponse(INDEX_HTML, media_type="text/html; charset=utf-8")
    return JSONResponse({"ok": True, "msg": "Use /ask or place static/index.html"})

def _answer_core(q: str) -> Dict[str, Any]:
    q = (q or "").strip()
    if not q:
        return {"ok": True, "answer": "OK\nDomanda vuota: inserisci una richiesta valida.\n"}

    # 1) Web
    web_results = search_web(q)
    web_synopsis = build_summary_from_web(web_results)
    sources = collect_sources(web_results)

    # 2) Sinapsi
    syn = apply_sinapsi(q)
    override = syn["override"]
    addons = syn["addons"]

    # 3) Format
    answer = format_answer(q, web_synopsis, addons, sources, override)
    return {"ok": True, "answer": answer}

@app.get("/api/ask")
def api_ask_get(q: str = Query("", description="Domanda")):
    try:
        return JSONResponse(_answer_core(q))
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)

@app.post("/api/ask")
async def api_ask_post(payload: Dict[str, Any]):
    try:
        q = (payload or {}).get("q", "")
        return JSONResponse(_answer_core(q))
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)
