# app.py — Tecnaria QA Bot (finale)
# FastAPI + Sinapsi pre-warm + filtri domini + compositore HTML
# Compatibile con Render.com e avvio locale

import os
import re
import json
import time
import html
import gzip
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import uvicorn
import requests
from fastapi import FastAPI, Request, Body
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

APP_TITLE = "Tecnaria QA Bot"
app = FastAPI(title=APP_TITLE)

# -----------------------------
# Config (da ENV, con default sensati)
# -----------------------------
STATIC_DIR = os.environ.get("STATIC_DIR", "static")
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(STATIC_DIR, "data"))
KB_FILE = os.environ.get("KB_FILE", os.path.join(DATA_DIR, "TECNARIA.TXT05102025.txt"))
SINAPSI_FILE = os.environ.get("SINAPSI_FILE", os.path.join(STATIC_DIR, "sinapsi_rules.json"))
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
ALLOWED_DOMAINS = json.loads(os.environ.get("ALLOWED_DOMAINS_JSON", '["tecnaria.com","spit.eu","spitpaslode.com"]'))
MIN_WEB_SCORE = float(os.environ.get("MIN_WEB_SCORE", "0.35"))
MAX_WEB_RESULTS = int(os.environ.get("MAX_WEB_RESULTS", "5"))
MODE = os.environ.get("MODE", "web_first_then_local")  # "web_first_then_local" | "local_only"

# -----------------------------
# Stato in memoria (pre-warm)
# -----------------------------
KB_TEXT: str = ""
KB_INDEX: List[Tuple[str, int]] = []  # (riga, offset)
SINAPSI_RULES: Dict[str, Any] = {}  # atteso schema: {"rules": [...], "exclude_any_q": [...] }

# -----------------------------
# Utilities
# -----------------------------

def _safe_read_text(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    if p.suffix == ".gz":
        with gzip.open(p, "rt", encoding="utf-8", errors="ignore") as f:
            return f.read()
    return p.read_text(encoding="utf-8", errors="ignore")


def load_kb_and_index() -> None:
    global KB_TEXT, KB_INDEX
    KB_TEXT = _safe_read_text(KB_FILE)
    lines = KB_TEXT.splitlines()
    KB_INDEX = [(ln.strip(), i) for i, ln in enumerate(lines) if ln.strip()]


def load_sinapsi_rules() -> None:
    global SINAPSI_RULES
    txt = _safe_read_text(SINAPSI_FILE)
    if txt.strip():
        try:
            SINAPSI_RULES = json.loads(txt)
        except Exception:
            SINAPSI_RULES = {}
    else:
        # Default minimale, sicuro
        SINAPSI_RULES = {
            "mode": "AB_test",
            "exclude_any_q": [
                "\\bprezz\\w*", "\\bcost\\w*", "\\bpreventiv\\w*"
            ],
            "style": {"tone": "professionale", "length": "standard"}
        }


def _blocked_by_rules(q: str) -> bool:
    rules = SINAPSI_RULES or {}
    patt_list = rules.get("exclude_any_q", [])
    for patt in patt_list:
        try:
            if re.search(patt, q, flags=re.I):
                return True
        except re.error:
            continue
    return False

# -----------------------------
# Sinapsi (estrazione risposte da rules[])
# -----------------------------

def sinapsi_extract_lines(query: str) -> Tuple[List[str], List[str], List[str]]:
    """Ritorna (override_lines, augment_lines, postscript_lines) dalle regole Sinapsi.
    Supporta schema: {"rules":[{"pattern","mode","answer"}, ...]}.
    """
    ovr: List[str] = []
    aug: List[str] = []
    psc: List[str] = []
    rules = []
    data = SINAPSI_RULES or {}
    if isinstance(data, dict):
        rules = data.get("rules") or []
    elif isinstance(data, list):
        rules = data
    q = query or ""
    for r in rules:
        try:
            patt = str(r.get("pattern", "")).strip()
            mode = str(r.get("mode", "augment")).lower()
            ans  = str(r.get("answer", "")).strip()
            if not patt or not ans:
                continue
            if re.search(patt, q, flags=re.I | re.S):
                if mode == "override":
                    ovr.append(ans)
                elif mode == "postscript":
                    psc.append(ans)
                else:
                    aug.append(ans)
        except Exception:
            continue
    r
# -----------------------------
# Web search (Brave) con filtri domini
# -----------------------------

def brave_search(query: str) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        return []
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    params = {"q": query, "count": 10, "freshness": "month"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        items = []
        for it in data.get("web", {}).get("results", []):
            host = (it.get("url", "").split("//")[-1]).split("/")[0]
            score = float(it.get("typeRank", 0.0))
            allowed = any(d in host for d in ALLOWED_DOMAINS)
            if allowed and score >= MIN_WEB_SCORE:
                items.append({
                    "title": it.get("title", ""),
                    "url": it.get("url", ""),
                    "snippet": it.get("description", ""),
                    "host": host,
                    "score": score,
                })
        items.sort(key=lambda x: x["score"], reverse=True)
        return items[:MAX_WEB_RESULTS]
    except Exception:
        return []


# -----------------------------
# Retrieval locale molto semplice
# -----------------------------

def local_retrieve(query: str, k: int = 12) -> List[str]:
    """Estrae righe pertinenti dal KB; ritorna blocchi compattati."""
    if not KB_INDEX:
        return []
    q = query.lower()
    hits: List[Tuple[int, str]] = []
    for line, idx in KB_INDEX:
        l = line.lower()
        score = 0
        # keyword naive
        for token in re.findall(r"[a-zàèéìòóùçA-Z0-9_-]{3,}", q, flags=re.I):
            if token in l:
                score += 1
        if score:
            hits.append((score, line))
    hits.sort(key=lambda x: x[0], reverse=True)
    return [h[1] for h in hits[:k]]


# -----------------------------
# Compositore narrativo + Cards HTML
# -----------------------------

def compose_answer(query: str, web_snips: List[Dict[str, Any]], local_snips: List[str]) -> Tuple[str, List[Dict[str, str]]]:
    """Ritorna (html, sources)."""
    parts: List[str] = []
    sources: List[Dict[str, str]] = []

    # Blocco risposta principale
    intro = f"<p><strong>Domanda:</strong> {html.escape(query)}</p>"
    parts.append(intro)

    if local_snips:
        parts.append("<h3>Risposta Tecnaria (base conoscenza)</h3>")
        bullets = "".join(f"<li>{html.escape(s)}</li>" for s in local_snips)
        parts.append(f"<ul class='list-disc pl-5'>{bullets}</ul>")

    if web_snips:
        parts.append("<h3>Fonti ufficiali (web filtrato)</h3>")
        li_web = []
        for w in web_snips:
            title = html.escape(w.get("title") or w.get("host"))
            url = html.escape(w.get("url", ""))
            snippet = html.escape(w.get("snippet", ""))
            li_web.append(f"<li><a href='{url}' target='_blank'>{title}</a><br><small>{snippet}</small></li>")
            sources.append({"title": title, "url": url})
        parts.append(f"<ol class='list-decimal pl-5'>{''.join(li_web)}</ol>")

    if not local_snips and not web_snips:
        parts.append("<p><em>Nessun contenuto trovato. Raffina la domanda oppure carica la base conoscenza.</em></p>")

    html_out = _wrap_cards("\n".join(parts))
    return html_out, sources


def _wrap_cards(inner_html: str) -> str:
    return f"""
<!doctype html>
<html lang=it>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{APP_TITLE}</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui; margin: 24px; background:#fafafa; color:#111; }}
    .card {{ background:#fff; border:1px solid #eee; border-radius:16px; padding:20px; box-shadow:0 6px 18px rgba(0,0,0,0.06); }}
    .header {{ display:flex; align-items:center; gap:12px; margin-bottom:16px; }}
    input[type=text] {{ width:100%; padding:12px 14px; border-radius:12px; border:1px solid #ddd; }}
    button {{ padding:10px 14px; border-radius:12px; border:0; background:#111; color:#fff; cursor:pointer; }}
    h1 {{ font-size:22px; margin:0; }}
    h3 {{ margin-top:18px; margin-bottom:8px; }}
    ul,ol {{ line-height:1.45; }}
    .muted {{ color:#666; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h1>{APP_TITLE}</h1>
      <span class="muted">web→filtrato + locale (Sinapsi)</span>
    </div>
    <form method="GET" action="/">
      <input type="text" name="q" placeholder="Fai una domanda (es. Differenza CTF vs Diapason)" />
      <button type="submit">Cerca</button>
    </form>
    <div style="height:12px"></div>
    {inner_html}
  </div>
</body>
</html>
"""


# -----------------------------
# Pre-warm all'avvio
# -----------------------------
@app.on_event("startup")
def _startup() -> None:
    os.makedirs(STATIC_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    load_kb_and_index()
    load_sinapsi_rules()


# -----------------------------
# Endpoints
# -----------------------------
@app.get("/health")
def health() -> JSONResponse:
    info = {
        "status": "ok",
        "mode": MODE,
        "kb_file": str(KB_FILE),
        "kb_chars": len(KB_TEXT or ""),
        "kb_lines": len(KB_INDEX or []),
        "rules_path": str(SINAPSI_FILE),
        "rules_loaded": len(SINAPSI_RULES or {}),
        "allowed_domains": ALLOWED_DOMAINS,
        "min_web_score": MIN_WEB_SCORE,
    }
    return JSONResponse(info)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, q: Optional[str] = None):
    if not q:
        html_out = _wrap_cards("<p class='muted'>Digita una domanda per iniziare.</p>")
        return HTMLResponse(html_out)

    if _blocked_by_rules(q):
        return HTMLResponse(_wrap_cards("<p>Mi dispiace, non posso rispondere a richieste di prezzi/costi/preventivi.</p>"))

    web_snips = brave_search(q) if MODE == "web_first_then_local" else []
    local_snips = local_retrieve(q)
    html_out, _ = compose_answer(q, web_snips, local_snips)
    return HTMLResponse(html_out)


@app.post("/ask")
async def ask(payload: Dict[str, Any] = Body(...)):
    q = str(payload.get("q", "")).strip()
    if not q:
        return JSONResponse({"error": "missing q"}, status_code=400)

    if _blocked_by_rules(q):
        return JSONResponse({"ok": True, "blocked": True, "answer_html": _wrap_cards("<p>Richiesta non ammessa (prezzi/costi/preventivi).")})

    web_snips = brave_search(q) if MODE == "web_first_then_local" else []
    local_snips = local_retrieve(q)
    html_out, sources = compose_answer(q, web_snips, local_snips)
    return JSONResponse({"ok": True, "answer_html": html_out, "sources": sources})


# -----------------------------
# Static
# -----------------------------
if Path(STATIC_DIR).exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# -----------------------------
# Avvio locale (facoltativo)
# -----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)


# ==============================================
# Quickstart di deployment (metti in README)
# ==============================================
# requirements.txt (minimi):
# fastapi==0.115.0
# uvicorn==0.30.6
# starlette==0.38.5
# requests==2.32.3
#
# Procfile (Render):
# web: uvicorn app:app --host 0.0.0.0 --port $PORT
#
# Struttura cartelle:
# .
# ├─ app.py
# ├─ requirements.txt
# ├─ Procfile
# └─ static/
#    ├─ sinapsi_rules.json   (opz.)
#    └─ data/
#       └─ TECNARIA.TXT05102025.txt
#
# Esempio .env (Render → Environment):
# STATIC_DIR=static
# DATA_DIR=static/data
# KB_FILE=static/data/TECNARIA.TXT05102025.txt
# SINAPSI_FILE=static/sinapsi_rules.json
# ALLOWED_DOMAINS_JSON=["tecnaria.com","spit.eu","spitpaslode.com"]
# MIN_WEB_SCORE=0.35
# MAX_WEB_RESULTS=5
# MODE=web_first_then_local
# BRAVE_API_KEY=***
#
# Avvio locale:
#   pip install -r requirements.txt
#   set STATIC_DIR=static & set DATA_DIR=static/data & set KB_FILE=static/data/TECNARIA.TXT05102025.txt & set MODE=local_only
#   python app.py
