# app.py — Tecnaria_V3 (interfaccia integrata)
from fastapi import FastAPI, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import time, re, csv, json

app = FastAPI(title="Tecnaria_V3")

# === Dati locali ===
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "static" / "data"
OV_JSON = DATA_DIR / "tecnaria_overviews.json"
CMP_JSON = DATA_DIR / "tecnaria_compare.json"
FAQ_CSV = DATA_DIR / "faq.csv"

def load_json(path: Path, fallback=None):
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f) or []
                if isinstance(data, list): return data
    except Exception: pass
    return fallback or []

def load_faq_csv(path: Path):
    rows = []
    if not path.exists(): return rows
    def _read(enc):
        with path.open("r", encoding=enc, newline="") as f:
            for r in csv.DictReader(f):
                rows.append({
                    "id": (r.get("id") or "").strip(),
                    "lang": (r.get("lang") or "").strip().lower() or "it",
                    "question": (r.get("question") or "").strip(),
                    "answer": (r.get("answer") or "").strip(),
                    "tags": (r.get("tags") or "").strip().lower()
                })
    try: _read("utf-8-sig")
    except: 
        try: _read("cp1252")
        except: return rows
    return rows

OV_ITEMS = load_json(OV_JSON, [])
CMP_ITEMS = load_json(CMP_JSON, [])
FAQ_ITEMS = load_faq_csv(FAQ_CSV)
FAQ_ROWS = len(FAQ_ITEMS)

FAQ_BY_LANG = {}
for r in FAQ_ITEMS:
    FAQ_BY_LANG.setdefault(r["lang"], []).append(r)

_LANG_PATTERNS = {
    "en": [r"\bwhat\b", r"\bhow\b", r"\bcan\b", r"\bshould\b", r"\bconnector"],
    "es": [r"¿", r"\bqué\b", r"\bcómo\b", r"\bconector"],
    "fr": [r"\bquoi\b", r"\bcomment\b", r"\bquel", r"\bconnecteur"],
    "de": [r"\bwas\b", r"\bwie\b", r"\bverbinder"],
}

def detect_lang(q: str) -> str:
    s = (q or "").lower()
    for lang, pats in _LANG_PATTERNS.items():
        for p in pats:
            if re.search(p, s): return lang
    if "¿" in s or "¡" in s: return "es"
    return "it"

FAM_TOKENS = {
    "CTF": ["ctf","lamiera","trave","powder","p560","chiodatrice","connector"],
    "CTL": ["ctl","soletta","legno","collaborazione","trave"],
    "VCEM": ["vcem","preforo","hardwood","predrill","resina"],
    "CEM-E": ["ceme","cem-e","laterocemento","secco"],
    "CTCEM": ["ctcem","laterocemento","malta","resine"],
    "GTS": ["gts","manicotto","filettato","sleeve","joint"],
    "P560": ["p560","spit","chiodatrice","powder","nailer","tool"]
}

def detect_family(text: str) -> Tuple[str,int]:
    t = " " + (text or "").lower() + " "
    best,score=" ",0
    for fam,toks in FAM_TOKENS.items():
        hits=0
        if fam.lower() in t: hits+=2
        for tok in toks:
            if tok in t: hits+=1
        if hits>score: best,score=fam,hits
    return best,score

def intent_route(q:str)->Dict[str,Any]:
    ql=(q or "").lower().strip()
    lang=detect_lang(ql)
    fam,hits=detect_family(ql)
    if hits>=1:
        for r in FAQ_BY_LANG.get(lang,[]):
            keys=((r.get("tags") or "")+" "+(r.get("question") or "")).lower()
            if fam.lower() in keys:
                return {"ok":True,"match_id":r.get("id"),"text":r.get("answer"),
                        "lang":lang,"family":fam,"intent":"faq","source":"faq","score":90}
    return {"ok":True,"match_id":"<NULL>","lang":lang,"family":"", "intent":"fallback",
            "source":"fallback","score":0,"text":"Non ho trovato una risposta diretta. Specifica meglio la famiglia o il prodotto.","html":""}

@app.get("/")
def root()->HTMLResponse:
    html = """
    <!DOCTYPE html><html lang="it"><head><meta charset="UTF-8">
    <title>Tecnaria_V3</title>
    <style>
    body{font-family:Segoe UI,Arial;background:#e8f5e9;color:#111;padding:2em;}
    header{background:#2e7d32;color:#fff;padding:10px;font-weight:bold;border-radius:8px;}
    textarea{width:100%;height:100px;border:2px solid #2e7d32;border-radius:6px;font-size:1em;padding:8px;}
    button{background:#2e7d32;color:#fff;border:none;padding:10px 20px;margin-top:10px;border-radius:6px;cursor:pointer;}
    .response{background:#fff;border-radius:6px;padding:15px;margin-top:20px;box-shadow:0 2px 4px rgba(0,0,0,.1);}
    </style></head><body>
    <header>🟢 Tecnaria_V3 — Chatbot Tecnico</header>
    <p>Scrivi la tua domanda e premi <b>Chiedi</b>:</p>
    <textarea id="question" placeholder="Esempio: Differenza tra CTF e CTL?"></textarea><br>
    <button onclick="ask()">Chiedi</button>
    <div id="response" class="response"></div>
    <script>
    async function ask(){
      const q=document.getElementById("question").value.trim();
      if(!q){alert("Scrivi una domanda!");return;}
      const box=document.getElementById("response");
      box.innerHTML="<i>Attendere...</i>";
      try{
        const r=await fetch('/api/ask?q='+encodeURIComponent(q));
        const d=await r.json();
        box.innerHTML='<b>match_id:</b> '+d.match_id+'<br>'+
                      '<b>famiglia:</b> '+d.family+'<br>'+
                      '<b>lang:</b> '+d.lang+' | <b>ms:</b> '+d.ms+'<br><br>'+
                      (d.html||d.text);
      }catch(e){box.innerHTML="<b style='color:red'>Errore server</b>";}
    }
    </script></body></html>
    """
    return HTMLResponse(content=html)

@app.get("/health")
def health(): 
    return {"ok":True,"faq_rows":FAQ_ROWS}

class AskIn(BaseModel): q:str
class AskOut(BaseModel):
    ok:bool; match_id:str; ms:int; text:Optional[str]=""; html:Optional[str]=""; lang:Optional[str]=None; family:Optional[str]=None; intent:Optional[str]=None; source:Optional[str]=None; score:Optional[float]=None

@app.get("/api/ask",response_model=AskOut)
def api_ask_get(q:str=Query(default=""))->AskOut:
    t0=time.time();r=intent_route(q);ms=int((time.time()-t0)*1000)
    return AskOut(ok=True,match_id=r.get("match_id"),ms=ms,text=r.get("text"),html=r.get("html",""),lang=r.get("lang"),family=r.get("family"),intent=r.get("intent"),source=r.get("source"),score=r.get("score"))
