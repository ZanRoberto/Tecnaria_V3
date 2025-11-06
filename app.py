# app.py — Tecnaria Sinapsi Q/A (OFFLINE) — Router GOLD con Narration Booster
# Rotte: / (UI), /ping, /status, /ask (POST)
# Legge SOLO static/data/tecnaria_gold.json (non modifichiamo il tuo file)
# Obiettivi:
# 1) Instradamento per FAMIGLIA -> Item (semantico) con antitemi e pesi come spareggio
# 2) Gold Narration Booster per risposte "secche" (Contesto → Istruzioni → Alternativa → Checklist → Nota RAG)

import json, re, unicodedata, math
from pathlib import Path
from typing import Dict, Any, List, Tuple
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

APP_DIR = Path(__file__).parent
DATA_FILE = APP_DIR / "static" / "data" / "tecnaria_gold.json"

app = FastAPI(title="Tecnaria Sinapsi — Q/A (offline, router GOLD)")

# ---------- Utils ----------

def norm(s: str) -> str:
    s = s.lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9\s/+-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def cosine(a_tokens: List[str], b_tokens: List[str]) -> float:
    from collections import Counter
    a, b = Counter(a_tokens), Counter(b_tokens)
    ka = set(a); kb = set(b)
    inter = sum(min(a[k], b[k]) for k in ka & kb)
    na = math.sqrt(sum(v*v for v in a.values()))
    nb = math.sqrt(sum(v*v for v in b.values()))
    return (inter/(na*nb)) if na and nb else 0.0

def tokenize(s: str) -> List[str]:
    return norm(s).split()

def soft_contains(text: str, words: List[str]) -> bool:
    t = norm(text)
    return any(w in t for w in words)

# ---------- Caricamento dati ----------
if not DATA_FILE.exists():
    raise RuntimeError(f"Manca il file dati: {DATA_FILE}")

with open(DATA_FILE, "r", encoding="utf-8") as f:
    DATA = json.load(f)

ITEMS: List[Dict[str, Any]] = DATA.get("items", [])
for it in ITEMS:
    it.setdefault("trigger", {"peso": 0.5, "keywords": []})
    if isinstance(it["trigger"], dict):
        it["trigger"].setdefault("keywords", [])

# Mappa famiglie -> parole chiave principali per instradamento
FAM_ROUTE = {
    "COMM": ["ordinare","acquistare","sede","indirizzo","telefono","email","listino","preventivo","commerciale"],
    "CTF":  ["trave acciaio","lamiera","p560","chiodi","hsbr14","sparare","spit"],
    "CTL":  ["legno","tavolato","soletta su legno","preforo","avvitare"],
    "CTL MAXI": ["legno","tavolato 2 cm","maxi","dente lungo"],
    "CTCEM": ["laterocemento","travetto","foro","vite","dentato","boiacca","malta"],
    "VCEM": ["laterocemento","foro","vite","p560 su vcem","sparato vcem"],
    "P560": ["p560","spit","chiodatrice","hsbr14","sparo","test di posa"],
    "DIAPASON": ["diapason","piastra","rinforzo"],
    "GTS": ["gts","trave solaio","giunzioni"],
    "ACCESSORI": ["punte","rondelle","adattatori","kit","accessori"],
    "CONFRONTO": ["differenza","vs","meglio","quale scelgo","confronto"],
    "PROBLEMATICHE": ["errore","problema","non entra","non risponde","extra data","503"],
    "KILLER": ["sparato vcem","errore killer","ho sparato","chiodo sbagliato"]
}

# Antitema: parole che escludono certi item (es. la “sede” non deve rispondere a “ordinare”)
ANTI = {
    "COMM-0001": ["ordinare","acquistare","preventivo","listino","prezzo","comprare","ordine"],
}

# ---------- Gold Narration Booster ----------
def is_too_short(resp: str) -> bool:
    # Se la risposta ha meno di 280 caratteri o meno di 3 righe, la consideriamo "secca"
    return len(resp.strip()) < 280 or resp.count("\n") < 3

def gold_boost(family: str, domanda: str, resp: str) -> str:
    if not is_too_short(resp):
        return resp  # già ok

    # Template GOLD sintetici per famiglia
    templates = {
        "COMM": {
            "contesto": "Le richieste COMM riguardano contatti, ordini, listini e supporto istituzionale Tecnaria.",
            "istruzioni": "Per piccoli ordini invia email a info@tecnaria.com con codice, quantità, cantiere e urgenza; per forniture complesse chiedi conferma disponibilità/tempi all’ufficio commerciale.",
            "alternativa": "Se non conosci i codici, allega foto o sezione del solaio: il tecnico indica famiglia e misura.",
            "check": ["codice + quantità","cantiere","tempi richiesti","eventuali accessori (P560/HSBR14/punte)"],
            "nota": "Ordine piccolo → canale veloce; ordine complesso → validazione tecnica prima."
        },
        "CTCEM": {
            "contesto": "CTCEM è la famiglia per solai in laterocemento con travetti: fissaggio meccanico, non a sparo.",
            "istruzioni": "Eseguire foro nel travetto, pulizia accurata, avvitatura secondo schema Tecnaria; getto C25/30 con rete a metà.",
            "alternativa": "Se i travetti sono ammalorati, prevedere ripristino prima della posa.",
            "check": ["foro pulito","vite corretta","rete a metà spessore","no P560, no resine"],
            "nota": "CTCEM quando serve più interblocco ‘dentato’ nel travetto."
        },
        "VCEM": {
            "contesto": "VCEM è la soluzione a vite per laterocemento quando serve fissaggio verticale e rapido.",
            "istruzioni": "Forare, pulire, avvitare secondo schema; getto C25/30 con rete a metà.",
            "alternativa": "Se hai sparato per errore, rimuovi chiodo, ripristina e posa con vite.",
            "check": ["no P560","foro pulito","vite idonea","rete a metà"],
            "nota": "VCEM per fissaggio a vite; se serve più presa nel travetto, valuta CTCEM."
        },
        "CTF": {
            "contesto": "CTF collega travi in acciaio a solette collaboranti, spesso con lamiera grecata.",
            "istruzioni": "Posa a secco con SPIT P560: 2 chiodi HSBR14 per connettore, lamiera ben serrata.",
            "alternativa": "Se la lamiera si muove, fissarla prima (viti/saldature puntuali).",
            "check": ["teste chiodi a filo piastra","perpendicolarità","rete a metà","cls ≥ C25/30"],
            "nota": "Tarare la P560 con 2–3 tiri di prova sullo stesso pacchetto."
        },
        "CTL": {
            "contesto": "CTL è per travi in legno con soletta collaborante.",
            "istruzioni": "Preforo guidato se necessario, avvitatura nel legno, posa rete, getto 5 cm circa.",
            "alternativa": "Tavolato 2 cm o soletta 5–6 cm? valuta CTL MAXI.",
            "check": ["legno sano","preforo se legno duro","rete a metà","cls C25/30"],
            "nota": "CTL per legno sano; se serve più dente nel calcestruzzo, CTL MAXI."
        }
    }

    t = templates.get(family)
    if not t:
        # fallback generico
        t = {
            "contesto":"Risposta arricchita in stile GOLD.",
            "istruzioni":"Applica le istruzioni ufficiali di posa e le raccomandazioni di sicurezza Tecnaria.",
            "alternativa":"Se il caso non rientra nello schema tipico, contatta l’ufficio tecnico.",
            "check":["coerenza supporto","istruzioni posa rispettate","rete a metà spessore","cls conforme"],
            "nota":"Instradamento automatico GOLD attivo."
        }

    chunks = []
    chunks.append(f"**Contesto:** {t['contesto']}")
    chunks.append(f"**Istruzioni pratiche:** {t['istruzioni']}")
    if t.get("alternativa"):
        chunks.append(f"**Alternativa:** {t['alternativa']}")
    if t.get("check"):
        cl = "".join([f"\n- [✔] {c}" for c in t["check"]])
        chunks.append(f"**Checklist:**{cl}")
    chunks.append(f"**Nota RAG:** {t['nota']}")

    # inseriamo anche la risposta originale (se c’è qualcosa di utile) in apertura
    base = resp.strip()
    if base:
        base = re.sub(r"\s+\n", "\n", base)
        base = f"{base}\n\n"
    return base + "\n".join(chunks)

# ---------- Instradamento domanda -> famiglia ----------

def guess_families(question: str) -> List[str]:
    qn = norm(question)
    # punteggio semplice per famiglia
    scores = []
    for fam, kws in FAM_ROUTE.items():
        score = sum(1 for k in kws if k in qn)
        scores.append((fam, score))
    # fallback: se non c'è nulla, prova famiglie “CONFRONTO” se compaiono vs/meglio/differenza
    scores.sort(key=lambda x: x[1], reverse=True)
    if scores and scores[0][1] > 0:
        # prendi le top 3 per sicurezza
        return [s for s,_ in scores[:3] if _>0]
    # default: tutte (ma CONFRONTO prima se c'è “vs/differenza/meglio”)
    if any(w in qn for w in ["vs","differenza","meglio","quale scelgo","confronto"]):
        ordered = ["CONFRONTO"] + [f for f in FAM_ROUTE if f!="CONFRONTO"]
        return ordered
    return list(FAM_ROUTE.keys())

def antitheme_exclude(item: Dict[str,Any], question: str) -> bool:
    # esclude item noti (es: COMM-0001 non deve rispondere a "ordinare")
    bad = ANTI.get(item.get("id",""), [])
    return any(w in norm(question) for w in bad)

def pick_item(question: str) -> Dict[str,Any]:
    fams = guess_families(question)
    q_tokens = tokenize(question)

    # filtra gli item per famiglie candidate
    pool = [it for it in ITEMS if it.get("family") in fams]
    if not pool:
        pool = ITEMS[:]  # worst case

    # calcola similarità testuale su (domanda + keywords)
    scored = []
    for it in pool:
        if antitheme_exclude(it, question):
            continue
        trig = it.get("trigger", {})
        kw = trig.get("keywords", []) if isinstance(trig, dict) else []
        it_text = f"{it.get('domanda','')} {' '.join(kw)}"
        score = cosine(q_tokens, tokenize(it_text))
        peso = float(trig.get("peso", 0.5)) if isinstance(trig, dict) else 0.5
        scored.append((score, peso, it))

    if not scored:
        # nulla di buono: prendi l'item con massima copertura COMM-0002 oppure fallback COMM-0001
        fallback = [it for it in ITEMS if it.get("id")=="COMM-0002"] or [it for it in ITEMS if it.get("id")=="COMM-0001"]
        return fallback[0] if fallback else ITEMS[0]

    # ordina: prima similarità, poi peso
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return scored[0][2]

# ---------- API ----------

class AskBody(BaseModel):
    question: str

UI_HTML = """
<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8"/>
  <title>Tecnaria Sinapsi — Q/A</title>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <style>
    body{font-family:ui-sans-serif,system-ui,Segoe UI,Roboto,Arial;background:#0d0d0d;color:#eee;margin:0}
    header{background:linear-gradient(90deg,#ff7a00,#111);padding:24px 16px}
    h1{margin:0;font-size:20px}
    .wrap{max-width:980px;margin:0 auto;padding:20px}
    .card{background:#151515;border:1px solid #222;border-radius:16px;padding:16px;box-shadow:0 10px 30px rgba(0,0,0,.3)}
    .pill{display:inline-block;background:#262626;border:1px solid #333;padding:6px 10px;border-radius:999px;margin-right:8px;font-size:12px;color:#bbb}
    input,button{font-size:16px}
    input{width:100%;padding:14px;border-radius:12px;border:1px solid #333;background:#111;color:#eee}
    button{padding:12px 16px;border-radius:12px;border:1px solid #444;background:#ff7a00;color:#111;font-weight:700;cursor:pointer}
    .answer{white-space:pre-wrap;line-height:1.5}
    .meta{font-size:12px;color:#aaa;margin-top:8px}
  </style>
</head>
<body>
<header><div class="wrap"><h1>Tecnaria Sinapsi — Q/A (Router GOLD)</h1></div></header>
<div class="wrap">
  <div class="card">
    <div style="margin-bottom:10px;">
      <span class="pill">Perfezione</span><span class="pill">CTF • CTL • CTCEM • VCEM • P560</span><span class="pill">IT ⇄ EN/FR/DE/ES (runtime)</span>
    </div>
    <input id="q" placeholder="Fai una domanda (es. Come faccio un piccolo ordine?)"/>
    <div style="margin-top:10px;"><button onclick="ask()">Chiedi</button></div>
    <div id="ans" class="answer" style="margin-top:16px;"></div>
    <div id="meta" class="meta"></div>
  </div>
</div>
<script>
async function ask(){
  const q = document.getElementById('q').value || '';
  const r = await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q})});
  const j = await r.json();
  document.getElementById('ans').textContent = j.answer || '(nessuna risposta)';
  document.getElementById('meta').textContent = `→ ID ${j.id} • Famiglia ${j.family}`;
}
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return UI_HTML

@app.get("/ping", response_class=PlainTextResponse)
def ping():
    return "alive"

@app.get("/status", response_class=JSONResponse)
def status():
    return {"items": len(ITEMS), "data_file": str(DATA_FILE)}

@app.post("/ask", response_class=JSONResponse)
def ask(body: AskBody):
    q = (body.question or "").strip()
    if not q:
        raise HTTPException(400, "question vuota")
    item = pick_item(q)
    family = item.get("family","")
    base_answer = item.get("risposta","").strip()

    # booster GOLD se la risposta è asciutta
    final_answer = gold_boost(family, q, base_answer)

    return {
        "id": item.get("id"),
        "family": family,
        "answer": final_answer
    }
