import os
import re
import json
import time
import html
from typing import List, Dict, Any, Optional, Tuple

import requests
from fastapi import FastAPI, Request, Body
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

APP_TITLE = "Tecnaria QA Bot"
app = FastAPI(title=APP_TITLE)

# -----------------------------
# Config
# -----------------------------
STATIC_DIR = os.environ.get("STATIC_DIR", "static")
SINAPSI_FILE = os.environ.get("SINAPSI_FILE", os.path.join(STATIC_DIR, "sinapsi_rules.json"))
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
ALLOWED_DOMAINS = json.loads(os.environ.get("ALLOWED_DOMAINS_JSON", '["tecnaria.com","spit.eu","spitpaslode.com"]'))
MIN_WEB_SCORE = float(os.environ.get("MIN_WEB_SCORE", "0.35"))

# -----------------------------
# Static mount (se esiste)
# -----------------------------
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# -----------------------------
# Sinapsi loader & engine
# -----------------------------
class Rule:
    def __init__(self, rid: str, pattern: str, mode: str, lang: str, answer: str, sources: Optional[List[Dict[str, str]]] = None):
        self.id = rid
        self.pattern_str = pattern
        self.pattern = re.compile(pattern, re.IGNORECASE | re.DOTALL)
        self.mode = mode.lower().strip()  # override | augment | postscript
        self.lang = lang
        self.answer = answer
        self.sources = sources or []

class SinapsiEngine:
    def __init__(self, path: str):
        self.path = path
        self.rules: List[Rule] = []
        self.meta = {}

    def load(self) -> int:
        if not os.path.exists(self.path):
            self.rules = []
            return 0
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        rules_raw = data["rules"] if isinstance(data, dict) and "rules" in data else data
        loaded = []
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
        self.meta = {"count": len(self.rules)}
        return len(self.rules)

    def apply(self, question: str, lang_hint: str = "it") -> Dict[str, Any]:
        """
        Ritorna:
        {
          "override": str|None,
          "augments": [str],
          "postscripts": [str],
          "sources": [ {title,url}, ... ]  # dei blocchi sinapsi usati
        }
        """
        ov: Optional[str] = None
        aug: List[str] = []
        ps: List[str] = []
        srcs: List[Dict[str,str]] = []
        for rule in self.rules:
            if rule.pattern.search(question or ""):
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
SINAPSI_COUNT = SINAPSI.load()  # pre-warm all'avvio


# -----------------------------
# Utilità
# -----------------------------
NOISE_PATTERNS = re.compile(
    r"(just a moment|checking your browser|questo sito utilizza i cookie|cookie policy|"
    r"%PDF-|stream x��|base64,|consenso cookie|enable javascript)",
    re.IGNORECASE
)

def is_allowed_domain(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        netloc = urlparse(url).netloc.lower()
        return any(netloc.endswith(d) for d in ALLOWED_DOMAINS)
    except Exception:
        return False

def clean_snippet(text: str) -> str:
    if not text:
        return ""
    t = html.unescape(text)
    # rimuovi rumore noto
    if NOISE_PATTERNS.search(t):
        return ""
    # elimina eccessi di whitespace
    t = re.sub(r"\s+", " ", t).strip()
    return t

def detect_lang(question: str) -> str:
    q = (question or "").lower()
    # euristica minimale: IT default
    if re.search(r"[äöüß]|(der|die|das|und|ist)\b", q):
        return "de"
    if re.search(r"\b(the|and|what|how|when|which|sheet|manual)\b", q):
        return "en"
    if re.search(r"\b(que|cómo|cuál|cuando|ventajas)\b", q):
        return "es"
    if re.search(r"\b(quels|comment|avantages|quand)\b", q):
        return "fr"
    return "it"

# -----------------------------
# Web search (Brave) opzionale
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
            "count": 7,
            "freshness": "month"
        }
        r = requests.get(url, headers=headers, params=params, timeout=8)
        r.raise_for_status()
        data = r.json()
        web = data.get("web", {})
        results = []
        for item in web.get("results", []):
            url_i = item.get("url", "")
            if not is_allowed_domain(url_i):
                continue
            score = float(item.get("properties", {}).get("score", 0.0))
            if score < MIN_WEB_SCORE:
                continue
            title = clean_snippet(item.get("title", ""))
            snippet = clean_snippet(item.get("description", "")) or clean_snippet(item.get("snippet", ""))
            if not title and not snippet:
                continue
            results.append({"title": title or url_i, "url": url_i, "snippet": snippet})
        return results[:5]
    except Exception:
        return []


# -----------------------------
# Composer narrativo + HTML
# -----------------------------
def compose_narrative(question: str, lang: str, sinapsi_pack: Dict[str, Any], web_hits: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str,str]]]:
    """
    Restituisce (testo_principale, sources)
    Logica:
      - se esiste un override → usalo come corpo (narrativo già pronto)
      - altrimenti crea narrativa da web_hits (allowed domains) + eventuali augment/postscript
    """
    used_sources: List[Dict[str,str]] = []

    override = sinapsi_pack.get("override")
    augments: List[str] = sinapsi_pack.get("augments", [])
    postscripts: List[str] = sinapsi_pack.get("postscripts", [])
    sinapsi_sources = sinapsi_pack.get("sources", [])

    if override:
        body = override.strip()
        used_sources.extend(sinapsi_sources)
        # chiudiamo con un’eventuale riga di stile
        return (body, used_sources)

    # Se non c'è override, proviamo a costruire una risposta da web + augment/postscript
    paragraphs: List[str] = []

    # Sintesi web (solo allowed domains)
    if web_hits:
        # Proviamo a tessere 4-5 frasi in modo narrativo
        bullets = []
        for h in web_hits[:3]:
            if h.get("snippet"):
                bullets.append(h["snippet"])
        if bullets:
            if lang == "it":
                intro = "Ecco una sintesi tecnico-commerciale dai contenuti ufficiali:"
            elif lang == "en":
                intro = "Here is a technical-commercial summary from official materials:"
            elif lang == "de":
                intro = "Technisch-kaufmännische Zusammenfassung aus offiziellen Unterlagen:"
            else:
                intro = "Sintesi dai contenuti ufficiali:"
            paragraphs.append(intro + " " + " ".join(bullets))

        # raccogliamo fonti
        for h in web_hits:
            used_sources.append({"title": h.get("title") or h.get("url","Fonte"), "url": h.get("url","")})

    # Augment: frasi additive
    for a in augments:
        if a and a not in paragraphs:
            paragraphs.append(a.strip())

    # Postscript: stile/note
    for p in postscripts:
        if p:
            paragraphs.append(p.strip())

    if not paragraphs:
        # fallback minimo
        paragraphs.append("Ho raccolto le informazioni pertinenti dai materiali Tecnaria e partner tecnici consentiti, in base alla tua domanda.")

    return ("\n\n".join(paragraphs), used_sources + sinapsi_sources)


def render_sources_html(sources: List[Dict[str, str]]) -> str:
    if not sources:
        return ""
    # Dedup per URL
    seen = set()
    items = []
    for s in sources:
        url = s.get("url","").strip()
        title = s.get("title","Fonte").strip() or "Fonte"
        if not url or url in seen:
            continue
        seen.add(url)
        items.append(f"📎 <a href='{html.escape(url)}' target='_blank'>{html.escape(title)}</a>")
    if not items:
        return ""
    return "<div class='sources'><strong>Fonti</strong><br>" + "<br>".join(items) + "</div>"


def render_card_html(body_text: str, sources: List[Dict[str,str]], elapsed_ms: int, subtitle: str = "Risposta Tecnaria") -> str:
    # Escaping conservativo: body_text è contenuto curato (può contenere link già formattati dagli override)
    safe_body = body_text.replace("\n\n", "</p><p>").replace("\n", "<br>")
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
    return HTMLResponse(
        "<pre>{\"ok\":true,\"msg\":\"Use /ask or place static/index.html\"}</pre>",
        media_type="text/html; charset=utf-8"
    )


@app.get("/health", response_class=JSONResponse)
def health():
    return JSONResponse({
        "status": "ok",
        "web_search": {
            "provider": "brave",
            "enabled": bool(BRAVE_API_KEY),
            "preferred_domains": ALLOWED_DOMAINS,
            "min_web_score": MIN_WEB_SCORE
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
        return HTMLResponse(render_card_html("Scrivi una domanda su prodotti e sistemi Tecnaria.", [], 0), media_type="text/html; charset=utf-8")

    lang = detect_lang(question)
    pack = SINAPSI.apply(question, lang_hint=lang)
    web_hits = []
    # Solo se NON c'è override usiamo la ricerca (filtrata)
    if not pack.get("override"):
        web_hits = brave_search(question, lang=lang)

    body, sources = compose_narrative(question, lang, pack, web_hits)
    html_card = render_card_html(body, sources, int((time.time()-started)*1000))
    return HTMLResponse(html_card, media_type="text/html; charset=utf-8")


@app.post("/api/ask", response_class=JSONResponse)
def ask_post(payload: Dict[str, Any] = Body(...)):
    started = time.time()
    question = (payload.get("q") or "").strip()
    if not question:
        return JSONResponse({"ok": False, "error": "missing q"})

    lang = detect_lang(question)
    pack = SINAPSI.apply(question, lang_hint=lang)
    web_hits = []
    if not pack.get("override"):
        web_hits = brave_search(question, lang=lang)

    body, sources = compose_narrative(question, lang, pack, web_hits)
    html_card = render_card_html(body, sources, int((time.time()-started)*1000))
    return JSONResponse({"ok": True, "html": html_card})
