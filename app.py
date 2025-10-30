# app.py
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pathlib import Path
import json
import re
from typing import List, Dict, Any, Optional

DATA_FILE = Path("static/data/tecnaria_gold.json")

# ========== LOAD DATA ==========
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

# ========== APP ==========
app = FastAPI(
    title="Tecnaria Sinapsi — Q/A",
    description="Bot ufficiale Tecnaria • CTF • CTL / CTL MAXI • CTCEM / VCEM • P560 • DIAPASON • GTS • ACCESSORI • stile GOLD",
    version="1.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== MATCH UTILS ==========
def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())

def match_by_id(q: str) -> Optional[Dict[str, Any]]:
    qn = normalize(q)
    for item in TECNARIA_ITEMS:
        if normalize(item.get("id", "")) == qn:
            return item
    return None

def match_misto_acciaio_latero(q: str) -> Optional[Dict[str, Any]]:
    """
    Caso speciale: è la tua domanda “regina”.
    Se nella frase compaiono insieme ACCIAIO e LATEROCEMENTO (o LATERIZIO / LATEROCEM),
    rispondiamo con il blocco di confronto “CTF + CTCEM/VCEM”.
    """
    qn = q.lower()
    ha_acciaio = "acciaio" in qn or "trave in acciaio" in qn
    ha_latero = "laterocemento" in qn or "laterizio" in qn or "latero" in qn
    if ha_acciaio and ha_latero:
        # cerchiamo uno dei nostri 3 id di confronto
        prefer_ids = {"CONF-0900", "CONF-0901", "CONF-0125"}
        for item in TECNARIA_ITEMS:
            if item.get("id") in prefer_ids:
                return item
        # se per caso non li trova (file futuro), prendiamo il primo CONFRONTO
        for item in TECNARIA_ITEMS:
            if item.get("family", "").lower() == "confronto":
                return item
    return None

def match_by_triggers(q: str) -> Optional[Dict[str, Any]]:
    qn = normalize(q)
    best_item = None
    best_weight = 0.0
    for item in TECNARIA_ITEMS:
        trig = item.get("trigger", {})
        peso = trig.get("peso", 0)
        kws = trig.get("keywords", [])
        for kw in kws:
            if normalize(kw) in qn:
                if peso > best_weight:
                    best_weight = peso
                    best_item = item
    return best_item

def match_by_family(q: str) -> Optional[Dict[str, Any]]:
    qn = normalize(q)
    for item in TECNARIA_ITEMS:
        fam = normalize(item.get("family", ""))
        if fam and fam in qn:
            return item
    return None

def build_fallback(q: str) -> Dict[str, Any]:
    return {
        "matched": False,
        "family": "COMM",
        "domanda": "Domanda non trovata nel dataset Tecnaria GOLD.",
        "risposta": (
            f"Hai chiesto: «{q}» ma non c'è un trigger GOLD esatto.\n"
            "Controlla che la domanda riguardi i connettori Tecnaria: CTF, CTL, CTL MAXI, CTCEM, VCEM, P560, DIAPASON, GTS, ACCESSORI.\n"
            "Se è un caso di posa reale (lamiera non serrata, connettori saldati, rete mancante) invia foto a info@tecnaria.com.\n"
            "Sede ufficiale: Viale Pecori Giraldi, 55 – 36061 Bassano del Grappa (VI). Tel. +39 0424 502029."
        )
    }

# ========== UI (Render) ==========
@app.get("/", response_class=HTMLResponse)
async def ui_home():
    return """<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <title>Tecnaria Sinapsi — Q/A</title>
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <style>
    :root {
      --bg: #020617;
      --panel: rgba(2,6,23,0.55);
      --accent: #f97316;
      --text: #e2e8f0;
      --muted: #94a3b8;
      --border: rgba(148,163,184,0.18);
    }
    body {
      margin: 0;
      background: radial-gradient(circle at top, #0f172a 0%, #020617 45%, #000 100%);
      min-height: 100vh;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      display: flex;
      flex-direction: column;
    }
    header {
      background: linear-gradient(120deg, #f97316 0%, #020617 40%, #000 100%);
      padding: 1.1rem 1.3rem .8rem 1.3rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    h1 { margin: 0; font-size: 1.05rem; }
    main {
      flex: 1;
      display: grid;
      grid-template-columns: 1.1fr .9fr;
      gap: .85rem;
      padding: .85rem;
    }
    .left, .right {
      background: var(--panel);
      border: 1px solid rgba(148,163,184,0.12);
      border-radius: .85rem;
      backdrop-filter: blur(12px);
      display: flex;
      flex-direction: column;
    }
    .left-top { padding: .6rem .7rem .4rem; border-bottom: 1px solid rgba(148,163,184,0.08); }
    .label { font-size: .62rem; text-transform: uppercase; color: var(--muted); margin-bottom: .35rem; }
    .searchbar { display: flex; gap: .4rem; background: rgba(15,23,42,0.5); border: 1px solid rgba(148,163,184,0.18); border-radius: .7rem; padding: .25rem .4rem .25rem .6rem; }
    .searchbar input { background: transparent; border: none; outline: none; color: white; font-size: .78rem; flex: 1; }
    .searchbar button { background: var(--accent); border: none; border-radius: .5rem; padding: .3rem .6rem; font-size: .68rem; font-weight: 600; cursor: pointer; }
    .pills { display: flex; gap: .4rem; flex-wrap: wrap; margin-top: .5rem; }
    .pill { background: rgba(249,115,22,0.05); border: 1px solid rgba(249,115,22,0.25); border-radius: .6rem; padding: .25rem .5rem; font-size: .6rem; cursor: pointer; }
    .left-body { flex: 1; padding: .5rem .7rem .7rem; overflow: auto; font-size: .76rem; line-height: 1.25rem; }
    .right { padding: .6rem .7rem; gap: .5rem; }
    .panel-title { font-size: .65rem; text-transform: uppercase; color: var(--muted); }
    .list { display: flex; flex-direction: column; gap: .4rem; }
    .list-item { background: rgba(15,23,42,0.4); border: 1px solid rgba(148,163,184,0.08); border-radius: .5rem; padding: .4rem .5rem; cursor: pointer; font-size: .68rem; }
    @media (max-width: 950px) { main { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Tecnaria Sinapsi — Q/A</h1>
      <div style="font-size:.65rem;opacity:.9;">Bot ufficiale Tecnaria • stile GOLD • Bassano del Grappa</div>
    </div>
    <div style="font-size:.55rem;background:rgba(15,23,42,0.25);padding:.3rem .6rem;border-radius:9999px;border:1px solid rgba(226,232,240,.12);">/qa/ask attivo</div>
  </header>
  <main>
    <section class="left">
      <div class="left-top">
        <div class="label">Chiedi a Sinapsi</div>
        <div class="searchbar">
          <input id="q" value="Ho travi in acciaio e campata in laterocemento, che connettori devo usare?" />
          <button onclick="ask()">Chiedi</button>
        </div>
        <div class="pills">
          <div class="pill" onclick="setQ('P560 con CTF: quanti tiri di prova devo fare?')">P560 (istruzioni)</div>
          <div class="pill" onclick="setQ('Posa CTF su trave in acciaio con lamiera grecata')">CTF (chiodatrice)</div>
          <div class="pill" onclick="setQ('Differenza tra CTL e CTL MAXI?')">CTL vs CTL MAXI</div>
          <div class="pill" onclick="setQ('Quando uso i VCEM al posto dei CTCEM?')">CTCEM / VCEM</div>
          <div class="pill" onclick="setQ('Manca la rete, posso gettare oggi?')">Problemi di cantiere</div>
        </div>
      </div>
      <div class="left-body" id="answer">
        <strong>Benvenuto nel bot Tecnaria.</strong><br/>
        Fai una domanda di cantiere: “manca la rete”, “hanno saldato i CTL”, “acciaio + laterocemento”, “P560 con VCEM?”. Ti risponde in stile GOLD.
      </div>
    </section>
    <section class="right">
      <div class="panel-title">Famiglie</div>
      <div class="list">
        <div class="list-item" onclick="setQ('Come si posano i CTF su acciaio con lamiera?')">CTF — posa con P560 e 2×HSBR14</div>
        <div class="list-item" onclick="setQ('Come si posano i CTL su legno o lamellare?')">CTL / CTL MAXI — legno</div>
        <div class="list-item" onclick="setQ('Quando è meglio usare i VCEM rispetto ai CTCEM?')">CTCEM / VCEM — laterocemento</div>
        <div class="list-item" onclick="setQ('Quanti tiri di prova con P560?')">P560 — 3 tiri di prova</div>
        <div class="list-item" onclick="setQ('Ho travi in acciaio e campata in laterocemento, cosa uso?')">Confronto — casi misti</div>
        <div class="list-item" onclick="setQ('Manca la rete, posso gettare?')">Problematiche — blocca getto</div>
      </div>
    </section>
  </main>
  <script>
    async function ask() {
      const q = document.getElementById("q").value;
      const box = document.getElementById("answer");
      box.innerHTML = "<p>⏳ Sto chiedendo a Sinapsi...</p>";
      try {
        const res = await fetch("/qa/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: q })
        });
        const data = await res.json();
        box.innerHTML = `
          <div style="font-weight:600;margin-bottom:.25rem;">${data.domanda || "Risposta Tecnaria"}</div>
          <div style="font-size:.6rem;color:#94a3b8;margin-bottom:.5rem;">${data.family ? "Famiglia: " + data.family : ""}</div>
          <div style="white-space:pre-line;">${data.risposta || "Nessuna risposta disponibile."}</div>
        `;
      } catch (e) {
        box.innerHTML = "<p>❌ Errore nel contattare /qa/ask</p>";
      }
    }
    function setQ(txt) {
      document.getElementById("q").value = txt;
      ask();
    }
  </script>
</body>
</html>
    """

# ========== API ==========
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

    # 0) match per ID (se qualcuno mette COMM-0001 ecc.)
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

    # 1) CASO SPECIALE: ACCIAIO + LATEROCEMENTO
    item = match_misto_acciaio_latero(q_norm)
    if item:
        return {
            "matched": True,
            "match_type": "special-misto-acciaio-latero",
            "family": item.get("family"),
            "domanda": item.get("domanda"),
            "risposta": item.get("risposta"),
            "trigger": item.get("trigger", {})
        }

    # 2) trigger normali
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

    # 3) family nel testo
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

    # 4) fallback
    return build_fallback(question)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
