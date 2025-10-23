# tecnaria_api.py — shim UI + test GET/POST /api/ask per l'app FastAPI esistente
# Render deve avviare: uvicorn tecnaria_api:app --host 0.0.0.0 --port $PORT

from fastapi.responses import HTMLResponse, JSONResponse
from fastapi import Query
import time

# Importa l'istanza FastAPI già definita in app.py
# (DEVE esistere in app.py: app = FastAPI(...), intent_route(q: str) -> dict)
from app import app  # usa SEMPRE questa app, NON ridefinire app qui!

try:
    from app import intent_route, JSON_BAG, DATA_DIR, FAQ_ROWS
except Exception:
    intent_route = None
    JSON_BAG = {}
    DATA_DIR = None
    FAQ_ROWS = 0

# ---- UI HTML (dark, responsive) ----
UI_HTML = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Tecnaria • Q&A</title>
<style>
  :root{
    --bg:#0f0f12; --card:#15161b; --muted:#8b8ea3; --text:#e9eaf0;
    --brand:#ff6b00; --brand2:#ffa149; --ok:#17c964; --warn:#f5a524;
  }
  *{box-sizing:border-box}
  body{margin:0;background:linear-gradient(180deg,#0d0e12,#141620 40%,#0d0e12);
       font-family:Inter,system-ui,Segoe UI,Roboto,Arial,sans-serif;color:var(--text)}
  .wrap{max-width:980px;margin:0 auto;padding:24px}
  header{display:flex;align-items:center;gap:12px;margin:8px 0 16px}
  .logo{width:40px;height:40px;border-radius:12px;background:linear-gradient(135deg,var(--brand),var(--brand2))}
  h1{font-size:20px;margin:0}
  .card{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.06);border-radius:16px;
        box-shadow:0 10px 30px rgba(0,0,0,.35)}
  .ask{padding:18px;display:flex;gap:10px;flex-wrap:wrap}
  input[type=text]{flex:1;min-width:220px;padding:14px 16px;border-radius:12px;background:#0f1116;
    border:1px solid #2a2d39;color:var(--text);font-size:16px;outline:none}
  button{padding:14px 18px;border-radius:12px;border:none;font-weight:600;cursor:pointer}
  .b1{background:linear-gradient(90deg,var(--brand),var(--brand2));color:#111}
  .b2{background:#222635;color:#d6d8e4;border:1px solid #2a2d39}
  .row{display:flex;gap:16px;flex-wrap:wrap;padding:18px}
  .col{flex:1;min-width:280px}
  .pill{display:inline-block;padding:4px 10px;border-radius:999px;background:#1b1e29;border:1px solid #2a2d39;color:#cfd1dc;font-size:12px}
  .pre{white-space:pre-wrap;word-wrap:break-word;background:#0c0e13;border:1px solid #22263a;border-radius:12px;padding:12px}
  footer{opacity:.6;font-size:12px;margin-top:10px}
</style>
</head>
<body>
<div class="wrap">
  <header><div class="logo"></div><h1>Tecnaria • Q&A</h1></header>

  <div class="card">
    <div class="ask">
      <input id="q" type="text" placeholder="Fai una domanda (es. 'Differenza tra CTF e CTL?')" />
      <button class="b1" onclick="ask()">Chiedi</button>
      <button class="b2" onclick="demo()">Esempi</button>
    </div>
    <div class="row">
      <div class="col">
        <div class="pill">Testo</div>
        <div id="text" class="pre" style="min-height:120px"></div>
      </div>
      <div class="col">
        <div class="pill">HTML</div>
        <div id="html" class="pre" style="min-height:120px"></div>
      </div>
    </div>
    <div class="row" style="border-top:1px solid #212433">
      <div class="col">
        <div class="pill">Meta</div>
        <div id="meta" class="pre"></div>
      </div>
    </div>
  </div>

  <footer>Stato: <span id="status">pronto</span></footer>
</div>

<script>
const statusEl = document.getElementById('status');
const qEl = document.getElementById('q');
const textEl = document.getElementById('text');
const htmlEl = document.getElementById('html');
const metaEl = document.getElementById('meta');

async function ask() {
  const q = qEl.value.trim();
  if(!q){ qEl.focus(); return; }
  statusEl.textContent = 'invio…';
  try{
    const r = await fetch('/api/ask', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ q })
    });
    const j = await r.json();
    textEl.textContent = j.text || '';
    htmlEl.innerHTML = j.html || '';
    metaEl.textContent = JSON.stringify({
      ok:j.ok, match_id:j.match_id, lang:j.lang, family:j.family,
      intent:j.intent, source:j.source, ms:j.ms, score:j.score
    }, null, 2);
    statusEl.textContent = 'ok';
  }catch(e){
    statusEl.textContent = 'errore';
    metaEl.textContent = String(e);
  }
}

function demo(){
  const samples = [
    "Differenza tra CTF e CTL?",
    "Quando scegliere CTL invece di CEM-E?",
    "CTF su lamiera grecata: controlli in cantiere?",
    "VCEM su essenze dure: serve preforo 70–80%?",
    "GTS: che cos’è e come si usa?",
    "P560: è un connettore o un'attrezzatura?"
  ];
  qEl.value = samples[Math.floor(Math.random()*samples.length)];
  ask();
}
window.addEventListener('keydown', (ev)=>{ if(ev.key === 'Enter') ask(); });
</script>
</body></html>
"""

# ---- /ui (pagina) ----
@app.get("/ui", response_class=HTMLResponse)
def ui_page():
    return HTMLResponse(content=UI_HTML)

# ---- /__routes (debug rapido) ----
@app.get("/__routes")
def __routes():
    try:
        routes = [
            {"path": r.path, "name": r.name, "methods": list(getattr(r, "methods", []) or [])}
            for r in app.routes
        ]
    except Exception:
        routes = []
    return JSONResponse({"routes": routes})

# ---- GET /api/ask (test da browser) ----
@app.get("/api/ask")
def api_ask_get(q: str = Query("", description="Domanda")):
    if intent_route is None:
        return JSONResponse({"ok": False, "error": "intent_route non disponibile in app.py"}, status_code=500)
    t0 = time.time()
    routed = intent_route(q or "")
    ms = int((time.time() - t0) * 1000) or 1
    return {
        "ok": True,
        "match_id": str(routed.get("match_id") or "<NULL>"),
        "ms": ms,
        "text": str(routed.get("text") or ""),
        "html": str(routed.get("html") or ""),
        "lang": routed.get("lang"),
        "family": routed.get("family"),
        "intent": routed.get("intent"),
        "source": routed.get("source"),
        "score": routed.get("score"),
    }
