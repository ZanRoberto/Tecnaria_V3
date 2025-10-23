# app.py — Tecnaria_V3 (deploy-safe, Python 3.13)

from pathlib import Path
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Any, List  # Dict NON usato: usiamo i built-in (dict[str, Any])
import time, re, csv, json

# -------------------------------------------------
# FastAPI
# -------------------------------------------------
app = FastAPI(title="Tecnaria_V3")

# -------------------------------------------------
# Dati (cartella: static/data)
# -------------------------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "static" / "data"
OV_JSON = DATA_DIR / "tecnaria_overviews.json"   # panoramiche famiglie
CMP_JSON = DATA_DIR / "tecnaria_compare.json"    # confronti A vs B
FAQ_CSV = DATA_DIR / "faq.csv"                   # domande/risposte brevi multi-lingua

def load_json(path: Path, fallback: List[dict[str, Any]] | None = None) -> List[dict[str, Any]]:
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f) or []
                if isinstance(data, list):
                    return data
    except Exception:
        pass
    return fallback or []

# === CSV robusto (UTF-8/CP1252 + fix mojibake) ===
def load_faq_csv(path: Path) -> List[dict[str, str]]:
    rows: List[dict[str, str]] = []
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
        _read("utf-8-sig")      # preferito (gestisce anche BOM)
    except Exception:
        try:
            _read("cp1252")     # fallback Windows
        except Exception:
            return rows

    # normalizza artefatti comuni
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

OV_ITEMS: List[dict[str, Any]] = load_json(OV_JSON, [])
CMP_ITEMS: List[dict[str, Any]] = load_json(CMP_JSON, [])
FAQ_ITEMS: List[dict[str, str]] = load_faq_csv(FAQ_CSV)

# Contatori esposti
JSON_BAG = {
    "overviews": OV_ITEMS,
    "compare": CMP_ITEMS,
    "faq": FAQ_ITEMS,
}
FAQ_ROWS = len(FAQ_ITEMS)

# -------------------------------------------------
# Indici + euristiche
# -------------------------------------------------
FAQ_BY_LANG: dict[str, List[dict[str, str]]] = {}
for r in FAQ_ITEMS:
    FAQ_BY_LANG.setdefault(r["lang"], []).append(r)

def detect_lang(q: str) -> str:
    s = (q or "").lower()
    if any(w in s for w in [" the ", " what ", " how ", " can ", " shall ", " should "]): return "en"
    if any(w in s for w in [" el ", " los ", " las ", "¿", "qué", "como", "cómo"]): return "es"
    if any(w in s for w in [" le ", " la ", " les ", " quelle", " comment"]): return "fr"
    if any(w in s for w in [" der ", " die ", " das ", " wie ", " was "]): return "de"
    return "it"

# Token famiglie
FAM_TOKENS: dict[str, List[str]] = {
    "CTF":   ["ctf","lamiera","p560","hsbr14","trave","chiodatrice","sparo"],
    "CTL":   ["ctl","soletta","calcestruzzo","collaborazione","legno"],
    "VCEM":  ["vcem","preforo","vite","legno","essenze","durezza"],
    "CEM-E": ["ceme","laterocemento","secco","senza resine","cappello","cem-e","ceme"],
    "CTCEM": ["ctcem","laterocemento","secco","senza resine","cappa"],
    "GTS":   ["gts","manicotto","filettato","giunzioni","secco"],
    "P560":  ["p560","chiodatrice","propulsori","hsbr14","spit"],
}

def _score_tokens(text: str, tokens: List[str]) -> float:
    t = (" " + (text or "").lower() + " ")
    hits = sum(1 for tok in tokens if tok in t)
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

# -------------------------------------------------
# Intent router
# -------------------------------------------------
def intent_route(q: str) -> dict[str, Any]:
    ql = (q or "").lower().strip()
    lang = detect_lang(ql)

    # 1) Confronti A vs B
    fams = list(FAM_TOKENS.keys())
    for a in fams:
        for b in fams:
            if a >= b:
                continue
            if a.lower() in ql and b.lower() in ql:
                found: dict[str, Any] | None = None
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

    # 2) Famiglia singola
    scored = [(fam, _score_tokens(ql, toks)) for fam, toks in FAM_TOKENS.items()]
    scored.sort(key=lambda x: x[1], reverse=True)
    fam, s = scored[0]
    if s >= 0.2:
        # 2a) FAQ
        for r in FAQ_BY_LANG.get(lang, []):
            keys = (r["tags"] or "") + " " + r["question"]
            token_list = [t for t in re.split(r"[,\s;/\-]+", keys.lower()) if t]
            if _score_tokens(ql, token_list) >= 0.25:
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

    # 3) Fallback
    return {
        "ok": True, "match_id": "<NULL>", "lang": lang,
        "family": "", "intent": "fallback", "source": "fallback", "score": 0,
        "text": "Non ho trovato una risposta diretta nei metadati locali. Specifica meglio la famiglia/prodotto.",
        "html": ""
    }

# -------------------------------------------------
# Endpoint di servizio
# -------------------------------------------------
@app.get("/")
def _root():
    try:
        return {
            "app": "Tecnaria_V3 (online)",
            "status": "ok",
            "data_dir": str(DATA_DIR),
            "json_loaded": list(JSON_BAG.keys()),
            "faq_rows": FAQ_ROWS
        }
    except Exception:
        return {"app": "Tecnaria_V3 (online)", "status": "ok"}

@app.get("/health")
def _health():
    try:
        return {
            "ok": True,
            "json_loaded": list(JSON_BAG.keys()),
            "faq_rows": FAQ_ROWS
        }
    except Exception:
        return {"ok": True}

# -------------------------------------------------
# /api/ask
# -------------------------------------------------
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

# -------------------------------------------------
# UI minimale
# -------------------------------------------------
@app.get("/ui")
def ui_page():
    return {
        "html": """<!doctype html><html lang="it"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Tecnaria V3 — QA</title>
<style>
 body{font-family:system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#0f141a;color:#e9eef3;margin:0}
 .wrap{max-width:980px;margin:40px auto;padding:0 16px}
 h1{font-size:28px;margin:0 0 16px}
 .card{background:#121a23;border:1px solid #22303c;border-radius:14px;padding:18px 18px 2px}
 .row{display:flex;gap:12px}
 input{flex:1;padding:12px;border-radius:12px;border:1px solid #2b3a47;background:#0b1016;color:#e9eef3}
 button{background:linear-gradient(90deg,#ff7a18,#ff3d54);color:#fff;border:none;border-radius:12px;padding:12px 18px;font-weight:600;cursor:pointer}
 .kv{display:grid;grid-template-columns:120px 1fr;gap:6px 14px;margin:14px 0}
 textarea{width:100%;min-height:140px;background:#0b1016;color:#e9eef3;border:1px solid #2b3a47;border-radius:12px;padding:12px}
 small{opacity:.7}
</style></head><body><div class="wrap">
<h1>Tecnaria V3 — QA <small id="pill">online</small></h1>
<div class="card">
  <div class="row">
    <input id="q" placeholder="Scrivi una domanda… (es. 'P560: è un connettore o un'attrezzatura?')"/>
    <button onclick="ask()">Chiedi</button>
  </div>
  <p><small>Endpoint: /api/ask. Rende <code>match_id</code>, <code>intent</code>, <code>family</code>, <code>text/html</code>.</small></p>
  <div class="kv" id="meta"></div>
  <textarea id="ans" readonly></textarea>
</div>
<p style="opacity:.7;margin-top:14px">© Tecnaria • demo UI</p>
</div>
<script>
async function ask(){
  const q = document.getElementById('q').value||'';
  const meta = document.getElementById('meta');
  const ans = document.getElementById('ans');
  meta.innerHTML=''; ans.value='';
  const t0 = performance.now();
  const r = await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q})});
  const j = await r.json();
  const ms = Math.max(1, Math.round(performance.now()-t0));
  const rows = [
    ['ok', String(j.ok).toLowerCase()],
    ['match_id', j.match_id||''],
    ['intent', j.intent||''],
    ['family', j.family||''],
    ['lang', j.lang||''],
    ['ms', String(ms)]
  ];
  meta.innerHTML = rows.map(([k,v])=>`<div><small>${k}</small></div><div>${v}</div>`).join('');
  ans.value = (j.text||'') + (j.html?("\\n\\n[html]\\n"+j.html):'');
}
</script>
</body></html>"""
    }
