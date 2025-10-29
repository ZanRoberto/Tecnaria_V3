import os, json, re, html
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

TITLE = "Tecnaria Sinapsi — Q/A"
DATA_PATH = os.environ.get("TECNARIA_DATA", "static/data/tecnaria_gold.json")

app = FastAPI(title=TITLE)

GOLD_ITEMS: List[Dict[str, Any]] = []
FAMILIES = set()

# -----------------------------
# Utilities
# -----------------------------
def normalize_item(it: Dict[str, Any]) -> Dict[str, Any]:
    out = {
        "family": it.get("family","").strip().upper(),
        "tags": sorted(list({t.strip().lower() for t in it.get("tags", []) if t.strip()})),
        "questions": [q.strip() for q in it.get("questions",[]) if isinstance(q,str) and q.strip()],
        "answer": it.get("answer","").strip(),
        # opzionali per traduzione pragmatica:
        "answer_en": it.get("answer_en","").strip(),
        "answer_fr": it.get("answer_fr","").strip(),
        "answer_de": it.get("answer_de","").strip(),
        "answer_es": it.get("answer_es","").strip(),
    }
    return out

def load_dataset(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = []
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    for it in data:
        items.append(normalize_item(it))
    return items

def tokenize(s: str) -> List[str]:
    return re.findall(r"[a-zA-ZàèéìòóùÀÈÉÌÒÓÙ0-9]+", s.lower())

def detect_lang(q: str) -> str:
    ql = q.lower()
    # euristica minimal: rileva EN/FR/DE/ES
    en = any(w in ql for w in ["what", "how", "can", "difference", "which", "vs"])
    fr = any(w in ql for w in ["quelle", "comment", "peut-on", "différence"])
    de = any(w in ql for w in ["was", "wie", "unterschied", "kann", "zwischen"])
    es = any(w in ql for w in ["qué", "cómo", "puedo", "diferencia", "entre"])
    if fr: return "fr"
    if de: return "de"
    if es: return "es"
    if en: return "en"
    return "it"

def format_answer(item: Dict[str,Any], lang: str) -> str:
    # seleziona lingua se disponibile
    ans = item.get("answer","")
    if lang == "en" and item.get("answer_en"): ans = item["answer_en"]
    if lang == "fr" and item.get("answer_fr"): ans = item["answer_fr"]
    if lang == "de" and item.get("answer_de"): ans = item["answer_de"]
    if lang == "es" and item.get("answer_es"): ans = item["answer_es"]

    # garantisce struttura GOLD: se già narrativa, lascia; altrimenti imposta baseline
    gold = ans.strip()
    if not gold:
        gold = (
            "**Contesto** Risposta non disponibile.\n\n"
            "**Istruzioni/Scelta** Consultare documentazione Tecnaria.\n\n"
            "**Errori comuni** —\n\n"
            "**Checklist** —\n\n"
            "*Nota RAG: risposte filtrate su prodotti Tecnaria; no marchi terzi.*"
        )
    return gold

# priorità famiglie per trigger diretti
FAMILY_ALIAS = {
    "P560": {"p560", "spit p560", "chiodatrice", "sparo", "propulsori", "hsbr14"},
    "CTF": {"ctf", "acciaio", "trave", "hsbr14", "lamiera", "s275", "s355"},
    "CTL": {"ctl", "legno", "soletta", "trave legno"},
    "CTL MAXI": {"maxi", "ctl maxi", "tavolato", "assito"},
    "CTCEM": {"ctcem", "laterocemento", "piastra dentata"},
    "VCEM": {"vcem", "laterocemento", "preforo"},
    "DIAPASON": {"diapason"},
    "GTS": {"gts", "manicotti", "tiranti"},
    "ACCESSORI": {"accessori", "viti", "chiodi", "kit", "cartucce"},
}

COMPARATORS = {
    "CTL vs CTL MAXI": ({"ctl"}, {"maxi","ctl maxi"}),
    "CTL vs CTF": ({"ctl"}, {"ctf"}),
    "CTCEM vs VCEM": ({"ctcem"}, {"vcem"}),
    "P560 vs generiche": ({"p560"}, {"chiodatrice generica","generica"}),
}

def score_item(q: str, item: Dict[str,Any]) -> float:
    q_tokens = set(tokenize(q))
    score = 0.0

    # match su domande registrate
    for qq in item.get("questions",[]):
        overlap = len(q_tokens & set(tokenize(qq)))
        score += overlap * 1.5

    # match su tag/keywords (boost)
    tagset = set(item.get("tags",[]))
    overlap_tags = len(q_tokens & tagset)
    score += overlap_tags * 2.0

    # boost per famiglia se trigger presente
    fam = item.get("family","")
    triggers = FAMILY_ALIAS.get(fam, set())
    if len(q_tokens & {t.lower() for t in triggers})>0:
        score += 3.0

    # boost comparazioni
    for name,(a,b) in COMPARATORS.items():
        if (q_tokens & a) and (q_tokens & b):
            # se l'item contiene entrambi i gruppi nei tag → più alto
            if set(a|b).issubset(set(tagset)):
                score += 4.0
            else:
                score += 2.0

    # piccola penalità se famiglia molto distante da trigger presenti
    fam_hits = 1 if len(q_tokens & {t.lower() for t in triggers})>0 else 0
    if fam_hits==0 and fam in ("P560","CTF") and ("p560" in q_tokens or "ctf" in q_tokens):
        score -= 0.5

    return score

def is_off_domain(q: str) -> bool:
    # se non troviamo nessun trigger Tecnaria e nessun token noto, blocchiamo
    tok = set(tokenize(q))
    tecnaria_tokens = {"ctf","ctl","maxi","p560","ctcem","vcem","diapason","gts","tecnaria","connettore","lamiera","soletta"}
    return len(tok & tecnaria_tokens) == 0

# -----------------------------
# Bootstrap
# -----------------------------
def bootstrap():
    global GOLD_ITEMS, FAMILIES
    GOLD_ITEMS = load_dataset(DATA_PATH)
    FAMILIES = {it["family"] for it in GOLD_ITEMS if it["family"]}
    # normalizzazione tag per matching
    for it in GOLD_ITEMS:
        it["tags"] = [t.lower() for t in it.get("tags",[])]
bootstrap()

# -----------------------------
# UI
# -----------------------------
UI_HTML = f"""
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{html.escape(TITLE)}</title>
<style>
  body {{
    margin:0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
    background: linear-gradient(135deg,#ff7a18 0%, #111 60%);
    color:#111;
  }}
  .wrap{{max-width:1000px;margin:0 auto;padding:32px}}
  .hero{{background:#fff; border-radius:20px; padding:28px 28px 20px; box-shadow:0 10px 30px rgba(0,0,0,.15)}}
  h1{{margin:0;font-size:28px}}
  .subtitle{{color:#555;margin-top:6px}}
  .bar{{margin-top:18px; display:flex; gap:10px}}
  input[type=text]{{flex:1;padding:14px;border-radius:12px;border:1px solid #ddd;font-size:16px}}
  button{{padding:14px 18px;border-radius:12px;border:0;background:#111;color:#fff;font-weight:600;cursor:pointer}}
  .pills{{display:flex;flex-wrap:wrap; gap:10px; margin-top:12px}}
  .pill{{background:#111;color:#fff;border-radius:999px;padding:8px 12px;font-size:13px;cursor:pointer}}
  .panel{{margin-top:16px;background:#fff;border-radius:16px;padding:18px; box-shadow:0 10px 30px rgba(0,0,0,.15)}}
  .muted{{color:#666;font-size:13px}}
  pre {{white-space:pre-wrap;}}
  .kv{{display:flex; gap:14px; flex-wrap:wrap; margin:6px 0 0}}
  .kv span{{background:#f3f3f3; border-radius:8px; padding:6px 8px; font-size:12px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1>Tecnaria Sinapsi — Q/A</h1>
    <div class="subtitle">Bot ufficiale — risposte GOLD su CTF, CTL/CTL MAXI, P560, CTCEM/VCEM, DIAPASON, GTS, ACCESSORI.</div>
    <div class="bar">
      <input id="q" type="text" placeholder="Chiedi a Sinapsi… Es: Mi parli della P560? Differenza CTL e CTL MAXI?" />
      <button onclick="ask()">Chiedi</button>
    </div>
    <div class="pills">
      <div class="pill" onclick="preset('Mi parli della P560?')">P560 (istruzioni)</div>
      <div class="pill" onclick="preset('Codici dei CTF?')">Codici CTF</div>
      <div class="pill" onclick="preset('CTL vs CTL MAXI?')">Confronto CTL</div>
      <div class="pill" onclick="preset('CTCEM: servono resine?')">CTCEM resine?</div>
      <div class="pill" onclick="preset('Posa CTF su S235 con lamiera 2×1,0 H75?')">CTF + lamiera</div>
    </div>
  </div>

  <div class="panel">
    <div id="meta" class="muted">Endpoint <code>/qa/ask</code> • Health: <span id="hstatus">…</span></div>
    <div id="result" style="margin-top:10px"></div>
  </div>
</div>

<script>
async function health(){ const r = await fetch('/health'); document.getElementById('hstatus').innerText = r.ok ? 'ok' : 'ko'; }
function preset(t){ document.getElementById('q').value=t; ask(); }
async function ask(){
  const q = document.getElementById('q').value.trim();
  if(!q){ return; }
  const r = await fetch('/qa/ask?q='+encodeURIComponent(q));
  const js = await r.json();
  const fam = js.family ? js.family : 'N/D';
  const score = js.score !== undefined ? js.score.toFixed(2) : 'N/D';
  document.getElementById('result').innerHTML =
    `<div class="kv"><span><b>Famiglia:</b> ${fam}</span><span><b>Score:</b> ${score}</span></div>` +
    `<h3>Risposta Migliore</h3><pre>${js.answer}</pre>`;
}
health();
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return UI_HTML

# -----------------------------
# API
# -----------------------------
@app.get("/health")
def health():
    exists = os.path.exists(DATA_PATH)
    size = os.path.getsize(DATA_PATH) if exists else 0
    return {
        "title": TITLE,
        "endpoints": {"health": "/health", "ask": "/qa/ask?q=MI%20PARLI%20DELLA%20P560%20%3F"},
        "data_file": DATA_PATH,
        "dataset_exists": exists,
        "dataset_size_bytes": size,
        "items_loaded": len(GOLD_ITEMS),
        "families": sorted(list(FAMILIES)),
        "routes": ["/","/health","/qa/ask","/ask","/debug"]
    }

@app.get("/debug")
def debug():
    from collections import Counter
    fam_count = Counter([it["family"] for it in GOLD_ITEMS])
    top = fam_count.most_common(5)
    empties = sum(1 for it in GOLD_ITEMS if not it.get("answer"))
    return {"top_families": top, "items": len(GOLD_ITEMS), "empty_answers": empties}

@app.get("/qa/ask")
def qa_ask(q: str = Query(..., min_length=2)):
    q_stripped = q.strip()
    # filtro dominio
    if is_off_domain(q_stripped):
        return {
            "family": None,
            "score": 0,
            "answer": ("Sono il bot ufficiale **Tecnaria** (Bassano del Grappa). "
                       "Rispondo solo a domande su prodotti e sistemi Tecnaria (CTF, CTL/CTL MAXI, P560, CTCEM/VCEM, DIAPASON, GTS, ACCESSORI).")
        }

    if not GOLD_ITEMS:
        return {"family": None, "score": 0,
                "answer": "Dataset non caricato. Verifica `static/data/tecnaria_gold.json`."}

    # ranking
    ranked = sorted(
        ((score_item(q_stripped, it), it) for it in GOLD_ITEMS),
        key=lambda x: x[0],
        reverse=True
    )
    top_score, top_item = ranked[0]
    lang = detect_lang(q_stripped)
    ans = format_answer(top_item, lang)

    return {
        "family": top_item.get("family"),
        "score": float(top_score),
        "answer": ans
    }

@app.get("/ask")
def ask_alias(q: str = Query(..., min_length=2)):
    return qa_ask(q)

# hot-reload dataset (facoltativo)
@app.get("/admin/reload")
def admin_reload():
    bootstrap()
    return {"reloaded": True, "items_loaded": len(GOLD_ITEMS)}
