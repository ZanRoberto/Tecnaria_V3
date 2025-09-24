# app.py — Tecnaria Bot "perfetto": UI su /, /ask, Responses API, fallback modelli,
# guard-rails specifici Tecnaria, micro-RAG locale opzionale (./static/docs/*.txt)

import os, time, re, glob, json
from pathlib import Path
from typing import Optional, List, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel

# OpenAI Responses API (SDK >= 1.40)
from openai import OpenAI
from openai._exceptions import (
    APIConnectionError, APIStatusError, RateLimitError, APITimeoutError
)

# =========================
# Configurazione
# =========================
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata nelle Environment Variables.")

# Modello preferito da ENV (es. "gpt-4o" o "gpt-4.1"). Default prudente: gpt-4.1
PREFERRED_MODEL = (os.environ.get("MODEL_NAME") or "gpt-4.1").strip()

# Fallback (ordine di prova)
MODEL_FALLBACKS = [
    PREFERRED_MODEL,
    "gpt-5",        # se abilitato sul tuo account
    "gpt-5-mini",   # se abilitato
    "gpt-4o",
    "gpt-4.1",
    "gpt-4.1-mini",
]

# UI lingua default
DEFAULT_LANG = (os.environ.get("DEFAULT_LANG") or "it").strip().lower()

# Abilita RAG locale? (metti i .txt in ./static/docs)
ENABLE_LOCAL_RAG = (os.environ.get("ENABLE_LOCAL_RAG") or "1").strip() in ("1","true","yes")
DOCS_DIR = Path("./static/docs")

# Limiti (puoi alzarli/abbassarli)
MAX_CTX_NOTES_CHARS = int(os.environ.get("MAX_CTX_NOTES_CHARS", "20000"))  # somma testi RAG
MAX_OUTPUT_TOKENS    = int(os.environ.get("MAX_OUTPUT_TOKENS", "1200"))    # risposta massima

# Prompt “policy Tecnaria”
PROMPT_BASE = (
    "Sei un tecnico/commerciale esperto di TECNARIA S.p.A. (Bassano del Grappa). "
    "Regole d'oro:\n"
    "1) Non inventare numeri di tabelle o certificazioni; se servono valori PRd, dì di fare riferimento alle tabelle ufficiali (es. ETA-18/0447) per il caso specifico.\n"
    "2) Per connettori CTF su lamiera con SPIT P560: indicare '2 chiodi idonei secondo istruzioni Tecnaria' (non fissare codici SPIT non ufficiali).\n"
    "3) Per HS code: inquadra nella famiglia 73 (strutture in ferro/acciaio) e specifica di 'validare con lo spedizioniere/dogana' il codice preciso per paese/prodotto.\n"
    "4) Passi/interassi: ricordare che dipendono dal V_Ed e dalle PRd tabellate (profilo lamiera, classe cls, direzione). Evita di prescrivere numeri fissi senza calcolo.\n"
    "5) Una sola risposta completa, chiara, operativa. Se fai assunzioni, dichiarale.\n"
)

# Messaggio di contesto per eventuali note locali
PROMPT_NOTES_HEADER = (
    "\n[NOTE TECNICHE LOCALI]\n"
    "Le seguenti note derivano da documenti interni (.txt) presenti sul server; usale SOLO come supporto "
    "per esempi/terminologia, senza contraddire istruzioni ufficiali/ETA. Non citare parti irrilevanti.\n"
)

client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# App FastAPI
# =========================
app = FastAPI(title="Tecnaria Bot",
              docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# =========================
# UI minimal su "/"
# =========================
HOME_HTML = f"""<!doctype html>
<meta charset="utf-8" />
<title>Tecnaria Bot</title>
<style>
  :root {{ --fg:#111; --muted:#666; --bd:#e5e7eb; --bg:#fff; }}
  html,body{{background:var(--bg); color:var(--fg); font:16px system-ui, Arial; margin:0; padding:0}}
  .wrap{{max-width:920px; margin:40px auto; padding:0 20px}}
  h1{{font-size:22px; margin:0 0 6px}}
  p.m{{color:var(--muted); margin:0 0 16px}}
  textarea{{width:100%; height:150px; padding:12px; border:1px solid var(--bd); border-radius:12px; box-sizing:border-box; font:16px/1.3 system-ui, Arial}}
  .row{{display:flex; gap:12px; align-items:center; margin:10px 0 0}}
  select,button{{padding:10px; border:1px solid var(--bd); border-radius:10px}}
  button{{background:#f8f9fb; cursor:pointer}}
  button:active{{transform:translateY(1px)}}
  .out{{white-space:pre-wrap; border:1px solid var(--bd); border-radius:12px; padding:12px; margin-top:14px; min-height:80px}}
  .small{{font-size:12px; color:var(--muted); margin-top:8px}}
</style>
<div class="wrap">
  <h1>🚀 Tecnaria Bot</h1>
  <p class="m">Fai una domanda tecnica o commerciale e premi “Chiedi”.</p>

  <label style="font-weight:600">Domanda</label>
  <textarea id="q" placeholder="Es: Solaio H55 C30/37: scelta CTF075/CTF090, passo e P560?"></textarea>

  <div class="row">
    <select id="lang">
      <option value="it" {"selected" if DEFAULT_LANG=="it" else ""}>Italiano</option>
      <option value="en" {"selected" if DEFAULT_LANG=="en" else ""}>English</option>
      <option value="fr" {"selected" if DEFAULT_LANG=="fr" else ""}>Français</option>
      <option value="de" {"selected" if DEFAULT_LANG=="de" else ""}>Deutsch</option>
      <option value="es" {"selected" if DEFAULT_LANG=="es" else ""}>Español</option>
    </select>
    <button onclick="ask()">Chiedi</button>
  </div>

  <div id="out" class="out"></div>
  <div class="small">Endpoint: <code>/ask</code> • Modello preferito: <code>{PREFERRED_MODEL}</code> • RAG locale: <code>{"ON" if ENABLE_LOCAL_RAG else "OFF"}</code></div>
</div>
<script>
async function ask(){{
  const out = document.getElementById('out');
  const q   = document.getElementById('q').value.trim();
  const lang= document.getElementById('lang').value;
  if(!q){{ out.textContent = "Inserisci una domanda."; return; }}
  out.textContent = "⏳ Invio...";
  try{{
    const res = await fetch("/ask", {{
      method:"POST",
      headers:{{ "Content-Type":"application/json" }},
      body: JSON.stringify({{ question: q, lang: lang }})
    }});
    const data = await res.json();
    if(data.ok){{ out.textContent = data.answer; }}
    else{{ out.textContent = "Errore: " + (data.detail || JSON.stringify(data)); }}
  }}catch(e){{
    out.textContent = "Errore di rete: " + e.message;
  }}
}}
</script>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(HOME_HTML)

@app.get("/favicon.ico")
def favicon():
    return PlainTextResponse("", status_code=204)

# =========================
# Schemi
# =========================
class AskPayload(BaseModel):
    question: str
    lang: Optional[str] = DEFAULT_LANG

# =========================
# Micro-RAG locale
# =========================
_DOC_CACHE: List[Tuple[str, str]] = []  # (filename, text)

def _load_local_docs():
    global _DOC_CACHE
    _DOC_CACHE = []
    if not ENABLE_LOCAL_RAG:
        return
    if not DOCS_DIR.exists():
        return
    for path in DOCS_DIR.glob("**/*.txt"):
        try:
            txt = path.read_text(encoding="utf-8", errors="ignore")
            if txt.strip():
                _DOC_CACHE.append((str(path), txt))
        except Exception:
            continue

def _best_notes_for(query: str, max_chars: int = MAX_CTX_NOTES_CHARS) -> str:
    """
    Semplice selezione per overlap di parole-chiave.
    Non fa pesi semantici, ma è robusta e veloce su Render.
    """
    if not ENABLE_LOCAL_RAG or not _DOC_CACHE:
        return ""
    q_words = set(re.findall(r"[a-zA-Z0-9\-/]+", query.lower()))
    scored = []
    for fname, txt in _DOC_CACHE:
        lw = txt.lower()
        hit = sum(1 for w in q_words if w and w in lw)
        if hit:
            scored.append((hit, fname, txt))
    scored.sort(reverse=True, key=lambda x: x[0])
    out, used = [], 0
    for _, fname, txt in scored:
