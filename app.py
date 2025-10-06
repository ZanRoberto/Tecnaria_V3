# app.py — Tecnaria QA Bot (WEB → SINAPSI → fallback) con Sinapsi nascosta e risposta narrativa
# Requisiti: fastapi==0.115.0, uvicorn[standard]==0.30.6, gunicorn==21.2.0, requests==2.32.3, beautifulsoup4==4.12.3, jinja2==3.1.4

import os, re, json, time, html, unicodedata
from pathlib import Path
from typing import List, Dict, Any, Tuple

import requests
from fastapi import FastAPI, Body, Header, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

APP_TITLE = "Tecnaria – Assistente Tecnico"
app = FastAPI(title=APP_TITLE)

# ============================== CONFIG ==============================
STATIC_DIR = os.environ.get("STATIC_DIR", "static")
SINAPSI_FILE = os.environ.get("SINAPSI_FILE", os.path.join(STATIC_DIR, "data", "sinapsi_rules.json"))

BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
PREFERRED_DOMAINS = [d.strip() for d in os.environ.get(
    "PREFERRED_DOMAINS",
    "tecnaria.com,www.tecnaria.com"
).split(",") if d.strip()]

WEB_RESULTS_COUNT_PREFERRED = int(os.environ.get("WEB_RESULTS_COUNT_PREFERRED", "3"))
WEB_RESULTS_COUNT_FALLBACK  = int(os.environ.get("WEB_RESULTS_COUNT_FALLBACK",  "0"))  # 0 = nessuna open search extra
WEB_FRESHNESS_DAYS          = os.environ.get("WEB_FRESHNESS_DAYS", "365d")

# Come mostrare Sinapsi: "none" (default, nascosta), "blend" (fusa nel paragrafo), "inline" (lista – sconsigliato)
SINAPSI_DISPLAY = os.environ.get("SINAPSI_DISPLAY", "none").strip().lower()

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

# ============================== STATO ==============================
SINAPSI: Dict[str, Any] = {"rules": [], "exclude_any_q": [r"\bprezz\w*", r"\bcost\w*", r"\bpreventiv\w*", r"\boffert\w*"]}
SINAPSI_COMPILED: List[Dict[str, Any]] = []

# ============================== UTILS ==============================
def _safe_read(path: str) -> str:
    p = Path(path)
    return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^\w\s/.\-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _content_words(s: str) -> List[str]:
    stop = {"il","lo","la","i","gli","le","un","una","di","del","della","dei","degli","delle",
            "per","con","da","a","al","ai","agli","alla","alle","su","nel","nella","nelle",
            "non","è","e","o","che","quale","d","l","all","allo","agli"}
    toks = [t for t in re.split(r"[^\w]+", _norm(s)) if len(t) > 3 and t not in stop]
    return toks

def _signature(s: str) -> str:
    # firma semantica per dedup
    toks = _content_words(s)
    if not toks: return _norm(s)
    boost = {"p560","ctf","ctl","diapason","lamiera","grecata","hsbr14","patentino","legno","acciaio","m2","metro","quadrato","lineare"}
    toks = sorted(toks, key=lambda w: (w not in boost, w))[:8]
    return " ".join(toks)

def _dedup_semantic_with_prio(items: List[Tuple[str,int]]) -> List[str]:
    best: Dict[str, Tuple[str,int]] = {}
    for ans, pr in items:
        sig = _signature(ans)
        keep = best.get(sig)
        if keep is None or pr > keep[1] or (pr == keep[1] and len(ans) < len(keep[0])):
            best[sig] = (ans, pr)
    return [best[k][0] for k in best]

def _sanitize_brands(text: str) -> str:
    # niente marchi concorrenti
    return re.sub(r"\b(hilti|dx\b|bx\b)\b", "altri utensili non supportati", text, flags=re.I)

# ============================== SINAPSI ==============================
def _compile_sinapsi() -> None:
    global SINAPSI_COMPILED
    SINAPSI_COMPILED = []
    for r in (SINAPSI.get("rules") or []):
        patt = (r.get("pattern") or "").strip()
        ans  = (r.get("answer")  or "").strip()
        if not patt or not ans: 
            continue
        try:
            rx = re.compile(patt, re.I | re.S)
        except re.error:
            continue
        SINAPSI_COMPILED.append({
            "id": r.get("id"),
            "mode": (r.get("mode") or "augment").lower().strip(),
            "answer": ans,
            "rx": rx,
            "priority": int(r.get("priority", 0))
        })
    SINAPSI_COMPILED.sort(key=lambda x: x["priority"], reverse=True)

def _load_sinapsi() -> None:
    global SINAPSI
    raw = _safe_read(SINAPSI_FILE)
    if raw.strip():
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                SINAPSI = {"rules": data.get("rules", []) or [], "exclude_any_q": data.get("exclude_any_q", SINAPSI.get("exclude_any_q", []))}
            elif isinstance(data, list):
                SINAPSI = {"rules": data, "exclude_any_q": SINAPSI.get("exclude_any_q", [])}
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

if Path(STATIC_DIR).exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR, html=True), name="static")

def sinapsi_match_all(q: str) -> Tuple[List[str], List[str], List[str]]:
    qn = _norm(q)
    ovr_items: List[Tuple[str,int]] = []
    aug_items: List[Tuple[str,int]] = []
    psc_items: List[Tuple[str,int]] = []
    for r in SINAPSI_COMPILED:
        try:
            if r["rx"].search(qn):
                tup = (_sanitize_brands(r["answer"]), r["priority"])
                if   r["mode"] == "override":   ovr_items.append(tup)
                elif r["mode"] == "postscript": psc_items.append(tup)
                else:                           aug_items.append(tup)
        except Exception:
            continue
    ovr = _dedup_semantic_with_prio(ovr_items)
    aug = _dedup_semantic_with_prio(aug_items)
    psc = _dedup_semantic_with_prio(psc_items)
    return ovr, aug, psc

# ============================== WEB SEARCH (Brave) ==============================
def _brave(q: str, preferred: bool, site: str = "", count: int = 3) -> List[Dict[str, Any]]:
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    query = f"site:{site} {q}" if site else q
    try:
        r = requests.get(url, headers=headers, params={"q": query, "count": count, "freshness": WEB_FRESHNESS_DAYS}, timeout=12)
        if not r.ok:
            return []
        items = (r.json().get("web", {}) or {}).get("results", []) or []
    except Exception:
        return []
    out = []
    for it in items:
        out.append({
            "title": it.get("title") or (site or "Fonte"),
            "url": it.get("url") or "",
            "snippet": it.get("description") or "",
            "preferred": preferred
        })
    return out

def _filter_hits_by_query(q: str, hits: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    # Tieni i risultati che contengono parole-chiave della domanda in title/snippet/url
    qkw = set(_content_words(q))
    if not qkw:
        return hits
    def ok(h):
        blob = _norm(" ".join([h.get("title",""), h.get("snippet",""), h.get("url","")]))
        words = set(_content_words(blob))
        return bool(qkw & words)
    filtered = [h for h in hits if ok(h)]
    return filtered or hits

def get_web_hits(q: str) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        return []
    hits: List[Dict[str, Any]] = []
    for d in PREFERRED_DOMAINS:
        hits.extend(_brave(q, True, d, WEB_RESULTS_COUNT_PREFERRED))
    if not hits and WEB_RESULTS_COUNT_FALLBACK > 0:
        hits = _brave(q, False, "", WEB_RESULTS_COUNT_FALLBACK)
    return _filter_hits_by_query(q, hits)

# ============================== HTML COMPOSER ==============================
def _card(title: str, body_html: str, ms: int) -> str:
    return "<div class=\"card\"><h2>{}</h2>{}<p><small>⏱ {} ms</small></p></div>".format(
        html.escape(title), body_html, ms
    )

def _merge_sentence(lines: List[str], limit: int = 2) -> str:
    if not lines:
        return ""
    # prendi 1–2 frasi al massimo, corte
    pick = sorted(lines, key=len)[:max(1, min(limit, len(lines)))]
    s = " ".join(x.rstrip(". ") for x in pick)
    if not s.endswith("."):
        s += "."
    return s

def _compose_body(web_hits: List[Dict[str, Any]], sin_ovr: List[str], sin_aug: List[str], sin_psc: List[str]) -> str:
    parts: List[str] = []

    # Se esiste un override (es. risposte Sì/No), quello diventa il paragrafo principale
    if sin_ovr:
        main = _merge_sentence(sin_ovr, limit=1)
        parts.append("<p>{}</p>".format(html.escape(main)))
    elif web_hits:
        # Sintesi narrativa guidata dal WEB (niente titolo "Integrazioni")
        blended = ""
        if SINAPSI_DISPLAY in ("blend", "inline") and sin_aug:
            blended = _merge_sentence(sin_aug, limit=1)

        # Se c'è testo blended, mettilo in coda alla frase introduttiva
        intro = "Sintesi tecnica dalle fonti ufficiali."
        sentence = intro + (" " + blended if blended else "")
        parts.append("<p>{}</p>".format(html.escape(sentence)))
    else:
        # Fallback elegante se non c'è nulla
        generic = ("In generale, i sistemi Tecnaria si scelgono in base al supporto: "
                   "acciaio+lamiera → CTF (posa a sparo con P560 e chiodi HSBR14); "
                   "legno → CTL (viti dall’alto); "
                   "laterocemento senza lamiera → Diapason o V CEM-E/MINI. "
                   "La verifica finale resta a cura del progettista.")
        parts.append("<p>{}</p>".format(html.escape(generic)))

    # Nota: SINAPSI_DISPLAY=inline (non default) mostra anche la lista (se mai volessi riattivarla)
    if SINAPSI_DISPLAY == "inline" and (sin_ovr or sin_aug):
        lis = "".join("<li>{}</li>".format(html.escape(x)) for x in (sin_ovr + sin_aug))
        parts.append("<ul>{}</ul>".format(lis))

    # Postscript (mai invadente)
    if sin_psc and SINAPSI_DISPLAY != "none":
        parts.append("<p class='muted'>{}</p>".format(" ".join(html.escape(x) for x in sin_psc)))

    # Fonti in coda (filtrate)
    if web_hits:
        lis = []
        for h in web_hits:
            lab = "" if h.get("preferred") else " <em>(fonte non preferita)</em>"
            title = html.escape(h.get("title") or "Fonte")
            url   = html.escape(h.get("url") or "")
            snip  = h.get("snippet") or ""
            lis.append("<li><a href=\"{}\" target=\"_blank\" rel=\"noopener\">{}</a>{}{}{}</li>".format(
                url, title, lab,
                "<br><small>{}</small>".format(html.escape(snip)) if snip else "",
                ""
            ))
        parts.append("<h3>Fonti</h3>")
        parts.append("<ol class='list-decimal pl-5'>{}</ol>".format("".join(lis)))

    return "\n".join(parts)

# ============================== ENDPOINTS ==============================
@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "web_enabled": bool(BRAVE_API_KEY),
        "preferred_domains": PREFERRED_DOMAINS,
        "rules_loaded": len(SINAPSI.get("rules", [])),
        "exclude_any_q": SINAPSI.get("exclude_any_q", []),
        "sinapsi_file": SINAPSI_FILE,
        "sinapsi_display": SINAPSI_DISPLAY,
        "app": "web->sinapsi->fallback"
    })

@app.get("/", response_class=HTMLResponse)
def root():
    idx = Path(STATIC_DIR) / "index.html"
    return HTMLResponse(_safe_read(str(idx))) if idx.exists() else HTMLResponse(
        "<!doctype html><meta charset='utf-8'><title>{}</title><pre>POST /api/ask</pre>".format(html.escape(APP_TITLE))
    )

@app.post("/api/ask")
def api_ask(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    q = str(payload.get("q", "")).strip()
    if not q:
        return JSONResponse({"ok": True, "html": _card("Risposta Tecnaria", "<p>Manca la domanda.</p>", 0)})
    # blocco prezzi/preventivi
    for patt in SINAPSI.get("exclude_any_q", []):
        try:
            if re.search(patt, q, flags=re.I):
                return JSONResponse({"ok": True, "html": _card("Risposta Tecnaria", "<p>Richiesta non ammessa (prezzi/costi/preventivi).</p>", 0)})
        except re.error:
            continue

    t0 = time.perf_counter()
    web_hits = get_web_hits(q)                    # 1) WEB
    sin_ovr, sin_aug, sin_psc = sinapsi_match_all(q)  # 2) SINAPSI
    body_html = _compose_body(web_hits, sin_ovr, sin_aug, sin_psc)
    ms = int((time.perf_counter() - t0) * 1000)
    return JSONResponse({"ok": True, "html": _card("Risposta Tecnaria", body_html, ms)})

@app.post("/admin/reload")
def admin_reload(authorization: str = Header(None)) -> JSONResponse:
    if ADMIN_TOKEN:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        if authorization.split(" ", 1)[1].strip() != ADMIN_TOKEN:
            raise HTTPException(status_code=403, detail="Invalid token")
    _load_sinapsi()
    return JSONResponse({"ok": True, "rules_loaded": len(SINAPSI.get("rules", []))})
