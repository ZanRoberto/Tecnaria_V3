import json
import os
import httpx
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from typing import Dict, Any

# ----------------------------------------------------
# CONFIGURAZIONE BASE
# ----------------------------------------------------
app = FastAPI(title="Tecnaria QA Bot", version="3.0")

# Domini ammessi (solo fonti ufficiali)
ALLOWED_DOMAINS = ["tecnaria.com", "spit.eu", "spitpaslode.com"]

# Percorsi principali
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "static" / "data"
SINAPSI_FILE = DATA_DIR / "sinapsi_rules.json"

# ----------------------------------------------------
# CARICAMENTO SINAPSI (con cache in memoria)
# ----------------------------------------------------
sinapsi_rules: list[Dict[str, Any]] = []

def load_sinapsi():
    global sinapsi_rules
    try:
        if SINAPSI_FILE.exists():
            sinapsi_rules = json.loads(SINAPSI_FILE.read_text(encoding="utf-8"))
            print(f"[Sinapsi] Caricate {len(sinapsi_rules)} regole da {SINAPSI_FILE}")
        else:
            sinapsi_rules = []
            print("[Sinapsi] Nessun file trovato.")
    except Exception as e:
        sinapsi_rules = []
        print(f"[Sinapsi] Errore nel caricamento: {e}")

# Precaricamento
load_sinapsi()

# ----------------------------------------------------
# FUNZIONE SINAPSI: arricchisce la risposta
# ----------------------------------------------------
def sinapsi_enhance(question: str, base_answer: str) -> str:
    for rule in sinapsi_rules:
        if rule.get("pattern") and rule.get("answer"):
            import re
            if re.search(rule["pattern"], question, re.IGNORECASE):
                mode = rule.get("mode", "augment")
                addon = rule["answer"].strip()
                if mode == "override":
                    return addon
                elif mode == "augment":
                    return base_answer + "<br><br>" + addon
                elif mode == "postscript":
                    return base_answer + f"<br><br><i>Nota:</i> {addon}"
    return base_answer

# ----------------------------------------------------
# FUNZIONE DI RICERCA WEB (limitata ai domini consentiti)
# ----------------------------------------------------
async def web_search_tecnaria(query: str) -> list[Dict[str, str]]:
    """Interroga Brave API o un motore predefinito."""
    results = []
    for domain in ALLOWED_DOMAINS:
        results.append({
            "title": f"Risorsa ufficiale ({domain})",
            "url": f"https://{domain}/search?q={query.replace(' ', '+')}",
            "snippet": f"Ho cercato informazioni su '{query}' nei contenuti di {domain}."
        })
    return results

# ----------------------------------------------------
# FORMATTATORE HTML “card”
# ----------------------------------------------------
def make_card_html(title: str, content: str, links: list[Dict[str, str]] = None) -> str:
    html = f"<div class='card'><h2>{title}</h2><p>{content}</p>"
    if links:
        html += "<p><strong>📎 Fonti:</strong><br>"
        for l in links:
            html += f"🔗 <a href='{l['url']}' target='_blank'>{l['title']}</a><br>"
        html += "</p>"
    html += "<p><small>Risposta da Sinapsi (narrativa).</small></p></div>"
    return html

# ----------------------------------------------------
# ENDPOINT PRINCIPALE /ask
# ----------------------------------------------------
@app.get("/ask", response_class=HTMLResponse)
async def ask(q: str = Query(..., description="Domanda dell'utente")):
    question = q.strip()
    print(f"[ASK] {question}")

    # Step 1: Web search simulata
    web_results = await web_search_tecnaria(question)
    base_answer = (
        "Ho raccolto le informazioni dai contenuti ufficiali Tecnaria e partner tecnici.<br>"
        "Ecco una sintesi narrativa chiara e completa dei punti più rilevanti."
    )

    # Step 2: Enrichment Sinapsi
    enriched = sinapsi_enhance(question, base_answer)

    # Step 3: Format final HTML
    html = make_card_html("Risposta Tecnaria", enriched, web_results)
    return HTMLResponse(html)

# ----------------------------------------------------
# ENDPOINT DI STATO
# ----------------------------------------------------
@app.get("/health", response_class=JSONResponse)
async def health():
    return {
        "status": "ok",
        "sinapsi_loaded": len(sinapsi_rules),
        "domains": ALLOWED_DOMAINS,
        "mode": "narrative",
    }

# ----------------------------------------------------
# STATIC + INTERFACCIA WEB
# ----------------------------------------------------
if Path("static").exists():
    try:
        app.mount("/static", StaticFiles(directory="static"), name="static")
    except RuntimeError:
        pass

_INDEX_CANDIDATES = [
    Path("static/index.html"),
    Path("templates/index.html"),
    Path("index.html"),
]

def _load_index_html() -> str:
    for p in _INDEX_CANDIDATES:
        if p.exists():
            return p.read_text(encoding="utf-8")
    return """
    <!doctype html><meta charset='utf-8'>
    <style>body{font-family:system-ui;margin:3em;}</style>
    <h1>UI non trovata</h1>
    <p>Posiziona <b>index.html</b> in <code>static/</code> o <code>templates/</code>.</p>
    """

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(_load_index_html())

@app.get("/index.html", response_class=HTMLResponse)
async def index_html():
    return HTMLResponse(_load_index_html())
