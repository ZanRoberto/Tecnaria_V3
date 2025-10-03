# app.py
# Tecnaria QA Bot — web-first con gestione PDF, snippet reale e template tecnici
# Endpoints: /, /ping, /health, /ask (GET/POST), /api/ask
# ENV minimi: SEARCH_PROVIDER=brave|bing + (BRAVE_API_KEY|BING_API_KEY)
# Facoltativi: PREFERRED_DOMAINS, MIN_WEB_SCORE, CRITICI_DIR, DEBUG

import os
import re
import json
import glob
import unicodedata
from typing import List, Dict, Tuple, Optional
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

# ----------------------------- ENV / CONFIG ---------------------------------
DEBUG               = os.getenv("DEBUG", "0") == "1"

MODE                = os.getenv("MODE", "web_first_then_local")  # compat
SEARCH_PROVIDER     = os.getenv("SEARCH_PROVIDER", "brave").lower()  # brave|bing
SEARCH_API_ENDPOINT = os.getenv("SEARCH_API_ENDPOINT", "").strip()
BRAVE_API_KEY       = os.getenv("BRAVE_API_KEY", "").strip()
BING_API_KEY        = (os.getenv("BING_API_KEY", "").strip()
                       or os.getenv("AZURE_BING_KEY", "").strip())

PREFERRED_DOMAINS   = [d.strip() for d in os.getenv(
    "PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com"
).split(",") if d.strip()]

MIN_WEB_SCORE       = float(os.getenv("MIN_WEB_SCORE", "0.35"))
WEB_TIMEOUT         = float(os.getenv("WEB_TIMEOUT", "8"))
WEB_RETRIES         = int(os.getenv("WEB_RETRIES", "2"))

CRITICI_DIR         = os.getenv("CRITICI_DIR", "static/static/data/critici").strip()
TEMPLATES_DIR       = os.getenv("TEMPLATES_DIR", "templates")
STATIC_DIR          = os.getenv("STATIC_DIR", "static")

print("[BOOT] -----------------------------------------------")
print(f"[BOOT] MODE={MODE} SEARCH_PROVIDER={SEARCH_PROVIDER} "
      f"PREFERRED_DOMAINS={PREFERRED_DOMAINS}")
print(f"[BOOT] MIN_WEB_SCORE={MIN_WEB_SCORE} WEB_TIMEOUT={WEB_TIMEOUT}s WEB_RETRIES={WEB_RETRIES}")
print(f"[BOOT] CRITICI_DIR={CRITICI_DIR} TEMPLATES_DIR={TEMPLATES_DIR} STATIC_DIR={STATIC_DIR}")
print("[BOOT] ------------------------------------------------")

# ----------------------------- UTIL -----------------------------------------
P560_PAT = re.compile(r"\bp\s*[- ]?\s*560\b", re.I)
LIC_PAT  = re.compile(r"\b(patentino|abilitazione|formazione)\b", re.I)

def normalize(text: str) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", t).strip().lower()

def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""

def short_text(text: str, n: int = 900) -> str:
    t = re.sub(r"\s+", " ", (text or "")).strip()
    return (t[:n] + "…") if len(t) > n else t

# --------------------------- WEB SEARCH / FETCH ------------------------------
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
        return [
            {"title": it.get("title") or "", "url": it.get("url") or "",
             "snippet": it.get("description") or ""}
            for it in data.get("web", {}).get("results", [])
        ]
    except Exception as e:
        if DEBUG: print("[BRAVE][ERR]", e)
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
        return [
            {"title": it.get("name") or "", "url": it.get("url") or "",
             "snippet": it.get("snippet") or ""}
            for it in data.get("webPages", {}).get("value", [])
        ]
    except Exception as e:
        if DEBUG: print("[BING][ERR]", e)
        return []

def web_search(q: str, topk: int = 5) -> List[Dict]:
    return bing_search(q, topk=topk) if SEARCH_PROVIDER == "bing" else brave_search(q, topk=topk)

# ----------- PDF-safe fetch (no blob), HTML -> testo pulito ------------------
def is_pdf_url(url: str) -> bool:
    u = (url or "").lower()
    return u.endswith(".pdf") or "/download/" in u or "/pdf/" in u

def fetch_text_or_none(url: str, timeout: float = WEB_TIMEOUT) -> Optional[str]:
    """Ritorna testo SOLO se HTML; per PDF torna None (evita %PDF-1.7 blob)."""
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}, stream=True)
        ct = (r.headers.get("Content-Type") or "").lower()
        if "application/pdf" in ct or is_pdf_url(url):
            return None
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n")
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        return text.strip()
    except Exception:
        return None

# --------------------------- RANK -------------------------------------------
def rank_results(q: str, results: List[Dict]) -> List[Dict]:
    nq = normalize(q)
    for it in results:
        score = 0.0
        if any(pd in domain_of(it.get("url", "")) for pd in PREFERRED_DOMAINS):
            score += 0.4
        sn = normalize((it.get("title", "") + " " + it.get("snippet", "")).strip())
        for w in set(nq.split()):
            if w and w in sn:
                score += 0.35
        if P560_PAT.search(sn):
            score += 0.25
        it["score"] = score
    return sorted(results, key=lambda x: x.get("score", 0.0), reverse=True)

# --------------------------- WEB LOOKUP -------------------------------------
def web_lookup(q: str,
               min_score: float = MIN_WEB_SCORE,
               timeout: float = WEB_TIMEOUT,
               retries: int = WEB_RETRIES,
               domains: Optional[List[str]] = None) -> Tuple[str, List[str], float]:
    """
    Cerca sul web (preferendo domini indicati) e restituisce UNA risposta
    con snippet REALE per HTML, e snippet SERP pulito per PDF.
    """
    doms = domains if domains is not None else PREFERRED_DOMAINS
    sources: List[str] = []
    best_score = 0.0

    if (SEARCH_PROVIDER == "brave" and not BRAVE_API_KEY) or (SEARCH_PROVIDER == "bing" and not BING_API_KEY):
        return "", [], 0.0

    for _ in range(retries + 1):
        results = web_search(q, topk=7) or []
        if doms:
            results = [r for r in results if any(d in domain_of(r.get("url", "")) for d in doms)]
        ranked = rank_results(q, results)
        if not ranked:
            continue

        top = ranked[0]
        best_score = top.get("score", 0.0)
        if best_score < min_score:
            continue

        url = top.get("url", "")
        title = top.get("title") or "pagina tecnica"

        # PDF → usa snippet SERP o fallback pulito
        if is_pdf_url(url):
            snippet_serp = (top.get("snippet") or "").strip()
            if not snippet_serp:
                snippet_serp = "Scheda/PDF Tecnaria pertinente alla domanda. Apri la fonte per i dettagli tecnici."
            answer = (
                "OK\n"
                f"- **Riferimento**: {title}\n"
                f"- **Sintesi web**: {snippet_serp}\n"
            )
            return answer, [url], best_score

        # HTML → estrai testo reale
        page_text = fetch_text_or_none(url, timeout=timeout)
        if page_text:
            snippet = short_text(page_text, 800)
            answer = (
                "OK\n"
                f"- **Riferimento**: {title}\n"
                f"- **Sintesi web**: {snippet}\n"
            )
            return answer, [url], best_score

        # Fallback: snippet SERP
        snippet_serp = (top.get("snippet") or "").strip()
        if not snippet_serp:
            snippet_serp = "Contenuto tecnico pertinente individuato. Apri la fonte per i dettagli."
        answer = (
            "OK\n"
            f"- **Riferimento**: {title}\n"
            f"- **Sintesi web**: {snippet_serp}\n"
        )
        return answer, [url], best_score

    return "", sources, best_score

# -------------------- Smart lookup (secondo giro senza domini) ---------------
KEYWORDS_FORCE_WEB = re.compile(
    r"\b(ctf|ctl|diapason|p560|hi[- ]?bond|lamiera|connettore|laterocemento|collaborante|solaio)\b",
    re.I
)

def force_web_needed(nq: str) -> bool:
    return bool(KEYWORDS_FORCE_WEB.search(nq))

def web_lookup_smart(q: str) -> Tuple[str, List[str], float]:
    # 1) con domini preferiti
    ans, srcs, sc = web_lookup(q, min_score=MIN_WEB_SCORE, timeout=WEB_TIMEOUT,
                               retries=WEB_RETRIES, domains=PREFERRED_DOMAINS)
    if ans:
        return ans, srcs, sc
    # 2) senza filtro domini (rank preferisce comunque Tecnaria)
    ans2, srcs2, sc2 = web_lookup(q, min_score=MIN_WEB_SCORE, timeout=WEB_TIMEOUT,
                                  retries=WEB_RETRIES, domains=[])
    return ans2, srcs2, sc2

# ------------- CONTATTI dai file critici (no falsi positivi) -----------------
def _fmt(v): return str(v).strip() if v is not None else ""

def load_contacts_from_critici() -> Optional[str]:
    if not CRITICI_DIR or not os.path.isdir(CRITICI_DIR):
        return None
    for pat in ["*contatti*.json", "*contacts*.json", "*.json"]:
        for p in glob.glob(os.path.join(CRITICI_DIR, pat)):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            entries = data if isinstance(data, list) else [data]
            for obj in entries:
                if not isinstance(obj, dict):
                    continue
                rs  = obj.get("ragione_sociale") or obj.get("ragioneSociale") or obj.get("azienda") or ""
                piva= obj.get("piva") or obj.get("partita_iva") or obj.get("partitaIva") or ""
                sdi = obj.get("sdi") or obj.get("SDI") or ""
                ind = obj.get("indirizzo") or obj.get("address") or ""
                tel = obj.get("telefono") or obj.get("phone") or ""
                em  = obj.get("email") or obj.get("mail") or ""
                pec = obj.get("pec") or ""
                if any([rs, tel, em]):
                    lines = [
                        "OK",
                        f"- **Ragione sociale**: {_fmt(rs) or '—'}",
                        f"- **P.IVA**: {_fmt(piva) or '—'}   **SDI**: {_fmt(sdi) or '—'}",
                        f"- **Indirizzo**: {_fmt(ind) or '—'}",
                        f"- **Telefono**: {_fmt(tel) or '—'}",
                        f"- **Email**: {_fmt(em) or '—'}",
                        f"- **PEC**: {_fmt(pec) or '—'}",
                        "",
                        "**Fonti**",
                        f"- file critici · {os.path.basename(p)}"
                    ]
                    return "\n".join(lines)
    return None

def answer_contacts() -> str:
    c = load_contacts_from_critici()
    if c:
        return c
    return ("OK\n- **Ragione sociale**: TECNARIA S.p.A.\n- **Telefono**: +39 0424 502029\n- **Email**: info@tecnaria.com\n")

def is_explicit_contacts(nq: str) -> bool:
    if not re.search(r"\b(contatti|telefono|email|pec)\b", nq):
        return False
    if re.search(r"\b(ctf|ctl|diapason|p560|hi[- ]?bond|lamiera|solaio|connettore|posa|densita|eta|dop|ce)\b", nq):
        return False
    return True

# ----------------------------- FORMATTER ------------------------------------
def format_as_bot(core_text: str, sources: Optional[List[str]] = None) -> str:
    core = core_text or ""
    if core.strip().startswith("OK"):
        if sources and "\n**Fonti**" not in core:
            core = core.rstrip() + "\n\n**Fonti**\n" + "\n".join(f"- {u}" for u in sources) + "\n"
        return core
    out = "OK\n" + core.strip()
    if sources:
        out += "\n\n**Fonti**\n" + "\n".join(f"- {u}" for u in sources) + "\n"
    return out

# ------------------------- TEMPLATE: P560 (patentino) -----------------------
def answer_p560_template() -> str:
    return (
        "OK\n"
        "- **Abilitazione/Patentino**: Non è richiesto un patentino specifico per la SPIT P560. "
        "È necessaria una formazione interna secondo le istruzioni del costruttore.\n"
        "- **Formazione minima**: scelta propulsori, taratura potenza, prova su campione, verifica ancoraggio dei chiodi, gestione inceppamenti.\n"
        "- **DPI e sicurezza**: occhiali, guanti, protezione udito; operare su lamiera ben aderente; rispettare distanze dai bordi.\n"
        "- **Procedura di posa**: 1 connettore CTF = 2 chiodi HSBR14 con P560; potenza regolata in funzione di lamiera/trave.\n"
    )

def build_p560_from_web(sources: List[str]) -> str:
    base = answer_p560_template()
    if sources:
        base += "\n**Fonti**\n" + "\n".join(f"- {u}" for u in sources) + "\n"
    else:
        base += "\n**Fonti**\n- web (tecnaria.com)\n"
    return base

# ---------- TEMPLATE: CTF densità/fissaggio (commerciale tecnico) -----------
CTF_KEY = re.compile(r"\bctf\b", re.I)
DENSITY_KEYS = re.compile(r"\b(densit[aà]|quanti|numero|n[.\s]*connettori|pezzi|m2|m²|al\s*m2|per\s*m2)\b", re.I)
FIX_KEYS = re.compile(r"\b(fissagg|posa|chiod|hsbr\s*14|hsbr14|p560|spit\s*p560)\b", re.I)
HIBOND_KEYS = re.compile(r"\bhi[- ]?bond\b", re.I)

def is_ctf_density_question(nq: str) -> bool:
    if not CTF_KEY.search(nq):
        return False
    has_density = bool(DENSITY_KEYS.search(nq))
    has_fix = bool(FIX_KEYS.search(nq))
    return has_density or has_fix

def build_ctf_density_answer(sources: List[str]) -> str:
    base = (
        "OK\n"
        "- **Densità indicativa**: per preventivo/pre-dimensionamento si considerano **~6–8 connettori CTF/m²** "
        "(distribuzione più fitta presso appoggi/muri, più rada in mezzeria).\n"
        "- **Determinazione esatta**: il **numero reale** di connettori è dato dal **calcolo strutturale** "
        "(luci, carichi, profilo lamiera, cls, schema statico, verifiche a taglio/scorrimento e deformazioni).\n"
        "- **Fissaggio**: ogni **CTF** si posa su lamiera grecata con **2 chiodi HSBR14** sparati con **SPIT P560**, "
        "senza preforatura; regolare la potenza in funzione di lamiera/trave e fare una prova su campione.\n"
        "- **Note di posa**: lamiera **ben aderente** all’appoggio; rispetto **distanze dai bordi**; DPI (occhiali, guanti, "
        "protezione udito); controllo dell’**ancoraggio** dei chiodi.\n"
    )
    if sources:
        base += "\n**Fonti**\n" + "\n".join(f"- {u}" for u in sources) + "\n"
    else:
        base += "\n**Fonti**\n- https://tecnaria.com/download/homepage/CT_CATALOGO_IT.pdf\n"
    return base

# --------------- NUOVO TEMPLATE: CTF vs DIAPASON (pulito) -------------------
COMPARE_KEYS = re.compile(r"\b(differenz|confront|quando\s+usare|quale\s+scegliere)\b", re.I)
DIAPASON_KEY = re.compile(r"\bdiapason\b", re.I)

def is_ctf_vs_diapason(nq: str) -> bool:
    # Domanda che contiene CTF e Diapason o parole di confronto
    has_ctf = bool(CTF_KEY.search(nq))
    has_diap = bool(DIAPASON_KEY.search(nq))
    return (has_ctf and has_diap) or (has_ctf and bool(COMPARE_KEYS.search(nq))) or (has_diap and bool(COMPARE_KEYS.search(nq)))

def build_ctf_vs_diapason_answer(sources: List[str]) -> str:
    base = (
        "OK\n"
        "- **CTF**: connettore per **solaio collaborante acciaio–calcestruzzo** su **lamiera grecata**; posa rapida con **SPIT P560** e **2 chiodi HSBR14**/pezzo; ideale per **nuovi solai** o rinforzi dove è prevista/già presente la lamiera (es. **Hi-Bond**).\n"
        "- **Diapason**: sistema per **rinforzo di solai in laterocemento** esistenti; utilizza **fissaggi meccanici** (non la P560); indicato quando **non c’è lamiera** e si interviene dall’alto con getto collaborante.\n"
        "- **Quando usare**: **CTF** se c’è/si posa la lamiera grecata e serve **velocità di cantiere**; **Diapason** se si deve **consolidare laterocemento** senza lamiera, con intervento poco invasivo.\n"
        "- **Scelta pratica**: dipende da **tipologia solaio**, **carichi**, **vincoli di spessore**, **accessi** e **tempi**; il numero di connettori/ancoraggi è definito dal **calcolo strutturale**.\n"
    )
    if sources:
        base += "\n**Fonti**\n" + "\n".join(f"- {u}" for u in sources) + "\n"
    else:
        base += "\n**Fonti**\n- https://tecnaria.com/download/homepage/CT_CATALOGO_IT.pdf\n"
    return base

# ----------------------------- ROUTING --------------------------------------
def route_question_to_answer(raw_q: str) -> str:
    if not raw_q or not raw_q.strip():
        return "OK\n- **Domanda vuota**: inserisci una richiesta valida.\n"

    q = raw_q.strip()
    nq = normalize(q)

    # Contatti → SOLO se chiesti davvero
    if is_explicit_contacts(nq):
        return answer_contacts()

    # Regola forte: P560 + patentino/formazione → template + (fonti web se disponibili)
    if P560_PAT.search(nq) and LIC_PAT.search(nq):
        _, srcs, _ = web_lookup_smart(q)
        return build_p560_from_web(srcs)

    # NUOVO: Confronto CTF vs Diapason → sempre template pulito + fonti web
    if is_ctf_vs_diapason(nq):
        _, srcs, _ = web_lookup_smart(q)
        return build_ctf_vs_diapason_answer(srcs)

    # Domande su densità/fissaggio CTF → template tecnico + fonti web
    if is_ctf_density_question(nq):
        _, srcs, _ = web_lookup_smart(q)
        return build_ctf_density_answer(srcs)

    # Se contiene parole chiave tecniche, forza WEB con strategia smart
    if force_web_needed(nq):
        ans, srcs, _ = web_lookup_smart(q)
        if ans:
            return format_as_bot(ans, srcs)

    # Altrimenti normale web-first
    ans, srcs, _ = web_lookup(q, min_score=MIN_WEB_SCORE, timeout=WEB_TIMEOUT,
                              retries=WEB_RETRIES, domains=PREFERRED_DOMAINS)
    if ans:
        return format_as_bot(ans, srcs)

    # Fallback elegante
    return ("OK\n- **Non ho trovato una risposta affidabile sul web** (o la ricerca non è configurata). "
            "Puoi riformulare la domanda oppure posso fornirti i contatti Tecnaria.\n")

# ----------------------------- FASTAPI APP ----------------------------------
app = FastAPI(title="Tecnaria QA Bot", version="3.5.0")

# static + templates
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# CORS permissivo
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# Homepage: se esiste index.html lo serve; supporta anche ?q=... per test rapido
@app.get("/", response_class=HTMLResponse)
def root(request: Request, q: Optional[str] = Query(None)):
    if q and q.strip():
        ans = route_question_to_answer(q)
        return JSONResponse({"ok": True, "answer": ans})
    index_path = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.isfile(index_path):
        return templates.TemplateResponse("index.html", {"request": request})
    return JSONResponse({"service": "Tecnaria QA Bot",
                         "endpoints": ["/ping", "/health", "/ask (GET q=... | POST JSON/Form/Text)"]})

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
            "min_web_score": MIN_WEB_SCORE
        },
        "critici": {"dir": CRITICI_DIR, "exists": bool(CRITICI_DIR and os.path.isdir(CRITICI_DIR))},
    }

# Estrazione q robusta per POST
def _extract_q_sync(body_bytes: bytes, content_type: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower() if content_type else ""
    if "application/json" in ct:
        try:
            data = json.loads(body_bytes.decode("utf-8", errors="ignore") or "{}")
            return (data.get("q") or data.get("question") or "").strip()
        except Exception:
            return ""
    if "application/x-www-form-urlencoded" in ct or "multipart/form-data" in ct:
        try:
            s = body_bytes.decode("utf-8", errors="ignore")
            d = parse_qs(s, keep_blank_values=True)
            return (d.get("q", [""])[0] or d.get("question", [""])[0]).strip()
        except Exception:
            return ""
    try:
        return (body_bytes.decode("utf-8", errors="ignore") or "").strip()
    except Exception:
        return ""

@app.post("/ask")
async def ask_post(req: Request):
    q = ""
    try:
        data = await req.json()
        q = (data.get("q") or data.get("question") or "").strip()
    except Exception:
        pass
    if not q:
        body = await req.body()
        q = _extract_q_sync(body, req.headers.get("content-type") or "")
    ans = route_question_to_answer(q)
    return JSONResponse({"ok": True, "answer": ans})

@app.get("/ask")
async def ask_get(q: str = Query("", description="Domanda")):
    ans = route_question_to_answer((q or "").strip())
    return JSONResponse({"ok": True, "answer": ans})

# alias /api/ask
@app.post("/api/ask")
async def api_ask_post(req: Request):
    return await ask_post(req)

@app.get("/api/ask")
async def api_ask_get(q: str = Query("", description="Domanda")):
    return await ask_get(q)

# ----------------------------- MAIN (dev) -----------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
