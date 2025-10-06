# app.py — Tecnaria QA Bot (WEB → SINAPSI → fallback)
# - POST /api/ask  → {"ok": true, "html": "<div class='card'>...</div>"}
# - GET  /health   → stato
# - POST /admin/reload (Bearer ADMIN_TOKEN opzionale) → ricarica il file di sinapsi senza redeploy
# Nessun KB locale. Compatibile con i tuoi requirements.

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
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

APP_TITLE = "Tecnaria – Assistente Tecnico"
app = FastAPI(title=APP_TITLE)

# ============================== CONFIG ==============================
STATIC_DIR = os.environ.get("STATIC_DIR", "static")
SINAPSI_FILE = os.environ.get("SINAPSI_FILE", os.path.join(STATIC_DIR, "data", "sinapsi_rules.json"))

# WEB first
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")  # se vuota → web disabilitato
PREFERRED_DOMAINS = [
    d.strip() for d in os.environ.get(
        "PREFERRED_DOMAINS",
        "tecnaria.com,www.tecnaria.com,spit.eu,spitpaslode.com"
    ).split(",") if d.strip()
]

WEB_RESULTS_COUNT_PREFERRED = int(os.environ.get("WEB_RESULTS_COUNT_PREFERRED", "5"))
WEB_RESULTS_COUNT_FALLBACK  = int(os.environ.get("WEB_RESULTS_COUNT_FALLBACK",  "3"))
WEB_FRESHNESS_DAYS          = os.environ.get("WEB_FRESHNESS_DAYS", "365d")  # es. 365d, month

# Admin
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")  # per /admin/reload (opzionale)

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
    s = s.strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^\w\s/.\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _compile_sinapsi() -> None:
    """Compila regex e ordina per priorità desc."""
    global SINAPSI_COMPILED
    SINAPSI_COMPILED = []
    rules = SINAPSI.get("rules", []) or []
    for r in rules:
        patt = str(r.get("pattern", "") or "").strip()
        ans  = str(r.get("answer", "")  or "").strip()
        if not patt or not ans:
            continue
        try:
            rx = re.compile(patt, re.I | re.S)
        except re.error:
            continue
        mode = str(r.get("mode", "augment")).lower().strip()
        prio = int(r.get("priority", 0))
        SINAPSI_COMPILED.append({"id": r.get("id"), "mode": mode, "answer": ans, "rx": rx, "priority": prio})
    SINAPSI_COMPILED.sort(key=lambda x: x["priority"], reverse=True)

def _load_sinapsi() -> None:
    """Carica e compila dal file configurato."""
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
def _startup() -> None:
    os.makedirs(STATIC_DIR, exist_ok=True)
    _load_sinapsi()

# Static (serve anche la tua index.html se presente)
if Path(STATIC_DIR).exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR, html=True), name="static")

# ============================== SINAPSI ENGINE ==============================
def sinapsi_match_all(q: str) -> Tuple[List[str], List[str], List[str]]:
    """Ritorna (override, augment, postscript) per la query normalizzata."""
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
    try:
        r = requests.get(url, headers=headers, params=params, timeout=12)
        if not r.ok:
            return []
        data = r.json()
    except Exception:
        return []
    items = (data.get("web", {}) or {}).get("results", []) or []
    out: List[Dict[str, Any]] = []
    for it in items:
        out.append({
            "title": it.get("title") or site_domain,
            "url": it.get("url") or "",
            "snippet": it.get("description") or "",
            "preferred": True
        })
    return out

def brave_search_open(q: str, count: int) -> List[Dict[str, Any]]:
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    params = {"q": q, "count": count, "freshness": WEB_FRESHNESS_DAYS}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=12)
        if not r.ok:
            return []
        data = r.json()
    except Exception:
        return []
    items = (data.get("web", {}) or {}).get("results", []) or []
    out: List[Dict[str, Any]] = []
    for it in items:
        out.append({
            "title": it.get("title") or "Fonte",
            "url": it.get("url") or "",
            "snippet": it.get("description") or "",
            "preferred": False
        })
    return out

def get_web_hits(q: str) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        return []
    hits: List[Dict[str, Any]] = []
    for d in PREFERRED_DOMAINS:
        site_hits = brave_search_site(q, d, WEB_RESULTS_COUNT_PREFERRED)
        if site_hits:
            hits.extend(site_hits)
    if not hits:
        hits = brave_search_open(q, WEB_RESULTS_COUNT_FALLBACK)
    limit = WEB_RESULTS_COUNT_PREFERRED + WEB_RESULTS_COUNT_FALLBACK
    return hits[:limit]

# ============================== HTML COMPOSER ==============================
def _card(title: str, body_html: str, ms: int) -> str:
    # niente f-string per evitare conflitti con graffe di contenuti
    return "<div class=\"card\"><h2>{}</h2>{}<p><small>⏱ {} ms</small></p></div>".format(
        html.escape(title), body_html, ms
    )

def _compose_body(web_hits: List[Dict[str, Any]], sin_ovr: List[str], sin_aug: List[str], sin_psc: List[str]) -> str:
    parts: List[str] = []
    if web_hits:
        parts.append("<p>Di seguito una sintesi tecnico-commerciale ricavata da fonti ufficiali; dove utile ho integrato indicazioni interne.</p>")
        refines: List[str] = []
        if sin_ovr:
            refines.extend(sin_ovr)
        if sin_aug:
            refines.extend(sin_aug)
        if refines:
            parts.append("<h3>Integrazioni (Sinapsi)</h3>")
            li = "".join("<li>{}</li>".format(html.escape(x)) for x in refines)
            parts.append("<ul>{}</ul>".format(li))
        if sin_psc:
            parts.append("<p class='muted'>{}</p>".format(" ".join(html.escape(x) for x in sin_psc)))
        # Fonti
        parts.append("<h3>Fonti</h3>")
        lis: List[str] = []
        for h in web_hits:
            lab = "" if h.get("preferred") else " <em>(fonte non preferita)</em>"
            title = html.escape(h.get("title") or "Fonte")
            url   = html.escape(h.get("url") or "")
            snip  = h.get("snippet") or ""
            li = "<li><a href=\"{}\" target=\"_blank\" rel=\"noopener\">{}</a>{}{}{}</li>".format(
                url, title, lab,
                "<br><small>{}</small>".format(html.escape(snip)) if snip else "",
                ""
            )
            lis.append(li)
        parts.append("<ol class='list-decimal pl-5'>{}</ol>".format("".join(lis)))
        return "\n".join(parts)

    # Niente web → Sinapsi
    if sin_ovr or sin_aug or sin_psc:
        if sin_ovr:
            parts.append("<ul>{}</ul>".format("".join("<li>{}</li>".format(html.escape(x)) for x in sin_ovr)))
        if sin_aug:
            parts.append("<ul>{}</ul>".format("".join("<li>{}</li>".format(html.escape(x)) for x in sin_aug)))
        if sin_psc:
            parts.append("<p class='muted'>{}</p>".format(" ".join(html.escape(x) for x in sin_psc)))
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
    return "<p>{}</p>".format(html.escape(generic))

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

@app.get("/", response_class=HTMLResponse)
def root():
    """Se esiste static/index.html (la tua UI), lo servo tale e quale; altrimenti placeholder minimale."""
    idx = Path(STATIC_DIR) / "index.html"
    if idx.exists():
        return HTMLResponse(_safe_read(str(idx)))
    return HTMLResponse("<!doctype html><meta charset='utf-8'><title>{}</title><pre>POST /api/ask</pre>".format(
        html.escape(APP_TITLE)
    ))

@app.post("/api/ask")
def api_ask(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    q = str(payload.get("q", "")).strip()
    if not q:
        return JSONResponse({"ok": True, "html": _card("Risposta Tecnaria", "<p>Manca la domanda.</p>", 0)})

    if _blocked_by_rules(q):
        return JSONResponse({"ok": True, "html": _card("Risposta Tecnaria", "<p>Richiesta non ammessa (prezzi/costi/preventivi).</p>", 0)})

    t0 = time.perf_counter()
    web_hits = get_web_hits(q)                 # 1) WEB (preferito)
    ovr, aug, psc = sinapsi_match_all(q)       # 2) SINAPSI (refine / fallback)
    body_html = _compose_body(web_hits, ovr, aug, psc)
    ms = int((time.perf_counter() - t0) * 1000)
    return JSONResponse({"ok": True, "html": _card("Risposta Tecnaria", body_html, ms)})

@app.post("/admin/reload")
def admin_reload(authorization: str = Header(None)) -> JSONResponse:
    """Ricarica sinapsi_rules.json senza redeploy. Se ADMIN_TOKEN non è impostato, è aperto."""
    if ADMIN_TOKEN:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        token = authorization.split(" ", 1)[1].strip()
        if token != ADMIN_TOKEN:
            raise HTTPException(status_code=403, detail="Invalid token")
    _load_sinapsi()
    return JSONResponse({"ok": True, "rules_loaded": len(SINAPSI.get("rules", []))})
