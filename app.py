import os, re, json, time, threading
from typing import Dict, Tuple, List, Optional
from pathlib import Path

import numpy as np
from fastapi import FastAPI, Request, Body
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

# ========= CONFIG DI BASE =========
BASE_DIR = os.path.dirname(__file__)
DATA_PATH = os.path.join(BASE_DIR, "static", "data", "SINAPSI_GLOBAL_TECNARIA_EXT.json")
I18N_DIR = os.path.join(BASE_DIR, "static", "i18n")
I18N_CACHE_DIR = os.getenv("I18N_CACHE_DIR", os.path.join(BASE_DIR, "static", "i18n-cache"))
ALLOWED_LANGS = {"it", "en", "fr", "de", "es"}
_lock = threading.Lock()

# ========= UTILS =========
def load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERRORE] Impossibile leggere {path}: {e}")
        return {}

def tokenize(s: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", s.lower())

def jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)

# ========= NORMALIZZAZIONE / ROUTER LEGGERO (facoltativo) =========
CANON = {
    # CTF codici
    r"\b(can you (tell|list)|what are)\b.*\bctf\b.*\bcodes?\b": "mi puoi dire i codici dei ctf?",
    r"\bpued(es|e)\b.*c[oó]digos?.*\bctf\b": "mi puoi dire i codici dei ctf?",
    r"\bpeux[- ]tu\b.*codes?.*\bctf\b": "mi puoi dire i codici dei ctf?",
    r"\bkannst du\b.*\bctf\b.*codes?": "mi puoi dire i codici dei ctf?",
    r"\bcodici.*ctf\b": "mi puoi dire i codici dei ctf?",
    r"\bsku.*ctf\b": "mi puoi dire i codici dei ctf?",
    # CTF posa / P560
    r"\bspit\s*p560\b": "connettori ctf: si può usare una chiodatrice qualsiasi?",
    r"\bp560\b": "connettori ctf: si può usare una chiodatrice qualsiasi?",
    r"\bchiodatrice\b": "connettori ctf: si può usare una chiodatrice qualsiasi?",
    r"\bgun\b.*(nail|pin)|\bnailer\b": "connettori ctf: si può usare una chiodatrice qualsiasi?",
    # CEM-E resine
    r"\bdo.*ctcem.*(use|using).*resins?\b": "i connettori tecnaria ctcem per solai in laterocemento si posano con resine?",
    r"\blos conectores\b.*ctcem.*resinas": "i connettori tecnaria ctcem per solai in laterocemento si posano con resine?",
    r"\bles connecteurs\b.*ctcem.*r[eé]sines": "i connettori tecnaria ctcem per solai in laterocemento si posano con resine?",
    r"\bctcem\b.*harz|harze": "i connettori tecnaria ctcem per solai in laterocemento si posano con resine?",
    r"\bvcem\b.*(resine|resins?|r[eé]sines|harz|harze)": "i connettori tecnaria ctcem per solai in laterocemento si posano con resine?",
    # CEM-E famiglie
    r"which connectors.*(hollow|hollow[- ]block).*slab": "quali connettori tecnaria ci sono per solai in laterocemento?",
    r"qu[eé]\s+conectores.*(bovedillas|forjados)": "quali connettori tecnaria ci sono per solai in laterocemento?",
    r"quels connecteurs.*(hourdis|planchers)": "quali connettori tecnaria ci sono per solai in laterocemento?",
    r"welche verbind(er|ungen).*hohlstein(decken)?": "quali connettori tecnaria ci sono per solai in laterocemento?",
    r"\b(laterocemento|hourdis|bovedillas|hollow)\b": "quali connettori tecnaria ci sono per solai in laterocemento?",
    # Guard-rail CTC non Tecnaria
    r"\bare ctc (codes|from) tecnaria": "i ctc sono un codice tecnaria?",
    r"\bctc\b.*c[oó]digo.*tecnaria": "i ctc sono un codice tecnaria?",
    r"\bctc\b.*codes?.*tecnaria": "i ctc sono un codice tecnaria?",
    r"\bsind\b.*\bctc\b.*tecnaria": "i ctc sono un codice tecnaria?",
}

def normalize_query_to_it(q: str) -> str:
    ql = q.lower().strip()
    for pat, canon in CANON.items():
        if re.search(pat, ql):
            return canon
    return ql

# ========= APP =========
app = FastAPI(title="Tecnaria BOT", version="4.0 (semantic)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ========= KB =========
KB: Dict[str, dict] = {}
_meta = {}
_keywords_bonus = [
    ("ctf", 0.15), ("p560", 0.20), ("spit", 0.10), ("hsbr14", 0.10),
    ("ctcem", 0.20), ("vcem", 0.20), ("laterocemento", 0.15),
    ("ctl", 0.10), ("diapason", 0.10), ("gts", 0.10),
    ("sku", 0.10), ("codici", 0.10), ("resine", 0.10), ("chiodatrice", 0.12)
]

def load_kb_only() -> Tuple[int, dict]:
    global KB, _meta
    try:
        with _lock:
            data = load_json(DATA_PATH)
            if not data:
                KB, _meta = {}, {}
                return 0, {}
            KB = {item["id"]: item for item in data.get("qa", [])}
            _meta = data.get("meta", {})
            return len(KB), _meta
    except Exception as e:
        print(f"[ERRORE] KB non caricata: {e}")
        KB, _meta = {}, {}
        return 0, {}

# ========= SEMANTICA =========
from sentence_transformers import SentenceTransformer
SEM_MODEL_NAME = os.getenv("SEM_MODEL_NAME", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
SEM_THRESHOLD = float(os.getenv("SEM_THRESHOLD", "0.40"))  # 0.35–0.45
_sem_model: Optional[SentenceTransformer] = None
_sem_matrix: Optional[np.ndarray] = None
_sem_ids: List[str] = []

def _semantic_corpus_from_item(item: Dict) -> str:
    q = item.get("q", "")
    # se nel tuo JSON non hai 'aliases', nessun problema:
    aliases = item.get("aliases", [])
    a = item.get("a", "")
    a_short = a.strip().replace("\n", " ")
    if len(a_short) > 400:
        a_short = a_short[:400] + "…"
    parts = [q] + aliases + [a_short]
    return " | ".join([p for p in parts if p])

def build_semantic_index() -> None:
    global _sem_model, _sem_matrix, _sem_ids
    if _sem_model is None:
        _sem_model = SentenceTransformer(SEM_MODEL_NAME)

    corpus_texts, sem_ids = [], []
    for _id, item in KB.items():
        doc = _semantic_corpus_from_item(item)
        if not doc:
            continue
        corpus_texts.append(doc)
        sem_ids.append(_id)

    if not corpus_texts:
        _sem_matrix = None
        _sem_ids = []
        return

    embeddings = _sem_model.encode(corpus_texts, normalize_embeddings=True, convert_to_numpy=True)
    _sem_matrix = embeddings.astype(np.float32)
    _sem_ids = sem_ids
    print(f"[SEM] Indice costruito: {len(_sem_ids)} voci, modello={SEM_MODEL_NAME}")

def semantic_search(query: str) -> Tuple[Optional[str], float]:
    if not query or _sem_model is None or _sem_matrix is None or _sem_matrix.size == 0:
        return None, 0.0
    qv = _sem_model.encode([query], normalize_embeddings=True, convert_to_numpy=True)[0].astype(np.float32)
    scores = _sem_matrix @ qv  # cosine (vettori normalizzati)
    top_idx = int(np.argmax(scores))
    best_score = float(scores[top_idx])
    best_id = _sem_ids[top_idx]
    if best_score >= SEM_THRESHOLD:
        return best_id, best_score
    return None, best_score

def load_kb_and_indexes() -> int:
    n, _ = load_kb_only()
    build_semantic_index()
    return n

n_init = load_kb_and_indexes()
print(f"[INIT] Caricate {n_init} voci KB da {DATA_PATH}")

# ========= MATCHING LEGGERO (backup) =========
def best_match_item_fuzzy(q_it: str) -> dict:
    if not KB:
        return {}
    tokens_q = tokenize(q_it)
    best = (0.0, None)
    for item in KB.values():
        tq = tokenize(item["q"])
        ta = tokenize(item["a"])
        score = 0.0
        ql = q_it.lower()
        # substring boost
        if ql in item["q"].lower():
            score += 0.60
        if ql in item["a"].lower():
            score += 0.40
        # jaccard
        score += 0.50 * jaccard(tokens_q, tq)
        score += 0.30 * jaccard(tokens_q, ta)
        # bonus keyword
        for kw, bonus in _keywords_bonus:
            if kw in ql and (kw in item["q"].lower() or kw in item["a"].lower()):
                score += bonus
        # guard-rail ctc
        if re.search(r"\bctc\b", ql) and item["id"].lower().startswith("ctc-"):
            score += 0.25
        if score > best[0]:
            best = (score, item)
    if best[0] >= 0.20:
        return best[1]
    return {}

# ========= SERVICE =========
@app.get("/")
def root():
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
    n = load_kb_and_indexes()
    return {"ok": True, "kb_items": n}

@app.get("/kb/ids")
def kb_ids():
    return list(KB.keys())

@app.get("/kb/item")
def kb_item(id: str):
    return KB.get(id) or JSONResponse({"error": "ID non trovato"}, status_code=404)

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

# ========= HTML CARD =========
def render_card(body_html: str, ms: int) -> str:
    return f"""
    <div class="card" style="border:1px solid #30343a;border-radius:14px;padding:16px;background:#111;border-color:#2b2f36">
        <h2 style="margin:0 0 10px 0;font-size:18px;color:#ff7a00;">Risposta Tecnaria</h2>
        <p style="margin:0 0 8px 0;line-height:1.6;color:#f5f7fa;">{body_html}</p>
        <p style="margin:8px 0 0 0;color:#a6adbb;font-size:12px;">⏱ {ms} ms</p>
    </div>
    """

# ========= Q&A =========
@app.post("/api/ask")
async def api_ask(req: Request):
    t0 = time.time()
    try:
        body = await req.json()
        q_raw = body.get("q", "")
        if not q_raw or not q_raw.strip():
            return JSONResponse({"ok": True, "html": render_card("Scrivi una domanda.", 0)})

        # 1) Router leggero (priorità) — opzionale, resta utile per evitare collisioni P560/posa CTF
        q_it = normalize_query_to_it(q_raw)
        # 2) Ricerca semantica multilingua
        best_id, score = semantic_search(q_it)
        if best_id and best_id in KB:
            ms = max(1, int((time.time() - t0) * 1000))
            return {"ok": True, "html": render_card(KB[best_id]["a"], ms)}

        # 3) Backup fuzzy (aiuta su stringhe molto corte o “rumorose”)
        item = best_match_item_fuzzy(q_it)
        if item:
            ms = max(1, int((time.time() - t0) * 1000))
            return {"ok": True, "html": render_card(item["a"], ms)}

        # 4) Fallback gentile (niente web)
        ms = max(1, int((time.time() - t0) * 1000))
        msg = ("Non riconosco questa formulazione. Prova a chiedere, ad esempio: "
               "“Mi parli della P560?”, “Mi puoi parlare dei connettori CTF?”, "
               "oppure usa i pulsanti rapidi. Se vuoi, mandami la frase e la aggiungo al KB.")
        return {"ok": True, "html": render_card(msg, ms)}

    except Exception as e:
        print("[ERRORE /api/ask]", e)
        return JSONResponse({"error": str(e)}, status_code=500)

# ========= UI =========
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
    :root{ --bg:#0f0f10; --card:#191a1c; --brand:#ff7a00; --text:#f5f7fa; --muted:#a6adbb; --line:#2b2f36; }
    *{box-sizing:border-box}
    body{margin:0;background:var(--bg);color:var(--text);font-family: system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial}
    header{padding:32px 20px;background:linear-gradient(180deg,#1b1c1f 0%,#111214 80%);border-bottom:1px solid var(--line)}
    .wrap{max-width:1100px;margin:0 auto;padding:0 16px}
    .brand{display:flex;align-items:center;gap:12px;margin-bottom:16px}
    .logo{width:36px;height:36px;border-radius:10px;background:var(--brand);color:#000;display:grid;place-items:center;font-weight:900;box-shadow:0 6px 20px rgba(255,122,0,.35)}
    h1{margin:0;font-size:28px}
    .tagline{color:var(--muted);margin-top:4px;font-size:14px}
    .row{display:flex;gap:10px;margin-top:16px}
    input{flex:1;padding:16px 14px;font-size:16px;border-radius:12px;border:1px solid var(--line);background:#0c0d0f;color:#f5f7fa;outline:none}
    button{padding:0 18px;height:48px;border:none;border-radius:12px;background:var(--brand);color:#000;font-weight:800;cursor:pointer;box-shadow:0 10px 24px rgba(255,122,0,.35)}
    main{padding:28px 20px}
    .chips{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 18px}
    .chip{background:#121316;border:1px solid var(--line);color:#a6adbb;padding:8px 12px;border-radius:999px;cursor:pointer;font-size:13px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;box-shadow:0 10px 32px rgba(0,0,0,.35)}
    .title{font-size:18px;font-weight:800;margin:0 0 6px}
    .latency{color:#a6adbb;font-size:12px;float:right}
    .answer{line-height:1.6;font-size:16px;white-space:pre-wrap}
    .hint{color:#a6adbb;font-size:12px;margin-top:10px}
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
        <input id="q" placeholder="Scrivi una domanda… es: La P560 come lavora? • Mi puoi parlare dei connettori CTF?" />
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
        <div class="hint">Suggerimento: usa le pillole sopra per esempi pronti. Premi Enter per inviare.</div>
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
