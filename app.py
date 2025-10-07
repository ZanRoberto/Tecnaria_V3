# -*- coding: utf-8 -*-
import json, re, math
from pathlib import Path
from collections import Counter, defaultdict

# ====== CONFIG ======
BASE_PATH = Path("static/data")  # qui stanno i tuoi JSON
RULES_FILE   = BASE_PATH / "sinapsi_rules.json"
ROUTER_FILE  = BASE_PATH / "tecnaria_router_index.json"
CATALOG_FILE = BASE_PATH / "tecnaria_catalogo_unico.json"

def dataset_path_for_family(code: str) -> Path:
    return BASE_PATH / f"tecnaria_{code.lower()}_qa500.json"

# ====== IO ======
def load_json(path: Path):
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def safe_get(d, k, default=None):
    return d[k] if isinstance(d, dict) and k in d else default

# ====== TEXT ======
import re as _re
WORD_RE = _re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9\-\_]+")
def norm(s: str) -> str:
    return (s or "").lower().strip()
def tokenize(text: str):
    return [t for t in WORD_RE.findall(norm(text)) if t]

# ====== REGOLE ======
def match_rules(query: str) -> str | None:
    data = load_json(RULES_FILE)
    rules = data.get("rules", [])
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
                if _re.search(r.get("pattern", ""), query, _re.I):
                    return r.get("answer", "").strip()
            except _re.error:
                pass
    return None

# ====== ROUTER ======
def route_family(query: str) -> str:
    router = load_json(ROUTER_FILE)
    q = norm(query)
    for p in router.get("products", []):
        keys = [norm(p.get("code","")), norm(p.get("name","")), norm(p.get("family",""))]
        for k in filter(None, keys):
            if k in q:
                return p.get("code","")
    # euristiche utili
    if any(k in q for k in ["p560","chiodatrice","propulsori"]): return "SPIT-P560"
    if any(k in q for k in ["gts","manicotto","giunzione meccanica"]): return "GTS"
    if any(k in q for k in ["diapason","laterocemento","rinforzo solaio"]): return "DIAPASON"
    if any(k in q for k in ["mini-cem-e","minicem","calcestruzzo-calcestruzzo"]): return "MINI-CEM-E"
    if any(k in q for k in ["ctl","legno","legno-calcestruzzo"]): return "CTL"
    if any(k in q for k in ["ctf","connettore","solaio collaborante"]): return "CTF"
    return ""

# ====== SEMANTICO LITE (BM25) ======
class TinySearch:
    def __init__(self, docs, text_fn):
        self.docs = docs
        self.text_fn = text_fn
        self.N = len(docs)
        self.df, self.doc_tokens = Counter(), []
        for d in docs:
            toks = tokenize(text_fn(d))
            self.doc_tokens.append(toks)
            for t in set(toks): self.df[t] += 1
        self.idf = defaultdict(float)
        for t, df in self.df.items():
            self.idf[t] = math.log(1 + (self.N - df + 0.5) / (df + 0.5))

    def score(self, qtok, idx, k1=1.5, b=0.75):
        toks = self.doc_tokens[idx]
        if not toks: return 0.0
        tf, dl = Counter(toks), len(toks)
        avgdl = (sum(len(x) for x in self.doc_tokens)/max(self.N,1)) if self.N else 1
        score = 0.0
        for t in qtok:
            if t not in tf: continue
            idf = self.idf.get(t, 0.0)
            denom = tf[t] + k1*(1 - b + b*dl/avgdl)
            score += idf * (tf[t]*(k1+1)) / (denom if denom else 1.0)
        return score

    def top1(self, query: str):
        qtok = tokenize(query)
        best = (-1.0, None)
        for i, d in enumerate(self.docs):
            s = self.score(qtok, i)
            if s > best[0]: best = (s, d)
        return best[1], best[0]

def semantic_pick(query: str, qa_list: list[dict]) -> dict | None:
    if not qa_list: return None
    def text_fn(d):
        return " ".join([
            safe_get(d,"q",""), safe_get(d,"a",""),
            safe_get(d,"category",""), " ".join(safe_get(d,"tags",[]))
        ])
    ts = TinySearch(qa_list, text_fn)
    best, score = ts.top1(query)
    return best if score and score > 0.5 else None

# ====== NARRATIVA ======
def compose_answer(hit: dict) -> str:
    a = (hit or {}).get("a","").strip()
    if not a: return ""
    if not a.endswith((".", "!", "?")): a += "."
    return a + " — Tecnaria S.p.A., Bassano del Grappa. Per i dettagli operativi: consultare schede e manuali ufficiali."

# ====== PIPELINE ======
def ask(query: str) -> str:
    ans = match_rules(query)
    if ans: return compose_answer({"a": ans})
    family = route_family(query)
    if family:
        ds = dataset_path_for_family(family)
        data = load_json(ds)
        hit = semantic_pick(query, data.get("qa", []))
        if hit: return compose_answer(hit)
    catalog = load_json(CATALOG_FILE)
    all_qa = []
    for item in catalog.get("items", []):
        all_qa.extend(item.get("qa", []))
    hit = semantic_pick(query, all_qa)
    if hit: return compose_answer(hit)
    return "Non ho trovato la risposta nei contenuti Tecnaria. Dimmi esattamente cosa ti serve e la aggiungo subito alla base."

# ====== FASTAPI app (quella che chiede Render) ======
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Tecnaria Sinapsi", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/ask")
def http_ask(q: str):
    return {"answer": ask(q)}
