# -*- coding: utf-8 -*-
"""
Tecnaria Sinapsi – app.py (router pesato + cross-answer CTF↔P560)
- Router robusto (punteggi per famiglia + priorità a keyword forti)
- Cross-answer: se la domanda è su CTF ma parla di chiodatrice/P560,
  la risposta include in modo deterministico una nota dalla famiglia P560.
- Endpoint di diagnostica: /health, /selfcheck, /debug
- UI minimale nera/arancione con logo "T" testuale
"""
import json, re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, HTMLResponse

# ====== CONFIG ======
BASE_PATH = Path("static/data")
ROUTER_FILE = BASE_PATH / "tecnaria_router_index.json"
CONTACTS_FILE = BASE_PATH / "contatti.json"
BANK_FILE = BASE_PATH / "bancari.json"

FAMILIES = ["ctf", "gts", "diapason", "ctl", "mini-cem-e", "spit-p560"]

# Parole chiave per scoring router
KEYWORDS: Dict[str, List[str]] = {
    "ctf": [
        "ctf","connettore","solaio","collaborante","acciaio","calcestruzzo",
        "lamiera", "lamiera grecata","trave","sparare","chiodo","chiodi"
    ],
    "gts": ["gts","manicotto","giunzione","spine","tiranti","camicia"],
    "diapason": ["diapason","soletta leggera","cappa","rinforzo laterocemento"],
    "ctl": ["ctl","vite","viti","legno calcestruzzo","tavolato","tetto","travi in legno"],
    "mini-cem-e": ["mini-cem-e","minicem","camicia","consolidamento","iniezione","boiacca"],
    "spit-p560": [
        "p560","spit","chiodatrice","sparachiodi","propulsore","propulsori",
        "taratura","potenza","hsbr14","hsbr 14","hsb r14"
    ],
}

# ====== IO sicuro ======
def load_json(path: Path) -> Any:
    if not path or not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"__error__": f"{path}: {e.__class__.__name__}: {e}"}

def extract_qa(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict) and "__error__" in payload:
        return []
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("qa"), list):
        return payload["qa"]
    acc = []
    for key in ("items", "dataset", "data", "entries"):
        arr = payload.get(key)
        if isinstance(arr, list):
            for it in arr:
                if isinstance(it, dict) and isinstance(it.get("qa"), list):
                    acc.extend(it["qa"])
    return acc

# ====== Normalizzazione + util ======
WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", re.UNICODE)
def norm(text: str) -> str:
    return " ".join(w.lower() for w in WORD_RE.findall(text or ""))

def contains_any(text: str, kws: List[str]) -> bool:
    t = norm(text)
    return any(k in t for k in kws)

# ====== Router: punteggi + priorità + cross-intent ======
def route_insight(question: str) -> Dict[str, Any]:
    """
    Calcola:
      - score per famiglia
      - primary (massimo score, con priorità a hit 'forti')
      - secondary (le altre non nulle)
      - flag cross: needs_p560_for_ctf quando domanda cita chiodatrice/P560 + CTF
    """
    t = norm(question)

    # punteggio grezzo: numero di keyword contenute
    scores: Dict[str, float] = {fam: 0.0 for fam in FAMILIES}
    for fam, kws in KEYWORDS.items():
        for k in kws:
            if k in t:
                scores[fam] += 1.0

    # bonus per keyword "forti"
    if contains_any(t, KEYWORDS["spit-p560"]):
        scores["spit-p560"] += 1.5
    if contains_any(t, ["overview","parlami","che cos","cos e","cos'è","cos'e"]):
        # preferisci famiglie con overview robuste; non forzi scelta,
        # ma alza i pesi di tutti per non tornare None
        for fam in FAMILIES:
            scores[fam] += 0.1

    # primary/secondary
    primary = max(scores, key=lambda k: scores[k]) if any(scores.values()) else None
    secondary = [fam for fam in FAMILIES if fam != primary and scores[fam] > 0]

    # cross rule CTF ↔ P560:
    mentions_ctf = contains_any(t, KEYWORDS["ctf"])
    mentions_p560 = contains_any(t, KEYWORDS["spit-p560"])
    mentions_tool = contains_any(t, ["chiodatrice","sparachiodi","propulsore","propulsori","taratura","potenza","chiodi"])

    needs_p560_for_ctf = (mentions_ctf and (mentions_p560 or mentions_tool))
    # Nota: se la domanda è solo “P560” senza CTF, primary resterà spit's score.
    # Ma se primary = ctf e needs_p560_for_ctf = True → risposta ibrida.

    return {
        "primary": primary,
        "secondary": secondary,
        "scores": scores,
        "mentions": {
            "ctf": mentions_ctf,
            "p560": mentions_p560,
            "tool": mentions_tool
        },
        "needs_p560_for_ctf": needs_p560_for_ctf
    }

# ====== dataset paths tolleranti ======
def dataset_candidates_for_code(code: Optional[str]) -> List[Path]:
    if not code:
        return []
    names = [f"tecnaria_{code}_qa500.json"]
    code_u = code.replace("-", "_")
    names += [
        f"tecnaria_{code_u}_qa500.json",
        f"{code}_qa500.json",
        f"{code_u}_qa500.json",
        f"tecnaria{code_u}_qa500.json",
        f"tecnaria_{code.replace('_','-')}_qa500.json",
    ]
    if code == "mini-cem-e":
        names += ["tecnaria_minicemE_qa500.json", "tecnaria_miniceme_qa500.json"]
    return [BASE_PATH / n for n in names]

def load_family_dataset(code: Optional[str]) -> Tuple[List[Dict[str, Any]], Optional[Path], Optional[str]]:
    for p in dataset_candidates_for_code(code):
        if p.exists():
            raw = load_json(p)
            qa = extract_qa(raw)
            err = raw.get("__error__") if isinstance(raw, dict) else None
            return qa, p, err
    return [], None, None

# ====== Ranker semplice ======
def score_item(q: str, item: Dict[str, Any]) -> float:
    tq = set(norm(q).split())
    iq = set(norm(item.get("q","")).split())
    ia = set(norm(item.get("a","")).split())
    overlap = len(tq & (iq | ia)) / (len(tq | iq | ia) or 1)
    bonus = 0.0
    cat = (item.get("category") or "").lower()
    tags = [t.lower() for t in item.get("tags", []) if isinstance(t, str)]
    if cat in ("prodotto_base","overview"):
        bonus += 0.15
    if any(t in ("overview","alias") for t in tags):
        bonus += 0.15
    if "parlami" in norm(q) and (cat == "prodotto_base" or "overview" in tags):
        bonus += 0.2
    return overlap + bonus

def semantic_pick(q: str, qa: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not qa:
        return None
    return max(qa, key=lambda it: score_item(q, it))

# ====== App ======
app = FastAPI(title="Tecnaria Sinapsi", version="3.1")

@app.get("/health")
def health():
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
        "router": True,
        "contacts": Path(CONTACTS_FILE).exists(),
        "datasets": datasets,
        "endpoints": {"ui": "/ui", "ask": "/ask?q=...", "debug": "/debug?q=...", "selfcheck": "/selfcheck"}
    }

@app.get("/selfcheck")
def selfcheck():
    checks = []
    for code in FAMILIES:
        qa, path, err = load_family_dataset(code)
        # probe di qualità: una domanda tipica per ogni famiglia
        probe = {
            "ctf": "Parlami dei connettori CTF",
            "gts": "Parlami del manicotto GTS",
            "diapason": "Parlami del sistema Diapason",
            "ctl": "Parlami del sistema CTL",
            "mini-cem-e": "Parlami del Mini-Cem-E",
            "spit-p560": "Parlami della SPIT P560",
        }[code]
        hit = semantic_pick(probe, qa) if qa else None
        checks.append({
            "family": code,
            "used_path": str(path) if path else None,
            "qa_count": len(qa),
            "probe_q": probe,
            "hit_q": (hit or {}).get("q"),
            "preview_a": ((hit or {}).get("a","")[:200] + ("…" if (hit and len(hit.get('a',''))>200) else "")) if hit else None
        })
    return {"status":"ok","checks":checks}

@app.get("/debug")
def debug(q: str = Query(..., description="Domanda per il debug")):
    insight = route_insight(q)
    primary = insight["primary"]
    qa, used, err = load_family_dataset(primary)
    hit = semantic_pick(q, qa) if qa else None

    # eventuale integrazione P560
    p560_note = None
    if insight["needs_p560_for_ctf"]:
        qa_p, used_p, err_p = load_family_dataset("spit-p560")
        hit_p = semantic_pick(q, qa_p) if qa_p else None
        p560_note = {
            "used_path": str(used_p) if used_p else None,
            "hit_q": (hit_p or {}).get("q"),
            "preview_a": ((hit_p or {}).get("a","")[:200] + ("…" if (hit_p and len(hit_p.get('a',''))>200) else "")) if hit_p else None
        }

    return {
        "query": q,
        "insight": insight,
        "primary_used_path": str(used) if used else None,
        "primary_qa_count": len(qa),
        "json_error": err,
        "hit_q": (hit or {}).get("q"),
        "preview_a": ((hit or {}).get("a","")[:220] + ("…" if (hit and len(hit.get('a',''))>220) else "")) if hit else None,
        "p560_augmented": p560_note
    }

@app.get("/ask")
def ask(q: str):
    insight = route_insight(q)
    primary = insight["primary"]
    qa, used, err = load_family_dataset(primary)

    if err:
        return {"answer": f"Dataset non disponibile per {primary}. Errore file: {err}"}
    if not qa:
        return {"answer": f"Nessuna base dati per {primary} (file: {used})."}

    hit = semantic_pick(q, qa)
    if not hit:
        return {"answer": "Non trovo una risposta precisa nei dati. Consulta le schede ufficiali Tecnaria."}

    answer = hit.get("a","").strip()

    # Cross-answer CTF ↔ P560: se la domanda è su CTF e parla di chiodatrice/P560,
    # aggiungi la nota operativa dalla famiglia P560.
    if primary == "ctf" and insight["needs_p560_for_ctf"]:
        qa_p, used_p, err_p = load_family_dataset("spit-p560")
        if qa_p and not err_p:
            hit_p = semantic_pick(q, qa_p) or semantic_pick("P560 chiodatrice per CTF", qa_p)
            if hit_p and isinstance(hit_p.get("a"), str):
                p560_txt = hit_p["a"].strip()
                # evita ripetizioni banali
                if p560_txt and p560_txt not in answer:
                    answer = (
                        answer
                        + "\n\n— **Nota P560 (obbligatoria per CTF)** —\n"
                        + p560_txt
                    )

    return {"answer": answer}

@app.get("/company")
def company():
    contacts = load_json(CONTACTS_FILE)
    bank = load_json(BANK_FILE)
    if isinstance(bank, dict) and "__error__" in bank:
        bank = {}
    return {"contacts": contacts, "bank": bank if isinstance(bank, dict) else {}}

# ====== UI (nera/arancione) ======
UI_HTML = """<!doctype html>
<html lang="it"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Tecnaria Sinapsi</title>
<link rel="icon" href="data:,">
<style>
:root { --orange:#f26522; --black:#111; --ink:#222; --muted:#666; --bg:#fafafa; }
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,sans-serif;background:linear-gradient(180deg,#111 0%,#111 40%,#f26522 100%);}
.container{max-width:1100px;margin:0 auto;padding:24px;}
.header{display:flex;align-items:center;gap:12px;color:#fff}
.logo{width:34px;height:34px;background:#000;border:2px solid #fff;border-radius:6px;display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff}
.h1{font-size:36px;font-weight:800;color:#fff;margin:12px 0 6px}
.sub{color:#ffd;opacity:.9;margin-bottom:18px}
.card{background:#fff;border-radius:16px;box-shadow:0 8px 24px rgba(0,0,0,.15);padding:18px}
.row{display:grid;grid-template-columns:1fr auto;gap:12px;margin:16px 0}
.input{width:100%;padding:14px 16px;border-radius:12px;border:1px solid #ddd;font-size:16px}
.btn{background:#000;color:#fff;border:0;border-radius:12px;padding:14px 18px;font-weight:700;cursor:pointer}
.btn:hover{opacity:.9}
.badges{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px}
.badge{background:rgba(255,255,255,.25);border:1px solid rgba(255,255,255,.4);color:#fff;border-radius:999px;padding:6px 10px;font-size:12px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}
.small{color:#777;font-size:13px}
.answer{white-space:pre-wrap;line-height:1.45}
.footer{color:#eee;padding:24px 0;text-align:center;font-size:12px}
.section{margin-top:14px}
.mono{font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace}
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
      <input id="q" class="input" placeholder="Scrivi qui la tua domanda (es. “Si può usare una qualsiasi chiodatrice per i CTF?”)"/>
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
      <div class="kv small">Base URL: <code id="base" class="mono"></code></div>
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
    const ds = j.datasets || {};
    document.getElementById('health').textContent =
      "CTF:"+(ds.ctf?.qa_count||0)+" | GTS:"+(ds.gts?.qa_count||0)+" | CTL:"+(ds["ctl"]?.qa_count||0)+" | P560:"+(ds["spit-p560"]?.qa_count||0);
    const c = await fetch(BASE + "/company"); const cj = await c.json();
    const ct = cj.contacts || {};
    const line = [ct.ragione_sociale, ct.indirizzo, ct.email].filter(Boolean).join(" — ");
    document.getElementById('contacts').textContent = line || "—";
  }catch(e){ document.getElementById('health').textContent="KO: "+e; }
}
async function ask(){
  const q = document.getElementById('q').value.trim();
  if(!q) return;
  const t0 = performance.now();
  const r = await fetch(BASE + "/ask?q=" + encodeURIComponent(q));
  const j = await r.json();
  const ms = Math.round(performance.now()-t0);
  document.getElementById('out').innerHTML = (j.answer||"—")
     .replaceAll("\\n","<br/>");
  document.getElementById('out').innerHTML += "<br/><br/><span class='small'>⏱ "+ms+" ms</span>";
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
