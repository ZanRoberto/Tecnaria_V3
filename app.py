# app.py — Tecnaria_V3 (router fix + UI già pronta)

from typing import List, Dict, Any
from pathlib import Path
from fastapi import FastAPI, Response
from pydantic import BaseModel
import time, re, csv, json

app = FastAPI(title="Tecnaria_V3")

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "static" / "data"
OV_JSON = DATA_DIR / "tecnaria_overviews.json"
CMP_JSON = DATA_DIR / "tecnaria_compare.json"
FAQ_CSV = DATA_DIR / "faq.csv"

def load_json(path: Path, fallback: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f) or []
                if isinstance(data, list):
                    return data
    except Exception:
        pass
    return fallback or []

def load_faq_csv(path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    if not path.exists():
        return rows

    def _read(encoding: str):
        with path.open("r", encoding=encoding, newline="") as f:
            rdr = csv.DictReader(f)
            for r in rdr:
                rows.append({
                    "id": (r.get("id") or "").strip(),
                    "lang": (r.get("lang") or "").strip().lower() or "it",
                    "question": (r.get("question") or "").strip(),
                    "answer": (r.get("answer") or "").strip(),
                    "tags": (r.get("tags") or "").strip().lower(),
                })

    try:
        _read("utf-8-sig")
    except Exception:
        try:
            _read("cp1252")
        except Exception:
            return rows

    fixes = {
        "â€™": "’", "â€œ": "“", "â€\x9d": "”", "â€“": "–", "â€”": "—",
        "Ã ": "à", "Ã¨": "è", "Ã©": "é", "Ã¬": "ì", "Ã²": "ò", "Ã¹": "ù",
        "Â°": "°", "Â§": "§", "Â±": "±", "Â€": "€",
    }
    for r in rows:
        for k in ("question", "answer", "tags"):
            t = r[k]
            for bad, good in fixes.items():
                t = t.replace(bad, good)
            r[k] = t

    return rows

OV_ITEMS: List[Dict[str, Any]] = load_json(OV_JSON, [])
CMP_ITEMS: List[Dict[str, Any]] = load_json(CMP_JSON, [])
FAQ_ITEMS: List[Dict[str, str]] = load_faq_csv(FAQ_CSV)

JSON_BAG = {"overviews": OV_ITEMS, "compare": CMP_ITEMS, "faq": FAQ_ITEMS}
FAQ_ROWS = len(FAQ_ITEMS)

FAQ_BY_LANG: Dict[str, List[Dict[str, str]]] = {}
for r in FAQ_ITEMS:
    FAQ_BY_LANG.setdefault(r["lang"], []).append(r)

def detect_lang(q: str) -> str:
    s = (q or "").lower()
    if any(w in s for w in [" the ", " what ", " how ", " can ", " shall ", " should ", "hardwood", "predrill"]): return "en"
    if any(w in s for w in [" el ", " los ", " las ", "¿", "qué", "como", "cómo"]): return "es"
    if any(w in s for w in [" le ", " la ", " les ", " quelle", " comment"]): return "fr"
    if any(w in s for w in [" der ", " die ", " das ", " wie ", " was "]): return "de"
    return "it"

# === Tokens (estesi) ===
FAM_TOKENS: Dict[str, List[str]] = {
    "CTF":   ["ctf","lamiera","p560","hsbr14","trave","chiodatrice","sparo","grecata","grecate"],
    "CTL":   ["ctl","soletta","calcestruzzo","collaborazione","legno","topping","timber"],
    "VCEM":  ["vcem","preforo","vite","legno","essenze","durezza","hardwood","predrill","pilot"],
    "CEM-E": ["cem-e","ceme","laterocemento","secco","senza resine","resin-free"],
    "CTCEM": ["ctcem","ct-cem","laterocemento","secco","senza resine"],
    "GTS":   ["gts","manicotto","filettato","giunzioni","secco","sleeve","threaded"],
    "P560":  ["p560","chiodatrice","propulsori","hsbr14","nailer","cartridges","cartucce","attrezzatura","tool"],
}

# === Helper: punteggio tokens ===
def _score_tokens(text: str, tokens: List[str]) -> float:
    t = (" " + (text or "").lower() + " ")
    hits = sum(1 for tok in tokens if tok and tok in t)
    return hits / max(1, len(tokens))

def _find_overview(fam: str) -> str:
    fam = (fam or "").upper()
    for it in OV_ITEMS:
        if (it.get("family") or "").upper() == fam:
            return (it.get("answer") or "").strip()
    return f"{fam}: descrizione, ambiti applicativi, posa, controlli e riferimenti."

def _compare_html(famA: str, famB: str, ansA: str, ansB: str) -> str:
    return (
        "<div><h2>Confronto</h2>"
        "<div style='display:flex;gap:24px;flex-wrap:wrap'>"
        f"<div class='side' style='flex:1;min-width:320px'><h3>{famA}</h3><p>{ansA}</p>"
        f"<p><small>Fonte: <b>OVERVIEW::{famA}</b></small></p></div>"
        f"<div class='side' style='flex:1;min-width:320px'><h3>{famB}</h3><p>{ansB}</p>"
        f"<p><small>Fonte: <b>OVERVIEW::{famB}</b></small></p></div>"
        "</div></div>"
    )

# === Intent router (fix: boost letterali + FAQ matcher più “morbido”) ===
def intent_route(q: str) -> Dict[str, Any]:
    ql = (q or "").lower().strip()
    lang = detect_lang(ql)

    # 0) scorciatoie letterali (vincono sempre)
    literals = {
        "p560": "P560", " gts": "GTS", "gts ": "GTS", " gts?": "GTS",
        "cem-e": "CEM-E", "ctcem": "CTCEM", "ctf": "CTF", "ctl": "CTL", "vcem": "VCEM"
    }
    for needle, fam_hit in literals.items():
        if needle in (" " + ql + " "):
            fam = fam_hit
            # prova FAQ diretta con soglia morbida
            for r in FAQ_BY_LANG.get(lang, []):
                keys = f"{r['tags']} {r['question']} {r['answer'][:200]}".lower()
                if _score_tokens(ql, re.split(r"[,\s;/\-]+", keys)) >= 0.15:
                    return {
                        "ok": True, "match_id": r["id"] or f"FAQ::{fam}", "lang": lang,
                        "family": fam, "intent": "faq", "source": "faq", "score": 90.0,
                        "text": r["answer"], "html": ""
                    }
            # se non trova FAQ, rimanda a overview della famiglia
            ov = _find_overview(fam)
            return {
                "ok": True, "match_id": f"OVERVIEW::{fam}", "lang": lang,
                "family": fam, "intent": "overview", "source": "overview", "score": 80.0,
                "text": ov, "html": ""
            }

    # 1) Confronti A vs B
    fams = list(FAM_TOKENS.keys())
    for a in fams:
        for b in fams:
            if a >= b:
                continue
            if a.lower() in ql and b.lower() in ql:
                found = None
                for it in CMP_ITEMS:
                    fa = (it.get("famA") or "").upper()
                    fb = (it.get("famB") or "").upper()
                    if {fa, fb} == {a, b}:
                        found = it
                        break
                if found:
                    html = found.get("html") or ""
                    text = found.get("answer") or ""
                else:
                    ansA = _find_overview(a)
                    ansB = _find_overview(b)
                    html = _compare_html(a, b, ansA, ansB)
                    text = ""
                return {
                    "ok": True,
                    "match_id": f"COMPARE::{a}_VS_{b}",
                    "lang": lang,
                    "family": f"{a}+{b}",
                    "intent": "compare",
                    "source": "compare" if found else "synthetic",
                    "score": 92.0,
                    "text": text,
                    "html": html,
                }

    # 2) Famiglia singola via punteggio
    scored = [(fam, _score_tokens(ql, toks)) for fam, toks in FAM_TOKENS.items()]
    scored.sort(key=lambda x: x[1], reverse=True)
    fam, s = scored[0]
    if s >= 0.20:
        # 2a) FAQ — soglia più permissiva e chiavi più ricche
        for r in FAQ_BY_LANG.get(lang, []):
            keys = f"{r['tags']} {r['question']} {r['answer'][:200]}".lower()
            if _score_tokens(ql, re.split(r"[,\s;/\-]+", keys)) >= 0.15:
                return {
                    "ok": True, "match_id": r["id"] or f"FAQ::{fam}", "lang": lang,
                    "family": fam, "intent": "faq", "source": "faq", "score": 88.0,
                    "text": r["answer"], "html": ""
                }
        # 2b) overview
        ov = _find_overview(fam)
        return {
            "ok": True, "match_id": f"OVERVIEW::{fam}", "lang": lang,
            "family": fam, "intent": "overview", "source": "overview", "score": 75.0,
            "text": ov, "html": ""
        }

    # 2c) caso speciale EN VCEM hardwoods → FAQ::VCEM
    if "vcem" in ql and any(k in ql for k in ["hardwood", "hardwoods", "predrill", "pre-drill", "pilot", "70", "80"]):
        return {
            "ok": True, "match_id": "FAQ::VCEM", "lang": "en",
            "family": "VCEM", "intent": "faq", "source": "faq", "score": 90.0,
            "text": next((r["answer"] for r in FAQ_BY_LANG.get("en", []) if "vcem" in (r["tags"]+" "+r["question"]).lower()), ""),
            "html": ""
        }

    # 3) Fallback
    return {
        "ok": True, "match_id": "<NULL>", "lang": lang,
        "family": "", "intent": "fallback", "source": "fallback", "score": 0,
        "text": "Non ho trovato una risposta diretta nei metadati locali. Specifica meglio la famiglia/prodotto.",
        "html": ""
    }

# --- Endpoint servizio ---
@app.get("/")
def _root():
    try:
        return {"app": "Tecnaria_V3 (online)", "status": "ok", "data_dir": str(DATA_DIR), "json_loaded": list(JSON_BAG.keys()), "faq_rows": FAQ_ROWS}
    except Exception:
        return {"app": "Tecnaria_V3 (online)", "status": "ok"}

@app.get("/health")
def _health():
    try:
        return {"ok": True, "json_loaded": list(JSON_BAG.keys()), "faq_rows": FAQ_ROWS}
    except Exception:
        return {"ok": True}

# --- /api/ask ---
class AskIn(BaseModel):
    q: str

class AskOut(BaseModel):
    ok: bool
    match_id: str
    ms: int
    text: str | None = ""
    html: str | None = ""
    lang: str | None = None
    family: str | None = None
    intent: str | None = None
    source: str | None = None
    score: float | int | None = None

@app.post("/api/ask", response_model=AskOut)
def api_ask_local(body: AskIn) -> AskOut:
    t0 = time.time()
    routed = intent_route(body.q or "")
    ms = int((time.time() - t0) * 1000)
    return AskOut(
        ok=True,
        match_id=str(routed.get("match_id") or "<NULL>"),
        ms=ms if ms > 0 else 1,
        text=str(routed.get("text") or ""),
        html=str(routed.get("html") or ""),
        lang=routed.get("lang"),
        family=routed.get("family"),
        intent=routed.get("intent"),
        source=routed.get("source"),
        score=routed.get("score"),
    )

# --- UI minimale già inclusa (/ui) ---
_UI_HTML = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Tecnaria V3 — QA</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet"/>
<style>
:root{--bg:#0b0f14;--panel:#111827;--ink:#e5e7eb;--muted:#9ca3af;--acc:#f59e0b;--ok:#10b981}
*{box-sizing:border-box} body{margin:0;background:linear-gradient(180deg,#0b0f14,#141a22);color:var(--ink);font-family:Inter,system-ui,sans-serif}
.wrap{max-width:980px;margin:40px auto;padding:0 16px}
.head{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.badge{background:#0f172a;border:1px solid #1f2937;color:#93c5fd;padding:6px 10px;border-radius:999px;font-size:12px}
.panel{background:var(--panel);border:1px solid #1f2937;border-radius:16px;padding:16px}
.row{display:flex;gap:12px;flex-wrap:wrap}
.inp{flex:1;min-width:220px;background:#0b1220;border:1px solid #23304a;border-radius:12px;padding:14px 14px;color:var(--ink);outline:none}
.btn{background:linear-gradient(90deg,#f59e0b,#ef4444);border:none;color:#111827;font-weight:700;border-radius:12px;padding:14px 18px;cursor:pointer}
.btn:disabled{opacity:.5;cursor:not-allowed}
.out{margin-top:16px;font-size:14px;white-space:pre-wrap}
.small{color:var(--muted);font-size:12px;margin-top:8px}
.kv{display:grid;grid-template-columns:120px 1fr;gap:6px 12px;margin-top:10px}
.kv b{color:#cbd5e1}
.code{font-family:ui-monospace,Menlo,Consolas,monospace;background:#0b1220;border:1px dashed #334155;border-radius:8px;padding:10px;overflow:auto}
</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <h1 style="margin:0;font-size:20px">Tecnaria V3 — QA</h1>
    <span class="badge">online</span>
  </div>
  <div class="panel">
    <div class="row">
      <input id="q" class="inp" placeholder="Scrivi una domanda… es. 'P560: è un connettore o un'attrezzatura?'"/>
      <button id="go" class="btn">Chiedi</button>
    </div>
    <div class="small">Endpoint: <code>/api/ask</code>. Rende <code>match_id, intent, family, text/html</code>.</div>
    <div id="out" class="out"></div>
  </div>
</div>
<script>
const q = document.getElementById('q');
const go = document.getElementById('go');
const out = document.getElementById('out');
async function ask() {
  const val = q.value.trim();
  if (!val) return;
  go.disabled = true; out.innerHTML = '…';
  try {
    const r = await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q:val})});
    const j = await r.json();
    out.innerHTML = `
      <div class="kv">
        <b>ok</b><span>${j.ok}</span>
        <b>match_id</b><span>${j.match_id||''}</span>
        <b>intent</b><span>${j.intent||''}</span>
        <b>family</b><span>${j.family||''}</span>
        <b>lang</b><span>${j.lang||''}</span>
        <b>ms</b><span>${j.ms}</span>
      </div>
      ${j.text?`<div class="code" style="margin-top:10px">${j.text}</div>`:''}
      ${j.html?`<div style="margin-top:10px">${j.html}</div>`:''}
    `;
  } catch(e){
    out.textContent = 'Errore: '+e;
  } finally {
    go.disabled = false;
  }
}
go.addEventListener('click', ask);
q.addEventListener('keydown', e=>{ if(e.key==='Enter') ask(); });
</script>
</body>
</html>
"""

@app.get("/ui")
def ui():
    return Response(content=_UI_HTML, media_type="text/html")
