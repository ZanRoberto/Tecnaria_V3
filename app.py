# app.py — Tecnaria Bot con UI + fallback modello

import os, time, re
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from openai import OpenAI
from openai._exceptions import APIConnectionError, APIStatusError, RateLimitError, APITimeoutError

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata nelle Environment Variables.")

# 1) Nome modello preferito via ENV, altrimenti default "gpt-4.1"
PREFERRED_MODEL = (os.environ.get("MODEL_NAME") or "gpt-4.1").strip()

# 2) Fallback noti (ordina a piacere). Verranno provati se il precedente dà "model_not_found".
MODEL_FALLBACKS = [
    PREFERRED_MODEL,
    "gpt-5",        # se ce l'hai abilitato
    "gpt-5-mini",   # versione mini, se disponibile
    "gpt-4o",       # 4o generalista
    "gpt-4.1",      # 4.1 standard
    "gpt-4.1-mini", # economico/veloce
]

PROMPT_BASE = (
    "Sei un tecnico/commerciale esperto di TECNARIA S.p.A. (Bassano del Grappa). "
    "Rispondi in modo chiaro e pratico. Se servono assunzioni, dichiarale. "
    "NON inventare dati di certificazioni; sii prudente. Una sola risposta completa."
)

client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI(title="Tecnaria Bot", docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AskPayload(BaseModel):
    question: str
    lang: Optional[str] = "it"

def _is_model_not_found(api_status_error: APIStatusError) -> bool:
    msg = getattr(api_status_error, "message", "") or str(api_status_error)
    msg = msg.lower()
    return ("model_not_found" in msg) or ("does not exist" in msg and "model" in msg)

def _call_with_model(model: str, prompt: str):
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": PROMPT_BASE},
            {"role": "user", "content": prompt},
        ],
    )
    if getattr(resp, "output_text", None):
        return resp.output_text.strip()
    # fallback di estrazione
    items = getattr(resp, "output", None) or []
    chunks = []
    for it in items:
        content = getattr(it, "content", None) or []
        for c in content:
            if getattr(c, "type", "") == "output_text":
                chunks.append(getattr(c, "text", ""))
    return ("\n".join([c for c in chunks if c]).strip()) or str(resp)

def _call_openai_responses(prompt: str, max_retries: int = 3, base_delay: float = 1.2) -> str:
    last_err = None
    # Proviamo i modelli in cascata se uno non esiste
    for model in MODEL_FALLBACKS:
        if not model:
            continue
        for attempt in range(1, max_retries + 1):
            try:
                return _call_with_model(model, prompt)
            except APIStatusError as e:
                if e.status_code == 400 and _is_model_not_found(e):
                    # modello non disponibile → prova il prossimo
                    break
                # altri 4xx/5xx: propaghiamo
                raise HTTPException(
                    status_code=502,
                    detail=f"Errore OpenAI (status {e.status_code}) con modello '{model}': {getattr(e, 'message', str(e))}"
                ) from e
            except (APIConnectionError, APITimeoutError, RateLimitError) as e:
                last_err = e
                time.sleep(base_delay * attempt)
            except Exception as e:
                last_err = e
                break
        # se siamo qui e non abbiamo fatto return: o rate/timeout/exception — passiamo al prossimo modello
        continue

    # se nessun modello ha funzionato
    raise HTTPException(
        status_code=504,
        detail=f"Impossibile contattare OpenAI o modello non disponibile. Ultimo errore: {type(last_err).__name__}: {str(last_err)}",
    )

# ---------------- UI minimale su "/" (così 'parte dal Render') ----------------
HOME_HTML = """<!doctype html>
<meta charset="utf-8" />
<title>Tecnaria Bot</title>
<style>
  :root { --fg:#111; --muted:#666; --bd:#e5e7eb; --bg:#fff; }
  html,body{background:var(--bg); color:var(--fg); font:16px system-ui, Arial; margin:0; padding:0}
  .wrap{max-width:920px; margin:40px auto; padding:0 20px}
  h1{font-size:22px; margin:0 0 6px}
  p.m{color:var(--muted); margin:0 0 16px}
  textarea{width:100%; height:150px; padding:12px; border:1px solid var(--bd); border-radius:12px; box-sizing:border-box; font:16px/1.3 system-ui, Arial}
  .row{display:flex; gap:12px; align-items:center; margin:10px 0 0}
  select{padding:10px; border:1px solid var(--bd); border-radius:10px}
  button{padding:12px 18px; border:1px solid var(--bd); border-radius:12px; background:#f8f9fb; cursor:pointer}
  button:active{transform:translateY(1px)}
  .out{white-space:pre-wrap; border:1px solid var(--bd); border-radius:12px; padding:12px; margin-top:14px; min-height:80px}
  .small{font-size:12px; color:var(--muted); margin-top:8px}
</style>
<div class="wrap">
  <h1>🚀 Tecnaria Bot</h1>
  <p class="m">Fai una domanda tecnica o commerciale e premi “Chiedi”.</p>

  <label style="font-weight:600">Domanda</label>
  <textarea id="q" placeholder="Es: Quali sono le differenze tra CTF e CTL?"></textarea>

  <div class="row">
    <select id="lang">
      <option value="it" selected>Italiano</option>
      <option value="en">English</option>
      <option value="fr">Français</option>
      <option value="de">Deutsch</option>
      <option value="es">Español</option>
    </select>
    <button onclick="ask()">Chiedi</button>
  </div>

  <div id="out" class="out"></div>
  <div class="small">Endpoint: <code>/ask</code> • Modello preferito: <code>""" + PREFERRED_MODEL + """</code></div>
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

@app.post("/ask")
def ask(payload: AskPayload):
    q = (payload.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="question mancante.")
    lang = (payload.lang or "it").strip().lower()
    final_prompt = q if lang == "it" else f"[Rispondi in {lang}] {q}"
    answer = _call_openai_responses(final_prompt)
    return JSONResponse({"ok": True, "answer": answer})
