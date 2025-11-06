# app.py — Tecnaria Sinapsi Q/A (OFFLINE) con UI
# Rotte: / (stato), /ping, /status, /ask (POST), /ui (interfaccia)
# Legge SOLO static/data/tecnaria_gold.json

import json, re, unicodedata
from pathlib import Path
from typing import Dict, Any, List, Tuple, Set
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel

APP_DIR = Path(__file__).parent
DATA_FILE = APP_DIR / "static" / "data" / "tecnaria_gold.json"

app = FastAPI(title="Tecnaria Sinapsi — Q/A (offline)")

# ---------- utils ----------
def normalize(text: str) -> str:
    if not text: return ""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.lower()
    t = re.sub(r"[^a-z0-9àèéìíòóùç\s\-_/]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def tokenize(text: str) -> List[str]:
    return normalize(text).split()

# ---------- loader con cache ----------
_db_cache: Dict[str, Any] = {}
_db_mtime: float = 0.0

def load_db(force: bool=False) -> Dict[str, Any]:
    global _db_cache, _db_mtime
    if not DATA_FILE.exists():
        raise FileNotFoundError(f"File non trovato: {DATA_FILE}")
    mtime = DATA_FILE.stat().st_mtime
    if force or not _db_cache or mtime != _db_mtime:
        raw = DATA_FILE.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON NON VALIDO: {e}")
        if not isinstance(data, dict) or "items" not in data or not isinstance(data["items"], list):
            raise ValueError("JSON valido ma senza chiave 'items' (lista).")
        _db_cache = data
        _db_mtime = mtime
    return _db_cache

# ---------- matcher ----------
FAMILY_HINT_WEIGHT: Dict[str, float] = {
    "CTF":1.1, "CTL":1.1, "CTL MAXI":1.15, "CTCEM":1.1, "VCEM":1.1,
    "P560":1.05, "DIAPASON":1.0, "GTS":1.0, "ACCESSORI":1.0,
    "CONFRONTO":0.9, "PROBLEMATICHE":1.0, "KILLER":1.0, "COMM":0.85
}
FAM_TOKENS = ["ctf","ctl","maxi","ctcem","vcem","p560","diapason","gts","accessori","confronto","problematiche","killer","comm"]

def family_hints_from_text(text_norm: str) -> Set[str]:
    hints: Set[str] = set()
    for fam in FAM_TOKENS:
        if fam in text_norm:
            hints.add(fam.upper() if fam != "maxi" else "CTL MAXI")
    return hints

def score_item(question_norm: str, item: Dict[str,Any], family_hints: Set[str]) -> Tuple[float,int]:
    trig = (item.get("trigger") or {})
    kws = [normalize(k) for k in (trig.get("keywords") or [])]
    peso = float(trig.get("peso", 1.0))
    hits = 0
    q_tokens = tokenize(question_norm)
    for kw in kws:
        if kw and (kw in question_norm or any(tok.startswith(kw) or kw.startswith(tok) for tok in q_tokens)):
            hits += 1
    fam = (item.get("family") or "").upper()
    fam_boost = 1.0
    if fam in FAMILY_HINT_WEIGHT and (fam in family_hints or (fam == "CTL MAXI" and "CTL MAXI" in family_hints)):
        fam_boost = FAMILY_HINT_WEIGHT[fam]
    score = (hits * peso) * fam_boost
    return score, hits

def answer_from_json(question: str) -> Dict[str, Any]:
    db = load_db()
    items = db.get("items", [])
    qn = normalize(question)
    hints = family_hints_from_text(qn)

    best = None; best_score = -1.0; best_hits = 0
    for it in items:
        s, h = score_item(qn, it, hints)
        if s > best_score:
            best_score, best, best_hits = s, it, h

    if best is None or best_score <= 0:
        for fid in ("CTF-0001","COMM-0001"):
            cand = next((it for it in items if it.get("id")==fid), None)
            if cand:
                best, best_score, best_hits = cand, 0.1, 0
                break

    resp_text = (best.get("risposta") or best.get("answer") or "").strip()
    return {
        "answer": resp_text,
        "meta": {
            "best_item": {"id": best.get("id"), "family": best.get("family")},
            "trigger_hits": best_hits,
            "score_internal": best_score
        }
    }

# ---------- I/O ----------
class AskInput(BaseModel):
    question: str

# ---------- ROUTES ----------
@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <!doctype html><meta charset="utf-8">
    <title>Tecnaria — Stato</title>
    <div style="font-family:system-ui;padding:18px">
      <h2>Tecnaria Sinapsi — Q/A (offline)</h2>
      <p>Endpoint: <a href="/ui">/ui</a> · <a href="/status">/status</a> · <a href="/ping">/ping</a></p>
      <pre id="out" style="background:#111;color:#eee;padding:12px;border-radius:10px;"></pre>
    </div>
    <script>fetch('/status').then(r=>r.json()).then(j=>{
      out.textContent = JSON.stringify(j,null,2);
    }).catch(e=>{ out.textContent = 'Errore: '+e; });</script>
    """

@app.get("/ui", response_class=HTMLResponse)
def ui():
    return """
    <!doctype html><meta charset="utf-8">
    <title>Tecnaria Sinapsi — Q/A</title>
    <style>
      body{font-family:system-ui;background:#0b0b0b;color:#eee;margin:0}
      header{background:linear-gradient(90deg,#ff7a00,#111);padding:16px 20px;font-weight:800}
      .wrap{max-width:1100px;margin:18px auto;padding:0 12px}
      .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
      #q{flex:1;min-width:420px;padding:12px;background:#111;border:1px solid #333;border-radius:10px;color:#fff}
      #go{background:#ff7a00;color:#111;border:0;padding:12px 18px;border-radius:10px;font-weight:800;cursor:pointer}
      .pill{display:inline-block;padding:6px 10px;border-radius:999px;background:#222;color:#ddd;margin-right:8px;font-size:12px}
      .ans{white-space:pre-wrap;background:#111;border:1px solid #333;border-radius:12px;padding:12px;margin-top:14px}
      .meta{font-size:12px;color:#aaa;margin-top:6px}
      a{color:#ffb366}
    </style>
    <header>Tekcnaria Sinapsi — Q/A</header>
    <div class="wrap">
      <div class="row">
        <input id="q" placeholder="Scrivi la domanda... es. CTF su lamiera: quanti chiodi?" />
        <button id="go">Invia</button>
        <span id="badge" class="pill">Verifica stato…</span>
      </div>
      <div style="margin-top:8px">
        <span class="pill" onclick="ex('CTF su lamiera: quanti chiodi?')">CTF su lamiera</span>
        <span class="pill" onclick="ex('CTL vs CTL MAXI: differenze?')">CTL vs CTL MAXI</span>
        <span class="pill" onclick="ex('P560 su VCEM: è valido?')">P560 su VCEM</span>
      </div>
      <div id="res" class="ans" style="display:none"></div>
      <div id="meta" class="meta"></div>
    </div>
    <script>
      async function status(){
        try{
          const r = await fetch('/status'); const j = await r.json();
          const b = document.getElementById('badge');
          if(j.ok){ b.textContent = 'PRONTO · items: '+j.items; b.style.background='#1b6'; }
          else { b.textContent = j.message || 'ERRORE'; b.style.background='#c33'; }
        }catch(e){ badge.textContent='ERRORE STATO'; badge.style.background='#c33'; }
      }
      async function ask(q){
        const r = await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q})});
        const j = await r.json();
        const R = document.getElementById('res');
        const M = document.getElementById('meta');
        if(j.ok){
          R.style.display='block';
          R.textContent = j.answer || '(nessuna risposta)';
          const m = j.meta||{};
          M.textContent = `id: ${m.best_item?.id||'-'} · family: ${m.best_item?.family||'-'} · trigger_hits: ${m.trigger_hits} · score: ${m.score_internal}`;
        }else{
          R.style.display='block';
          R.textContent = 'Errore: '+(j.detail||JSON.stringify(j));
          M.textContent = '';
        }
      }
      document.getElementById('go').onclick = ()=>{
        const q = document.getElementById('q').value.trim();
        if(q) ask(q);
      };
      function ex(t){ document.getElementById('q').value=t; }
      status();
    </script>
    """

@app.get("/ping")
def ping():
    return "alive"

@app.get("/status")
def status():
    try:
        db = load_db(False)
        return JSONResponse({"ok": True, "file": str(DATA_FILE), "items": len(db.get("items", [])), "message": "PRONTO"})
    except FileNotFoundError as e:
        return JSONResponse({"ok": False, "file": str(DATA_FILE), "items": 0, "message": "FILE NON TROVATO", "error": str(e)}, status_code=500)
    except ValueError as e:
        return JSONResponse({"ok": False, "file": str(DATA_FILE), "items": 0, "message": "JSON NON VALIDO", "error": str(e)}, status_code=500)
    except Exception as e:
        return JSONResponse({"ok": False, "file": str(DATA_FILE), "items": 0, "message": "ERRORE GENERICO", "error": str(e)}, status_code=500)

class AskInput(BaseModel):
    question: str

@app.post("/ask")
def ask(body: AskInput):
    q = (body.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="question mancante")
    try:
        load_db()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Dataset non disponibile: {e}")
    result = answer_from_json(q)
    return {"ok": True, "question": q, **result}
