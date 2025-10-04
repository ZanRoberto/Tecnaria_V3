# app.py
# -----------------------------------------------------------------------------
# Tecnaria QA Bot – Web-first → Local KB → Sinapsi (override/augment/postscript)
# Stile narrativo (no markdown pesante), fonti cliccabili, UI opzionale.
# -----------------------------------------------------------------------------

import os, re, json, glob, time, unicodedata
from typing import List, Dict, Tuple, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# -----------------------------------------------------------------------------
# ENV / CONFIG
# -----------------------------------------------------------------------------
DEBUG               = os.getenv("DEBUG", "0") == "1"
MODE                = os.getenv("MODE", "web_first_then_local")  # web_first_then_local | web_only | local_only
SEARCH_PROVIDER     = os.getenv("SEARCH_PROVIDER", "brave").lower().strip()
BRAVE_API_KEY       = os.getenv("BRAVE_API_KEY", "").strip()
BING_API_KEY        = os.getenv("BING_API_KEY", "").strip() or os.getenv("AZURE_BING_KEY", "").strip()
SEARCH_API_ENDPOINT = os.getenv("SEARCH_API_ENDPOINT", "").strip()  # opzionale per Bing

PREFERRED_DOMAINS   = [d.strip().lower() for d in os.getenv("PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com").split(",") if d.strip()]
DOC_GLOB            = os.getenv("DOC_GLOB", "static/docs/*.txt")
CRITICI_DIR         = os.getenv("CRITICI_DIR", "static/data").strip()
SINAPSI_FILE        = os.getenv("SINAPSI_FILE", os.path.join(CRITICI_DIR, "sinapsi_rules.json"))

MIN_WEB_SCORE       = float(os.getenv("MIN_WEB_SCORE", "0.35"))
WEB_TIMEOUT         = float(os.getenv("WEB_TIMEOUT", "6"))
WEB_RETRIES         = int(os.getenv("WEB_RETRIES", "2"))

FORCE_P560_WEB      = os.getenv("FORCE_P560_WEB", "1") == "1"
DEMOTE_CONTACTS     = os.getenv("DEMOTE_CONTACTS", "1") == "1"

# traduzione opzionale: se deep_translator disponibile
try:
    from deep_translator import GoogleTranslator
    TRANSLATOR_OK = True
except Exception:
    TRANSLATOR_OK = False

# -----------------------------------------------------------------------------
# LOG avvio
# -----------------------------------------------------------------------------
print("[BOOT] -----------------------------------------------")
print(f"[BOOT] MODE={MODE}; SEARCH_PROVIDER={SEARCH_PROVIDER}")
print(f"[BOOT] PREF_DOMAINS={PREFERRED_DOMAINS}  MIN_WEB_SCORE={MIN_WEB_SCORE}  TIMEOUT={WEB_TIMEOUT}s  RETRIES={WEB_RETRIES}")
print(f"[BOOT] DOC_GLOB={DOC_GLOB}")
print(f"[BOOT] CRITICI_DIR={CRITICI_DIR}  SINAPSI_FILE={SINAPSI_FILE}")
print(f"[BOOT] TRANSLATION={'ON' if TRANSLATOR_OK else 'OFF'}")
print("[BOOT] ------------------------------------------------")

# -----------------------------------------------------------------------------
# UTIL pulizie / lingua
# -----------------------------------------------------------------------------
UI_STRIP_PREFIXES = ("chiedi", "pulisci", "copia risposta", "risposta", "p560", "connettori ctf", "contatti", "usa get (debug)")

def normalize(s: str) -> str:
    if not s: return ""
    t = unicodedata.normalize("NFKD", s)
    t = t.encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t

def clean_prompt(s: str) -> str:
    if not s: return ""
    lines = [ln for ln in s.splitlines() if ln.strip()]
    keep = []
    for l in lines:
        low = l.strip().lower()
        if any(low.startswith(p) for p in UI_STRIP_PREFIXES):
            continue
        keep.append(l)
    return " ".join(keep).strip()

def detect_lang(text: str) -> str:
    """Heuristica semplice: usa stopword minime per it/en/de/fr/es; fallback 'it'."""
    if not text: return "it"
    t = normalize(text)
    score = {"it":0, "en":0, "de":0, "fr":0, "es":0}
    sw = {
        "it": {"il","lo","la","che","per","con","senza","quando","quale","una","dei","degli","delle"},
        "en": {"the","and","for","with","without","when","which","a","of"},
        "de": {"der","die","das","und","mit","ohne","wann","welche","ein"},
        "fr": {"le","la","les","et","avec","sans","quand","quelle","un","une","des"},
        "es": {"el","la","los","y","con","sin","cuando","cual","una","de","del"}
    }
    words = set(re.findall(r"[a-z]+", t))
    for lang, bag in sw.items():
        score[lang] = len(words & bag)
    # pick max; default it
    lang = max(score, key=score.get)
    return lang or "it"

def translate_to(text: str, target_lang: str) -> str:
    if not text: return text
    if not TRANSLATOR_OK: 
        # fallback: se target è italiano o uguale, restituisci com’è
        return text
    try:
        if target_lang == "it":
            return GoogleTranslator(source="auto", target="it").translate(text)
        elif target_lang == "en":
            return GoogleTranslator(source="auto", target="en").translate(text)
        elif target_lang == "de":
            return GoogleTranslator(source="auto", target="de").translate(text)
        elif target_lang == "fr":
            return GoogleTranslator(source="auto", target="fr").translate(text)
        elif target_lang == "es":
            return GoogleTranslator(source="auto", target="es").translate(text)
        else:
            # default inglese se lingua esotica
            return GoogleTranslator(source="auto", target="en").translate(text)
    except Exception:
        return text

def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""

def short_text(text: str, n:int=900) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return (t[:n] + "…") if len(t) > n else t

# -----------------------------------------------------------------------------
# KB LOCALE (semplice)
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
            if DEBUG: print(f"[KB][ERR] {p}: {e}")

load_kb()

def kb_lookup(q: str, exclude_contacts: bool = True) -> Optional[str]:
    nq = normalize(q)
    best = None
    best_score = 0.0
    cands = KB_DOCS.copy()
    if not exclude_contacts and CONTACTS_DOC:
        cands.append(CONTACTS_DOC)
    for doc in cands:
        low = normalize(doc["text"] + " " + doc["name"])
        score = 0.0
        for w in set(nq.split()):
            if w and w in low:
                score += 1.0
        if "contatti" in doc["name"].lower() and exclude_contacts:
            score -= 3.0
        if score > best_score:
            best_score = score
            best = doc
    if best and best_score > 0.5:
        return short_text(best["text"], 1100)
    return None

# -----------------------------------------------------------------------------
# WEB SEARCH / FETCH
# -----------------------------------------------------------------------------
def brave_search(q: str, topk:int=5, timeout:float=WEB_TIMEOUT) -> List[Dict]:
    if not BRAVE_API_KEY: return []
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept":"application/json","X-Subscription-Token":BRAVE_API_KEY}
    params = {"q": q, "count": topk}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        out = []
        for it in data.get("web", {}).get("results", []):
            out.append({
                "title": it.get("title") or "",
                "url": it.get("url") or "",
                "snippet": it.get("description") or ""
            })
        return out
    except Exception as e:
        if DEBUG: print("[BRAVE][ERR]", e)
        return []

def bing_search(q: str, topk:int=5, timeout:float=WEB_TIMEOUT) -> List[Dict]:
    key = BING_API_KEY
    endpoint = SEARCH_API_ENDPOINT or "https://api.bing.microsoft.com/v7.0/search"
    if not key: return []
    headers = {"Ocp-Apim-Subscription-Key": key}
    params = {"q": q, "count": topk, "responseFilter": "Webpages"}
    try:
        r = requests.get(endpoint, headers=headers, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        out = []
        for it in data.get("webPages", {}).get("value", []):
            out.append({
                "title": it.get("name") or "",
                "url": it.get("url") or "",
                "snippet": it.get("snippet") or ""
            })
        return out
    except Exception as e:
        if DEBUG: print("[BING][ERR]", e)
        return []

def web_search(q: str, topk:int=6) -> List[Dict]:
    if SEARCH_PROVIDER == "bing":
        return bing_search(q, topk=topk)
    return brave_search(q, topk=topk)

def prefer_score_for_domain(url: str) -> float:
    d = domain_of(url)
    for pd in PREFERRED_DOMAINS:
        if pd in d:
            return 0.35
    return 0.0

def rank_results(q: str, results: List[Dict]) -> List[Dict]:
    nq = normalize(q)
    for it in results:
        s = 0.0
        s += prefer_score_for_domain(it.get("url",""))
        sn = normalize((it.get("title") or "") + " " + (it.get("snippet") or ""))
        for w in set(nq.split()):
            if w and w in sn: s += 0.25
        if re.search(r"\b(ctf|p560|diapason|lamiera|tecnaria)\b", sn, re.I):
            s += 0.3
        it["score"] = s
    return sorted(results, key=lambda x: x.get("score", 0.0), reverse=True)

def fetch_text(url: str, timeout: float = WEB_TIMEOUT) -> str:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"})
        r.raise_for_status()
        ctype = r.headers.get("Content-Type","").lower()
        if "pdf" in ctype:
            # niente binario: prova a descrivere
            return f"[PDF] {url}"
        html_text = r.text
        soup = BeautifulSoup(html_text, "html.parser")
        for tag in soup(["script","style","noscript"]):
            tag.decompose()
        text = soup.get_text("\n")
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        return text.strip()
    except Exception as e:
        if DEBUG: print("[FETCH][ERR]", url, e)
        return ""

def web_lookup(q: str, min_score: float = MIN_WEB_SCORE) -> Tuple[str, List[str], float]:
    last_sources: List[str] = []
    best_score = 0.0
    for _ in range(WEB_RETRIES+1):
        results = web_search(q, topk=8)
        if not results: 
            continue
        # filtra per domini preferiti se possibile ma consenti fallback
        ranked = rank_results(q, results)
        if not ranked: 
            continue
        # prova i primi 4 finché trovi un testo non vuoto
        for top in ranked[:4]:
            best_score = max(best_score, top.get("score",0.0))
            if best_score < min_score: 
                continue
            txt = fetch_text(top["url"])
            if not txt: 
                continue
            last_sources = [top["url"]]
            # costruzione narrativa breve
            if txt.startswith("[PDF]"):
                ans = "Riferimento tecnico in PDF pertinente all’argomento. Consulta la scheda per dettagli su posa, densità e requisiti."
            else:
                # estrai una mini-sintesi pulita
                blob = short_text(txt, 650)
                # rimuovi residui di layout
                blob = re.sub(r"\s+", " ", blob)
                ans = f"Sintesi dai contenuti tecnici pertinenti disponibili sul sito: {blob}"
            return ans, last_sources, best_score
    return "", last_sources, best_score

# -----------------------------------------------------------------------------
# SINAPSI
# -----------------------------------------------------------------------------
SINAPSI_RULES: List[Dict] = []

def load_sinapsi() -> int:
    global SINAPSI_RULES
    SINAPSI_RULES = []
    try:
        with open(SINAPSI_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                for it in data:
                    # normalizza campi
                    it.setdefault("mode", "augment")  # override | augment | postscript
                    it.setdefault("lang", "it")
                    it.setdefault("answer", "")
                    it.setdefault("id", "")
                    it.setdefault("pattern","")
                    SINAPSI_RULES.append(it)
    except Exception as e:
        if DEBUG: print("[SINAPSI][ERR]", e)
    return len(SINAPSI_RULES)

SINAPSI_COUNT = load_sinapsi()

def apply_sinapsi(q: str, base_text: str, lang_target: str) -> str:
    nq = q or ""
    applied = False
    augmented = base_text or ""
    post = []
    override_text = None

    for rule in SINAPSI_RULES:
        pat = rule.get("pattern","")
        mode = rule.get("mode","augment").lower()
        ans  = rule.get("answer","")
        if not pat or not ans: 
            continue
        try:
            if re.search(pat, nq, flags=re.I):
                applied = True
                if mode == "override":
                    override_text = ans
                elif mode == "augment":
                    # se già abbiamo testo web: lo manteniamo e aggiungiamo blocco sinapsi
                    augmented = (augmented.strip() + ("\n\n" if augmented else "") + ans.strip()).strip()
                elif mode == "postscript":
                    post.append(ans.strip())
        except re.error:
            continue

    if not applied:
        return base_text

    # compone output
    if override_text is not None:
        out = override_text
    else:
        out = augmented
        if post:
            out += "\n\nNota finale:\n" + "\n".join(post)

    # traduci se richiesto e possibile
    detected_out_lang = "it"  # sinapsi è in IT
    if lang_target != detected_out_lang:
        out = translate_to(out, lang_target)

    return out

# -----------------------------------------------------------------------------
# FORMATTER – stile narrativo pulito (no **, no bullet brutti)
# -----------------------------------------------------------------------------
def format_narrative(core: str, sources: Optional[List[str]]=None) -> str:
    # ripulisci markdown bold e strong
    text = core.replace("**", "")
    text = re.sub(r"</?strong>", "", text, flags=re.I)
    text = re.sub(r"</?em>", "", text, flags=re.I)
    text = re.sub(r"\s+\n", "\n", text)

    blocks = [t.strip() for t in text.strip().splitlines() if t.strip()]
    final = "\n".join(blocks)

    if sources:
        # tieni solo URL uniche e preferisci tecnaria
        uniq = []
        seen = set()
        for u in sources:
            if u not in seen:
                uniq.append(u); seen.add(u)
        uniq = sorted(uniq, key=lambda u: (0 if "tecnaria.com" in u else 1, u))
        # append sezione fonti come elenco link
        src_lines = "\n".join(f"- {u}" for u in uniq)
        final += "\n\nFonti:\n" + src_lines

    return final

# -----------------------------------------------------------------------------
# ROUTER principale
# -----------------------------------------------------------------------------
P560_PAT = re.compile(r"\bp\s*[- ]?\s*560\b", re.I)
LIC_PAT  = re.compile(r"\b(patentino|abilitazione|formazione)\b", re.I)
CONT_PAT = re.compile(r"\b(contatti|telefono|email|pec)\b", re.I)

def answer_contacts() -> str:
    if CONTACTS_DOC:
        return CONTACTS_DOC["text"].strip()
    return ("Ragione sociale: TECNARIA S.p.A.\n"
            "Telefono: +39 0424 502029\n"
            "Email: info@tecnaria.com")

def route_question(raw_q: str) -> Tuple[str, List[str]]:
    if not raw_q or not raw_q.strip():
        return ("Domanda vuota: inserisci una richiesta valida.", [])

    question = clean_prompt(raw_q)
    lang_in = detect_lang(question)

    # Regola forte: P560 + (patentino/formazione) → vai web su domini preferiti e usa testo guida
    if FORCE_P560_WEB and P560_PAT.search(question) and LIC_PAT.search(question):
        # prova web
        web_text, web_srcs, _ = web_lookup(question, min_score=MIN_WEB_SCORE)
        # risposta tecnica canonica
        canon = (
            "Abilitazione/Patentino: non è richiesto un patentino specifico per SPIT P560; "
            "è necessaria una formazione interna secondo le istruzioni del costruttore.\n"
            "Formazione minima: scelta propulsori, taratura potenza, prova su campione, "
            "verifica ancoraggio dei chiodi, gestione inceppamenti.\n"
            "DPI e sicurezza: occhiali, guanti, protezione udito; lamiera ben aderente; rispetto distanze dai bordi.\n"
            "Procedura di posa CTF: 2 chiodi HSBR14 per connettore con SPIT P560, senza preforatura."
        )
        # preferisci canon + fonti web (se trovate)
        base = canon
        sources = web_srcs
        # sinapsi può ancora rifinire (override non necessario qui)
        final = apply_sinapsi(question, base, lang_in)
        return final, sources

    # WEB FIRST (salvo local_only)
    sources = []
    base = ""
    if MODE != "local_only":
        web_text, web_srcs, score = web_lookup(question, min_score=MIN_WEB_SCORE)
        if web_text:
            base = web_text
            sources = web_srcs

    # KB come fallback / arricchimento
    if not base and MODE != "web_only":
        local = kb_lookup(question, exclude_contacts=DEMOTE_CONTACTS)
        if local:
            base = f"Sintesi da materiale locale disponibile: {local}"

    # Contatti se esplicitamente chiesti e demotizzati
    if not base and CONT_PAT.search(question):
        return (answer_contacts(), [])

    if not base:
        base = ("Non ho trovato una risposta affidabile nelle fonti disponibili in questo momento. "
                "Posso insistere nella ricerca sul sito Tecnaria o metterti in contatto con un tecnico.")

    # Applica Sinapsi (augment/postscript/override)
    base = apply_sinapsi(question, base, lang_in)

    # Formatta narrativamente + fonti
    out = format_narrative(base, sources)
    return out, sources

# -----------------------------------------------------------------------------
# FASTAPI
# -----------------------------------------------------------------------------
app = FastAPI(title="Tecnaria QA Bot", version="3.x")

# Static & UI
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
def home():
    # se c’è una UI custom in /static/index.html la serviamo
    idx_static = os.path.join("static", "index.html")
    if os.path.isfile(idx_static):
        return FileResponse(idx_static)
    # fallback: pagina endpoints
    html = """
<!doctype html><html lang="it"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tecnaria QA Bot</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,"Noto Sans",sans-serif;margin:0;color:#0a0a0a;background:#f7f7f8}
.wrap{max-width:980px;margin:0 auto;padding:24px}
h1{font-weight:700;margin:0 0 8px}
.card{background:#fff;border:1px solid #e6e6eb;border-radius:12px;padding:16px}
code,kbd{background:#f0f0f3;padding:2px 6px;border-radius:6px}
a{color:#0b67ff;text-decoration:none}
a:hover{text-decoration:underline}
</style>
</head><body><div class="wrap">
<h1>Tecnaria QA Bot</h1>
<p class="card">Endpoint:<br>
/ping<br>
/health<br>
/api/ask (GET q=... | POST JSON {"q":"..."})</p>
</div></body></html>"""
    return HTMLResponse(html)

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
        },
        "kb": {
            "docs_loaded": len(KB_DOCS),
            "contacts": bool(CONTACTS_DOC),
            "doc_glob": DOC_GLOB
        },
        "translation": {"available": TRANSLATOR_OK}
    }

@app.get("/api/ask")
def ask_get(q: Optional[str] = ""):
    text, _ = route_question(q or "")
    return {"ok": True, "answer": text}

@app.post("/api/ask")
async def ask_post(req: Request):
    payload = {}
    try:
        payload = await req.json()
    except Exception:
        pass
    q = (payload.get("q") or payload.get("question") or "").strip()
    text, _ = route_question(q)
    return JSONResponse({"ok": True, "answer": text})

# -----------------------------------------------------------------------------
# MAIN (sviluppo locale)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT","8000")), reload=True)
