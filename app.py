import json
import re
from pathlib import Path
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Tecnaria GOLD — Sinapsi")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_PATH = Path("static/data/tecnaria_gold.json")


# ----------------- UTILITY PULIZIA -----------------
def _strip_json_comments(txt: str) -> str:
    txt = re.sub(r"/\*.*?\*/", "", txt, flags=re.DOTALL)
    txt = re.sub(r"//.*?$", "", txt, flags=re.MULTILINE)
    return txt.strip()


def _split_multi_json(txt: str):
    parts = []
    buf = ""
    depth = 0
    in_str = False
    esc = False
    for ch in txt:
        buf += ch
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    parts.append(buf.strip())
                    buf = ""
    if buf.strip():
        parts.append(buf.strip())
    return parts


def _force_items_merge(json_objs):
    if not json_objs:
        return {"_meta": {}, "items": []}
    base = json_objs[0]
    if not isinstance(base, dict):
        base = {"_meta": {}, "items": base if isinstance(base, list) else [base]}
    items = base.get("items", [])
    if not isinstance(items, list):
        items = []
    for extra in json_objs[1:]:
        if isinstance(extra, list):
            items.extend(extra)
        elif isinstance(extra, dict):
            items.append(extra)
    base["items"] = items
    return base


def load_dataset_safe(path: Path):
    # 1) se non esiste → dataset minimo
    if not path.exists():
        return {
            "_meta": {
                "version": "FALLBACK",
                "note": "file tecnaria_gold.json non trovato, uso dataset interno"
            },
            "items": [
                {
                    "id": "COMM-0001",
                    "family": "COMM",
                    "domanda": "Dove si trova Tecnaria?",
                    "risposta": "Tecnaria S.p.A. — Viale Pecori Giraldi, 55 – 36061 Bassano del Grappa (VI). Tel. +39 0424 502029.",
                    "trigger": {"peso": 1.0, "keywords": ["dove si trova", "sede tecnaria", "bassano"]}
                },
                {
                    "id": "VCEM-0001",
                    "family": "VCEM",
                    "domanda": "posso usare la P560 sui VCEM?",
                    "risposta": "No. La SPIT P560 è solo per i CTF su acciaio con chiodi HSBR14. I VCEM si fissano per foratura + avvitatura meccanica. Usare la P560 sui VCEM fa perdere conformità.",
                    "trigger": {"peso": 1.0, "keywords": ["p560 su vcem", "ho sparato sui vcem", "vcem laterocemento"]}
                }
            ]
        }

    raw_txt = path.read_text(encoding="utf-8")
    cleaned = _strip_json_comments(raw_txt)

    # 2) provo a caricare direttamente
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # 3) allora è il tuo caso: coda aggiunta
        pass

    parts = _split_multi_json(cleaned)
    objs = []
    for p in parts:
        try:
            objs.append(json.loads(p))
        except json.JSONDecodeError:
            continue

    return _force_items_merge(objs)


DATA = load_dataset_safe(DATA_PATH)


# ----------------- MATCHER -----------------
class AskIn(BaseModel):
    question: str


class AskOut(BaseModel):
    answer: str
    source_id: str | None = None
    matched: float | None = None


def score(q: str, item: dict) -> float:
    ql = q.lower()
    trig = item.get("trigger") or {}
    peso = float(trig.get("peso", 0.5))
    kws = trig.get("keywords") or []
    hit = 0
    for k in kws:
        if k and k.lower() in ql:
            hit += 1
    fam = (item.get("family") or "").lower()
    fam_bonus = 0.05 if fam and fam in ql else 0.0
    return peso + hit * 0.12 + fam_bonus


def find_best(q: str):
    items = DATA.get("items", [])
    if not items:
        return ("Dataset Tecnaria GOLD vuoto.", None, 0.0)
    best = None
    best_s = -1.0
    for it in items:
        s = score(q, it)
        if s > best_s:
            best_s = s
            best = it
    if not best:
        return ("Nessuna risposta trovata.", None, 0.0)
    return (best.get("risposta", "…"), best.get("id"), best_s)


# ----------------- UI (INTERFACCIA) -----------------
HTML_UI = """<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <title>Tecnaria Sinapsi — Q/A</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body { margin:0; font-family:system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:#0b1020; color:#fff; height:100vh; display:flex; flex-direction:column; }
    header { background:linear-gradient(90deg, #ff7a00 0%, #000 65%); padding:14px 20px; }
    header h1 { margin:0; font-size:1.1rem; }
    header small { opacity:.7; font-size:.65rem; }
    main { flex:1; display:flex; }
    .left { width:38%; min-width:310px; border-right:1px solid rgba(255,255,255,.03); padding:16px; background:rgba(10,12,22,.5); }
    .right { flex:1; background:#f5f5f5; color:#0f172a; display:flex; flex-direction:column; }
    textarea { width:100%; min-height:140px; background:rgba(0,0,0,.3); border:1px solid rgba(255,255,255,.15); border-radius:10px; padding:10px; color:#fff; }
    button { background:#ff7a00; color:#fff; border:none; border-radius:999px; padding:9px 16px; margin-top:10px; cursor:pointer; }
    .right-header { padding:12px 18px; border-bottom:1px solid rgba(0,0,0,.05); display:flex; justify-content:space-between; align-items:center; }
    .answer { padding:14px 18px; white-space:pre-wrap; }
    .meta { font-size:.7rem; opacity:.6; padding:0 18px 12px; }
    .chip { display:inline-block; background:rgba(255,122,0,.12); border:1px solid rgba(255,122,0,.6); border-radius:999px; padding:4px 10px; font-size:.65rem; margin:5px 5px 0 0; cursor:pointer; }
  </style>
</head>
<body>
  <header>
    <h1>Tecnaria Sinapsi — Q/A</h1>
    <small>Dataset: tecnaria_gold · Motore: Sinapsi + Camilla + NLM · Endpoint: /ask</small>
  </header>
  <main>
    <div class="left">
      <p style="font-size:.7rem;opacity:.7;margin-bottom:6px;">Chiedi a Sinapsi</p>
      <textarea id="q" placeholder="es. per errore ho usato la P560 su un VCEM, è valida?"></textarea>
      <button onclick="ask()">Invia</button>
      <div style="margin-top:12px;">
        <span class="chip" onclick="setQ('posso usare la P560 sui VCEM?')">P560 su VCEM</span>
        <span class="chip" onclick="setQ('lamiera non serrata sui CTF, cosa controllo?')">Lamiera non serrata</span>
        <span class="chip" onclick="setQ('mi dai i codici dei connettori CTF?')">Codici CTF</span>
        <span class="chip" onclick="setQ('quando devo usare il CTL MAXI?')">CTL MAXI</span>
      </div>
    </div>
    <div class="right">
      <div class="right-header">
        <div>Risposta GOLD</div>
        <div id="status" style="font-size:.7rem;opacity:.6;">pronto</div>
      </div>
      <div class="answer" id="ans">Fai una domanda…</div>
      <div class="meta" id="meta" style="display:none;">
        id: <span id="sid"></span> · match: <span id="sco"></span>
      </div>
    </div>
  </main>
  <script>
    function setQ(t){ document.getElementById('q').value = t; ask(); }
    async function ask(){
      const q = document.getElementById('q').value.trim();
      const ans = document.getElementById('ans');
      const status = document.getElementById('status');
      const meta = document.getElementById('meta');
      const sid = document.getElementById('sid');
      const sco = document.getElementById('sco');
      if(!q){ ans.textContent = 'Scrivi una domanda.'; return; }
      status.textContent = 'sto chiedendo…';
      try{
        const res = await fetch('/ask', {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({question: q})
        });
        const data = await res.json();
        ans.textContent = data.answer || 'Nessuna risposta.';
        sid.textContent = data.source_id || '—';
        sco.textContent = data.matched !== null ? data.matched.toFixed(2) : '—';
        meta.style.display = 'block';
        status.textContent = 'ok';
      }catch(e){
        ans.textContent = 'Errore di rete o backend.';
        status.textContent = 'errore';
        meta.style.display = 'none';
      }
    }
  </script>
</body>
</html>
"""


# ----------------- ROUTES -----------------
@app.get("/")
def root():
    return Response(content=HTML_UI, media_type="text/html")


@app.get("/ping")
def ping():
    return {
        "status": "ok",
        "items": len(DATA.get("items", [])),
        "note": DATA.get("_meta", {}).get("note", "")
    }


@app.post("/ask", response_model=AskOut)
def ask(body: AskIn):
    ans, src, m = find_best(body.question)
    return AskOut(answer=ans, source_id=src, matched=m)


@app.post("/api/ask", response_model=AskOut)
def ask_alias(body: AskIn):
    ans, src, m = find_best(body.question)
    return AskOut(answer=ans, source_id=src, matched=m)
