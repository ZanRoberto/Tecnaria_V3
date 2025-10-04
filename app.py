# app.py
# -----------------------------------------------------------------------------
# Tecnaria QA Bot – Web → Local → Sinapsi (override/augment/postscript)
# FastAPI + Brave/Bing + KB locale semplice + formatter narrativo
# -----------------------------------------------------------------------------

import os
import re
import json
import glob
import time
import html
import unicodedata
from typing import List, Dict, Tuple, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse

# -----------------------------------------------------------------------------
# ENV / CONFIG
# -----------------------------------------------------------------------------
DEBUG               = os.getenv("DEBUG", "0") == "1"

# Modalità di routing (stringa informativa, la pipeline resta: Web → Local → Sinapsi)
MODE                = os.getenv("MODE", "web_first_then_local")

# Web Search
SEARCH_PROVIDER     = os.getenv("SEARCH_PROVIDER", "brave").lower()      # "brave" | "bing"
SEARCH_API_ENDPOINT = os.getenv("SEARCH_API_ENDPOINT", "").strip()
BRAVE_API_KEY       = os.getenv("BRAVE_API_KEY", "").strip()
BING_API_KEY        = (os.getenv("BING_API_KEY", "") or os.getenv("AZURE_BING_KEY", "")).strip()
PREFERRED_DOMAINS   = [d.strip() for d in os.getenv("PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com").split(",") if d.strip()]
MIN_WEB_SCORE       = float(os.getenv("MIN_WEB_SCORE", "0.35"))
WEB_TIMEOUT         = float(os.getenv("WEB_TIMEOUT", "6"))
WEB_RETRIES         = int(os.getenv("WEB_RETRIES", "2"))

# KB locale (txt/md/json semplici)
DOC_GLOB            = os.getenv("DOC_GLOB", "static/docs/*.txt")

# Cartella contenuti critici (Sinapsi)
CRITICI_DIR         = os.getenv("CRITICI_DIR", "static/data").strip()
SINAPSI_FILE        = os.path.join(CRITICI_DIR, os.getenv("SINAPSI_FILE", "sinapsi_rules.json"))

# Demote contatti (non proporli mai se non esplicitamente richiesti)
DEMOTE_CONTACTS     = os.getenv("DEMOTE_CONTACTS", "1") == "1"

# -----------------------------------------------------------------------------
# LOG AVVIO
# -----------------------------------------------------------------------------
print("[BOOT] -----------------------------------------------")
print(f"[BOOT] MODE={MODE}")
print(f"[BOOT] SEARCH_PROVIDER={SEARCH_PROVIDER} endpoint={SEARCH_API_ENDPOINT or '(default)'}")
print(f"[BOOT] PREFERRED_DOMAINS={PREFERRED_DOMAINS} MIN_WEB_SCORE={MIN_WEB_SCORE}")
print(f"[BOOT] WEB_TIMEOUT={WEB_TIMEOUT}s WEB_RETRIES={WEB_RETRIES}")
print(f"[BOOT] DOC_GLOB={DOC_GLOB}")
print(f"[BOOT] CRITICI_DIR={CRITICI_DIR} SINAPSI_FILE={SINAPSI_FILE}")
print("[BOOT] ------------------------------------------------")

# -----------------------------------------------------------------------------
# UTILS
# -----------------------------------------------------------------------------
UI_NOISE_PREFIXES = ("chiedi", "pulisci", "copia risposta", "risposta", "—", "p560", "contatti")

P560_PAT = re.compile(r"\bp\s*[- ]?\s*560\b", re.I)
LIC_PAT  = re.compile(r"\b(patentino|abilitazione|formazione)\b", re.I)
CONT_PAT = re.compile(r"\b(contatti|telefono|email|pec)\b", re.I)

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
            return 0.25  # bonus
    return 0.0

def short_text(text: str, n: int = 1000) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return (t[:n] + "…") if len(t) > n else t

def detect_lang_from_text(s: str) -> str:
    # euristico leggero per lingua output
    if re.search(r"[àèéìòù]", s.lower()):
        return "it"
    if re.search(r"[äöüß]", s.lower()):
        return "de"
    if re.search(r"[áéíóúñ]", s.lower()):
        return "es"
    # fallback inglese se niente indizi
    return "it" if re.search(r"[a-zàèéìòù]", s.lower()) else "it"

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
    nq = normalize(q)
    best = None
    best_score = 0.0

    candidates = KB_DOCS.copy()
    if not exclude_contacts and CONTACTS_DOC:
        candidates.append(CONTACTS_DOC)

    for doc in candidates:
        text = doc["text"]
        name = doc["name"]
        low = normalize(text + " " + name)
        score = 0.0
        for w in set(nq.split()):
            if w and w in low:
                score += 1.0
        if "contatti" in name.lower() and exclude_contacts:
            score -= 3.0
        if "ctf" in low:
            score += 0.2
        if "p560" in low:
            score += 0.2
        if score > best_score:
            best_score = score
            best = doc

    if best and best_score > 0.5:
        return short_text(best["text"], 1200)
    return None

# -----------------------------------------------------------------------------
# WEB SEARCH / FETCH
# -----------------------------------------------------------------------------
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
        items = []
        for it in data.get("webPages", {}).get("value", []):
            items.append({
                "title": it.get("name") or "",
                "url": it.get("url") or "",
                "snippet": it.get("snippet") or ""
            })
        return items
    except Exception as e:
        if DEBUG: print("[BING][ERR]", e)
        return []

def web_search(q: str, topk: int = 7) -> List[Dict]:
    if SEARCH_PROVIDER == "bing":
        return bing_search(q, topk=topk)
    return brave_search(q, topk=topk)

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
        if DEBUG: print("[FETCH][ERR]", url, e)
        return ""

def rank_results(q: str, results: List[Dict]) -> List[Dict]:
    nq = normalize(q)
    out = []
    for it in results:
        score = 0.0
        score += prefer_score_for_domain(it.get("url", ""))
        sn = normalize((it.get("title") or "") + " " + (it.get("snippet") or ""))
        for w in set(nq.split()):
            if w and w in sn:
                score += 0.4
        if P560_PAT.search(sn):
            score += 0.4
        it["score"] = score
        out.append(it)
    return sorted(out, key=lambda x: x.get("score", 0.0), reverse=True)

def web_lookup(q: str) -> Tuple[str, List[Dict], List[str], float]:
    """
    Ritorna: (core_text, ranked_results, used_sources, best_score)
    """
    used_sources: List[str] = []
    best_score = 0.0
    for _ in range(WEB_RETRIES + 1):
        results = web_search(q, topk=7)
        if not results:
            continue
        # filtra per domini preferiti se presenti
        filtered = [r for r in results if any(d in domain_of(r["url"]) for d in PREFERRED_DOMAINS)] or results
        ranked = rank_results(q, filtered)
        if not ranked:
            continue
        top = ranked[0]
        best_score = top.get("score", 0.0)
        if best_score < MIN_WEB_SCORE:
            continue
        txt = fetch_text(top["url"])
        if not txt:
            continue
        used_sources.append(top["url"])
        return short_text(txt, 1200), ranked, used_sources, best_score
    return "", [], used_sources, best_score

# -----------------------------------------------------------------------------
# SINAPSI RULES
# -----------------------------------------------------------------------------
SINAPSI_RULES: List[Dict] = []

def load_sinapsi():
    global SINAPSI_RULES
    SINAPSI_RULES = []
    try:
        if os.path.exists(SINAPSI_FILE):
            with open(SINAPSI_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                SINAPSI_RULES = data
    except Exception as e:
        if DEBUG:
            print("[SINAPSI][ERR] load:", e)

load_sinapsi()
print(f"[SINAPSI] Regole caricate: {len(SINAPSI_RULES)}")

# -----------------------------------------------------------------------------
# FORMATTER – "Narrativo Tecnico Commerciale"
# -----------------------------------------------------------------------------
A_TAG = re.compile(r"\*{1,2}|_{1,2}")  # rimuove ** e __ e *
HTML_TAGS = re.compile(r"<\s*\/?\s*(b|strong|em|i|u)\s*>", re.I)

def sanitize_markdown(s: str) -> str:
    # toglie i marcatori bold/italic grezzi e tag html elementari
    s = A_TAG.sub("", s or "")
    s = HTML_TAGS.sub("", s)
    s = s.replace("**", "").replace("__", "")
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s

def normalize_sources(raw: List[str]) -> List[Tuple[str, str]]:
    out = []
    seen = set()
    for u in raw:
        if not u or u in seen:
            continue
        seen.add(u)
        title = "tecnaria.com"
        try:
            tld = urlparse(u).netloc.replace("www.", "")
            if "tecnaria" in tld:
                title = "Tecnaria"
            elif "spit" in tld:
                title = "SPIT"
            else:
                title = tld
        except:
            pass
        out.append((title, u))
    return out

def format_narrative(core_text: str,
                     sinapsi_addon: Optional[str],
                     sources: List[str],
                     lang: str) -> str:
    """
    Produce una risposta pulita, narrativa, con elenco leggibile e fonti cliccabili.
    """
    # 1) pulizia base
    core = sanitize_markdown(core_text)

    # 2) se Sinapsi ha dato override → prendiamo quello e basta
    #    altrimenti se augment → lo appiccichiamo sotto forma di paragrafo finale
    addon = sanitize_markdown(sinapsi_addon or "")

    # 3) fonti
    src_pairs = normalize_sources(sources)
    if lang == "it":
        fonti_title = "Fonti"
    elif lang == "de":
        fonti_title = "Quellen"
    elif lang == "en":
        fonti_title = "Sources"
    else:
        fonti_title = "Fonti"

    # 4) Assemblaggio finale (testo piano, con link in markdown)
    blocks = []
    if core:
        blocks.append(core)
    if addon:
        blocks.append(addon)

    if src_pairs:
        src_lines = "\n".join([f"- [{name}]({url})" for name, url in src_pairs])
        blocks.append(f"{fonti_title}:\n{src_lines}")

    final = "\n\n".join(blocks).strip()
    return final if final else "Non ho trovato informazioni sufficienti."

# -----------------------------------------------------------------------------
# ROUTING & FUSIONE (Web → Local → Sinapsi)
# -----------------------------------------------------------------------------
def apply_sinapsi(q: str, base_text: str, sources: List[str], lang: str) -> str:
    """
    Applica regole Sinapsi (override/augment/postscript).
    Ritorna il testo finale già fuso.
    """
    n = normalize(q)
    out_text = base_text
    addon_texts: List[str] = []
    post_texts: List[str] = []

    for rule in SINAPSI_RULES:
        pat = rule.get("pattern", "")
        mode = (rule.get("mode") or "augment").lower()
        ans  = rule.get("answer", "").strip()
        if not pat or not ans:
            continue
        try:
            if re.search(pat, q, flags=re.I):
                if mode == "override":
                    # override totale
                    return format_narrative("", ans, sources, lang)
                elif mode == "augment":
                    addon_texts.append(ans)
                elif mode == "postscript":
                    post_texts.append(ans)
        except re.error:
            # pattern invalido: salta
            continue

    fused_addon = "\n".join(addon_texts).strip()
    fused_post  = "\n".join(post_texts).strip()

    # Se c'è postscript lo appendiamo alla fine come paragrafo separato
    if fused_post:
        fused_addon = (fused_addon + ("\n\n" if fused_addon else "") + fused_post).strip()

    return format_narrative(out_text, fused_addon, sources, lang)

def answer_contacts() -> str:
    if CONTACTS_DOC:
        return sanitize_markdown(CONTACTS_DOC["text"])
    return ("TECNARIA S.p.A.\n"
            "Telefono: +39 0424 502029\n"
            "Email: info@tecnaria.com")

def route_question_to_answer(raw_q: str) -> str:
    if not raw_q or not raw_q.strip():
        return "Domanda vuota: inserisci una richiesta valida."

    cleaned = clean_ui_noise(raw_q)
    nq = normalize(cleaned)
    lang = detect_lang_from_text(cleaned)

    # 1) Contatti espliciti (se non demotizzati)
    if CONT_PAT.search(nq) and not DEMOTE_CONTACTS:
        return answer_contacts()

    # 2) WEB FIRST
    web_text, ranked, used_sources, score = web_lookup(cleaned)

    # 3) se web non c'è, prova KB locale
    base_text = web_text
    if not base_text:
        local = kb_lookup(cleaned, exclude_contacts=DEMOTE_CONTACTS)
        if local:
            base_text = local

    # 4) se ancora niente e l'utente ha chiesto contatti
    if not base_text and CONT_PAT.search(nq) and DEMOTE_CONTACTS:
        return answer_contacts()

    # 5) applica Sinapsi (override/augment/postscript) e formatta in modo narrativo
    final = apply_sinapsi(cleaned, base_text, [r.get("url") for r in ranked[:3] if r.get("url")] or used_sources, lang)

    # 6) ultimo fallback pulito
    if not final.strip():
        return "Non ho trovato una risposta affidabile. Posso cercare meglio sul sito Tecnaria o metterti in contatto con un tecnico."

    return final

# -----------------------------------------------------------------------------
# FASTAPI
# -----------------------------------------------------------------------------
app = FastAPI(title="Tecnaria QA Bot", version="3.0.0")

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
        "critici": {
            "dir": os.path.abspath(CRITICI_DIR),
            "exists": os.path.isdir(CRITICI_DIR),
            "sinapsi_file": os.path.abspath(SINAPSI_FILE),
            "sinapsi_loaded": len(SINAPSI_RULES)
        }
    }

@app.get("/")
def home():
    # serve interfaccia se presente
    idx1 = os.path.join("static", "index.html")
    idx2 = os.path.join("templates", "index.html")
    if os.path.exists(idx1):
        return FileResponse(idx1, media_type="text/html; charset=utf-8")
    if os.path.exists(idx2):
        return FileResponse(idx2, media_type="text/html; charset=utf-8")
    return JSONResponse({"ok": True, "msg": "Use /ask or place static/index.html"})

@app.get("/ask")
def ask_get(q: Optional[str] = None):
    ans = route_question_to_answer(q or "")
    # Plain text per semplicità; la tua UI lo visualizza comunque bene
    return PlainTextResponse(ans, media_type="text/plain; charset=utf-8")

@app.post("/api/ask")
async def ask_post(req: Request):
    data = {}
    try:
        data = await req.json()
    except Exception:
        pass
    q = (data.get("q") or "").strip()
    ans = route_question_to_answer(q)
    return JSONResponse({"ok": True, "answer": ans})

# -----------------------------------------------------------------------------
# MAIN (esecuzione locale)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
