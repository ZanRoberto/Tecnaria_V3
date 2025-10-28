# app.py — Tecnaria Sinapsi (paracadute stabile)
# Avvio: uvicorn app:app --host 0.0.0.0 --port $PORT
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import json

app = FastAPI(title="Tecnaria Sinapsi")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)

# --------- Helpers per caricare eventuali GOLD se presenti ----------
BASE = Path(__file__).parent
DATA = BASE / "static" / "data"
GOLD_FILES = [
    "ctf_gold.json", "ctl_gold.json", "p560_gold.json",
    "vcem_gold.json", "diapason_gold.json", "gts_gold.json",
    "ceme_gold.json", "accessori_gold.json"
]
GOLD = []

def _safe_load_gold():
    items = []
    if DATA.exists():
        for name in GOLD_FILES:
            p = DATA / name
            if p.exists():
                try:
                    with p.open("r", encoding="utf-8") as fh:
                        items.extend(json.load(fh))
                except Exception:
                    # se un file è corrotto, continuiamo con gli altri
                    pass
    return items

GOLD = _safe_load_gold()

# --------- UI minimale per evitare 404 sul root ----------
HTML = """
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Tecnaria Sinapsi</title>
<style>
body{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;background:
linear-gradient(180deg,#1c1c1c 0,#1c1c1c 20%,#ff6a00 160%);margin:0;color:#111}
.header{padding:28px 20px;color:#fff}
.header h1{margin:0;font-weight:700}
.container{max-width:1000px;margin:0 auto;padding:24px}
.card{background:#fff;border-radius:14px;box-shadow:0 10px 25px rgba(0,0,0,.15);padding:18px}
.row{display:flex;gap:12px;align-items:center}
input[type=text]{flex:1;font-size:18px;padding:14px 16px;border-radius:12px;border:1px solid #ddd;outline:none}
button{background:#111;color:#fff;border:0;border-radius:12px;padding:14px 18px;font-weight:700;cursor:pointer}
.badge{display:inline-block;background:#111;color:#fff;padding:4px 10px;border-radius:999px;font-size:12px;margin-left:8px}
.small{opacity:.7;font-size:12px}
pre{white-space:pre-wrap;word-wrap:break-word}
</style>
</head>
<body>
  <div class="header">
    <div class="container">
      <h1>Trova la soluzione, in linguaggio Tecnaria.</h1>
      <div class="small">CTF, CTL, CTL MAXI, Diapason, GTS, Mini-Cem-E, SPIT P560.</div>
    </div>
  </div>
  <div class="container">
    <div class="card">
      <div class="row">
        <input id="q" type="text" placeholder="Scrivi la tua domanda… Es: Mi spieghi la chiodatrice P560?"/>
        <button onclick="ask()">Chiedi a Sinapsi</button>
      </div>
      <div style="margin-top:14px">
        <span class="small">Endpoint</span>
        <span class="badge" id="ep">/qa/ask</span>
        <span class="small" style="margin-left:10px">Health</span>
        <span class="badge" id="health">–</span>
      </div>
    </div>
    <div style="height:14px"></div>
    <div class="card">
      <div class="small">Risposta</div>
      <pre id="out">(nessuna)</pre>
    </div>
  </div>
<script>
async function ping(){
  try{
    const r = await fetch('/health');
    const j = await r.json();
    document.getElementById('health').textContent = j.status || 'ok';
  }catch(e){ document.getElementById('health').textContent = 'down'; }
}
async function ask(){
  const q = document.getElementById('q').value.trim();
  if(!q){ return; }
  const url = '/qa/ask?q=' + encodeURIComponent(q);
  document.getElementById('ep').textContent = '/qa/ask';
  const r = await fetch(url);
  const j = await r.json();
  document.getElementById('out').textContent = j.answer || JSON.stringify(j,null,2);
}
ping();
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTML

@app.get("/health")
def health():
    return {"status": "ok", "loaded_items": len(GOLD)}

# --------- Endpoint Q/A minimo, pronto a test ---------
@app.get("/qa/ask")
def qa(q: str = Query(..., min_length=1)):
    text = q.lower().strip()

    # 1) risposte-semplici: P560, codici CTF, differenze CTL/CTL MAXI
    if "p560" in text or "chiodatrice" in text:
        return {"answer": ("No: per i CTF Tecnaria è ammessa esclusivamente la chiodatrice "
                           "SPIT P560 con kit/adattatori dedicati Tecnaria. Ogni connettore si posa "
                           "con 2 chiodi HSBR14; eseguire 2–3 tiri di prova per taratura e registrare "
                           "potenza/lotti nel giornale lavori.")}

    if "codici" in text and "ctf" in text:
        # se abbiamo GOLD caricati, prova ad estrarre liste; altrimenti risposta tecnica
        codes = []
        for it in GOLD:
            # tenta campi tipici
            for k in ("code","codice","sku","id"):
                if isinstance(it, dict) and k in it and isinstance(it[k], str) and "CTF" in it.get("family","CTF"):
                    codes.append(it[k])
        if codes:
            uniq = sorted(set(codes))
            return {"answer": "Codici CTF disponibili (estratto): " + ", ".join(uniq[:30]) + (" …" if len(uniq)>30 else "")}
        else:
            return {"answer": ("I codici CTF variano per configurazione; se nel progetto sono presenti i file GOLD "
                               "(ctf_gold.json, ecc.), verranno elencati automaticamente. In assenza, richiedi il listino "
                               "aggiornato o consulta la documentazione Tecnaria.")}

    if "differenza" in text and "ctl" in text and "maxi" in text:
        return {"answer": ("CTL (standard) lavora su trave in legno senza tavolato e viti Ø10×100/120 con solette 4–5 cm; "
                           "CTL MAXI è progettato per tavolato/assito ≥25–30 mm, con viti Ø10×120/140 e solette 5–6 cm. "
                           "La testa del MAXI deve risultare sopra la rete ma sotto il filo del getto.")}

    # 2) fallback: se esistono GOLD, cerca la prima answer compatibile
    for it in GOLD:
        if isinstance(it, dict):
            # euristica banalissima (da sostituire col tuo router semantico)
            txt = " ".join(str(v) for v in it.values()).lower()
            if all(tok in txt for tok in text.split()[:2]):  # grezzo ma evita 0 risposte
                ans = it.get("answer") or it.get("risposta") or it.get("text") or ""
                if ans:
                    return {"answer": ans}

    # 3) fallback definitivo
    return {"answer": ("Nessun dato puntuale trovato. Verifica che i file GOLD siano presenti in "
                       "static/data/ e che la domanda citi CTF/CTL/P560/Diapason/GTS in modo riconoscibile.")}
