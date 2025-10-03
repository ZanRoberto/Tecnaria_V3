# app.py
# -------------------------------------------------------------------
# Tecnaria QA Bot – Web-first + Sinapsi (override/augment/postscript)
# FastAPI + Brave/Bing + PDF-safe + preferenza domini Tecnaria
# Interfaccia HTML inclusa (no asset esterni)
# -------------------------------------------------------------------

import os, re, json, unicodedata
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse

# ---------------------- ENV / CONFIG --------------------------------
DEBUG               = os.getenv("DEBUG", "0") == "1"
SEARCH_PROVIDER     = os.getenv("SEARCH_PROVIDER", "brave").lower()  # brave|bing
BRAVE_API_KEY       = os.getenv("BRAVE_API_KEY", "").strip()
BING_API_KEY        = (os.getenv("BING_API_KEY", "") or os.getenv("AZURE_BING_KEY", "")).strip()
SEARCH_API_ENDPOINT = os.getenv("SEARCH_API_ENDPOINT", "").strip()   # opzionale per bing

PREFERRED_DOMAINS   = [d.strip() for d in os.getenv("PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com").split(",") if d.strip()]
MIN_WEB_SCORE       = float(os.getenv("MIN_WEB_SCORE", "0.35"))
WEB_TIMEOUT         = float(os.getenv("WEB_TIMEOUT", "6"))
WEB_RETRIES         = int(os.getenv("WEB_RETRIES", "2"))

CRITICI_DIR         = os.getenv("CRITICI_DIR", "Tecnaria_V3/static/static/data/critici").strip()

print("[BOOT] -----------------------------------------------")
print(f"[BOOT] WEB_FIRST; SEARCH_PROVIDER={SEARCH_PROVIDER}; PREFERRED_DOMAINS={PREFERRED_DOMAINS}")
print(f"[BOOT] MIN_WEB_SCORE={MIN_WEB_SCORE} WEB_TIMEOUT={WEB_TIMEOUT}s WEB_RETRIES={WEB_RETRIES}")
print(f"[BOOT] CRITICI_DIR={CRITICI_DIR}")
print("[BOOT] ------------------------------------------------")

# ---------------------- UTIL ----------------------------------------
def normalize(t: str) -> str:
    if not t: return ""
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t

def domain_of(url: str) -> str:
    try: return urlparse(url).netloc.lower()
    except: return ""

def prefer_score_for(url: str) -> float:
    d = domain_of(url)
    return 0.25 if any(pd in d for pd in PREFERRED_DOMAINS) else 0.0

def strip_html_keep_text(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    # togli i tag che poi uscivano come <strong> ecc.
    for tag in soup.find_all(['strong','b','em','i']):
        tag.unwrap()
    text = soup.get_text("\n")
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()

def short(text: str, n: int = 900) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return (text[:n] + "…") if len(text) > n else text

# ---------------------- WEB SEARCH ----------------------------------
def brave_search(q: str, topk: int = 6) -> List[Dict]:
    if not BRAVE_API_KEY: return []
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    params = {"q": q, "count": topk}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=WEB_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        out = []
        for it in data.get("web", {}).get("results", []):
            out.append({"title": it.get("title") or "", "url": it.get("url") or "", "snippet": it.get("description") or ""})
        return out
    except Exception as e:
        if DEBUG: print("[BRAVE][ERR]", e)
        return []

def bing_search(q: str, topk: int = 6) -> List[Dict]:
    if not BING_API_KEY: return []
    endpoint = SEARCH_API_ENDPOINT or "https://api.bing.microsoft.com/v7.0/search"
    headers = {"Ocp-Apim-Subscription-Key": BING_API_KEY}
    params = {"q": q, "count": topk, "responseFilter": "Webpages"}
    try:
        r = requests.get(endpoint, headers=headers, params=params, timeout=WEB_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        out = []
        for it in data.get("webPages", {}).get("value", []):
            out.append({"title": it.get("name") or "", "url": it.get("url") or "", "snippet": it.get("snippet") or ""})
        return out
    except Exception as e:
        if DEBUG: print("[BING][ERR]", e)
        return []

def web_search(q: str, topk: int = 6) -> List[Dict]:
    return bing_search(q, topk) if SEARCH_PROVIDER == "bing" else brave_search(q, topk)

def fetch_text(url: str) -> str:
    try:
        r = requests.get(url, timeout=WEB_TIMEOUT, headers={"User-Agent":"Mozilla/5.0"})
        r.raise_for_status()
        ctype = r.headers.get("content-type","").lower()
        # se è PDF: non sputare byte; restituisci un riassunto placeholder leggibile
        if "pdf" in ctype or url.lower().endswith(".pdf"):
            return "Documento **PDF**: apri la fonte per i dettagli tecnici. (contenuto binario non mostrabile qui)"
        return strip_html_keep_text(r.text)
    except Exception as e:
        if DEBUG: print("[FETCH][ERR]", url, e)
        return ""

def rank_results(q: str, results: List[Dict]) -> List[Dict]:
    nq = normalize(q)
    for it in results:
        sc = 0.0
        sc += prefer_score_for(it.get("url",""))
        blob = normalize((it.get("title") or "") + " " + (it.get("snippet") or ""))
        for w in set(nq.split()):
            if w and w in blob: sc += 0.4
        it["score"] = sc
    return sorted(results, key=lambda x: x.get("score",0.0), reverse=True)

def web_lookup(q: str) -> Tuple[str, List[str], float]:
    for _ in range(WEB_RETRIES+1):
        rs = web_search(q, topk=8)
        if not rs: continue
        pref = [r for r in rs if any(pd in domain_of(r["url"]) for pd in PREFERRED_DOMAINS)]
        cand = pref if pref else rs
        ranked = rank_results(q, cand)
        if not ranked: continue
        top = ranked[0]
        if top.get("score",0.0) < MIN_WEB_SCORE: continue
        txt = fetch_text(top["url"])
        if not txt: continue
        ans = (
            "OK\n"
            f"- **Riferimento**: {top.get('title') or 'pagina tecnica'}\n"
            f"- **Sintesi web**: {short(txt, 500)}\n"
        )
        return ans, [top["url"]], top.get("score",0.0)
    return "", [], 0.0

# ---------------------- SINAPSI -------------------------------------
SINAPSI: List[Dict] = []

def load_sinapsi():
    global SINAPSI
    SINAPSI = []
    try:
        path = os.path.join(CRITICI_DIR, "sinapsi_brain.json")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            if "mode" not in item: item["mode"] = "augment"
            SINAPSI.append(item)
        if DEBUG: print(f"[SINAPSI] loaded {len(SINAPSI)} entries from {path}")
    except Exception as e:
        if DEBUG: print("[SINAPSI][ERR]", e)

load_sinapsi()

def sinapsi_hit(q: str) -> List[Dict]:
    hits = []
    for it in SINAPSI:
        try:
            if re.search(it.get("pattern",""), q or "", flags=re.IGNORECASE):
                hits.append(it)
        except Exception:
            continue
    return hits

def fuse_answer(web_core: str, web_sources: List[str], hits: List[Dict]) -> str:
    # 1) nessuna sinapsi -> web pure
    if not hits:
        out = web_core or "OK\n- **Non ho trovato una risposta affidabile sul web**.\n"
        if web_sources:
            out += "\n**Fonti**\n" + "\n".join(f"- {u}" for u in web_sources) + "\n"
        return out

    overrides  = [h for h in hits if h.get("mode") == "override"]
    augments   = [h for h in hits if h.get("mode") == "augment"]
    postscripts= [h for h in hits if h.get("mode") == "postscript"]

    # 2) override -> solo sinapsi
    if overrides:
        out = "OK\n" + "\n".join(h.get("answer","").strip() for h in overrides if h.get("answer"))
        return out.strip()

    # 3) base web + add-on sinapsi + fonti
    out = web_core or "OK\n"
    if augments:
        aug_txt = "\n".join(h.get("answer","").strip() for h in augments if h.get("answer"))
        if aug_txt:
            if not out.endswith("\n"): out += "\n"
            out += aug_txt.strip() + "\n"
    if web_sources:
        if not out.endswith("\n"): out += "\n"
        out += "**Fonti**\n" + "\n".join(f"- {u}" for u in web_sources) + "\n"
    if postscripts:
        ps = " ".join(h.get("answer","").strip() for h in postscripts if h.get("answer"))
        if ps:
            out += "\n_P.S._ " + ps.strip() + "\n"
    return out

def answer(q: str) -> str:
    q = (q or "").strip()
    if not q:
        return "OK\n- **Domanda vuota**: inserisci una richiesta valida.\n"
    web_core, web_src, _ = web_lookup(q)
    hits = sinapsi_hit(q)
    out = fuse_answer(web_core, web_src, hits)

    # modalità "essenziale" su richiesta nel testo
    if re.search(r"(?i)\b(sintetico|in breve|da cantiere|essenziale)\b", q):
        lines = [ln.strip(" -") for ln in out.splitlines() if ln.strip()]
        bullets = [ln for ln in lines if ln.startswith("**") or ":" in ln]
        if bullets:
            out = "OK\n- " + "\n- ".join(bullets[:6]) + ("\n" if not bullets[-1].endswith("\n") else "")
    return out

# ---------------------- API -----------------------------------------
app = FastAPI(title="Tecnaria QA Bot", version="3.1.0")

# ---- UI ----
@app.get("/", response_class=HTMLResponse)
def ui():
    return """
<!doctype html><html lang="it"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Tecnaria QA Bot</title>
<style>
:root{--bg:#0b1220;--panel:#141b2d;--text:#e7f0ff;--muted:#9fb2d0;--accent:#25d366}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.45 system-ui,Segoe UI,Arial}
.wrap{max-width:980px;margin:24px auto;padding:16px}
h1{font-size:22px;margin:0 0 8px}
.badge{display:inline-block;padding:6px 12px;border-radius:999px;background:#1f2a44;margin:6px 6px 6px 0;color:#cfe1ff;cursor:pointer}
.panel{background:var(--panel);border-radius:16px;padding:16px;margin:16px 0}
textarea{width:100%;min-height:140px;border:1px solid #2b3758;background:#0e1626;color:var(--text);border-radius:10px;padding:12px}
.row{display:flex;gap:10px;align-items:center;margin-top:12px}
.btn{background:#2b3758;color:#fff;border:0;border-radius:10px;padding:10px 14px;cursor:pointer}
.btn:active{transform:translateY(1px)}
.out{white-space:pre-wrap;background:#0e1626;border-radius:12px;padding:12px;min-height:160px;border:1px solid #2b3758}
.small{color:var(--muted);font-size:13px}
.chk{display:inline-flex;align-items:center;gap:6px;color:#cfe1ff}
kbd{background:#26314f;border-radius:6px;padding:2px 6px}
</style>
</head><body>
<div class="wrap">
  <h1>Tecnaria QA Bot</h1>
  <div class="panel">
    <div id="chips">
      <span class="badge" data-q="Devo usare la chiodatrice P560 per fissare i CTF. Serve un patentino o formazione speciale?">P560 + CTF</span>
      <span class="badge" data-q="Che differenza c’è tra CTF e il sistema Diapason? Quando usare uno o l’altro?">CTF vs Diapason</span>
      <span class="badge" data-q="Quanti connettori CTF servono per m² e come si fissano con SPIT P560?">Densità CTF</span>
      <span class="badge" data-q="Mi dai i contatti Tecnaria per assistenza tecnica/commerciale?">Contatti</span>
    </div>
    <textarea id="q" placeholder="Scrivi la domanda…"></textarea>
    <div class="row">
      <button class="btn" id="ask">Chiedi</button>
      <button class="btn" id="clear">Pulisci</button>
      <button class="btn" id="copy">Copia risposta</button>
      <label class="chk"><input type="checkbox" id="useget"/> usa GET (debug)</label>
    </div>
  </div>

  <div class="panel">
    <div id="out" class="out">Risposte qui…</div>
    <div class="small">Endpoint: <kbd>/api/ask</kbd> (POST JSON { q }) oppure <kbd>/api/ask?q=…</kbd></div>
  </div>
</div>

<script>
const $ = (s)=>document.querySelector(s);
document.querySelectorAll(".badge").forEach(b=>b.onclick=()=>{$("#q").value=b.dataset.q;});
$("#clear").onclick=()=>{$("#q").value="";$("#out").textContent="";};
$("#copy").onclick=()=>{navigator.clipboard.writeText($("#out").textContent||"");};
$("#ask").onclick=async()=>{
  const q = ($("#q").value||"").trim();
  $("#out").textContent = "…";
  try{
    let data;
    if($("#useget").checked){
      const r = await fetch("/api/ask?q="+encodeURIComponent(q));
      data = await r.json();
    }else{
      const r = await fetch("/api/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({q})});
      data = await r.json();
    }
    $("#out").textContent = (data && (data.answer||data.error)) ? (data.answer||("Errore: "+data.error)) : "Errore di risposta";
  }catch(e){
    $("#out").textContent = "Errore di rete: "+e;
  }
};
</script>
</body></html>
    """

@app.get("/ping")
def ping():
    return {"ok": True, "pong": True}

@app.get("/health")
def health():
    return {
        "status": "ok",
        "web_search": {
            "provider": SEARCH_PROVIDER,
            "brave_key": bool(BRAVE_API_KEY),
            "bing_key": bool(BING_API_KEY),
            "preferred_domains": PREFERRED_DOMAINS,
            "min_web_score": MIN_WEB_SCORE
        },
        "critici": {
            "dir": CRITICI_DIR,
            "exists": os.path.isdir(CRITICI_DIR),
            "sinapsi_loaded": len(SINAPSI)
        }
    }

@app.get("/api/ask")
def ask_get(q: Optional[str] = None):
    return {"ok": True, "answer": answer(q or "")}

@app.post("/api/ask")
async def ask_post(req: Request):
    try:
        data = await req.json()
    except:
        data = {}
    q = (data.get("q") or "").strip()
    if not q:
        return JSONResponse({"ok": False, "error": "Missing q"}, status_code=400)
    return {"ok": True, "answer": answer(q)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT","8000")), reload=True)
