import os, re, html, time, textwrap, io, json
from typing import List, Dict, Tuple
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from openai import OpenAI

# ─────────────── ENV / MODELLI ───────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata.")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
if OPENAI_MODEL.startswith("gpt-5"):
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL_COMPAT", "gpt-4o")

# WEB → LOCALE (web first)
WEB_MAX_RESULTS   = int(os.environ.get("WEB_MAX_RESULTS", "8"))
WEB_MAX_PAGES     = int(os.environ.get("WEB_MAX_PAGES", "4"))
WEB_FETCH_TIMEOUT = float(os.environ.get("WEB_FETCH_TIMEOUT", "10"))
SAFE_DOMAINS = [d.strip().lower() for d in os.environ.get(
    "WEB_SAFE_DOMAINS",
    "tecnaria.com,www.tecnaria.com,spitpaslode.it,spit.eu,eta.europa.eu,cstb.fr"
).split(",") if d.strip()]

# provider (ne basta uno)
TAVILY_API_KEY  = os.environ.get("TAVILY_API_KEY", "")
SERPAPI_API_KEY = os.environ.get("SERPAPI_API_KEY", "")
BRAVE_API_KEY   = os.environ.get("BRAVE_API_KEY", "")

# Knowledge base interna (SOLO per nota integrativa, non nel RAG)
KB_PATH = os.environ.get("KB_PATH", "KB_FAQ.md")
KB_MIN_OVERLAP = int(os.environ.get("KB_MIN_OVERLAP", "1"))
KB_TOPK = int(os.environ.get("KB_TOPK", "2"))

# Memoria tecnica (Sinapsi) — opzionale, file JSON locale (best effort)
SINAPSI_PATH = os.environ.get("SINAPSI_PATH", "sinapsi_brain.json")

client = OpenAI(api_key=OPENAI_API_KEY)

# ─────────────── PROMPT LOCALE (immutato, niente dati “magici”) ───────────────
PROMPT = """
Agisci come TECNICO-COMMERCIALE SENIOR di TECNARIA S.p.A. (Bassano del Grappa).
Obiettivo: risposte corrette, sintetiche e utili alla decisione d’acquisto/posa. ZERO invenzioni.

Ambito: connettori CTF (lamiera grecata), CTL (legno-calcestruzzo), CTCEM/VCEM (acciaio-calcestruzzo),
accessori/posa (SPIT P560, chiodi/propulsori, kit/adattatori), utilizzi, compatibilità, vantaggi/limiti,
note su certificazioni/ETA e documentazione.

Regole:
1) Domanda semplice/commerciale → risposta BREVE (2–5 righe).
2) Domanda tecnica → risposta DETTAGLIATA ma concisa; punti elenco solo se utili.
3) Domanda ambigua → risposta STANDARD e proponi documento/contatto tecnico.
4) Mai inventare codici, PRd, ETA o combinazioni di lamiera: “Dato non disponibile in questa sede; fornibile su scheda/ETA su richiesta”.
5) P560: fissaggi su acciaio/lamiera (CTF, travi metalliche); per legno puro (CTL) si usano viti/bulloni, non la P560.
Tono: tecnico, professionale, concreto. Italiano.
""".strip()

# ─────────────── FASTAPI ───────────────
app = FastAPI(title="Tecnaria Bot — WEB → LOCALE + Note interne")

class AskPayload(BaseModel):
    question: str

# ─────────────── UI ───────────────
@app.get("/", response_class=HTMLResponse)
def ui():
    html_page = """
<!doctype html><meta charset="utf-8"><title>Tecnaria Bot</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--g:#1aa35b;--bg:#0b0f19;--card:#0f1527;--mut:#9fb3c8}
body{margin:0;background:var(--bg);color:#e6e6e6;font-family:system-ui,Segoe UI,Roboto,Arial}
.wrap{max-width:1080px;margin:24px auto;padding:0 16px}
.header{display:flex;align-items:center;gap:12px}
.badge{background:#0e1c2f;border:1px solid #27405c;border-radius:999px;padding:6px 10px;font-size:12px;color:#cfe1ff}
.panel{display:grid;grid-template-columns:320px 1fr;gap:20px;margin-top:14px}
.left{background:var(--card);border:1px solid #273047;border-radius:16px;padding:14px}
.right{background:#111833;border:1px solid #273047;border-radius:16px;padding:14px;min-height:180px}
h1{margin:.2rem 0 0;font-size:22px}
.label{font-size:12px;color:var(--mut);margin:10px 0 6px}
textarea{width:100%;height:320px;background:#0f1426;border:1px solid #26314a;border-radius:12px;color:#e6e6e6;padding:10px;resize:vertical}
.btn{display:inline-block;backgr
