import json
import re
from pathlib import Path
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

# =========================
# CONFIG BASE
# =========================
DATA_PATH = Path("static/data/tecnaria_gold.json")  # cambia qui se il file è altrove

app = FastAPI(title="Tecnaria GOLD — Sinapsi+Camilla")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# 1) UTILI DI PULIZIA
# =========================

def _strip_json_comments(txt: str) -> str:
    """
    Togli:
    - /* ... */
    - // ...
    perché il json di Python non li accetta.
    """
    # blocchi /* ... */
    txt = re.sub(r"/\*.*?\*/", "", txt, flags=re.DOTALL)
    # righe //
    txt = re.sub(r"//.*?$", "", txt, flags=re.MULTILINE)
    return txt


def _split_multi_json(txt: str):
    """
    A volte il file che hai ora è fatto così:
        { "_meta": ..., "items": [ ... ] }
        [ {...}, {...} ]
        [ {...} ]
    cioè più JSON uno dopo l'altro.
    Questa funzione li separa.
    """
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
                    # fine di un JSON
                    parts.append(buf.strip())
                    buf = ""
    # se avanza roba
    if buf.strip():
        parts.append(buf.strip())
    return parts


def _force_items_merge(json_objs):
    """
    - Se il primo è l’oggetto grande { "_meta": ..., "items": [...] }
    - e gli altri sono array/oggetti singoli,
      li butto dentro in fondo a items.
    """
    if not json_objs:
        return {"_meta": {}, "items": []}

    base = json_objs[0]
    if not isinstance(base, dict):
        # caso strano, ma lo normalizzo
        base = {"_meta": {}, "items": base if isinstance(base, list) else [base]}

    items = base.get("items", [])
    if not isinstance(items, list):
        items = []

    # tutti gli altri
    for extra in json_objs[1:]:
        if isinstance(extra, list):
            items.extend(extra)
        elif isinstance(extra, dict):
            items.append(extra)
        else:
            # ignoro
            pass

    base["items"] = items
    return base


def load_dataset(path: Path):
    raw_txt = path.read_text(encoding="utf-8")
    cleaned = _strip_json_comments(raw_txt).strip()

    # prova a caricarlo se è singolo
    try:
        data = json.loads(cleaned)
        # se è già ok, ritorno
        return data
    except json.JSONDecodeError:
        # ok, allora è il caso tuo: più JSON attaccati
        pass

    # split in più json
    parts = _split_multi_json(cleaned)
    objs = []
    for p in parts:
        try:
            objs.append(json.loads(p))
        except json.JSONDecodeError:
            # se proprio un pezzo è marcio lo salto
            continue

    merged = _force_items_merge(objs)
    return merged


# carico subito all’avvio
DATA = load_dataset(DATA_PATH)


# =========================
# 2) MODELLI
# =========================

class AskIn(BaseModel):
    question: str


class AskOut(BaseModel):
    answer: str
    source_id: str | None = None
    matched: float | None = None


# =========================
# 3) NLM ROZZO (ma veloce)
# =========================

def score(q: str, item: dict) -> float:
    """
    matching brutale ma rapidissimo:
    - peso del trigger
    - presenza di keyword
    - mini bonus su family
    """
    qlow = q.lower()
    trig = item.get("trigger") or {}
    peso = float(trig.get("peso", 0.5))

    kw = trig.get("keywords") or []
    hit = 0
    for k in kw:
        if k and k.lower() in qlow:
            hit += 1

    # family bonus
    fam = (item.get("family") or "").lower()
    fam_bonus = 0.05 if fam and fam in qlow else 0.0

    return peso + hit * 0.12 + fam_bonus


def find_best_answer(question: str) -> tuple[str, str | None, float]:
    items = DATA.get("items", [])
    if not items:
        return ("Non ho trovato nulla nel dataset Tecnaria GOLD.", None, 0.0)

    best_item = None
    best_score = -1.0

    for it in items:
        s = score(question, it)
        if s > best_score:
            best_score = s
            best_item = it

    if not best_item:
        return ("Non ho trovato nulla nel dataset Tecnaria GOLD.", None, 0.0)

    return (best_item.get("risposta", "…"), best_item.get("id"), best_score)


# =========================
# 4) ENDPOINT
# =========================

@app.get("/ping")
def ping():
    return {"status": "ok", "items": len(DATA.get("items", []))}


@app.post("/ask", response_model=AskOut)
def ask(body: AskIn):
    ans, src, m = find_best_answer(body.question)
    return AskOut(answer=ans, source_id=src, matched=m)
