# app.py — Tecnaria QA Bot (WEB → SINAPSI → fallback)
# - Corpo risposta: 2–4 frasi dagli snippet IT + 1 frase Sinapsi fusa
# - Per domande di confronto: frase "CTF vs Diapason" dedicata all'inizio
# - Fonti: solo in fondo, pannello collassabile (Chiudi fonti), IT prima
# Requisiti: fastapi==0.115.0, uvicorn[standard]==0.30.6, gunicorn==21.2.0,
#            requests==2.32.3, beautifulsoup4==4.12.3, jinja2==3.1.4

import os, re, json, time, html, unicodedata
from pathlib import Path
from typing import List, Dict, Any, Tuple

import requests
from bs4 import BeautifulSoup
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
WEB_RESULTS_COUNT_FALLBACK  = int(os.environ.get("WEB_RESULTS_COUNT_FALLBACK",  "0"))
WEB_FRESHNESS_DAYS          = os.environ.get("WEB_FRESHNESS_DAYS", "365d")
LANG_PREFERRED              = os.environ.get("LANG_PREFERRED", "it").strip().lower()
DISAMBIG_STRICT             = (os.environ.get("DISAMBIG_STRICT", "true").strip().lower() in ("1","true","yes"))

# Sinapsi fusa (max 1 frase)
SINAPSI_FUSE = True
NARRATIVE_MIN = 2
NARRATIVE_MAX = 4

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
    toks = _content_words(s)
    if not toks: return _norm(s)
    boost = {"p560","ctf","ctl","diapason","lamiera","grecata","hsbr14","legno","acciaio","calcestruzzo","solaio","tecnaria"}
    toks = sorted(toks, key=lambda w: (w not in boost, w))[:8]
    return " ".join(toks)

def _dedup_semantic(items: List[Tuple[str,int]]) -> List[str]:
    best: Dict[str, Tuple[str,int]] = {}
    for ans, pr in items:
        sig = _signature(ans)
        keep = best.get(sig)
        if keep is None or pr > keep[1] or (pr == keep[1] and len(ans) < len(keep[0])):
            best[sig] = (ans, pr)
    return [best[k][0] for k in best]

def _strip_html(s: str) -> str:
    if not s: return ""
    try:
        return BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    except Exception:
        return re.sub(r"<[^>]+>", " ", s)

def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+|[\n\r]+|;\s+", text or "")
    out = []
    for p in parts:
        s = _strip_html(p.strip())
        if 8 <= len(s) <= 240:
            out.append(s)
    return out

def _sanitize_brands(text: str) -> str:
    # niente nomi di utensili concorrenti nelle frasi Sinapsi
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
    ovr = _dedup_semantic(ovr_items)
    aug = _dedup_semantic(aug_items)
    psc = _dedup_semantic(psc_items)
    return ovr, aug, psc

# ============================== WEB (Brave) ==============================
def _build_query(q: str) -> str:
    if not DISAMBIG_STRICT:
        return q
    qn = _norm(q)
    plus = []
    minus = []
    plus.append('"Tecnaria S.p.A." OR Tecnaria')
    plus.append('"Bassano del Grappa"')
    plus.append('connettori OR connettore OR "solai misti" OR "acciaio calcestruzzo" OR lamiera')
    if "ctf" in qn:
        minus += ["chimica", "farmacia", "farmaceutic*"]
        plus.append("CTF connettori")
    if "diapason" in qn:
        minus += ["musica", "strumento", "accordare", "tuning fork"]
        plus.append("Diapason connettori")
    if "p560" in qn or "spit" in qn:
        plus.append('"SPIT P560" connettori CTF Tecnaria')
    add = ""
    if plus:  add += " " + " ".join(plus)
    if minus: add += " " + " ".join(f"-{m}" for m in minus)
    return f"{q}{add}".strip()

def _brave(q: str, preferred: bool, site: str = "", count: int = 3) -> List[Dict[str, Any]]:
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    q_built = _build_query(q)
    query = f"site:{site} {q_built}" if site else q_built
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
            "title": _strip_html(it.get("title") or (site or "Fonte")),
            "url": it.get("url") or "",
            "snippet": _strip_html(it.get("description") or ""),
            "preferred": preferred,
            "language": (it.get("language") or "").lower()
        })
    return out

def _is_it_url(url: str) -> bool:
    u = (url or "").lower()
    if "/it/" in u: return True
    if "/en/" in u: return False
    if "tecnaria.com" in u and "/en" not in u:
        return True
    return False

def _rank_hits_lang(q: str, hits: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    qkw = set(_content_words(q))
    def score(h):
        url = h.get("url",""); title = h.get("title",""); snip = h.get("snippet","")
        blob = _norm(" ".join([title, snip, url]))
        qscore = len(qkw & set(_content_words(blob)))
        site_bonus = 3 if "tecnaria.com" in url else (1 if "spit" in url else 0)
        lang = (h.get("language") or "").lower()
        it_bonus = 3 if lang == "it" or _is_it_url(url) else (-3 if "/en/" in url or lang == "en" else 0)
        return (qscore, site_bonus, it_bonus, -len(title))
    return sorted(hits, key=score, reverse=True)

def _filter_hits_by_query(q: str, hits: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    qkw = set(_content_words(q))
    if not qkw:
        return hits
    def ok(h):
        blob = _norm(" ".join([h.get("title",""), h.get("snippet",""), h.get("url","")]))
        words = set(_content_words(blob))
        return bool(qkw & words)
    filtered = [h for h in hits if ok(h)]
    return filtered or hits

def _split_by_lang(hits: List[Dict[str,Any]]) -> Tuple[List[Dict[str,Any]], List[Dict[str,Any]]]:
    it_hits, other = [], []
    for h in hits:
        lang = (h.get("language") or "").lower()
        if lang == LANG_PREFERRED or _is_it_url(h.get("url","")):
            it_hits.append(h)
        else:
            other.append(h)
    return it_hits, other

def get_web_hits(q: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not BRAVE_API_KEY:
        return [], []
    hits: List[Dict[str, Any]] = []
    for d in PREFERRED_DOMAINS:
        hits.extend(_brave(q, True, d, WEB_RESULTS_COUNT_PREFERRED))
    if not hits and WEB_RESULTS_COUNT_FALLBACK > 0:
        hits = _brave(q, False, "", WEB_RESULTS_COUNT_FALLBACK)
    hits = _filter_hits_by_query(q, hits)
    hits = _rank_hits_lang(q, hits)
    it_hits, other = _split_by_lang(hits)
    return it_hits, other

# ============================== NARRATIVA ==============================
def _web_summary_from_snippets(hits: List[Dict[str,Any]], need: int) -> List[str]:
    """Raccoglie frasi dagli snippet (priorità IT). Se poche, prende le prime del migliore snippet."""
    lines: List[str] = []
    for h in hits[:3]:
        snip = h.get("snippet","") or ""
        for s in _sentences(snip):
            if s and s not in lines:
                lines.append(s)
            if len(lines) >= need:
                break
        if len(lines) >= need:
            break
    if len(lines) < need and hits:
        # prendi comunque prime frasi del miglior snippet disponibile
        sents = _sentences(hits[0].get("snippet","") or "")
        for s in sents:
            if s and s not in lines:
                lines.append(s)
            if len(lines) >= need:
                break
    return lines[:need]

def _build_diff_line(q: str) -> str:
    qn = _norm(q)
    if ("differenz" in qn) or (" vs " in f" {qn} ") or ("confront" in qn):
        return ("CTF: connettore a piolo su piastra, per solai misti acciaio-calcestruzzo "
                "su travi in acciaio con lamiera grecata (posa a sparo con SPIT P560). "
                "Diapason: connettore a staffa, indicato per travi senza lamiera e per "
                "sollecitazioni più elevate, fissato sempre a freddo con chiodi.")
    return ""

# ============================== HTML ==============================
def _card(title: str, body_html: str, ms: int) -> str:
    return "<div class=\"card\"><h2>{}</h2>{}<p><small>⏱ {} ms</small></p></div>".format(
        html.escape(title), body_html, ms
    )

def _merge_sentence(lines: List[str], limit: int = 1) -> str:
    if not lines:
        return ""
    pick = sorted(lines, key=len)[:max(1, min(limit, len(lines)))]
    s = " ".join(x.rstrip(". ") for x in pick)
    if not s.endswith("."):
        s += "."
    return s

def _compose_body(hits_it: List[Dict[str, Any]], hits_other: List[Dict[str, Any]],
                  sin_ovr: List[str], sin_aug: List[str], sin_psc: List[str], q: str) -> str:
    parts: List[str] = []

    # 1) Frase di confronto (se pertinente) o override Sinapsi
    diff = _build_diff_line(q)
    if sin_ovr:
        diff = _merge_sentence(sin_ovr, limit=1)
    if diff:
        parts.append("<p>{}</p>".format(html.escape(diff)))

    # 2) Frasi dagli snippet (IT → altre). Mai titoli nel corpo.
    web_for_narr = hits_it if hits_it else hits_other
    if web_for_narr:
        needed = max(NARRATIVE_MIN, 2)
        snippet_lines = _web_summary_from_snippets(web_for_narr, need=min(NARRATIVE_MAX, needed+2))
        if snippet_lines:
            body = " ".join(s.rstrip(" .") for s in snippet_lines)
            if not body.endswith("."):
                body += "."
            # Fusione Sinapsi (una riga) senza intestazioni
            if not sin_ovr and SINAPSI_FUSE and sin_aug:
                body += " " + _merge_sentence(sin_aug, limit=1)
            parts.append("<p>{}</p>".format(html.escape(body)))

    # 3) Fallback elegante (se proprio nulla)
    if not web_for_narr and not sin_ovr:
        generic = ("In generale, i sistemi Tecnaria si scelgono in base al supporto: "
                   "acciaio+lamiera → CTF (posa a sparo con P560 e chiodi HSBR14); "
                   "legno → CTL (viti dall’alto); "
                   "laterocemento senza lamiera → Diapason o V CEM-E/MINI. "
                   "La verifica finale resta a cura del progettista.")
        parts.append("<p>{}</p>".format(html.escape(generic)))

    # 4) Fonti (collassabili) — IT prima; se niente IT, massimo 3 altre etichettate
    if hits_it or hits_other:
        parts.append("<div class='nav'><button onclick=\"try{history.back()}catch(e){}\">⬅ Torna indietro</button> <a class='btn' href='/'>Home</a></div>")
        lis = []
        def render_li(h, show_snip: bool, extra_label: str = ""):
            title = html.escape(h.get("title") or "Fonte")
            url   = html.escape(h.get("url") or "")
            snip  = html.escape(h.get("snippet") or "")
            label = f" <em>({extra_label})</em>" if extra_label else ""
            li = f"<li><a href=\"{url}\" target=\"_blank\" rel=\"noopener\">{title}</a>{label}"
            if show_snip and snip:
                li += f"<br><small>{snip}</small>"
            li += "</li>"
            return li

        for h in hits_it:
            lis.append(render_li(h, show_snip=True, extra_label="IT"))
        if not hits_it:
            for h in hits_other[:3]:
                lang = (h.get("language") or "").upper()
                tag = "EN" if "/en/" in (h.get("url","")).lower() or lang == "EN" else (lang or "ALTRE")
                lis.append(render_li(h, show_snip=False, extra_label=tag))

        # pannello collassabile
        parts.append(
            "<details open>"
            "<summary><strong>Fonti</strong></summary>"
            "<div style='margin:.5rem 0'>"
            "<button type='button' onclick=\"this.closest('details').removeAttribute('open')\">Chiudi fonti</button>"
            "</div>"
            f"<ol class='list-decimal pl-5'>{''.join(lis)}</ol>"
            "</details>"
        )

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
        "lang_preferred": LANG_PREFERRED,
        "disambig_strict": DISAMBIG_STRICT,
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
    if _blocked_by_rules(q):
        return JSONResponse({"ok": True, "html": _card("Risposta Tecnaria", "<p>Richiesta non ammessa (prezzi/costi/preventivi).</p>", 0)})

    t0 = time.perf_counter()
    hits_it, hits_other = get_web_hits(q)
    sin_ovr, sin_aug, sin_psc = sinapsi_match_all(q)
    body_html = _compose_body(hits_it, hits_other, sin_ovr, sin_aug, sin_psc, q)
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
