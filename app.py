import os
import json
import re
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

import requests
from fastapi import FastAPI, Body
from fastapi.responses import JSONResponse

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
class Config:
    MODE = os.getenv("MODE", "web_then_sinapsi_refine_single_it_priority")
    STATIC_DIR = os.getenv("STATIC_DIR", "static")
    SINAPSI_PATH = os.getenv("SINAPSI_PATH", os.path.join(STATIC_DIR, "data", "sinapsi_rules.json"))

    # Web search (Brave)
    WEB_ENABLED = os.getenv("WEB_ENABLED", "true").lower() == "true"
    WEB_PROVIDER = os.getenv("WEB_PROVIDER", "brave")
    BRAVE_TOKEN = os.getenv("BRAVE_TOKEN", os.getenv("BRAVE_API_KEY", ""))  # support both names
    PREFERRED_DOMAINS = os.getenv("PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com")
    MIN_WEB_SCORE = float(os.getenv("MIN_WEB_SCORE", "0.35"))
    MIN_WEB_QUALITY = float(os.getenv("MIN_WEB_QUALITY", "0.55"))

    # Behavior
    STRICT_ON_OVERRIDE = os.getenv("STRICT_ON_OVERRIDE", "true").lower() == "true"


cfg = Config()

# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------
def strip_accents(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))

def normalize_text(q: str) -> str:
    """
    Normalizzazione robusta e SICURA (niente stringhe lasciate aperte).
    """
    s = q.lower()
    s = strip_accents(s)
    s = re.sub(r'[“”\"\'`´]', '"', s)
    s = re.sub(r"[’‘]", "'", s)
    s = re.sub(r'\s+', ' ', s).strip()

    # sinonimi/varianti
    mappings: List[Tuple[str, str]] = [
        (r'\bspit\s*p560\b', 'p560'),
        (r'\bp560\b', 'p560'),
        (r'\bhsbr-?14\b', 'hsbr14'),
        (r'\bsparachiod\w*\b', 'chiodatrice'),            # "sparachiodi"
        (r'\bpistola(?:\s+a)?\s*sparo\b', 'chiodatrice'), # "pistola a sparo"
        (r'\b(sparo|tiro\s*indiretto)\b', 'chiodatrice'),
        (r'\bchiodatrice\b', 'chiodatrice'),

        (r'\bconnettori?\s*ctf\b', 'ctf'),
        (r'\bconnettori?\s*ctl\b', 'ctl'),
        (r'\bv\s*cem-?e\b', 'v cem-e'),
        (r'\bmini\s*cem-?e\b', 'mini cem-e'),
        (r'\bdiapason\b', 'diapason'),

        (r'm²', 'm2'),
        (r'\bmq\b', 'm2'),
    ]
    for pat, rep in mappings:
        s = re.sub(pat, rep, s)
    return s


# -----------------------------------------------------------------------------
# Sinapsi rules
# -----------------------------------------------------------------------------
class Sinapsi:
    def __init__(self, path: str):
        self.path = path
        self.rules: List[Dict[str, Any]] = []
        self.error: Optional[str] = None
        self.load()

    def load(self):
        self.rules = []
        self.error = None
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # data può essere direttamente {"rules":[...]} o già la lista
            rules = data.get("rules") if isinstance(data, dict) else data
            if not isinstance(rules, list):
                raise ValueError("Formato sinapsi_rules.json non valido: atteso array rules")
            # normalizza campi minimi
            for r in rules:
                r.setdefault("priority", 0)
                r.setdefault("mode", "override")
                r.setdefault("lang", "it")
                r.setdefault("answer", "")
                r.setdefault("pattern", "")
            # ordina per priority desc
            self.rules = sorted(rules, key=lambda x: int(x.get("priority", 0)), reverse=True)
        except Exception as e:
            self.error = str(e)
            self.rules = []

    def match(self, q: str) -> Optional[Dict[str, Any]]:
        """
        Ritorna la prima regola (priorità più alta) che fa match sul testo normalizzato q.
        I pattern spesso includono (?i); non forziamo IGNORECASE per non interferire.
        """
        for r in self.rules:
            pat = r.get("pattern", "")
            if not pat:
                continue
            try:
                if re.search(pat, q):
                    return r
            except re.error:
                # pattern malformato: salta
                continue
        return None


sinapsi = Sinapsi(cfg.SINAPSI_PATH)

# -----------------------------------------------------------------------------
# Web search (Brave) opzionale
# -----------------------------------------------------------------------------
def brave_search(query: str, n: int = 5) -> List[Dict[str, Any]]:
    """
    Ritorna lista semplificata di risultati Brave:
    [{url, title, snippet, domain, score, language, derived_score}...]
    Filtra e calcola un punteggio grezzo in base a preferred domains / soglie.
    """
    if not cfg.WEB_ENABLED or cfg.WEB_PROVIDER != "brave" or not cfg.BRAVE_TOKEN:
        return []

    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"X-Subscription-Token": cfg.BRAVE_TOKEN}
    params = {"q": query, "count": n, "country": "it", "search_lang": "it", "freshness": "year"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    items = []
    preferred = [d.strip().lower() for d in cfg.PREFERRED_DOMAINS.split(",") if d.strip()]
    web_results = (data.get("web", {}) or {}).get("results", []) or []
    for r in web_results:
        u = r.get("url", "")
        d = r.get("meta_url", {}).get("hostname", "") or r.get("source", "")
        s = r.get("meta", {}).get("score", 0.0)
        q = r.get("meta", {}).get("quality", 0.0)
        title = r.get("title", "")
        snippet = r.get("description", "") or r.get("snippet", "")
        lang = r.get("language", "")

        dom_ok = any(d.endswith(p) or d == p for p in preferred) if preferred else True
        score_ok = (s or 0) >= cfg.MIN_WEB_SCORE
        qual_ok = (q or 0) >= cfg.MIN_WEB_QUALITY

        derived = 0.0
        if dom_ok:
            derived += 0.6
        if score_ok:
            derived += 0.25
        if qual_ok:
            derived += 0.15

        items.append({
            "url": u, "title": title, "snippet": snippet,
            "domain": d, "score": s, "quality": q, "language": lang,
            "derived_score": derived, "preferred_domain": dom_ok
        })

    # ordina per derived_score desc
    items.sort(key=lambda x: x["derived_score"], reverse=True)
    return items


def summarize_results(items: List[Dict[str, Any]]) -> Optional[str]:
    """
    Semplice sommario: prende il migliore da dominio preferito e usa il suo snippet.
    Se non trova, None.
    """
    for it in items:
        if it.get("preferred_domain"):
            sn = it.get("snippet", "")
            if sn:
                return sn
    return None


# -----------------------------------------------------------------------------
# FastAPI
# -----------------------------------------------------------------------------
app = FastAPI(title="Tecnaria_V3 API", version="3.0")

def html_card(text: str, ms: int) -> str:
    return f"""
    <div class="card">
      <h2>Risposta Tecnaria</h2>
      <p>{text}</p>
      
      <p><small>⏱ {ms} ms</small></p>
    </div>
    """.strip()


@app.get("/health")
def health():
    # rileggi regole se file cambiato (stateless semplice)
    # (Se vuoi hot-reload più aggressivo puoi sempre fare sinapsi.load() qui)
    loaded = len(sinapsi.rules)
    return JSONResponse({
        "status": "ok",
        "mode": cfg.MODE,
        "web_search": {
            "provider": cfg.WEB_PROVIDER,
            "enabled": cfg.WEB_ENABLED,
            "preferred_domains": [d.strip() for d in cfg.PREFERRED_DOMAINS.split(",") if d.strip()],
            "min_web_score": cfg.MIN_WEB_SCORE,
            "web_min_quality": cfg.MIN_WEB_QUALITY,
        },
        "behavior": {
            "strict_on_override": cfg.STRICT_ON_OVERRIDE
        },
        "critici": {
            "dir": cfg.STATIC_DIR,
            "sinapsi_file": cfg.SINAPSI_PATH,
            "sinapsi_loaded": loaded,
            "sinapsi_error": sinapsi.error
        }
    })


@app.post("/api/ask")
def api_ask(payload: Dict[str, Any] = Body(...)):
    t0 = time.perf_counter()
    q = str(payload.get("q", "")).strip()
    if not q:
        ms = int((time.perf_counter() - t0) * 1000)
        return JSONResponse({"ok": True, "html": html_card("Domanda vuota.", ms)})

    # Normalizzazione
    q_norm = normalize_text(q)

    # 1) Match regole (priorità alta = prima)
    rule = sinapsi.match(q_norm)
    if rule and cfg.STRICT_ON_OVERRIDE and rule.get("mode", "override") == "override":
        ans = rule.get("answer", "").strip() or "—"
        ms = int((time.perf_counter() - t0) * 1000)
        return JSONResponse({"ok": True, "html": html_card(ans, ms)})

    # 2) Web (se attivo) — molto semplice: usa snippet del best preferred-domain
    web_ans = None
    if cfg.WEB_ENABLED and cfg.BRAVE_TOKEN and cfg.WEB_PROVIDER == "brave":
        items = brave_search(q, n=5)
        web_ans = summarize_results(items)

    # 3) Se c'è una regola (non strict) e una bozza web, puoi “affinare” (qui manteniamo semplice)
    if rule and not cfg.STRICT_ON_OVERRIDE:
        base = rule.get("answer", "").strip()
        if web_ans:
            answer = f"{base} {web_ans}"
        else:
            answer = base or "—"
        ms = int((time.perf_counter() - t0) * 1000)
        return JSONResponse({"ok": True, "html": html_card(answer, ms)})

    # 4) Se non c'è regola valida ma il web ha dato qualcosa
    if web_ans:
        ms = int((time.perf_counter() - t0) * 1000)
        return JSONResponse({"ok": True, "html": html_card(web_ans, ms)})

    # 5) Fallback
    fallback = "Non ho trovato elementi sufficienti su domini autorizzati o nelle regole. Raffina la domanda o aggiorna le regole."
    ms = int((time.perf_counter() - t0) * 1000)
    return JSONResponse({"ok": True, "html": html_card(fallback, ms)})

# Nota: su Render usa il command:
# gunicorn -k uvicorn.workers.UvicornWorker app:app --workers=1 --threads=2 --timeout=180 --bind 0.0.0.0:$PORT
