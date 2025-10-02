import os
import json
import re
import unicodedata
from pathlib import Path
from typing import List, Dict, Tuple

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from openai import OpenAI

# ------------------- App setup -------------------
app = FastAPI(title="Tecnaria Bot - Web+Local")
templates = Jinja2Templates(directory="templates")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata.")
client = OpenAI(api_key=OPENAI_API_KEY)

OPENAI_MODEL    = os.getenv("OPENAI_MODEL", "gpt-4o")
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "brave")  # 'brave' o altro/vuoto per disattivare
FETCH_WEB_FIRST = os.getenv("FETCH_WEB_FIRST", "1") == "1"
SEARCH_TOPK     = int(os.getenv("SEARCH_TOPK", "6"))
DEBUG           = os.getenv("DEBUG", "0") == "1"

# ------------------- Input model (tollerante) -------------------
class AskRequest(BaseModel):
    question: str | None = None
    message:  str | None = None
    Domanda:  str | None = None
    q:        str | None = None
    def text(self) -> str:
        for k in (self.question, self.message, self.Domanda, self.q):
            if k:
                return k
        return ""

# ------------------- Paths e config locali -------------------
CRITICI_DIR = os.environ.get("CRITICI_DIR", "static/static/data/critici")
LOCAL_DOCS_DIR = "static/docs"

# Hotfix CTF (risposta deterministica)
CTF_HOTFIX = os.environ.get("CTF_HOTFIX", "0") == "1"
CTF_CODES_INLINE = os.environ.get(
    "CTF_CODES_INLINE",
    "CTF020,CTF025,CTF030,CTF040,CTF060,CTF070,CTF080,CTF090,CTF105,CTF125,CTF135"
)

# ------------------- Normalizzazione testo -------------------
def _norma_txt(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))
    s = s.lower()
    return re.sub(r"[^a-z0-9/ ]+", "", s)

# ------------------- SENSITIVE/LOCAL DATA (solo file) -------------------
# Mappatura parole chiave -> file locale (coerente con la tua cartella "critici")
SENSITIVE_FILES = {
    # Contatti
    "contatti": "contatti.json", "contatto": "contatti.json", "telefono": "contatti.json",
    "tel ": "contatti.json", "email": "contatti.json", "mail": "contatti.json",
    "pec": "contatti.json", "sdi": "contatti.json", "p.iva": "contatti.json",
    "partita iva": "contatti.json", "indirizzo": "contatti.json", "sede": "contatti.json",
    "dove siamo": "contatti.json",
    # Banca (nel tuo repo è "bancari.json")
    "iban": "bancari.json", "bonifico": "bancari.json", "conto": "bancari.json",
    "coordinate bancarie": "bancari.json",
    # Pagamenti
    "pagamento": "pagamenti.json", "pagamenti": "pagamenti.json",
    "metodi di pagamento": "pagamenti.json",
}

_SENSITIVE_KWS = re.compile(
    r"\b(contatt\w*|telefono|tel\.?|mail|email|pec|sdi|p\.?\s*iva|partita\s*iva|indirizzo|sede|dove\s+siamo|iban|bonifico|conto|banc\w*|pagamento|pagamenti|metod[ei]\s+di\s+pagamento)\b",
    re.IGNORECASE
)

def find_sensitive_file_for_question(q: str) -> str | None:
    if not q:
        return None
    ql = q.lower()
    for kw, fname in SENSITIVE_FILES.items():
        if kw in ql:
            fp = Path(CRITICI_DIR) / fname
            if fp.exists():
                return str(fp)  # file trovato
            else:
                return ""       # richiesto ma file mancante
    if _SENSITIVE_KWS.search(ql):
        return "" if not Path(CRITICI_DIR).exists() else None
    return None

def load_json_file(path: str) -> dict | None:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        if not raw.strip():
            if DEBUG: print(f"[CRITICI] File vuoto: {path}")
            return None
        return json.loads(raw)
    except Exception as e:
        if DEBUG: print(f"[CRITICI] JSON non valido in {path}: {e}")
        return None

def format_from_contatti(data: dict) -> str:
    rs   = data.get("ragione_sociale") or "TECNARIA S.p.A."
    piva = data.get("piva") or data.get("partita_iva") or "Dato non disponibile"
    sdi  = data.get("sdi")  or "Dato non disponibile"
    tel  = data.get("telefono") or "Dato non disponibile"
    email= data.get("email") or "Dato non disponibile"
    pec  = data.get("pec") or "—"
    ind  = data.get("indirizzo") or {}
    addr = ", ".join([ind.get("via",""), ind.get("cap",""), ind.get("citta",""),
                      ind.get("provincia",""), ind.get("stato","")]).replace(" ,","").strip(" ,")
    return (
        f"- **Ragione sociale**: {rs}\n"
        f"- **P.IVA**: {piva}   **SDI**: {sdi}\n"
        f"- **Indirizzo**: {addr or 'Dato non disponibile'}\n"
        f"- **Telefono**: {tel}\n"
        f"- **Email**: {email}\n"
        f"- **PEC**: {pec}\n\n"
        "**Fonti**\n- file locale · contatti.json"
    )

def format_from_bank(data: dict) -> str:
    iban  = data.get("iban") or data.get("conto") or "Dato non disponibile"
    intes = data.get("intestatario", "Dato non disponibile")
    banca = data.get("banca", "Dato non disponibile")
    bic   = data.get("bic", data.get("swift", "Dato non disponibile"))
    note  = data.get("note", "-")
    return (
        f"- **Intestatario**: {intes}\n"
        f"- **Banca**: {banca}\n"
        f"- **IBAN**: {iban}\n"
        f"- **BIC/SWIFT**: {bic}\n"
        f"- **Note**: {note or '-'}\n\n"
        "**Fonti**\n- file locale · bancari.json"
    )

def format_from_pagamenti(data: dict) -> str:
    lines = []
    for m in data.get("metodi", []):
        lines.append(f"- **{m.get('tipo','metodo')}**: {m.get('istruzioni','')}")
    if not lines:
        lines = ["- (nessun metodo di pagamento presente)"]
    note = data.get("note")
    if note:
        lines.append(f"- **Note**: {note}")
    return "\n".join(lines) + "\n\n**Fonti**\n- file locale · pagamenti.json"

def format_sensitive_answer_from_file(path: str) -> str:
    data = load_json_file(path)
    if not data:
        return "- Dato non disponibile nel file locale.\n\n**Fonti**\n- file locale"
    name = Path(path).name
    if name == "contatti.json":
        return format_from_contatti(data)
    if name == "bancari.json":
        return format_from_bank(data)
    if name == "pagamenti.json":
        return format_from_pagamenti(data)
    pretty = json.dumps(data, indent=2, ensure_ascii=False)
    return f"- Dati:\n```\n{pretty[:1000]}\n```\n\n**Fonti**\n- file locale · {name}"

# ------------------- CRITICI: FAQ deterministiche -------------------
def load_critici_faq(dir_path: str = CRITICI_DIR):
    qa_entries = []
    files = []
    if not os.path.isdir(dir_path):
        return qa_entries, files
    for p in Path(dir_path).glob("*.json"):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            for item in doc.get("faq", []):
                triggers = [_norma_txt(q) for q in item.get("q", [])]
                qa_entries.append({"triggers": triggers, "answer": item.get("a","")})
            files.append(p.name)
        except Exception as e:
            if DEBUG: print(f"[critici] errore su {p}: {e}")
    return qa_entries, files

CRITICI_QA, CRITICI_FILES = load_critici_faq()

def answer_from_critici(user_q: str) -> str | None:
    if not user_q:
        return None
    nq = _norma_txt(user_q)
    for qa in CRITICI_QA:
        for trig in qa["triggers"]:
            if nq == trig or trig in nq:
                return qa["answer"]
    # micro-fallback per “CTF 12/xx”
    if "ctf" in nq and "12/" in nq:
        return ("La notazione “CTF 12/xx” è equivalente al codice con altezza xx: "
                "12/40→CTF040, 12/60→CTF060, 12/105→CTF105, 12/125→CTF125, 12/135→CTF135.")
    return None

# ------------------- Hotfix CTF (risposta certa) -------------------
def ctf_hotfix_answer(user_q: str) -> str | None:
    if not CTF_HOTFIX:
        return None
    nq = _norma_txt(user_q)
    trigger_codici = ("ctf" in nq) and any(k in nq for k in ["codici", "codice", "codes", "lista"])
    trigger_formato = ("ctf" in nq) and ("12/" in nq or "12 " in nq)
    if trigger_codici:
        return (f"I codici ufficiali dei connettori CTF Tecnaria sono: {CTF_CODES_INLINE}. "
                "Ø12 mm; fissaggio con chiodatrice SPIT P560.")
    if trigger_formato:
        return ("La notazione “CTF 12/xx” è equivalente al codice con altezza xx: "
                "12/40→CTF040, 12/60→CTF060, 12/105→CTF105, 12/125→CTF125, 12/135→CTF135.")
    return None

# ------------------- Helpers (web/local generali) -------------------
def expand_query_if_needed(query: str) -> str:
    q = query.lower()
    if "p560" in q and "spit" not in q:
        return query + " SPIT chiodatrice pistola sparachiodi Tecnaria HSBR14"
    return query

def _prefer_score(url: str) -> int:
    preferred = [d.strip().lower() for d in (os.getenv("PREFERRED_DOMAINS") or "tecnaria.com,spit.eu,spitpaslode.com,ordini.tecnaria.com").split(",") if d.strip()]
    return 1 if any(d in url.lower() for d in preferred) else 0

def _looks_like_p560_misclassified(text: str) -> bool:
    t = (text or "").lower()
    return ("p560" in t or "spit p560" in t) and ("connettor" in t) and not any(w in t for w in ["chiodatrice","pistola","sparachiodi"])

def _force_bullets_and_sources(text: str, web_hits: List[Dict], local_docs: Dict[str, str]) -> str:
    txt = (text or "").strip()
    has_sources = ("**Fonti**" in txt) or ("\nFonti\n" in txt) or ("\nFonti:" in txt)
    if not has_sources:
        sources = [h["url"] for h in web_hits] if web_hits else list(local_docs.keys())
        if sources:
            txt += "\n\n**Fonti**\n" + "\n".join(f"- {s}" for s in sources)
        else:
            txt += "\n\n**Fonti**\n- (nessuna fonte trovata)"
    return txt

# ------------------- Web search (Brave, server-side) -------------------
async def web_search(query: str, topk: int = 6) -> List[Dict]:
    if SEARCH_PROVIDER.lower() != "brave":
        return []
    key = os.getenv("BRAVE_API_KEY")
    if not key:
        if DEBUG:
            print("[BRAVE] manca BRAVE_API_KEY (il bot funzionerà solo con basi locali)")
        return []
    query = expand_query_if_needed(query)
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"X-Subscription-Token": key, "User-Agent": "Mozilla/5.0"}
    params = {"q": query, "count": topk}
    async with httpx.AsyncClient(follow_redirects=True, timeout=18.0) as ac:
        r = await ac.get(url, headers=headers, params=params)
        if DEBUG:
            print("[BRAVE] status:", r.status_code)
            try:
                print("[BRAVE] body sample:", r.text[:250].replace("\n", " "))
            except Exception:
                pass
        if r.status_code != 200:
            return []
        data = r.json()
        results = []
        for item in (data.get("web", {}).get("results") or [])[:topk]:
            u = item.get("url")
            if u:
                results.append({"name": item.get("title", "") or u, "url": u})
        results.sort(key=lambda x: _prefer_score(x["url"]), reverse=True)
        return results

# ------------------- Fetch & Extract (codici prodotto) -------------------
async def _fetch_text_for_regex(url: str, timeout: float = 18.0) -> str:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as ac:
            r = await ac.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                return ""
            ct = (r.headers.get("content-type") or "").lower()
            if "pdf" in ct or url.lower().endswith(".pdf"):
                try:
                    return r.content.decode("latin-1", errors="ignore")
                except Exception:
                    return r.content.decode("utf-8", errors="ignore")
            return r.text or ""
    except Exception as e:
        if DEBUG: print("[FETCH] err:", url, e)
        return ""

_CODE_PATTERNS = {
    "ctf": re.compile(r"\bCTF\d{3}\b", re.IGNORECASE),
    "ctl": re.compile(r"\bCTL\d{3}\b", re.IGNORECASE),
    "ctcem": re.compile(r"\bCTCEM\d{3}\b", re.IGNORECASE),
    "vcem": re.compile(r"\bVCEM\d{3}\b", re.IGNORECASE),
    "diapason": re.compile(r"\bDIAPASON\d{2,4}\b", re.IGNORECASE),
}
GENERIC_CODE = re.compile(r"\b[A-Z]{2,8}\d{2,4}\b")
FAMILIES = ["ctf", "ctl", "ctcem", "vcem", "diapason"]
_ALLOWED_DOMAINS = ("tecnaria.com", "ordini.tecnaria.com")

async def _collect_hits_from_queries(base_query: str, max_links: int) -> List[str]:
    queries = [f"site:tecnaria.com {base_query}",
               f"site:tecnaria.com filetype:pdf {base_query}"]
    urls: List[str] = []
    seen = set()
    for q in queries:
        hits = await web_search(q, topk=max_links) or []
        for h in hits:
            url = (h.get("url") or "").strip()
            if not url:
                continue
            if not any(dom in url.lower() for dom in _ALLOWED_DOMAINS):
                continue
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
    return urls[:max_links]

async def find_product_codes_from_web(query: str, family: str, max_links: int = 10) -> Tuple[List[str], List[str]]:
    base_q = query
    urls = await _collect_hits_from_queries(base_q, max_links)
    pattern = _CODE_PATTERNS.get(family.lower()) or GENERIC_CODE

    codes: set[str] = set()
    used_sources: List[str] = []

    for url in urls:
        text = await _fetch_text_for_regex(url)
        if not text:
            continue
        found = set(m.upper() for m in pattern.findall(text))
        if found:
            codes |= found
            used_sources.append(url)

    if not codes:
        hint = family.upper() if family != "generic" else "CTF CTL VCEM CTCEM DIAPASON"
        urls2 = await _collect_hits_from_queries(hint, max_links)
        for url in urls2:
            if url in used_sources:
                continue
            text = await _fetch_text_for_regex(url)
            if not text:
                continue
            found = set(m.upper() for m in pattern.findall(text))
            if found:
                codes |= found
                used_sources.append(url)

    def _num_key(c: str) -> int:
        try:
            return int(re.findall(r"(\d{2,4})", c)[0])
        except Exception:
            return 0

    sorted_codes = sorted(codes, key=_num_key)
    return sorted_codes, used_sources[:6]

# ------------------- Local docs (txt liberi) -------------------
def load_local_docs() -> Dict[str, str]:
    docs: Dict[str, str] = {}
    base = LOCAL_DOCS_DIR
    if not os.path.isdir(base):
        return docs
    for fn in os.listdir(base):
        if fn.lower().endswith(".txt"):
            try:
                with open(os.path.join(base, fn), "r", encoding="utf-8") as f:
                    docs[fn] = f.read()
            except Exception:
                continue
    return docs

# ------------------- System prompt -------------------
SYSTEM_PROMPT = """
Parli come il MIGLIOR TECNICO-COMMERCIALE di Tecnaria S.p.A. (Bassano del Grappa):
competente, chiaro, autorevole, propositivo e vicino al cliente.

TASSONOMIA OBBLIGATORIA (NON VIOLARE)
- P560 / SPIT P560 = chiodatrice a sparo (pistola sparachiodi) per fissaggi su ACCIAIO/LAMIERA. NON è un connettore.
  • Usata nei sistemi CTF (lamiera grecata + calcestruzzo) con chiodi HSBR14.
  • Usata anche per fissare connettori su TRAVI IN ACCIAIO (es. VCEM/altre configurazioni su acciaio).
  • NON si usa per connettori su LEGNO (CTL/CTCEM: fissaggi meccanici a vite/bullone).
- CTF = connettori per solai collaboranti ACCIAIO-CALCESTRUZZO su lamiera grecata; fissaggio a sparo (HSBR14) con P560.
- CTL = connettori per solai collaboranti LEGNO-CALCESTRUZZO; fissaggio con VITI, NO P560.
- DIAPASON (laterocemento) = fissaggi meccanici (bulloni/barre), NO P560.

REGOLE DI RISPOSTA (OBBLIGATORIE)
- Ordine fonti: WEB → poi documenti locali. Se il web è vuoto, usa i locali; se comunque non trovi, dillo.
- Stile TECNICO-COMMERCIALE in ITALIANO.
- OUTPUT SEMPRE in 5–7 BULLET sintetici e pratici (vantaggi, efficienza in cantiere, sicurezza/certificazioni quando rilevanti).
- CHIUDI SEMPRE con una sezione **Fonti** con URL (se web) o “file locale”.
- NON annunciare ricerche/attese (“sto cercando…”, “un momento…”): fornisci direttamente il risultato.
- NON confondere mai P560 con un connettore. Se la domanda contiene “P560”, chiarisci esplicitamente che è una chiodatrice.
""".strip()

# ------------------- Ambito aziendale (solo temi Tecnaria) -------------------
SCOPE_KWS = re.compile(
    r"\b(tecnaria|ctf|ctl|ctcem|vcem|diapason|lamiera|grecata|solai?|p560|spit|connettor\w+|"
    r"acciaio|legno|laterocemento|collaborant\w+|eta|posa|chiodi|hsbr14)\b",
    re.IGNORECASE
)

# Intent “codici”
CODICI_INTENT = re.compile(
    r"\b(codic[io]|\bcod\.|\blista\s+codici|\btutti\s+i\s+codici|\bcodici\s+prodotti?)\b",
    re.IGNORECASE
)

def _detect_family_in_text(t: str) -> str | None:
    t = t.lower()
    if   "ctf"      in t: return "ctf"
    if   "ctl"      in t: return "ctl"
    if   "ctcem"    in t: return "ctcem"
    if   "vcem"     in t: return "vcem"
    if   "diapason" in t: return "diapason"
    return None

# ------------------- Routes -------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/ping")
async def ping():
    return {"status": "ok", "service": "Tecnaria Bot - Web+Local", "model": OPENAI_MODEL}

@app.get("/health_critici")
async def health_critici():
    return {
        "dir": CRITICI_DIR,
        "files": CRITICI_FILES,
        "faq_loaded": sum(len(x["triggers"]) for x in CRITICI_QA),
        "hotfix_ctf": CTF_HOTFIX,
        "ctf_codes_inline": CTF_CODES_INLINE
    }

@app.get("/api/ask")
async def ask_get(request: Request, q: str = ""):
    return await ask(AskRequest(q=q))

@app.post("/api/ask")
async def ask(req: AskRequest):
    try:
        user_q = (req.text() or "").strip()
        if not user_q:
            return JSONResponse({"ok": False, "error": "Domanda mancante"}, status_code=400)

        # --- PRIORITÀ: dati sensibili/autoritatitivi solo da file locale ------
        sensitive_path = find_sensitive_file_for_question(user_q)
        if sensitive_path is not None:
            if sensitive_path == "":
                return {"ok": True, "answer": "- Informazione sensibile richiesta ma il file locale non è presente.\n\n**Fonti**\n- (file locale mancante)"}
            return {"ok": True, "answer": format_sensitive_answer_from_file(sensitive_path)}
        # ---------------------------------------------------------------------

        # --- AMBITO AZIENDALE: rispondi solo su temi Tecnaria -----------------
        if not SCOPE_KWS.search(user_q):
            msg = (
                "- Ambito del bot: prodotti/soluzioni Tecnaria (connettori CTF/CTL/Diapason, posa, P560, documentazione, contatti, pagamenti).\n"
                "- La tua domanda non rientra in questo ambito.\n"
                "- Esempi utili: «Come si fissano i CTF su lamiera TR60?» · «Qual è il nostro IBAN?» · «Mi dai i contatti aziendali?»\n\n"
                "**Fonti**\n- policy interna (ambito Tecnaria)"
            )
            return {"ok": True, "answer": msg}
        # ---------------------------------------------------------------------

        # --- FAST PATH 1: hotfix CTF (deterministico) -------------------------
        resp = ctf_hotfix_answer(user_q)
        if resp:
            return {"ok": True, "answer": resp}

        # --- FAST PATH 2: critici/faq deterministiche -------------------------
        resp = answer_from_critici(user_q)
        if resp:
            return {"ok": True, "answer": resp}
        # ---------------------------------------------------------------------

        # --- CODICI PRODOTTO dal WEB (tecnaria.com + PDF) --------------------
        ql = user_q.lower()
        if CODICI_INTENT.search(ql):
            fam = _detect_family_in_text(ql)

            # Caso A: famiglia specificata
            if fam:
                codes, srcs = await find_product_codes_from_web(user_q, fam)
                if codes:
                    lines  = [f"- **{c}**" for c in codes]
                    fontes = "\n".join(f"- {u}" for u in srcs) if srcs else "- tecnaria.com"
                    return {"ok": True, "answer": f"OK · Codici {fam.upper()}\n" + "\n".join(lines) + f"\n\n**Fonti**\n{fontes}"}
                else:
                    tried = "- " + "\n- ".join(srcs) if srcs else "- (nessuna pagina rilevante trovata su tecnaria.com)"
                    return {"ok": True, "answer": f"Non ho trovato codici {fam.upper()} su tecnaria.com.\n\n**Pagine controllate**\n{tried}\n\n**Fonti**\n- tecnaria.com"}

            # Caso B: nessuna famiglia specificata → tutte
            all_blocks: List[str] = []
            all_sources: List[str] = []
            found_any = False

            for famx in FAMILIES:
                codes, srcs = await find_product_codes_from_web(user_q, famx)
                if codes:
                    found_any = True
                    all_blocks.append(f"**{famx.upper()}**\n" + "\n".join(f"- **{c}**" for c in codes))
                    for s in srcs:
                        if s not in all_sources:
                            all_sources.append(s)

            if found_any:
                fontes = "\n".join(f"- {u}" for u in all_sources) if all_sources else "- tecnaria.com"
                return {"ok": True, "answer": "OK · Codici prodotti Tecnaria\n" + "\n\n".join(all_blocks) + f"\n\n**Fonti**\n{fontes}"}
            else:
                return {"ok": True, "answer": "Non ho trovato codici su tecnaria.com per le famiglie note (CTF, CTL, CTCEM, VCEM, DIAPASON).\n\n**Fonti**\n- tecnaria.com"}
        # ---------------------------------------------------------------------

        # ------------------- Flusso standard (web → locali → LLM) -------------
        web_hits: List[Dict] = []
        local_docs: Dict[str, str] = {}
        if FETCH_WEB_FIRST:
            web_hits = await web_search(user_q, topk=SEARCH_TOPK)
            local_docs = load_local_docs()
        else:
            local_docs = load_local_docs()
            if not local_docs:
                web_hits = await web_search(user_q, topk=SEARCH_TOPK)

        parts: List[str] = []
        if web_hits:
            parts.append("Fonti web:\n" + "\n".join(f"- {h['name']} {h['url']}" for h in web_hits))
        if local_docs:
            parts.append("Documenti locali:\n" + "\n".join(f"- {k}" for k in local_docs.keys()))
        context_blob = "\n\n".join(parts) if parts else "(nessun contesto disponibile)"

        user_prompt = (
            "Rispondi in 5–7 bullet tecnico-commerciali (vantaggi pratici, posa, sicurezza/certificazioni quando rilevanti). "
            "Chiudi con sezione **Fonti** (URL se web, altrimenti 'file locale'). "
            f"\n\nDomanda: {user_q}\n\nSorgenti disponibili:\n{context_blob}"
        )

        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2,
            max_tokens=900,
        )
        text = (resp.choices[0].message.content or "").strip()

        if _looks_like_p560_misclassified(text):
            if DEBUG:
                print("[GUARD] Correzione P560: no 'connettore'.")
            fix_prompt = (
                "Correggi subito: la P560 è una chiodatrice a sparo (non un connettore) per fissaggi su acciaio/lamiera. "
                "Rispondi in 5–7 bullet e chiudi con **Fonti** (URL o file locale). "
                f"\n\nDomanda: {user_q}"
            )
            r2 = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role":"system","content":SYSTEM_PROMPT},
                          {"role":"user","content":fix_prompt}],
                temperature=0.1,
                max_tokens=700,
            )
            text = (r2.choices[0].message.content or "").strip()

        text = _force_bullets_and_sources(text, web_hits, local_docs)
        return {"ok": True, "answer": text}

    except Exception as e:
        if DEBUG:
            print("[ERROR] /api/ask:", repr(e))
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
