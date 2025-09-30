import os, glob, re
from typing import List, Tuple
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from openai import OpenAI

# ================== APP ==================
app = FastAPI(title="Tecnaria Bot - Web+Local")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # restringi al tuo dominio se vuoi
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

# ================== ENV ==================
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata.")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.1-mini")

SEARCH_API_ENDPOINT = os.environ.get("SEARCH_API_ENDPOINT")  # es. Bing Web Search
SEARCH_API_KEY = os.environ.get("SEARCH_API_KEY")
SEARCH_TOPK = int(os.environ.get("SEARCH_TOPK", "5"))
FETCH_WEB_FIRST = os.environ.get("FETCH_WEB_FIRST", "1") == "1"
PREFERRED_DOMAINS = [d.strip() for d in os.getenv("PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com").split(",") if d.strip()]
DOC_GLOB = os.environ.get("DOC_GLOB", "static/docs/*.txt")
DEBUG = os.environ.get("DEBUG", "0") == "1"

client = OpenAI(api_key=OPENAI_API_KEY)

# ================== PROMPT ==================
SYSTEM_PROMPT = """
Parli come Tecnaria Bot per TECNARIA S.p.A. (Bassano del Grappa).

TASSONOMIA OBBLIGATORIA (NON VIOLARE)
- CONNETTORI Tecnaria: CTF (acciaio–calcestruzzo su lamiera grecata), CTL (legno–calcestruzzo), CTCEM/VCEM, Diapason, ecc.
- ATTREZZATURE/ATTREZZAGGI: SPIT P560 = chiodatrice a sparo (pistola sparachiodi) per la posa dei connettori. NON è un connettore.
  • Consumabili tipici: chiodi idonei (es. HSBR14) e propulsori a cartuccia adeguati.

REGOLE
- Se l’oggetto è “P560”/“SPIT P560”: trattala come chiodatrice/attrezzatura e dillo esplicitamente.
- Usa prima il WEB (ricerca aperta); se ci sono più fonti dai priorità a pagine ufficiali/tecniche (es. tecnaria.com) senza escludere le altre.
- Se il WEB è vuoto, integra con documenti locali; se comunque non trovi, dillo onestamente.
- Non inventare. Risposte a bullet; chiudi SEMPRE con **Fonti** (URL o “file locale”).
- Lingua: IT.
""".strip()

# ================== I/O ==================
class ChatIn(BaseModel):
    message: str

# ================== HELPERS ==================
def _prefer_score(url: str) -> int:
    return 1 if any(p in url for p in PREFERRED_DOMAINS) else 0

def _sanitize(html: str, max_chars: int = 12000) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]

def expand_query_if_needed(q: str) -> str:
    """Aiuta la ricerca quando compare P560."""
    if "p560" in q.lower():
        extra = " SPIT P560 chiodatrice pistola sparachiodi Tecnaria connettori HSBR14 propulsori"
        if extra.lower() not in q.lower():
            return q + extra
    return q

async def web_search(query: str, topk: int = 5) -> list[dict]:
    """Ricerca sul web tramite provider esterno (Bing Web Search API / SerpAPI / ecc.)."""
    query = expand_query_if_needed(query)
    if not SEARCH_API_ENDPOINT or not SEARCH_API_KEY:
        return []
    headers = {"Ocp-Apim-Subscription-Key": SEARCH_API_KEY}
    params = {"q": query, "count": topk, "textDecorations": False, "textFormat": "Raw"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as ac:
        r = await ac.get(SEARCH_API_ENDPOINT, headers=headers, params=params)
        if r.status_code != 200:
            return []
        data = r.json()
        items = []
        for w in (data.get("webPages", {}) or {}).get("value", []):
            items.append({"name": w.get("name"), "url": w.get("url")})
        items.sort(key=lambda x: _prefer_score(x["url"]), reverse=True)
        return items[:topk]

async def gather_web_context_generic(user_query: str) -> List[Tuple[str, str]]:
    results = await web_search(user_query, topk=SEARCH_TOPK)
    ctx: List[Tuple[str, str]] = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as ac:
        for it in results:
            try:
                resp = await ac.get(it["url"], headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200 and resp.text:
                    ctx.append((it["url"], _sanitize(resp.text)))
            except Exception:
                continue
    return ctx

def load_local_docs() -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for path in glob.glob(DOC_GLOB):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                out.append((f"(file locale) {os.path.basename(path)}", f.read()[:8000]))
        except Exception:
            continue
    return out

def build_input_blocks(system_prompt: str, user_query: str, web_ctx: List[Tuple[str,str]], local_ctx: List[Tuple[str,str]]):
    chunks = []
    if web_ctx:
        for url, txt in web_ctx:
            chunks.append(f"[WEB:{url}]\n{txt}")
    if local_ctx:
        for name, txt in local_ctx:
            chunks.append(f"[LOCAL:{name}]\n{txt}")
    context_blob = "\n\n---\n\n".join(chunks) if chunks else "(nessun contesto disponibile)"
    system = {"role": "system", "content":[{"type":"input_text","text": system_prompt}]}
    user = {"role": "user", "content":[
        {"type":"input_text","text": f"Domanda utente: {user_query}"},
        {"type":"input_text","text": f"Contesto disponibile:\n{context_blob}"},
    ]}
    return [system, user]

def post_format(answer: str, web_ctx: List[Tuple[str,str]], local_ctx: List[Tuple[str,str]]) -> str:
    if "Fonti" not in answer and "Fonti:" not in answer:
        srcs = [u for u,_ in web_ctx] or [n for n,_ in local_ctx]  # preferisci URL web se presenti
        answer += "\n\n**Fonti**\n" + ("\n".join(f"- {s}" for s in srcs) if srcs else "- (nessuna fonte trovata)")
    return answer

def _looks_like_p560_misclassified(text: str) -> bool:
    t = text.lower()
    return ("p560" in t or "spit p560" in t) and ("connettor" in t) and not any(w in t for w in ["chiodatrice","pistola","sparachiodi"])

def make_correction_prompt(user_q: str, bad_answer: str) -> list:
    sys = {"role":"system","content":[{"type":"input_text","text": SYSTEM_PROMPT}]}
    user = {"role":"user","content":[{"type":"input_text","text": f"""
Correggi la seguente risposta errata: hai classificato la P560 come connettore ma è una chiodatrice a sparo (pistola sparachiodi) per la posa dei connettori.
Riscrivi in 5–7 bullet (uso con connettori Tecnaria, consumabili/propulsori, note di posa/sicurezza, manutenzione) e chiudi con **Fonti** con URL.
Domanda originale: {user_q}

Risposta da correggere:
{bad_answer}
""".strip()}]}
    return [sys, user]

# ================== ROUTES ==================
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/ping")
def ping():
    return {"pong": True, "model": OPENAI_MODEL}

@app.post("/api/ask")
async def ask(inp: ChatIn):
    q = inp.message.strip()
    try:
        web_ctx: List[Tuple[str,str]] = []
        local_ctx: List[Tuple[str,str]] = []

        if FETCH_WEB_FIRST:
            web_ctx = await gather_web_context_generic(q)
            if not web_ctx:
                local_ctx = load_local_docs()
        else:
            local_ctx = load_local_docs()
            if not local_ctx:
                web_ctx = await gather_web_context_generic(q)

        # Messaggi per Responses API (1.x)
        if not web_ctx and not local_ctx:
            msgs = [
                {"role":"system","content":[{"type":"input_text","text": SYSTEM_PROMPT}]},
                {"role":"user","content":[{"type":"input_text","text": q}]}
            ]
        else:
            msgs = build_input_blocks(SYSTEM_PROMPT, q, web_ctx, local_ctx)

        text = None

        # ---- Tentativo 1: Responses API (openai>=1.0)
        try:
            resp = client.responses.create(model=OPENAI_MODEL, input=msgs, temperature=0.2)
            text = getattr(resp, "output_text", None)
            if not text:
                try:
                    out = resp.output or []
                    if out and "content" in out[0] and out[0]["content"]:
                        text = out[0]["content"][0].get("text")
                except Exception:
                    pass
        except Exception as e:
            if DEBUG:
                print("[DEBUG] Responses API non disponibile, provo chat.completions:", repr(e))

        # ---- Tentativo 2: Fallback Chat Completions (SDK vecchio)
        if not text:
            messages = [{"role":"system","content":SYSTEM_PROMPT}]
            user_txt = q
            if web_ctx or local_ctx:
                ctx_lines = [*(f"[WEB:{u}]" for u,_ in web_ctx), *(f"[LOCAL:{n}]" for n,_ in local_ctx)]
                if ctx_lines:
                    user_txt = f"{q}\n\nContesto (sorgenti):\n" + "\n".join(ctx_lines)
            messages.append({"role":"user","content":user_txt})
            try:
                resp2 = client.chat.completions.create(model=OPENAI_MODEL, messages=messages, temperature=0.2)
                text = resp2.choices[0].message.content.strip()
            except Exception as e2:
                try:
                    resp2 = client.ChatCompletion.create(model=OPENAI_MODEL, messages=messages, temperature=0.2)  # type: ignore
                    text = resp2["choices"][0]["message"]["content"].strip()
                except Exception as e3:
                    raise RuntimeError(f"OpenAI call failed (responses+chat): {e2} / {e3}")

        if not text:
            raise RuntimeError("Risposta vuota dal modello.")

        text = post_format(text.strip(), web_ctx, local_ctx)

        # ---- Guardrail: P560 non deve essere mai “connettore”
        if _looks_like_p560_misclassified(text):
            if DEBUG:
                print("[GUARD] P560 misclassificata. Rigenero risposta corretta.")
            corr_msgs = make_correction_prompt(q, text)
            try:
                corr = client.responses.create(model=OPENAI_MODEL, input=corr_msgs, temperature=0.1)
                fixed = getattr(corr, "output_text", "") or ""
            except Exception:
                # fallback vecchio SDK
                messages = [{"role":"system","content":SYSTEM_PROMPT},
                            {"role":"user","content":"Correggi: P560 è chiodatrice a sparo, non connettore. 5–7 bullet + Fonti con URL."}]
                resp2 = client.chat.completions.create(model=OPENAI_MODEL, messages=messages, temperature=0.1)
                fixed = resp2.choices[0].message.content.strip()
            if fixed:
                text = post_format(fixed.strip(), web_ctx, local_ctx)

        if DEBUG:
            print("[DEBUG] query:", q)
            print("[DEBUG] web_ctx URLs:", [u for u,_ in web_ctx])
            print("[DEBUG] local_ctx files:", [n for n,_ in local_ctx])

        return {"ok": True, "answer": text}

    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
