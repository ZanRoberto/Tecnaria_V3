import json
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_FILE = Path("tecnaria_gold.json")

# fallback minimo, così l’interfaccia parte SEMPRE
FALLBACK_DATA = {
    "_meta": {
        "version": "TECNARIA_GOLD-FALLBACK",
        "project": "TECNARIA_GOLD",
        "generated_at": "2025-11-02T00:00:00",
        "language_base": "it",
        "families_active": [
            "COMM",
            "CTF",
            "CTL",
            "CTL MAXI",
            "CTCEM",
            "VCEM",
            "P560",
            "DIAPASON",
            "GTS",
            "ACCESSORI",
            "CONFRONTO",
            "PROBLEMATICHE",
            "KILLER",
        ],
    },
    "items": [
        {
            "id": "fallback-1",
            "question": "dove si trova la sede tecnaria?",
            "family": "COMM",
            "triggers": ["sede", "tecnaria", "bassano", "contatti", "telefono"],
            "answer": (
                "La Tecnaria S.p.A. ha sede in **Viale Pecori Giraldi 55, 36061 Bassano del Grappa (VI)**. "
                "Tel. **+39 0424 502029** – Email **info@tecnaria.com**. "
                "Questo è il riferimento unico anche per supporto tecnico su CTF, CTL, VCEM, P560."
            ),
        }
    ],
}

LOADED_DATA = FALLBACK_DATA
LOAD_ERROR = None


def try_load_dataset():
    """Prova a caricare tecnaria_gold.json SENZA mangiarlo/riordinarlo.
    Se fallisce, tiene il fallback ma memorizza l'errore.
    """
    global LOADED_DATA, LOAD_ERROR
    if not DATA_FILE.exists():
        LOAD_ERROR = f"File {DATA_FILE} non trovato."
        LOADED_DATA = FALLBACK_DATA
        return

    raw_text = DATA_FILE.read_text(encoding="utf-8")
    try:
        data = json.loads(raw_text)
        # controllo minimo: deve esserci 'items'
        if "items" not in data:
            raise ValueError("JSON valido ma senza chiave 'items'.")
        LOADED_DATA = data
        LOAD_ERROR = None
    except Exception as e:
        # NON mangiamo il file, solo segnaliamo
        LOAD_ERROR = f"Errore nel JSON: {e}"
        LOADED_DATA = FALLBACK_DATA


try_load_dataset()


def normalize(q: str) -> str:
    return q.lower().strip()


def score_match(user_q: str, item: dict) -> int:
    """scoring semplice ma efficace per NLM 'sporco':
    - +50 se la domanda è quasi uguale
    - +20 per ogni trigger che compare
    - +10 se il nome famiglia compare
    """
    u = normalize(user_q)
    score = 0
    # 1) match quasi diretto
    if "question" in item:
        base_q = normalize(item["question"])
        if base_q in u or u in base_q:
            score += 50

    # 2) trigger
    for t in item.get("triggers", []):
        if t and normalize(t) in u:
            score += 20

    # 3) family
    fam = item.get("family") or item.get("famiglia")
    if fam and normalize(fam) in u:
        score += 10

    # 4) parole chiave tipiche tecnaria
    tecnaria_words = ["ctf", "ctl", "maxi", "vcem", "ctcem", "p560", "lamiera", "chiodo", "connettori"]
    for w in tecnaria_words:
        if w in u:
            score += 5

    return score


def find_best_answer(user_q: str) -> dict:
    items = LOADED_DATA.get("items", [])
    if not items:
        return {
            "answer": "Nessun item caricato. Controlla il file tecnaria_gold.json.",
            "item_id": None,
            "score": 0,
        }

    best_item = None
    best_score = -1
    for it in items:
        s = score_match(user_q, it)
        if s > best_score:
            best_score = s
            best_item = it

    # se lo score è troppo basso, preferiamo una risposta controllata
    if best_score < 10:
        return {
            "answer": (
                "La domanda è stata capita solo in parte. "
                "Prova a specificare se parli di **CTF**, **CTL/CTL MAXI**, **VCEM/CTCEM** "
                "oppure della **SPIT P560**. "
                "Se stai parlando di posa su **lamiera non serrata** la risposta è: "
                "**la lamiera va serrata prima di sparare i CTF**, altrimenti il chiodo può piegarsi."
            ),
            "item_id": None,
            "score": best_score,
        }

    return {
        "answer": best_item.get("answer", "Nessuna risposta salvata."),
        "item_id": best_item.get("id"),
        "score": best_score,
    }


HTML_PAGE = """
<!doctype html>
<html lang="it">
  <head>
    <meta charset="utf-8" />
    <title>Tecnaria Sinapsi — Q/A</title>
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <style>
      body { margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; background:#111; color:#fff; }
      header { background:linear-gradient(90deg,#ff7b00,#000); padding:16px 20px; display:flex; justify-content:space-between; align-items:center; }
      .title { font-size:1.1rem; font-weight:600; }
      .status { font-size:0.8rem; padding:4px 10px; border-radius:999px; background:rgba(0,0,0,.2); }
      main { display:flex; gap:20px; padding:20px; }
      .left { flex:0 0 420px; }
      .right { flex:1; background:rgba(0,0,0,.25); border:1px solid rgba(255,255,255,.06); border-radius:14px; padding:16px; min-height:300px; }
      input[type=text] { width:100%; padding:10px 12px; border-radius:10px; border:1px solid rgba(255,255,255,.15); background:rgba(0,0,0,.35); color:#fff; font-size:0.9rem; }
      button { margin-top:10px; background:#ff7b00; color:#000; border:none; padding:8px 14px; border-radius:8px; font-weight:600; cursor:pointer; }
      .answer { white-space:pre-wrap; line-height:1.5; }
      .badge-error { background:#ff004d; color:#fff; padding:3px 10px; border-radius:999px; font-size:0.7rem; margin-left:8px; }
      .hint { font-size:0.7rem; opacity:.6; margin-top:4px; }
      .pill { display:inline-block; background:rgba(255,255,255,.08); padding:5px 10px; border-radius:999px; font-size:0.7rem; cursor:pointer; margin:4px 6px 0 0; }
    </style>
  </head>
  <body>
    <header>
      <div class="title">Tecnaria Sinapsi — Q/A</div>
      <div id="status" class="status">pronto</div>
    </header>
    <main>
      <div class="left">
        <label>Chiedi a Sinapsi</label>
        <input id="q" type="text" placeholder="es. ho bucato un VCEM con la P560, è valido?" />
        <button onclick="ask()">Invia</button>
        <div class="hint">Esempi veloci:</div>
        <div>
          <span class="pill" onclick="quick('posso usare i CTF su lamiera non serrata?')">CTF su lamiera</span>
          <span class="pill" onclick="quick('differenza tra CTL e CTL MAXI?')">CTL vs CTL MAXI</span>
          <span class="pill" onclick="quick('posso sparare VCEM con P560?')">P560 su VCEM</span>
        </div>
      </div>
      <div class="right">
        <div id="out" class="answer">Fai una domanda su CTF, CTL, VCEM, P560, Diapason, accessori…</div>
      </div>
    </main>
    <script>
      async function ask() {
        const q = document.getElementById("q").value;
        const res = await fetch("/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: q })
        });
        const data = await res.json();
        const out = document.getElementById("out");
        out.innerHTML = data.answer;
      }
      function quick(t) {
        document.getElementById("q").value = t;
        ask();
      }
    </script>
  </body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def home():
    # se c'è errore nel JSON lo mostriamo nel badge
    html = HTML_PAGE
    if LOAD_ERROR:
        # iniettiamo un piccolo js per mostrare l'errore
        error_js = f"""
        <script>
          const st = document.getElementById("status");
          st.innerHTML = "JSON NON VALIDO";
          st.className = "status";
          st.style.background = "#ff004d";
          st.title = {json.dumps(LOAD_ERROR)};
        </script>
        """
        html = html.replace("</body>", error_js + "</body>")
    return HTMLResponse(content=html)


@app.get("/health")
async def health():
    return {
        "status": "ok" if not LOAD_ERROR else "warn",
        "message": "Tecnaria GOLD pronto" if not LOAD_ERROR else "Tecnaria GOLD con JSON non valido",
        "items": len(LOADED_DATA.get("items", [])),
        "error": LOAD_ERROR,
    }


@app.post("/ask")
async def ask(payload: dict):
    q = payload.get("question") or ""
    ans = find_best_answer(q)
    # arricchiamo con info di debug
    return {
        "answer": ans["answer"],
        "score": ans["score"],
        "item_id": ans["item_id"],
        "items_total": len(LOADED_DATA.get("items", [])),
        "json_error": LOAD_ERROR,
    }
