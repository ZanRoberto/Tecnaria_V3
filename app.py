# app.py — Tecnaria_V3 (Render-ready, UI inclusa)

from typing import List, Dict, Any
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
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

# === CSV robusto (UTF-8/CP1252 + fix mojibake) ===
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
        _read("utf-8-sig")      # preferito (gestisce anche BOM)
    except Exception:
        try:
            _read("cp1252")     # fallback per file salvati in Windows
        except Exception:
            return rows

    # normalizza artefatti comuni (— ’ … accenti, euro, ecc.)
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
FAQ_BY_LANG: Dict[str, List[Dict[str, str]]] = {}
for r in FAQ_ITEMS:
    FAQ_BY_LANG.setdefault(r["lang"], []).append(r)

def detect_lang(q: str) -> str:
    s = (q or "").lower()
    if any(w in s for w in [" the ", " what ", " how ", " can ", " shall ", " should ", " is "]): return "en"
    if any(w in s for w in [" el ", " los ", " las ", "¿", "qué", "como", "cómo", " es "]): return "es"
    if any(w in s for w in [" le ", " la ", " les ", " quelle", " comment", " est "]): return "fr"
    if any(w in s for w in [" der ", " die ", " das ", " wie ", " was ", " ist "]): return "de"
    return "it"

# Token famiglie (no “traliccio” — NON Tecnaria)
FAM_TOKENS: Dict[str, List[str]] = {
    "CTF":   ["ctf","lamiera","p560","hsbr14","trave","chiodatrice","sparo"],
    "CTL":   ["ctl","soletta","calcestruzzo","collaborazione","legno"],
    "VCEM":  ["vcem","preforo","vite","legno","essenze","durezza","hardwood","predrill","pilot","70","80"],
    "CEM-E": ["ceme","laterocemento","secco","senza resine","cappello"],
    "CTCEM": ["ctcem","laterocemento","secco","senza resine","cappa"],
    "GTS":   ["gts","manicotto","filettato","giunzioni","secco","sleeve","thread"],
    "P560":  ["p560","chiodatrice","propulsori","hsbr14","nailer","cartridges"],
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
def intent_route(q: str) -> Dict[str, Any]:
    ql = (q or "").lower().strip()
    lang = detect_lang(ql)

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

    # 2) Famiglia singola
    scored = [(fam, _score_tokens(ql, toks)) for fam, toks in FAM_TOKENS.items()]
    scored.sort(key=lambda x: x[1], reverse=True)
    fam, s = scored[0]
    if s >= 0.2:
        # 2a) FAQ
        for r in FAQ_BY_LANG.get(lang, []):
            keys = (r["tags"] or "") + " " + r["question"]
            if _score_tokens(ql, re.split(r"[,\s;/\-]+", keys.lower())) >= 0.25:
                return {
                    "ok": True, "match_id": r["id"] or f"FAQ::{fam}", "lang": lang,
                    "family": fam, "intent": "faq", "source": "faq", "score": 88.0,
                    "text": r["answer"], "html": ""
                }
        # 2b) overview fallback
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
# /api/ask locale (GET + POST)
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
def api_ask_post(body: AskIn) -> AskOut:
    t0 = time.time()
    routed = intent_route(body.q or "")
    ms = int((time.time() - t0) * 1000)
    return AskOut(
        ok=True, match_id=str(routed.get("match_id") or "<NULL>"),
        ms=ms if ms > 0 else 1,
        text=str(routed.get("text") or ""), html=str(routed.get("html") or ""),
        lang=routed.get("lang"), family=routed.get("family"),
        intent=routed.get("intent"), source=routed.get("source"),
        score=routed.get("score"),
    )

@app.get("/api/ask", response_model=AskOut)
def api_ask_get(q: str = Query(default="")) -> AskOut:
    t0 = time.time()
    routed = intent_route(q or "")
    ms = int((time.time() - t0) * 1000)
    return AskOut(
        ok=True, match_id=str(routed.get("match_id") or "<NULL>"),
        ms=ms if ms > 0 else 1,
        text=str(routed.get("text") or ""), html=str(routed.get("html") or ""),
        lang=routed.get("lang"), family=routed.get("family"),
        intent=routed.get("intent"), source=routed.get("source"),
        score=routed.get("score"),
    )

# -------------------------------------------------
# Interfaccia Web /ui (responsive + microfono opzionale)
# -------------------------------------------------
@app.get("/ui", response_class=HTMLResponse)
def ui_page():
    return """
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Tecnaria · Q&A</title>
<style>
  :root{
    --bg:#0b0b0c; --panel:#121214; --muted:#1b1c20;
    --brand:#FF6A00; --brand-2:#ffa149;
    --text:#f6f7f9; --text-dim:#b9bdc7;
    --ok:#18c37e; --radius:18px; --shadow:0 10px 30px rgba(0,0,0,.35);
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{
    margin:0;
    background: radial-gradient(1200px 1200px at 120% -10%, rgba(255,106,0,.16), transparent 60%) , var(--bg);
    color:var(--text);
    font: 16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial;
    display:flex; flex-direction:column; gap:22px;
  }
  header{
    position:sticky; top:0; z-index:10; backdrop-filter: blur(6px);
    background: linear-gradient(180deg, rgba(11,11,12,.85), rgba(11,11,12,.55) 70%, transparent);
    border-bottom:1px solid rgba(255,255,255,.06);
  }
  .wrap{max-width:1100px; margin:0 auto; padding:18px 18px 8px}
  .brand{display:flex; align-items:center; gap:12px}
  .dot{width:12px; height:12px; border-radius:50%; background:var(--ok); box-shadow:0 0 0 4px rgba(24,195,126,.15)}
  h1{margin:0; font-size:1.05rem; letter-spacing:.3px; font-weight:650}
  main{max-width:1100px; margin:0 auto; padding:0 18px 24px}
  .card{
    background:linear-gradient(180deg, rgba(255,255,255,.03), rgba(255,255,255,.01));
    border:1px solid rgba(255,255,255,.06); border-radius: var(--radius);
    box-shadow: var(--shadow);
  }
  .bar{display:flex; gap:10px; padding:12px; border-bottom:1px solid rgba(255,255,255,.06)}
  .pill{
    background: linear-gradient(135deg, var(--brand), var(--brand-2));
    color:#111; font-weight:700; border:none; border-radius: 999px;
    padding:10px 16px; cursor:pointer; transition: transform .06s ease;
  }
  .pill:active{ transform: scale(.98) }
  .ghost{ background:transparent; color:var(--text); border:1px solid rgba(255,255,255,.12)}
  .ghost:hover{ border-color: rgba(255,255,255,.22)}
  .io{ display:grid; grid-template-columns: 1.2fr .8fr; gap:16px; padding:16px; }
  @media (max-width: 900px){ .io{ grid-template-columns: 1fr } }
  .pane{padding:14px; border:1px solid rgba(255,255,255,.06); border-radius:calc(var(--radius) - 6px); background:var(--panel)}
  .pane h3{ margin:0 0 10px; font-size:.95rem; color:var(--text-dim); font-weight:600; letter-spacing:.2px}
  textarea{
    width:100%; min-height:110px; resize:vertical; border-radius:12px; border:1px solid rgba(255,255,255,.12);
    background:var(--muted); color:var(--text); padding:12px 44px 12px 12px; outline:none;
  }
  .out{ min-height:140px; white-space:pre-wrap }
  .meta{ font: 12px/1.3 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono"; color:var(--text-dim)}
  .status{ display:flex; gap:10px; align-items:center; padding:10px 12px; border-top:1px dashed rgba(255,255,255,.08)}
  .kbd{ font: 12px/1.2 ui-monospace; background:rgba(255,255,255,.06); padding:2px 6px; border-radius:6px; border:1px solid rgba(255,255,255,.12) }
  .tag{ display:inline-block; font-size:11px; padding:3px 8px; border-radius:8px; border:1px solid rgba(255,255,255,.16); margin-right:6px; color:var(--text-dim)}
  .actions{ display:flex; gap:10px; justify-content:flex-end; padding:0 12px 12px}
  .btn{ background:linear-gradient(135deg, var(--brand), var(--brand-2)); color:#111; font-weight:800; border:none; padding:10px 16px; border-radius:12px; cursor:pointer}
  .btn.sec{ background:transparent; color:var(--text); border:1px solid rgba(255,255,255,.16)}
  .mic{
    position:absolute; right:16px; bottom:16px; width:38px; height:38px; border-radius:50%;
    display:grid; place-items:center; border:1px solid rgba(255,255,255,.16); background:rgba(255,255,255,.06); cursor:pointer;
  }
  .mic.on{ outline: 3px solid rgba(255,106,0,.35); background:rgba(255,106,0,.2); border-color:rgba(255,106,0,.6) }
  a{color:var(--brand-2); text-decoration:none}
  .small{font-size:.9rem; color:var(--text-dim)}
</style>
</head>
<body>
  <header>
    <div class="wrap">
      <div class="brand">
        <div class="dot"></div>
        <h1>Tecnaria · Q&A</h1>
      </div>
    </div>
  </header>

  <main>
    <div class="card">
      <div class="bar">
        <button id="btnGet" class="pill ghost" title="GET /api/ask?q=...">GET</button>
        <button id="btnPost" class="pill" title="POST /api/ask">POST</button>
        <div style="flex:1"></div>
        <span class="small">Microfono: <span id="micState" class="tag">spento</span></span>
      </div>

      <div class="io">
        <!-- INPUT -->
        <div class="pane">
          <h3>Domanda</h3>
          <div style="position:relative">
            <textarea id="q" placeholder="Es. Differenza tra CTF e CTL?"></textarea>
            <button id="mic" class="mic" title="Dettatura vocale (opzionale)">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M12 14a3 3 0 0 0 3-3V7a3 3 0 0 0-6 0v4a3 3 0 0 0 3 3Z" stroke="currentColor" stroke-width="1.8"/>
                <path d="M19 11a7 7 0 0 1-14 0M12 18v4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
              </svg>
            </button>
          </div>

          <div class="actions">
            <button id="example1" class="btn sec">Esempio: CTF vs CTL</button>
            <button id="example2" class="btn sec">Esempio: VCEM preforo</button>
            <button id="go" class="btn">Chiedi</button>
          </div>
        </div>

        <!-- OUTPUT -->
        <div class="pane">
          <h3>Risposta Tecnaria</h3>
          <div id="out" class="out"></div>
          <div id="html" class="out"></div>
          <div class="status">
            <span class="meta">match_id: <span id="mid">—</span></span>
            <span class="meta" style="margin-left:10px">famiglia: <span id="fam">—</span></span>
            <span class="meta" style="margin-left:10px">intent: <span id="int">—</span></span>
            <div style="flex:1"></div>
            <span class="meta"><span id="ms">0</span> ms</span>
          </div>
        </div>
      </div>
    </div>

    <p class="small" style="margin-top:10px">
      Suggerimenti: prova <span class="kbd">Differenza tra CTF e CTL?</span>,
      <span class="kbd">P560: è un connettore o un'attrezzatura?</span>,
      <span class="kbd">VCEM su essenze dure: serve preforo?</span>
      • API docs: <a href="/health">/health</a>, <a href="/api/ask">/api/ask</a>
    </p>
  </main>

<script>
(() => {
  const base = location.origin; // stesso dominio (Render)
  const $ = sel => document.querySelector(sel);
  const q = $("#q"), out = $("#out"), html = $("#html");
  const mid = $("#mid"), fam = $("#fam"), intn = $("#int"), ms = $("#ms");
  const btnGet = $("#btnGet"), btnPost = $("#btnPost"), go = $("#go");
  const example1 = $("#example1"), example2 = $("#example2");
  const micBtn = $("#mic"), micState = $("#micState");

  // ------- MICROFONO (opzionale) -------
  let rec = null, recOn = false;
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition || null;

  function micSupported(){
    return !!SR && (location.protocol === "https:" || location.hostname === "localhost");
  }
  function toggleMic(){
    if(!micSupported()){
      alert("Il microfono richiede un browser compatibile e HTTPS.");
      return;
    }
    if(recOn){ rec.stop(); return; }
    rec = new SR();
    const lang = guessLang(q.value);
    rec.lang = (lang === "en" ? "en-US" :
                lang === "fr" ? "fr-FR" :
                lang === "es" ? "es-ES" :
                lang === "de" ? "de-DE" : "it-IT");
    rec.interimResults = true; rec.continuous = false;
    rec.onstart = () => setMic(true);
    rec.onerror = () => setMic(false);
    rec.onend = () => setMic(false);
    rec.onresult = (e) => {
      let final = "";
      for (let i = e.resultIndex; i < e.results.length; i++){
        const tr = e.results[i][0].transcript;
        if(e.results[i].isFinal) final += tr;
      }
      if(final){ q.value = (q.value.trim() ? q.value + " " : "") + final.trim(); }
    };
    rec.start();
  }
  function setMic(on){
    recOn = on;
    micBtn.classList.toggle("on", on);
    micState.textContent = on ? "acceso" : "spento";
  }
  micBtn.addEventListener("click", toggleMic);
  if(!micSupported()){
    micBtn.title = "Non supportato (richiede HTTPS + browser compatibile)";
    micState.textContent = "non disponibile";
  }

  // ------- Helpers -------
  function guessLang(text){
    const s = (text || "").toLowerCase();
    if(/\b(what|how|can|should|when|difference)\b/.test(s)) return "en";
    if(/[¿¡]|(qué|cómo|cuando)/.test(s)) return "es";
    if(/\b(quoi|comment|quand|pourquoi)\b|[àâçéèêëîïôûùüÿœ]/.test(s)) return "fr";
    if(/\b(was|wie|wann|warum)\b|[äöüß]/.test(s)) return "de";
    return "it";
  }
  function show(r){
    out.textContent = r.text || "";
    html.innerHTML = r.html || "";
    mid.textContent = r.match_id || "—";
    fam.textContent = r.family || "—";
    intn.textContent = r.intent || "—";
    ms.textContent = r.ms || 0;
  }
  async function askPOST(){
    const body = { q: q.value || "" };
    const t0 = performance.now();
    const res = await fetch(base + "/api/ask", {
      method:"POST", headers:{ "Content-Type":"application/json" },
      body: JSON.stringify(body)
    });
    const r = await res.json();
    r.ms = Math.max(1, Math.round(performance.now()-t0));
    show(r);
  }
  async function askGET(){
    const url = base + "/api/ask?q=" + encodeURIComponent(q.value || "");
    const t0 = performance.now();
    const res = await fetch(url);
    const r = await res.json();
    r.ms = Math.max(1, Math.round(performance.now()-t0));
    show(r);
  }
  go.addEventListener("click", askPOST);
  btnPost.addEventListener("click", askPOST);
  btnGet.addEventListener("click", askGET);
  example1.addEventListener("click", () => { q.value="Differenza tra CTF e CTL?"; askGET(); });
  example2.addEventListener("click", () => { q.value="VCEM su essenze dure: serve preforo 70–80%?"; askPOST(); });
  q.addEventListener("keydown", (e) => { if(e.key === "Enter" && (e.metaKey || e.ctrlKey)) { askPOST(); } });
})();
</script>
</body>
</html>
    """
