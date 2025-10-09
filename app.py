import os, re, json, time, threading
from typing import Dict, Tuple
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

# ================ CONFIG ================
BASE_DIR = os.path.dirname(__file__)
DATA_PATH = os.path.join(BASE_DIR, "static", "data", "SINAPSI_GLOBAL_TECNARIA_EXT.json")
I18N_DIR = os.path.join(BASE_DIR, "static", "i18n")
I18N_CACHE_DIR = os.getenv("I18N_CACHE_DIR", os.path.join(BASE_DIR, "static", "i18n-cache"))
ALLOWED_LANGS = {"it", "en", "fr", "de", "es"}
_lock = threading.Lock()

# ================ UTILS ================
def load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERRORE] Impossibile leggere {path}: {e}")
        return {}

# Mappe multi-lingua → domanda canonica IT presente nel KB
CANON = {
    # CTF — codici
    r"\bcan you (tell|list).*\bctf code": "mi puoi dire i codici dei ctf?",
    r"puedes.*c[oó]digos.*ctf": "mi puoi dire i codici dei ctf?",
    r"peux[- ]tu.*codes.*ctf": "mi puoi dire i codici dei ctf?",
    r"kannst du.*ctf.*codes": "mi puoi dire i codici dei ctf?",
    # CTF — posa/chiodatrice (P560)
    r"\bhow to install\b.*ctf|tools and constraints": "connettori ctf: si può usare una chiodatrice qualsiasi?",
    r"peux[- ]tu.*poser.*ctf|outils.*contraintes": "connettori ctf: si può usare una chiodatrice qualsiasi?",
    r"wie.*montiert.*ctf|werkzeuge|vorgaben": "connettori ctf: si può usare una chiodatrice qualsiasi?",
    r"como.*instala.*ctf|herramientas.*l[ií]mites": "connettori ctf: si può usare una chiodatrice qualsiasi?",
    # P560 catch-all
    r"\bspit\s*p560\b": "connettori ctf: si può usare una chiodatrice qualsiasi?",
    r"\bp560\b": "connettori ctf: si può usare una chiodatrice qualsiasi?",
    r"chiodatrice\s*p560": "connettori ctf: si può usare una chiodatrice qualsiasi?",
    r"mi (parli|spieghi).*(p560|chiodatrice)": "connettori ctf: si può usare una chiodatrice qualsiasi?",
    # CEM-E — resine
    r"\bdo.*ctcem.*(use|using).*resin": "i connettori tecnaria ctcem per solai in laterocemento si posano con resine?",
    r"los conectores.*ctcem.*resinas": "i connettori tecnaria ctcem per solai in laterocemento si posano con resine?",
    r"les connecteurs.*ctcem.*r[eé]sines": "i connettori tecnaria ctcem per solai in laterocemento si posano con resine?",
    r"ctcem.*harz|harze": "i connettori tecnaria ctcem per solai in laterocemento si posano con resine?",
    # CEM-E — famiglie
    r"which connectors.*(hollow|hollow[- ]block).*slab": "quali connettori tecnaria ci sono per solai in laterocemento?",
    r"qu[eé]\s+conectores.*(bovedillas|forjados)": "quali connettori tecnaria ci sono per solai in laterocemento?",
    r"quels connecteurs.*(hourdis|planchers)": "quali connettori tecnaria ci sono per solai in laterocemento?",
    r"welche verbind(er|ungen).*hohlstein(decken)?": "quali connettori tecnaria ci sono per solai in laterocemento?",
    # Guard-rail — CTC
    r"\bare ctc (codes|from) tecnaria": "i ctc sono un codice tecnaria?",
    r"ctc.*c[oó]digo.*tecnaria": "i ctc sono un codice tecnaria?",
    r"ctc.*code.*tecnaria": "i ctc sono un codice tecnaria?",
    r"sind ctc.*tecnaria": "i ctc sono un codice tecnaria?",
}

def normalize_query_to_it(q: str) -> str:
    ql = q.lower().strip()
    # già IT?
    if ("ctf" in ql and "codici" in ql) or \
       ("connettori ctf" in ql) or ("chiodatrice" in ql) or \
       ("ctcem" in ql and "resine" in ql) or ("solai in laterocemento" in ql) or \
       re.search(r"\bi ctc\b|\bctc\b.*tecnaria", ql):
        return ql
    for pat, canon in CANON.items():
        if re.search(pat, ql):
            return canon
    return ql

# ================ APP ================
app = FastAPI(title="Tecnaria BOT", version="3.5")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ================ KB ================
KB: Dict[str, dict] = {}
_meta = {}

def load_kb() -> Tuple[int, dict]:
    global KB, _meta
    try:
        with _lock:
            data = load_json(DATA_PATH)
            if not data:
                return 0, {}
            KB = {item["id"]: item for item in data.get("qa", [])}
            _meta = data.get("meta", {})
            return len(KB), _meta
    except Exception as e:
        print(f"[ERRORE] KB non caricata: {e}")
        return 0, {}

count, _ = load_kb()
print(f"[INIT] Caricate {count} voci KB da {DATA_PATH}")

# ================ SERVICE ================
@app.get("/")
def root():  # redirect comodo alla UI
    return RedirectResponse("/ui", status_code=307)

@app.get("/health")
def health():
    return {"ok": True, "kb_items": len(KB), "langs": list(ALLOWED_LANGS)}

@app.get("/debug-paths")
def debug_paths():
    return {
        "DATA_PATH": DATA_PATH,
        "DATA_PATH_type": "file" if os.path.isfile(DATA_PATH) else "missing",
        "I18N_DIR": I18N_DIR,
        "I18N_DIR_type": "dir" if os.path.isdir(I18N_DIR) else "missing",
        "I18N_CACHE_DIR": I18N_CACHE_DIR,
        "I18N_CACHE_DIR_type": "dir" if os.path.isdir(I18N_CACHE_DIR) else "missing",
        "ALLOWED_LANGS": list(ALLOWED_LANGS),
    }

@app.post("/reload-kb")
def reload_kb():
    n, _ = load_kb()
    return {"ok": True, "kb_items": n}

@app.get("/kb/ids")
def kb_ids():
    return list(KB.keys())

@app.get("/kb/item")
def kb_item(id: str):
    if id in KB:
        return KB[id]
    return JSONResponse({"error": "ID non trovato"}, status_code=404)

@app.get("/kb/search")
def kb_search(q: str = "", k: int = 10):
    ql = q.lower().strip()
    if not ql:
        return {"ok": True, "count": len(KB), "items": []}
    matches = []
    for item in KB.values():
        if ql in item["q"].lower() or ql in item["a"].lower():
            matches.append(item)
        if len(matches) >= k:
            break
    return {"ok": True, "count": len(matches), "items": matches}

# ================ Q&A ================
@app.post("/api/ask")
async def api_ask(req: Request):
    try:
        body = await req.json()
        q_raw = body.get("q", "")
        if not q_raw or not q_raw.strip():
            return JSONResponse({"error": "Domanda vuota"}, status_code=400)

        q_it = normalize_query_to_it(q_raw)

        for item in KB.values():
            if q_it in item["q"].lower():
                html = f"""
                <div class="card" style="border:1px solid #30343a;border-radius:14px;padding:16px;background:#111;border-color:#2b2f36">
                    <h2 style="margin:0 0 10px 0;font-size:18px;color:#ff7a00;">Risposta Tecnaria</h2>
                    <p style="margin:0 0 8px 0;line-height:1.6;color:#f5f7fa;">{item["a"]}</p>
                    <p style="margin:8px 0 0 0;color:#a6adbb;font-size:12px;">⏱ {int(time.time() % 1000)} ms</p>
                </div>
                """
                return {"ok": True, "html": html}

        return {
            "ok": True,
            "html": """
            <div class="card" style="border:1px solid #30343a;border-radius:14px;padding:16px;background:#111;border-color:#2b2f36">
                <h2 style="margin:0 0 10px 0;font-size:18px;color:#ff7a00;">Risposta Tecnaria</h2>
                <p style="margin:0 0 8px 0;line-height:1.6;color:#f5f7fa;">Non ho trovato elementi sufficienti su domini autorizzati o nelle regole. Raffina la domanda o aggiorna le regole.</p>
                <p style="margin:8px 0 0 0;color:#a6adbb;font-size:12px;">⏱ 0 ms</p>
            </div>
            """
        }
    except Exception as e:
        print("[ERRORE /api/ask]", e)
        return JSONResponse({"error": str(e)}, status_code=500)

# ================ UI ================
@app.get("/ui", response_class=HTMLResponse)
def ui():
    return """
<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Tecnaria Sinapsi — BOT</title>
  <style>
    :root{
      --bg:#0f0f10; --card:#191a1c; --brand:#ff7a00; --text:#f5f7fa;
      --muted:#a6adbb; --line:#2b2f36;
    }
    *{box-sizing:border-box}
    body{margin:0;background:var(--bg);color:var(--text);
         font-family: system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial}
    header{padding:32px 20px;background:linear-gradient(180deg,#1b1c1f 0%,#111214 80%);
           border-bottom:1px solid var(--line)}
    .wrap{max-width:1100px;margin:0 auto;padding:0 16px}
    .brand{display:flex;align-items:center;gap:12px;margin-bottom:16px}
    .logo{width:36px;height:36px;border-radius:10px;background:var(--brand);color:#000;
          display:grid;place-items:center;font-weight:900;box-shadow:0 6px 20px rgba(255,122,0,.35)}
    h1{margin:0;font-size:28px}
    .tagline{color:var(--muted);margin-top:4px;font-size:14px}
    .row{display:flex;gap:10px;margin-top:16px}
    input{flex:1;padding:16px 14px;font-size:16px;border-radius:12px;border:1px solid var(--line);
          background:#0c0d0f;color:var(--text);outline:none}
    button{padding:0 18px;height:48px;border:none;border-radius:12px;background:var(--brand);
           color:#000;font-weight:800;cursor:pointer;box-shadow:0 10px 24px rgba(255,122,0,.35)}
    main{padding:28px 20px}
    .chips{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 18px}
    .chip{background:#121316;border:1px solid var(--line);color:var(--muted);
          padding:8px 12px;border-radius:999px;cursor:pointer;font-size:13px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;
          box-shadow:0 10px 32px rgba(0,0,0,.35)}
    .title{font-size:18px;font-weight:800;margin:0 0 6px}
    .latency{color:var(--muted);font-size:12px;float:right}
    .answer{line-height:1.6;font-size:16px;white-space:pre-wrap}
    .hint{color:var(--muted);font-size:12px;margin-top:10px}
    .footer{color:#6b7280;font-size:12px;margin-top:18px}
    @media (max-width:720px){.row{flex-direction:column}button{height:44px}}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <div class="brand">
        <div class="logo">T</div>
        <div>
          <h1>Tecnaria Sinapsi — BOT</h1>
          <div class="tagline">CTF, CEM-E (CTCEM/VCEM), SPIT P560, CTL, GTS • IT/EN/ES/FR/DE</div>
        </div>
      </div>
      <div class="row">
        <input id="q" placeholder="Scrivi una domanda… es: Mi puoi dire i codici dei CTF? • Mi spieghi la chiodatrice P560?" />
        <button onclick="ask()">Chiedi a Sinapsi</button>
      </div>
      <div class="chips">
        <div class="chip" onclick="preset('Mi puoi dire i codici dei CTF?')">CTF • Codici</div>
        <div class="chip" onclick="preset('Connettori CTF: si può usare una chiodatrice qualsiasi?')">CTF • Posa P560</div>
        <div class="chip" onclick="preset('I connettori Tecnaria CTCEM per solai in laterocemento si posano con resine?')">CEM-E • Resine</div>
        <div class="chip" onclick="preset('Quali connettori Tecnaria ci sono per solai in laterocemento?')">CEM-E • Famiglie</div>
        <div class="chip" onclick="preset('I CTC sono un codice Tecnaria?')">Guard-rail • CTC</div>
      </div>
    </div>
  </header>

  <main>
    <div class="wrap">
      <div id="res" class="card">
        <div class="title">Risposta Tecnaria <span id="lat" class="latency"></span></div>
        <div id="html" class="answer">Scrivi una domanda per iniziare.</div>
        <div class="hint">Suggerimento: usa le pillole sopra per esempi pronti. Premere Enter invia la domanda.</div>
      </div>
      <div class="footer">© Tecnaria S.p.A. • Interfaccia dimostrativa Sinapsi</div>
    </div>
  </main>

  <script>
    function preset(text){ document.getElementById('q').value=text; ask(); }
    async function ask(){
      const t0 = performance.now();
      const q = document.getElementById('q').value.trim();
      const box = document.getElementById('html');
      const lat = document.getElementById('lat');
      if(!q){ box.textContent = "Scrivi una domanda."; return; }
      box.textContent = "⏳ Attendi risposta…";
      try{
        const r = await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q})});
        const j = await r.json();
        box.innerHTML = j.html || "❌ Nessuna risposta trovata.";
      }catch(e){ box.textContent = "❌ Errore di rete."; }
      finally{ lat.textContent = Math.max(1,Math.round(performance.now()-t0)) + " ms"; }
    }
    document.getElementById('q').addEventListener('keydown', e=>{ if(e.key==='Enter'){ ask(); }});
  </script>
</body>
</html>
    """
