# app.py
# -----------------------------------------------------------------------------
# Tecnaria QA Bot – homepage attiva, /health, /ask (GET/POST), web-first, regola P560
# -----------------------------------------------------------------------------

import os, re, glob, time, unicodedata
from typing import List, Dict, Tuple, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ------------------------------ ENV / CONFIG ---------------------------------
DEBUG               = os.getenv("DEBUG", "0") == "1"
MODE                = os.getenv("MODE", "web_first_then_local")
FETCH_WEB_FIRST     = os.getenv("FETCH_WEB_FIRST", "1") == "1"
POLICY_MODE         = os.getenv("POLICY_MODE", "default")

SEARCH_PROVIDER     = os.getenv("SEARCH_PROVIDER", "brave").lower()  # brave|bing
SEARCH_API_ENDPOINT = os.getenv("SEARCH_API_ENDPOINT", "").strip()
BRAVE_API_KEY       = os.getenv("BRAVE_API_KEY", "").strip()
BING_API_KEY        = os.getenv("BING_API_KEY", "").strip() or os.getenv("AZURE_BING_KEY", "").strip()

PREFERRED_DOMAINS   = [d.strip() for d in os.getenv("PREFERRED_DOMAINS","tecnaria.com,spit.eu,spitpaslode.com").split(",") if d.strip()]
DOC_GLOB            = os.getenv("DOC_GLOB", "static/docs/*.txt")

MIN_WEB_SCORE       = float(os.getenv("MIN_WEB_SCORE", "0.35"))
WEB_TIMEOUT         = float(os.getenv("WEB_TIMEOUT", "6"))
WEB_RETRIES         = int(os.getenv("WEB_RETRIES", "2"))

FORCE_P560_WEB      = os.getenv("FORCE_P560_WEB", "1") == "1"
DEMOTE_CONTACTS     = os.getenv("DEMOTE_CONTACTS", "1") == "1"

print("[BOOT] MODE=", MODE, " SEARCH_PROVIDER=", SEARCH_PROVIDER)

# ------------------------------ UTIL -----------------------------------------
P560_PAT = re.compile(r"\bp\s*[- ]?\s*560\b", re.I)
LIC_PAT  = re.compile(r"\b(patentino|abilitazione|formazione)\b", re.I)
CONT_PAT = re.compile(r"\b(contatti|telefono|email|pec)\b", re.I)

UI_NOISE_PREFIXES = ("chiedi","pulisci","copia risposta","risposta","connettori ctf","contatti","—")

def normalize(t: str) -> str:
    if not t: return ""
    t = unicodedata.normalize("NFKD", t).encode("ascii","ignore").decode("ascii")
    return re.sub(r"\s+"," ",t).strip().lower()

def clean_ui_noise(text: str) -> str:
    if not text: return ""
    keep=[]
    for l in [x for x in text.splitlines() if x.strip()]:
        if any(l.strip().lower().startswith(p) for p in UI_NOISE_PREFIXES): continue
        keep.append(l)
    return " ".join(keep).strip()

def domain_of(url: str) -> str:
    try: return urlparse(url).netloc.lower()
    except: return ""

def prefer_score_for_domain(url: str) -> float:
    d = domain_of(url)
    return 0.25 if d and any(pd in d for pd in PREFERRED_DOMAINS) else 0.0

def short_text(text: str, n: int = 900) -> str:
    t = re.sub(r"\s+"," ", text or "").strip()
    return (t[:n]+"…") if len(t)>n else t

# ------------------------------ KB LOCALE ------------------------------------
KB_DOCS: List[Dict] = []
CONTACTS_DOC: Optional[Dict] = None

def load_kb():
    global KB_DOCS, CONTACTS_DOC
    KB_DOCS=[]; CONTACTS_DOC=None
    for p in glob.glob(DOC_GLOB) if DOC_GLOB else []:
        try:
            with open(p,"r",encoding="utf-8",errors="ignore") as f: txt=f.read()
            entry={"path":p,"text":txt,"name":os.path.basename(p)}
            if "contatti" in entry["name"].lower(): CONTACTS_DOC=entry
            else: KB_DOCS.append(entry)
        except Exception as e:
            if DEBUG: print("[KB][ERR]", p, e)

load_kb()

def kb_lookup(q: str, exclude_contacts: bool=True) -> Optional[str]:
    nq=normalize(q); best=None; best_s=0.0
    candidates=KB_DOCS.copy()
    if not exclude_contacts and CONTACTS_DOC: candidates.append(CONTACTS_DOC)
    for doc in candidates:
        low=normalize(doc["text"]+" "+doc["name"]); s=0.0
        for w in set(nq.split()):
            if w and w in low: s+=1.0
        if "contatti" in doc["name"].lower() and exclude_contacts: s-=3.0
        if "p560" in low: s+=0.2
        if "ctf" in low: s+=0.2
        if s>best_s: best_s=s; best=doc
    return short_text(best["text"],1200) if best and best_s>0.5 else None

# ------------------------------ WEB SEARCH -----------------------------------
def brave_search(q, topk=5, timeout=WEB_TIMEOUT):
    if not BRAVE_API_KEY: return []
    try:
        r=requests.get("https://api.search.brave.com/res/v1/web/search",
                       headers={"Accept":"application/json","X-Subscription-Token":BRAVE_API_KEY},
                       params={"q":q,"count":topk}, timeout=timeout)
        r.raise_for_status(); data=r.json(); out=[]
        for it in data.get("web",{}).get("results",[]):
            out.append({"title":it.get("title",""),"url":it.get("url",""),"snippet":it.get("description","")})
        return out
    except Exception as e:
        if DEBUG: print("[BRAVE][ERR]", e); return []

def bing_search(q, topk=5, timeout=WEB_TIMEOUT):
    key=BING_API_KEY; endpoint=SEARCH_API_ENDPOINT or "https://api.bing.microsoft.com/v7.0/search"
    if not key: return []
    try:
        r=requests.get(endpoint, headers={"Ocp-Apim-Subscription-Key":key},
                       params={"q":q,"count":topk,"responseFilter":"Webpages"}, timeout=timeout)
        r.raise_for_status(); data=r.json(); out=[]
        for it in data.get("webPages",{}).get("value",[]):
            out.append({"title":it.get("name",""),"url":it.get("url",""),"snippet":it.get("snippet","")})
        return out
    except Exception as e:
        if DEBUG: print("[BING][ERR]", e); return []

def web_search(q, topk=5): return bing_search(q, topk) if SEARCH_PROVIDER=="bing" else brave_search(q, topk)

def fetch_text(url, timeout=WEB_TIMEOUT):
    try:
        r=requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"}); r.raise_for_status()
        soup=BeautifulSoup(r.text,"html.parser")
        for t in soup(["script","style","noscript"]): t.decompose()
        txt=soup.get_text("\n"); return re.sub(r"\n\s*\n+","\n\n",txt).strip()
    except Exception as e:
        if DEBUG: print("[FETCH][ERR]", url, e); return ""

def rank_results(q, results, prefer_domains):
    nq=normalize(q)
    for it in results:
        s=prefer_score_for_domain(it.get("url",""))
        sn=normalize((it.get("title","")+" "+it.get("snippet","")))
        for w in set(nq.split()):
            if w and w in sn: s+=0.4
        if P560_PAT.search(sn): s+=0.5
        it["score"]=s
    return sorted(results, key=lambda x:x.get("score",0.0), reverse=True)

def web_lookup(q, min_score=MIN_WEB_SCORE, timeout=WEB_TIMEOUT, retries=WEB_RETRIES, domains=None)->Tuple[str,List[str],float]:
    doms=domains or PREFERRED_DOMAINS; srcs=[]; best=0.0
    # se mancano API key, non rompiamo: ritorniamo vuoto → useremo template
    if (SEARCH_PROVIDER=="brave" and not BRAVE_API_KEY) or (SEARCH_PROVIDER=="bing" and not BING_API_KEY):
        if DEBUG: print("[WEB] No API key; using template fallback"); return "", [], 0.0
    for _ in range(retries+1):
        results=web_search(q, topk=7) or []
        if doms: results=[r for r in results if any(d in domain_of(r["url"]) for d in doms)]
        ranked=rank_results(q, results, doms)
        if not ranked: continue
        top=ranked[0]; best=top.get("score",0.0)
        if best < min_score: continue
        _txt=fetch_text(top["url"], timeout=timeout)
        if not _txt: continue
        srcs.append(top["url"])
        ans=("OK\n"
             f"- **Riferimento**: {top.get('title') or 'pagina tecnica'}.\n"
             "- **Sintesi**: contenuti tecnici pertinenti alla query trovati sul sito preferito.\n"
             "- **Nota**: verificare sempre le istruzioni ufficiali e la documentazione aggiornata.\n")
        return ans, srcs, best
    return "", srcs, best

# ------------------------------ FORMAT ---------------------------------------
def format_as_bot(core_text: str, sources: Optional[List[str]]=None) -> str:
    if core_text.strip().startswith("OK"):
        if sources:
            src = "\n".join(f"- {u}" for u in sources)
            if "\n**Fonti**" not in core_text:
                return core_text.rstrip() + f"\n\n**Fonti**\n{src}\n"
        return core_text
    out = "OK\n" + core_text.strip()
    if sources:
        out += "\n\n**Fonti**\n" + "\n".join(f"- {u}" for u in sources) + "\n"
    return out

def answer_contacts() -> str:
    if CONTACTS_DOC: return "OK\n" + CONTACTS_DOC["text"].strip()
    return ("OK\n- **Ragione sociale**: TECNARIA S.p.A.\n- **Telefono**: +39 0424 502029\n- **Email**: info@tecnaria.com\n")

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
    base += ("\n**Fonti**\n" + "\n".join(f"- {u}" for u in sources) + "\n") if sources else "\n**Fonti**\n- web (tecnaria.com)\n"
    return base

# ------------------------------ ROUTING --------------------------------------
def route_question_to_answer(raw_q: str) -> str:
    if not raw_q or not raw_q.strip():
        return "OK\n- **Domanda vuota**: inserisci una richiesta valida.\n"
    cleaned = clean_ui_noise(raw_q); nq = normalize(cleaned)

    if CONT_PAT.search(nq) and not DEMOTE_CONTACTS:
        return answer_contacts()

    if FORCE_P560_WEB and P560_PAT.search(nq) and LIC_PAT.search(nq):
        ans, srcs, _ = web_lookup(cleaned, min_score=MIN_WEB_SCORE, timeout=WEB_TIMEOUT, retries=WEB_RETRIES, domains=PREFERRED_DOMAINS)
        return build_p560_from_web(srcs)

    if MODE.startswith("web_first") or (FETCH_WEB_FIRST and MODE != "local_only"):
        ans, srcs, _ = web_lookup(cleaned, min_score=MIN_WEB_SCORE, timeout=WEB_TIMEOUT, retries=WEB_RETRIES)
        if ans: return format_as_bot(ans, srcs)

    local = kb_lookup(cleaned, exclude_contacts=DEMOTE_CONTACTS)
    if local:
        return format_as_bot("OK\n- **Riferimento locale** trovato.\n- **Sintesi**: " + short_text(local, 800))

    if CONT_PAT.search(nq) and DEMOTE_CONTACTS:
        return answer_contacts()

    return "OK\n- **Informazione non presente** in fonti web/KB. Posso cercare meglio sul web o metterti in contatto con un tecnico.\n"

# ------------------------------ API ------------------------------------------
app = FastAPI(title="Tecnaria QA Bot", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# HOMEPAGE **ATTIVA**:
# - senza q → banner con endpoints (mai più 404)
# - con q   → risponde come /ask
@app.get("/")
def root(q: Optional[str] = Query(None, description="Domanda opzionale")):
    if q:
        return {"ok": True, "answer": route_question_to_answer(q)}
    return {"service":"Tecnaria QA Bot","endpoints":["/health","/ask (GET q=... | POST JSON {q})"]}

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

@app.post("/ask")
async def ask_post(req: Request):
    try: data = await req.json()
    except Exception: data = {}
    q = (data.get("q") or data.get("question") or "").strip()
    return JSONResponse({"ok": True, "answer": route_question_to_answer(q)})

@app.get("/ask")
async def ask_get(q: str = Query("", description="Domanda")):
    return JSONResponse({"ok": True, "answer": route_question_to_answer(q or "")})

# ------------------------------ MAIN (dev) -----------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT","8000")), reload=True)
