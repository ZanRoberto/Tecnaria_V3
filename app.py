import os
import httpx
from typing import List, Dict
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

# ------------------- Input model (tollerante) -------------------
class AskRequest(BaseModel):
    # Accetta varie chiavi dal frontend per evitare 422
    question: str | None = None
    message:  str | None = None
    Domanda:  str | None = None
    q:        str | None = None

    def text(self) -> str:
        for k in (self.question, self.message, self.Domanda, self.q):
            if k:
                return k
        return ""

# ------------------- Helpers -------------------
def expand_query_if_needed(query: str) -> str:
    q = query.lower()
    if "p560" in q and "spit" not in q:
        return query + " SPIT chiodatrice pistola sparachiodi Tecnaria"
    return query

def _prefer_score(url: str) -> int:
    preferred = [d.strip().lower() for d in (os.getenv("PREFERRED_DOMAINS") or "").split(",") if d.strip()]
    return 1 if any(d in url.lower() for d in preferred) else 0

async def web_search(query: str, topk: int = 5) -> List[Dict]:
    """Ricerca web con Brave API (server-side)."""
    query = expand_query_if_needed(query)
    if os.getenv("SEARCH_PROVIDER", "brave") != "brave":
        return []
    key = os.getenv("BRAVE_API_KEY")
    if not key:
        if os.getenv("DEBUG") == "1":
            print("[BRAVE] manca BRAVE_API_KEY")
        return []

    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"X-Subscription-Token": key, "User-Agent": "Mozilla/5.0"}
    params = {"q": query, "count": topk}

    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as ac:
        r = await ac.get(url, headers=headers, params=params)
        if os.getenv("DEBUG") == "1":
            print("[BRAVE] status:", r.status_code)
            try:
                print("[BRAVE] body sample:", r.text[:300].replace("\n", " "))
            except Exception:
                pass
        if r.status_code != 200:
            return []

        data = r.json()
        results = []
        for item in (data.get("web", {}).get("results") or [])[:topk]:
            u = item.get("url")
            if u:
                results.append({"name": item.get("title", ""), "url": u})
        results.sort(key=lambda x: _prefer_score(x["url"]), reverse=True)
        return results

def load_local_docs() -> Dict[str, str]:
    docs: Dict[str, str] = {}
    base = "static/docs"
    if not os.path.isdir(base):
        return docs
    for fn in os.listdir(base):
        if fn.endswith(".txt"):
            try:
                with open(os.path.join(base, fn), "r", encoding="utf-8") as f:
                    docs[fn] = f.read()
            except Exception:
                continue
    return docs

# ------------------- System prompt -------------------
SYSTEM_PROMPT = """Parli come Tecnaria Bot per TECNARIA S.p.A. (Bassano del Grappa).
- Usa prima il WEB (ricerca aperta). Se ci sono più fonti, privilegia domini ufficiali/tecnici (es. tecnaria.com), senza escludere gli altri.
- Se il WEB non porta risultati utili, integra con documenti locali (static/docs). Se comunque non trovi, dillo onestamente.
- P560/“SPIT P560”: è una chiodatrice a sparo (pistola sparachiodi) per la posa dei connettori. NON è un connettore.
- Rispondi in bullet sintetici e chiudi SEMPRE con una sezione **Fonti** con URL (se web) o “file locale”.
- Non annunciare attese/ricerche: fornisci direttamente la risposta con le Fonti.
- Lingua: IT.
"""

# ------------------- Routes -------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/ping")
async def ping():
    return {"status": "ok", "service": "Tecnaria Bot - Web+Local", "model": os.getenv("OPENAI_MODEL", "gpt-4o")}

@app.post("/api/ask")
async def ask(req: AskRequest):
    try:
        user_q = req.text().strip()
        if not user_q:
            return JSONResponse({"ok": False, "error": "Domanda mancante"}, status_code=400)

        fetch_web_first = os.getenv("FETCH_WEB_FIRST", "1") == "1"
        topk = int(os.getenv("SEARCH_TOPK", "5"))

        # Costruzione contesto
        parts: List[str] = []
        web_hits = []
        if fetch_web_first:
            web_hits = await web_search(user_q, topk=topk)
            if web_hits:
                parts.append("Fonti web:\n" + "\n".join(f"- {h['name']} {h['url']}" for h in web_hits))
        local_docs = load_local_docs()
        if local_docs:
            parts.append("Documenti locali:\n" + "\n".join(f"- {k}" for k in local_docs.keys()))

        context_blob = "\n\n".join(parts) if parts else "(nessun contesto disponibile)"

        # Messaggi per OpenAI (chat.completions: compatibilità massima)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Domanda: {user_q}\n\nContesto disponibile:\n{context_blob}"}
        ]

        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            messages=messages,
            temperature=0.2,
        )
        text = resp.choices[0].message.content.strip()

        # Post-format: assicurati sezione Fonti
        if "**Fonti**" not in text and "Fonti" not in text:
            sources = [h["url"] for h in web_hits] if web_hits else list(local_docs.keys())
            if sources:
                text += "\n\n**Fonti**\n" + "\n".join(f"- {s}" for s in sources)
            else:
                text += "\n\n**Fonti**\n- (nessuna fonte trovata)"

        return {"ok": True, "answer": text}

    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
