# -*- coding: utf-8 -*-
import json, re
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel

# ====== CONFIG ======
BASE_PATH = Path("static/data")
ROUTER_FILE = BASE_PATH / "tecnaria_router_index.json"
CONTACTS_FILE = BASE_PATH / "contatti.json"
BANK_FILE = BASE_PATH / "bancari.json"

FAMILIES = [
    "ctf",
    "gts",
    "diapason",
    "ctl",
    "mini-cem-e",
    "spit-p560",
]

# Fallback router (se manca/è rotto il file JSON)
FALLBACK_ROUTER = {
    "ctf": ["ctf", "connettore", "collaborante", "solaio", "acciaio calcestruzzo"],
    "gts": ["gts", "manicotto", "giunzione", "spine", "tiranti"],
    "diapason": ["diapason", "soletta leggera", "connessione legno", "piastra"],
    "ctl": ["ctl", "vite", "vite strutturale", "tavolato", "tetto"],
    "mini-cem-e": ["mini-cem-e", "minicem", "camicia", "consolidamento", "iniezione"],
    "spit-p560": ["p560", "spit p560", "chiodatrice", "sparachiodi", "propulsori"],
}

# ====== IO sicuro ======
def load_json(path: Path) -> Any:
    if not path or not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        # NON crashare: segnala quale file e il motivo
        return {"__error__": f"{path}: {e.__class__.__name__}: {e}"}

def extract_qa(payload: Any) -> List[Dict[str, Any]]:
    # Se load_json ha segnalato errore
    if isinstance(payload, dict) and "__error__" in payload:
        return []
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("qa"), list):
        return payload["qa"]
    # tollera strutture alternative
    acc = []
    for key in ("items", "dataset", "data", "entries"):
        arr = payload.get(key)
        if isinstance(arr, list):
            for it in arr:
                if isinstance(it, dict) and isinstance(it.get("qa"), list):
                    acc.extend(it["qa"])
    return acc

# ====== Normalizzazione testo + router ======
WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", re.UNICODE)
def norm(text: str) -> str:
    return " ".join(w.lower() for w in WORD_RE.findall(text or ""))

def load_router() -> Dict[str, List[str]]:
    data = load_json(ROUTER_FILE)
    if isinstance(data, dict) and data and "__error__" not in data:
        # atteso formato {"families":[{"code":"ctf","keywords":[...]}, ...]}
        fams = {}
        if "families" in data and isinstance(data["families"], list):
            for it in data["families"]:
                code = (it.get("code") or "").lower()
                kws = [k.lower() for k in it.get("keywords", []) if isinstance(k, str)]
                if code and kws:
                    fams[code] = kws
        # fallback se il file non è nel formato atteso
        return fams or FALLBACK_ROUTER
    return FALLBACK_ROUTER

ROUTER = load_router()

def route_family(q: str) -> Optional[str]:
    t = norm(q)
    # match router json
    for code, kws in ROUTER.items():
        if any(k in t for k in kws):
            return code
    # fallback euristico minimo
    for code in FAMILIES:
        if code.replace("-", "") in t.replace("-", ""):
            return code
    # domande generiche “parlami …”
    if "parlami" in t or "cos'e" in t or "cos è" in t or "che cos" in t:
        # preferisci overview di famiglie “note”
        return None
    return None

# Candidati di filename per una famiglia (tollerante)
def dataset_candidates_for_code(code: Optional[str]) -> List[Path]:
    if not code:
        return []
    names = []
    # forma canonica
    names.append(f"tecnaria_{code}_qa500.json")
    # varianti comuni
    code_u = code.replace("-", "_")
    names += [
        f"tecnaria_{code_u}_qa500.json",
        f"{code}_qa500.json",
        f"{code_u}_qa500.json",
        f"tecnaria{code_u}_qa500.json",
        f"tecnaria_{code.replace('_','-')}_qa500.json",
    ]
    # special cases pregressi
    if code == "mini-cem-e":
        names += ["tecnaria_minicemE_qa500.json", "tecnaria_miniceme_qa500.json"]
    return [BASE_PATH / n for n in names]

def load_family_dataset(code: Optional[str]):
    for p in dataset_candidates_for_code(code):
        if p.exists():
            raw = load_json(p)
            qa = extract_qa(raw)
            return qa, p, (raw.get("__error__") if isinstance(raw, dict) else None)
    return [], None, None

# ====== Ranker semplice ma efficace ======
def score_item(q: str, item: Dict[str, Any]) -> float:
    tq = set(norm(q).split())
    iq = set(norm(item.get("q", "")).split())
    ia = set(norm(item.get("a", "")).split())
    # Jaccard con priorità alla domanda
    overlap = len(tq & (iq | ia)) / (len(tq | iq | ia) or 1)
    bonus = 0.0
    cat = (item.get("category") or "").lower()
    tags = [t.lower() for t in item.get("tags", []) if isinstance(t, str)]
    if cat in ("prodotto_base", "overview"):
        bonus += 0.15
    if any(t in ("overview", "alias") for t in tags):
        bonus += 0.15
    if "parlami" in norm(q):
        if cat == "prodotto_base" or "overview" in tags:
            bonus += 0.2
    return overlap + bonus

def semantic_pick(q: str, qa: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not qa:
        return None
    # prima prova: categoria/overview
    candidates = sorted(qa, key=lambda it: score_item(q, it), reverse=True)
    return candidates[0] if candidates else None

# ====== APP ======
app = FastAPI(title="Tecnaria Sinapsi", version="3.0")

@app.get("/health")
def health():
    # verifica router + existence dei dataset, senza crash
    datasets = {}
    for code in FAMILIES:
        paths = [str(p) for p in dataset_candidates_for_code(code)]
        existing = [str(p) for p in dataset_candidates_for_code(code) if p.exists()]
        raw = load_json(Path(existing[0])) if existing else {}
        err = raw.get("__error__") if isinstance(raw, dict) else None
        qa = extract_qa(raw)
        datasets[code] = {
            "used_path": existing[0] if existing else None,
            "qa_count": len(qa),
            "json_error": err,
        }
    return {
        "status": "ok",
        "router_loaded": ROUTER is not None,
        "datasets": datasets,
        "contacts": Path(CONTACTS_FILE).exists(),
        "bank": Path(BANK_FILE).exists(),
        "endpoints": {"ui": "/ui", "ask": "/ask?q=...", "debug": "/debug?q=...", "selfcheck": "/selfcheck"}
    }

@app.get("/selfcheck")
def selfcheck():
    report = []
    for code in FAMILIES:
        qa, used, err = load_family_dataset(code)
        report.append({
            "family": code,
            "used_path": str(used) if used else None,
            "qa_count": len(qa),
            "json_error": err
        })
    return {"families": report}

@app.get("/debug")
def debug(q: str = Query(..., description="Domanda per il debug")):
    fam = route_family(q)
    candidates = [str(p) for p in dataset_candidates_for_code(fam)] if fam else []
    existing = [c for c in candidates if Path(c).exists()]
    used = existing[0] if existing else None
    raw = load_json(Path(used)) if used else {}
    err = raw.get("__error__") if isinstance(raw, dict) else None
    qa = extract_qa(raw)
    hit = semantic_pick(q, qa) if qa else None
    return {
        "query": q,
        "family": fam,
        "used_path": used,
        "qa_count": len(qa),
        "candidates": candidates[:8],
        "existing": existing[:8],
        "json_error": err,
        "hit_q": (hit or {}).get("q"),
        "preview_a": ((hit or {}).get("a","")[:220] + ("…" if (hit and len(hit.get('a',""))>220) else "")) if hit else None
    }

@app.get("/ask")
def ask(q: str):
    fam = route_family(q)
    qa, used, err = load_family_dataset(fam)
    if err:
        return {"answer": f"Dataset non disponibile per {fam}. Errore file: {err}"}
    if not qa:
        return {"answer": f"Nessuna base dati per {fam} (file: {used})."}
    hit = semantic_pick(q, qa)
    if not hit:
        return {"answer": "Non trovo una risposta precisa nei dati. Consulta le schede ufficiali Tecnaria."}
    return {"answer": hit.get("a", "").strip()}

@app.get("/company")
def company():
    contacts = load_json(CONTACTS_FILE)
    bank = load_json(BANK_FILE)
    if isinstance(bank, dict) and "__error__" in bank:
        bank = {}
    return {"contacts": contacts, "bank": bank if isinstance(bank, dict) else {}}

# ====== UI minimal (nera/arancione) ======
UI_HTML = """<!doctype html>
<html lang="it"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Tecnaria Sinapsi</title>
<link rel="icon" href="data:,">
<style>
:root { --orange:#f26522; --black:#111; --ink:#222; --muted:#666; --bg:#fafafa; }
body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,sans-serif;background:linear-gradient(180deg,#111 0%,#111 40%,#f26522 100%);}
.container{max-width:1100px;margin:0 auto;padding:24px;}
.header{display:flex;align-items:center;gap:12px;color:#fff;}
.logo{width:34px;height:34px;background:#000;border:2px solid #fff;border-radius:6px;display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;}
.h1{font-size:36px;font-weight:800;color:#fff;margin:12px 0 6px;}
.sub{color:#ffd;opacity:.9;margin-bottom:18px}
.card{background:#fff;border-radius:16px;box-shadow:0 8px 24px rgba(0,0,0,.15);padding:18px;}
.row{display:grid;grid-template-columns:1fr auto;gap:12px;margin:16px 0}
.input{width:100%;padding:14px 16px;border-radius:12px;border:1px solid #ddd;font-size:16px}
.btn{background:#000;color:#fff;border:0;border-radius:12px;padding:14px 18px;font-weight:700;cursor:pointer}
.btn:hover{opacity:.9}
.badges{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px}
.badge{background:rgba(255,255,255,.25);border:1px solid rgba(255,255,255,.4);color:#fff;border-radius:999px;padding:6px 10px;font-size:12px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}
.small{color:#777;font-size:13px}
.kv{margin:2px 0}
.answer{white-space:pre-wrap;line-height:1.45}
.footer{color:#eee;padding:24px 0;text-align:center;font-size:12px}
.section{margin-top:14px}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="logo">T</div>
    <div>
      <div class="h1">Tecnaria Sinapsi</div>
      <div class="sub">Risposte tecniche. Voce ufficiale Tecnaria.</div>
    </div>
  </div>

  <div class="card">
    <div class="row">
      <input id="q" class="input" placeholder="Scrivi qui la tua domanda (es. “Parlami del manicotto GTS”)"/>
      <button id="ask" class="btn">Chiedi a Sinapsi</button>
    </div>
    <div class="badges">
      <div class="badge">CTF</div><div class="badge">GTS</div><div class="badge">Diapason</div>
      <div class="badge">CTL</div><div class="badge">Mini-Cem-E</div><div class="badge">SPIT P560</div>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <div class="section"><b>Risposta</b></div>
      <div id="out" class="answer small">—</div>
    </div>
    <div class="card">
      <div class="section"><b>Impostazioni</b></div>
      <div class="kv small">Base URL: <code id="base"></code></div>
      <div class="kv small" id="health">Stato: …</div>
      <div class="section"><b>Contatti & Dati aziendali</b></div>
      <div id="contacts" class="small">—</div>
    </div>
  </div>

  <div class="footer">© Tecnaria S.p.A. — Bassano del Grappa (VI)</div>
</div>
<script>
const BASE = location.origin;
document.getElementById('base').textContent = BASE;
async function ping(){
  try{
    const r = await fetch(BASE + "/health");
    const j = await r.json();
    document.getElementById('health').textContent = "Router: " + j.router_loaded + " | CTF:" + j.datasets.ctf.qa_count + " | GTS:" + j.datasets["gts"].qa_count;
    const c = await fetch(BASE + "/company"); const cj = await c.json();
    const ct = cj.contacts || {};
    document.getElementById('contacts').textContent = (ct.ragione_sociale||"") + " — " + (ct.indirizzo||"") + " — " + (ct.email||"");
  }catch(e){ document.getElementById('health').textContent="KO: "+e; }
}
async function ask(){
  const q = document.getElementById('q').value.trim();
  if(!q) return;
  const t0 = performance.now();
  const r = await fetch(BASE + "/ask?q=" + encodeURIComponent(q));
  const j = await r.json();
  const ms = Math.round(performance.now()-t0);
  document.getElementById('out').textContent = j.answer + "\\n\\n⏱ " + ms + " ms";
}
document.getElementById('ask').onclick = ask;
document.getElementById('q').addEventListener('keydown',e=>{if(e.key==='Enter') ask();});
ping();
</script>
</body></html>
"""

@app.get("/ui")
def ui():
    return HTMLResponse(UI_HTML)
