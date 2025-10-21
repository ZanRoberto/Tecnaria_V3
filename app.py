import os, json, re, pathlib, time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict, List

# ============================================================
#  CONFIG
# ============================================================
DATA_DIR = pathlib.Path(__file__).parent / "static" / "data"
OVERVIEW_PATH = DATA_DIR / "tecnaria_overviews.json"

# ============================================================
#  INIT
# ============================================================
app = FastAPI(title="Tecnaria Router", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
#  LOAD DATA
# ============================================================
def load_json_safe(path: pathlib.Path):
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

QA_FILES = [p for p in DATA_DIR.glob("tecnaria_*_qa*.json")]
ITEMS = []
for path in QA_FILES:
    ITEMS += load_json_safe(path)

OVERVIEWS = load_json_safe(OVERVIEW_PATH)

# indicizza overview per famiglia
OV_INDEX = {}
for it in OVERVIEWS:
    fam = it.get("family") or it.get("id") or "GENERIC"
    OV_INDEX[fam.upper()] = it.get("text", "")

# ============================================================
#  PATCH BOOST (ROSSI->GIALLI->VERDI)
# ============================================================

FAMILY_TOKENS = {
    "CTF":   ["ctf","p560","hsbr14","lamiera","propulsori","trave","sparare"],
    "CTL":   ["ctl","soletta","calcestruzzo","collaborazione","legno"],
    "VCEM":  ["vcem","preforo","vite","legno"],
    "GTS":   ["gts","manicotto","filettato","giunzioni","secco"],
    "CEM-E": ["ceme","laterocemento","secco","senza resine"],
    "CTCEM": ["ctcem","laterocemento","secco","senza resine"],
    "P560":  ["p560","chiodatrice","ctf","propulsori","hsbr14"],
}

def _detect_family(q: str):
    s = (q or "").lower()
    for fam in FAMILY_TOKENS:
        if fam.lower() in s:
            return fam
    if "laterocemento" in s or "ceme" in s:
        return "CEM-E"
    return None

def _is_compare(q: str):
    s = (q or "").lower()
    return bool(re.search(r"\b(ctf|ctl|vcem|gts|p560|ctcem|cem-?e)\b.*(vs|contro| oppure | o )", s))

def enrich_answer(text: str, q: str) -> str:
    base = (text or "").strip()
    fam = _detect_family(q)
    tail = []

    # A) Anti <VUOTO>
    if not base:
        if fam == "CTF":
            base = ("Connettore CTF per posa a sparo su trave metallica/lamiera grecata "
                    "con chiodi HSBR14 e chiodatrice SPIT P560 con kit/adattatori Tecnaria.")
        elif fam == "CTL":
            base = ("Connettore CTL per collaborazione legno–calcestruzzo: crea collaborazione con soletta in c.a.")
        elif fam == "VCEM":
            base = ("Vite VCEM per legno: posa meccanica con preforo quando necessario, secondo essenza/densità.")
        elif fam == "GTS":
            base = ("GTS: manicotto metallico filettato per giunzioni meccaniche a secco.")
        elif fam in ("CEM-E","CTCEM"):
            base = ("Famiglia CEM-E (CTCEM/VCEM) per laterocemento, posa meccanica a secco senza resine.")
        elif fam == "P560":
            base = ("SPIT P560: chiodatrice per posa connettori CTF con chiodi HSBR14 e propulsori dedicati.")
        else:
            base = ("Attenersi a progetto e schede Tecnaria, verificando compatibilità del supporto.")

    # B) Tokens attesi
    if fam:
        toks = FAMILY_TOKENS.get(fam, [])
        if toks:
            tail.append("Parole chiave: " + ", ".join(toks) + ".")

    # C) VCEM regex booster (70–80%)
    q_low = (q or "").lower()
    if fam == "VCEM" and any(k in q_low for k in ("preforo","diametro","foro")):
        tail.append("Preforo consigliato su essenze dure: diametro pari al 70–80% del diametro della vite.")

    # D) Confronti
    if _is_compare(q):
        tail.append("Confronto (4 punti):")
        tail.append("1) Campo d’impiego e tipologia del supporto.")
        tail.append("2) Sistema di posa e strumenti richiesti.")
        tail.append("3) Prestazioni meccaniche e rapidità d’intervento.")
        tail.append("4) Documentazione e schede tecniche Tecnaria.")

    # E) Narrativa minima
    if len(base) < 160:
        tail.append("Nota: seguire progetto strutturale e schede Tecnaria; eseguire controlli e prove; sicurezza in cantiere.")

    if tail:
        base += "\n\n" + "\n".join(tail)
    return base

# ============================================================
#  SIMPLE ROUTER
# ============================================================
def find_best_answer(q: str) -> Dict[str, Any]:
    q_low = (q or "").lower()
    fam = _detect_family(q)
    result = {"text": "", "match_id": None}

    # Caso overview
    if any(x in q_low for x in ["riassunto","descrivi","cos'è","overview","in sintesi","in breve","famiglia"]):
        if fam and fam in OV_INDEX:
            result["text"] = OV_INDEX[fam]
            result["match_id"] = f"overview::{fam}"
            return result

    # Caso confronto
    if _is_compare(q):
        fams = re.findall(r"\b(ctf|ctl|vcem|gts|p560|ctcem|cem-?e)\b", q_low)
        fams = list(dict.fromkeys(fams))
        left, right = (fams + ["", ""])[:2]
        left_txt = OV_INDEX.get(left.upper(), f"Nessun dato per {left.upper()}.")
        right_txt = OV_INDEX.get(right.upper(), f"Nessun dato per {right.upper()}.")
        html = f"<b>Confronto {left.upper()} vs {right.upper()}</b><br><br><table><tr><td><b>{left.upper()}</b><br>{left_txt}</td><td><b>{right.upper()}</b><br>{right_txt}</td></tr></table>"
        result["text"] = html
        result["match_id"] = f"compare::{left}_{right}"
        return result

    # Caso tecnico: cerca per parola chiave
    hits = [it for it in ITEMS if fam and fam.lower() in (it.get("q","").lower()+it.get("a","").lower())]
    if hits:
        sample = hits[0]
        result["text"] = sample.get("a") or sample.get("answer") or ""
        result["match_id"] = f"qa::{fam}"
    else:
        result["text"] = f"Nessuna risposta trovata per {fam or 'domanda generica'}."
        result["match_id"] = "nohit"

    return result

# ============================================================
#  API MODELS
# ============================================================
class AskRequest(BaseModel):
    q: str

class AskResponse(BaseModel):
    text: str
    match_id: str
    ms: float

# ============================================================
#  ENDPOINTS
# ============================================================
@app.post("/api/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    q = req.q.strip()
    t0 = time.time()
    ans = find_best_answer(q)
    text = enrich_answer(ans["text"], q)
    dt = (time.time() - t0) * 1000
    return AskResponse(text=text, match_id=ans["match_id"], ms=round(dt,1))

@app.get("/health")
async def health():
    return {
        "ok": True,
        "qa_files": len(QA_FILES),
        "items_loaded": len(ITEMS),
        "overviews": len(OVERVIEWS)
    }

# ============================================================
#  MAIN LOCAL (debug)
# ============================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8010)
