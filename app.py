# app.py — Tecnaria QA Bot (WEB-first, IT-only, hard guards + fonti compatte) — v70
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

# =================== CONFIG ===================
STATIC_DIR   = os.environ.get("STATIC_DIR", "static")
SINAPSI_FILE = os.environ.get("SINAPSI_FILE", os.path.join(STATIC_DIR, "data", "sinapsi_rules.json"))

BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "").strip()
PREFERRED_DOMAINS = [d.strip() for d in os.environ.get("PREFERRED_DOMAINS", "tecnaria.com,www.tecnaria.com").split(",") if d.strip()]

WEB_RESULTS_COUNT_PREFERRED = int(os.environ.get("WEB_RESULTS_COUNT_PREFERRED", "12"))
WEB_RESULTS_COUNT_FALLBACK  = int(os.environ.get("WEB_RESULTS_COUNT_FALLBACK",  "0"))
WEB_FRESHNESS_DAYS          = os.environ.get("WEB_FRESHNESS_DAYS", "365d")
LANG_PREFERRED              = os.environ.get("LANG_PREFERRED", "it").strip().lower()
DISAMBIG_STRICT             = os.environ.get("DISAMBIG_STRICT", "true").strip().lower() in ("1","true","yes")

ANSWER_MODE        = os.environ.get("ANSWER_MODE", "full").strip().lower()
MAX_ANSWER_CHARS   = int(os.environ.get("MAX_ANSWER_CHARS", "2000"))
FETCH_TECNARIA     = os.environ.get("FETCH_TECNARIA", "true").strip().lower() in ("1","true","yes")
HTTP_TIMEOUT       = float(os.environ.get("HTTP_TIMEOUT", "8.0"))

SOURCES_SHOW_SNIPPETS = os.environ.get("SOURCES_SHOW_SNIPPETS", "false").strip().lower() in ("1","true","yes")
SOURCES_MAX           = int(os.environ.get("SOURCES_MAX", "3"))              # << NEW: max 3 fonti
SOURCES_COLLAPSED     = os.environ.get("SOURCES_COLLAPSED","true").lower() in ("1","true","yes")  # << NEW

ALLOW_SINAPSI_OVERRIDE = os.environ.get("ALLOW_SINAPSI_OVERRIDE", "false").strip().lower() in ("1","true","yes")
SINAPSI_MODE = os.environ.get("SINAPSI_MODE", "off").strip().lower()  # off|assist|fallback
MIN_WEB_OK_CHARS = int(os.environ.get("MIN_WEB_OK_CHARS", "200"))
MIN_WEB_OK_SENTENCES = int(os.environ.get("MIN_WEB_OK_SENTENCES", "2"))

ACCEPT_EN_BACKFILL = os.environ.get("ACCEPT_EN_BACKFILL", "true").strip().lower() in ("1","true","yes")
USE_SNIPPET_BACKFILL = os.environ.get("USE_SNIPPET_BACKFILL", "true").strip().lower() in ("1","true","yes")

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()

# =================== STATO ===================
SINAPSI: Dict[str, Any] = {"rules": [], "exclude_any_q": [r"\bprezz\w*", r"\bcost\w*", r"\bpreventiv\w*", r"\boffert\w*"]}
SINAPSI_COMPILED: List[Dict[str, Any]] = []

# =================== UTILS ===================
def _safe_read(path: str) -> str:
    p = Path(path)
    try:
        return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
    except Exception:
        return ""

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = s.replace("·", ". ")
    s = re.sub(r"[^\w\s/.\-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _strip_html(s: str) -> str:
    if not s: return ""
    try:
        return BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    except Exception:
        return re.sub(r"<[^>]+>", " ", s)

def _sentences(text: str) -> List[str]:
    t = (_strip_html(text or "")).replace("·", ". ")
    parts = re.split(r"(?<=[.!?])\s+|[\n\r]+|;\s+", t)
    out = []
    for p in parts:
        s = p.strip()
        if 8 <= len(s) <= 240:
            out.append(s)
    return out

def _content_words(s: str) -> List[str]:
    stop = {"il","lo","la","i","gli","le","un","una","di","del","della","dei","degli","delle","per","con","da","a","al","ai","agli","alla","alle","su","nel","nella","nelle","non","è","e","o","che","quale","d","l","all","allo","agli","in"}
    toks = [t for t in re.split(r"[^\w]+", _norm(s)) if len(t) > 3 and t not in stop]
    return toks

def _signature(s: str) -> str:
    toks = _content_words(s)
    if not toks: return _norm(s)
    boost = {"p560","ctf","ctl","diapason","lamiera","grecata","hsbr","legno","acciaio","calcestruzzo","solaio","tecnaria","chiod","patent","metro","quadrato","mq","m2","m²"}
    toks = sorted(toks, key=lambda w: (w not in boost, w))[:8]
    return " ".join(toks)

def _dedup_semantic(items: List[str]) -> List[str]:
    best: Dict[str, str] = {}
    for ans in items:
        sig = _signature(ans)
        if sig not in best or len(ans) < len(best[sig]):
            best[sig] = ans
    return list(best.values())

def _guess_italian(text: str) -> bool:
    anchors = [" il ", " la ", " dei ", " delle ", " con ", " senza ", " chiod", " lamiera ", " calcestruzzo ", " posa ", " metri ", " metro "]
    t = " " + (_strip_html(text).lower()) + " "
    return sum(1 for a in anchors if a in t) >= 2

def _is_junk_sentence(s: str) -> bool:
    s_l = s.lower()
    # Junk EN/specs/indirizzi & simili
    if "viale pecori giraldi" in s_l or "italy" in s_l: return True
    if "specifications" in s_l or "dimensions" in s_l or "available shank heights" in s_l: return True
    if "on this page you can download" in s_l or "to download" in s_l: return True
    if "floor reinforcement" in s_l and "restoration" in s_l: return True
    if re.search(r"https?://", s_l): return True
    # Rumore numerico
    if len(re.findall(r"\b\d{2,}\b", s)) >= 3: return True
    # Lunghezze estreme
    if len(s) < 12 or len(s) > 220: return True
    return False

def _tidy_narrative(txt: str, max_chars: int) -> str:
    """Ripulisce da code-mix e rumore, forza IT breve e chiusa in punto."""
    if not txt: return ""
    # tagli dure contro pattern noti
    cuts = [
        r"Specifications.*", r"Dimensions.*", r"Available shank heights.*",
        r"Viale Pecori Giraldi.*", r"On this page you can download.*",
        r"Floor reinforcement.*", r"Pressed connection bracket.*"
    ]
    for c in cuts:
        txt = re.sub(c, "", txt, flags=re.I|re.S)
    # spazi & punti
    txt = re.sub(r"\s+", " ", txt).strip()
    # se non sembra italiano, svuota (niente ibridi)
    if not _guess_italian(txt):
        return ""
    # tronca a max_chars e assicurati che finisca con punto
    if len(txt) > max_chars:
        txt = txt[:max_chars].rsplit(" ", 1)[0].rstrip(",;:")
    if not txt.endswith((".", "!", "?")):
        txt += "."
    return txt

# =================== SINAPSI ===================
def _compile_sinapsi() -> None:
    global SINAPSI_COMPILED
    SINAPSI_COMPILED = []
    rules = (SINAPSI.get("rules") or [])
    for r in rules:
        patt = (r.get("pattern") or "").strip()
        ans  = (r.get("answer")  or "").strip()
        mode = (r.get("mode") or "augment").lower().strip()
        if not patt or not ans: continue
        if (mode == "override") and (not ALLOW_SINAPSI_OVERRIDE): continue
        try:
            rx = re.compile(patt, re.I | re.S)
        except re.error:
            continue
        SINAPSI_COMPILED.append({"id": r.get("id"), "mode": mode, "answer": ans, "rx": rx, "priority": int(r.get("priority", 0))})
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
            if re.search(patt, q, flags=re.I): return True
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
    if SINAPSI_MODE == "off": return "",""
    qn = _norm(q); aug: List[str] = []; psc: List[str] = []
    for r in SINAPSI_COMPILED:
        try:
            if r["rx"].search(qn):
                if r["mode"] == "augment": aug.append(r["answer"])
                elif r["mode"] == "postscript": psc.append(r["answer"])
        except Exception: continue
    return ( _dedup_semantic(aug)[0] if aug else "" , _dedup_semantic(psc)[0] if psc else "" )

# =================== BRAVE ===================
def _build_query(q: str, wants_license: bool) -> str:
    if not DISAMBIG_STRICT: return q
    qn = _norm(q); plus, minus = [], []
    plus.append('"Tecnaria S.p.A." OR Tecnaria')
    plus.append('"Bassano del Grappa"')
    plus.append('connettori OR connettore OR "solai misti" OR "acciaio calcestruzzo" OR lamiera')
    if "ctf" in qn: minus += ["chimica","farmacia","farmaceutic*"]; plus.append("CTF connettori")
    if "diapason" in qn: minus += ["musica","accordare","tuning fork"]; plus.append("Diapason connettori")
    if ("p560" in qn) or ("spit" in qn): plus.append('"SPIT P560" connettori CTF Tecnaria')
    if any(k in qn for k in ["m2","m²","mq","metro quadrato","al m2","al mq"]): plus.append('maglia passo connettori m² mq')
    if wants_license: plus.append('"does not require special license" OR "no special license"')
    add = (" " + " ".join(plus) if plus else "") + (" " + " ".join(f"-{m}" for m in minus) if minus else "")
    return f"{q}{add}".strip()

def _brave(q: str, preferred: bool, site: str = "", count: int = 8) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY: return []
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    wants_license = any(k in _norm(q) for k in ["patent","patentino","licenz","autorizz"])
    q_built = _build_query(q, wants_license)
    query = f"site:{site} {q_built}" if site else q_built
    try:
        r = requests.get(url, headers=headers, params={"q": query, "count": count, "freshness": WEB_FRESHNESS_DAYS}, timeout=HTTP_TIMEOUT)
        if not r.ok: return []
        items = (r.json().get("web", {}) or {}).get("results", []) or []
    except Exception:
        return []
    out = []
    for it in items:
        out.append({
            "title": _strip_html(it.get("title") or (site or "Fonte")),
            "url": it.get("url") or "",
            "snippet": _strip_html((it.get("description") or "").replace("·",". ")),
            "preferred": preferred,
            "language": (it.get("language") or "").lower()
        })
    return out

def _is_it_url(url: str) -> bool:
    u = (url or "").lower()
    if "/it/" in u: return True
    if "/en/" in u: return False
    if "tecnaria.com" in u and "/en" not in u: return True
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
    if not qkw: return hits
    def ok(h):
        blob = _norm(" ".join([h.get("title",""), h.get("snippet",""), h.get("url","")]))
        words = set(_content_words(blob))
        return bool(qkw & words)
    filtered = [h for h in hits if ok(h)]
    return filtered or hits

def _split_by_lang(hits: List[Dict[str,Any]]) -> Tuple[List[Dict[str,Any]], List[Dict[str,Any]]]:
    it_hits, other = [], []
    for h in hits:
        if _is_it_url(h.get("url","")): it_hits.append(h)
        else: other.append(h)
    return it_hits, other

def get_web_hits(q: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    hits: List[Dict[str, Any]] = []
    wants_license = any(k in _norm(q) for k in ["patent","patentino","licenz","autorizz"])
    for d in PREFERRED_DOMAINS:
        hits.extend(_brave(q, True, d, WEB_RESULTS_COUNT_PREFERRED))
    if wants_license and len(hits) < 3:
        for d in PREFERRED_DOMAINS:
            hits.extend(_brave('P560 nail gun Tecnaria', True, d, 8))
    if not hits and WEB_RESULTS_COUNT_FALLBACK > 0:
        hits = _brave(q, False, "", WEB_RESULTS_COUNT_FALLBACK)
    hits = _filter_hits_by_query(q, hits)
    hits = _rank_hits_lang(q, hits)
    it_hits, other = _split_by_lang(hits)
    return it_hits, other

# =================== FETCH & NARRATIVA ===================
def _extract_main_text(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "html.parser")
    md = soup.find("meta", attrs={"name":"description"})
    if md and md.get("content"): 
        return (md["content"] or "").replace("·",". ")
    ps = " ".join(p.get_text(" ", strip=True) for p in soup.find_all("p"))
    return (ps or "").replace("·",". ")

def _fetch_url(url: str) -> str:
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        if r.ok and "text/html" in (r.headers.get("Content-Type","")): 
            return r.text
    except Exception:
        pass
    return ""

def _topic_keywords(q: str) -> List[str]:
    qn = _norm(q)
    kw = ["solaio","solai","acciaio","calcestruzzo","lamiera","collaborante","spit","tecnaria","connettore","connettori","posa","staffa","piolo","patent","licenz","autorizz","metro","quadrato","m2","m²","mq"]
    if "p560" in qn or "spit" in qn: kw += ["p560","propuls","guidapunte","pistone","a freddo","chiod","license","authoris"]
    if "ctf" in qn: kw += ["ctf","piolo","piastra","lamiera","2 chiod","maglia","passo","densità"]
    if "diapason" in qn: kw += ["diapason","staffa","4 chiod","prestaz","travi"]
    if ("differenz" in qn) or (" vs " in f" {qn} ") or ("confront" in qn): kw += ["differenza","confronto"]
    return kw

def _score_sentence(s: str, kw: List[str]) -> int:
    s_l = " " + s.lower() + " "
    score = 0
    for k in kw:
        if k in s_l: score += 2 if len(k) > 4 else 1
    for bonus in [" chiod"," p560"," lamiera "," staffa "," piolo "," prova "," push-out "," eta "," license "," authoris"," m² "," mq "," m2 "]:
        if bonus in s_l: score += 2
    n = len(s)
    if n < 30: score -= 1
    if n > 240: score -= 1
    if _is_junk_sentence(s): score -= 4
    return score

def _best_sentences_from_html(html_text: str, q: str, need: int) -> List[str]:
    text = _extract_main_text(html_text)
    sents = _sentences(text)
    kw = _topic_keywords(q)
    scored = sorted(sents, key=lambda s: (_score_sentence(s, kw), -len(s)), reverse=True)
    out: List[str] = []
    seen = set()
    for s in scored:
        if _is_junk_sentence(s): continue
        if not _guess_italian(s):  # narrativa solo IT
            continue
        sig = _signature(s)
        if sig in seen: continue
        seen.add(sig); out.append(s)
        if len(out) >= need: break
    return out

def _license_free_en(text: str) -> bool:
    t = " " + _strip_html(text).lower() + " "
    return ("does not require special license" in t) or ("no special license" in t)

def _it_sentence_license_free() -> str:
    return ("Per la chiodatrice SPIT P560 **non serve alcun patentino né autorizzazioni speciali**: "
            "è a tiro indiretto (classe A) con propulsori a salve; restano obbligatori i DPI e l’uso conforme al manuale.")

# Riconoscitore domanda "Quanti CTF al m²"
_CTF_DENSITY_RX = re.compile(r"(quanti|quanto|densit[aà]|passo|maglia).*(ctf).*?(m2|m²|mq|metro quadrato)", re.I | re.S)
def _is_ctf_density_question(q: str) -> bool:
    qn = _norm(q)
    if "ctf" in qn and any(k in qn for k in [" m2 "," m² "," mq "," metro quadrato "," al m2 "," al mq "]):
        return True
    return bool(_CTF_DENSITY_RX.search(q))

def _ctf_density_answer() -> str:
    return ("La quantità di connettori CTF **deriva dal calcolo strutturale** (luci, carichi, profilo di lamiera, spessore soletta, verifiche EC4/ETA). "
            "Come **ordine di grandezza** si impiegano **circa 6–8 CTF per m²**, con maglia più fitta in prossimità degli appoggi e più rada in mezzeria. "
            "Il passo effettivo e gli eventuali rinfoltimenti sono definiti dal progettista.")

def _collect_narrative_from_web(hits_it: List[Dict[str,Any]], hits_other: List[Dict[str,Any]], q: str, max_chars: int) -> str:
    lines: List[str] = []
    qn = _norm(q)
    is_diff = ("differenz" in qn) or (" vs " in f" {qn} ") or ("confront" in qn)
    wants_license = any(k in qn for k in ["patent","patentino","licenz","autorizz"])

    # CASO 1: patentino/licenza -> 2 frasi secche e STOP (niente snippet aggiuntivi)
    if wants_license:
        lines.append(_it_sentence_license_free())
        lines.append("La P560, usata per fissare dall’alto i connettori CTF/Diapason a freddo, richiede una breve formazione interna e l’uso dei DPI.")
        narrative = " ".join(lines)
        return _tidy_narrative(narrative, max_chars)

    # CASO 2: Quanti CTF al m² -> risposta deterministica
    if _is_ctf_density_question(q):
        return _tidy_narrative(_ctf_density_answer(), max_chars)

    # CASO 3: confronto CTF vs Diapason (narrativa breve)
    if is_diff:
        lines.append("CTF è un connettore a piolo su piastra per solai misti acciaio–calcestruzzo, tipicamente su lamiera grecata; posa a freddo con SPIT P560 (due chiodi).")
        lines.append("Diapason è una staffa per travi in acciaio con prestazioni superiori; posa a freddo con P560 (quattro chiodi).")

    # 1) IT Tecnaria only
    if FETCH_TECNARIA and hits_it:
        for h in hits_it[:6]:
            url = h.get("url","")
            if "tecnaria.com" not in url.lower(): continue
            html_text = _fetch_url(url)
            best = _best_sentences_from_html(html_text, q, need=6) if html_text else []
            if not best and USE_SNIPPET_BACKFILL:
                cand = [s for s in _sentences(h.get("snippet","")) if not _is_junk_sentence(s) and _guess_italian(s)]
                best = cand[:2]
            for s in best:
                sigs = [_signature(x) for x in lines]
                if _signature(s) not in sigs:
                    lines.append(s)
                if len(lines) >= 3: break  # << narrativa corta
            if len(lines) >= 3: break

    narrative = " ".join(line.rstrip(" .") for line in lines).strip()
    return _tidy_narrative(narrative, max_chars)

# =================== HTML ===================
def _nav_bar() -> str:
    return (
        "<div class='nav' style='display:flex;gap:.5rem;flex-wrap:wrap;margin:.5rem 0 1rem 0'>"
        "<button class='btn' style='font-weight:600;padding:.4rem .7rem' onclick=\"try{history.back()}catch(e){}\">⬅ Torna indietro</button>"
        "<a class='btn' style='font-weight:600;padding:.4rem .7rem' href='/'>Home</a>"
        "</div>"
    )

def _dedup_sources(hits: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    seen = set(); out = []
    for h in hits:
        u = (h.get("url") or "").strip().lower()
        if not u: continue
        if u in seen: continue
        seen.add(u)
        out.append(h)
    return out

def _render_sources(it_hits: List[Dict[str,Any]]) -> str:
    it_clean = _dedup_sources([h for h in it_hits if "tecnaria.com" in (h.get("url","")).lower()])
    if not it_clean:
        return ""
    it_clean = it_clean[:SOURCES_MAX]
    lis = []
    for h in it_clean:
        title = html.escape(h.get("title") or "Fonte")
        url   = html.escape(h.get("url") or "")
        if SOURCES_SHOW_SNIPPETS and h.get("snippet"):
            snip = html.escape(h.get("snippet") or "")
            lis.append(f"<li><a href=\"{url}\" target=\"_blank\" rel=\"noopener\">{title}</a> <em>(IT)</em><br><small>{snip}</small></li>")
        else:
            lis.append(f"<li><a href=\"{url}\" target=\"_blank\" rel=\"noopener\">{title}</a> <em>(IT)</em></li>")
    details_open = "" if SOURCES_COLLAPSED else " open"
    return ("<details"+details_open+"><summary><strong>Fonti</strong> (Tecnaria)</summary>"
            "<div style='margin:.5rem 0'><button type='button' onclick=\"this.closest('details').removeAttribute('open')\">Chiudi fonti</button></div>"
            f"<ol class='list-decimal pl-5'>{''.join(lis)}</ol></details>")

def _render_body(narrative: str, hits_it: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    parts.append(_nav_bar())
    parts.append(f"<p>{html.escape(narrative)}</p>")
    src_html = _render_sources(hits_it)
    if src_html:
        parts.append(src_html)
    parts.append(_nav_bar())
    return "\n".join(parts)

# =================== ENDPOINTS ===================
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
        "sources_max": SOURCES_MAX,
        "sources_collapsed": SOURCES_COLLAPSED,
        "sinapsi_mode": SINAPSI_MODE,
        "min_web_ok_chars": MIN_WEB_OK_CHARS,
        "min_web_ok_sentences": MIN_WEB_OK_SENTENCES,
        "accept_en_backfill": ACCEPT_EN_BACKFILL,
        "use_snippet_backfill": USE_SNIPPET_BACKFILL,
        "app": "web->IT narrative (hard guards)->fonti compatte"
    })

def _web_quality_ok(narrative: str) -> bool:
    if not narrative: return False
    chars = len(narrative.strip()); sents = len(_sentences(narrative))
    return (chars >= MIN_WEB_OK_CHARS) and (sents >= MIN_WEB_OK_SENTENCES)

@app.post("/api/ask")
def api_ask(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    q = str(payload.get("q", "")).strip()
    if not q:
        return JSONResponse({"ok": True, "html": _card("Risposta Tecnaria", "<p>Manca la domanda.</p>", 0)})
    if _blocked_by_rules(q):
        return JSONResponse({"ok": True, "html": _card("Risposta Tecnaria", "<p>Richiesta non ammessa (prezzi/costi/preventivi).</p>", 0)})

    t0 = time.perf_counter()

    qn = _norm(q)
    wants_license = any(k in qn for k in ["patent","patentino","licenz","autorizz"]) and ("p560" in qn or "spit" in qn)
    wants_ctf_density = _is_ctf_density_question(q)

    hits_it, hits_other = get_web_hits(q)
    narrative_web = _collect_narrative_from_web(hits_it, hits_other, q, MAX_ANSWER_CHARS)

    sin_aug, sin_psc = ("","")
    if not wants_license and not wants_ctf_density and SINAPSI_MODE in ("assist","fallback"):
        sin_aug, sin_psc = sinapsi_match(q)

    narrative_final = narrative_web
    if not wants_license and not wants_ctf_density:
        if SINAPSI_MODE == "assist":
            if not _web_quality_ok(narrative_web) and sin_aug:
                narrative_final = (narrative_web + " " + sin_aug).strip() if narrative_web else sin_aug
            elif not narrative_web and sin_psc:
                narrative_final = sin_psc
        elif SINAPSI_MODE == "fallback":
            if not narrative_web:
                narrative_final = sin_aug or sin_psc or ""

    if not narrative_final:
        narrative_final = ("Sintesi tecnico-commerciale ricavata da documentazione Tecnaria e norme di riferimento. "
                           "Per casi reali attenersi sempre al progetto esecutivo e al manuale di posa.")

    body_html = _render_body(narrative_final, hits_it)  # SOLO IT nelle fonti
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

# =================== UI SHELL ===================
INDEX_HTML = f"""
<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <title>{APP_TITLE}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 24px; }}
    .card {{ border:1px solid #e6e6e6; border-radius:12px; padding:16px; max-width:960px; }}
    .btn {{ border:1px solid #ddd; border-radius:8px; text-decoration:none; background:#f8f8f8; }}
    h1 {{ font-size:20px; margin:0 0 12px; }}
    input,button {{ font-size:16px; }}
    .nav a, .nav button {{ cursor:pointer; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>TECNARIA<br><small>{APP_TITLE}</small></h1>
    <form onsubmit="ev(event)">
      <label>Fai una domanda</label><br/>
      <input id="q" name="q" style="width:70%" placeholder="Es. &quot;Serve il patentino per la P560?&quot;" />
      <button class="btn" type="submit">Cerca</button>
    </form>
    <div id="out" style="margin-top:16px"></div>
  </div>

<script>
async function ev(e){ e.preventDefault();
  const q = document.getElementById('q').value;
  const r = await fetch('/api/ask', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{q}})}});
  const j = await r.json();
  document.getElementById('out').innerHTML = j.html;
}
</script>
</body>
</html>
"""

@app.get("/")
def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)

def _card(title: str, body_html: str, ms: int) -> str:
    return (f"<div class=\"card\"><h2>{html.escape(title)}</h2>{body_html}"
            f"<p><small>⏱ {ms} ms</small></p></div>")
