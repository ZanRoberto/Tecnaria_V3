import json, re
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from typing import List, Dict, Any

APP_TITLE = "Tecnaria Sinapsi — Q/A"
DATA_PATH = Path(__file__).parent / "static" / "data" / "tecnaria_gold.json"

app = FastAPI(title=APP_TITLE)

# -------------------------------
# Load dataset safely
# -------------------------------
def load_items() -> List[Dict[str, Any]]:
    if not DATA_PATH.exists():
        return []
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "items" in data:
            items = data.get("items", [])
        elif isinstance(data, list):
            items = data
        else:
            items = []
        out = []
        for it in items:
            if isinstance(it, dict):
                fam = str(it.get("family") or it.get("famiglia") or "").strip()
                q = str(it.get("q") or it.get("question") or "").strip()
                a = str(it.get("answer") or it.get("a") or "").strip()
                tags = it.get("tags") or it.get("keywords") or []
                if isinstance(tags, str):
                    tags = [tags]
                out.append({"family": fam, "q": q, "answer": a, "tags": tags})
        return out
    except Exception:
        return []

GOLD_ITEMS: List[Dict[str, Any]] = load_items()

# -------------------------------
# Family routing (P560 vince su CTF)
# -------------------------------
FAMILY_KEYWORDS = [
    ("P560", [
        r"\bp560\b", r"\bspit\s*p560\b", r"\bchiodatrice\b", r"\bspara(chiodi|chiodi)\b",
        r"\butensile\b", r"\bpropulsor[ie]\b", r"\bhsbr14\b"
    ]),
    ("CTF", [
        r"\bctf\b", r"\btrave\b.*\bacciaio\b", r"\blamiera\b\s*(grecata|h\d+)?",
        r"\bchiod[io]?\b", r"\bdoppia\s*chiodatura\b"
    ]),
    ("CTL MAXI", [r"\bctl\s*maxi\b", r"\btavolato\b", r"\bassito\b", r"\bviti?\s*ø?\s*10\b", r"\bsoletta\b"]),
    ("CTL", [r"\bctl\b", r"\btrave\b.*\blegno\b", r"\bviti?\s*ø?\s*10\b"]),
    ("CTCEM", [r"\bctcem\b", r"\blaterocemento\b", r"\bresine?\b", r"\bpre[ -]?foro\b"]),
    ("VCEM", [r"\bvcem\b", r"\blaterocemento\b", r"\bmeccanico\b"]),
    ("DIAPASON", [r"\bdiapason\b"]),
    ("GTS", [r"\bgts\b", r"\bmanicott[io]\b"]),
    ("ACCESSORI", [r"\baccessori\b", r"\bkit\b", r"\badattator[ei]\b"]),
]

def classify_family(query: str) -> str:
    q = query.lower()
    for fam, patterns in FAMILY_KEYWORDS:
        for pat in patterns:
            if re.search(pat, q):
                return fam
    return ""

# -------------------------------
# Scoring semplice
# -------------------------------
def score_item(q: str, item: Dict[str, Any]) -> float:
    ql = q.lower()
    score = 0.0
    if item.get("q"):
        score += len(set(ql.split()) & set(item["q"].lower().split()))
    if item.get("answer"):
        ans = item["answer"].lower()
        for tok in ["p560","ctf","ctl","maxi","lamiera","viti","chiod","rete","taratura","hsbr14"]:
            if tok in ql and tok in ans:
                score += 0.8
    for t in item.get("tags", []):
        if isinstance(t, str) and t.lower() in ql:
            score += 1.2
    return score

# -------------------------------
# Clean-up tono/duplicati
# -------------------------------
def clean_answer(text: str) -> str:
    if not text: return ""
    text = re.sub(r"^\s*no\s*[:\.]\s*", "", text, flags=re.I)  # niente "No." in testa
    text = re.sub(r"(\*?Nota RAG:[^\n]*\.)\s*(\*?Nota RAG:[^\n]*\.)", r"\1", text, flags=re.I)  # dedup
    return text.strip()

# -------------------------------
# Fallback canonici
# -------------------------------
CANONICAL = {
    "P560": (
        "**P560 — Utensile dedicato per CTF (posa a secco)**\n"
        "**Quando si usa**: posa dei connettori CTF su travi in acciaio S275–S355; ammessa lamiera grecata "
        "**1×1,5 mm** o **2×1,0 mm** solo se **ben serrata** all’ala.\n\n"
        "**Sequenza operativa**\n"
        "1) Tracciamento maglia e pulizia del punto d’impatto.\n"
        "2) Appoggio del connettore e **doppia chiodatura (2×HSBR14)** con **SPIT P560**: utensile perpendicolare, pressione piena.\n"
        "3) **Taratura**: eseguire **2–3 tiri di prova** sullo stesso acciaio; chiodi a **filo piastra** (no sporgenze).\n"
        "4) Registrare potenza impostata e lotti cartucce nel **giornale lavori**.\n\n"
        "**Sicurezza (DPI)**: occhiali EN166, guanti antitaglio, protezione udito; **perimetro 3 m**.\n\n"
        "**Errori comuni**\n"
        "- Lamiera non serrata → rimbalzo; serrare con morsetti/puntellazioni.\n"
        "- Potenza insufficiente → chiodi sporgenti.\n"
        "- Connettore disassato → contatto piastra/ala insufficiente.\n\n"
        "**Checklist rapida**\n"
        "• 2–3 tiri di prova • Doppia chiodatura completata • Piastra aderente • Lamiera ben serrata • DPI + perimetro 3 m\n\n"
        "*Nota RAG: risposte filtrate su prodotti Tecnaria; no marchi terzi.*"
    ),
    "CTF": (
        "**CTF — Connettori per acciaio-calcestruzzo (posa a secco)**\n"
        "**Fissaggio**: SPIT **P560** + **2 chiodi HSBR14** per connettore. Trave S275–S355; anima ≥ **6 mm**.\n"
        "Con lamiera: **1×1,5 mm** o **2×1,0 mm** **ben serrata** all’ala; posa **sopra la lamiera**.\n\n"
        "**Taratura & Sicurezza**: 2–3 tiri di prova; chiodi a filo piastra; DPI EN166/guanti/udito; perimetro 3 m.\n\n"
        "**Checklist**: doppia chiodatura, piastra aderente, rete a metà spessore, cls ≥ C25/30.\n\n"
        "*Nota RAG: risposte filtrate su prodotti Tecnaria; no marchi terzi.*"
    ),
}

# -------------------------------
# Best answer selection
# -------------------------------
def best_answer(query: str) -> Dict[str, Any]:
    fam = classify_family(query)
    candidates = []
    pool = GOLD_ITEMS
    if fam:
        for it in GOLD_ITEMS:
            if it.get("family","").strip().lower() == fam.lower():
                candidates.append(it)
        pool = candidates or GOLD_ITEMS

    if not pool:
        txt = CANONICAL.get(fam, "")
        return {"family": fam or "", "score": 0.0, "answer": clean_answer(txt)}

    scored = []
    for it in pool:
        s = score_item(query, it)
        if fam and it.get("family","").strip().lower() == fam.lower():
            s += 1.5
        scored.append((s, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    top_score, top = scored[0]
    ans = clean_answer(top.get("answer",""))

    if (not ans or len(ans) < 120) and fam in CANONICAL:
        base = CANONICAL[fam]
        if ans and ans not in base:
            ans = f"{ans}\n\n{base}"
        else:
            ans = base

    return {"family": fam or (top.get("family") or ""), "score": round(float(top_score), 2), "answer": clean_answer(ans or "")}

# -------------------------------
# HTML (no f-string!) + replace
# -------------------------------
HTML_PAGE_TEMPLATE = """
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>{{APP_TITLE}}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
<style>
  body { margin:0; font-family:Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial; background:#0b0b0b; color:#fff; }
  .header {
    background: linear-gradient(90deg, #ff7a1a, #0b0b0b);
    padding: 28px 18px;
  }
  .wrap { max-width: 980px; margin: 0 auto; }
  h1 { margin: 0 0 8px; font-size: 28px; font-weight: 800; }
  .subtitle { opacity:.9; }
  .searchbox { margin: 22px 0; display:flex; gap:10px; }
  input[type="text"] {
    flex: 1; padding: 14px 16px; border-radius: 12px; border: 1px solid #222; background: #111; color: #fff; outline:none;
  }
  button {
    padding: 14px 18px; border-radius: 12px; border: 0; background: #ff7a1a; color:#0b0b0b; font-weight:800; cursor:pointer;
  }
  .pillbar { display:flex; gap:8px; flex-wrap:wrap; margin-top:6px; }
  .pill {
    background:#161616; border:1px solid #222; padding:8px 10px; border-radius:999px; font-size:13px; cursor:pointer;
  }
  .grid { display:grid; grid-template-columns: 2fr 3fr; gap: 18px; margin: 22px 0 40px; }
  .card { background:#0f0f0f; border:1px solid #1b1b1b; border-radius:16px; padding:18px; }
  .muted { color:#cfcfcf; opacity:.8; font-size:13px; }
  .ans h3 { margin:0 0 8px; }
  .meta { font-size:12px; color:#bbb; margin:6px 0 14px; }
  .answer { line-height:1.5; white-space:pre-wrap; }
</style>
</head>
<body>
  <div class="header">
    <div class="wrap">
      <h1>Trova la soluzione, in linguaggio Tecnaria.</h1>
      <div class="subtitle">Bot ufficiale — risposte su CTF, CTL/CTL MAXI, P560, CTCEM/VCEM, confronti, codici, ordini.</div>
      <div class="searchbox">
        <input id="q" type="text" placeholder="Scrivi la domanda (es. ‘Mi parli della P560?’)" />
        <button onclick="ask()">Chiedi a Sinapsi</button>
      </div>
      <div class="pillbar">
        <div class="pill" onclick="sample('Mi parli della P560?')">P560 (istruzioni)</div>
        <div class="pill" onclick="sample('CTL MAXI su tavolato 30 mm e soletta 50 mm: quali viti?')">CTL MAXI + viti</div>
        <div class="pill" onclick="sample('Posso usare CTL e CTL MAXI nello stesso solaio?')">Mix CTL/MAXI</div>
        <div class="pill" onclick="sample('I CTCEM usano resine?')">CTCEM resine?</div>
        <div class="pill" onclick="sample('Mi dai i codici dei CTF?')">Codici CTF</div>
      </div>
    </div>
  </div>
  <div class="wrap grid">
    <div class="card">
      <div class="muted">Endpoint /qa/ask: <span id="hstatus">…</span></div>
      <div class="muted">Famiglia: <span id="fam">—</span> | Score: <span id="score">—</span></div>
    </div>
    <div class="card ans">
      <h3>Risposta Migliore</h3>
      <div class="answer" id="answer">—</div>
    </div>
  </div>
<script>
async function health(){
  const r = await fetch('/health');
  document.getElementById('hstatus').innerText = r.ok ? 'ok' : 'ko';
}
function sample(v){ document.getElementById('q').value=v; ask(); }
async function ask(){
  const q = document.getElementById('q').value || '';
  const r = await fetch('/qa/ask?q='+encodeURIComponent(q));
  const j = await r.json();
  document.getElementById('fam').innerText = j.family || '—';
  document.getElementById('score').innerText = (j.score ?? '—');
  document.getElementById('answer').innerText = j.answer || '—';
}
health();
</script>
</body>
</html>
"""

HTML_PAGE = HTML_PAGE_TEMPLATE.replace("{{APP_TITLE}}", APP_TITLE)

@app.get("/", response_class=HTMLResponse)
def home():
    return HTML_PAGE

@app.get("/health")
def health():
    return {
        "title": APP_TITLE,
        "endpoints": {"health": "/health", "ask": "/qa/ask?q=MI%20PARLI%20DELLA%20P560%20%3F"},
        "data_file": str(DATA_PATH),
        "items_loaded": len(GOLD_ITEMS)
    }

@app.get("/qa/ask")
def qa_ask(q: str = Query(..., min_length=2)):
    guard_terms = ["tecnaria","ctf","ctl","p560","ctcem","vcem","diapason","gts","connettori","lamiera","viti","chiod"]
    if not any(t in q.lower() for t in guard_terms):
        msg = ("Sono il bot ufficiale Tecnaria (Bassano del Grappa). "
               "Rispondo solo a domande su prodotti e sistemi Tecnaria (CTF, CTL/CTL MAXI, P560, CTCEM/VCEM, ecc.).")
        return JSONResponse({"family": "", "score": 0.0, "answer": msg})
    res = best_answer(q)
    return JSONResponse(res)
