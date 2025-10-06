# app.py — Tecnaria QA Bot (WEB ➜ SINAPSI ➜ fallback) — no KB, no JS inline
# - POST /api/ask  → {"ok": true, "html": "<div class='card'>…</div>"}
# - GET  /health   → stato
# - POST /admin/reload (Bearer ADMIN_TOKEN) → ricarica sinapsi_rules.json senza redeploy
#
# Requirements compatibili con quelli che hai scelto:
# fastapi==0.115.0, uvicorn[standard]==0.30.6, gunicorn==21.2.0, requests==2.32.3, beautifulsoup4==4.12.3, jinja2==3.1.4

import os
import re
import json
import time
import html
import unicodedata
from pathlib import Path
from typing import List, Dict, Any, Tuple

import requests
from fastapi import FastAPI, Body, Header, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

APP_TITLE = "Tecnaria – Assistente Tecnico"
app = FastAPI(title=APP_TITLE)

# ============================== CONFIG ==============================
STATIC_DIR = os.environ.get("STATIC_DIR", "static")
SINAPSI_FILE = os.environ.get("SINAPSI_FILE", os.path.join(STATIC_DIR, "data", "sinapsi_rules.json"))

# WEB first
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")  # se vuota, web disabilitato
PREFERRED_DOMAINS = [d.strip() for d in os.environ.get(
    "PREFERRED_DOMAINS",
    "tecnaria.com,www.tecnaria.com,spit.eu,spitpaslode.com"
).split(",") if d.strip()]

WEB_RESULTS_COUNT_PREFERRED = int(os.environ.get("WEB_RESULTS_COUNT_PREFERRED", "5"))
WEB_RESULTS_COUNT_FALLBACK = int(os.environ.get("WEB_RESULTS_COUNT_FALLBACK", "3"))
WEB_FRESHNESS_DAYS = os.environ.get("WEB_FRESHNESS_DAYS", "365d")  # 365d, month, etc.

# Admin
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")  # opzionale per /admin/reload

# ============================== STATO ==============================
SINAPSI: Dict[str, Any] = {"rules": [], "exclude_any_q": [r"\bprezz\w*", r"\bcost\w*", r"\bpreventiv\w*", r"\boffert\w*"]}
SINAPSI_COMPILED: List[Dict[str, Any]] = []

# ============================== UTILS ==============================
def _safe_read(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="ignore")

def _normalize_text(s: str) -> str:
    s = s or ""
    s = s.strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^\w\s/.\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _compile_sinapsi():
    """Compila le regex e ordina per priorità discendente."""
    global SINAPSI_COMPILED
    SINAPSI_COMPILED = []
    rules = SINAPSI.get("rules", []) or []
    for r in rules:
        patt = str(r.get("pattern", "") or "").strip()
        if not patt:
            continue
        try:
            rx = re.compile(patt, re.I | re.S)
        except re.error:
            continue
        mode = str(r.get("mode", "augment")).lower().strip()
        prio = r.get("priority", 0)
        ans  = str(r.get("answer", "") or "").strip()
        if not ans:
            continue
        SINAPSI_COMPILED.append({
            "id": r.get("id"),
            "mode": mode,
            "answer": ans,
            "rx": rx,
            "priority": prio
        })
    SINAPSI_COMPILED.sort(key=lambda x: x["priority"], reverse=True)

def _load_sinapsi():
    """Carica/ricompila sinapsi dal file configurato."""
    global SINAPSI
    raw = _safe_read(SINAPSI_FILE)
    if not raw.strip():
        _compile_sinapsi()
        return
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            SINAPSI = {
                "rules": data.get("rules", []) or [],
                "exclude_any_q": data.get("exclude_any_q", SINAPSI.get("exclude_any_q", []))
            }
        elif isinstance(data, list):
            SINAPSI = {"rules": data, "exclude_any_q": SINAPSI.get("exclude_any_q", [])}
        else:
            SINAPSI = {"rules": [], "exclude_any_q": SINAPSI.get("exclude_any_q", [])}
    except Exception:
        SINAPSI = {"rules": [], "exclude_any_q": SINAPSI.get("exclude_any_q", [])}
    _compile_sinapsi()

def _blocked_by_rules(q: str) -> bool:
    for patt in SINAPSI.get("exclude_any_q", []):
        try:
            if re.search(patt, q, flags=re.I):
                return True
        except re.error:
            continue
    return False

@app.on_event("startup")
def _startup():
    os.makedirs(STATIC_DIR, exist_ok=True)
    _load_sinapsi()

if Path(STATIC_DIR).exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ============================== SINAPSI ENGINE ==============================
def sinapsi_match_all(q: str) -> Tuple[List[str], List[str], List[str]]:
    """
    Ritorna (override_list, augment_list, postscript_list) per q normalizzata.
    Con WEB presente, anche 'override' viene usato come integrazione (non sostituisce le fonti).
    """
    qn = _normalize_text(q)
    ovr: List[str] = []
    aug: List[str] = []
    psc: List[str] = []
    for r in SINAPSI_COMPILED:
        try:
            if r["rx"].search(qn):
                m = r["mode"]
                if m == "override":
                    ovr.append(r["answer"])
                elif m == "postscript":
                    psc.append(r["answer"])
                else:
                    aug.append(r["answer"])
        except Exception:
            continue
    return ovr, aug, psc

# ============================== WEB SEARCH (Brave) ==============================
def brave_search_site(q: str, site_domain: str, count: int) -> List[Dict[str, Any]]:
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    params = {"q": f"site:{site_domain} {q}", "count": count, "freshness": WEB_FRESHNESS_DAYS}
    r = requests.get(url, headers=headers, params=params, timeout=12)
    if not r.ok:
        return []
    items = (r.json().get("web", {}) or {}).get("results", []) or []
    results = []
    for it in items:
        results.append({
            "title": it.get("title") or site_domain,
            "url": it.get("url") or "",
            "snippet": it.get("description") or "",
            "preferred": True
        })
    return results

def brave_search_open(q: str, count: int) -> List[Dict[str, Any]]:
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    params = {"q": q, "count": count, "freshness": WEB_FRESHNESS_DAYS}
    r = requests.get(url, headers=headers, params=params, timeout=12)
    if not r.ok:
        return []
    items = (r.json().get("web", {}) or {}).get("results", []) or []
    results = []
    for it in items:
        results.append({
            "title": it.get("title") or "Fonte",
            "url": it.get("url") or "",
            "snippet": it.get("description") or "",
            "preferred": False
        })
    return results

def get_web_hits(q: str) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        return []
    hits: List[Dict[str, Any]] = []
    # domini preferiti
    for d in PREFERRED_DOMAINS:
        try:
            hits.extend(brave_search_site(q, d, WEB_RESULTS_COUNT_PREFERRED))
        except Exception:
            continue
    # allarga se vuoto
    if not hits:
        try:
            hits = brave_search_open(q, WEB_RESULTS_COUNT_FALLBACK)
        except Exception:
            hits = []
    # limita
    return hits[: (WEB_RESULTS_COUNT_PREFERRED + WEB_RESULTS_COUNT_FALLBACK)]

# ============================== HTML COMPOSER ==============================
def _card(title: str, body_html: str, ms: int) -> str:
    return f"""<div class="card">
  <h2>{html.escape(title)}</h2>
  {body_html}
  <p><small>⏱ {ms} ms</small></p>
</div>"""

def _compose_body(web_hits: List[Dict[str, Any]], sin_ovr: List[str], sin_aug: List[str], sin_psc: List[str]) -> str:
    parts: List[str] = []
    if web_hits:
        # Web → narrativa + refine
        parts.append("<p>Di seguito una sintesi tecnico-commerciale ricavata da fonti ufficiali; dove utile ho integrato indicazioni interne.</p>")
        refines = []
        if sin_ovr: refines.extend(sin_ovr)
        if sin_aug: refines.extend(sin_aug)
        if refines:
            parts.append("<h3>Integrazioni (Sinapsi)</h3>")
            parts.append("<ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in refines) + "</ul>")
        if sin_psc:
            parts.append("<p class='muted'>" + " ".join(html.escape(x) for x in sin_psc) + "</p>")
        # fonti
        parts.append("<h3>Fonti</h3>")
        lis = []
        for h in web_hits:
            lab = "" if h.get("preferred") else " <em>(fonte non preferita)</em>"
            lis.append(
                f"<li><a href=\"{html.escape(h['url'])}\" target=\"_blank\" rel=\"noopener\">{html.escape(h['title'])}</a>{lab}"
                + (f"<br><small>{html.escape(h['snippet'])}</small>" if h.get("snippet") else "")
                + "</li>"
            )
        parts.append("<ol class='list-decimal pl-5'>" + "".join(lis) + "</ol>")
        return "\n".join(parts)

    # Niente web → Sinapsi
    if sin_ovr or sin_aug or sin_psc:
        if sin_ovr:
            parts.append("<ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in sin_ovr) + "</ul>")
        if sin_aug:
            parts.append("<ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in sin_aug) + "</ul>")
        if sin_psc:
            parts.append("<p class='muted'>" + " ".join(html.escape(x) for x in sin_psc) + "</p>")
        parts.append("<p><small>Nota: in assenza di fonti web attendibili ho riportato le indicazioni interne pertinenti.</small></p>")
        return "\n".join(parts)

    # Fallback elegante (mai "nessun contenuto")
    generic = (
        "Non ho trovato fonti web attendibili sull’argomento specifico. "
        "In generale, i sistemi Tecnaria si scelgono in base al supporto: "
        "acciaio+lamiera → CTF (posa a sparo con P560 e chiodi HSBR14); "
        "legno → CTL (viti dall’alto); "
        "laterocemento senza lamiera → Diapason o V CEM-E/MINI. "
        "Per il dimensionamento servono luci, carichi e profilo lamiera/cassero. "
        "La verifica finale resta a cura del progettista."
    )
    return f"<p>{html.escape(generic)}</p>"

# ============================== ENDPOINTS ==============================
@app.get("/health")
def health() -> JSONResponse:
    info = {
        "status": "ok",
        "web_enabled": bool(BRAVE_API_KEY),
        "preferred_domains": PREFERRED_DOMAINS,
        "rules_loaded": len(SINAPSI.get("rules", [])),
        "exclude_any_q": SINAPSI.get("exclude_any_q", []),
        "sinapsi_file": SINAPSI_FILE,
        "app": "web->sinapsi->fallback",
    }
    return JSONResponse(info)

@app.get("/", response_class=PlainTextResponse)
def root():
    # Nessun HTML complesso → niente rischi di SyntaxError da graffe
    return PlainTextResponse(f"{APP_TITLE} — POST /api/ask")

@app.post("/api/ask")
def api_ask(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    q = str(payload.get("q", "")).strip()
    if not q:
        card = _card("Risposta Tecnaria", "<p>Manca la domanda.</p>", 0)
        return JSONResponse({"ok": True, "html": card})

    if _blocked_by_rules(q):
        card = _card("Risposta Tecnaria", "<p>Richiesta non ammessa (prezzi/costi/preventivi).</p>", 0)
        return JSONResponse({"ok": True, "html": card})

    t0 = time.perf_counter()
    web_hits = get_web_hits(q)            # 1) WEB (preferito)
    sin_ovr, sin_aug, sin_psc = sinapsi_match_all(q)   # 2) SINAPSI (refine / fallback)
    body_html = _compose_body(web_hits, sin_ovr, sin_aug, sin_psc)
    ms = int((time.perf_counter() - t0) * 1000)
    card = _card("Risposta Tecnaria", body_html, ms)
    return JSONResponse({"ok": True, "html": card})

@app.post("/admin/reload")
def admin_reload(authorization: str = Header(None)) -> JSONResponse:
    if ADMIN_TOKEN:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        token = authorization.split(" ", 1)[1].strip()
        if token != ADMIN_TOKEN:
            raise HTTPException(status_code=403, detail="Invalid token")
    _load_sinapsi()
    return JSONResponse({"ok": True, "rules_loaded": len(SINAPSI.get("rules", []))})
