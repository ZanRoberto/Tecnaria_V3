# -*- coding: utf-8 -*-
"""
Tecnaria Sinapsi – app.py (v4.0)
- Router pesato per famiglia
- Classifier semantico leggero per: ruolo(entità) + intento
- Priorità e regole cross migliorate (CTF <-> P560)
- Picker ottimizzato per "parlami..." (overview)
- Endpoints: /health, /selfcheck, /debug, /ask, /company, /ui
"""
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

# ====== CONFIG ======
BASE_PATH = Path("static/data")
CONTACTS_FILE = BASE_PATH / "contatti.json"
BANK_FILE = BASE_PATH / "bancari.json"

FAMILIES = ["ctf", "gts", "diapason", "ctl", "mini-cem-e", "spit-p560"]

# KEYWORDS base (spostati "chiodo/chiodi" su P560)
KEYWORDS: Dict[str, List[str]] = {
    "ctf": [
        "ctf","connettore","solaio","collaborante","acciaio","calcestruzzo",
        "lamiera","lamiera grecata","trave"
    ],
    "gts": ["gts","manicotto","giunzione","spine","tiranti","camicia"],
    "diapason": ["diapason","soletta leggera","cappa","rinforzo laterocemento"],
    "ctl": ["ctl","vite","viti","legno calcestruzzo","tavolato","tetto","travi in legno"],
    "mini-cem-e": ["mini-cem-e","minicem","camicia","consolidamento","iniezione","boiacca"],
    "spit-p560": [
        "p560","spit","chiodatrice","sparachiodi","propulsore","propulsori",
        "taratura","potenza","hsbr14","hsbr 14","hsb r14","chiodo","chiodi"
    ],
}

# Diccionari semantici estesi (entity role detector)
ENTITY_DICT = {
    "tool": ["p560", "spit", "chiodatrice", "sparachiodi", "propulsore", "propulsori", "avvitatore", "trapano"],
    "component": ["ctf", "connettore", "gts", "manicotto", "diapason", "ctl", "minicem", "mini-cem-e"],
    "material": ["legno", "acciaio", "calcestruzzo", "lamiera", "lamiera grecata", "laterocemento"],
    "action": ["posa", "fissare", "infissione", "tarare", "taratura", "regolare", "montare", "saldare", "iniezione"],
}

# Intent patterns
INTENT_PATTERNS = {
    "explain": ["parlami", "che cos", "cos'è", "cos e", "descrivi", "spiegami"],
    "usage": ["posso", "come si", "come va", "si può", "si puo", "serve", "necessario", "obbligatorio"],
    "compare": ["vs", "contro", "meglio", "convenienza", "conviene", "o ", "oppure"],
    "verify": ["come verifico", "come controllo", "controllo", "verifica", "taratura", "tarare"],
    "safety": ["sicurezza", "dpi", "protezione", "occhiali", "guanti", "cuffie"],
}

# Regex helper
WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", re.UNICODE)
def norm(text: str) -> str:
    return " ".join(w.lower() for w in WORD_RE.findall(text or ""))

def contains_any_norm(text: str, kws: List[str]) -> bool:
    t = norm(text)
    return any(k in t for k in kws)

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

# ====== Classifier leggero: entities + intent ======
def detect_entities(question: str) -> Dict[str, List[str]]:
    t = norm(question)
    found = {"tool": [], "component": [], "material": [], "action": []}
    for role, kws in ENTITY_DICT.items():
        for k in kws:
            if k in t:
                found[role].append(k)
    return found

def detect_intent(question: str) -> str:
    t = norm(question)
    # compare first (explicit tokens)
    if any(x in t for x in (" vs ", " vs.", " vs,", " vs:", " vs?")) or contains_any_norm(question, [" vs", " vs "]) or contains_any_norm(question, ["contro", "meglio", "conviene", "convenienza", "oppure"]):
        return "compare"
    for intent, pats in INTENT_PATTERNS.items():
        if contains_any_norm(question, pats):
            return intent
    # default heuristics
    if any(a in t for a in ("come","posso","si puo","si può","serve","necessario")):
        return "usage"
    return "explain"

# ====== Scoring and insight (router + classifier integration) ======
def base_scores_from_keywords(question: str) -> Dict[str, float]:
    t = norm(question)
    scores = {fam: 0.0 for fam in FAMILIES}
    for fam, kws in KEYWORDS.items():
        for k in kws:
            if k in t:
                scores[fam] += 1.0
    # P560 strong bonus
    if contains_any_norm(question, KEYWORDS["spit-p560"]):
        scores["spit-p560"] += 1.5
    # slight boost for general "parlami"
    if contains_any_norm(question, ["parlami", "che cos", "cos e", "cos'è", "cos'e"]):
        for fam in FAMILIES:
            scores[fam] += 0.05
    return scores

def route_insight(question: str) -> Dict[str, Any]:
    t = norm(question)
    intent = detect_intent(question)
    entities = detect_entities(question)
    base_scores = base_scores_from_keywords(question)

    # primary decision rules with classifier:
    # 1) if question explicitly mentions a tool and not a component -> choose tool family (P560)
    mentions_tool = len(entities.get("tool", [])) > 0
    mentions_component = len(entities.get("component", [])) > 0
    mentions_material = len(entities.get("material", [])) > 0
    mentions_action = len(entities.get("action", [])) > 0

    primary = None
    # Rule A: explicit tool and no component => tool primary
    if mentions_tool and not mentions_component:
        primary = "spit-p560"
    else:
        # Rule B: choose max score
        primary = max(base_scores, key=lambda k: base_scores[k]) if any(base_scores.values()) else None

    # Special cross detection: if primary is ctf but question mentions tool or tool-like actions -> mark augmentation
    needs_p560_for_ctf = False
    if primary == "ctf":
        # if mentions tool, or wording is "posso usare" + mentions tool-like word -> augment
        if mentions_tool or contains_any_norm(question, ["posso usare", "si può usare", "si puo usare", "chiodatrice", "chiodi", "propulsore", "propulsori", "taratura", "potenza"]):
            needs_p560_for_ctf = True

    # If intent is compare, try to detect which two families are compared
    compare_candidates = []
    if intent == "compare":
        # pick top2 by score or by explicit entities
        sorted_by_score = sorted(base_scores.items(), key=lambda x: x[1], reverse=True)
        compare_candidates = [k for k,v in sorted_by_score if v>0][:2]
        # if explicit component mentions exist, map them to families
        for comp in entities.get("component",[]):
            comp_lower = comp.lower()
            if comp_lower in ("ctf","connettore"):
                if "ctf" not in compare_candidates: compare_candidates.insert(0,"ctf")
            if comp_lower in ("gts","manicotto"):
                if "gts" not in compare_candidates: compare_candidates.insert(0,"gts")
            if comp_lower in ("ctl",):
                if "ctl" not in compare_candidates: compare_candidates.insert(0,"ctl")
            if comp_lower in ("diapason",):
                if "diapason" not in compare_candidates: compare_candidates.insert(0,"diapason")
            if comp_lower in ("mini-cem-e","minicem"):
                if "mini-cem-e" not in compare_candidates: compare_candidates.insert(0,"mini-cem-e")
    # Build insight
    insight = {
        "intent": intent,
        "entities": entities,
        "base_scores": base_scores,
        "primary": primary,
        "secondary": sorted([fam for fam in FAMILIES if fam != primary and base_scores[fam] > 0], key=lambda x: base_scores[x], reverse=True),
        "needs_p560_for_ctf": needs_p560_for_ctf,
        "compare_candidates": compare_candidates
    }
    return insight

# ====== Ranker + picker ======
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
    t = norm(q)
    if any(x in t for x in ("parlami","overview","che cos","cos e","cos'è","cos'e")):
        overviews = [it for it in qa if (it.get("category","").lower() in ("prodotto_base","overview")
                                         or "overview" in [str(tt).lower() for tt in it.get("tags",[] )])]
        if overviews:
            return max(overviews, key=lambda it: len(it.get("a","")))
    return max(qa, key=lambda it: score_item(q, it))

# ====== App helpers ======
def load_family_by_code(code: Optional[str]) -> Tuple[List[Dict[str, Any]], Optional[Path], Optional[str]]:
    return load_family_dataset(code)

# ====== APP ======
app = FastAPI(title="Tecnaria Sinapsi", version="4.0")

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
    probes = {
        "ctf": "Parlami dei connettori CTF",
        "gts": "Parlami del manicotto GTS",
        "diapason": "Parlami del sistema Diapason",
        "ctl": "Parlami del sistema CTL",
        "mini-cem-e": "Parlami del Mini-Cem-E",
        "spit-p560": "Parlami della SPIT P560",
    }
    for code in FAMILIES:
        qa, path, err = load_family_by_code(code)
        hit = semantic_pick(probes[code], qa) if qa else None
        checks.append({
            "family": code,
            "used_path": str(path) if path else None,
            "qa_count": len(qa),
            "probe_q": probes[code],
            "hit_q": (hit or {}).get("q"),
            "preview_a": ((hit or {}).get("a","")[:200] + ("…" if (hit and len(hit.get('a',''))>200) else "")) if hit else None
        })
    return {"status":"ok","checks":checks}

@app.get("/debug")
def debug(q: str = Query(..., description="Domanda per il debug")):
    insight = route_insight(q)
    primary = insight["primary"]
    qa, used, err = load_family_by_code(primary)
    hit = semantic_pick(q, qa) if qa else None

    p560_note = None
    if insight["needs_p560_for_ctf"]:
        qa_p, used_p, err_p = load_family_by_code("spit-p560")
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
    intent = insight["intent"]

    # Compare intent handling: produce synthesized compare answer if possible
    if intent == "compare" and insight.get("compare_candidates"):
        candidates = insight["compare_candidates"][:2]
        # load both families
        qa_a, used_a, err_a = load_family_by_code(candidates[0]) if len(candidates) > 0 else ([], None, None)
        qa_b, used_b, err_b = load_family_by_code(candidates[1]) if len(candidates) > 1 else ([], None, None)
        hit_a = semantic_pick(q, qa_a) if qa_a else None
        hit_b = semantic_pick(q, qa_b) if qa_b else None

        parts = []
        if hit_a:
            parts.append(f"**{candidates[0].upper()} — Sintesi:**\n{hit_a.get('a')}")
        if hit_b:
            parts.append(f"**{candidates[1].upper()} — Sintesi:**\n{hit_b.get('a')}")
        # generic compare block (if compare.json present, would be used — for now use heuristics)
        compare_note = "\n\n**Confronto sintetico:** valuta azioni, costi di posa, attrezzature e vincoli; preferire la tecnologia che soddisfa i vincoli geometrici e di accesso in cantiere."
        return {"answer": "\n\n".join(parts) + compare_note}

    # Single-family flow
    primary = insight["primary"]
    qa, used, err = load_family_by_code(primary)
    if err:
        return {"answer": f"Dataset non disponibile per {primary}. Errore file: {err}"}
    if not qa:
        return {"answer": f"Nessuna base dati per {primary} (file: {used})."}

    hit = semantic_pick(q, qa)
    if not hit:
        return {"answer": "Non trovo una risposta precisa nei dati. Consulta le schede ufficiali Tecnaria."}

    answer = hit.get("a","").strip()

    # Cross-note: se è CTF e si parla di macchina/propulsori -> aggiungi nota P560
    if primary == "ctf" and insight["needs_p560_for_ctf"]:
        qa_p, used_p, err_p = load_family_by_code("spit-p560")
        if qa_p and not err_p:
            hit_p = semantic_pick(q, qa_p) or semantic_pick("P560 chiodatrice per CTF", qa_p)
            if hit_p and isinstance(hit_p.get("a"), str):
                p560_txt = hit_p["a"].strip()
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

# ====== UI minimal (nera/arancione) ======
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
  document.getElementById('out').innerHTML = (j.answer||"—").replaceAll("\\n","<br/>");
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
