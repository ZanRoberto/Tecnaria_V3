import os, re, json, math
from typing import List, Dict, Optional
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------- ENV ----------
BRAVE_KEY = os.getenv("BRAVE_API_KEY", "").strip()
PREFERRED_DOMAINS = [d.strip().lower() for d in os.getenv(
    "PREFERRED_DOMAINS",
    "tecnaria.com, spit.eu, spitpaslode.com"
).split(",") if d.strip()]
MIN_WEB_SCORE = float(os.getenv("MIN_WEB_SCORE", "0.35"))
CRITICI_DIR = os.getenv("CRITICI_DIR", "static/data/critici").strip()
SINAPSI_PATH = os.path.join(CRITICI_DIR, "sinapsi_rules.json")
FORCE_WEB = os.getenv("CRITICAL_ENRICH_FORCE_WEB", "0").lower() in ("1","true","yes")
DEBUG = os.getenv("DEBUG", "0").lower() in ("1","true","yes")

# ---------- FASTAPI ----------
app = FastAPI(title="Tecnaria QA Bot")

# ---------- MODELS ----------
class AskIn(BaseModel):
    q: str

# ---------- UTILS ----------
def log(msg: str):
    if DEBUG:
        print(msg, flush=True)

def domain_of(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower()
    except:
        return ""

def allowed_domain(url: str) -> bool:
    dom = domain_of(url)
    return any(dom.endswith(d) for d in PREFERRED_DOMAINS)

def clean_html_to_text(html: str) -> str:
    # niente <strong>, niente js/css, frasi corte
    soup = BeautifulSoup(html or "", "html.parser")
    for t in soup(["script","style","noscript"]):
        t.decompose()
    text = soup.get_text(separator=" ", strip=True)
    # compatta spazi e taglia lunghezze ridicole
    text = re.sub(r"\s+", " ", text).strip()
    return text

# ---------- SINAPSI ----------
class Rule:
    def __init__(self, r: dict):
        self.id = r.get("id","")
        self.pattern = r.get("pattern","")
        self.re = re.compile(self.pattern, re.I)
        self.mode = r.get("mode","augment").lower()  # override | augment | postscript
        self.lang = r.get("lang","it")
        self.answer = r.get("answer","").rstrip()

def load_sinapsi(path: str) -> List[Rule]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        rules = [Rule(x) for x in data if isinstance(x, dict)]
        return rules
    except Exception as e:
        log(f"[sinapsi] load error: {e}")
        return []

SINAPSI_RULES: List[Rule] = load_sinapsi(SINAPSI_PATH)

def apply_sinapsi(query: str, base: Optional[str]) -> Optional[str]:
    """Applica in ordine: override -> augment -> postscript."""
    global SINAPSI_RULES
    matches = [r for r in SINAPSI_RULES if r.re.search(query)]
    if not matches:
        return base

    # 1) override
    for r in matches:
        if r.mode == "override":
            return tone_refine(r.answer)

    # 2) augment
    augmented = base or ""
    for r in matches:
        if r.mode == "augment":
            if augmented:
                augmented = augmented.rstrip() + "\n" + r.answer
            else:
                augmented = r.answer

    # 3) postscript
    post = "\n".join(r.answer for r in matches if r.mode == "postscript")
    if post:
        augmented = (augmented.rstrip() + "\n\n" + post.strip()) if augmented else post

    return tone_refine(augmented or base)

# ---------- WEB SEARCH (BRAVE) ----------
def brave_search(q: str, limit: int = 5) -> List[Dict]:
    if not BRAVE_KEY:
        return []
    try:
        url = "https://api.search.brave.com/res/v1/web/search"
        headers = {"X-Subscription-Token": BRAVE_KEY}
        params = {"q": q, "count": limit, "safesearch": "moderate", "country": "it"}
        r = requests.get(url, headers=headers, params=params, timeout=12)
        r.raise_for_status()
        data = r.json() or {}
        web = (data.get("web", {}) or {}).get("results", []) or []
        results = []
        for it in web:
            href = it.get("url") or ""
            title = it.get("title") or ""
            snippet = it.get("description") or ""
            score = float(it.get("page_freshness_score") or 0.5)  # fallback
            results.append({
                "url": href, "title": title, "snippet": snippet, "score": score
            })
        return results
    except Exception as e:
        log(f"[brave] {e}")
        return []

def fetch_preview(url: str) -> str:
    try:
        if url.lower().endswith(".pdf"):
            return "PDF individuato (apri la scheda tecnica)."
        r = requests.get(url, timeout=12)
        r.raise_for_status()
        return clean_html_to_text(r.text)[:600]
    except:
        return ""

def web_answer(q: str) -> Optional[str]:
    res = brave_search(q, limit=6)
    if not res:
        return None
    # filtra per dominio e score
    filtered = [x for x in res if allowed_domain(x["url"]) and (x["score"] >= MIN_WEB_SCORE)]
    if not filtered:
        return None
    bullets = []
    sources = []
    for item in filtered[:3]:
        preview = fetch_preview(item["url"])
        if preview:
            bullets.append(f"- {preview}")
        sources.append(f"- {item['url']}")
    if not bullets:
        return None
    text = "OK\n" + "\n".join(bullets) + "\n\n**Fonti**\n" + "\n".join(sources)
    return text

# ---------- TONE REFINER ----------
def tone_refine(answer: Optional[str]) -> Optional[str]:
    if not answer:
        return answer
    a = answer.strip()
    # normalizza l’head “OK”
    if not a.startswith("OK"):
        a = "OK\n" + a
    # togli doppie righe, spazi, ecc.
    a = re.sub(r"\n{3,}", "\n\n", a)
    return a

# ---------- ROUTES ----------
@app.get("/")
def index():
    # servi la tua index (già ok). Se è in /static/index.html:
    index_path = os.path.join(APP_DIR, "static", "index.html")
    if not os.path.exists(index_path):
        # fallback a una piccola pagina test
        return JSONResponse({"ok": True, "msg": "Use /ask or place static/index.html"})
    return FileResponse(index_path)

@app.get("/health")
def health():
    return JSONResponse({
        "status": "ok",
        "web_search": {
            "provider": "brave",
            "brave_key": bool(BRAVE_KEY),
            "preferred_domains": PREFERRED_DOMAINS,
            "min_web_score": MIN_WEB_SCORE
        },
        "critici": {
            "dir": os.path.abspath(CRITICI_DIR),
            "exists": os.path.isdir(CRITICI_DIR),
            "sinapsi_file": os.path.abspath(SINAPSI_PATH),
            "sinapsi_loaded": len(SINAPSI_RULES)
        }
    })

@app.post("/ask")
def ask(body: AskIn):
    q = (body.q or "").strip()
    if not q:
        return JSONResponse({"ok": True, "answer": "OK\n- **Domanda vuota**: inserisci una richiesta valida."})

    # 1) Sinapsi override subito
    base = apply_sinapsi(q, base=None)
    if base and not base.startswith("OK\n- **Non ho trovato"):
        # Se l’override ha risposto, esci
        for r in SINAPSI_RULES:
            if r.re.search(q) and r.mode == "override":
                return JSONResponse({"ok": True, "answer": base})

    # 2) Web layer (se chiave c’è, altrimenti salta)
    web = web_answer(q) if (BRAVE_KEY or FORCE_WEB) else None

    # 3) Applica Sinapsi come augment/postscript sul risultato web
    final = apply_sinapsi(q, base=web)

    # 4) Fallback: solo Sinapsi (se aveva messo qualcosa) o messaggio standard
    if not final:
        final = "OK\n- **Non ho trovato una risposta affidabile** (o la ricerca non è configurata)."

    return JSONResponse({"ok": True, "answer": final})
