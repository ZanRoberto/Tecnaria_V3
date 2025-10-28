
import json, re, os, math
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

APP_DIR = Path(__file__).parent
DATA_PATH = APP_DIR / "static" / "data" / "tecnaria_gold_full.json"

app = FastAPI(title="Tecnaria Sinapsi • GOLD")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def normalize(s:str)->str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9àèéìòùçäöüß\s/×\-\.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def contains_keywords(text, keywords):
    t = " " + normalize(text) + " "
    score = 0
    hits = []
    for kw in keywords:
        kwn = " " + normalize(kw) + " "
        if kwn in t:
            score += 1
            hits.append(kw)
    return score, hits

def soft_ratio(a, b):
    ta = set(normalize(a).split())
    tb = set(normalize(b).split())
    if not ta or not tb: 
        return 0.0
    inter = len(ta & tb)
    denom = math.sqrt(len(ta)*len(tb))
    return inter/denom

def it_only_guard(q):
    blacklist = ["bitcoin","binance","forex","tourism","hotel","iphone","android","python code","javascript","football","soccer","car","trading"]
    qn = normalize(q)
    if any(x in qn for x in blacklist):
        return False
    return True

with open(DATA_PATH, "r", encoding="utf-8") as f:
    GOLD = json.load(f)

ITEMS = []
for fam, rows in GOLD.items():
    for r in rows:
        rr = dict(r)
        rr["_family"] = fam
        ITEMS.append(rr)

@app.get("/", response_class=HTMLResponse)
def index():
    html = (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html, status_code=200)

@app.get("/health")
def health():
    return {"ok": True, "items": len(ITEMS), "families": list(GOLD.keys())}

@app.post("/qa/ask")
async def qa_ask(payload: dict):
    q = payload.get("q","").strip()
    if not q:
        return JSONResponse({"error":"Missing question"}, status_code=400)

    if not it_only_guard(q):
        return {"family": "FILTER", "score": 0.0, "answer": "Rispondo esclusivamente su prodotti e sistemi **Tecnaria S.p.A.** (CTF, CTL/MAXI, VCEM/CTCEM, DIAPASON, GTS, P560, accessori, ordini/forniture). Riformula la domanda in questo perimetro."}

    best = None
    best_score = -1
    trace = []
    for it in ITEMS:
        title = it.get("q","")
        ans = it.get("a","")
        tags = " ".join(it.get("tags",[]))
        patterns = " ".join(it.get("patterns",[]))

        s_title = soft_ratio(q, title)
        s_ans = soft_ratio(q, ans[:300])
        s_tags = soft_ratio(q, tags)
        s_pat = soft_ratio(q, patterns)

        kw_score, kw_hits = contains_keywords(q, it.get("keywords", []))
        score = (2.0*s_title + 0.6*s_ans + 1.0*s_tags + 1.4*s_pat) + 0.8*kw_score

        trace.append((it["_family"], it.get("id",""), float(score), kw_hits))

        if score > best_score:
            best_score = score
            best = it

    if best is None or best_score < 0.7:
        return {
            "family":"HINT",
            "score": round(float(best_score),3) if best is not None else 0.0,
            "answer": "Domanda poco chiara o fuori ambito. Esempi utili:
• **P560**: taratura e DPI
• **CTL MAXI**: su tavolato 25–30 mm che viti uso?
• **CTF**: su S355 con lamiera 1×1,5 mm come si posa?
• **VCEM/CTCEM**: servono resine?",
            "trace": trace[:10]
        }

    family = best.get("_family","GEN")
    answer = best.get("a","").strip()
    if not answer.endswith("
"):
        answer += "
"

    return {
        "family": family,
        "score": round(float(best_score),3),
        "answer": answer,
        "id": best.get("id",""),
        "trace": trace[:8]
    }
