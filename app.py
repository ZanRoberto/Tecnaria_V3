# app.py — versione stabile con fix urlencode + pulizia risposta HTML
import re
import json
import html
import os
from urllib.parse import quote_plus as _quote_plus
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import httpx

app = FastAPI()

# === Funzione urlencode (sostituisce mdurl.urlencode) ===
def urlencode(s: str) -> str:
    return _quote_plus(s, safe="")

# === Setup cartelle statiche ===
static_dir = os.path.join(os.getcwd(), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# === Carica file sinapsi_rules.json ===
SINAPSI_PATH = os.path.join(static_dir, "data", "sinapsi_rules.json")
sinapsi_rules = []
if os.path.exists(SINAPSI_PATH):
    try:
        with open(SINAPSI_PATH, "r", encoding="utf-8") as f:
            sinapsi_rules = json.load(f)
        print(f"✅ Sinapsi rules loaded: {len(sinapsi_rules)}")
    except Exception as e:
        print("⚠️ Errore caricando sinapsi_rules:", e)

# === Endpoint base ===
@app.get("/", response_class=HTMLResponse)
async def home():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse("<h1>Tecnaria QA Bot</h1><p>Use /ask?q=...</p>")

@app.get("/ping")
async def ping():
    return {"ok": True, "pong": True}

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "web_search": {
            "provider": "brave",
            "brave_key": bool(os.getenv("BRAVE_API_KEY")),
            "bing_key": bool(os.getenv("BING_API_KEY")),
            "preferred_domains": ["tecnaria.com", "spit.eu", "spitpaslode.com"],
            "min_web_score": 0.35,
        },
        "critici": {
            "dir": static_dir,
            "exists": os.path.isdir(static_dir),
            "sinapsi_file": SINAPSI_PATH,
            "sinapsi_loaded": len(sinapsi_rules),
        },
    }

# === Funzione per pulizia del testo ===
def clean_text(t: str) -> str:
    if not t:
        return ""
    t = re.sub(r"%PDF-[\s\S]+?(?=\n|$)", "", t)       # rimuove header PDF
    t = re.sub(r"\n{2,}", "\n", t)                    # rimuove doppi \n
    t = re.sub(r"(?<!\n)- ", "• ", t)                 # bullet estetico
    t = html.escape(t)
    t = t.replace("\n", "<br>")
    return t

# === Match di sinapsi ===
def find_sinapsi_answer(question: str):
    q = question.lower()
    for rule in sinapsi_rules:
        if re.search(rule.get("pattern", ""), q, flags=re.IGNORECASE):
            return rule
    return None

# === Core handler ===
async def query_brave_search(q: str):
    """
    Esegue ricerca web (mockata o reale via Brave se BRAVE_API_KEY presente)
    """
    try:
        api_key = os.getenv("BRAVE_API_KEY")
        if not api_key:
            return None
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": q, "count": 3},
                headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            )
            data = resp.json()
            if "web" in data and "results" in data["web"]:
                results = data["web"]["results"]
                joined = []
                for r in results:
                    title = r.get("title", "")
                    url = r.get("url", "")
                    snippet = r.get("description", "")
                    joined.append(f"📎 <a href='{url}' target='_blank'>{title}</a><br>{snippet}")
                return "<br>".join(joined)
    except Exception as e:
        print("❌ Brave search error:", e)
    return None

# === Endpoint principale ===
@app.get("/ask", response_class=HTMLResponse)
async def ask(q: str = ""):
    if not q:
        return HTMLResponse("<p>Manca il parametro ?q= nella richiesta.</p>")
    q = q.strip()

    # 1️⃣ Controlla se c'è una regola Sinapsi
    sinapsi = find_sinapsi_answer(q)
    sinapsi_text = ""
    mode = "augment"
    if sinapsi:
        sinapsi_text = sinapsi.get("answer", "")
        mode = sinapsi.get("mode", "augment")

    # 2️⃣ Ricerca web
    web_content = await query_brave_search(q)
    if not web_content:
        web_content = "🌐 <i>Nessuna risposta diretta trovata sul web.</i>"

    # 3️⃣ Fusione logica
    if mode == "override":
        final_answer = sinapsi_text
    elif mode == "augment":
        final_answer = f"{web_content}<br><br>{sinapsi_text}"
    elif mode == "postscript":
        final_answer = f"{web_content}<br><br><i>{sinapsi_text}</i>"
    else:
        final_answer = web_content

    # 4️⃣ Pulizia
    final_answer = clean_text(final_answer)

    # 5️⃣ Formattazione HTML
    html_response = f"""
    <html>
    <head>
      <meta charset='utf-8'>
      <title>Tecnaria Bot</title>
      <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; color:#222; background:#fafafa; padding:20px; }}
        h1 {{ color:#005a9c; }}
        a {{ color:#0056b3; text-decoration:none; }}
        a:hover {{ text-decoration:underline; }}
        .answer {{ background:white; padding:20px; border-radius:12px; box-shadow:0 0 10px rgba(0,0,0,0.08); }}
        br {{ line-height:1.6; }}
      </style>
    </head>
    <body>
      <h1>Risposta Tecnaria Bot</h1>
      <div class='answer'>{final_answer}</div>
      <hr>
      <small style="color:gray;">Domanda: {html.escape(q)}</small>
    </body>
    </html>
    """
    return HTMLResponse(html_response)

# === Endpoint POST ===
@app.post("/api/ask")
async def ask_post(request: Request):
    data = await request.json()
    q = data.get("q", "")
    return JSONResponse({"ok": True, "answer": (await ask(q)).body.decode("utf-8")})
