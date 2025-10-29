import json, re, os
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel
from fastapi.responses import HTMLResponse, JSONResponse

APP_TITLE = "Tecnaria Sinapsi — Q/A"
DATA_PATH = Path(__file__).parent / "static" / "data" / "tecnaria_gold.json"

app = FastAPI(title=APP_TITLE)

# ---------------------------
# Utilities
# ---------------------------
def load_items() -> List[Dict[str, Any]]:
    if not DATA_PATH.exists():
        return []
    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "items" in data:
            return data["items"]
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []

ITEMS: List[Dict[str, Any]] = load_items()

# Family boosts and keyword routing
FAMILY_ALIASES = {
    "CTF": ["ctf", "acciaio", "trave acciaio", "hsbr14", "lamiera", "piastra", "s275", "s355"],
    "CTL": ["ctl", "legno", "trave legno", "soletta 4", "soletta 5"],
    "CTL_MAXI": ["ctl maxi", "maxi", "tavolato", "assito", "viti 120", "viti 140"],
    "P560_TOOL": ["p560", "chiodatrice", "sparo", "propulsori", "cartucce", "taratura", "colpo a vuoto"],
    "CTCEM": ["ctcem", "laterocemento", "piastra dentata", "preforo"],
    "VCEM": ["vcem", "laterocemento", "preforo", "avvitare"],
    "DIAPASON": ["diapason"],
    "GTS": ["gts", "manicotti", "barre filettate"],
    "ACCESSORI": ["accessori", "kit", "adattatori"],
    "COMPARAZIONI": ["differenza", "confronto", "vs", "mix", "insieme", "compatibili"],
    "CODICI_ORDINI": ["codici", "sku", "ordine", "offerta", "distinta"]
}

# Hard routing for P560 (takes precedence over anything that mentions it)
def forced_family(query: str) -> Optional[str]:
    q = query.lower()
    p560_triggers = ["p560", "chiodatrice", "sparo", "hsbr14", "propulsori", "cartucce", "taratura", "colpo a vuoto", "boccola"]
    if any(t in q for t in p560_triggers):
        return "P560_TOOL"
    return None

# Naive language detection among IT/EN/FR/DE/ES
def detect_lang(q: str) -> str:
    ql = q.lower()
    # quick hints
    if re.search(r"\b(el|la|los|las|herramienta|hormigón)\b", ql): return "es"
    if re.search(r"\b(le|la|les|outil|béton)\b", ql): return "fr"
    if re.search(r"\b(the|tool|concrete|steel|difference)\b", ql): return "en"
    if re.search(r"\b(die|der|das|werkzeug|beton)\b", ql): return "de"
    # default italian
    return "it"

def lang_labels(lang: str) -> Dict[str, str]:
    MAP = {
        "it": {"ask":"Chiedi a Sinapsi","answer":"Risposta","family":"Famiglia","score":"Score","ok":"ok"},
        "en": {"ask":"Ask Sinapsi","answer":"Answer","family":"Family","score":"Score","ok":"ok"},
        "fr": {"ask":"Demander à Sinapsi","answer":"Réponse","family":"Famille","score":"Score","ok":"ok"},
        "de": {"ask":"Frag Sinapsi","answer":"Antwort","family":"Familie","score":"Score","ok":"ok"},
        "es": {"ask":"Pregunta a Sinapsi","answer":"Respuesta","family":"Familia","score":"Puntuación","ok":"ok"},
    }
    return MAP.get(lang, MAP["it"])

def score_item(q: str, it: Dict[str, Any]) -> float:
    s = 0.0
    ql = q.lower()
    text = " ".join([
        it.get("question",""), it.get("answer",""),
        " ".join(it.get("tags",[])), it.get("family","")
    ]).lower()

    # exact term overlap
    for tok in set(re.findall(r"[a-z0-9]+", ql)):
        if tok in text:
            s += 1.0

    # family boost by aliases
    fam = it.get("family","").upper()
    for k, alias in FAMILY_ALIASES.items():
        if k == fam:
            if any(a in ql for a in alias): s += 2.5

    # explicit family words in query
    if fam and fam.lower() in ql: s += 2.0

    # prefer more “gold” length (richer answer)
    ans_len = len(it.get("answer",""))
    s += min(ans_len/800.0, 2.0)

    return s

def select_best(q: str, items: List[Dict[str,Any]]) -> Dict[str,Any]:
    forced = forced_family(q)
    candidates = items
    if forced:
        candidates = [it for it in items if it.get("family","").upper() == forced]
        # if nothing found (shouldn’t happen), fallback to all items
        if not candidates:
            candidates = items

    # single best
    best = max(candidates, key=lambda it: score_item(q, it), default=None)
    return best or {}

def wrap_gold_answer(ans: str, fam: str, lang: str) -> str:
    # ensure NOTE RAG and single-answer format
    NOTE = {
        "it": "\n\n*Nota RAG: risposte filtrate su prodotti Tecnaria; no marchi terzi.*",
        "en": "\n\n*RAG note: answers filtered to Tecnaria products only; no third-party brands.*",
        "fr": "\n\n*Note RAG : réponses limitées aux produits Tecnaria ; sans marques tierces.*",
        "de": "\n\n*RAG-Hinweis: Antworten nur zu Tecnaria-Produkten; keine Fremdmarken.*",
        "es": "\n\n*Nota RAG: respuestas filtradas sólo a productos Tecnaria; sin marcas de terceros.*",
    }
    footer = NOTE.get(lang, NOTE["it"])
    return ans.strip() + footer

# ---------------------------
# Web UI
# ---------------------------
HTML_PAGE = f"""
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{APP_TITLE}</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  .grad {{ background: linear-gradient(90deg, #ff8c00 0%, #111 70%); }}
</style>
</head>
<body class="bg-neutral-50">
<header class="grad text-white p-6">
  <div class="max-w-5xl mx-auto">
    <h1 class="text-3xl font-bold">Tecnaria Sinapsi — Q/A</h1>
    <p class="opacity-90">Trova la soluzione, in linguaggio Tecnaria.</p>
  </div>
</header>

<main class="max-w-5xl mx-auto p-6 grid grid-cols-1 md:grid-cols-3 gap-4">
  <section class="md:col-span-2">
    <div class="bg-white rounded-2xl shadow p-4">
      <form id="askForm" class="flex gap-2">
        <input id="q" name="q" autofocus
          class="flex-1 border rounded-xl px-4 py-3"
          placeholder="Es. Mi parli della P560? Codici CTF? Differenza CTL vs CTL MAXI?" />
        <button class="px-4 py-3 rounded-xl bg-black text-white">Chiedi a Sinapsi</button>
      </form>
      <div id="meta" class="text-sm text-neutral-500 mt-3"></div>
      <article id="answer" class="prose max-w-none mt-4"></article>
    </div>
  </section>

  <aside class="md:col-span-1">
    <div class="bg-white rounded-2xl shadow p-4">
      <h3 class="font-semibold mb-2">Suggerimenti</h3>
      <div class="flex flex-wrap gap-2">
        <button class="sug">Mi parli della P560?</button>
        <button class="sug">Codici CTF</button>
        <button class="sug">CTL vs CTL MAXI</button>
        <button class="sug">CTF su S275 con lamiera 1×1,5 mm</button>
        <button class="sug">CTCEM: servono resine?</button>
      </div>
    </div>
    <div class="bg-white rounded-2xl shadow p-4 mt-4">
      <h3 class="font-semibold mb-2">Stato</h3>
      <pre class="text-xs" id="state"></pre>
    </div>
  </aside>
</main>

<script>
async function health() {{
  const r = await fetch('/health');
  const j = await r.json();
  document.getElementById('state').textContent = JSON.stringify(j, null, 2);
}}

async function ask(q) {{
  const r = await fetch('/qa/ask?q=' + encodeURIComponent(q));
  const j = await r.json();
  document.getElementById('meta').textContent =
    'Famiglia: ' + (j.family||'-') + ' | Score: ' + (j.score?.toFixed(2)||'') + ' | Endpoint /qa/ask: ok';
  document.getElementById('answer').innerHTML = j.answer_html || '<p class="text-red-600">Nessuna risposta.</p>';
}}

document.getElementById('askForm').addEventListener('submit', (e) => {{
  e.preventDefault();
  const q = document.getElementById('q').value.trim();
  if (!q) return;
  ask(q);
}});

document.querySelectorAll('.sug').forEach(b => {{
  b.addEventListener('click', () => {{
    document.getElementById('q').value = b.textContent;
    document.getElementById('q').focus();
  }});
}});

health();
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTML_PAGE

@app.get("/health")
def health():
    return {
        "title": APP_TITLE,
        "endpoints": {"health": "/health", "ask": "/qa/ask?q=MI%20PARLI%20DELLA%20P560%20%3F"},
        "data_file": str(DATA_PATH),
        "items_loaded": len(ITEMS),
    }

class AskOut(BaseModel):
    family: Optional[str] = None
    score: float = 0.0
    answer_html: str = ""

@app.get("/qa/ask", response_model=AskOut)
def qa_ask(q: str = Query(..., min_length=2)):
    lang = detect_lang(q)
    best = select_best(q, ITEMS)
    if not best:
        fallback = {
            "it": "Domanda poco chiara o fuori ambito Tecnaria. Esempi: “Mi parli della P560?” • “Codici CTF” • “CTL vs CTL MAXI”.",
            "en": "Question unclear or out of Tecnaria scope. Examples: “Tell me about P560” • “CTF codes” • “CTL vs CTL MAXI”.",
            "fr": "Question floue ou hors périmètre Tecnaria. Exemples : « Parlez-moi de la P560 » • « Codes CTF » • « CTL vs CTL MAXI ».",
            "de": "Unklare oder außerhalb des Tecnaria-Rahmens liegende Frage. Beispiele: „Erzählen Sie mir von P560“ • „CTF-Codes“ • „CTL vs CTL MAXI“.",
            "es": "Pregunta poco clara o fuera del ámbito Tecnaria. Ejemplos: «Háblame de P560» • «Códigos CTF» • «CTL vs CTL MAXI».",
        }
        return AskOut(family=None, score=0.0, answer_html=f"<p>{fallback.get(lang,fallback['it'])}</p>")

    fam = best.get("family","").upper()
    # compose final gold answer
    ans = best.get("answer","").strip()
    ans = wrap_gold_answer(ans, fam, lang)
    # simple Markdown → HTML (very light)
    html = (
        ans.replace("\n\n", "</p><p>")
           .replace("\n•", "<br>•")
           .replace("\n- ", "<br>- ")
    )
    html = f"<p>{html}</p>"
    return AskOut(family=fam, score=score_item(q, best), answer_html=html)
