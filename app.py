# app.py — Tecnaria QA Bot (WEB ➜ SINAPSI ➜ fallback)
# - Priorità: WEB (domini preferiti) -> integrazione SINAPSI (refine) -> fallback tecnico (mai "nessun contenuto")
# - Endpoint compatibile con la tua UI: POST /api/ask  → {"ok":true,"html":"<div class='card'>...</div>"}
# - Fonti cliccabili SOLO dal web (Sinapsi non è cliccabile)

import os
import re
import json
import time
import html
import unicodedata
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import requests
from fastapi import FastAPI, Body
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

APP_TITLE = "Tecnaria QA Bot"
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
WEB_FRESHNESS_DAYS = os.environ.get("WEB_FRESHNESS_DAYS", "365d")  # brave accepts e.g. 365d, month

# ================================================== STATO / CARICAMENTI ==================================================
SINAPSI: Dict[str, Any] = {"rules": [], "exclude_any_q": [r"\bprezz\w*", r"\bcost\w*", r"\bpreventiv\w*", r"\boffert\w*"]}
SINAPSI_COMPILED: List[Dict[str, Any]] = []  # regole con regex compilata

def _safe_read(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="ignore")

def _normalize_text(s: str) -> str:
    s = s or ""
    s = s.strip()
    # lower + remove diacritics
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    # compress spaces, keep / . - for patterns like TR-60 etc.
    s = re.sub(r"[^\w\s/.\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _compile_sinapsi():
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
        SINAPSI_COMPILED.append({
            "id": r.get("id"),
            "mode": mode,
            "answer": str(r.get("answer", "") or "").strip(),
            "rx": rx,
            "priority": prio
        })
    # Ordina per priorità discendente (prima quelle più importanti)
    SINAPSI_COMPILED.sort(key=lambda x: x["priority"], reverse=True)

def _load_sinapsi():
    global SINAPSI
    raw = _safe_read(SINAPSI_FILE)
    if not raw.strip():
        # resta default SINAPSI
        _compile_sinapsi()
        return
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            SINAPSI = {
                "rules": data.get("rules", []) or [],
                "exclude_any_q": data.get("exclude_any_q", SINAPSI["exclude_any_q"])
            }
        elif isinstance(data, list):
            SINAPSI = {"rules": data, "exclude_any_q": SINAPSI["exclude_any_q"]}
        else:
            # fallback a default
            SINAPSI = {"rules": [], "exclude_any_q": SINAPSI["exclude_any_q"]}
    except Exception:
        SINAPSI = {"rules": [], "exclude_any_q": SINAPSI["exclude_any_q"]}
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

# ================================================== SINAPSI MATCH (REFINE) ==================================================
def sinapsi_match_all(q: str) -> Tuple[List[str], List[str], List[str]]:
    """
    Ritorna (override_list, augment_list, postscript_list) per la query normalizzata.
    In presenza di WEB, 'override' viene comunque usato come refine (non sostituisce le fonti).
    """
    qn = _normalize_text(q)
    ovr: List[str] = []
    aug: List[str] = []
    psc: List[str] = []
    for r in SINAPSI_COMPILED:
        try:
            if r["rx"].search(qn):
                ans = r.get("answer", "")
                if not ans:
                    continue
                m = r["mode"]
                if m == "override":
                    ovr.append(ans)
                elif m == "postscript":
                    psc.append(ans)
                else:
                    aug.append(ans)
        except Exception:
            continue
    return ovr, aug, psc

# ================================================== WEB SEARCH (BRAVE) ==================================================
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
            "preferred": False  # non preferita
        })
    return results

def get_web_hits(q: str) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        return []
    # Tenta prima i domini preferiti
    hits: List[Dict[str, Any]] = []
    for d in PREFERRED_DOMAINS:
        try:
            hits.extend(brave_search_site(q, d, WEB_RESULTS_COUNT_PREFERRED))
        except Exception:
            continue
    # Se vuoto, allarga
    if not hits:
        try:
            hits = brave_search_open(q, WEB_RESULTS_COUNT_FALLBACK)
        except Exception:
            hits = []
    return hits[: (WEB_RESULTS_COUNT_PREFERRED + WEB_RESULTS_COUNT_FALLBACK)]

# ================================================== HTML COMPOSER ==================================================
def _card(title: str, body_html: str, ms: int) -> str:
    # Stile essenziale, compatibile con la tua UI
    return f"""<div class="card">
      <h2>{html.escape(title)}</h2>
      {body_html}
      <p><small>⏱ {ms} ms</small></p>
    </div>"""

def _compose_web_first(q: str, web_hits: List[Dict[str, Any]], sin_ovr: List[str], sin_aug: List[str], sin_psc: List[str]) -> str:
    parts: List[str] = []
    # WEB presente → testo tecnico + refine
    if web_hits:
        parts.append("<p>Di seguito una sintesi tecnico-commerciale ricavata da fonti ufficiali; dove utile ho integrato indicazioni interne.</p>")
        # Refine: in presenza di web, override viene trattato come integrazione
        refines = []
        if sin_ovr:
            refines.extend(sin_ovr)
        if sin_aug:
            refines.extend(sin_aug)
        if refines:
            parts.append("<h3>Integrazioni (Sinapsi)</h3>")
            parts.append("<ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in refines) + "</ul>")
        if sin_psc:
            parts.append("<p class='muted'>" + " ".join(html.escape(x) for x in sin_psc) + "</p>")

        # Fonti cliccabili
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

    # Niente web → usa Sinapsi per non bucare mai
    if sin_ovr or sin_aug or sin_psc:
        if sin_ovr:
            parts.append("<ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in sin_ovr) + "</ul>")
        if sin_aug:
            parts.append("<ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in sin_aug) + "</ul>")
        if sin_psc:
            parts.append("<p class='muted'>" + " ".join(html.escape(x) for x in sin_psc) + "</p>")
        parts.append("<p><small>Nota: in assenza di fonti web attendibili ho riportato le indicazioni interne pertinenti.</small></p>")
        return "\n".join(parts)

    # Fallback elegante (MAI "nessun contenuto")
    generic = (
        "Non ho trovato fonti web attendibili sull’argomento specifico. "
        "In generale, i sistemi Tecnaria si scelgono in base al supporto: "
        "acciaio+lamiera → CTF (con posa a sparo, chiodi HSBR14, P560); "
        "legno → CTL (viti dall’alto); "
        "laterocemento senza lamiera → Diapason o V CEM-E/MINI. "
        "Per un dimensionamento corretto servono luci, carichi e profilo lamiera/cassero. "
        "La verifica finale resta a cura del progettista."
    )
    return f"<p>{html.escape(generic)}</p>"

# ================================================== ENDPOINTS ==================================================
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
def home():
    # Pagina minimale per test manuale
    inner = f"""
    <div class="card">
      <h2>{html.escape(APP_TITLE)}</h2>
      <p><small>WEB filtrato → Sinapsi (refine) → fallback</small></p>
      <form id="f" onsubmit="send();return false;">
        <input id="q" type="text" placeholder="Scrivi una domanda (es. Mi parli della P560?)" style="width:100%;padding:8px;margin:6px 0;">
        <button>Chiedi</button>
      </form>
      <div id="out" style="margin-top:12px;"></div>
    </div>
    <script>
      async function send(){
        const q = document.getElementById('q').value;
        const r = await fetch('/api/ask', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{q}})}});
        const j = await r.json();
        document.getElementById('out').innerHTML = j.html || '<p>Errore</p>';
      }
    </script>
    """
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(APP_TITLE)}</title>
    <style>body{{font-family:ui-sans-serif,system-ui;background:#fafafa;margin:24px;color:#111}}
    .card{{background:#fff;border:1px solid #eee;border-radius:16px;padding:20px;box-shadow:0 6px 18px rgba(0,0,0,.06)}}
    h2{{margin:0 0 8px 0}}</style></head><body>{inner}</body></html>"""
    return HTMLResponse(page)

@app.post("/ask")
def ask(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    # Alias di comodo (usato solo se qualcuno chiama /ask)
    return api_ask(payload)

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
    # 1) WEB FIRST
    web_hits = get_web_hits(q)
    # 2) SINAPSI refine
    sin_ovr, sin_aug, sin_psc = sinapsi_match_all(q)
    body_html = _compose_web_first(q, web_hits, sin_ovr, sin_aug, sin_psc)
    ms = int((time.perf_counter() - t0) * 1000)

    card = _card("Risposta Tecnaria", body_html, ms)
    return JSONResponse({"ok": True, "html": card})
