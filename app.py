import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from openai import OpenAI

# ------------------- Config -------------------
app = FastAPI(title="Tecnaria Bot - Web+Local")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# templates per interfaccia web
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ------------------- Modelli -------------------
class AskRequest(BaseModel):
    question: str

# ------------------- Utils -------------------
def expand_query_if_needed(query: str) -> str:
    """
    Espansione query per parole chiave note (es. P560).
    """
    q = query.lower()
    if "p560" in q and "spit" not in q:
        return query + " SPIT chiodatrice pistola sparachiodi Tecnaria"
    return query

def _prefer_score(url: str) -> int:
    """
    Boost soft per domini preferiti.
    """
    preferred = (os.getenv("PREFERRED_DOMAINS") or "").split(",")
    return sum([1 for d in preferred if d.strip() and d.strip().lower() in url.lower()])

# ------------------- Web search (Brave) -------------------
async def web_search(query: str, topk: int = 5) -> list[dict]:
    query = expand_query_if_needed(query)
    if os.getenv("SEARCH_PROVIDER", "brave") != "brave":
        return []
    key = os.getenv("BRAVE_API_KEY")
    if not key:
        if os.getenv("DEBUG") == "1":
            print("[BRAVE] manca BRAVE_API_KEY")
        return []

    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "X-Subscription-Token": key,
        "User-Agent": "Mozilla/5.0"
    }
    params = {"q": query, "count": topk}

    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as ac:
        r = await ac.get(url, headers=headers, params=params)

        if os.getenv("DEBUG") == "1":
            print("[BRAVE] status:", r.status_code)
            try:
                sample = r.text[:300].replace("\n", " ")
                print("[BRAVE] body sample:", sample)
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

# ------------------- Local docs -------------------
def load_local_docs() -> dict:
    docs = {}
    base = "static/docs"
    if not os.path.isdir(base):
        return docs
    for fn in os.listdir(base):
        if fn.endswith(".txt"):
            with open(os.path.join(base, fn), "r", encoding="utf-8") as f:
                docs[fn] = f.read()
    return docs

# ------------------- Routes -------------------
@app.get("/ping")
async def ping():
    return {"status": "ok", "service": "Tecnaria Bot - Web+Local", "model": "gpt-4o"}

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/ask")
async def ask(req: AskRequest):
    q = req.question.strip()
    fetch_web_first = os.getenv("FETCH_WEB_FIRST", "1") == "1"

    context_parts = []

    if fetch_web_first:
        hits = await web_search(q)
        if hits:
            context_parts.append("Fonti web:\n" + "\n".join([f"- {h['name']} {h['url']}" for h in hits]))

    docs = load_local_docs()
    if docs:
        context_parts.append("Documenti locali:\n" + "\n".join([f"[{k}]" for k in docs.keys()]))

    # costruzione prompt
    SYSTEM_PROMPT = """Sei un assistente tecnico di Tecnaria S.p.A. (Bassano del Grappa).
Rispondi con precisione, in punti elenco sintetici, usando sia fonti web che documenti locali.
Non inventare, non dire 'potrebbe essere qualsiasi cosa'.
Chiudi SEMPRE con una sezione 'Fonti' con URL se presenti.
"""
    prompt = SYSTEM_PROMPT + "\n\nContesto disponibile:\n" + "\n\n".join(context_parts) + f"\n\nDomanda:\n{q}"

    try:
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": prompt}],
            temperature=0.2,
        )
        answer = resp.choices[0].message.content
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    return {"answer": answer}
