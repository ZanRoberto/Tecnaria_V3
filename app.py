# app.py — Tecnaria Bot "perfetto"
# - UI integrata su "/"
# - Endpoint /ask (Responses API con fallback modelli)
# - Guard-rails Tecnaria (niente codici SPIT impropri, HS code prudente, no numeri inventati)
# - Micro-RAG locale opzionale da ./static/docs/*.txt
# - /docs disattivato, favicon silenziata

import os, time, re
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

# Lingua UI di default
DEFAULT_LANG = (os.environ.get("DEFAULT_LANG") or "it").strip().lower()

# Abilita RAG locale? (metti i .txt in ./static/docs)
ENABLE_LOCAL_RAG = (os.environ.get("ENABLE_LOCAL_RAG") or "1").strip().lower() in ("1", "true", "yes")
DOCS_DIR = Path("./static/docs")

# Limiti
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

# Intestazione per eventuali note locali
PROMPT_NOTES_HEADER = (
    "\n[NOTE TECNICHE LOCALI]\n"
    "Le seguenti note derivano da documenti interni (.txt) presenti sul server; usale SOLO come supporto "
    "per esempi/terminologia, senza contraddire istruzioni ufficiali/ETA. Non citare parti irrilevanti.\n"
)

client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# App FastAPI
# =========================
app = FastAPI(
    title="Tecnaria Bot",
    docs_url=None, redoc_url=None, openapi_url=None  # nessuna /docs
)

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
async function ask(){
  const out = document.getElementById('out');
  const q   = document.getElementById('q').value.trim();
  const lang= document.getElementById('lang').value;
  if(!q){ out.textContent = "Inserisci una domanda."; return; }
  out.textContent = "⏳ Invio...";
  try{
    const res = await fetch("/ask", {
      method:"POST",
      headers:{ "Content-Type":"application/json" },
      body: JSON.stringify({ question: q, lang: lang })
    });
    const data = await res.json();
    if(data.ok){ out.textContent = data.answer; }
    else{ out.textContent = "Errore: " + (data.detail || JSON.stringify(data)); }
  }catch(e){
    out.textContent = "Errore di rete: " + e.message;
  }
}
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

class AskResponse(BaseModel):
    ok: bool
    answer: str
    model: Optional[str] = None

# =========================
# Micro-RAG locale
# =========================
_DOC_CACHE: List[Tuple[str, str]] = []  # (filename, text)

def _load_local_docs():
    """Carica i .txt da ./static/docs se abilitato."""
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
    Semplice selezione per overlap di parole-chiave (robusto/veloce).
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
        if used >= max_chars:
            break
        chunk = txt.strip()
        if not chunk:
            continue
        # Limita contributo per file
        take = min(len(chunk), max_chars - used)
        chunk = chunk[:take]
        out.append(f"[{Path(fname).name}]\n{chunk}")
        used += take

    if not out:
        return ""
    return PROMPT_NOTES_HEADER + "\n\n".join(out)

# carico i docs una volta all'avvio
_load_local_docs()

# =========================
# OpenAI helpers
# =========================
def _is_model_not_found(e: APIStatusError) -> bool:
    msg = (getattr(e, "message", "") or str(e)).lower()
    return ("model_not_found" in msg) or ("does not exist" in msg and "model" in msg)

def _call_with_model(model: str, full_input):
    resp = client.responses.create(
        model=model,
        input=full_input,
        max_output_tokens=MAX_OUTPUT_TOKENS
    )
    if getattr(resp, "output_text", None):
        return resp.output_text.strip()
    # fallback estrazione
    items = getattr(resp, "output", None) or []
    chunks = []
    for it in items:
        content = getattr(it, "content", None) or []
        for c in content:
            if getattr(c, "type", "") == "output_text":
                chunks.append(getattr(c, "text", ""))
    return ("\n".join([c for c in chunks if c]).strip()) or str(resp)

def _call_responses(prompt: str, lang: str) -> Tuple[str, str]:
    """
    Tenta i modelli in fallback. Ritorna (answer, model_used).
    """
    notes = _best_notes_for(prompt)
    full_input = [
        {"role": "system", "content": PROMPT_BASE},
        {"role": "user",   "content": (prompt if lang == "it" else f"[Rispondi in {lang}] {prompt}") + (notes or "")},
    ]
    last_err = None
    for model in MODEL_FALLBACKS:
        if not model:
            continue
        for attempt in range(1, 3+1):
            try:
                ans = _call_with_model(model, full_input)
                return ans, model
            except APIStatusError as e:
                if e.status_code == 400 and _is_model_not_found(e):
                    break  # prova prossimo modello
                raise HTTPException(
                    status_code=502,
                    detail=f"Errore OpenAI (status {e.status_code}) con modello '{model}': {getattr(e, 'message', str(e))}"
                ) from e
            except (APIConnectionError, APITimeoutError, RateLimitError) as e:
                last_err = e
                time.sleep(1.2 * attempt)
            except Exception as e:
                last_err = e
                break
    raise HTTPException(
        status_code=504,
        detail=f"Impossibile contattare OpenAI o modelli non disponibili. Ultimo errore: {type(last_err).__name__}: {str(last_err)}"
    )

# =========================
# Post-processing (guard-rails)
# =========================
_SPIT_CODE_RX = re.compile(r"\b(spit\s*[-_]?\s*[a-z]*\d+|enk\d+)\b", re.I)
_HS_EXACT_RX  = re.compile(r"\bHS\s*code\s*[:\-]?\s*\d{4,10}\b", re.I)
_HS_PURE_RX   = re.compile(r"\b\d{6,10}\b")

def _sanitize_answer(text: str, query: str) -> str:
    out = text

    # 1) Non fissare codici chiodi: sostituisci con dicitura ufficiale
    if _SPIT_CODE_RX.search(out):
        out = _SPIT_CODE_RX.sub("chiodi idonei P560 secondo istruzioni Tecnaria", out)

    # 2) HS code: evita codici numerici precisi → usa famiglia e validazione
    if ("hs" in query.lower() or "incoterm" in query.lower() or "export" in query.lower()
        or _HS_EXACT_RX.search(out) or "HS code" in out):
            out = _HS_EXACT_RX.sub(
                "HS code: famiglia 73 (strutture in ferro/acciaio) — validare con spedizioniere/dogana", out
            )
            out = _HS_PURE_RX.sub(lambda m: "XXXX", out)

    # 3) Ammorbidisci “numeri fissi” su passi/interassi se non c’è condizione esplicita
    if "passo" in out.lower() and ("V_Ed" not in out and "VEd" not in out):
        out += ("\n\nNota: il passo/interasse finale dipende dal V_Ed di progetto e dalla PRd tabellata "
                "(profilo lamiera, cls, direzione).")

    return out.strip()

# =========================
# Endpoint principale
# =========================
@app.post("/ask", response_model=AskResponse)
def ask(payload: AskPayload):
    q = (payload.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="question mancante.")
    lang = (payload.lang or DEFAULT_LANG).strip().lower()

    # Chiamata OpenAI con fallback
    answer, model_used = _call_responses(q, lang)

    # Post-process tecnico (guard-rails)
    answer = _sanitize_answer(answer, q)

    return JSONResponse({"ok": True, "answer": answer, "model": model_used})
