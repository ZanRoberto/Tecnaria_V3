# app.py
# Tecnaria QA Bot — Web-first + SINAPSI (fusione risposte curate), PDF-safe, anti-inglese
# Endpoints: /, /ping, /health, /ask (GET/POST), /api/ask
# ENV: SEARCH_PROVIDER=brave|bing + (BRAVE_API_KEY|BING_API_KEY)
# Opt: PREFERRED_DOMAINS, MIN_WEB_SCORE, CRITICI_DIR, DEBUG

import os
import re
import json
import glob
import html
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
print(f"[BOOT] SEARCH_PROVIDER={SEARCH_PROVIDER} PREFERRED_DOMAINS={PREFERRED_DOMAINS}")
print(f"[BOOT] MIN_WEB_SCORE={MIN_WEB_SCORE} WEB_TIMEOUT={WEB_TIMEOUT}s WEB_RETRIES={WEB_RETRIES}")
print(f"[BOOT] CRITICI_DIR={CRITICI_DIR} TEMPLATES_DIR={TEMPLATES_DIR} STATIC_DIR={STATIC_DIR}")
print("[BOOT] ------------------------------------------------")

# ----------------------------- UTIL -----------------------------------------
P560_PAT = re.compile(r"\bp\s*[- ]?\s*560\b", re.I)
LIC_PAT  = re.compile(r"\b(patentino|abilitazione|formazione)\b", re.I)

CTF_KEY  = re.compile(r"\bctf\b", re.I)
CTL_KEY  = re.compile(r"\bctl\b", re.I)
DIAPASON_KEY = re.compile(r"\bdiapason\b", re.I)

DENSITY_KEYS = re.compile(r"\b(densit[aà]|quanti|numero|n[.\s]*connettori|pezzi|m2|m²|al\s*m2|per\s*m2)\b", re.I)
FIX_KEYS     = re.compile(r"\b(fissagg|posa|chiod|hsbr\s*14|hsbr14|p560|spit\s*p560)\b", re.I)

COMPARE_KEYS = re.compile(r"\b(differenz|confront|quando\s+usare|quale\s+scegliere)\b", re.I)

TECH_KEYS = re.compile(
    r"\b(ctf|ctl|diapason|p560|hi[- ]?bond|lamiera|connettore|laterocemento|collaborante|solaio|eta|dop|ce)\b",
    re.I
)

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

def strip_html_snippet(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return html.unescape(s)

# detection linguistica ultra-leggera
IT_STOPS = set("il lo la gli le un una di del della dei delle che per con su tra fra come quanto quando quale perché dei nel nelle negli agli alle alla più meno già".split())
EN_STOPS = set("the a an of for with by from to in on and or than which when why how is are was were be been being this that these those as about into onto over under more less already".split())

def looks_italian(s: str) -> bool:
    if not s: return False
    low = normalize(s)
    it = sum(1 for w in low.split() if w in IT_STOPS)
    en = sum(1 for w in low.split() if w in EN_STOPS)
    return it >= en

def looks_english(s: str) -> bool:
    if not s: return False
    low = normalize(s)
    it = sum(1 for w in low.split() if w in IT_STOPS)
    en = sum(1 for w in low.split() if w in EN_STOPS)
    return en > it

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

        # PDF → snippet SERP pulito
        if is_pdf_url(url):
            snippet_serp = strip_html_snippet((top.get("snippet") or "").strip())
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

        # Fallback: snippet SERP (pulito)
        snippet_serp = strip_html_snippet((top.get("snippet") or "").strip())
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
def web_lookup_smart(q: str) -> Tuple[str, List[str], float]:
    ans, srcs, sc = web_lookup(q, min_score=MIN_WEB_SCORE, timeout=WEB_TIMEOUT,
                               retries=WEB_RETRIES, domains=PREFERRED_DOMAINS)
    if ans:
        return ans, srcs, sc
    ans2, srcs2, sc2 = web_lookup(q, min_score=MIN_WEB_SCORE, timeout=WEB_TIMEOUT,
                                  retries=WEB_RETRIES, domains=[])
    return ans2, srcs2, sc2

# ----------------------------- SINAPSI --------------------------------------
SINAPSI: List[Dict] = []

BUILTIN_SINAPSI = [
    {
        "id": "ctf_vs_diapason",
        "pattern": r"\b(ctf).*(diapason)|(diapason).*(ctf)|differenz|confront|quando\s+usare|quale\s+scegliere",
        "lang": "it",
        "answer": (
            "OK\n"
            "- **CTF**: connettore per **solaio collaborante acciaio–calcestruzzo** su **lamiera grecata**; posa rapida con **SPIT P560** e **2 chiodi HSBR14**/pezzo; ideale per **nuovi solai** o rinforzi dove è prevista/già presente la lamiera (es. **Hi-Bond**).\n"
            "- **Diapason**: sistema per **rinforzo di solai in laterocemento** esistenti; utilizza **fissaggi meccanici** (non la P560); indicato quando **non c’è lamiera** e si interviene dall’alto con getto collaborante.\n"
            "- **Quando usare**: **CTF** se c’è/si posa la lamiera grecata e serve **velocità di cantiere**; **Diapason** se si deve **consolidare laterocemento** senza lamiera, con intervento poco invasivo.\n"
            "- **Scelta pratica**: dipende da **tipologia solaio**, **carichi**, **vincoli di spessore**, **accessi** e **tempi**; il numero di connettori/ancoraggi è definito dal **calcolo strutturale**.\n"
            "\n**Fonti**\n- Sinapsi (curated)\n"
        )
    },
    {
        "id": "ctl_vs_diapason",
        "pattern": r"\b(ctl).*(diapason)|(diapason).*(ctl)|laterocemento.*ctl|ctl.*laterocemento|differenz|confront",
        "lang": "it",
        "answer": (
            "OK\n"
            "- **CTL**: connettore per **solaio legno–calcestruzzo**; lavora con viti su supporto ligneo. Non è adatto a **laterocemento**.\n"
            "- **Diapason**: sistema per **rinforzo solai in laterocemento** esistenti, con **fissaggi meccanici** e getto collaborante dall’alto.\n"
            "- **Quando usare**: **Diapason** per laterocemento fessurato/da consolidare; **CTL** solo per solai in **legno**.\n"
            "- **Scelta tecnica**: definita da **tipologia solaio** e **calcolo** (carichi, luci, deformazioni). Supporto di progetto disponibile.\n"
            "\n**Fonti**\n- Sinapsi (curated)\n"
        )
    },
    {
        "id": "p560_paten",
        "pattern": r"\bp\s*[- ]?\s*560\b.*(patentino|abilitazione|formazione)|\b(patentino|abilitazione|formazione)\b.*\bp\s*[- ]?\s*560\b",
        "lang": "it",
        "answer": (
            "OK\n"
            "- **Abilitazione/Patentino**: Non è richiesto un patentino specifico per la **SPIT P560**. È necessaria una **formazione interna** secondo le istruzioni del costruttore.\n"
            "- **Formazione minima**: scelta propulsori, taratura potenza, prova su campione, verifica ancoraggio dei chiodi, gestione inceppamenti.\n"
            "- **DPI e sicurezza**: occhiali, guanti, protezione udito; lamiera ben aderente; rispetto distanze dai bordi.\n"
            "- **Procedura**: 1 **CTF** = **2 chiodi HSBR14** con **P560**, senza preforatura.\n"
            "\n**Fonti**\n- Sinapsi (curated)\n"
        )
    },
    {
        "id": "ctf_density_fix",
        "pattern": r"\bctf\b.*(densit|quanti|m2|m²|numero|pezzi|fissagg|chiod|p560|hsbr)",
        "lang": "it",
        "answer": (
            "OK\n"
            "- **Densità indicativa**: **~6–8 CTF/m²** (più fitta agli appoggi, più rada in mezzeria).\n"
            "- **Determinazione esatta**: da **calcolo strutturale** (luci, carichi, profilo lamiera, cls, verifiche a taglio/scorrimento e deformazioni).\n"
            "- **Fissaggio**: **2 chiodi HSBR14** per connettore con **SPIT P560**, senza preforatura; regolare potenza e fare prova su campione.\n"
            "- **Sicurezza**: lamiera ben aderente, distanze dai bordi, DPI (occhiali/guanti/protezione udito).\n"
            "\n**Fonti**\n- Sinapsi (curated)\n"
        )
    },
    {
        "id": "ctf_hibond_advantages",
        "pattern": r"\bctf\b.*(hi[- ]?bond|lamiera)|hi[- ]?bond.*ctf|vantagg",
        "lang": "it",
        "answer": (
            "OK\n"
            "- **Compatibilità**: i **CTF** sono progettati per lamiere grecate certificate (es. **Hi-Bond**), garantendo piena collaborazione acciaio–calcestruzzo.\n"
            "- **Velocità di posa**: fissaggio dall’alto con **SPIT P560** e **2 chiodi HSBR14**, senza preforatura.\n"
            "- **Prestazioni**: maggiore rigidezza e capacità portante, deformazioni ridotte, luci maggiori a spessore contenuto.\n"
            "- **Sicurezza normativa**: documentazione **ETA** e tracciabilità.\n"
            "- **Efficienza**: tempi e costi di cantiere ridotti rispetto ad alternative non integrate.\n"
            "- **Supporto**: schede di posa, software e assistenza Tecnaria.\n"
            "\n**Fonti**\n- Sinapsi (curated)\n"
        )
    },
]

def load_sinapsi_from_file() -> List[Dict]:
    if not CRITICI_DIR or not os.path.isdir(CRITICI_DIR):
        return []
    for pat in ["*sinapsi*.json", "sinapsi_brain.json"]:
        for p in glob.glob(os.path.join(CRITICI_DIR, pat)):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
            except Exception as e:
                if DEBUG: print("[SINAPSI][ERR]", e)
    return []

SINAPSI = load_sinapsi_from_file() or BUILTIN_SINAPSI
print(f"[SINAPSI] Loaded {len(SINAPSI)} entries ({'file' if SINAPSI != BUILTIN_SINAPSI else 'builtin'})")

def sinapsi_match_answer(q: str) -> Optional[str]:
    """Se una regola SINAPSI matcha la domanda, restituisce la risposta curata."""
    nq = normalize(q)
    for entry in SINAPSI:
        pat = entry.get("pattern")
        ans = entry.get("answer")
        if not pat or not ans:
            continue
        try:
            if re.search(pat, nq, flags=re.I):
                return ans
        except re.error:
            # pattern non valido: skip
            continue
    return None

def merge_with_sinapsi(q: str, web_answer: str, web_sources: List[str]) -> str:
    """
    Fusiona: se SINAPSI ha risposta per q -> usa quella (autorità prioritaria).
    Se la domanda è in IT ma lo snippet web è EN -> ignora snippet e mostra risposta SINAPSI in IT.
    Altrimenti, se non c'è match SINAPSI -> lascia web_answer così com'è.
    """
    curated = sinapsi_match_answer(q)
    if curated:
        # Se c'è già "Fonti" dentro curated, ritorna as-is.
        if "\n**Fonti**" in curated:
            return curated
        # Altrimenti aggiungi etichetta Sinapsi
        return curated.rstrip() + "\n\n**Fonti**\n- Sinapsi (curated)\n"

    # No entry Sinapsi: se domanda in IT e web_answer sembra EN → togli snippet EN
    if looks_italian(q) and looks_english(web_answer):
        # rimuovi “Sintesi web: ...” e tieni solo Riferimento + fonte
        cleaned = re.sub(r"- \*\*Sintesi web\*\*:.*?(?:\n|$)", "", web_answer)
        if cleaned.strip().startswith("OK"):
            # aggiungi nota di traduzione evitando inglese
            cleaned += "\n- **Nota**: contenuto originale in inglese; per dettagli fare riferimento a documentazione italiana o assistenza Tecnaria.\n"
            if "\n**Fonti**" not in cleaned and web_sources:
                cleaned += "\n**Fonti**\n" + "\n".join(f"- {u}" for u in web_sources) + "\n"
            return cleaned
    return web_answer

# ----------------------------- CONTATTI -------------------------------------
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
    if TECH_KEYS.search(nq):
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

# ----------------------------- ROUTING --------------------------------------
def is_ctf_density_question(nq: str) -> bool:
    if not CTF_KEY.search(nq): return False
    return bool(DENSITY_KEYS.search(nq) or FIX_KEYS.search(nq))

def is_connector_vs_diapason(nq: str) -> bool:
    has_ctf = bool(CTF_KEY.search(nq))
    has_ctl = bool(CTL_KEY.search(nq))
    has_diap = bool(DIAPASON_KEY.search(nq))
    has_comp = bool(COMPARE_KEYS.search(nq))
    return (has_diap and (has_ctf or has_ctl)) or (has_comp and (has_ctf or has_ctl or has_diap))

def route_question_to_answer(raw_q: str) -> str:
    if not raw_q or not raw_q.strip():
        return "OK\n- **Domanda vuota**: inserisci una richiesta valida.\n"

    q = raw_q.strip()
    nq = normalize(q)

    # 0) Contatti → SOLO se chiesti davvero
    if is_explicit_contacts(nq):
        return answer_contacts()

    # 1) SINAPSI ha autorità: se esiste risposta curata → restituisci quella
    curated = sinapsi_match_answer(q)
    if curated:
        return curated if "\n**Fonti**" in curated else curated.rstrip() + "\n\n**Fonti**\n- Sinapsi (curated)\n"

    # 2) Regola forte: P560 + patentino/formazione → se manca SINAPSI sopra, fai web e poi fusiona
    if P560_PAT.search(nq) and LIC_PAT.search(nq):
        web_ans, web_srcs, _ = web_lookup_smart(q)
        fused = merge_with_sinapsi(q, web_ans or "", web_srcs)
        return fused or format_as_bot("OK\n- **Informazione non presente**.\n")

    # 3) Domande CTF densità/fissaggio → web + SINAPSI fusion
    if is_ctf_density_question(nq):
        web_ans, web_srcs, _ = web_lookup_smart(q)
        fused = merge_with_sinapsi(q, web_ans or "", web_srcs)
        return fused or format_as_bot("OK\n- **Informazione non presente**.\n")

    # 4) Confronti con Diapason → web + SINAPSI fusion
    if is_connector_vs_diapason(nq):
        web_ans, web_srcs, _ = web_lookup_smart(q)
        fused = merge_with_sinapsi(q, web_ans or "", web_srcs)
        return fused or format_as_bot("OK\n- **Informazione non presente**.\n")

    # 5) Tecnico: forza web smart; poi fusiona con SINAPSI (se snippet è inglese lo sostituisce)
    if TECH_KEYS.search(nq):
        web_ans, web_srcs, _ = web_lookup_smart(q)
        if web_ans:
            return merge_with_sinapsi(q, web_ans, web_srcs)

    # 6) Web-first standard
    web_ans, web_srcs, _ = web_lookup(q, min_score=MIN_WEB_SCORE, timeout=WEB_TIMEOUT,
                                      retries=WEB_RETRIES, domains=PREFERRED_DOMAINS)
    if web_ans:
        return merge_with_sinapsi(q, web_ans, web_srcs)

    # 7) Fallback
    return ("OK\n- **Non ho trovato una risposta affidabile**. Posso cercare meglio o metterti in contatto con un tecnico.\n")

# ----------------------------- FASTAPI APP ----------------------------------
app = FastAPI(title="Tecnaria QA Bot + SINAPSI", version="4.0.0")

# static + templates
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# CORS permissivo
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# Homepage: se esiste index.html lo serve; supporta anche ?q=...
@app.get("/", response_class=HTMLResponse)
def root(request: Request, q: Optional[str] = Query(None)):
    if q and q.strip():
        ans = route_question_to_answer(q)
        return JSONResponse({"ok": True, "answer": ans})
    index_path = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.isfile(index_path):
        return templates.TemplateResponse("index.html", {"request": request})
    return JSONResponse({"service": "Tecnaria QA Bot + SINAPSI",
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
        "sinapsi_entries": len(SINAPSI),
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
