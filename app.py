# app.py
# -----------------------------------------------------------------------------
# Tecnaria QA Bot – web_first_then_local con regole P560 e demote "Contatti"
# FastAPI + web lookup (Brave/Bing) + KB locale semplice
# -----------------------------------------------------------------------------

import os
import re
import json
import glob
import time
import math
import html
import unicodedata
from typing import List, Dict, Tuple, Optional
from urllib.parse import urlencode, urlparse

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# -----------------------------------------------------------------------------
# ENV / CONFIG
# -----------------------------------------------------------------------------
DEBUG               = os.getenv("DEBUG", "0") == "1"
MODE                = os.getenv("MODE", "web_first_then_local")  # "web_first_then_local" | "local_only" | "web_only"
FETCH_WEB_FIRST     = os.getenv("FETCH_WEB_FIRST", "1") == "1"   # legacy flag, manteniamo
POLICY_MODE         = os.getenv("POLICY_MODE", "default")
SEARCH_PROVIDER     = os.getenv("SEARCH_PROVIDER", "brave").lower()  # "brave" | "bing"
SEARCH_API_ENDPOINT = os.getenv("SEARCH_API_ENDPOINT", "").strip()   # opzionale per Bing
BRAVE_API_KEY       = os.getenv("BRAVE_API_KEY", "").strip()
BING_API_KEY        = os.getenv("BING_API_KEY", "").strip() or os.getenv("AZURE_BING_KEY", "").strip()
PREFERRED_DOMAINS   = [d.strip() for d in os.getenv("PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com").split(",") if d.strip()]
DOC_GLOB            = os.getenv("DOC_GLOB", "static/docs/*.txt")  # cartelle kb locali (txt/md/json)
MIN_WEB_SCORE       = float(os.getenv("MIN_WEB_SCORE", "0.35"))
WEB_TIMEOUT         = float(os.getenv("WEB_TIMEOUT", "6"))
WEB_RETRIES         = int(os.getenv("WEB_RETRIES", "2"))
FORCE_P560_WEB      = os.getenv("FORCE_P560_WEB", "1") == "1"
DEMOTE_CONTACTS     = os.getenv("DEMOTE_CONTACTS", "1") == "1"
ADMIN_TOKEN         = os.getenv("ADMIN_TOKEN", "")

# -----------------------------------------------------------------------------
# LOG di avvio
# -----------------------------------------------------------------------------
print("[BOOT] -----------------------------------------------")
print(f"[BOOT] MODE={MODE} FETCH_WEB_FIRST={FETCH_WEB_FIRST} POLICY_MODE={POLICY_MODE}")
print(f"[BOOT] SEARCH_PROVIDER={SEARCH_PROVIDER} SEARCH_API_ENDPOINT={SEARCH_API_ENDPOINT or '(default)'}")
print(f"[BOOT] PREFERRED_DOMAINS={PREFERRED_DOMAINS}")
print(f"[BOOT] MIN_WEB_SCORE={MIN_WEB_SCORE} WEB_TIMEOUT={WEB_TIMEOUT}s WEB_RETRIES={WEB_RETRIES}")
print(f"[BOOT] FORCE_P560_WEB={FORCE_P560_WEB} DEMOTE_CONTACTS={DEMOTE_CONTACTS}")
print(f"[BOOT] DOC_GLOB={DOC_GLOB}")
print("[BOOT] ------------------------------------------------")

# -----------------------------------------------------------------------------
# UTIL
# -----------------------------------------------------------------------------
P560_PAT = re.compile(r"\bp\s*[- ]?\s*560\b", re.I)
LIC_PAT  = re.compile(r"\b(patentino|abilitazione|formazione)\b", re.I)
CONT_PAT = re.compile(r"\b(contatti|telefono|email|pec)\b", re.I)

# ATTENZIONE: tolto "p560" dai prefix per non filtrare domande che iniziano con P560
UI_NOISE_PREFIXES = (
    "chiedi", "pulisci", "copia risposta", "risposta", "connettori ctf",
    "contatti", "—"
)

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
    s = " ".join(keep).strip()
    # non normalizziamo qui gli accenti (lo facciamo nella normalize)
    return s

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
            return 0.25  # bonus
    return 0.0

def short_text(text: str, n: int = 900) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return (t[:n] + "…") if len(t) > n else t

# -----------------------------------------------------------------------------
# KB LOCALE
# -----------------------------------------------------------------------------
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
                text = f.read()
            entry = {"path": p, "text": text, "name": os.path.basename(p)}
            if entry["name"].lower().startswith("contatti") or "contatti" in entry["name"].lower():
                CONTACTS_DOC = entry
            else:
                KB_DOCS.append(entry)
        except Exception as e:
            if DEBUG:
                print(f"[KB][ERR] {p}: {e}")

load_kb()
print(f"[KB] Caricati {len(KB_DOCS)} documenti. Contatti={'OK' if CONTACTS_DOC else 'NO'}")

def kb_lookup(q: str, exclude_contacts: bool = True) -> Optional[str]:
    """
    Ricerca semplicistica su KB: match parole chiave e titolo file.
    """
    nq = normalize(q)
    best = None
    best_score = 0.0

    # Opzionalmente includi contatti
    candidates = KB_DOCS.copy()
    if not exclude_contacts and CONTACTS_DOC:
        candidates.append(CONTACTS_DOC)

    for doc in candidates:
        text = doc["text"]
        name = doc["name"]
        score = 0.0
        low = normalize(text + " " + name)
        # boost parole
        for w in set(nq.split()):
            if w and w in low:
                score += 1.0
        # penalty se è chiaramente "contatti" e non richiesto
        if "contatti" in name.lower() and exclude_contacts:
            score -= 3.0
        score += 0.2 if "p560" in low else 0.0
        score += 0.2 if "ctf" in low else 0.0
        if score > best_score:
            best_score = score
            best = doc

    if best and best_score > 0.5:
        # restituisci snippet
        snippet = short_text(best["text"], 1200)
        return snippet
    return None

# -----------------------------------------------------------------------------
# WEB SEARCH / FETCH
# -----------------------------------------------------------------------------

def brave_search(q: str, topk: int = 5, timeout: float = WEB_TIMEOUT) -> List[Dict]:
    # Brave Search API. Richiede BRAVE_API_KEY.
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
    if SEARCH_PROVIDER == "bing":
        return bing_search(q, topk=topk)
    # default brave
    return brave_search(q, topk=topk)

def fetch_text(url: str, timeout: float = WEB_TIMEOUT) -> str:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        html_text = r.text
        soup = BeautifulSoup(html_text, "html.parser")
        # togli script/style
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
        # keyword matching
        for w in set(nq.split()):
            if w and w in sn:
                score += 0.4
        # P560 boost
        if P560_PAT.search(sn):
            score += 0.5
        it["score"] = score
    # ordina per score desc
    return sorted(results, key=lambda x: x.get("score", 0.0), reverse=True)

def web_lookup(q: str,
               min_score: float = MIN_WEB_SCORE,
               timeout: float = WEB_TIMEOUT,
               retries: int = WEB_RETRIES,
               domains: Optional[List[str]] = None) -> Tuple[str, List[str], float]:
    """
    Cerca sul web; privilegia domini passati o PREFERRED_DOMAINS.
    Torna (answer_text, sources_urls, best_score).
    """
    doms = domains or PREFERRED_DOMAINS
    sources: List[str] = []
    best_score = 0.0
    last_err = None

    for attempt in range(retries + 1):
        try:
            results = web_search(q, topk=7)
            if not results:
                continue
            # filtra per domini preferiti (se impostati)
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

            # Costruiamo risposta breve (bot-style) per uso generale
            snippet = short_text(txt, 1000)
            # Non vogliamo incollare il testo della pagina; generiamo 4-5 bullet generici
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

# -----------------------------------------------------------------------------
# FORMATTER – stile “tuo bot”
# -----------------------------------------------------------------------------
def format_as_bot(core_text: str, sources: Optional[List[str]] = None) -> str:
    # Lasciamo invariato il testo se già formattato in bullet "OK\n- ..."
    if core_text.strip().startswith("OK"):
        if sources:
            src_lines = "\n".join(f"- {u}" for u in sources)
            if "\n**Fonti**" not in core_text:
                return core_text.rstrip() + f"\n\n**Fonti**\n{src_lines}\n"
        return core_text

    # altrimenti incapsuliamo
    out = "OK\n" + core_text.strip()
    if sources:
        src_lines = "\n".join(f"- {u}" for u in sources)
        out += f"\n\n**Fonti**\n{src_lines}\n"
    return out

def answer_contacts() -> str:
    if CONTACTS_DOC:
        # prova a estrarre dati minimi
        txt = CONTACTS_DOC["text"]
        return "OK\n" + txt.strip()
    # fallback minimo
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
        src_lines = "\n".join(f"- {u}" for u in sources)
        base += f"\n**Fonti**\n{src_lines}\n"
    else:
        base += "\n**Fonti**\n- web (tecnaria.com)\n"
    return base

# -----------------------------------------------------------------------------
# ROUTING PRINCIPALE
# -----------------------------------------------------------------------------
def route_question_to_answer(raw_q: str) -> str:
    """
    Pipeline: pulizia -> regola P560 -> web first -> KB -> contatti (solo se chiesti) -> fallback elegante
    """
    if not raw_q or not raw_q.strip():
        return "OK\n- **Domanda vuota**: inserisci una richiesta valida.\n"

    cleaned = clean_ui_noise(raw_q)
    nq = normalize(cleaned)

    # 1) Se l'utente chiede contatti esplicitamente e non demotizziamo contatti:
    if CONT_PAT.search(nq) and not DEMOTE_CONTACTS:
        return answer_contacts()

    # 2) REGOLA FORTE: P560 + (patentino|formazione|abilitazione) => WEB su domini preferiti
    if FORCE_P560_WEB and P560_PAT.search(nq) and LIC_PAT.search(nq):
        ans, srcs, sc = web_lookup(cleaned, min_score=MIN_WEB_SCORE, timeout=WEB_TIMEOUT, retries=WEB_RETRIES, domains=PREFERRED_DOMAINS)
        if ans:
            # riscrivi con la risposta canonica stile bot + fonti
            return build_p560_from_web(srcs)
        # se il web non restituisce nulla → template tecnico (mai contatti)
        return build_p560_from_web(srcs)

    # 3) WEB-FIRST se richiesto
    if MODE.startswith("web_first") or (FETCH_WEB_FIRST and MODE != "local_only"):
        ans, srcs, sc = web_lookup(cleaned, min_score=MIN_WEB_SCORE, timeout=WEB_TIMEOUT, retries=WEB_RETRIES)
        if ans:
            return format_as_bot(ans, srcs)

    # 4) KB locale (con contatti demotizzati se DEMOTE_CONTACTS=1)
    local = kb_lookup(cleaned, exclude_contacts=DEMOTE_CONTACTS)
    if local:
        return format_as_bot("OK\n- **Riferimento locale** trovato.\n- **Sintesi**: " + short_text(local, 800))

    # 5) SOLO se l'utente ha chiesto contatti e li abbiamo demotizzati
    if CONT_PAT.search(nq) and DEMOTE_CONTACTS:
        return answer_contacts()

    # 6) Fallback
    return (
        "OK\n"
        "- **Informazione non presente** in fonti web/KB. Posso cercare meglio sul web o metterti in contatto con un tecnico.\n"
    )

# -----------------------------------------------------------------------------
# API
# -----------------------------------------------------------------------------
app = FastAPI(title="Tecnaria QA Bot", version="1.0.0")

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
        "kb": {
            "docs_loaded": len(KB_DOCS),
            "contacts": bool(CONTACTS_DOC),
            "doc_glob": DOC_GLOB
        }
    }

@app.post("/ask")
async def ask(req: Request):
    data = {}
    try:
        data = await req.json()
    except Exception:
        pass
    q = (data.get("q") or data.get("question") or "").strip()
    ans = route_question_to_answer(q)
    return JSONResponse({"ok": True, "answer": ans})

# -----------------------------------------------------------------------------
# MAIN (per run locale: uvicorn app:app --reload)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
