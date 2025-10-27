# app.py
from __future__ import annotations
import orjson, csv, re, unicodedata
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from fastapi import FastAPI, Query, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse

APP = FastAPI(title="Tecnaria_V3")

# ---------- Utils ----------
def normalize(txt: str) -> str:
    if not txt:
        return ""
    t = txt.lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s\-/+]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def to_json(obj) -> JSONResponse:
    return Response(orjson.dumps(obj), media_type="application/json")

# ---------- Data model ----------
DATA_DIR = Path("./data")

class Row:
    def __init__(self, d: Dict[str,str]):
        self.match_id = d.get("match_id","").strip()
        self.intent   = d.get("intent","").strip() or "faq"
        self.family   = d.get("famiglia","").strip()
        self.lang     = d.get("lang","").strip() or "it"
        self.text     = d.get("text","").strip()
        self.html     = d.get("html","").strip()
        self.source   = d.get("source","").strip() or "faq"
        self.score    = float(d.get("score", "100") or 100)

    def as_dict(self):
        return dict(match_id=self.match_id, intent=self.intent, family=self.family,
                    lang=self.lang, text=self.text, html=self.html,
                    source=self.source, score=self.score)

# Knowledge containers
FAQ_ROWS: List[Row] = []
OVERVIEW: Dict[str, str] = {}  # family -> overview HTML/Text
CODES: Dict[str, List[str]] = {} # family -> list of codes/models
ALIASES: Dict[str, str] = {}  # alias token -> family key

# ---------- Load CSVs ----------
def load_all():
    global FAQ_ROWS, OVERVIEW, CODES, ALIASES
    FAQ_ROWS, OVERVIEW, CODES, ALIASES = [], {}, {}, {}
    if not DATA_DIR.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    csv_paths = sorted(list(DATA_DIR.glob("*.csv")))
    for p in csv_paths:
        with p.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                d = {k.strip(): (v or "").strip() for k,v in raw.items()}
                row = Row(d)

                # Overview rows: match_id should be like OVERVIEW::CTF
                if row.match_id.upper().startswith("OVERVIEW::"):
                    fam = row.match_id.split("::",1)[1].strip().upper()
                    OVERVIEW[fam] = row.html or row.text
                    continue

                # Codes rows: match_id like CODES::CTF  text = "CTF020;CTF025;CTF030;..."
                if row.match_id.upper().startswith("CODES::"):
                    fam = row.match_id.split("::",1)[1].strip().upper()
                    codes = [c.strip() for c in (row.text or "").replace(",", ";").split(";") if c.strip()]
                    if codes: CODES[fam] = codes
                    continue

                # Aliases rows: match_id like ALIAS::CTF  text="connettori ctf|pioli ctf|...”
                if row.match_id.upper().startswith("ALIAS::"):
                    fam = row.match_id.split("::",1)[1].strip().upper()
                    toks = re.split(r"[|;,\s]+", row.text or "")
                    for t in [normalize(t) for t in toks if t.strip()]:
                        ALIASES[t] = fam
                    continue

                # Normal FAQ / Compare rows
                FAQ_ROWS.append(row)

    # Safety defaults (if CSV non presente)
    ALIASES.update({
        "ctf":"CTF","connettori ctf":"CTF","p560":"P560","spit p560":"P560",
        "ctl":"CTL","ctcem":"CTCEM","cem-e":"CEM-E","cem e":"CEM-E","vcem":"VCEM"
    })
    if "CTF" not in OVERVIEW:
        OVERVIEW["CTF"] = ("<p>Solai misti acciaio–calcestruzzo su travi in acciaio e/o lamiera grecata. "
                           "Posa a freddo con SPIT P560 e due chiodi HSBR14 per connettore.</p>")
    if "CTL" not in OVERVIEW:
        OVERVIEW["CTL"] = ("<p>Solai legno–calcestruzzo su travi/tavolato in legno. "
                           "Posa con viti Ø10 dall’alto; soletta ≥5 cm con rete a metà spessore.</p>")
    if "CTF" not in CODES:
        CODES["CTF"] = ["CTF020","CTF025","CTF030","CTF040"]
    if "CTL" not in CODES:
        CODES["CTL"] = ["CTL 12/030","CTL 12/040","CTL MAXI 12/040","CTL MAXI 12/050"]

load_all()

# ---------- NLP-ish helpers ----------
from rapidfuzz import process, fuzz

COMPARE_TRIGGERS = {"differenza","differenze","vs","confronto","confrontare","meglio di","contro"}
CODES_TRIGGERS   = {"codici","codice","modelli","sigle","catalogo","gamma","misure","varianti","altezze"}
P560_TRIGGERS    = {"p560","spit p560"}

def extract_families(q_norm: str) -> List[str]:
    fams = []
    for tok in re.split(r"[\s/,+\-]+", q_norm):
        if tok in ALIASES:
            fam = ALIASES[tok]
            if fam not in fams:
                fams.append(fam)
    # fallback su maiuscole classiche presenti nel testo
    for fam in ["CTF","CTL","CEM-E","CTCEM","VCEM","P560"]:
        key = normalize(fam)
        if key in q_norm and fam not in fams:
            fams.append(fam)
    return fams

def looks_like_compare(q_norm: str) -> bool:
    return any(t in q_norm for t in COMPARE_TRIGGERS)

def looks_like_codes(q_norm: str) -> bool:
    return any(t in q_norm for t in CODES_TRIGGERS)

def looks_like_p560(q_norm: str) -> bool:
    return any(t in q_norm for t in P560_TRIGGERS)

# ---------- Answer builders ----------
def render_compare(f1: str, f2: str) -> Dict:
    o1 = OVERVIEW.get(f1, "<p>(nessuna scheda)</p>")
    o2 = OVERVIEW.get(f2, "<p>(nessuna scheda)</p>")
    html = f"""
    <div>
      <h2>Confronto</h2>
      <div style='display:flex;gap:28px;flex-wrap:wrap'>
        <div class='side' style='flex:1;min-width:320px'>
          <h3>{f1}</h3>
          {o1}
          <p><small>Fonte: OVERVIEW::{f1}</small></p>
        </div>
        <div class='side' style='flex:1;min-width:320px'>
          <h3>{f2}</h3>
          {o2}
          <p><small>Fonte: OVERVIEW::{f2}</small></p>
        </div>
      </div>
    </div>
    """
    return dict(ok=True, match_id=f"COMPARE::{f1}_VS_{f2}", text="", html=html,
                lang="it", family=f"{f1}+{f2}", intent="compare", ms=1, score=92.0)

def render_codes(fam: str) -> Dict:
    codes = CODES.get(fam, [])
    bullet = "".join(f"<li><code>{c}</code></li>" for c in codes) or "<li>(non disponibili)</li>"
    hint = ("<p>Nota: scegli l’altezza in funzione di lamiera/tavolato e spessore soletta; "
            "rispetta i minimi da scheda tecnica/ETA.</p>")
    html = f"""
    <div>
      <h3>Codici {fam}</h3>
      <ul>{bullet}</ul>
      {hint}
      <p><small>Fonte: CODES::{fam}</small></p>
    </div>
    """
    return dict(ok=True, match_id=f"CODES::{fam}", text="", html=html,
                lang="it", family=fam, intent="codes", ms=1, score=95.0)

def render_p560() -> Dict:
    html = ("<p>È un’attrezzatura (chiodatrice a polvere <b>SPIT P560</b>) per fissare i "
            "connettori <b>CTF</b> con chiodi <b>HSBR14</b> (2 per connettore). "
            "Usa propulsori dosati in funzione del supporto e kit/adattatori Tecnaria. "
            "Non sono ammesse macchine alternative.</p>")
    return dict(ok=True, match_id="FAQ::P560::WHAT", text="",
                html=html, lang="it", family="P560", intent="faq", ms=1, score=98.0)

def render_faq_best(q: str) -> Dict:
    # fuzzy match su tutte le FAQ gold
    choices = [(idx, (r.text or r.html or r.match_id)) for idx, r in enumerate(FAQ_ROWS)]
    corpus = [normalize(txt) for _, txt in choices]
    qn = normalize(q)
    if not corpus:
        return render_fallback(q)
    match = process.extractOne(qn, corpus, scorer=fuzz.WRatio)
    if not match or match[1] < 70:  # soglia qualità
        return render_fallback(q)
    idx = match[2]
    row = FAQ_ROWS[idx]
    # Se la FAQ ha solo testo breve, arricchisco con Overview
    html = row.html or f"<p>{row.text}</p>"
    fams = [f.strip().upper() for f in (row.family or "").split("+") if f.strip()]
    if fams:
        enrich = "".join(f"<p><small>Riferimento: OVERVIEW::{f}</small></p>" for f in fams if f in OVERVIEW)
        html = html + enrich
    return dict(ok=True, match_id=row.match_id, text=row.text, html=html,
                lang=row.lang or "it", family=row.family, intent=row.intent, ms=1, score=match[1])

def render_fallback(q: str) -> Dict:
    html = (
        "<p>Non ho trovato una <b>gold answer</b> per questa domanda. "
        "Prova a specificare la <i>famiglia</i> (CTF, CTL, CEM-E, CTCEM, VCEM) "
        "oppure chiedi un <b>confronto</b> (es. “Differenza tra CEM-E e CTCEM?”) "
        "o i <b>codici</b> (es. “Codici CTF”).</p>"
    )
    return dict(ok=True, match_id="", text="", html=html,
                lang="it", family="", intent="none", ms=1, score=0)

# ---------- API ----------
@APP.get("/health")
def health():
    return {"ok": True, "faq_rows": len(FAQ_ROWS), "overview": list(OVERVIEW.keys())}

@APP.get("/api/ask")
def api_ask(q: str = Query(..., description="Domanda in linguaggio naturale")):
    qn = normalize(q)

    # 1) P560 diretto
    if looks_like_p560(qn):
        return to_json(render_p560())

    # 2) intento confronto
    fams = extract_families(qn)
    if looks_like_compare(qn) and len(fams) >= 2:
        return to_json(render_compare(fams[0], fams[1]))

    # 3) intento codici
    if looks_like_codes(qn) and fams:
        return to_json(render_codes(fams[0]))

    # 4) fallback: FAQ fuzzy
    return to_json(render_faq_best(q))

# ---------- UI ----------
UI_HTML = """
<!doctype html><html lang="it"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Tecnaria_V3 — Chatbot Tecnico</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
<style>
  :root{--bg:#0f1a17;--panel:#0f2620;--acc:#19d47b;--txt:#e7fff4;--mut:#98b3aa}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--txt);font-family:Inter,system-ui,Segoe UI,Arial}
  .wrap{max-width:980px;margin:28px auto;padding:0 16px}
  .card{background:var(--panel);border:1px solid #1b3b32;border-radius:16px;box-shadow:0 8px 24px #0008}
  h1{display:flex;align-items:center;gap:10px;font-size:28px;margin:0 0 18px}
  .dot{width:10px;height:10px;border-radius:99px;background:var(--acc);box-shadow:0 0 0 6px #19d47b22}
  .row{display:flex;gap:10px;padding:18px}
  input{flex:1;background:#0b1814;border:1px solid #1a3b32;border-radius:12px;padding:14px 16px;color:var(--txt);font-size:18px}
  button{background:var(--acc);color:#04120d;border:0;border-radius:12px;padding:12px 18px;font-weight:800;font-size:16px;cursor:pointer}
  .chips{display:flex;flex-wrap:wrap;gap:10px;padding:0 18px 18px}
  .chip{background:#15352c;color:var(--txt);padding:10px 14px;border-radius:28px;border:1px solid #1b3b32;cursor:pointer}
  .ans{padding:18px;border-top:1px solid #1b3b32}
  small{color:var(--mut)}
</style></head><body>
<div class="wrap">
  <h1><span class="dot"></span> Tecnaria_V3 — Chatbot Tecnico</h1>
  <div class="card">
    <div class="row">
      <input id="q" placeholder="Scrivi la tua domanda e premi Chiedi…" />
      <button id="go">Chiedi</button>
    </div>
    <div class="chips" id="chips"></div>
    <div id="out" class="ans"><small>UI locale — usa /api/ask lato server</small></div>
  </div>
</div>
<script>
const SUGG = [
  "Differenza tra CTF e CTL?",
  "Differenza tra CEM-E e CTCEM?",
  "Posso usare una chiodatrice qualsiasi per i CTF?",
  "CTF su lamiera grecata: controlli in cantiere?",
  "Mi dai i codici dei CTF?"
];
const chips = document.getElementById("chips");
SUGG.forEach(s=>{
  const b=document.createElement("div"); b.className="chip"; b.textContent=s;
  b.onclick=()=>{document.getElementById("q").value=s; ask();}
  chips.appendChild(b);
});
async function ask(){
  const q = document.getElementById("q").value||"";
  const out=document.getElementById("out");
  out.innerHTML="<small>Attendere…</small>";
  const r = await fetch("/api/ask?q="+encodeURIComponent(q));
  const j = await r.json();
  if(j.html){ out.innerHTML = j.html + `<p><small>match_id: ${j.match_id} | intent: ${j.intent} | famiglia: ${j.family} | lang: ${j.lang} | ms: ${j.ms}</small></p>`; }
  else if(j.text){ out.innerHTML = `<p>${j.text}</p>`; }
  else { out.innerHTML = "<p>(nessuna risposta)</p>"; }
}
document.getElementById("go").onclick=ask;
document.getElementById("q").addEventListener("keydown", (e)=>{ if(e.key==="Enter") ask(); });
</script>
</body></html>
"""

@APP.get("/ui", response_class=HTMLResponse)
def ui():
    return HTMLResponse(UI_HTML)

# Root shortcut
@APP.get("/", response_class=HTMLResponse)
def root():
    return ui()
