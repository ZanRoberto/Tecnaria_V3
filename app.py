# app.py — Tecnaria QA Bot (WEB → Sinapsi refine, risposta unica)
# - Nessun KB locale
# - SINAPSI_FILE default: static/data/sinapsi_rules.json
# - /health pulito
# - Pipeline: Web (Brave, domini ammessi) -> Sinapsi refine -> testo unico

import os
import re
import json
import time
import html
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, Body
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

APP_TITLE = "Tecnaria QA Bot"
MODE = "web_first_then_sinapsi_refine_single"  # visibile su /health

app = FastAPI(title=APP_TITLE)

# -----------------------------
# Config
# -----------------------------
STATIC_DIR = os.environ.get("STATIC_DIR", "static")
SINAPSI_FILE = os.environ.get("SINAPSI_FILE", os.path.join(STATIC_DIR, "data", "sinapsi_rules.json"))
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
ALLOWED_DOMAINS = json.loads(os.environ.get("ALLOWED_DOMAINS_JSON", '["tecnaria.com","spit.eu","spitpaslode.com"]'))
MIN_WEB_SCORE = float(os.environ.get("MIN_WEB_SCORE", "0.35"))   # confermato
MAX_WEB_RESULTS = int(os.environ.get("MAX_WEB_RESULTS", "5"))
WEB_MIN_QUALITY = float(os.environ.get("WEB_MIN_QUALITY", "0.55"))

# -----------------------------
# Static mount (se esiste)
# -----------------------------
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# -----------------------------
# Sinapsi loader & engine
# -----------------------------
class Rule:
    def __init__(self, rid: str, pattern: str, mode: str, lang: str, answer: str,
                 sources: Optional[List[Dict[str, str]]] = None):
        self.id = rid
        self.pattern_str = pattern
        self.pattern = re.compile(pattern, re.IGNORECASE | re.DOTALL)
        self.mode = (mode or "augment").lower().strip()  # override | augment | postscript
        self.lang = lang or "it"
        self.answer = answer or ""
        self.sources = sources or []

class SinapsiEngine:
    def __init__(self, path: str):
        self.path = path
        self.rules: List[Rule] = []
        self.meta = {}

    def load(self) -> int:
        if not os.path.exists(self.path):
            self.rules = []
            self.meta = {"count": 0, "path": self.path}
            return 0
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        rules_raw = data["rules"] if isinstance(data, dict) and "rules" in data else data
        loaded: List[Rule] = []
        for r in rules_raw:
            loaded.append(Rule(
                rid=r.get("id", ""),
                pattern=r.get("pattern", "(?s).*"),
                mode=r.get("mode", "augment"),
                lang=r.get("lang", "it"),
                answer=r.get("answer", ""),
                sources=r.get("sources", [])
            ))
        self.rules = loaded
        self.meta = {"count": len(self.rules), "path": self.path}
        return len(self.rules)

    def apply(self, question: str, lang_hint: str = "it") -> Dict[str, Any]:
        ov: Optional[str] = None
        aug: List[str] = []
        ps: List[str] = []
        srcs: List[Dict[str, str]] = []
        q = question or ""
        for rule in self.rules:
            if rule.pattern.search(q):
                if rule.mode == "override" and ov is None:
                    ov = rule.answer
                    srcs.extend(rule.sources)
                elif rule.mode == "augment":
                    aug.append(rule.answer)
                    srcs.extend(rule.sources)
                elif rule.mode == "postscript":
                    ps.append(rule.answer)
                    srcs.extend(rule.sources)
        return {"override": ov, "augments": aug, "postscripts": ps, "sources": srcs}

SINAPSI = SinapsiEngine(SINAPSI_FILE)
SINAPSI.load()  # ✅ precaricato all'avvio

# -----------------------------
# Utilità lingua & pulizia
# -----------------------------
NOISE_PATTERNS = re.compile(
    r"(just a moment|checking your browser|questo sito utilizza i cookie|cookie policy|%PDF-|consenso cookie|enable javascript)",
    re.IGNORECASE
)

def is_allowed_domain(url: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower()
        return any(netloc.endswith(d) for d in ALLOWED_DOMAINS)
    except Exception:
        return False

def clean_snippet(text: str) -> str:
    if not text:
        return ""
    t = html.unescape(text)
    if NOISE_PATTERNS.search(t):
        return ""
    t = re.sub(r"\s+", " ", t).strip()
    return t

def detect_lang(question: str) -> str:
    q = (question or "").lower()
    if re.search(r"[äöüß]|(der|die|das|und|ist)\b", q): return "de"
    if re.search(r"\b(the|and|what|how|when|which|sheet|manual)\b", q): return "en"
    if re.search(r"\b(que|cómo|cuál|cuando|ventajas)\b", q): return "es"
    if re.search(r"\b(quels|comment|avantages|quand)\b", q): return "fr"
    return "it"

# -----------------------------
# Web search (Brave) — sempre per primo
# -----------------------------
def brave_search(query: str, lang: str = "it") -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        return []
    try:
        url = "https://api.search.brave.com/res/v1/web/search"
        headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
        params = {
            "q": query,
            "country": "it",
            "source": "web",
            "count": 10,
            "freshness": "month"  # se vuoi più fresco: "week" o "day"
        }
        r = requests.get(url, headers=headers, params=params, timeout=8)
        r.raise_for_status()
        data = r.json()
        web = data.get("web", {}) or {}
        results = []
        for item in web.get("results", []):
            url_i = item.get("url", "")
            if not is_allowed_domain(url_i):
                continue
            props = item.get("properties", {}) or {}
            score = float(props.get("score") or item.get("typeRank") or 0.0)
            if score < MIN_WEB_SCORE:
                continue
            title = clean_snippet(item.get("title", "")) or url_i
            snippet = clean_snippet(item.get("description", "")) or clean_snippet(item.get("snippet", ""))
            if not (title or snippet):
                continue
            results.append({"title": title, "url": url_i, "snippet": snippet})
        return results[:MAX_WEB_RESULTS]
    except Exception:
        return []

# -----------------------------
# Scoring qualità bozza web
# -----------------------------
def score_web_quality(web_hits: List[Dict[str, Any]]) -> float:
    if not web_hits:
        return 0.0
    n = len(web_hits)
    total_len = sum(len(h.get("snippet", "")) for h in web_hits)
    uniq_hosts = len({urlparse(h.get("url","")).netloc for h in web_hits if h.get("url")})
    n_term = min(n / max(1, MAX_WEB_RESULTS), 1.0)
    len_term = min(total_len / 900.0, 1.0)       # ~3 snippet medi
    host_term = min(uniq_hosts / 3.0, 1.0)       # varietà 2-3 host
    return 0.45*n_term + 0.40*len_term + 0.15*host_term

# -----------------------------
# Composer — produce UN SOLO TESTO finale
# -----------------------------
def compose_single_answer(question: str, lang: str, sinapsi_pack: Dict[str, Any],
                          web_hits: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, str]]]:
    """Ritorna (testo_unico, sources)."""
    sources: List[Dict[str, str]] = []
    override = sinapsi_pack.get("override")
    augments: List[str] = sinapsi_pack.get("augments", [])
    postscripts: List[str] = sinapsi_pack.get("postscripts", [])
    sinapsi_sources = sinapsi_pack.get("sources", [])
    web_quality = score_web_quality(web_hits)

    # 1) Base: web (se buono) altrimenti override Sinapsi
    base_parts: List[str] = []
    if web_quality >= WEB_MIN_QUALITY and web_hits:
        picked = [h for h in web_hits if h.get("snippet")]
        picked = picked[:3]
        if picked:
            base_parts.append(" ".join(h["snippet"] for h in picked))
        else:
            base_parts.append("Informazioni ufficiali reperite dai siti consentiti.")
        for h in web_hits:
            sources.append({"title": h.get("title") or h.get("url","Fonte"), "url": h.get("url","")})
        # Se c'è un override, lo uso come "nota tecnica" fondendolo nel testo
        if override:
            base_parts.append(override.strip())
            sources.extend(sinapsi_sources)
    else:
        if override:
            base_parts.append(override.strip())
            sources.extend(sinapsi_sources)
        else:
            # nessun web utile e nessun override -> provo con augment/postscript
            pass

    # 2) Arricchisco con augment e postscript (coda)
    if augments:
        base_parts.append(" ".join(a.strip() for a in augments if a and a.strip()))
    if postscripts:
        base_parts.append(" ".join(p.strip() for p in postscripts if p and p.strip()))

    # 3) Fallback
    final_text = " ".join([b for b in base_parts if b]).strip() or \
                 "Non ho trovato elementi sufficienti su domini autorizzati o nelle regole. Raffina la domanda o aggiorna le regole."

    # Compatta spazi
    final_text = re.sub(r"\s+", " ", final_text).strip()
    return final_text, sources

# -----------------------------
# Rendering card unica
# -----------------------------
def render_sources_html(sources: List[Dict[str, str]]) -> str:
    if not sources:
        return ""
    seen = set()
    items = []
    for s in sources:
        url = (s.get("url") or "").strip()
        title = (s.get("title") or "Fonte").strip() or "Fonte"
        if not url or url in seen:
            continue
        seen.add(url)
        items.append(f"📎 <a href='{html.escape(url)}' target='_blank'>{html.escape(title)}</a>")
    if not items:
        return ""
    return "<div class='sources'><strong>Fonti</strong><br>" + "<br>".join(items) + "</div>"

def render_card_html(body_text: str, sources: List[Dict[str,str]], elapsed_ms: int,
                     subtitle: str = "Risposta Tecnaria") -> str:
    safe_body = html.escape(body_text).replace("\n\n", "</p><p>").replace("\n", "<br>")
    sources_html = render_sources_html(sources)
    return f"""
    <div class="card">
      <h2>{html.escape(subtitle)}</h2>
      <p>{safe_body}</p>
      {sources_html}
      <p><small>⏱ {elapsed_ms} ms</small></p>
    </div>
    """

# -----------------------------
# Endpoints
# -----------------------------
@app.get("/", response_class=HTMLResponse)
def home():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html; charset=utf-8")
    # fallback minimo se manca l'index
    return HTMLResponse("<pre>{\"ok\":true,\"msg\":\"Use /ask or place static/index.html\"}</pre>",
                        media_type="text/html; charset=utf-8")

@app.get("/health", response_class=JSONResponse)
def health():
    return JSONResponse({
        "status": "ok",
        "mode": MODE,
        "web_search": {
            "provider": "brave",
            "enabled": bool(BRAVE_API_KEY),
            "preferred_domains": ALLOWED_DOMAINS,
            "min_web_score": MIN_WEB_SCORE,
            "web_min_quality": WEB_MIN_QUALITY
        },
        "critici": {
            "dir": STATIC_DIR,
            "sinapsi_file": SINAPSI_FILE,
            "sinapsi_loaded": SINAPSI.meta.get("count", 0)
        }
    })

@app.get("/ask", response_class=HTMLResponse)
def ask_get(q: Optional[str] = None):
    started = time.time()
    question = (q or "").strip()
    if not question:
        return HTMLResponse(render_card_html("Scrivi una domanda su prodotti e sistemi Tecnaria.", [], 0),
                            media_type="text/html; charset=utf-8")

    lang = detect_lang(question)
    web_hits = brave_search(question, lang=lang)       # 1) WEB
    pack = SINAPSI.apply(question, lang_hint=lang)     # 2) SINAPSI refine
    text, sources = compose_single_answer(question, lang, pack, web_hits)
    html_card = render_card_html(text, sources, int((time.time()-started)*1000))
    return HTMLResponse(html_card, media_type="text/html; charset=utf-8")

@app.post("/api/ask", response_class=JSONResponse)
def ask_post(payload: Dict[str, Any] = Body(...)):
    started = time.time()
    question = (payload.get("q") or "").strip()
    if not question:
        return JSONResponse({"ok": False, "error": "missing q"})
    lang = detect_lang(question)
    web_hits = brave_search(question, lang=lang)
    pack = SINAPSI.apply(question, lang_hint=lang)
    text, sources = compose_single_answer(question, lang, pack, web_hits)
    html_card = render_card_html(text, sources, int((time.time()-started)*1000))
    return JSONResponse({"ok": True, "html": html_card})
