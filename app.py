import json
import re
from pathlib import Path
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

DATA_PATH = Path("static/data/tecnaria_gold.json")

app = FastAPI(title="Tecnaria GOLD")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- pulizia json sporco ----------
def _strip_json_comments(txt: str) -> str:
    txt = re.sub(r"/\*.*?\*/", "", txt, flags=re.DOTALL)
    txt = re.sub(r"//.*?$", "", txt, flags=re.MULTILINE)
    return txt

def _split_multi_json(txt: str):
    parts = []
    buf = ""
    depth = 0
    in_str = False
    esc = False
    for ch in txt:
        buf += ch
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    parts.append(buf.strip())
                    buf = ""
    if buf.strip():
        parts.append(buf.strip())
    return parts

def _force_items_merge(json_objs):
    if not json_objs:
        return {"_meta": {}, "items": []}
    base = json_objs[0]
    if not isinstance(base, dict):
        base = {"_meta": {}, "items": base if isinstance(base, list) else [base]}
    items = base.get("items", [])
    if not isinstance(items, list):
        items = []
    for extra in json_objs[1:]:
        if isinstance(extra, list):
            items.extend(extra)
        elif isinstance(extra, dict):
            items.append(extra)
    base["items"] = items
    return base

def load_dataset(path: Path):
    raw_txt = path.read_text(encoding="utf-8")
    cleaned = _strip_json_comments(raw_txt).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    parts = _split_multi_json(cleaned)
    objs = []
    for p in parts:
        try:
            objs.append(json.loads(p))
        except json.JSONDecodeError:
            continue
    return _force_items_merge(objs)

DATA = load_dataset(DATA_PATH)

# ---------- modelli ----------
class AskIn(BaseModel):
    question: str

class AskOut(BaseModel):
    answer: str
    source_id: str | None = None
    matched: float | None = None

# ---------- matcher molto semplice ----------
def score(q: str, item: dict) -> float:
    ql = q.lower()
    trig = item.get("trigger") or {}
    peso = float(trig.get("peso", 0.5))
    kw = trig.get("keywords") or []
    hit = 0
    for k in kw:
        if k and k.lower() in ql:
            hit += 1
    fam = (item.get("family") or "").lower()
    fam_bonus = 0.05 if fam and fam in ql else 0.0
    return peso + hit * 0.12 + fam_bonus

def find_best_answer(q: str):
    items = DATA.get("items", [])
    if not items:
        return ("Dataset vuoto o non caricato.", None, 0.0)
    best = None
    bests = -1.0
    for it in items:
        s = score(q, it)
        if s > bests:
            bests = s
            best = it
    if not best:
        return ("Nessuna risposta trovata nel dataset Tecnaria GOLD.", None, 0.0)
    return (best.get("risposta", "…"), best.get("id"), bests)

# ---------- routes ----------
@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "Tecnaria GOLD pronto",
        "items": len(DATA.get("items", [])),
        "hint": "fai POST su /ask con {'question': '...'}"
    }

@app.get("/ping")
def ping():
    return {"status": "ok", "items": len(DATA.get("items", []))}

@app.post("/ask", response_model=AskOut)
def ask(body: AskIn):
    ans, src, m = find_best_answer(body.question)
    return AskOut(answer=ans, source_id=src, matched=m)

@app.post("/api/ask", response_model=AskOut)
def ask_alias(body: AskIn):
    ans, src, m = find_best_answer(body.question)
    return AskOut(answer=ans, source_id=src, matched=m)
