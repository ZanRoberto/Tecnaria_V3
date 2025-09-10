# app.py — Tecnaria Bot (Document-Only, FastAPI + Uvicorn)

import os
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import HTMLResponse
from scraper_tecnaria import risposta_document_first, reload_index

BOT_OFFLINE_ONLY = os.getenv("BOT_OFFLINE_ONLY", "true").lower() == "true"
DOC_FOLDER = os.getenv("DOC_FOLDER", "./documenti_gTab")

app = FastAPI(title="Tecnaria Bot - Document Only")

class Query(BaseModel):
    question: str

# -----------------------------
# Home page (mini UI)
# -----------------------------
@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Tecnaria Bot (document-only)</title>
  <style>
    :root{--b:#111827;--g:#e5e7eb;--m:#6b7280}
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         margin:24px;line-height:1.45;background:#fff}
    .card{max-width:960px;margin:auto;border:1px solid var(--g);border-radius:14px;padding:20px}
    textarea{width:100%;min-height:110px;padding:10px;border:1px solid var(--g);border-radius:10px}
    button{padding:10px 16px;border:0;border-radius:10px;background:var(--b);color:#fff;cursor:pointer}
    .muted{color:var(--m);font-size:14px}
    pre{white-space:pre-wrap;border:1px solid var(--g);border-radius:10px;padding:12px;background:#fafafa}
    .src{font-size:13px;color:#374151}
    a{color:var(--b);text-decoration:none}
  </style>
</head>
<body>
  <div class="card">
    <h2>🧠 Tecnaria Bot — solo documenti locali</h2>
    <p class="muted">Il bot risponde esclusivamente leggendo i file <code>.txt</code> in <code>documenti_gTab/</code>.</p>
    <textarea id="q" placeholder="Esempio: Come funziona il noleggio della P560?"></textarea>
    <div style="margin-top:10px;display:flex;gap:12px;align-items:center;">
      <button onclick="ask()">Chiedi</button>
      <a href="/docs">→ Open API Docs</a>
      <a href="/healthz">→ Health</a>
      <button onclick="reloadIdx()" title="Ricarica l'indice dei .txt">Ricarica indice</button>
    </div>
    <div id="out" style="margin-top:18px;"></div>
  </div>
<script>
async function ask(){
  const q = document.getElementById('q').value.trim();
  const out = document.getElementById('out');
  if(!q){ out.innerHTML = '<p class="muted">Scrivi una domanda…</p>'; return; }
  out.innerHTML = '<p class="muted">Sto cercando nei documenti…</p>';
  try{
    const res = await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q})});
    const data = await res.json();
    if(!data.found){
      out.innerHTML = `<pre>${data.answer}</pre>`;
      return;
    }
    const src = (data.sources||[]).map(s=>`• ${s}`).join('\\n');
    out.innerHTML = `<pre>${data.answer}</pre><p class="src"><b>Fonti:</b><br>${src}</p>`;
  }catch(e){
    out.innerHTML = `<pre>Errore di rete: ${e}</pre>`;
  }
}
async function reloadIdx(){
  const out = document.getElementById('out');
  out.innerHTML = '<p class="muted">Ricarico indice…</p>';
  try{
    const res = await fetch('/reload',{method:'POST'});
    const data = await res.json();
    out.innerHTML = `<pre>Indice ricaricato. Documenti indicizzati: ${data.documents}</pre>`;
  }catch(e){
    out.innerHTML = `<pre>Errore: ${e}</pre>`;
  }
}
</script>
</body>
</html>
    """

# -----------------------------
# Health
# -----------------------------
@app.get("/healthz")
def healthz():
    return {"ok": True, "offline_only": BOT_OFFLINE_ONLY, "doc_folder": DOC_FOLDER}

# -----------------------------
# Q&A endpoint (document-only)
# -----------------------------
@app.post("/ask")
def ask(q: Query):
    # Sempre e solo dai documenti locali
    return risposta_document_first(q.question)

# -----------------------------
# Reload indice documenti
# -----------------------------
@app.post("/reload")
def reload_docs():
    n = reload_index()
    return {"ok": True, "documents": n}

# Avvio locale (Render usa lo Start Command con uvicorn)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")), log_level="info")
