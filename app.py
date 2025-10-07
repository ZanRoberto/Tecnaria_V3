# -*- coding: utf-8 -*-
"""
Tecnaria Sinapsi – app.py
- Legge regole (/static/data/sinapsi_rules.json)
- Instrada tramite router (/static/data/tecnaria_router_index.json)
- Ricerca nelle Q&A per famiglia (tecnaria_<code>_qa500.json)
- Fallback su catalogo unico (/static/data/tecnaria_catalogo_unico.json)
- Espone API FastAPI: /, /health, /ask?q=...
"""

import json, math, re
from pathlib import Path
from collections import Counter, defaultdict

# =========================
# CONFIG PERCORSI DATI
# =========================
BASE_PATH   = Path("static/data")  # cartella con i JSON
RULES_FILE  = BASE_PATH / "sinapsi_rules.json"
ROUTER_FILE = BASE_PATH / "tecnaria_router_index.json"
CATALOG_FILE= BASE_PATH / "tecnaria_catalogo_unico.json"

def dataset_path_for_family(code: str) -> Path:
    """Path del dataset della famiglia (es. 'CTF' -> tecnaria_ctf_qa500.json)."""
    return BASE_PATH / f"tecnaria_{code.lower()}_qa500.json"


# =========================
# UTIL / IO
# =========================
def load_json(path: Path):
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def safe_get(d, k, default=None):
    return d[k] if isinstance(d, dict) and k in d else default

def norm(s: str) -> str:
    return (s or "").lower().strip()

WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9\-\_]+")
def tokenize(text: str):
    return [t for t in WORD_RE.findall(norm(text)) if t]


# =========================
# REGOLE (override)
# =========================
def match_rules(query: str):
    data = load_json(RULES_FILE)
    rules = data.get("rules", [])
    # ordina priorità discendente
    rules = sorted(rules, key=lambda r: r.get("priority", 0), reverse=True)
    q = norm(query)

    for r in rules:
        patt = norm(r.get("pattern", ""))
        mode = r.get("mode", "contains").lower()
        if not patt:
            continue
        if mode == "contains" and patt in q:
            return r.get("answer", "").strip()
        if mode == "regex":
            try:
                if re.search(r.get("pattern", ""), query, re.I):
                    return r.get("answer", "").strip()
            except re.error:
                # pattern non valido: ignoro
                continue
    return None


# =========================
# ROUTER (famiglia)
# =========================
def route_family(query: str) -> str:
    router = load_json(ROUTER_FILE)
    q = norm(query)

    # matching diretto su code / name / family
    for p in router.get("products", []):
        code   = norm(p.get("code", ""))
        name   = norm(p.get("name", ""))
        family = norm(p.get("family", ""))
        for key in (code, name, family):
            if key and key in q:
                return p.get("code", "")

    # euristiche utili quando la famiglia non è scritta esplicitamente
    if any(k in q for k in ["p560", "chiodatrice", "propulsori", "propulsore"]):
        return "SPIT-P560"
    if any(k in q for k in ["gts", "manicotto", "giunzione meccanica"]):
        return "GTS"
    if any(k in q for k in ["diapason", "laterocemento", "rinforzo solaio"]):
        return "DIAPASON"
    if any(k in q for k in ["mini-cem-e", "minicem", "calcestruzzo-calcestruzzo"]):
        return "MINI-CEM-E"
    if any(k in q for k in ["ctl", "legno-calcestruzzo", "legno"]):
        return "CTL"
    if any(k in q for k in ["ctf", "solaio collaborante", "connettore"]):
        return "CTF"

    return ""  # non identificata


# =========================
# SEMANTICO LITE (BM25)
# =========================
class TinySearch:
    def __init__(self, docs, text_fn):
        self.docs = docs
        self.text_fn = text_fn
        self.N = len(docs)
        self.df = Counter()
        self.doc_tokens = []
        for d in docs:
            toks = tokenize(text_fn(d))
            self.doc_tokens.append(toks)
            for t in set(toks):
                self.df[t] += 1
        self.idf = defaultdict(float)
        for t, df in self.df.items():
            # idf BM25 con smoothing
            self.idf[t] = math.log(1 + (self.N - df + 0.5) / (df + 0.5))

    def score(self, qtok, idx, k1=1.5, b=0.75):
        toks = self.doc_tokens[idx]
        if not toks:
            return 0.0
        tf = Counter(toks)
        dl = len(toks)
        avgdl = (sum(len(x) for x in self.doc_tokens)/max(self.N,1)) if self.N else 1.0
        s = 0.0
        for t in qtok:
            if t not in tf:
                continue
            idf = self.idf.get(t, 0.0)
            denom = tf[t] + k1*(1 - b + b*dl/avgdl)
            s += idf * (tf[t]*(k1+1)) / (denom if denom else 1.0)
        return s

    def top1(self, query: str):
        qtok = tokenize(query)
        best_score = -1.0
        best_doc = None
        for i, d in enumerate(self.docs):
            sc = self.score(qtok, i)
            if sc > best_score:
                best_score, best_doc = sc, d
        return best_doc, best_score


def semantic_pick(query: str, qa_list: list[dict]):
    if not qa_list:
        return None

    def text_fn(d):
        # indicizza domanda + risposta + meta (category/tags)
        return " ".join([
            safe_get(d, "q", ""),
            safe_get(d, "a", ""),
            safe_get(d, "category", ""),
            " ".join(safe_get(d, "tags", []))
        ])

    ts = TinySearch(qa_list, text_fn)
    best, score = ts.top1(query)
    # soglia minima per evitare rumore
    return best if score and score > 0.5 else None


# =========================
# COMPOSIZIONE RISPOSTA
# =========================
def compose_answer(hit: dict) -> str:
    a = (hit or {}).get("a", "").strip()
    if not a:
        return ""
    if not a.endswith((".", "!", "?")):
        a += "."
    # firma aziendale coerente
    return a + " — Tecnaria S.p.A., Bassano del Grappa. Per i dettagli operativi: consultare schede e manuali ufficiali."


# =========================
# PIPELINE PRINCIPALE
# =========================
def ask(query: str) -> str:
    # 1) override da regole
    ans = match_rules(query)
    if ans:
        return compose_answer({"a": ans})

    # 2) routing per famiglia
    family = route_family(query)
    if family:
        ds = dataset_path_for_family(family)
        data = load_json(ds)
        hit = semantic_pick(query, data.get("qa", []))
        if hit:
            return compose_answer(hit)

    # 3) fallback catalogo unico
    catalog = load_json(CATALOG_FILE)
    all_qa = []
    for item in catalog.get("items", []):
        all_qa.extend(item.get("qa", []))
    hit = semantic_pick(query, all_qa)
    if hit:
        return compose_answer(hit)

    # 4) miss finale
    return "Non ho trovato la risposta nei contenuti Tecnaria. Dimmi esattamente cosa ti serve e la aggiungo subito alla base."


# =========================
# FASTAPI (per Gunicorn/Uvicorn)
# =========================
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Tecnaria Sinapsi", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)

@app.get("/")
def root():
    return {
        "name": "Tecnaria Sinapsi",
        "status": "ok",
        "endpoints": {
            "health": "/health",
            "ask": "/ask?q=...",
            "docs": "/docs"
        }
    }

@app.get("/health")
def health():
    # check rapido presenza file principali (non blocca l'avvio)
    return {
        "status": "ok",
        "rules": RULES_FILE.exists(),
        "router": ROUTER_FILE.exists(),
        "catalog": CATALOG_FILE.exists()
    }

@app.get("/ask")
def http_ask(q: str = Query(..., description="Domanda da porre al motore Tecnaria")):
    return {"answer": ask(q)}
