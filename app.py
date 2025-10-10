import os
import json
import time
from pathlib import Path
from typing import List, Dict, Any

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import HTMLResponse, JSONResponse

# ------------------------------
# Config
# ------------------------------
UI_TITLE = os.getenv("UI_TITLE", "Tecnaria – QA Bot")
KB_DIR = Path(os.getenv("KB_DIR", "./static/data/critici"))
ALLOWED_DOMAINS = set((os.getenv("ALLOWED_DOMAINS", "tecnaria.com, spit.eu, spitpaslode.com").replace(" ", "").split(",")))
FETCH_WEB_FIRST = os.getenv("FETCH_WEB_FIRST", "0") == "1"  # lasciato per compatibilità futura

# ------------------------------
# Minimal, deterministic KB loader + fallback entries
# ------------------------------
DEFAULT_KB: List[Dict[str, Any]] = [
    {
        "id": "CTCEM-DATI-0001",
        "category": "scheda_tecnica",
        "q": "Che cos'è il connettore CTCEM Tecnaria?",
        "a": (
            "Il connettore CTCEM è un dispositivo Tecnaria per il consolidamento di solai in calcestruzzo esistente. "
            "È un connettore meccanico, a fissaggio completamente a secco, che consente di migliorare la collaborazione "
            "tra la soletta nuova e la struttura sottostante senza impiegare resine o ancoranti chimici. "
            "Installazione: preforo Ø 11 mm (~75 mm), pulizia foro e avvitatura del piolo con avvitatore fino a battuta."
        ),
    },
    {
        "id": "CTCEM-DATI-0002",
        "category": "posa_installazione",
        "q": "Come si posa un connettore CTCEM Tecnaria?",
        "a": (
            "Posa meccanica a secco: 1) incisione/fresata per la piastra dentata; 2) preforo Ø 11 mm prof. ~75 mm; "
            "3) pulizia accurata; 4) avvitatura del piolo con avvitatore a percussione/frizione fino a battuta. "
            "Nessuna resina, nessun tempo di indurimento."
        ),
    },
    {
        "id": "CTCEM-DATI-0003",
        "category": "caratteristiche",
        "q": "Quali sono le caratteristiche principali del sistema CTCEM?",
        "a": (
            "• Fissaggio meccanico a secco (senza resine)\n"
            "• Preforo Ø 11 mm, profondità ~75 mm\n"
            "• Avvitatura con avvitatore a percussione/frizione\n"
            "• Piastra dentata in acciaio\n"
            "• Nessun tempo di presa\n"
            "• Alternativa alle barre incollate\n"
            "• Rapidità e pulizia in cantiere"
        ),
    },
    {
        "id": "CTCEM-DATI-0004",
        "category": "varianti_modelli",
        "q": "Esistono varianti del connettore CTCEM?",
        "a": (
            "Sì: CTCEM (calcestruzzo) e VCEM (legno-calcestruzzo, piastra più ampia). Entrambi sono connettori meccanici a secco."
        ),
    },
    {
        "id": "CTCEM-DATI-0005",
        "category": "certificazioni_norme",
        "q": "Quali certificazioni ha il sistema CTCEM?",
        "a": (
            "Sistema oggetto di prove presso laboratori accreditati (es. SOCOTEC) e allineato alla documentazione Tecnaria. "
            "Fare sempre riferimento alle schede ufficiali aggiornate."
        ),
    },
]

KB: List[Dict[str, Any]] = []


def _normalize_text(s: str) -> List[str]:
    return [t for t in ''.join(ch.lower() if ch.isalnum() or ch.isspace() else ' ' for ch in s).split() if t]


def _load_kb_from_disk() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if KB_DIR.exists():
        for p in KB_DIR.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    items.extend(data)
                elif isinstance(data, dict):
                    items.append(data)
            except Exception:
                # ignora file malformati, continua
                pass
    # merge con DEFAULT_KB (senza duplicare stessi id)
    seen = {it.get("id") for it in items}
    for it in DEFAULT_KB:
        if it.get("id") not in seen:
            items.append(it)
    return items


def _score(query: str, candidate_q: str, candidate_a: str) -> float:
    qt = set(_normalize_text(query))
    if not qt:
        return 0.0
    ct = set(_normalize_text(candidate_q)) | set(_normalize_text(candidate_a))
    if not ct:
        return 0.0
    overlap = len(qt & ct)
    return overlap / max(1, len(qt))


def kb_search(query: str, k: int = 5) -> List[Dict[str, Any]]:
    scored = []
    for it in KB:
        s = _score(query, it.get("q", ""), it.get("a", ""))
        if s > 0:
            scored.append((s, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [it for _, it in scored[:k]]


# ------------------------------
# FastAPI app
# ------------------------------
app = FastAPI(title=UI_TITLE)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskIn(BaseModel):
    q: str


@app.on_event("startup")
async def startup_event():
    global KB
    KB_DIR.mkdir(parents=True, exist_ok=True)
    KB = _load_kb_from_disk()


@app.get("/health")
async def health():
    return {"ok": True, "items_loaded": len(KB)}


@app.get("/")
async def root_ui() -> HTMLResponse:
    html = f"""
    <!doctype html>
    <html lang=it>
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>{UI_TITLE}</title>
      <style>
        body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; margin: 24px; }}
        .card {{ border: 1px solid #ddd; border-radius: 12px; padding: 16px; box-shadow: 0 2px 12px rgba(0,0,0,.05); max-width: 900px; }}
        .row {{ display:flex; gap:8px; margin-bottom: 12px; }}
        input[type=text] {{ flex:1; padding:12px; border-radius:10px; border:1px solid #bbb; }}
        button {{ padding:12px 16px; border-radius:10px; border:0; background:#ff7a00; color:white; font-weight:600; cursor:pointer; }}
        .muted {{ color:#666; font-size:12px; }}
        .answer {{ line-height:1.5; }}
      </style>
    </head>
    <body>
      <h1>{UI_TITLE}</h1>
      <div class="card">
        <div class="row">
          <input id="q" type="text" placeholder="Fai una domanda (es. ‘CTCEM usa resine?’)" />
          <button onclick="ask()">Chiedi</button>
        </div>
        <div id="out" class="answer"></div>
      </div>
      <p class="muted">Domini consentiti: {', '.join(sorted(ALLOWED_DOMAINS))} — Modalità: domanda libera (KB locale → nessun web fetch).</p>
      <script>
        async function ask() {{
          const q = document.getElementById('q').value;
          const t0 = performance.now();
          const res = await fetch('/api/ask', {{ method:'POST', headers: {{'Content-Type':'application/json'}}, body: JSON.stringify({{q}}) }});
          const json = await res.json();
          const dt = Math.max(1, Math.round(performance.now()-t0));
          if (!json.ok) {{ document.getElementById('out').innerHTML = `<p>Errore: ${'{'}json.error{'}'}</p>`; return; }}
          document.getElementById('out').innerHTML = json.html.replace('</div>', `<p class=\"muted\">⏱ ${'{'}dt{'}'} ms</p></div>`);
        }}
      </script>
    </body>
    </html>
    """
    return HTMLResponse(html)


@app.get("/kb/ids")
async def kb_ids():
    return [it.get("id") for it in KB]


@app.get("/kb/search")
async def kb_search_endpoint(q: str = Query(""), k: int = Query(5, ge=1, le=20)):
    if not q:
        return {"ok": True, "count": 0, "items": []}
    items = kb_search(q, k=k)
    return {"ok": True, "count": len(items), "items": items}


@app.post("/api/ask")
async def api_ask(payload: AskIn = Body(...)):
    q = (payload.q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail={"error": "Campo 'q' mancante o vuoto"})

    t0 = time.perf_counter()
    hits = kb_search(q, k=5)

    if hits:
        best = hits[0]
        html = f"""
        <div class=card>
          <h2>Risposta Tecnaria</h2>
          <p class=answer>{best.get('a')}</p>
          <p class=muted>Fonte KB: <b>{best.get('id')}</b> — categoria: {best.get('category','')}</p>
        </div>
        """
        dt = int((time.perf_counter() - t0) * 1000)
        return JSONResponse({"ok": True, "html": html, "ms": dt, "match_id": best.get("id")})

    # Nessun match locale → niente web fetch (modalità richiesta: no prove, no browsing).
    html = (
        "<div class=card>"
        "<h2>Risposta Tecnaria</h2>"
        "<p>Non ho trovato elementi sufficienti nel KB locale per rispondere con certezza. "
        "Raffina la domanda oppure aggiungi la scheda corrispondente in <code>static/data/critici</code>.</p>"
        "</div>"
    )
    dt = int((time.perf_counter() - t0) * 1000)
    return JSONResponse({"ok": True, "html": html, "ms": dt, "match_id": None})


# ------------------------------
# Run helper (only for local dev):
#   uvicorn app:app --reload --port 8000
# ------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
