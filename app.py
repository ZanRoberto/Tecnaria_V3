# app.py
# TECNARIA_GOLD_SEMANTIC v2.1 — DEDUPE FIX
# - UI su "/"
# - /health
# - /qa/search (top-k con DEDUPE)
# - /qa/ask (motore semantico + composizione GOLD + DEDUPE sezioni)
# - /debug/datasets (conteggi)
# - /debug/classify?q=... (famiglia/intenti/anteprima top)
#
# Dataset richiesti in repo:
#   static/data/ctf_gold.json
#   static/data/ctl_gold.json
#   static/data/p560_gold.json
#
# Start Render:
#   gunicorn -k uvicorn.workers.UvicornWorker app:app

from __future__ import annotations

import json
import pathlib
import re
from typing import List, Dict, Any, Optional, Tuple
from functools import lru_cache

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# ---------------------------------------------------------------
# Config
# ---------------------------------------------------------------
APP_DIR = pathlib.Path(__file__).parent
DATA_DIR = APP_DIR / "static" / "data"
GOLD_FILES = ["ctf_gold.json", "ctl_gold.json", "p560_gold.json"]

# Intents e famiglie
INTENT_LEXICON: Dict[str, List[str]] = {
    "codes":    ["codici", "codice", "catalogo", "sigle", "modelli", "modello", "nomenclatura", "sku", "listino", "tabella"],
    "sequence": ["sequenza", "ordine", "fasi", "passaggi", "step", "procedura", "posa corretta", "come si posa", "posa"],
    "taratura": ["taratura", "potenza", "colpo a vuoto", "sporgenti", "ritaro", "penetr", "test di prova"],
    "viti":     ["vite", "viti", "lunghezza", "ø10", "diametro", "100", "120", "140"],
    "sicurezza":["dpi", "sicurezza", "perimetro", "occhiali", "guanti", "udito"],
    "collaudo": ["collaudo", "verifica", "controlli", "registro", "giornale lavori", "foto"],
    "errori":   ["errori", "rischi", "attenzione", "avvertenze", "problema", "sporge", "rimbalzo"],
}

FAMILY_LEXICON: Dict[str, List[str]] = {
    "CTF":      ["ctf", "acciaio", "lamiera", "hsbr14", "piastra", "s235", "s275", "s355", "trave"],
    "CTL":      ["ctl", "legno", "soletta", "rete", "connettore legno", "viti"],
    "CTL MAXI": ["maxi", "ctl maxi", "tavolato", "assito"],
    "P560":     ["p560", "chiodatrice", "sparo", "spit"],
}

CATALOG_TOKENS = INTENT_LEXICON["codes"]

# ---------------------------------------------------------------
# Models
# ---------------------------------------------------------------
class QAItem(BaseModel):
    qid: Optional[str] = None
    family: Optional[str] = None
    question: str
    answer: str
    tags: Optional[List[str]] = []
    level: Optional[str] = None
    source_hint: Optional[str] = None

class SearchResponse(BaseModel):
    query: str
    count: int
    results: List[QAItem]

class AskResponse(BaseModel):
    query: str
    result: Optional[QAItem] = None
    found: bool

# ---------------------------------------------------------------
# App
# ---------------------------------------------------------------
app = FastAPI(
    title="Tecnaria Q/A Service — TECNARIA_GOLD_SEMANTIC",
    version="2.1.0",
    description="UI + API con ranking, motore semantico e deduplica per CTF/CTL/CTL MAXI/P560."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------
def _iter_candidate_files() -> List[pathlib.Path]:
    cand: List[pathlib.Path] = []
    for name in GOLD_FILES:
        p = DATA_DIR / name
        if p.exists() and p.is_file():
            cand.append(p)
    for p in sorted(DATA_DIR.glob("*_gold.json")):
        if p not in cand:
            cand.append(p)
    if not cand:
        raise FileNotFoundError(
            f"Nessun dataset GOLD trovato. Attesi: {', '.join(GOLD_FILES)} "
            f"oppure qualsiasi *_gold.json in {DATA_DIR}"
        )
    return cand

def _normalize_records(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict) and isinstance(raw.get("items"), list):
        return raw["items"]
    if isinstance(raw, list):
        return raw
    raise ValueError("Formato dataset non valido.")

@lru_cache(maxsize=1)
def load_gold() -> List[QAItem]:
    items: List[QAItem] = []
    seen = set()
    for p in _iter_candidate_files():
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        data = json.loads(p.read_text(encoding="utf-8"))
        for rec in _normalize_records(data):
            q = (rec.get("question") or "").strip()
            a = (rec.get("answer") or "").strip()
            if not q or not a:
                continue
            items.append(QAItem(
                qid=rec.get("qid"),
                family=rec.get("family"),
                question=q,
                answer=a,
                tags=rec.get("tags") or [],
                level=rec.get("level"),
                source_hint=rec.get("source_hint"),
            ))
    if not items:
        raise ValueError("Nessun item valido caricato.")
    return items

# ---------------------------------------------------------------
# Util: normalizzazione e deduplica
# ---------------------------------------------------------------
STEEL_RE = re.compile(r"\bS(235|275|355)\b", re.I)
DECK_RE  = re.compile(r"\bH(55|75)\b", re.I)
THICK_RE = re.compile(r"(1×1,5|2×1,0)", re.I)
NUM_RE   = re.compile(r"\b\d+(?:[,\.]\d+)?\b")

STOP_TOKENS = {
    "ctf","ctl","maxi","p560","lamiera","trave","posa","connettore","connettori","chiodi",
    "hsbr14","spit","rete","getto","calcestruzzo","sparo","kit","tecnaria","acciaio",
    "sequenza","istruzioni","parametri","sicurezza","errori","checklist","doppia"
}

def normalize_question(text: str) -> str:
    s = text.lower()
    s = STEEL_RE.sub("", s)
    s = DECK_RE.sub("", s)
    s = THICK_RE.sub("", s)
    s = NUM_RE.sub("", s)
    s = re.sub(r"[^a-zà-ù0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def jaccard(a: str, b: str) -> float:
    ta = {t for t in a.split() if t not in STOP_TOKENS}
    tb = {t for t in b.split() if t not in STOP_TOKENS}
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0

def dedupe_items(items: List[QAItem], sim_threshold: float = 0.80, limit: int = 5) -> List[QAItem]:
    out: List[QAItem] = []
    keys: List[str] = []
    for it in items:
        nk = normalize_question(it.question)
        duplicate = False
        for prevk in keys:
            if jaccard(nk, prevk) >= sim_threshold:
                duplicate = True
                break
        if not duplicate:
            out.append(it)
            keys.append(nk)
        if len(out) >= limit:
            break
    return out

# ---------------------------------------------------------------
# Classificazione semantica
# ---------------------------------------------------------------
def classify_family(query: str) -> str:
    q = query.lower()
    scores: Dict[str, float] = {k: 0.0 for k in FAMILY_LEXICON}
    for fam, toks in FAMILY_LEXICON.items():
        for t in toks:
            if t in q:
                scores[fam] += 1.0
    if any(t in q for t in ["lamiera", "chiodatrice", "hsbr14"]) and "ctf" not in q and "p560" in q:
        scores["CTF"] += 1.0
    if any(t in q for t in ["tavolato", "assito"]) and "ctl" in q:
        scores["CTL MAXI"] += 0.5
    fam = max(scores.items(), key=lambda kv: kv[1])[0]
    if all(v == 0 for v in scores.values()) and "p560" in q:
        fam = "P560"
    return fam

def classify_intents(query: str) -> List[str]:
    q = query.lower()
    found: List[str] = []
    for intent, toks in INTENT_LEXICON.items():
        if any(t in q for t in toks):
            found.append(intent)
    if "come si posa" in q or "posa corretta" in q:
        if "sequence" not in found:
            found.insert(0, "sequence")
    if not found:
        if any(k in q for k in ["posare", "posa", "montare", "installare"]):
            found.append("sequence")
        elif any(k in q for k in ["controllo", "verifica", "collaudo"]):
            found.append("collaudo")
    return found or ["sequence"]

def needs_catalog_fallback(query: str) -> bool:
    q = query.lower()
    return any(tok in q for tok in CATALOG_TOKENS)

# ---------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------
def _score(item: QAItem, ql: str) -> float:
    base = 0.0
    fam = (item.family or "").lower()
    if fam and fam in ql:
        base += 1.5
    for t in (item.tags or []):
        t0 = (t or "").lower()
        if t0 and t0 in ql:
            base += 0.8
    qtxt = (item.question or "").lower()
    atxt = (item.answer or "").lower()
    if ql in qtxt:
        base += 1.0
    tokens = {tok for tok in re.split(r"\W+", ql) if tok}
    for tok in tokens:
        if tok in qtxt:
            base += 0.35
        if tok in atxt:
            base += 0.20
    for k, b in [("p560", 0.5), ("hsbr14", 0.3), ("lamiera", 0.3), ("tavolato", 0.3), ("rete", 0.2)]:
        if k in ql:
            base += b
    return base

def _rank(query: str, k: int = 5) -> List[QAItem]:
    ql = (query or "").lower().strip()
    if not ql:
        return []
    items = load_gold()
    ranked = sorted(items, key=lambda it: _score(it, ql), reverse=True)
    return ranked[:max(1, k)]

def _rank_with_filters(query: str, fam: str, intents: List[str], k: int = 5) -> List[QAItem]:
    ql = (query or "").lower()
    items = load_gold()
    filtered = []
    for it in items:
        if fam and it.family and fam.lower().startswith((it.family or "").lower()[:3]):
            filtered.append(it)
        elif fam in ["P560", "CTL MAXI"] and it.family and fam.lower().startswith((it.family or "").lower()[:3]):
            filtered.append(it)
    base_pool = filtered or items
    def score2(it: QAItem) -> float:
        s = _score(it, ql)
        tags = " ".join(it.tags or []).lower()
        txt = (it.question + " " + it.answer).lower()
        for intent in intents:
            if intent in tags: s += 1.2
            if intent == "sequence" and any(k in txt for k in ["sequenza","procedura","passaggi","step"]): s += 0.9
            if intent == "viti" and any(k in txt for k in ["vite","viti","ø10","lunghezza"]): s += 0.9
            if intent == "taratura" and any(k in txt for k in ["taratura","potenza","colpo a vuoto"]): s += 0.9
            if intent == "sicurezza" and any(k in txt for k in ["dpi","sicurezza","perimetro","occhiali"]): s += 0.7
            if intent == "collaudo" and any(k in txt for k in ["collaudo","verifica","registro","fot"]): s += 0.7
            if intent == "errori" and any(k in txt for k in ["errore","errori","attenzione","rischio","rimbalzo","sporg"]): s += 0.7
        return s
    ranked = sorted(base_pool, key=score2, reverse=True)
    # DEDUPE a monte
    return dedupe_items(ranked, sim_threshold=0.80, limit=k)

# ---------------------------------------------------------------
# Risposte preconfezionate (catalogo / codici) + composizione
# ---------------------------------------------------------------
CATALOGO_ANSWER = """\
Ecco una **scheda rapida codici/modelli** per le famiglie presenti:

**CTF (acciaio)**
• Modello: **CTF** — fissaggio meccanico con **2 chiodi HSBR14** per connettore.  
• Posa: a secco con **SPIT P560** + kit/adattatori Tecnaria.  
• Contesti: trave acciaio con anima ≥ **6 mm**; con lamiera grecata: **1×1,5 mm** oppure **2×1,0 mm** ben serrata.  
• Note: niente resine; rete a metà spessore; cls **≥ C25/30**.

**CTL (legno) — standard**
• **CTL 12/030**, **12/040**, **12/050**, **12/060**  
• Fissaggio: **2 viti Ø10**; lunghezze tipiche **100/120 mm** (in base a interposti/tavolato).

**CTL MAXI (tavolato/assito)**
• **CTL MAXI 12/040**, **12/050**, **12/060**  
• Fissaggio: **2 viti Ø10**; lunghezze comuni **100/120/140 mm** (se assito ≥25–30 mm → 120/140).

**P560 (utensile)**
• **SPIT P560** con **kit Tecnaria**.  
• Taratura: **2–3 tiri di prova**; doppia chiodatura; DPI + perimetro **3 m**.

Se ti servono **codici articolo/SKU** per ordine/offerta, genero una tabella pronta (Famiglia · Modello · Viti/Chiodi · Note).
"""

def make_catalog_item(query: str) -> QAItem:
    return QAItem(
        qid="CAT-001",
        family="CATALOGO",
        question="Quali codici/modelli sono disponibili per CTF, CTL (standard/MAXI) e utensile P560?",
        answer=CATALOGO_ANSWER,
        tags=["codici","catalogo","modelli","sigle","CTL","CTF","P560"],
        level="sintesi",
        source_hint="Sintesi operativa famiglie e utensile."
    )

# --- Composizione GOLD con anti-duplicazione sezioni
SECTION_HEADERS = [
    ("contesto", r"(^|\n)\**\s*contesto\s*\**", "**Contesto**"),
    ("istruzioni", r"(^|\n)\**\s*istruzioni[^\n]*\s*\**", "**Istruzioni operative**"),
    ("parametri", r"(^|\n)\**\s*parametri[^\n]*\s*\**", "**Parametri consigliati**"),
    ("sicurezza", r"(^|\n)\**\s*sicurezza\s*\**", "**Sicurezza**"),
    ("errori", r"(^|\n)\**\s*errori[^\n]*\s*\**", "**Errori comuni**"),
    ("checklist", r"(^|\n)\**\s*checklist[^\n]*\s*\**", "**Checklist**"),
]

def extract_sections(text: str) -> Dict[str, str]:
    """
    Estrae blocchi per intestazioni note; se non trova, restituisce vuoto.
    """
    low = text.lower()
    sections: Dict[str, str] = {}
    # Trova posizioni intestazioni nel testo
    idx: List[Tuple[str, int, str]] = []
    for key, pattern, canon in SECTION_HEADERS:
        m = re.search(pattern, low, flags=re.IGNORECASE)
        if m:
            idx.append((key, m.start(), canon))
    if not idx:
        return sections
    idx.sort(key=lambda x: x[1])
    # Segmenta tra header i...i+1
    for i, (key, pos, canon) in enumerate(idx):
        start = pos
        end = idx[i+1][1] if i+1 < len(idx) else len(text)
        chunk = text[start:end].strip()
        # Rimuovi header duplicando quello canonico
        chunk = re.sub(r"^\**\s*[A-Za-zÀ-ÿ \/\-]+?\s*\**\s*", "", chunk, count=1).strip()
        sections[key] = f"{canon}\n{chunk}".strip()
    return sections

def compose_gold_answer(query: str, fam: str, intents: List[str], top_items: List[QAItem]) -> QAItem:
    """
    Composizione GOLD con anti-duplicazione:
    - Usa al max 3 item deduplicati
    - Per ogni sezione nota, inserisce UNA sola volta (prima utile)
    - Se sezione mancante, aggiunge fallback standardizzato
    """
    chosen = top_items[:3]  # già deduplicati a monte
    collected: Dict[str, str] = {}

    # 1) prova a estrarre sezioni dai migliori item
    for it in chosen:
        secs = extract_sections(it.answer)
        for key, block in secs.items():
            if key not in collected and block.strip():
                collected[key] = block.strip()

    # 2) fallback dove mancano sezioni
    if "contesto" not in collected:
        if fam.startswith("CTF"):
            collected["contesto"] = "**Contesto**\nI CTF si fissano a secco con **SPIT P560** e kit Tecnaria; ogni connettore con **2 chiodi HSBR14**. Con lamiera, questa va **ben serrata** per evitare rimbalzi."
        elif fam.startswith("CTL"):
            collected["contesto"] = "**Contesto**\nI CTL lavorano con soletta collaborante; fissaggio con **2 viti Ø10** per connettore; testa sopra la rete a metà spessore."
        elif fam == "P560":
            collected["contesto"] = "**Contesto**\nLa **SPIT P560** è l’utensile dedicato alla posa dei CTF; richiede taratura con tiri di prova e DPI."

    if "istruzioni" not in collected and "sequence" in intents:
        if fam.startswith("CTF"):
            collected["istruzioni"] = "**Istruzioni operative**\n1) Traccia maglia e verifica serraggio lamiera.\n2) Posiziona CTF ortogonale (sopra lamiera).\n3) Doppia chiodatura con P560.\n4) Verifica chiodi a filo e piastra aderente.\n5) Registra potenza/lotti/note."
        elif fam.startswith("CTL"):
            collected["istruzioni"] = "**Istruzioni operative**\n1) Posa CTL su tavolato, rete a metà spessore.\n2) 2 viti Ø10 (100/120/140 mm secondo interposti).\n3) Getta soletta ≥5 cm, vibrazione moderata.\n4) Verifica copriferro/quote."

    if "parametri" not in collected:
        if fam.startswith("CTF"):
            collected["parametri"] = "**Parametri consigliati**\n- Acciaio S235/S275/S355 con anima ≥ 6 mm\n- Lamiera 1×1,5 mm o 2×1,0 mm ben serrata\n- SPIT P560 + 2 chiodi HSBR14\n- Rete a metà spessore; cls ≥ C25/30"
        elif fam.startswith("CTL"):
            collected["parametri"] = "**Parametri consigliati**\n- Soletta ≥ 5 cm; cls ≥ C25/30; rete a metà spessore\n- Fissaggio: 2 viti Ø10; 100/120/140 mm secondo interposti\n- Testa connettore sopra rete, sotto filo superiore"

    if "sicurezza" not in collected and fam in ["CTF","P560"]:
        collected["sicurezza"] = "**Sicurezza**\n- DPI: occhiali EN166, guanti antitaglio, protezione udito\n- Perimetro 3 m; area sgombra; serrare lamiera con morsetti"

    if "errori" not in collected:
        if fam.startswith("CTF"):
            collected["errori"] = "**Errori comuni**\n- Lamiera non serrata → rimbalzo\n- Potenza insufficiente → chiodi sporgenti\n- Mancata doppia chiodatura"
        elif fam.startswith("CTL"):
            collected["errori"] = "**Errori comuni**\n- Vite troppo corta per interposti\n- Testa che interferisce con la rete\n- Vibrazione eccessiva del getto"

    if "checklist" not in collected:
        collected["checklist"] = "**Checklist**\n- 2–3 tiri di prova (taratura ok)\n- Aderenza piastra/testa corretta\n- Doppio fissaggio completato\n- Rete a metà spessore e DPI"

    # 3) Ordina sezioni e compone senza duplicati
    order = ["contesto","istruzioni","parametri","sicurezza","errori","checklist"]
    blocks = [collected[k] for k in order if k in collected]
    answer = "\n\n".join(blocks).strip()

    # 4) pulizia finale: rimuove header doppi adiacenti (ulteriore safety)
    for key, _, canon in SECTION_HEADERS:
        answer = re.sub(rf"({re.escape(canon)}\s*)+", f"{canon}\n", answer, flags=re.IGNORECASE)

    return QAItem(
        qid=chosen[0].qid if chosen else None,
        family=fam,
        question=query.strip(),
        answer=answer,
        tags=list(set((chosen[0].tags if chosen else []) + intents)),
        level="sintesi",
        source_hint="Composizione semantica da dataset GOLD (dedupe sezioni)"
    )

# ---------------------------------------------------------------
# UI
# ---------------------------------------------------------------
HTML_UI = r"""<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Tecnaria Q/A — GOLD Semantic</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, 'Helvetica Neue', Arial, sans-serif; margin: 0; background: #0b0c10; color: #eaf0f6; }
    header { padding: 20px; background: #101219; border-bottom: 1px solid #1c2030; }
    h1 { margin: 0; font-size: 20px; letter-spacing: .5px; }
    main { max-width: 1100px; margin: 0 auto; padding: 20px; }
    .card { background: #111622; border: 1px solid #1c2030; border-radius: 14px; padding: 16px; margin-bottom: 16px; box-shadow: 0 2px 10px rgba(0,0,0,.3); }
    .row { display: grid; grid-template-columns: 1fr auto; gap: 12px; align-items: center; }
    input, button { height: 44px; border-radius: 10px; border: 1px solid #283049; background: #0f1420; color: #eaf0f6; }
    input { padding: 0 12px; width: 100%; }
    button { padding: 0 18px; cursor: pointer; }
    .pill { display:inline-block; padding: 2px 8px; border: 1px solid #2e3754; border-radius: 999px; margin-right: 6px; font-size: 12px; color: #a9b6d3; }
    .q { font-weight: 600; margin-bottom: 6px; }
    .a { white-space: pre-wrap; line-height: 1.45; }
    .meta { font-size: 12px; color: #93a2c8; margin-top: 6px; }
    .err { color: #ff6b6b; }
    .muted { color:#93a2c8; font-size:13px; }
    .footer { margin-top: 24px; font-size: 12px; color: #7f8bb0; }
    .split { display:grid; grid-template-columns: 1fr 1fr; gap:16px; }
    @media (max-width: 900px) { .split { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
<header>
  <h1> Tecnaria Q/A — GOLD Semantic · <span class="muted" id="count">—</span> </h1>
  <div class="muted">Files: <span id="files">—</span> <span class="err" id="err"></span></div>
</header>

<main>
  <div class="card">
    <div class="row">
      <input id="q" placeholder='Domanda libera (es. “Sequenza posa CTF su lamiera 1×1,5” · “Che codici hanno i connettori?” · “P560 colpo a vuoto”)' />
      <button onclick="ask()">Chiedi</button>
    </div>
    <div class="muted" style="margin-top:8px">
      Suggerimenti: “Che codici hanno i connettori?”, “CTL MAXI tavolato 25–30 mm viti 120”, “CTF lamiera 2×1,0 mm S355”, “P560 DPI e taratura”.
    </div>
  </div>

  <div class="split">
    <div class="card">
      <h3>Top Risposte</h3>
      <div class="row" style="margin-bottom:8px">
        <input id="qsearch" placeholder="Cerca (top-5)..." />
        <button onclick="search()">Cerca</button>
      </div>
      <div id="results"></div>
    </div>

    <div class="card">
      <h3>Risposta Migliore</h3>
      <div id="best"></div>
    </div>
  </div>

  <div class="footer">
    Health: <a href="/health" target="_blank">/health</a> · API: <code>/qa/search</code>, <code>/qa/ask</code> · Debug: <a href="/debug/datasets" target="_blank">/debug/datasets</a> · Classify: <code>/debug/classify?q=...</code>
  </div>
</main>

<script>
async function hydrate() {
  try {
    const r = await fetch('/health');
    const d = await r.json();
    if (d.status === 'ok') {
      document.getElementById('count').textContent = d.items_loaded;
      document.getElementById('files').textContent = (d.files || []).join(', ');
    } else {
      document.getElementById('err').textContent = d.error || 'errore';
    }
  } catch (e) {
    document.getElementById('err').textContent = String(e);
  }
}

async function search() {
  const q = document.getElementById('qsearch').value.trim();
  if (!q) return;
  const r = await fetch(`/qa/search?q=${encodeURIComponent(q)}&k=5`);
  const data = await r.json();
  const root = document.getElementById('results');
  root.innerHTML = '';
  (data.results || []).forEach(it => {
    const el = document.createElement('div');
    el.className = 'card';
    el.innerHTML = `
      <div class="q">Q: ${it.question}</div>
      <div class="a">${it.answer.replace(/\\n/g,'<br/>')}</div>
      <div class="meta">
        <span class="pill">${it.family || 'n/a'}</span>
        ${(it.tags||[]).map(t => `<span class='pill'>${t}</span>`).join(' ')}
        ${it.qid ? `<span class='pill'>${it.qid}</span>` : ''}
      </div>`;
    root.appendChild(el);
  });
}

async function ask() {
  const q = document.getElementById('q').value.trim();
  if (!q) return;
  document.getElementById('results').innerHTML = ''; // reset elenco a ogni domanda
  const r = await fetch(`/qa/ask?q=${encodeURIComponent(q)}`);
  const data = await r.json();
  const best = document.getElementById('best');
  if (!data.found) {
    best.innerHTML = `<div class='err'>Nessun risultato.</div>`;
    return;
  }
  const it = data.result;
  best.innerHTML = `
    <div class="q">Q: ${it.question}</div>
    <div class="a">${it.answer.replace(/\\n/g,'<br/>')}</div>
    <div class="meta">
      <span class="pill">${it.family || 'n/a'}</span>
      ${(it.tags||[]).map(t => `<span class='pill'>${t}</span>`).join(' ')}
      ${it.qid ? `<span class='pill'>${it.qid}</span>` : ''}
    </div>`;
}

hydrate();
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def ui_root() -> HTMLResponse:
    return HTMLResponse(content=HTML_UI)

# ---------------------------------------------------------------
# Health + API
# ---------------------------------------------------------------
@app.get("/health", summary="Health JSON")
def health() -> Dict[str, Any]:
    try:
        n = len(load_gold())
        files = [p.name for p in _iter_candidate_files()]
        return {"service":"Tecnaria Q/A Service","status":"ok","items_loaded":n,"data_dir":str(DATA_DIR),"files":files}
    except Exception as e:
        return {"service":"Tecnaria Q/A Service","status":"error","error":str(e)}

@app.get("/qa/search", response_model=SearchResponse, summary="Top-k Q/A (ranking con DEDUPE)")
def qa_search(
    q: str = Query(..., min_length=2, description="Testo della ricerca"),
    k: int = Query(5, ge=1, le=25, description="Numero risultati")
) -> SearchResponse:
    try:
        ranked = _rank(q, k=max(k*3, 10))  # prendi più candidati...
        de_duplicated = dedupe_items(ranked, sim_threshold=0.80, limit=k)  # ...poi deduplica
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore durante la ricerca: {e}")
    return SearchResponse(query=q, count=len(de_duplicated), results=de_duplicated)

@app.get("/qa/ask", response_model=AskResponse, summary="Risposta migliore (semantica + DEDUPE)")
def qa_ask(q: str = Query(..., min_length=2, description="Domanda libera")) -> AskResponse:
    try:
        if needs_catalog_fallback(q):
            return AskResponse(query=q, result=make_catalog_item(q), found=True)

        fam = classify_family(q)
        intents = classify_intents(q)
        top_items = _rank_with_filters(q, fam=fam, intents=intents, k=5)  # già deduplicati
        composed = compose_gold_answer(q, fam=fam, intents=intents, top_items=top_items)
        return AskResponse(query=q, result=composed, found=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore durante la ricerca: {e}")

# ---------------------------------------------------------------
# Debug
# ---------------------------------------------------------------
@app.get("/debug/datasets", summary="Conteggi per file GOLD")
def debug_datasets() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        for p in _iter_candidate_files():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
                    out[p.name] = len(data["items"])
                elif isinstance(data, list):
                    out[p.name] = len(data)
                else:
                    out[p.name] = None
            except Exception as e:
                out[p.name] = f"error: {e}"
        out["_total_items_loaded"] = len(load_gold())
    except Exception as e:
        out["error"] = str(e)
    return out

@app.get("/debug/classify", summary="Mostra famiglia/intenti per una query")
def debug_classify(q: str) -> Dict[str, Any]:
    fam = classify_family(q)
    intents = classify_intents(q)
    top = [it.qid or it.question[:60] for it in _rank_with_filters(q, fam=fam, intents=intents, k=3)]
    return {"query": q, "family": fam, "intents": intents, "top_like": top}

# ---------------------------------------------------------------
# Local run
# ---------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
