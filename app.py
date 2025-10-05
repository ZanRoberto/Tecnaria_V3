# app.py — Tecnaria QA Bot (Render Versione Ottobre 2025)
# Autore: GPT-5 per Roberto Zannoni

import os, json, re, asyncio
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from bs4 import BeautifulSoup

app = FastAPI(title="Tecnaria QA Bot", version="3.5-narrative")

# -------------------------------
# 📁 Percorsi e impostazioni base
# -------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "static" / "data"
SINAPSI_PATH = DATA_DIR / "sinapsi_rules.json"
ALLOWED_DOMAINS = ["tecnaria.com", "spit.eu", "spitpaslode.com"]

# -------------------------------
# 🌐 CORS (per interfaccia web)
# -------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------
# ⚙️ Caricamento Sinapsi
# -------------------------------
sinapsi_rules = []

def load_sinapsi():
    global sinapsi_rules
    if SINAPSI_PATH.exists():
        with open(SINAPSI_PATH, "r", encoding="utf-8") as f:
            sinapsi_rules = json.load(f)
        print(f"✅ Sinapsi caricata: {len(sinapsi_rules)} regole.")
    else:
        print("⚠️ Nessun file sinapsi trovato.")

@app.on_event("startup")
async def startup_event():
    load_sinapsi()
    print("🔄 Prewarm Sinapsi completato.")

# -------------------------------
# 🧠 Funzioni di supporto
# -------------------------------
def apply_sinapsi(query: str, text: str) -> str:
    """Applica regole Sinapsi alla risposta HTML."""
    for rule in sinapsi_rules:
        if re.search(rule["pattern"], query, re.IGNORECASE):
            mode = rule.get("mode", "augment")
            answer = rule.get("answer", "").strip()
            if mode == "override":
                return f"<p>{answer}</p>"
            elif mode == "augment":
                return text + f"<hr><p>{answer}</p>"
            elif mode == "postscript":
                return text + f"<br><em>{answer}</em>"
    return text

async def web_search_tecnaria(query: str) -> str:
    """Simula ricerca web su domini consentiti."""
    async with httpx.AsyncClient(timeout=15) as client:
        results = []
        for domain in ALLOWED_DOMAINS:
            try:
                r = await client.get(f"https://api.allorigins.win/get?url=https://{domain}/search?q={query}")
                if r.status_code == 200:
                    data = r.json().get("contents", "")
                    soup = BeautifulSoup(data, "html.parser")
                    for a in soup.find_all("a", href=True):
                        href = a["href"]
                        if domain in href:
                            title = a.text.strip() or domain
                            results.append(f"📎 <a href='{href}' target='_blank'>{title}</a>")
            except Exception:
                continue

        if not results:
            return "<p>Nessun risultato trovato nei domini ufficiali Tecnaria.</p>"

        return "<br>".join(results)

# -------------------------------
# 🌍 API: health check
# -------------------------------
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "sinapsi_loaded": len(sinapsi_rules),
        "allowed_domains": ALLOWED_DOMAINS,
        "mode": "narrative+prewarm",
    }

# -------------------------------
# 🌐 API: risposta /ask
# -------------------------------
@app.get("/ask", response_class=HTMLResponse)
async def ask(q: str):
    q = q.strip()
    if not q:
        return HTMLResponse("<p>❓ Inserisci una domanda.</p>")

    # Cerca nei domini ufficiali
    html = await web_search_tecnaria(q)

    # Applica Sinapsi
    enriched = apply_sinapsi(q, html)
    return HTMLResponse(enriched)

# -------------------------------
# 🌐 Homepage statica
# -------------------------------
app.mount("/", StaticFiles(directory="static", html=True), name="static")

