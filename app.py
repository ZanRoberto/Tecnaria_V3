# app.py — Tecnaria QA Bot (WEB first, Sinapsi controllata)
# Stile narrativa: "Versione 3" (ricca, IT), Fonti solo link, NAV chiara
#
# Requisiti:
#   fastapi==0.115.0
#   uvicorn[standard]==0.30.6
#   gunicorn==21.2.0
#   requests==2.32.3
#   beautifulsoup4==4.12.3
#   jinja2==3.1.4  (opzionale)
#
# ENV principali:
#   BRAVE_API_KEY=<chiave valida>
#   PREFERRED_DOMAINS=tecnaria.com,www.tecnaria.com
#   LANG_PREFERRED=it
#   DISAMBIG_STRICT=true
#   FETCH_TECNARIA=true
#   ANSWER_MODE=full
#   MAX_ANSWER_CHARS=1800
#   ALLOW_SINAPSI_OVERRIDE=false
#   SOURCES_SHOW_SNIPPETS=false
#   SINAPSI_FILE=static/data/sinapsi_rules.json
#   HTTP_TIMEOUT=8.0
#   SINAPSI_MODE=off|assist|fallback   (default: assist)
#   MIN_WEB_OK_CHARS=400               (se web >= soglia → non si usa Sinapsi in assist)
#   MIN_WEB_OK_SENTENCES=3

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
STATIC_DIR   = os.environ.get("STATIC_DIR", "static")
SINAPSI_FILE = os.environ.get("SINAPSI_FILE", os.path.join(STATIC_DIR, "data", "sinapsi_rules.json"))

BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "").strip()
PREFERRED_DOMAINS = [d.strip() for d in os.environ.get(
    "PREFERRED_DOMAINS", "tecnaria.com,www.tecnaria.com"
).split(",") if d.strip()]

WEB_RESULTS_COUNT_PREFERRED = int(os.environ.get("WEB_RESULTS_COUNT_PREFERRED", "5"))
WEB_RESULTS_COUNT_FALLBACK  = int(os.environ.get("WEB_RESULTS_COUNT_FALLBACK",  "0"))
WEB_FRESHNESS_DAYS          = os.environ.get("WEB_FRESHNESS_DAYS", "365d")
LANG_PREFERRED              = os.environ.get("LANG_PREFERRED", "it").strip().lower()
DISAMBIG_STRICT             = os.environ.get("DISAMBIG_STRICT", "true").strip().lower() in ("1","true","yes")

ANSWER_MODE        = os.environ.get("ANSWER_MODE", "full").strip().lower()  # "full"|"standard"
MAX_ANSWER_CHARS   = int(os.environ.get("MAX_ANSWER_CHARS", "1800"))
FETCH_TECNARIA     = os.environ.get("FETCH_TECNARIA", "true").strip().lower() in ("1","true","yes")
HTTP_TIMEOUT       = float(os.environ.get("HTTP_TIMEOUT", "8.0"))

SOURCES_SHOW_SNIPPETS = os.environ.get("SOURCES_SHOW_SNIPPETS", "false").strip().lower() in ("1","true","yes")
ALLOW_SINAPSI_OVERRIDE = os.environ.get("ALLOW_SINAPSI_OVERRIDE", "false").strip().lower() in ("1","true","yes")

SINAPSI_MODE = os.environ.get("SINAPSI_MODE", "assist").strip().lower()  # off|assist|fallback
MIN_WEB_OK_CHARS = int(os.environ.get("MIN_WEB_OK_CHARS", "400"))
MIN_WEB_OK_SENTENCES = int(os.environ.get("MIN_WEB_OK_SENTENCES", "3"))

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()

# ============================== STATO ==============================
SINAPSI: Dict[str, Any] = {"rules": [], "exclude_any_q": [r"\bprezz\w*", r"\bcost\w*", r"\bpreventiv\w*", r"\boffert\w*"]}
SINAPSI_COMPILED: List[Dict[str, Any]] = []

# ============================== UTILS ==============================
def _safe_read(path: str) -> str:
    p = Path(path)
    try:
        return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
    except Exception:
        return ""

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
    boost = {"p560","ctf","ctl","diapason","lamiera","grecata","hsbr","legno","acciaio","calcestruzzo","solaio","tecnaria","chiod"}
    toks = sorted(toks, key=lambda w: (w not in boost, w))[:8]
    return " ".join(toks)

def _dedup_semantic(items: List[str]) -> List[str]:
    best: Dict[str, str] = {}
    for ans in items:
        sig = _signature(ans)
        if sig not in best or len(ans) < len(best[sig]):
            best[sig] = ans
    return list(best.values())

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
        if 8 <= len(s) <= 260:
            out.append(s)
    return out

def _guess_italian(text: str) -> bool:
    anchors = [" il ", " la ", " dei ", " delle ", " con ", " senza ", " chiod", " lamiera ", " calcestruzzo ", " posa "]
    t = " " + (_strip_html(text).lower()) + " "
    return sum(1 for a in anchors if a in t) >= 2

def _sanitize_brands(text: str) -> str:
    return re.sub(r"\b(hilti|dx\b|bx\b)\b", "altri utensili non supportati", text, flags=re.I)

# ============================== SINAPSI ==============================
def _compile_sinapsi() -> None:
    global SINAPSI_COMPILED
    SINAPSI_COMPILED = []
    rules = (SINAPSI.get("rules") or [])
    for r in rules:
        patt = (r.get("pattern") or "").strip()
        ans  = (r.get("answer")  or "").strip()
        mode = (r.get("mode") or "augment").lower().strip()
        if not patt or not ans:
            continue
        if (mode == "override") and (not ALLOW_SINAPSI_OVERRIDE):
            # ignoriamo gli override per sicurezza
            continue
        try:
            rx = re.compile(patt, re.I | re.S)
        except re.error:
            continue
        SINAPSI_COMPILED.append({
            "id": r.get("id"),
            "mode": mode,
            "answer": ans,
            "rx": rx,
            "priority": int(r.get("priority", 0))
        })
    # priorità: augment (desc) → postscript (desc)
    SINAPSI_COMPILED.sort(key=lambda x: (0 if x["mode"]=="augment" else 1, -x["priority"]))

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

def sinapsi_match(q: str) -> Tuple[str, str]:
    qn = _norm(q)
    aug: List[str] = []
    psc: List[str] = []
    for r in SINAPSI_COMPILED:
        try:
            if r["rx"].search(qn):
                if r["mode"] == "augment":
                    aug.append(_sanitize_brands(r["answer"]))
                elif r["mode"] == "postscript":
                    psc.append(_sanitize_brands(r["answer"]))
        except Exception:
            continue
    aug = _dedup_semantic(aug)
    psc = _dedup_semantic(psc)
    return (aug[0] if aug else ""), (psc[0] if psc else "")

# ============================== BRAVE SEARCH ==============================
def _build_query(q: str) -> str:
    if not DISAMBIG_STRICT:
        return q
    qn = _norm(q)
    plus, minus = [], []
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

def _brave(q: str, preferred: bool, site: str = "", count: int = 5) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        return []
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    q_built = _build_query(q)
    query = f"site:{site} {q_built}" if site else q_built
    try:
        r = requests.get(url, headers=headers, params={"q": query, "count": count, "freshness": WEB_FRESHNESS_DAYS}, timeout=HTTP_TIMEOUT)
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
        site_bonus = 6 if "tecnaria.com" in url else (1 if "spit" in url else 0)
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
    hits: List[Dict[str, Any]] = []
    for d in PREFERRED_DOMAINS:
        hits.extend(_brave(q, True, d, WEB_RESULTS_COUNT_PREFERRED))
    if not hits and WEB_RESULTS_COUNT_FALLBACK > 0:
        hits = _brave(q, False, "", WEB_RESULTS_COUNT_FALLBACK)
    hits = _filter_hits_by_query(q, hits)
    hits = _rank_hits_lang(q, hits)
    it_hits, other = _split_by_lang(hits)
    return it_hits, other

# ============================== FETCH & NARRATIVA ==============================
def _extract_main_text(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "html.parser")
    md = soup.find("meta", attrs={"name":"description"})
    if md and md.get("content"):
        return md["content"]
    candidates = []
    for sel in ["article", ".entry-content", ".post-content", ".content", "main", "#content", ".wp-block-post-content"]:
        node = soup.select_one(sel)
        if node:
            candidates.append(node.get_text(" ", strip=True))
    if not candidates:
        ps = " ".join(p.get_text(" ", strip=True) for p in soup.find_all("p"))
        if ps: candidates.append(ps)
    # accettiamo anche blocchi >=40 caratteri per ridurre i "vuoti"
    for c in candidates:
        if len(c.strip()) >= 40:
            return c.strip()
    return " ".join(candidates).strip() if candidates else ""

def _fetch_url(url: str) -> str:
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        if r.ok and "text/html" in (r.headers.get("Content-Type","")):
            return r.text
    except Exception:
        pass
    return ""

def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+|[\n\r]+|;\s+", text or "")
    out = []
    for p in parts:
        s = _strip_html(p.strip())
        if 8 <= len(s) <= 260:
            out.append(s)
    return out

def _topic_keywords(q: str) -> List[str]:
    qn = _norm(q)
    kw = ["solaio","solai","acciaio","calcestruzzo","lamiera","collaborante","spit","tecnaria","connettore","connettori","posa","staffa","piolo"]
    if "p560" in qn or "spit" in qn: kw += ["p560","propuls","guidapunte","pistone","a freddo","chiod"]
    if "ctf" in qn: kw += ["ctf","piolo","piastra","lamiera","2 chiod"]
    if "diapason" in qn: kw += ["diapason","staffa","4 chiod","prestaz","travi"]
    if "ctl" in qn: kw += ["ctl","viti","legno","tavolato"]
    if ("differenz" in qn) or (" vs " in f" {qn} ") or ("confront" in qn): kw += ["differenza","confronto"]
    return kw

def _score_sentence(s: str, kw: List[str]) -> int:
    s_l = " " + s.lower() + " "
    score = 0
    for k in kw:
        if k in s_l:
            score += 2 if len(k) > 4 else 1
    for bonus in [" chiod", " p560", " lamiera ", " staffa ", " piolo ", " prova ", " push-out ", " eta "]:
        if bonus in s_l: score += 2
    n = len(s)
    if n < 30: score -= 1
    if n > 240: score -= 1
    return score

def _best_sentences_from_html(html_text: str, q: str, need: int) -> List[str]:
    text = _extract_main_text(html_text)
    sents = _sentences(text)
    kw = _topic_keywords(q)
    scored = sorted(sents, key=lambda s: (_score_sentence(s, kw), -len(s)), reverse=True)
    out: List[str] = []
    for s in scored:
        sc = _score_sentence(s, kw)
        if sc <= 0: continue
        sig = _signature(s)
        if not any(_signature(a) == sig for a in out):
            out.append(s)
        if len(out) >= need: break
    return out

def _collect_narrative_from_web(hits_it: List[Dict[str,Any]], hits_other: List[Dict[str,Any]], q: str, max_chars: int) -> str:
    lines: List[str] = []

    # Confronti espliciti
    qn = _norm(q)
    if ("differenz" in qn) or (" vs " in f" {qn} ") or ("confront" in qn):
        lines.append(
            "CTF è un connettore a piolo su piastra per solai misti acciaio–calcestruzzo, tipicamente su lamiera grecata, con posa a freddo tramite SPIT P560 e due chiodi per connettore. "
            "Diapason è una staffa per travi in acciaio con o senza lamiera, indicata quando servono prestazioni più elevate; si posa a freddo con P560 e quattro chiodi."
        )

    # Fetch su tecnaria.com (max 4 URL)
    if FETCH_TECNARIA and hits_it:
        for h in hits_it[:4]:
            url = h.get("url","")
            if "tecnaria.com" not in url.lower(): continue
            html_text = _fetch_url(url)
            if not html_text: continue
            best = _best_sentences_from_html(html_text, q, need=12)
            best = [s for s in best if _guess_italian(s)]
            for s in best:
                if _signature(s) not in [_signature(x) for x in lines]:
                    lines.append(s)
                if len(" ".join(lines)) >= max_chars:
                    break
            if len(" ".join(lines)) >= max_chars:
                break

    # Se ancora corto, integra con snippet Brave (IT → altre)
    if len(" ".join(lines)) < max_chars:
        web_for_snip = hits_it if hits_it else hits_other
        for h in web_for_snip[:3]:
            for s in _sentences(h.get("snippet","") or ""):
                if _guess_italian(s) and _signature(s) not in [_signature(x) for x in lines]:
                    lines.append(s)
                if len(" ".join(lines)) >= max_chars:
                    break
            if len(" ".join(lines)) >= max_chars:
                break

    narrative = " ".join(line.rstrip(" .") for line in lines).strip()
    # FIX: se vuota → restituisco "" per attivare fallback
    if not narrative:
        return ""
    narrative = (narrative + ".").replace("..",".")
    if len(narrative) > max_chars:
        narrative = narrative[:max_chars].rsplit(" ", 1)[0] + "."
    return narrative

# ============================== HTML ==============================
def _nav_bar() -> str:
    return (
        "<div class='nav' style='display:flex;gap:.5rem;flex-wrap:wrap;margin:.5rem 0 1rem 0'>"
        "<button class='btn' style='font-weight:600;padding:.4rem .7rem' onclick=\"try{history.back()}catch(e){}\">⬅ Torna indietro</button>"
        "<a class='btn' style='font-weight:600;padding:.4rem .7rem' href='/'>Home</a>"
        "</div>"
    )

def _card(title: str, body_html: str, ms: int) -> str:
    return "<div class=\"card\"><h2>{}</h2>{}<p><small>⏱ {} ms</small></p></div>".format(
        html.escape(title), body_html, ms
    )

def _render_body(narrative: str, hits_it: List[Dict[str, Any]], hits_other: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    parts.append(_nav_bar())
    parts.append("<p>{}</p>".format(html.escape(narrative)))

    # Fonti
    if hits_it or hits_other:
        lis = []
        def render_li(h, extra_label: str = ""):
            title = html.escape(h.get("title") or "Fonte")
            url   = html.escape(h.get("url") or "")
            label = f" <em>({extra_label})</em>" if extra_label else ""
            if SOURCES_SHOW_SNIPPETS:
                snip = html.escape(h.get("snippet") or "")
                return f"<li><a href=\"{url}\" target=\"_blank\" rel=\"noopener\">{title}</a>{label}" + (f"<br><small>{snip}</small>" if snip else "") + "</li>"
            else:
                return f"<li><a href=\"{url}\" target=\"_blank\" rel=\"noopener\">{title}</a>{label}</li>"

        if hits_it:
            for h in hits_it:
                lis.append(render_li(h, "IT"))
        else:
            for h in hits_other[:5]:
                lang = (h.get("language") or "").upper()
                tag = "EN" if "/en/" in (h.get("url","")).lower() or lang == "EN" else (lang or "ALTRE")
                lis.append(render_li(h, tag))

        parts.append(
            "<details open>"
            "<summary><strong>Fonti</strong></summary>"
            "<div style='margin:.5rem 0'>"
            "<button type='button' onclick=\"this.closest('details').removeAttribute('open')\">Chiudi fonti</button>"
            "</div>"
            f"<ol class='list-decimal pl-5'>{''.join(lis)}</ol>"
            "</details>"
        )

    parts.append(_nav_bar())
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
        "answer_mode": ANSWER_MODE,
        "max_answer_chars": MAX_ANSWER_CHARS,
        "fetch_tecnaria": FETCH_TECNARIA,
        "allow_sinapsi_override": ALLOW_SINAPSI_OVERRIDE,
        "sources_show_snippets": SOURCES_SHOW_SNIPPETS,
        "sinapsi_mode": SINAPSI_MODE,
        "min_web_ok_chars": MIN_WEB_OK_CHARS,
        "min_web_ok_sentences": MIN_WEB_OK_SENTENCES,
        "app": "web->fetch_tecnaria->(sinapsi mode) -> render"
    })

@app.get("/", response_class=HTMLResponse)
def root():
    idx = Path(STATIC_DIR) / "index.html"
    return HTMLResponse(_safe_read(str(idx))) if idx.exists() else HTMLResponse(
        "<!doctype html><meta charset='utf-8'><title>{}</title><pre>POST /api/ask</pre>".format(html.escape(APP_TITLE))
    )

def _web_quality_ok(narrative: str) -> bool:
    if not narrative:
        return False
    chars = len(narrative.strip())
    sents = len(_sentences(narrative))
    return (chars >= MIN_WEB_OK_CHARS) and (sents >= MIN_WEB_OK_SENTENCES)

@app.post("/api/ask")
def api_ask(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    q = str(payload.get("q", "")).strip()
    if not q:
        return JSONResponse({"ok": True, "html": _card("Risposta Tecnaria", "<p>Manca la domanda.</p>", 0)})
    if _blocked_by_rules(q):
        return JSONResponse({"ok": True, "html": _card("Risposta Tecnaria", "<p>Richiesta non ammessa (prezzi/costi/preventivi).</p>", 0)})

    t0 = time.perf_counter()

    # 1) WEB
    hits_it, hits_other = get_web_hits(q)
    narrative_web = _collect_narrative_from_web(hits_it, hits_other, q, MAX_ANSWER_CHARS)

    # 2) SINAPSI — controllata dal MODE
    sin_aug, sin_psc = ("","")
    if SINAPSI_MODE in ("assist","fallback"):
        # carico i match solo se potrei usarli
        sin_aug, sin_psc = sinapsi_match(q)

    narrative_final = narrative_web

    if SINAPSI_MODE == "off":
        # non tocco niente
        pass

    elif SINAPSI_MODE == "assist":
        # uso Sinapsi SOLO se il web è debole
        if not _web_quality_ok(narrative_web):
            if sin_aug:
                narrative_final = (narrative_web + " " + sin_aug).strip() if narrative_web else sin_aug
            elif not narrative_web and sin_psc:
                narrative_final = sin_psc  # extrema ratio
        # se web è ok → ignoro Sinapsi

    elif SINAPSI_MODE == "fallback":
        # uso Sinapsi SOLO se il web è vuoto
        if not narrative_web:
            if sin_aug:
                narrative_final = sin_aug
            elif sin_psc:
                narrative_final = sin_psc

    # 3) Fallback assoluto se ancora vuota
    if not narrative_final:
        narrative_final = (
            "Sintesi tecnica ricavata da documentazione Tecnaria: la scelta dei sistemi dipende dal supporto "
            "(acciaio+lamiera → CTF; legno → CTL; assenza di lamiera → Diapason/V CEM-E), con posa a freddo e verifica a cura del progettista."
        )

    body_html = _render_body(narrative_final, hits_it, hits_other)
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
