import os
import httpx
import json
import re
from pathlib import Path
from typing import List, Dict, Tuple
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

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "brave")  # 'brave' o altro/vuoto per disattivare
FETCH_WEB_FIRST = os.getenv("FETCH_WEB_FIRST", "1") == "1"
SEARCH_TOPK = int(os.getenv("SEARCH_TOPK", "5"))
DEBUG = os.getenv("DEBUG", "0") == "1"

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

# ------------------- SENSITIVE/LOCAL DATA (solo file) -------------------
CRITICI_DIR = "static/static/data/critici"

SENSITIVE_FILES = {
    # Contatti
    "contatti": "contatti.json", "contatto": "contatti.json", "telefono": "contatti.json",
    "tel ": "contatti.json", "email": "contatti.json", "mail": "contatti.json",
    "pec": "contatti.json", "sdi": "contatti.json", "p.iva": "contatti.json",
    "partita iva": "contatti.json", "indirizzo": "contatti.json", "sede": "contatti.json",
    "dove siamo": "contatti.json",
    # Banca
    "iban": "bank.json", "bonifico": "bank.json", "conto": "bank.json", "coordinate bancarie": "bank.json",
    # Pagamenti
    "pagamento": "pagamenti.json", "pagamenti": "pagamenti.json", "metodi di pagamento": "pagamenti.json",
    # (nota: non forziamo codici CTF/CTL come locali se li vogliamo dal web)
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
                return str(fp)
            else:
                return ""
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
    piva = data.get("piva") or "Dato non disponibile"
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
    bic   = data.get("bic", "Dato non disponibile")
    note  = data.get("note", "-")
    return (
        f"- **Intestatario**: {intes}\n"
        f"- **Banca**: {banca}\n"
        f"- **IBAN**: {iban}\n"
        f"- **BIC**: {bic}\n"
        f"- **Note**: {note or '-'}\n\n"
        "**Fonti**\n- file locale · bank.json"
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
    if name == "bank.json":
        return format_from_bank(data)
    if name == "pagamenti.json":
        return format_from_pagamenti(data)
    pretty = json.dumps(data, indent=2, ensure_ascii=False)
    return f"- Dati:\n```\n{pretty[:1000]}\n```\n\n**Fonti**\n- file locale · {name}"

# ------------------- Helpers (web/local generali) -------------------
def expand_query_if_needed(query: str) -> str:
    q = query.lower()
    if "p560" in q and "spit" not in q:
        return query + " SPIT chiodatrice pistola sparachiodi Tecnaria HSBR14"
    return query

def _prefer_score(url: str) -> int:
    preferred = [d.strip().lower() for d in (os.getenv("PREFERRED_DOMAINS") or "tecnaria.com,spit.eu,spitpaslode.com").split(",") if d.strip()]
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
async def web_search(query: str, topk: int = 5) -> List[Dict]:
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

    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as ac:
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
async def _fetch_page_text(url: str, timeout: float = 15.0) -> str:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as ac:
            r = await ac.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                return ""
            return r.text or ""
    except Exception:
        return ""

_CODE_PATTERNS = {
    "ctf": re.compile(r"\bCTF\d{3}\b", re.IGNORECASE),
    "ctl": re.compile(r"\bCTL\d{3}\b", re.IGNORECASE),
}

async def find_product_codes_from_web(query: str, family: str, max_links: int = 8) -> Tuple[List[str], List[str]]:
    q = f"site:tecnaria.com {query}"
    hits = await web_search(q, topk=max_links) or []
    pattern = _CODE_PATTERNS.get(family.lower())
    if not pattern:
        return [], []
    codes = set()
    sources = []
    for h in hits[:max_links]:
        url = h.get("url") or ""
        if "tecnaria.com" not in url.lower():
            continue
        html = await _fetch_page_text(url)
        if not html:
            continue
        found = set(m.upper() for m in pattern.findall(html))
        if found:
            codes |= found
            sources.append(url)
    def _num(c):
        try:
            return int(re.findall(r"(\d{3})", c)[0])
        except Exception:
            return 0
    sorted_codes = sorted(codes, key=_num)
    return sorted_codes, sources[:5]

# ------------------- Local docs -------------------
def load_local_docs() -> Dict[str, str]:
    docs: Dict[str, str] = {}
    base = "static/docs"
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

# ------------------- Routes -------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/ping")
async def ping():
    return {"status": "ok", "service": "Tecnaria Bot - Web+Local", "model": OPENAI_MODEL}

@app.post("/api/ask")
async def ask(req: AskRequest):
    try:
        user_q = req.text().strip()
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

        # --- CODICI PRODOTTO da WEB (tecnaria.com) ---------------------------
        # se la domanda chiede "codici" e menziona ctf/ctl, forza estrazione dal sito tecnaria.com
        ql = user_q.lower()
        if ("codici" in ql or "codice" in ql) and ("ctf" in ql or "ctl" in ql):
            fam = "ctf" if "ctf" in ql else ("ctl" if "ctl" in ql else None)
            if fam:
                codes, srcs = await find_product_codes_from_web(user_q, fam)
                if codes:
                    lines = [f"- **{c}**" for c in codes]
                    fontes = "\n".join(f"- {u}" for u in srcs) if srcs else "- tecnaria.com"
                    answer = "OK\n" + "\n".join(lines) + f"\n\n**Fonti**\n{fontes}"
                    return {"ok": True, "answer": answer}
        # ---------------------------------------------------------------------

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
