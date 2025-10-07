# -*- coding: utf-8 -*-
import json, re, sys, argparse, math
from pathlib import Path
from collections import Counter, defaultdict

# ====== CONFIG ======
BASE_PATH = Path("static/data")  # cartella dei JSON
RULES_FILE   = BASE_PATH / "sinapsi_rules.json"
ROUTER_FILE  = BASE_PATH / "tecnaria_router_index.json"
CATALOG_FILE = BASE_PATH / "tecnaria_catalogo_unico.json"

def dataset_path_for_family(code: str) -> Path:
    return BASE_PATH / f"tecnaria_{code.lower()}_qa500.json"

# ====== IO ======
def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def safe_get(d, k, default=None):
    return d[k] if isinstance(d, dict) and k in d else default

# ====== NORMALIZZAZIONE TESTO ======
WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9\-\_]+")

def norm(s: str) -> str:
    return (s or "").lower().strip()

def tokenize(text: str):
    return [t for t in WORD_RE.findall(norm(text)) if t]

# ====== MATCH REGOLE (override) ======
def match_rules(query: str, rules_path: Path) -> str | None:
    if not rules_path.exists():
        return None
    data = load_json(rules_path)
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
                if re.search(r.get("pattern", ""), query, re.I):
                    return r.get("answer", "").strip()
            except re.error:
                pass
    return None

# ====== ROUTING PER FAMIGLIA ======
def route_family(query: str, router_path: Path) -> str:
    if not router_path.exists():
        return ""
    router = load_json(router_path)
    q = norm(query)
    for p in router.get("products", []):
        code   = norm(p.get("code", ""))
        name   = norm(p.get("name", ""))
        family = norm(p.get("family", ""))
        if not code:
            continue
        for key in filter(None, [code, name, family]):
            if key and key in q:
                return p["code"]
    # euristiche utili
    if any(k in q for k in ["chiodatrice", "p560", "propulsore", "propulsori"]):
        return "SPIT-P560"
    if any(k in q for k in ["manicotto", "gts", "giunzione meccanica"]):
        return "GTS"
    if any(k in q for k in ["diapason", "rinforzo solaio", "laterocemento"]):
        return "DIAPASON"
    if any(k in q for k in ["mini-cem-e", "minicem", "collaborazione calcestruzzo"]):
        return "MINI-CEM-E"
    if any(k in q for k in ["ctf", "connettore", "solaio collaborante"]):
        return "CTF"
    if any(k in q for k in ["ctl", "legno", "legno-calcestruzzo", "soletta su travi in legno"]):
        return "CTL"
    return ""

# ====== SEMANTICO LEGGERO (BM25-lite) ======
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
            self.idf[t] = math.log(1 + (self.N - df + 0.5) / (df + 0.5))

    def score(self, query_tokens, idx, k1=1.5, b=0.75):
        toks = self.doc_tokens[idx]
        if not toks:
            return 0.0
        tf = Counter(toks)
        dl = len(toks)
        avgdl = (sum(len(x) for x in self.doc_tokens) / max(self.N,1)) if self.N else 1
        score = 0.0
        for t in query_tokens:
            if t not in tf:
                continue
            idf = self.idf.get(t, 0.0)
            denom = tf[t] + k1*(1 - b + b*dl/avgdl)
            score += idf * (tf[t]*(k1+1)) / (denom if denom else 1.0)
        return score

    def top1(self, query: str):
        qtok = tokenize(query)
        best = (-1.0, None)
        for i, d in enumerate(self.docs):
            s = self.score(qtok, i)
            if s > best[0]:
                best = (s, d)
        return best[1], best[0]

def semantic_pick(query: str, qa_list: list[dict]) -> dict | None:
    if not qa_list:
        return None
    def text_fn(d):
        parts = [safe_get(d, "q",""), safe_get(d, "a",""),
                 safe_get(d, "category",""), " ".join(safe_get(d, "tags", []))]
        return " ".join(parts)
    ts = TinySearch(qa_list, text_fn)
    best, score = ts.top1(query)
    return best if score and score > 0.5 else None

# ====== COMPOSIZIONE RISPOSTA ======
def compose_answer(hit: dict) -> str:
    a = (hit or {}).get("a", "").strip()
    if not a:
        return ""
    if not a.endswith((".", "!", "?")):
        a += "."
    closing = " — Tecnaria S.p.A., Bassano del Grappa. Per i dettagli operativi: consultare schede e manuali ufficiali."
    return a + closing

# ====== PIPELINE ======
def ask(query: str) -> str:
    # 1) regole
    ans = match_rules(query, RULES_FILE)
    if ans:
        return compose_answer({"a": ans})
    # 2) routing famiglia
    family = route_family(query, ROUTER_FILE)
    if family:
        path = dataset_path_for_family(family)
        if path.exists():
            data = load_json(path)
            hit = semantic_pick(query, data.get("qa", []))
            if hit:
                return compose_answer(hit)
    # 3) fallback: catalogo unico
    if CATALOG_FILE.exists():
        catalog = load_json(CATALOG_FILE)
        all_qa = []
        for item in catalog.get("items", []):
            all_qa.extend(item.get("qa", []))
        hit = semantic_pick(query, all_qa)
        if hit:
            return compose_answer(hit)
    # 4) miss
    return "Non ho trovato la risposta nei contenuti Tecnaria. Dimmi esattamente cosa ti serve e la aggiungo subito alla base."

# ====== FASTAPI (app per Gunicorn/Uvicorn) ======
try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    app = FastAPI(title="Tecnaria Sinapsi", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
    )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ask")
    def http_ask(q: str):
        return {"answer": ask(q)}
except Exception as e:
    # se fastapi non è installato, la parte CLI continua a funzionare;
    # su Render è comunque presente tramite requirements.txt
    app = None  # importante: se usi gunicorn devi avere fastapi installato

# ====== CLI locale ======
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ask", type=str, required=True, help="Domanda da porre al motore Tecnaria")
    args = parser.parse_args()
    print(ask(args.ask))
