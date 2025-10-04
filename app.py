import os, json, re, html, typing
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import requests

# -------- CONFIG ----------
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "brave").strip().lower()
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()
BING_API_KEY  = os.getenv("BING_API_KEY", "").strip()
MIN_WEB_SCORE = float(os.getenv("MIN_WEB_SCORE", "0.35"))
CRITICI_DIR   = os.getenv("CRITICI_DIR", "static/data/critici").strip()
PREFERRED_DOMAINS = [d.strip() for d in os.getenv(
    "PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com"
).split(",") if d.strip()]

# -------- APP & STATIC ----
app = FastAPI(title="Tecnaria QA Bot")
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# -------- UTIL ------------

def allowed_domain(url: str, prefer: List[str]) -> bool:
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower()
        return any(host.endswith(d.lower()) for d in prefer)
    except Exception:
        return False

def is_pdf(url: str) -> bool:
    return url.lower().endswith(".pdf")

def clean_html(text: str) -> str:
    # toglie script/style e tag HTML, normalizza spazi, elimina <strong>…>
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?is)</?strong[^>]*>", "", text)
    text = re.sub(r"(?is)</?[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def refine_tone(it_text: str) -> str:
    # Tono "Tecnaria": tecnico, chiaro, fluido, sintetico. Niente “bollettini”.
    if not it_text:
        return it_text
    # minuscola iniziale rumorosa (tipo “OK …”) → struttura a punti ordinati
    it_text = it_text.strip()
    it_text = re.sub(r"^ok\s*[-:]*\s*", "", it_text, flags=re.I)
    # punti elenco doppi -> singoli
    it_text = re.sub(r"(\n|\r)+", "\n", it_text)
    it_text = re.sub(r"\u2022|\u00b7|·", "-", it_text)
    return it_text

# -------- SINAPSI ---------
class SinapsiRule(BaseModel):
    id: Optional[str] = None
    pattern: str
    mode: str = "augment"  # override | augment | postscript
    lang: str = "it"
    answer: str

def load_sinapsi(path: str) -> List[SinapsiRule]:
    rules: List[SinapsiRule] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("rules", [])
        for item in data:
            try:
                rules.append(SinapsiRule(**item))
            except Exception:
                continue
    except Exception:
        pass
    return rules

SINAPSI_FILE = os.path.join(CRITICI_DIR, "sinapsi_rules.json")
SINAPSI_RULES: List[SinapsiRule] = load_sinapsi(SINAPSI_FILE)

def apply_sinapsi(q: str, base_answer: str) -> str:
    """Applica la prima regola che matcha (priorità: override > augment > postscript)."""
    if not SINAPSI_RULES:
        return base_answer

    matches_override = []
    matches_augment = []
    matches_post = []

    for r in SINAPSI_RULES:
        try:
            if re.search(r.pattern, q, flags=re.I):
                if r.mode == "override":
                    matches_override.append(r)
                elif r.mode == "augment":
                    matches_augment.append(r)
                elif r.mode == "postscript":
                    matches_post.append(r)
        except Exception:
            continue

    if matches_override:
        # prende la prima override
        return refine_tone(matches_override[0].answer)

    answer = base_answer or ""
    if matches_augment:
        # attacca tutte le augment (ordinate)
        for r in matches_augment:
            if r.answer.strip():
                addon = "\n" + refine_tone(r.answer.strip())
                answer = (answer + "\n" + addon).strip() if answer else addon.strip()

    if matches_post:
        # aggiunge PS
        ps_lines = []
        for r in matches_post:
            if r.answer.strip():
                ps_lines.append(refine_tone(r.answer.strip()))
        if ps_lines:
            answer = (answer + "\n\nPS\n" + "\n".join(ps_lines)).strip()

    return answer

# -------- WEB SEARCH ------

def brave_search(query: str) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        return []
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    params = {"q": query, "count": 8, "search_lang": "it"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=8)
        if r.status_code != 200:
            return []
        js = r.json()
        web = js.get("web", {}).get("results", [])
        out = []
        for it in web:
            res_url = it.get("url") or ""
            title = it.get("title") or ""
            desc = it.get("description") or ""
            score = float(it.get("language_score", 0.5))
            out.append({"url": res_url, "title": title, "snippet": desc, "score": score})
        return out
    except Exception:
        return []

def bing_search(query: str) -> List[Dict[str, Any]]:
    if not BING_API_KEY:
        return []
    url = "https://api.bing.microsoft.com/v7.0/search"
    headers = {"Ocp-Apim-Subscription-Key": BING_API_KEY}
    params = {"q": query, "count": 8, "mkt": "it-IT", "responseFilter": "Webpages"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=8)
        if r.status_code != 200:
            return []
        js = r.json()
        web = js.get("webPages", {}).get("value", [])
        out = []
        for it in web:
            out.append({"url": it.get("url"), "title": it.get("name"), "snippet": it.get("snippet"), "score": 0.6})
        return out
    except Exception:
        return []


def do_search(query: str) -> Dict[str, Any]:
    if SEARCH_PROVIDER == "bing":
        results = bing_search(query)
    else:
        results = brave_search(query)

    # filtra domini preferiti e per punteggio
    filtered = []
    for r in results:
        url = r.get("url") or ""
        if not url:
            continue
        if not allowed_domain(url, PREFERRED_DOMAINS):
            continue
        score = float(r.get("score") or 0)
        if score < MIN_WEB_SCORE:
            continue
        filtered.append(r)

    return {"results": filtered}

# -------- ANSWER LOGIC ----

def compose_answer_from_web(items: List[Dict[str, Any]]) -> str:
    if not items:
        return ""
    bullets = []
    sources = []
    for it in items[:4]:
        u = it.get("url", "")
        t = clean_html(it.get("title", "") or "")
        s = clean_html(it.get("snippet", "") or "")
        # evita includere blob PDF
        if is_pdf(u):
            bullets.append(f"- Documento tecnico ufficiale (PDF) disponibile.")
        else:
            # bullet compatto e informativo
            chunk = f"- {t or 'Contenuto ufficiale'}: {s[:220]}".rstrip()
            bullets.append(chunk)
        sources.append(u)
    head = "OK\n" + "\n".join(bullets) + "\n\n**Fonti**\n- " + "\n- ".join(sources)
    return refine_tone(head)

def final_refine(text: str) -> str:
    # pulizia finale e tono coerente
    text = clean_html(text)
    text = refine_tone(text)
    # niente “Ecco le informazioni richieste…”
    text = re.sub(r"^Ecco le informazioni richieste.*?\.\s*", "", text, flags=re.I)
    return text.strip()

# -------- ROUTES ----------

class AskBody(BaseModel):
    q: str

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
            "dir": os.path.abspath(CRITICI_DIR),
            "exists": os.path.isdir(CRITICI_DIR),
            "sinapsi_file": os.path.abspath(SINAPSI_FILE),
            "sinapsi_loaded": len(SINAPSI_RULES),
        }
    }

def handle_question(q: str) -> str:
    q = (q or "").strip()
    if not q:
        return "OK\n- Domanda vuota: inserisci una richiesta valida."

    # 1) Web first
    web = do_search(q)
    items = web.get("results", [])

    base = ""
    if items:
        base = compose_answer_from_web(items)

    # 2) Sinapsi (override/augment/postscript)
    out = apply_sinapsi(q, base)

    # 3) Se ancora vuoto → messaggio sobrio
    if not out.strip():
        return "OK\n- Non ho trovato una risposta affidabile su fonti ufficiali. Se vuoi, riformula oppure indicami maggiori dettagli (prodotto, supporto, spessore, ecc.)."

    return final_refine(out)

@app.get("/ask")
def ask_get(q: str = Query(..., description="Domanda")):
    answer = handle_question(q)
    return {"ok": True, "answer": answer}

@app.post("/api/ask")
def ask_post(body: AskBody):
    answer = handle_question(body.q)
    return {"ok": True, "answer": answer}

@app.get("/")
def home():
    # se c'è interfaccia, servila
    index_path = os.path.join("static", "index.html")
    if os.path.isfile(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    # altrimenti messaggio minimo
    return JSONResponse({"ok": True, "msg": "Use /ask or place static/index.html"})
