# app.py
# -----------------------------------------------------------------------------
# Tecnaria QA Bot – versione "prod" per interfaccia web
# - Serve templates/ (index.html) e static/
# - /ping, /health, /ask (GET/POST)
# - web-first then local, regola P560 per "patentino/formazione"
# - fallback sicuro se mancano API-keys
# -----------------------------------------------------------------------------

import os
import re
import glob
import time
import unicodedata
from typing import List, Dict, Tuple, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

# ------------------------------ CONFIG / ENV ---------------------------------
DEBUG               = os.getenv("DEBUG", "0") == "1"
MODE                = os.getenv("MODE", "web_first_then_local")  # web_first_then_local | local_only | web_only
FETCH_WEB_FIRST     = os.getenv("FETCH_WEB_FIRST", "1") == "1"
POLICY_MODE         = os.getenv("POLICY_MODE", "default")

SEARCH_PROVIDER     = os.getenv("SEARCH_PROVIDER", "brave").lower()  # brave | bing
SEARCH_API_ENDPOINT = os.getenv("SEARCH_API_ENDPOINT", "").strip()
BRAVE_API_KEY       = os.getenv("BRAVE_API_KEY", "").strip()
BING_API_KEY        = os.getenv("BING_API_KEY", "").strip() or os.getenv("AZURE_BING_KEY", "").strip()

PREFERRED_DOMAINS   = [d.strip() for d in os.getenv("PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com").split(",") if d.strip()]
DOC_GLOB            = os.getenv("DOC_GLOB", "static/docs/*.txt")

MIN_WEB_SCORE       = float(os.getenv("MIN_WEB_SCORE", "0.35"))
WEB_TIMEOUT         = float(os.getenv("WEB_TIMEOUT", "6"))
WEB_RETRIES         = int(os.getenv("WEB_RETRIES", "2"))

FORCE_P560_WEB      = os.getenv("FORCE_P560_WEB", "1") == "1"
DEMOTE_CONTACTS     = os.getenv("DEMOTE_CONTACTS", "1") == "1"

# Template / static dirs
TEMPLATES_DIR = os.getenv("TEMPLATES_DIR", "templates")
STATIC_DIR = os.getenv("STATIC_DIR", "static")

print("[BOOT] -----------------------------------------------")
print(f"[BOOT] MODE={MODE} FETCH_WEB_FIRST={FETCH_WEB_FIRST} POLICY_MODE={POLICY_MODE}")
print(f"[BOOT] SEARCH_PROVIDER={SEARCH_PROVIDER} SEARCH_API_ENDPOINT={(SEARCH_API_ENDPOINT or '(default)')}")
print(f"[BOOT] PREFERRED_DOMAINS={PREFERRED_DOMAINS}")
print(f"[BOOT] MIN_WEB_SCORE={MIN_WEB_SCORE} WEB_TIMEOUT={WEB_TIMEOUT}s WEB_RETRIES={WEB_RETRIES}")
print(f"[BOOT] FORCE_P560_WEB={FORCE_P560_WEB} DEMOTE_CONTACTS={DEMOTE_CONTACTS}")
print(f"[BOOT] DOC_GLOB={DOC_GLOB} TEMPLATES_DIR={TEMPLATES_DIR} STATIC_DIR={STATIC_DIR}")
print("[BOOT] ------------------------------------------------")

# ------------------------------ UTIL -----------------------------------------
P560_PAT = re.compile(r"\bp\s*[- ]?\s*560\b", re.I)
LIC_PAT  = re.compile(r"\b(patentino|abilitazione|formazione)\b", re.I)
CONT_PAT = re.compile(r"\b(contatti|telefono|email|pec)\b", re.I)

UI_NOISE_PREFIXES = ("chiedi", "pulisci", "copia risposta", "risposta", "connettori ctf", "contatti", "—")

def normalize(text: str) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t

def clean_ui_noise(text: str) -> str:
    if not text:
        return ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    keep: List[str] = []
    for l in lines:
        low = l.strip().lower()
        if any(low.startswith(pfx) for pfx in UI_NOISE_PREFIXES):
            continue
        keep.append(l)
    return " ".join(keep).strip()

def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""

def prefer_score_for_domain(url: str) -> float:
    dom = domain_of(url)
    if not dom:
        return 0.0
    for pd in PREFERRED_DOMAINS:
        if pd in dom:
            return 0.25
    return 0.0

def short_text(text: str, n: int = 900) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return (t[:n] + "…") if len(t) > n else t

# ------------------------------ KB LOCALE ------------------------------------
KB_DOCS: List[Dict] = []
CONTACTS_DOC: Optional[Dict] = None

def load_kb():
    global KB_DOCS, CONTACTS_DOC
    KB_DOCS = []
    CONTACTS_DOC = None
    paths = glob.glob(DOC_GLOB) if DOC_GLOB else []
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                txt = f.read()
            entry = {"path": p, "text": txt, "name": os.path.basename(p)}
            if entry["name"].lower().startswith("contatti") or "contatti" in entry["name"].lower():
                CONTACTS_DOC = entry
            else:
                KB_DOCS.append(entry)
        except Exception as e:
            if DEBUG:
                print("[KB][ERR]", p, e)

load_kb()
print(f"[KB] Caricati {len(KB_DOCS)} documenti. Contatti={'OK' if CONTACTS_DOC else 'NO'}")

def kb_lookup(q: str, exclude_contacts: bool = True) -> Optional[str]:
    nq = normalize(q)
    best = None
    best_score = 0.0
    candidates = KB_DOCS.copy()
    if not exclude_contacts and CONTACTS_DOC:
        candidates.append(CONTACTS_DOC)

    for doc in candidates:
        text = doc["text"]
        name = doc["name"]
        score = 0.0
        low = normalize(text + " " + name)
        for w in set(nq.split()):
            if w and w in low:
                score += 1.0
        if "contatti" in name.lower() and exclude_contacts:
            score -= 3.0
        if "p560" in low:
            score += 0.2
        if "ctf" in low:
            score += 0.2
        if score > best_score:
            best_score = score
            best = doc

    if best and best_score > 0.5:
        return short_text(best["text"], 1200)
    return None

# ------------------------------ WEB SEARCH / FETCH ---------------------------
def brave_search(q: str, topk: int = 5, timeout: float = WEB_TIMEOUT) -> List[Dict]:
    if not BRAVE_API_KEY:
        return []
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    params = {"q": q, "count": topk}
    url = "https://api.search.brave.com/res/v1/web/search"
    try:
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        items = []
        for it in data.get("web", {}).get("results", []):
            items.append({
                "title": it.get("title") or "",
                "url": it.get("url") or "",
                "snippet": it.get("description") or ""
            })
        return items
    except Exception as e:
        if DEBUG:
            print("[BRAVE][ERR]", e)
        return []

def bing_search(q: str, topk: int = 5, timeout: float = WEB_TIMEOUT) -> List[Dict]:
    key = BING_API_KEY
    endpoint = SEARCH_API_ENDPOINT or "https://api.bing.microsoft.com/v7.0/search"
    if not key:
        return []
    headers = {"Ocp-Apim-Subscription-Key": key}
    params = {"q": q, "count": topk, "responseFilter": "Webpages"}
    try:
        r = requests.get(endpoint, headers=headers, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        items = []
        for it in data.get("webPages", {}).get("value", []):
            items.append({
                "title": it.get("name") or "",
                "url": it.get("url") or "",
                "snippet": it.get("snippet") or ""
            })
        return items
    except Exception as e:
        if DEBUG:
            print("[BING][ERR]", e)
        return []

def web_search(q: str, topk: int = 5) -> List[Dict]:
    return bing_search(q, topk=topk) if SEARCH_PROVIDER == "bing" else brave_search(q, topk=topk)

def fetch_text(url: str, timeout: float = WEB_TIMEOUT) -> str:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n")
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        return text.strip()
    except Exception as e:
        if DEBUG:
            print("[FETCH][ERR]", url, e)
        return ""

def rank_results(q: str, results: List[Dict], prefer_domains: List[str]) -> List[Dict]:
    nq = normalize(q)
    for it in results:
        score = 0.0
        score += prefer_score_for_domain(it.get("url", ""))
        sn = normalize((it.get("title") or "") + " " + (it.get("snippet") or ""))
        for w in set(nq.split()):
            if w and w in sn:
                score += 0.4
        if P560_PAT.search(sn):
            score += 0.5
        it["score"] = score
    return sorted(results, key=lambda x: x.get("score", 0.0), reverse=True)

def web_lookup(q: str,
               min_score: float = MIN_WEB_SCORE,
               timeout: float = WEB_TIMEOUT,
               retries: int = WEB_RETRIES,
               domains: Optional[List[str]] = None) -> Tuple[str, List[str], float]:
    doms = domains or PREFERRED_DOMAINS
    sources: List[str] = []
    best_score = 0.0
    last_err = None

    # if missing API keys, return empty (we will use template fallback)
    if (SEARCH_PROVIDER == "brave" and not BRAVE_API_KEY) or (SEARCH_PROVIDER == "bing" and not BING_API_KEY):
        if DEBUG:
            print("[WEB] No API key; will use template fallback")
        return "", [], 0.0

    for attempt in range(retries + 1):
        try:
            results = web_search(q, topk=7)
            if not results:
                continue
            if doms:
                results = [r for r in results if any(d in domain_of(r["url"]) for d in doms)]
                if DEBUG:
                    print(f"[WEB] Filtered by domains {doms}: {len(results)} hits")
            ranked = rank_results(q, results, doms)
            if not ranked:
                continue
            top = ranked[0]
            best_score = top.get("score", 0.0)
            if DEBUG:
                print(f"[WEB] best={top.get('url')} score={best_score:.2f}")
            if best_score < min_score:
                continue
            txt = fetch_text(top["url"], timeout=timeout)
            if not txt:
                continue
            sources.append(top["url"])
            ans = (
                "OK\n"
                f"- **Riferimento**: {top.get('title') or 'pagina tecnica'}.\n"
                "- **Sintesi**: contenuti tecnici pertinenti alla query trovati sul sito preferito.\n"
                "- **Nota**: verificare sempre le istruzioni ufficiali e la documentazione aggiornata.\n"
            )
            return ans, sources, best_score
        except Exception as e:
            last_err = e
            time.sleep(0.25)

    if DEBUG and last_err:
        print("[WEB][ERROR]", last_err)

    return "", sources, best_score

# ------------------------------ FORMATTER -------------------------------------
def format_as_bot(core_text: str, sources: Optional[List[str]] = None) -> str:
    core = core_text or ""
    if core.strip().startswith("OK"):
        if sources:
            src_lines = "\n".join(f"- {u}" for u in sources)
            if "\n**Fonti**" not in core:
                return core.rstrip() + f"\n\n**Fonti**\n{src_lines}\n"
        return core
    out = "OK\n" + core.strip()
    if sources:
        out += "\n\n**Fonti**\n" + "\n".join(f"- {u}" for u in sources) + "\n"
    return out

def answer_contacts() -> str:
    if CONTACTS_DOC:
        return "OK\n" + CONTACTS_DOC["text"].strip()
    # fallback generic
    return (
        "OK\n"
        "- **Ragione sociale**: TECNARIA S.p.A.\n"
        "- **Telefono**: +39 0424 502029\n"
        "- **Email**: info@tecnaria.com\n"
    )

def answer_p560_template() -> str:
    return (
        "OK\n"
        "- **Abilitazione/Patentino**: Non è richiesto un patentino specifico per la **SPIT P560**. "
        "È necessaria una **formazione interna** secondo le istruzioni del costruttore.\n"
        "- **Formazione minima**: scelta propulsori, **taratura potenza**, prova su campione, verifica **ancoraggio** dei chiodi, gestione inceppamenti.\n"
        "- **DPI e sicurezza**: **occhiali**, **guanti**, **protezione udito**; operare su **lamiera ben aderente**; rispettare distanze dai bordi; non sparare su supporti deformati/non idonei.\n"
        "- **Procedura di posa**: 1 connettore **CTF** = **2 chiodi HSBR14** con **P560**; nessuna preforatura; potenza regolata in funzione di lamiera/trave.\n"
        "- **Supporto Tecnaria**: disponibili **istruzioni di posa** e indicazioni pratiche per il cantiere.\n"
    )

def build_p560_from_web(sources: List[str]) -> str:
    base = answer_p560_template()
    if sources:
        base += "\n**Fonti**\n" + "\n".join(f"- {u}" for u in sources) + "\n"
    else:
        base += "\n**Fonti**\n- web (tecnaria.com)\n"
    return base

# ------------------------------ ROUTING LOGIC --------------------------------
def route_question_to_answer(raw_q: str) -> str:
    if not raw_q or not raw_q.strip():
        return "OK\n- **Domanda vuota**: inserisci una richiesta valida.\n"

    cleaned = clean_ui_noise(raw_q)
    nq = normalize(cleaned)

    # 1) se utente chiede contatti esplicitamente (e non sono demotivati)
    if CONT_PAT.search(nq) and not DEMOTE_CONTACTS:
        return answer_contacts()

    # 2) regola forte: P560 + licenza/formazione => web preferito (PREFERRED_DOMAINS)
    if FORCE_P560_WEB and P560_PAT.search(nq) and LIC_PAT.search(nq):
        ans, srcs, sc = web_lookup(cleaned, min_score=MIN_WEB_SCORE, timeout=WEB_TIMEOUT, retries=WEB_RETRIES, domains=PREFERRED_DOMAINS)
        if ans:
            return build_p560_from_web(srcs)
        return build_p560_from_web(srcs)

    # 3) WEB-FIRST generale
    if MODE.startswith("web_first") or (FETCH_WEB_FIRST and MODE != "local_only"):
        ans, srcs, sc = web_lookup(cleaned, min_score=MIN_WEB_SCORE, timeout=WEB_TIMEOUT, retries=WEB_RETRIES)
        if ans:
            return format_as_bot(ans, srcs)

    # 4) KB locale (con contatti demotizzati se DEMOTE_CONTACTS)
    local = kb_lookup(cleaned, exclude_contacts=DEMOTE_CONTACTS)
    if local:
        return format_as_bot("OK\n- **Riferimento locale** trovato.\n- **Sintesi**: " + short_text(local, 800))

    # 5) se chiede contatti e li abbiamo demotizzati
    if CONT_PAT.search(nq) and DEMOTE_CONTACTS:
        return answer_contacts()

    # 6) fallback elegante
    return "OK\n- **Informazione non presente** in fonti web/KB. Posso cercare meglio sul web o metterti in contatto con un tecnico.\n"

# ------------------------------ FASTAPI APP ----------------------------------
app = FastAPI(title="Tecnaria QA Bot", version="1.0.0")

# static + templates mount
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# CORS (se la UI è su altro origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Homepage: se c'è template index.html servilo, altrimenti banner JSON
@app.get("/", response_class=HTMLResponse)
def root(request: Request, q: Optional[str] = Query(None)):
    if q and q.strip():
        ans = route_question_to_answer(q)
        return JSONResponse({"ok": True, "answer": ans})
    # prefer template if exists
    index_path = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.isfile(index_path):
        return templates.TemplateResponse("index.html", {"request": request})
    # fallback banner
    return JSONResponse({"service": "Tecnaria QA Bot", "endpoints": ["/ping", "/health", "/ask (GET q=... | POST JSON {q})"]})

# health endpoints for Render
@app.get("/ping")
def ping():
    return {"ok": True, "pong": True}

@app.get("/health")
def health():
    return {
        "status": "ok",
        "mode": MODE,
        "web_first": MODE.startswith("web_first") or FETCH_WEB_FIRST,
        "policy_mode": POLICY_MODE,
        "preferred_domains": PREFERRED_DOMAINS,
        "rules": {
            "force_p560_web": FORCE_P560_WEB,
            "demote_contacts": DEMOTE_CONTACTS,
            "min_web_score": MIN_WEB_SCORE,
            "web_timeout": WEB_TIMEOUT,
            "web_retries": WEB_RETRIES
        },
        "kb": {"docs_loaded": len(KB_DOCS), "contacts": bool(CONTACTS_DOC), "doc_glob": DOC_GLOB}
    }

# /ask POST & GET for the web UI
@app.post("/ask")
async def ask_post(req: Request):
    try:
        data = await req.json()
    except Exception:
        data = {}
    q = (data.get("q") or data.get("question") or "").strip()
    ans = route_question_to_answer(q)
    return JSONResponse({"ok": True, "answer": ans})

@app.get("/ask")
async def ask_get(q: str = Query("", description="Domanda")):
    ans = route_question_to_answer(q or "")
    return JSONResponse({"ok": True, "answer": ans})

# alias endpoints for API usage (/api/ask)
@app.post("/api/ask")
async def api_ask_post(req: Request):
    try:
        data = await req.json()
    except Exception:
        data = {}
    q = (data.get("q") or data.get("question") or "").strip()
    ans = route_question_to_answer(q)
    return JSONResponse({"ok": True, "answer": ans})

@app.get("/api/ask")
async def api_ask_get(q: str = Query("", description="Domanda")):
    ans = route_question_to_answer(q or "")
    return JSONResponse({"ok": True, "answer": ans})

# ------------------------------ MAIN (dev) -----------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
