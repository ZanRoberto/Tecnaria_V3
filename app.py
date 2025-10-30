# app.py
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pathlib import Path
import json
import re
from typing import List, Dict, Any, Optional

DATA_FILE = Path("static/data/tecnaria_gold.json")

def load_tecnaria_data(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"File JSON non trovato: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("items", [])
    return data

try:
    TECNARIA_DATA = load_tecnaria_data(DATA_FILE)
    TECNARIA_ITEMS: List[Dict[str, Any]] = TECNARIA_DATA.get("items", [])
except Exception as e:
    TECNARIA_DATA = {"_meta": {}, "items": []}
    TECNARIA_ITEMS = []
    print(f"[WARN] impossibile caricare {DATA_FILE}: {e}")

app = FastAPI(
    title="Tecnaria Sinapsi — Q/A",
    description="Bot ufficiale: CTF, CTL/CTL MAXI, P560, CTCEM/VCEM, DIAPASON, GTS, ACCESSORI • Stile GOLD • RAG Tecnaria-only",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())

def match_by_id(q: str) -> Optional[Dict[str, Any]]:
    q_norm = normalize(q)
    for item in TECNARIA_ITEMS:
        if normalize(item.get("id", "")) == q_norm:
            return item
    return None

def match_by_family(q: str) -> Optional[Dict[str, Any]]:
    q_norm = normalize(q)
    for item in TECNARIA_ITEMS:
        fam = normalize(item.get("family", ""))
        if fam and fam in q_norm:
            return item
    return None

def match_by_triggers(q: str) -> Optional[Dict[str, Any]]:
    q_norm = normalize(q)
    best_item = None
    best_weight = 0.0
    for item in TECNARIA_ITEMS:
        trig = item.get("trigger", {})
        peso = trig.get("peso", 0)
        keywords = trig.get("keywords", [])
        for kw in keywords:
            if normalize(kw) in q_norm:
                if peso > best_weight:
                    best_weight = peso
                    best_item = item
    return best_item

def build_fallback(q: str) -> Dict[str, Any]:
    return {
        "matched": False,
        "family": "COMM",
        "domanda": "Domanda non trovata nel dataset Tecnaria GOLD.",
        "risposta": (
            "La tua domanda è stata ricevuta ma non c'è un trigger GOLD esatto.\n"
            "Verifica che la richiesta riguardi **CTF, CTL, CTL MAXI, CTCEM, VCEM, P560, DIAPASON, GTS, ACCESSORI** "
            "oppure invia foto e descrizione a **info@tecnaria.com** indicando il cantiere.\n"
            "Sede: Viale Pecori Giraldi, 55 – 36061 Bassano del Grappa (VI). Tel. +39 0424 502029."
        )
    }

# ============= ROUTE UI (render button) =============
@app.get("/", response_class=HTMLResponse)
async def ui_home():
    # UI “Tecnaria Sinapsi — Q/A”
    return """
<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <title>Tecnaria Sinapsi — Q/A</title>
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <style>
    :root {
      --bg: #0f172a;
      --panel: #0f172a;
      --accent: #f97316;
      --accent-soft: rgba(249,115,22,0.08);
      --text: #e2e8f0;
      --muted: #94a3b8;
      --border: rgba(148,163,184,0.18);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(circle at top, #0f172a 0%, #020617 40%, #000 100%);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    header {
      background: linear-gradient(120deg, #f97316 0%, #020617 40%, #000 100%);
      padding: 1.3rem 1.6rem 1rem 1.6rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      border-bottom: 1px solid rgba(0,0,0,0.2);
    }
    .title-block {
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
    }
    h1 {
      font-size: 1.1rem;
      margin: 0;
      font-weight: 600;
    }
    .subtitle {
      font-size: 0.7rem;
      color: rgba(226,232,240,0.85);
    }
    .badge {
      background: rgba(2,6,23,0.35);
      border: 1px solid rgba(226,232,240,0.12);
      border-radius: 9999px;
      padding: 0.25rem 0.8rem;
      font-size: 0.6rem;
      text-transform: uppercase;
      letter-spacing: .03em;
    }
    main {
      flex: 1;
      display: grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap: 0.9rem;
      padding: 0.9rem 1rem 1rem;
      min-height: 0;
    }
    .left, .right {
      background: rgba(2,6,23,0.35);
      border: 1px solid rgba(148,163,184,0.12);
      border-radius: 1rem;
      backdrop-filter: blur(10px);
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .left-top {
      padding: .75rem .8rem .4rem .8rem;
      border-bottom: 1px solid rgba(148,163,184,0.07);
    }
    .label {
      font-size: .63rem;
      text-transform: uppercase;
      letter-spacing: .04em;
      color: var(--muted);
      margin-bottom: .35rem;
    }
    .searchbar {
      display: flex;
      gap: .4rem;
      align-items: center;
      background: rgba(15,23,42,0.5);
      border: 1px solid rgba(148,163,184,0.18);
      border-radius: .8rem;
      padding: .35rem .5rem .35rem .7rem;
    }
    .searchbar input {
      flex: 1;
      background: transparent;
      border: none;
      outline: none;
      color: white;
      font-size: .8rem;
    }
    .searchbar button {
      background: var(--accent);
      border: none;
      border-radius: .6rem;
      padding: .4rem .8rem;
      color: #0f172a;
      font-weight: 600;
      font-size: .72rem;
      cursor: pointer;
    }
    .pills {
      display: flex;
      gap: .4rem;
      margin-top: .5rem;
      flex-wrap: wrap;
    }
    .pill {
      background: rgba(249,115,22,0.06);
      border: 1px solid rgba(249,115,22,0.25);
      border-radius: .6rem;
      padding: .25rem .55rem;
      font-size: .64rem;
      cursor: pointer;
      transition: .15s;
    }
    .pill:hover {
      background: rgba(249,115,22,0.28);
    }
    .left-body {
      flex: 1;
      padding: .6rem .8rem .8rem;
      overflow: auto;
      font-size: .78rem;
      line-height: 1.3rem;
    }
    .answer-title {
      font-weight: 600;
      margin-bottom: .3rem;
    }
    .answer-meta {
      font-size: .6rem;
      color: var(--muted);
      margin-bottom: .6rem;
    }
    .right {
      padding: .75rem .8rem .8rem;
      gap: .65rem;
    }
    .panel-title {
      font-size: .7rem;
      text-transform: uppercase;
      letter-spacing: .04em;
      color: var(--muted);
    }
    .list {
      display: flex;
      flex-direction: column;
      gap: .4rem;
      font-size: .7rem;
    }
    .list-item {
      background: rgba(15,23,42,0.35);
      border: 1px solid rgba(148,163,184,0.08);
      border-radius: .6rem;
      padding: .4rem .5rem;
      cursor: pointer;
    }
    .list-item strong {
      font-size: .7rem;
    }
    @media (max-width: 990px) {
      main {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <header>
    <div class="title-block">
      <h1>Tecnaria Sinapsi — Q/A</h1>
      <div class="subtitle">Bot ufficiale: CTF, CTL/CTL MAXI, P560, CTCEM/VCEM, DIAPASON, GTS, ACCESSORI</div>
    </div>
    <div class="badge">Stile GOLD · Bassano del Grappa</div>
  </header>
  <main>
    <section class="left">
      <div class="left-top">
        <div class="label">Chiedi a Sinapsi</div>
        <div class="searchbar">
          <input id="q" value="Ho travi in acciaio e campata in laterocemento, cosa uso?" />
          <button onclick="ask()">Chiedi</button>
        </div>
        <div class="pills">
          <div class="pill" onclick="setQ('P560 con CTF: quanti tiri di prova devo fare?')">P560 (istruzioni)</div>
          <div class="pill" onclick="setQ('Posa CTF su trave in acciaio con lamiera grecata')">CTF (chiodatrice)</div>
          <div class="pill" onclick="setQ('Differenza tra CTL e CTL MAXI?')">CTL vs CTL MAXI</div>
          <div class="pill" onclick="setQ('Quando uso i VCEM e quando i CTCEM?')">CTCEM / VCEM</div>
          <div class="pill" onclick="setQ('Manca la rete, posso gettare oggi?')">Problemi di cantiere</div>
        </div>
      </div>
      <div class="left-body" id="answer">
        <div class="answer-title">Benvenuto nel bot Tecnaria</div>
        <div class="answer-meta">Stile GOLD • dataset: tecnaria_gold.json</div>
        <p>Fai una domanda tecnica esattamente come la faresti via WhatsApp dal cantiere: “manca la rete”, “hanno saldato i CTL”, “posso usare la P560 anche qui?”, “ho acciaio e laterocemento insieme cosa uso?”.</p>
        <p>Il bot risponde solo su **prodotti e posa Tecnaria S.p.A. di Bassano del Grappa**.</p>
      </div>
    </section>
    <section class="right">
      <div class="panel-title">Famiglie supportate</div>
      <div class="list">
        <div class="list-item" onclick="setQ('Come si posano i CTF su acciaio con lamiera?')"><strong>CTF</strong> · posa a secco con P560, 2×HSBR14</div>
        <div class="list-item" onclick="setQ('Come si posano i connettori CTL su legno?')"><strong>CTL / CTL MAXI</strong> · legno, viti Ø10, solette 4–8 cm</div>
        <div class="list-item" onclick="setQ('Quando usare CTCEM e quando VCEM?')"><strong>CTCEM / VCEM</strong> · laterocemento, foro e avvitatura</div>
        <div class="list-item" onclick="setQ('Quanti tiri di prova devo fare con la P560?')"><strong>P560</strong> · almeno 3 tiri di prova</div>
        <div class="list-item" onclick="setQ('Ho travi in acciaio e campata in laterocemento, cosa uso?')"><strong>Confronto</strong> · casi misti acciaio/laterizio</div>
        <div class="list-item" onclick="setQ('Manca la rete, posso gettare?')"><strong>Problematiche</strong> · cantiere reale</div>
        <div class="list-item" onclick="setQ('Hanno saldato i CTL MAXI, va bene?')"><strong>Killer</strong> · errori gravi da bloccare</div>
      </div>
    </section>
  </main>
  <script>
    async function ask() {
      const q = document.getElementById("q").value;
      const area = document.getElementById("answer");
      area.innerHTML = "<p>⏳ Sto chiedendo a Sinapsi...</p>";
      try {
        const res = await fetch("/qa/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: q })
        });
        const data = await res.json();
        area.innerHTML = `
          <div class="answer-title">${data.domanda || "Risposta Tecnaria"}</div>
          <div class="answer-meta">${data.family ? "Famiglia: " + data.family : ""}</div>
          <p style="white-space: pre-line;">${data.risposta || "Nessuna risposta."}</p>
        `;
      } catch (err) {
        area.innerHTML = "<p>❌ Errore nel contattare il bot.</p>";
      }
    }
    function setQ(text) {
      document.getElementById("q").value = text;
      ask();
    }
  </script>
</body>
</html>
    """

# ============= API =============
@app.get("/health")
def health():
    return {
        "status": "ok",
        "items": len(TECNARIA_ITEMS),
        "families": sorted(list({it.get("family", "") for it in TECNARIA_ITEMS if it.get("family")})),
        "source": str(DATA_FILE)
    }

@app.post("/qa/ask")
async def qa_ask(payload: Dict[str, Any], request: Request):
    question = payload.get("question", "")
    if not question:
        raise HTTPException(status_code=400, detail="Campo 'question' mancante")

    q_norm = normalize(question)

    # 1. id
    item = match_by_id(q_norm)
    if item:
        return {
            "matched": True,
            "match_type": "id",
            "family": item.get("family"),
            "domanda": item.get("domanda"),
            "risposta": item.get("risposta"),
            "trigger": item.get("trigger", {})
        }

    # 2. trigger
    item = match_by_triggers(q_norm)
    if item:
        return {
            "matched": True,
            "match_type": "trigger",
            "family": item.get("family"),
            "domanda": item.get("domanda"),
            "risposta": item.get("risposta"),
            "trigger": item.get("trigger", {})
        }

    # 3. family nel testo
    item = match_by_family(q_norm)
    if item:
        return {
            "matched": True,
            "match_type": "family",
            "family": item.get("family"),
            "domanda": item.get("domanda"),
            "risposta": item.get("risposta"),
            "trigger": item.get("trigger", {})
        }

    # 4. fallback GOLD
    return build_fallback(question)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
