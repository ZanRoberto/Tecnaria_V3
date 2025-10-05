# app.py — Tecnaria QA Bot (ITA only, robusto per demo/cliente)
# Migliorie chiave:
# - PRIORITA' REGOLE via campo "priority" nel JSON (più basso = più importante)
# - STRICT_ON_OVERRIDE: se c'è override, ignora il web → risposta sempre on-topic
# - WEB usato solo se qualità bozza ≥ soglia
# - Postscript ignorati
# - /selftest: verifica automatica di 4 FAQ critiche (PASS/FAIL)

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
MODE = "web_then_sinapsi_refine_single_it_priority"  # visibile su /health

app = FastAPI(title=APP_TITLE)

# -----------------------------
# Config
# -----------------------------
STATIC_DIR = os.environ.get("STATIC_DIR", "static")
SINAPSI_FILE = os.environ.get("SINAPSI_FILE", os.path.join(STATIC_DIR, "data", "sinapsi_rules.json"))

# Ricerca web (opzionale)
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "").strip()
ALLOWED_DOMAINS = json.loads(os.environ.get("ALLOWED_DOMAINS_JSON", '["tecnaria.com","spit.eu","spitpaslode.com"]'))
MIN_WEB_SCORE = float(os.environ.get("MIN_WEB_SCORE", "0.35"))
MAX_WEB_RESULTS = int(os.environ.get("MAX_WEB_RESULTS", "5"))
WEB_MIN_QUALITY = float(os.environ.get("WEB_MIN_QUALITY", "0.55"))

# Comportamento robusto
STRICT_ON_OVERRIDE = os.environ.get("STRICT_ON_OVERRIDE", "1") not in ("0", "false", "False", "")

# -----------------------------
# Static mount (se esiste)
# -----------------------------
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# -----------------------------
# Sinapsi loader (JSONC-tolerant) & engine con PRIORITY
# -----------------------------
def _jsonc_to_json(txt: str) -> str:
    """Rimuove BOM, commenti /*...*/, //... e virgole finali (JSONC → JSON)."""
    if not txt:
        return ""
    txt = txt.lstrip("\ufeff")
    txt = re.sub(r"/\*[\s\S]*?\*/", "", txt)          # /* ... */
    txt = re.sub(r"(^|\s)//[^\n\r]*", r"\1", txt)     # // ...
    txt = re.sub(r",\s*(\]|\})", r"\1", txt)          # trailing commas
    return txt.strip()

class Rule:
    def __init__(self, raw: Dict[str, Any]):
        self.raw = raw
        self.id = raw.get("id", "")
        self.pattern_str = raw.get("pattern", "(?s).*")
        self.pattern = re.compile(self.pattern_str, re.IGNORECASE | re.DOTALL)
        self.mode = (raw.get("mode", "augment") or "augment").lower().strip()  # override | augment | postscript
        self.lang = raw.get("lang", "it")
        self.answer = raw.get("answer", "") or ""
        self.sources = raw.get("sources", []) or []
        # PRIORITA': più basso = più importante (default 100)
        try:
            self.priority = int(raw.get("priority", 100))
        except Exception:
            self.priority = 100
        # Heuristica di specificità: lunghezza pattern (serve solo come tie-break)
        self.specificity = len(self.pattern_str or "")

class SinapsiEngine:
    def __init__(self, path: str):
        self.path = path
        self.rules: List[Rule] = []
        self.meta = {"count": 0, "path": path, "error": None}

    def load(self) -> int:
        if not os.path.exists(self.path):
            self.rules = []
            self.meta = {"count": 0, "path": self.path, "error": "file-not-found"}
            return 0
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = f.read()
            cleaned = _jsonc_to_json(raw)
            if not cleaned:
                self.rules = []
                self.meta = {"count": 0, "path": self.path, "error": "empty-file"}
                return 0
            data = json.loads(cleaned)
            rules_raw = data["rules"] if isinstance(data, dict) and "rules" in data else data
            self.rules = [Rule(r) for r in rules_raw]
            # Ordine di scansione predefinito: priorità crescente, poi override prima di augment, poi specificità desc
            self.rules.sort(key=lambda r: (r.priority, 0 if r.mode=="override" else 1, -r.specificity))
            self.meta = {"count": len(self.rules), "path": self.path, "error": None}
            return len(self.rules)
        except Exception as e:
            self.rules = []
            self.meta = {"count": 0, "path": self.path, "error": f"parse-failed: {type(e).__name__}"}
            return 0

    def apply(self, question: str) -> Dict[str, Any]:
        """Ritorna override/augments/sources. I postscript sono ignorati."""
        q = question or ""
        chosen_override: Optional[Rule] = None
        augments: List[str] = []
        srcs: List[Dict[str, str]] = []

        for rule in self.rules:
            if not rule.pattern.search(q):
                continue
            if rule.mode == "override":
                if chosen_override is None:
                    chosen_override = rule  # grazie all'ordinamento, è la migliore
                    if rule.sources: srcs.extend(rule.sources)
            elif rule.mode == "augment":
                if rule.answer:
                    augments.append(rule.answer)
                if rule.sources:
                    srcs.extend(rule.sources)
            # postscript ignorati

        return {
            "override": chosen_override.answer if chosen_override else None,
            "augments": augments,
            "sources": srcs,
            "override_id": chosen_override.id if chosen_override else None,
            "override_priority": chosen_override.priority if chosen_override else None
        }

SINAPSI = SinapsiEngine(SINAPSI_FILE)
SINAPSI.load()  # precaricato all'avvio

# -----------------------------
# Utilità
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

# -----------------------------
# Web search (Brave) — se disponibile
# -----------------------------
def brave_search(query: str) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        return []
    try:
        url = "https://api.search.brave.com/res/v1/web/search"
        headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
        params = {"q": query, "country": "it", "source": "web", "count": 10, "freshness": "month"}
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
    len_term = min(total_len / 900.0, 1.0)
    host_term = min(uniq_hosts / 3.0, 1.0)
    return 0.45*n_term + 0.40*len_term + 0.15*host_term

# -----------------------------
# Composer — produce UN SOLO TESTO (in ITA)
# -----------------------------
def compose_single_answer(question: str, sinapsi_pack: Dict[str, Any],
                          web_hits: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, str]]]:
    """Ritorna (testo_ITA, sources)."""
    sources: List[Dict[str, str]] = []
    override = sinapsi_pack.get("override")
    augments: List[str] = sinapsi_pack.get("augments", [])
    web_quality = score_web_quality(web_hits)

    parts: List[str] = []

    # Se ho un override e la modalità è "strict", NON mischio il web (evito rumore/divagazioni).
    if not (STRICT_ON_OVERRIDE and override):
        if web_quality >= WEB_MIN_QUALITY and web_hits:
            picked = [h for h in web_hits if h.get("snippet")]
            picked = picked[:3]
            if picked:
                parts.append(" ".join(h["snippet"] for h in picked))
            else:
                parts.append("Informazioni ufficiali reperite dai siti consentiti.")
            for h in web_hits:
                sources.append({"title": h.get("title") or h.get("url","Fonte"), "url": h.get("url","")})

    if override:
        parts.append(override.strip())
    if augments:
        parts.append(" ".join(a.strip() for a in augments if a and a.strip()))

    text_it = " ".join([p for p in parts if p]).strip() or \
              "Non ho trovato elementi sufficienti su domini autorizzati o nelle regole. Raffina la domanda o aggiorna le regole."
    text_it = re.sub(r"\s+", " ", text_it).strip()
    return text_it, sources

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
        "behavior": {
            "strict_on_override": STRICT_ON_OVERRIDE
        },
        "critici": {
            "dir": STATIC_DIR,
            "sinapsi_file": SINAPSI_FILE,
            "sinapsi_loaded": SINAPSI.meta.get("count", 0),
            "sinapsi_error": SINAPSI.meta.get("error")
        }
    })

@app.get("/ask", response_class=HTMLResponse)
def ask_get(q: Optional[str] = None):
    started = time.time()
    question = (q or "").strip()
    if not question:
        return HTMLResponse(render_card_html("Scrivi una domanda su prodotti e sistemi Tecnaria.", [], 0),
                            media_type="text/html; charset=utf-8")

    web_hits = brave_search(question)      # 1) WEB (se attivo)
    pack = SINAPSI.apply(question)         # 2) SINAPSI refine (priorità)
    text_it, sources = compose_single_answer(question, pack, web_hits)
    html_card = render_card_html(text_it, sources, int((time.time()-started)*1000))
    return HTMLResponse(html_card, media_type="text/html; charset=utf-8")

@app.post("/api/ask", response_class=JSONResponse)
def ask_post(payload: Dict[str, Any] = Body(...)):
    started = time.time()
    question = (payload.get("q") or "").strip()
    if not question:
        return JSONResponse({"ok": False, "error": "missing q"})
    web_hits = brave_search(question)
    pack = SINAPSI.apply(question)
    text_it, sources = compose_single_answer(question, pack, web_hits)
    html_card = render_card_html(text_it, sources, int((time.time()-started)*1000))
    return JSONResponse({"ok": True, "html": html_card})

# -----------------------------
# Self-test (per demo/cliente)
# -----------------------------
def _contains_any(text: str, needles: List[str]) -> bool:
    t = (text or "").lower()
    return any(n.lower() in t for n in needles)

@app.get("/selftest", response_class=JSONResponse)
def selftest():
    """Esegue 4 test chiave senza web (deterministici) e ritorna PASS/FAIL."""
    tests = [
        {
            "name": "P560 - non qualsiasi chiodatrice",
            "q": "posso usare una normale chiodatrice a sparo per i CTF?",
            "expect_any": ["non con una chiodatrice qualsiasi", "solo SPIT P560", "2 chiodi", "HSBR14"]
        },
        {
            "name": "CTF vs Diapason",
            "q": "Differenza CTF e Diapason",
            "expect_any": ["CTF", "Diapason", "lamiera grecata", "laterocemento"]
        },
        {
            "name": "Densità CTF",
            "q": "Quanti CTF al m2",
            "expect_any": ["6–8", "6-8", "6 — 8", "connettori/m²"]
        },
        {
            "name": "P560 overview",
            "q": "p560",
            "expect_any": ["chiodatrice", "SPIT P560", "due chiodi", "HSBR14"]
        }
    ]

    out = []
    for t in tests:
        pack = SINAPSI.apply(t["q"])
        # Forziamo web vuoto per evitare rumore esterno
        ans, _ = compose_single_answer(t["q"], pack, web_hits=[])
        ok = _contains_any(ans, t["expect_any"])
        out.append({"test": t["name"], "query": t["q"], "pass": ok, "answer": ans[:220] + ("…" if len(ans) > 220 else "")})

    return JSONResponse({"ok": all(x["pass"] for x in out), "results": out, "rules_loaded": SINAPSI.meta.get("count", 0)})
