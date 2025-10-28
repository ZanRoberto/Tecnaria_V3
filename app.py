# app.py — Tecnaria GOLD (single-best answer + runtime translation)
# FastAPI app completa, pronta per Render.
# Nessuna dipendenza extra oltre a FastAPI/uvicorn già in uso.

import os, json, re, unicodedata
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ==== Config ====
APP_NAME = "Tecnaria Q/A Service"
BASE_DIR = Path(__file__).parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "static" / "data"))
GOLD_FILE = Path(os.getenv("GOLD_FILE", DATA_DIR / "tecnaria_gold.json"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-turbo")  # puoi cambiarlo da Render
ENABLE_TRANSLATION = True if OPENAI_API_KEY else False

# ==== App ====
app = FastAPI(title=APP_NAME)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

# Monta /static se presente (non obbligatorio)
static_path = BASE_DIR / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# ==== Utils di normalizzazione e scoring (no librerie esterne) ====
def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def norm(text: str) -> str:
    if not text:
        return ""
    text = _strip_accents(text.lower())
    text = re.sub(r"[^a-z0-9\s\-\+\./_]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def tokenize(text: str) -> List[str]:
    return norm(text).split()

# Pesi dei campi per lo scoring
FIELD_WEIGHTS = {
    "q": 3.0,        # domanda
    "title": 3.0,    # eventuale titolo
    "a": 1.8,        # risposta
    "tags": 2.2,     # tag
    "family": 1.5,   # famiglia (CTL/CTF/…)
    "sku": 2.5,      # codici
}

def jaccard(a: List[str], b: List[str]) -> float:
    if not a or not b: return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0

def field_score(q_tokens: List[str], doc_text: str, weight: float) -> float:
    if not doc_text: return 0.0
    d_tokens = tokenize(doc_text)
    base = jaccard(q_tokens, d_tokens)
    return base * weight

def score_item(question: str, item: Dict[str, Any]) -> float:
    q_tokens = tokenize(question)
    s = 0.0
    s += field_score(q_tokens, item.get("q") or item.get("question") or "", FIELD_WEIGHTS["q"])
    s += field_score(q_tokens, item.get("title", ""), FIELD_WEIGHTS["title"])
    s += field_score(q_tokens, item.get("a") or item.get("answer") or "", FIELD_WEIGHTS["a"])
    s += field_score(q_tokens, " ".join(item.get("tags", [])) if isinstance(item.get("tags"), list) else (item.get("tags") or ""), FIELD_WEIGHTS["tags"])
    s += field_score(q_tokens, item.get("family", ""), FIELD_WEIGHTS["family"])
    s += field_score(q_tokens, item.get("sku", ""), FIELD_WEIGHTS["sku"])
    # bonus se combacia la famiglia esplicitamente nominata nella domanda
    fam = (item.get("family") or "").lower()
    if fam and fam in norm(question):
        s *= 1.05
    return s

# ==== Caricamento GOLD ====
GOLD_ITEMS: List[Dict[str, Any]] = []
GOLD_META: Dict[str, Any] = {}

def coerce_item(x: Any) -> Optional[Dict[str, Any]]:
    """
    Accetta strutture varie e le porta a: {id,q,a,tags[],family,sku}
    """
    if not x: return None
    if isinstance(x, dict):
        q = x.get("q") or x.get("question") or x.get("Q") or ""
        a = x.get("a") or x.get("answer") or x.get("A") or ""
        tags = x.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        family = x.get("family") or x.get("famiglia") or ""
        sku = x.get("sku") or x.get("codici") or ""
        title = x.get("title") or x.get("titolo") or ""
        if not (q or a):
            return None
        # ID deterministico breve
        hid = norm(q)[:48] or norm(a)[:48] or "item"
        return {"id": hid, "q": q.strip(), "a": a.strip(), "tags": tags, "family": family, "sku": sku, "title": title}
    # liste/tuple ignorate qui (ci pensa load_gold a iterarle)
    return None

def load_gold():
    global GOLD_ITEMS, GOLD_META
    GOLD_ITEMS, GOLD_META = [], {}
    if not GOLD_FILE.exists():
        return
    with open(GOLD_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # data può essere: lista di item, oppure dict con chiavi varie
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        GOLD_META = {k: v for k, v in data.items() if k not in ("items", "data")}
        if "items" in data and isinstance(data["items"], list):
            items = data["items"]
        elif "data" in data and isinstance(data["data"], list):
            items = data["data"]
        else:
            # fallback: raccogli valori list da chiavi note
            for k in ("ctf", "ctl", "p560", "vcem", "ceme", "gts", "diapason"):
                if isinstance(data.get(k), list):
                    items.extend(data[k])

    seen = set()
    for raw in items:
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            raw = {"q": raw[0], "a": raw[1]}
        item = coerce_item(raw)
        if not item: 
            continue
        key = (norm(item["q"]), norm(item["a"]))
        if key in seen:
            continue
        seen.add(key)
        GOLD_ITEMS.append(item)

load_gold()

# ==== Lingua & Traduzione ====
def detect_lang_heuristic(text: str) -> str:
    """
    Euristica leggera: IT/EN/FR/DE/ES (default: it)
    """
    t = norm(text)
    if not t: return "it"
    # parole frequenti
    en = {"the","and","what","how","can","use","with","for","gun","nail","tool"}
    fr = {"le","la","les","des","est","avec","peux","utiliser","outil","clou"}
    de = {"und","ist","mit","kann","verwenden","werkzeug","nagel"}
    es = {"el","la","los","las","con","puedo","usar","herramienta","clavo"}
    it = {"il","la","le","gli","con","posso","usare","chiodatrice","viti","soletta"}

    toks = set(t.split())
    scores = {
        "en": len(toks & en),
        "fr": len(toks & fr),
        "de": len(toks & de),
        "es": len(toks & es),
        "it": len(toks & it)
    }
    # se ascii con molte parole inglesi
    if max(scores.values()) == 0:
        # fallback: se contiene "¿" o "¡" → es, se contiene "ß/ä/ö/ü" → de, accenti francesi → fr
        raw = text
        if re.search(r"[¿¡]", raw): return "es"
        if re.search(r"[äöüÄÖÜß]", raw): return "de"
        if re.search(r"[éèêàçûùôî]", raw): return "fr"
        if re.search(r"[a-z]", raw.lower()) and not re.search(r"[àèéìòù]", raw.lower()):
            return "en"
        return "it"
    return max(scores.items(), key=lambda kv: kv[1])[0]

def translate_via_openai(text: str, target_lang: str) -> str:
    # Se non c'è chiave, ritorna com'è
    if not ENABLE_TRANSLATION or target_lang == "it":
        return text
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        sys = "Sei un traduttore tecnico del settore edilizio/strutturale. Mantieni precisione, terminologia e tono professionale."
        user = f"Traduci in lingua '{target_lang}' mantenendo formato elenco e grassetti quando presenti:\n\n{text}"
        rsp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role":"system","content":sys},{"role":"user","content":user}],
            temperature=0.2
        )
        out = rsp.choices[0].message.content.strip()
        return out or text
    except Exception as e:
        print("translate_via_openai error:", e)
        return text

def maybe_translate(answer: str, question: str) -> Tuple[str, str]:
    lang = detect_lang_heuristic(question)
    if lang == "it":
        return answer, "it"
    return translate_via_openai(answer, lang), lang

# ==== Motore di ricerca ====
def topk(question: str, k: int = 5) -> List[Tuple[Dict[str,Any], float]]:
    scored = []
    for it in GOLD_ITEMS:
        s = score_item(question, it)
        if s > 0:
            scored.append((it, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    out = []
    seenq = set()
    for it, s in scored:
        keyq = norm(it["q"])
        if keyq in seenq:
            continue
        seenq.add(keyq)
        out.append((it, s))
        if len(out) >= k:
            break
    return out

def best_one(question: str) -> Tuple[Optional[Dict[str,Any]], float]:
    res = topk(question, k=1)
    if not res: return None, 0.0
    return res[0]

# ==== UI (homepage molto semplice, postura "Tecnaria Sinapsi") ====
HOME_HTML = """
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Tecnaria Sinapsi — GOLD</title>
<style>
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu; background:#0b0b0c; color:#f6f6f7; margin:0;}
  .hero{padding:48px 24px; background:linear-gradient(135deg,#ff7a18, #18181b 60%);}
  .wrap{max-width:980px; margin:0 auto;}
  h1{font-size:28px; margin:0 0 8px;}
  .sub{opacity:.85; margin:0 0 24px;}
  .card{background:#151519; border:1px solid #2a2a30; border-radius:16px; padding:16px; box-shadow:0 10px 30px rgba(0,0,0,.25);}
  .row{display:flex; gap:8px;}
  input,button{font-size:16px; border-radius:12px; border:1px solid #31313a; background:#0f0f13; color:#fff; padding:12px 14px;}
  input{flex:1;}
  button{cursor:pointer;}
  .pill{display:inline-block; padding:6px 10px; border-radius:999px; background:#1b1b20; border:1px solid #2a2a30; margin:4px 6px 0 0; font-size:12px;}
  .answer{white-space:pre-wrap; line-height:1.5; margin-top:10px;}
  .muted{opacity:.7; font-size:12px;}
  .ok{color:#7CFC9A;}
  .err{color:#ff7a7a;}
</style>
</head>
<body>
  <div class="hero">
    <div class="wrap">
      <h1>Trova la soluzione, in linguaggio Tecnaria.</h1>
      <p class="sub">GOLD — risposta migliore unica • CTF • CTL • P560 • (VCEM/CEME/GTS/Diapason se presenti nel file)</p>
      <div class="card">
        <div class="row">
          <input id="q" placeholder="Scrivi la tua domanda (es. Mi dai i codici dei CTF?)"/>
          <button onclick="ask()">Chiedi a Sinapsi</button>
        </div>
        <div style="margin-top:8px">
          <span class="pill">CTF</span>
          <span class="pill">CTL / CTL MAXI</span>
          <span class="pill">P560 (taratura, DPI)</span>
          <span class="pill">Codici / ordini</span>
          <span class="pill">Lamiera grecata</span>
        </div>
        <div id="status" class="muted" style="margin-top:10px">Endpoint <code>/qa/ask</code>: <span id="h" class="ok">health?</span></div>
        <div id="out" class="answer"></div>
      </div>
    </div>
  </div>
<script>
async function health(){
  try{
    const r = await fetch('/health'); const j = await r.json();
    document.getElementById('h').textContent = (j.status==='ok' ? 'ok' : 'errore');
    if(j.status!=='ok'){ document.getElementById('h').className='err'; }
  }catch(e){ document.getElementById('h').textContent='errore'; document.getElementById('h').className='err'; }
}
health();

async function ask(){
  const q = document.getElementById('q').value.trim();
  if(!q){return;}
  document.getElementById('out').textContent = '...';
  const r = await fetch('/qa/ask',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({question:q})});
  const j = await r.json();
  if(j.error){ document.getElementById('out').textContent = 'Errore: '+j.error; return; }
  const meta = `Famiglia: ${j.family||'-'} | Score: ${j.score?.toFixed(3)||'-'} | Lingua: ${j.lang}`;
  document.getElementById('out').innerHTML =
    `<div class='muted'>${meta}</div>\n\n<b>Risposta Migliore</b>\nQ: ${escapeHtml(j.question)}\n\n${j.answer}`;
}
function escapeHtml(s){ return s.replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(HOME_HTML)

@app.get("/health")
def health():
    status = "ok" if GOLD_ITEMS else "empty"
    return {"service": APP_NAME, "status": status, "items_loaded": len(GOLD_ITEMS), "data_dir": str(DATA_DIR), "file": str(GOLD_FILE.name)}

# ==== Core endpoints ====

@app.post("/qa/ask")
async def qa_ask(payload: Dict[str, Any]):
    try:
        q = (payload.get("question") or "").strip()
        if not q:
            return JSONResponse({"error":"question vuota"}, status_code=400)
        item, sc = best_one(q)
        if not item:
            # fallback “did you mean”
            return {"question": q, "answer": "Non ho trovato una risposta precisa nei GOLD. Prova a riformulare con i termini del prodotto (es. CTF, CTL, P560).", "lang": "it", "score": 0.0}
        # Risposta migliore unica
        answer = item.get("a") or item.get("answer") or ""
        # traduzione runtime se serve
        answer_t, lang = maybe_translate(answer, q)
        return {
            "question": q,
            "answer": answer_t,
            "lang": lang,
            "score": sc,
            "id": item.get("id"),
            "family": item.get("family",""),
            "sku": item.get("sku",""),
            "tags": item.get("tags",[])
        }
    except Exception as e:
        return JSONResponse({"error": f"server error: {e}"}, status_code=500)

@app.post("/qa/search")
async def qa_search(payload: Dict[str, Any]):
    q = (payload.get("question") or "").strip()
    k = int(payload.get("k", 5))
    if not q:
        return JSONResponse({"error":"question vuota"}, status_code=400)
    res = topk(q, k=k)
    out = []
    for it, sc in res:
        out.append({
            "id": it.get("id"),
            "q": it.get("q"),
            "a": it.get("a")[:600],
            "score": sc,
            "family": it.get("family",""),
            "tags": it.get("tags",[]),
            "sku": it.get("sku","")
        })
    return {"question": q, "results": out, "count": len(out)}

# ==== Reload dati (facoltativo, utile in sviluppo) ====
@app.post("/admin/reload")
def admin_reload():
    load_gold()
    return {"status":"ok", "items_loaded": len(GOLD_ITEMS), "file": str(GOLD_FILE)}

# ==== Avvio locale ====
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
