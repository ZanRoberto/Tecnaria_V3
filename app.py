import os
import json
import re
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, Request, Body, Query
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# --- Config ---
APP_TITLE = "Tecnaria QA Bot"
APP_ENDPOINTS = ["/ping", "/health", "/api/ask (GET q=... | POST JSON {q})"]

# Env
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()
BING_API_KEY = os.getenv("BING_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

SEARCH_PROVIDER = (os.getenv("SEARCH_PROVIDER") or "brave").lower()
PREFERRED_DOMAINS = [d.strip() for d in (os.getenv("PREFERRED_DOMAINS") or "tecnaria.com,spit.eu,spitpaslode.com").split(",") if d.strip()]
MIN_WEB_SCORE = float(os.getenv("MIN_WEB_SCORE") or 0.35)
WEB_TIMEOUT = float(os.getenv("WEB_TIMEOUT") or 6.0)
WEB_RETRIES = int(os.getenv("WEB_RETRIES") or 2)

CRITICI_DIR = os.getenv("CRITICI_DIR") or "static/data/critici"
STATIC_DIR = os.getenv("STATIC_DIR") or "static"
SINAPSI_FILE = os.path.join(os.getenv("STATIC_DATA_DIR") or "static/data", "sinapsi_rules.json")

# --- App ---
app = FastAPI(title=APP_TITLE)

# Static mount (serve index.html se presente)
if not os.path.isdir(STATIC_DIR):
    os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- Sinapsi loader ---
class Rule(BaseModel):
    id: Optional[str] = None
    pattern: str
    mode: str  # override | augment | postscript
    lang: str = "it"
    answer: str

def _load_sinapsi_rules(path: str) -> List[Rule]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        rules: List[Rule] = []
        for r in data:
            # tollero duplicati e piccoli errori
            try:
                rules.append(Rule(**r))
            except Exception:
                continue
        return rules
    except FileNotFoundError:
        return []
    except Exception:
        return []

SINAPSI_RULES: List[Rule] = _load_sinapsi_rules(SINAPSI_FILE)


# --- Mini web search (placeholder robusto) ---
import requests

def _score_url(u: str) -> float:
    base = u.split("/")[2] if "://" in u else u
    for i, dom in enumerate(PREFERRED_DOMAINS):
        if dom in base:
            # più a sinistra nella lista = più importante
            return 1.0 - (i * 0.1)
    return 0.2

def brave_search(q: str) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        return []
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    params = {"q": q, "count": 5, "search_lang": "it"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=WEB_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        out = []
        for item in (data.get("web", {}) or {}).get("results", []):
            u = item.get("url")
            title = item.get("title") or ""
            snippet = item.get("description") or ""
            score = _score_url(u)
            out.append({"url": u, "title": title, "snippet": snippet, "score": score})
        # preferiti prima
        out.sort(key=lambda x: x.get("score", 0), reverse=True)
        # filtra per soglia
        out = [x for x in out if x["score"] >= MIN_WEB_SCORE]
        return out
    except Exception:
        return []

def web_search(q: str) -> List[Dict[str, Any]]:
    if SEARCH_PROVIDER == "brave":
        return brave_search(q)
    # fallback nullo se non supportato
    return []


# --- Composer risposte ---
def apply_sinapsi(q: str, base_answer: str) -> str:
    if not SINAPSI_RULES:
        return base_answer

    q_norm = q.strip().lower()
    override_blocks: List[str] = []
    augment_blocks: List[str] = []
    postscripts: List[str] = []

    for rule in SINAPSI_RULES:
        try:
            if re.search(rule.pattern, q, flags=re.IGNORECASE | re.DOTALL):
                if rule.mode == "override":
                    override_blocks.append(rule.answer.strip())
                elif rule.mode == "augment":
                    augment_blocks.append(rule.answer.strip())
                elif rule.mode == "postscript":
                    postscripts.append(rule.answer.strip())
        except re.error:
            # regex sbagliata → salto
            continue

    if override_blocks:
        # se più override matchano, concateno (primo vince il tono)
        out = "\n\n".join(override_blocks)
    else:
        out = base_answer.strip()
        if augment_blocks:
            out += ("\n" if out else "") + "\n".join(augment_blocks)
    if postscripts:
        out += ("\n" if out else "") + "\n".join(postscripts)

    return out.strip()


def build_web_answer(q: str, hits: List[Dict[str, Any]]) -> str:
    if not hits:
        return "OK\n- **Non ho trovato una risposta affidabile sul web** (o la ricerca non è configurata). Puoi riformulare la domanda oppure posso fornirti i contatti Tecnaria.\n"

    # Sintesi molto leggibile (senza <strong> sporchi dai PDF)
    bullet_intro = "OK\n- **Riferimento**: strutture miste e connettori"
    # Prendo 1–3 fonti top
    top = hits[:3]
    # Lista fonti pulita
    fonti = "\n".join(f"- {h['url']}" for h in top if h.get("url"))
    out = f"{bullet_intro}\n\n**Fonti**\n{fonti}\n"
    return out


# --- API model ---
class AskIn(BaseModel):
    q: str


# --- Routes ---
@app.get("/ping")
def ping():
    return {"ok": True, "pong": True}

@app.get("/health")
def health():
    return {
        "status": "ok",
        "web_search": {
            "provider": SEARCH_PROVIDER,
            "brave_key": bool(BRAVE_API_KEY),
            "bing_key": bool(BING_API_KEY),
            "preferred_domains": PREFERRED_DOMAINS,
            "min_web_score": MIN_WEB_SCORE,
        },
        "critici": {
            "dir": os.path.abspath(os.path.join("static", "data")),
            "exists": os.path.isdir(os.path.join("static", "data")),
            "sinapsi_file": SINAPSI_FILE,
            "sinapsi_loaded": len(SINAPSI_RULES),
        },
    }

def _answer_flow(q: str) -> Dict[str, Any]:
    q = (q or "").strip()
    if not q:
        return {"ok": True, "answer": "OK\n- **Domanda vuota**: inserisci una richiesta valida.\n"}

    # 1) Web
    hits = web_search(q)
    base_answer = build_web_answer(q, hits)

    # 2) Sinapsi
    final_answer = apply_sinapsi(q, base_answer)

    # 3) Se tutto still “vuoto” metto una chiusura educata
    if not final_answer.strip():
        final_answer = "OK\n- **Non ho trovato** una risposta utilizzabile. Vuoi che ti metta in contatto con un tecnico/commerciale Tecnaria?\n"

    return {"ok": True, "answer": final_answer}

@app.get("/api/ask")
def ask_get(q: str = Query(..., description="Domanda")):
    return _answer_flow(q)

@app.post("/api/ask")
def ask_post(payload: AskIn = Body(...)):
    return _answer_flow(payload.q)

@app.get("/", response_class=HTMLResponse)
def root():
    """
    Se esiste static/index.html → serve UI
    Altrimenti mostra un mini banner con gli endpoint.
    """
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path, media_type="text/html")
    # fallback banner
    body = {
        "service": APP_TITLE,
        "endpoints": APP_ENDPOINTS,
        "msg": "Use /ask or place static/index.html",
    }
    html = f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{APP_TITLE}</title>
<style>
  body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin:0; padding:48px; background:#0b1220; color:#eee; }}
  .wrap {{ max-width: 960px; margin: 0 auto; }}
  .card {{ background:#0f172a; border:1px solid #1f2a44; border-radius:14px; padding:24px; }}
  code, pre {{ background:#0b1220; color:#e2e8f0; padding:2px 6px; border-radius:6px; }}
  a {{ color:#93c5fd; text-decoration:none; }}
  h1 {{ margin-top:0 }}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>{APP_TITLE}</h1>
    <p>UI non trovata. Metti un file <code>static/index.html</code>.</p>
    <pre>{json.dumps(body, ensure_ascii=False, indent=2)}</pre>
  </div>
</div>
</body>
</html>"""
    return HTMLResponse(html)
