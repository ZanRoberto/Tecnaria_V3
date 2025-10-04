# app.py
import os
import re
import json
import html
import logging
from typing import List, Dict, Any, Optional, Tuple

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import requests

# ------------------------------
# Config & logging
# ------------------------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tecnaria-bot")

PORT = int(os.environ.get("PORT", "10000"))

STATIC_DIR = os.environ.get("STATIC_DIR", "static")
TEMPLATES_DIR = os.environ.get("TEMPLATES_DIR", "templates")  # per retro-compatibilità
CRITICI_DIR = os.environ.get("CRITICI_DIR", os.path.join(STATIC_DIR, "data"))

SINAPSI_FILE = os.environ.get("SINAPSI_FILE",
                              os.path.join(CRITICI_DIR, "sinapsi_rules_narrative.json"))
BRAVE_KEY = os.environ.get("BRAVE_API_KEY", "").strip()
SEARCH_PROVIDER = "brave" if BRAVE_KEY else "disabled"

PREFERRED_DOMAINS = os.environ.get("PREFERRED_DOMAINS",
                                   "tecnaria.com,spit.eu,spitpaslode.com")
PREFERRED_DOMAINS = [d.strip().lower() for d in PREFERRED_DOMAINS.split(",") if d.strip()]

MIN_WEB_RESULTS = int(os.environ.get("MIN_WEB_RESULTS", "3"))

# ------------------------------
# App init
# ------------------------------
app = FastAPI(title="Tecnaria QA Bot")

# static mount (serve index.html se presente)
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ------------------------------
# Utilities
# ------------------------------
_NON_WORD_JUNK = re.compile(r"[^A-Za-zÀ-ÖØ-öø-ÿ0-9\s\.\,\:\;\-\+\(\)\[\]\'\"\/\&]+")
_IS_PDF = re.compile(r"\.pdf(\?|$)", re.IGNORECASE)
_HTTP = re.compile(r"^https?://", re.IGNORECASE)

def clean_snippet(text: str) -> str:
    """Ripulisce snippet/estratti: rimuove blocchi binari o prefissi PDF."""
    if not text:
        return ""
    if "%PDF" in text or "obj <<" in text:
        # è un dump di PDF → scartiamo lo snippet
        return ""
    # limita ripetizioni e caratteri strani
    txt = _NON_WORD_JUNK.sub(" ", text)
    txt = re.sub(r"\s{2,}", " ", txt).strip()
    return html.escape(txt)

def a_link(url: str, label: str) -> str:
    if not _HTTP.search(url):
        return ""
    safe = html.escape(label)
    href = html.escape(url)
    return f"📎 <a href='{href}' target='_blank'>{safe}</a>"

def detect_lang(q: str) -> str:
    # semplice euristica: se contiene molte parole italiane → it, se molte inglesi → en
    it_hits = len(re.findall(r"\b(che|come|quando|quanti|differenza|serve|posare|lamiera|connettore|soletta)\b", q, re.I))
    en_hits = len(re.findall(r"\b(what|how|when|difference|need|install|sheet|connector|slab)\b", q, re.I))
    if it_hits >= en_hits:
        return "it"
    return "en"

# ------------------------------
# Sinapsi preload & match
# ------------------------------
class Rule:
    def __init__(self, rule: Dict[str, Any]):
        self.id = rule.get("id") or ""
        self.pattern_raw = rule.get("pattern") or ".*"
        self.pattern = re.compile(self.pattern_raw, re.IGNORECASE | re.DOTALL)
        self.mode = rule.get("mode", "augment")  # override | augment | postscript
        self.lang = rule.get("lang", "it")
        self.answer = rule.get("answer", "")
        # se l'answer è markdown-ish, lo trattiamo già come HTML semplice
        # qui assumiamo che sinapsi_narrative fornisca <p>, <h3>, <ul> ecc.
    def matches(self, q: str) -> bool:
        return bool(self.pattern.search(q))

class Sinapsi:
    def __init__(self):
        self.rules: List[Rule] = []

    def load(self, path: str) -> int:
        self.rules = []
        if not os.path.isfile(path):
            log.warning("Sinapsi file not found: %s", path)
            return 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for r in data:
                try:
                    self.rules.append(Rule(r))
                except re.error as e:
                    log.error("Regex error in rule %s: %s", r.get("id"), e)
            log.info("Loaded %d Sinapsi rules from %s", len(self.rules), path)
            return len(self.rules)
        except Exception as e:
            log.exception("Failed to load sinapsi: %s", e)
            return 0

    def apply(self, q: str, lang: str) -> Tuple[Optional[Rule], List[Rule], List[Rule]]:
        """Ritorna (override, augments, postscripts) in quest'ordine."""
        override = None
        augments, posts = [], []
        for r in self.rules:
            if r.lang != lang and r.lang != "any":
                continue
            if r.matches(q):
                if r.mode == "override" and override is None:
                    override = r
                elif r.mode == "augment":
                    augments.append(r)
                elif r.mode == "postscript":
                    posts.append(r)
        return override, augments, posts

S = Sinapsi()
SINAPSI_COUNT = S.load(SINAPSI_FILE)

# ------------------------------
# Web search (Brave)
# ------------------------------
def brave_search(q: str, count: int = 6) -> List[Dict[str, str]]:
    if not BRAVE_KEY:
        return []
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": BRAVE_KEY},
            params={"q": q, "count": max(3, count), "search_lang": "it"},
            timeout=6
        )
        r.raise_for_status()
        data = r.json()
        items = []
        for it in (data.get("web", {}) or {}).get("results", []):
            url = it.get("url", "")
            if not url:
                continue
            # privilegia i domini preferiti
            domain_ok = any(d in url.lower() for d in PREFERRED_DOMAINS)
            title = it.get("title") or url
            snippet = clean_snippet(it.get("description") or "")
            items.append({"url": url, "title": title, "snippet": snippet, "preferred": domain_ok})
        # ordina preferiti on top, poi no-pdf prima dei pdf
        items.sort(key=lambda x: (not x["preferred"], bool(_IS_PDF.search(x["url"]))))
        # rimuovi snippet se PDF (così evitiamo sporcizia)
        for it in items:
            if _IS_PDF.search(it["url"]):
                it["snippet"] = ""
        return items[:count]
    except Exception as e:
        log.warning("Brave search failed: %s", e)
        return []

# ------------------------------
# Synthesis / Rendering
# ------------------------------
def synthesize_web_html(q: str, items: List[Dict[str, str]], lang: str) -> str:
    if not items:
        return ""
    # Narrative breve + fonti cliccabili
    if lang == "it":
        head = "<h3>Risposta sintetica</h3>"
        why = "<p>Ho cercato nei contenuti ufficiali Tecnaria (e partner) e ho selezionato le fonti più pertinenti.</p>"
        ftitle = "<h4>Fonti</h4>"
    else:
        head = "<h3>Brief answer</h3>"
        why = "<p>I checked official Tecnaria sources (and partners) and selected the most relevant references.</p>"
        ftitle = "<h4>Sources</h4>"

    links = []
    for it in items:
        label = it["title"] or it["url"]
        href = it["url"]
        link = a_link(href, label)
        if it["snippet"]:
            links.append(f"{link}<br><span class='src-note'>{it['snippet']}</span>")
        else:
            links.append(f"{link}")

    return f"""
<div class="answer">
  {head}
  {why}
  <div class="spacer"></div>
  {ftitle}
  <div class="sources">
    {'<br>'.join(links)}
  </div>
</div>
""".strip()

def render_final_html(q: str, lang: str,
                      web_html: str,
                      override: Optional[Rule],
                      augments: List[Rule],
                      posts: List[Rule]) -> str:
    # override → risposta ufficiale e basta (con eventuali PS)
    if override:
        body = f"<div class='answer'>{override.answer}</div>"
        if posts:
            tail = "".join(f"<div class='ps'>{p.answer}</div>" for p in posts)
            body += tail
        return wrap_html(body)

    # altrimenti costruisci: web → augment → postscript
    parts = []
    if web_html:
        parts.append(web_html)
    for a in augments:
        parts.append(f"<div class='augment'>{a.answer}</div>")
    for p in posts:
        parts.append(f"<div class='ps'>{p.answer}</div>")
    if not parts:
        # fallback super-essenziale
        if lang == "it":
            parts.append("<p>Non ho trovato una risposta diretta nelle fonti preferite. Prova a riformulare o indicami maggiori dettagli.</p>")
        else:
            parts.append("<p>I couldn’t find a direct answer in the preferred sources. Try rephrasing or provide more details.</p>")
    return wrap_html("\n".join(parts))

def wrap_html(inner: str) -> str:
    # stile sobrio “Tecnaria-like”
    return f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Tecnaria QA</title>
<style>
:root {{
  --fg:#1c1c1c; --muted:#5a5a5a; --brand:#ff6a00; --bg:#f8f8f8; --card:#ffffff;
}}
html,body {{ margin:0; padding:0; font-family: Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; color:var(--fg); background:var(--bg); }}
.wrap {{ max-width: 980px; margin: 0 auto; padding: 24px; }}
.card {{ background:var(--card); border:1px solid #eee; border-radius:14px; padding:20px 22px; box-shadow:0 1px 2px rgba(0,0,0,.04); }}
h3 {{ margin:0 0 8px; font-weight:700; font-size:20px; }}
h4 {{ margin:18px 0 6px; font-weight:700; font-size:16px; }}
p {{ line-height:1.5; margin:10px 0; }}
.sources a {{ text-decoration:none; border-bottom:1px solid rgba(255,106,0,.35); }}
.sources a:hover {{ background:rgba(255,106,0,.08); }}
.src-note {{ display:block; color:var(--muted); font-size:14px; margin:4px 0 10px; }}
.augment {{ border-left:3px solid var(--brand); padding-left:12px; margin:14px 0; }}
.ps {{ color:var(--muted); font-size:14px; margin-top:10px; }}
.spacer {{ height:8px; }}
</style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      {inner}
    </div>
  </div>
</body>
</html>"""

# ------------------------------
# API Models
# ------------------------------
class AskBody(BaseModel):
    q: str

# ------------------------------
# Routes
# ------------------------------
@app.get("/ping")
def ping():
    return {"ok": True, "pong": True}

@app.get("/health")
def health():
    return {
        "status": "ok",
        "web_search": {
            "provider": SEARCH_PROVIDER,
            "brave_key": bool(BRAVE_KEY),
            "preferred_domains": PREFERRED_DOMAINS
        },
        "critici": {
            "dir": CRITICI_DIR,
            "exists": os.path.isdir(CRITICI_DIR),
            "sinapsi_file": SINAPSI_FILE,
            "sinapsi_loaded": SINAPSI_COUNT
        }
    }

@app.get("/", response_class=HTMLResponse)
def home():
    # serve static/index.html se presente, altrimenti messaggio minimo
    idx = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(idx):
        return FileResponse(idx)
    return HTMLResponse("<pre>{\"ok\":true,\"msg\":\"Use /ask or place static/index.html\"}</pre>")

def answer_core(q: str) -> str:
    lang = detect_lang(q)
    # 1) Sinapsi
    override, augments, posts = S.apply(q, lang)
    # override? → rendi subito
    if override:
        return render_final_html(q, lang, "", override, [], posts)

    # 2) Web (se disponibile)
    items = brave_search(q, count=6) if BRAVE_KEY else []
    web_html = synthesize_web_html(q, items, lang)

    # 3) Integra augment/postscript
    return render_final_html(q, lang, web_html, None, augments, posts)

@app.get("/ask", response_class=HTMLResponse)
def ask_get(q: str = Query(..., description="Domanda")):
    html_answer = answer_core(q.strip())
    return HTMLResponse(html_answer)

@app.post("/api/ask")
def ask_post(body: AskBody):
    q = (body.q or "").strip()
    if not q:
        return JSONResponse({"ok": True, "answer": "<p>Domanda vuota: inserisci una richiesta valida.</p>"})
    html_answer = answer_core(q)
    return JSONResponse({"ok": True, "answer": html_answer})

# ------------------------------
# Avvio (solo per esecuzione locale)
# ------------------------------
if __name__ == "__main__":
    import uvicorn
    log.info("[BOOT] WEB: %s; CRITICI_DIR=%s; SINAPSI=%s", SEARCH_PROVIDER, CRITICI_DIR, SINAPSI_FILE)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
