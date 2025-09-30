import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from openai import OpenAI

# ------------------- Config -------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata nelle Environment Variables di Render.")

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.0")
OPENAI_MODEL_FALLBACK = os.environ.get("OPENAI_MODEL_FALLBACK")  # opzionale

client = OpenAI(api_key=OPENAI_API_KEY)

# ------------------- Prompt universale con stile -------------------
PROMPT = """
Sei un tecnico esperto di TECNARIA S.p.A. (Bassano del Grappa) e rispondi su:
- Connettori per solai collaboranti: CTF (lamiera grecata), CTL (legno-calcestruzzo), CTCEM/VCEM (acciaio-calcestruzzo), sistemi correlati e accessori (es. chiodatrice SPIT P560, chiodi/propulsori, kit e adattatori).
- Ambiti di utilizzo, posa, compatibilità, vantaggi, limiti d’impiego, indicazioni generali su certificazioni/ETA e documentazione tecnica.
- Se la domanda richiede dati non presenti, NON inventare: dì chiaramente che l’informazione non è disponibile e proponi documentazione/contatto tecnico.

Regole di risposta (stile):
1) Domanda semplice/commerciale → risposta BREVE, chiara, rassicurante.
2) Domanda tecnica (progettista/ingegnere, normative, prestazioni) → risposta DETTAGLIATA con logica tecnica e riferimenti (senza inventare codici/ETA specifici: se mancano, indica che puoi fornirli su richiesta).
3) Domanda ambigua → risposta STANDARD, poi offri di inviare schede tecniche/ETA o link ai PDF.
4) Mai allungare inutilmente: la risposta deve essere corretta; varia solo la profondità (breve/standard/dettagliata).
5) Se la domanda riguarda la P560: specifica che si usa per fissaggi su acciaio/lamiera (es. CTF, travi metalliche) e NON serve per fissaggi su legno puro (es. CTL) dove si usano viti/bulloni tradizionali.
6) Se non sei certo di un dato (codici articolo, PRd, ETA numeriche, combinazioni di lamiera specifiche), dichiara la non disponibilità e suggerisci il documento o il canale corretto senza inventare.

Tono: tecnico, professionale, concreto. Linguaggio italiano. Non inserire markdown eccessivo: usa elenchi puntati solo se migliorano la leggibilità.
"""

# ------------------- FastAPI -------------------
app = FastAPI(title="Tecnaria Bot - Risposte uniformi (Prompt con stile)")

class AskPayload(BaseModel):
    question: str
    # opzionale: lascia vuoto, lo stile lo decide il prompt
    context: Optional[str] = None

@app.get("/", response_class=HTMLResponse)
def root():
    # Semplice UI monodominio (domanda → risposta)
    html = """
<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <title>Tecnaria Bot</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html,body {font-family: system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,"Helvetica Neue",Arial,"Noto Sans","Apple Color Emoji","Segoe UI Emoji"; margin:0; padding:0; background:#0b0f19; color:#e6e6e6;}
    .wrap {max-width:900px; margin:32px auto; padding:0 16px;}
    h1 {font-size:24px; margin:0 0 12px;}
    p.sub {opacity:.7; margin:0 0 20px;}
    form {display:flex; gap:8px; margin:16px 0 12px;}
    input[type=text] {flex:1; padding:12px 14px; border-radius:12px; border:1px solid #273047; background:#12182b; color:#e6e6e6;}
    button {padding:12px 16px; border:0; border-radius:12px; background:#3a5bfd; color:#fff; cursor:pointer; font-weight:600;}
    button:disabled {opacity:.6; cursor:not-allowed;}
    .card {background:#0f1527; border:1px solid #273047; border-radius:14px; padding:16px; margin-top:12px; white-space:pre-wrap; line-height:1.45}
    .small {font-size:12px; opacity:.7; margin-top:8px;}
    .foot {opacity:.55; font-size:12px; margin-top:18px}
    a {color:#8fb3ff; text-decoration:none}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Tecnaria Bot</h1>
    <p class="sub">Una domanda alla volta. Risposta unica, con stile deciso dal prompt (breve/standard/dettagliata).</p>
    <form id="f">
      <input id="q" type="text" placeholder="Scrivi la tua domanda su CTF, CTL, P560, ecc." required />
      <button id="b" type="submit">Chiedi</button>
    </form>
    <div id="out" class="card" style="display:none"></div>
    <div id="meta" class="small"></div>
    <div class="foot">Modello: <span id="model"></span></div>
  </div>
<script>
const f = document.getElementById('f');
const q = document.getElementById('q');
const b = document.getElementById('b');
const out = document.getElementById('out');
const meta = document.getElementById('meta');
const modelSpan = document.getElementById('model');

async function ask(question){
  const res = await fetch('/api/ask', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({question})
  });
  if(!res.ok){
    const t = await res.text();
    throw new Error(t || ('HTTP ' + res.status));
  }
  return res.json();
}

f.addEventListener('submit', async (e)=>{
  e.preventDefault();
  b.disabled = true;
  out.style.display = 'block';
  out.textContent = 'Sto pensando...';
  meta.textContent = '';
  try{
    const data = await ask(q.value);
    out.textContent = data.answer || '(nessuna risposta)';
    meta.textContent = data.info ? ('Info: ' + data.info) : '';
    modelSpan.textContent = data.model || '';
  }catch(err){
    out.textContent = 'Errore: ' + (err.message || err);
  }finally{
    b.disabled = false;
  }
});
</script>
</body>
</html>
"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})

@app.get("/health")
def health():
    return JSONResponse({"status": "ok", "service": "Tecnaria Bot - Prompt con stile"})

def _call_openai(model: str, question: str, context: Optional[str]) -> str:
    """
    Chiama la Responses API con messaggi (system + user). Ritorna il testo.
    """
    msgs = [
        {"role": "system", "content": PROMPT.strip()},
        {"role": "user", "content": question if not context else f"{question}\n\nContesto:\n{context}"}
    ]
    resp = client.responses.create(
        model=model,
        input=msgs,
        temperature=0.2,          # stabilità
        max_output_tokens=750,    # sufficiente per risposte dettagliate
    )
    # Estrarre il testo in modo robusto
    for item in resp.output:
        if item.type == "message" and item.message and item.message.content:
            # content è una lista di blocchi (text, tool, ecc.)
            chunks = []
            for c in item.message.content:
                if c.get("type") == "output_text" and "text" in c:
                    chunks.append(c["text"])
            if chunks:
                return "\n".join(chunks).strip()
    # Fallback generico
    return (getattr(resp, "output_text", None) or "").strip() or "Non ho trovato una risposta utile con i dati disponibili."

@app.post("/api/ask")
def api_ask(payload: AskPayload):
    question = (payload.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="La domanda è vuota.")

    # Primo tentativo con il modello principale
    try:
        answer = _call_openai(OPENAI_MODEL, question, payload.context)
        return JSONResponse({"answer": answer, "model": OPENAI_MODEL, "info": "primary"})
    except Exception as e_primary:
        # Fallback opzionale
        if OPENAI_MODEL_FALLBACK:
            try:
                answer = _call_openai(OPENAI_MODEL_FALLBACK, question, payload.context)
                return JSONResponse({"answer": answer, "model": OPENAI_MODEL_FALLBACK, "info": "fallback"})
            except Exception as e_fallback:
                raise HTTPException(status_code=500, detail=f"Errore modelli (fallback): {e_fallback}") from e_fallback
        raise HTTPException(status_code=500, detail=f"Errore modello primario: {e_primary}")

# Avvio locale (su Render usa Procfile/gunicorn/uvicorn)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
