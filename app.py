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

# Middleware CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # se vuoi puoi restringere al tuo dominio
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Templates
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
PREFERRED_DOMAINS = [d.strip() for d in os.getenv("PREFERRED_DOMAINS", "tecnaria.com").split(",") if d.strip()]
DOC_GLOB = os.environ.get("DOC_GLOB", "static/docs/*.txt")
DEBUG = os.environ.get("DEBUG", "0") == "1"

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
Sei il Tecnaria Bot.
- Usa prima i contenuti dal WEB (ricerca aperta). Se ci sono più fonti, privilegia quelle ufficiali/tecniche, senza escludere le altre.
- Se il WEB è vuoto, integra con i documenti locali (static/docs).
- Se non trovi nulla, dillo chiaramente senza inventare.
- Rispondi con bullet chiari; chiudi con sezione **Fonti** (URL o “file locale”).
- Lingua: IT.
""".strip()

class ChatIn(BaseModel):
    message: str

# ================== HELPERS ==================
def _prefer_score(url: str) -> int:
    return 1 if any(p in url for p in PREFERRED_DOMAINS) else 0

async def web_search(query: str, topk: int = 5) -> list[dict]:
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

def _sanitize(html: str, max_chars: int = 12000) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]

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
        srcs = [u for u,_ in web_ctx] + [n for n,_ in local_ctx]
        answer += "\n\n**Fonti**\n" + ("\n".join(f"- {s}" for s in srcs) if srcs else "- (nessuna fonte trovata)")
    return answer

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

        if not web_ctx and not local_ctx:
            msgs = [
                {"role":"system","content":[{"type":"input_text","text": SYSTEM_PROMPT}]},
                {"role":"user","content":[{"type":"input_text","text": q}]}
            ]
        else:
            msgs = build_input_blocks(SYSTEM_PROMPT, q, web_ctx, local_ctx)

        resp = client.responses.create(
            model=OPENAI_MODEL,
            input=msgs,
            temperature=0.2,
        )
        text = getattr(resp, "output_text", "").strip()
        if not text:
            raise RuntimeError("Risposta vuota dal modello.")

        text = post_format(text, web_ctx, local_ctx)

        if DEBUG:
            print("[DEBUG] query:", q)
            print("[DEBUG] web_ctx URLs:", [u for u,_ in web_ctx])
            print("[DEBUG] local_ctx files:", [n for n,_ in local_ctx])

        return {"ok": True, "answer": text}

    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
