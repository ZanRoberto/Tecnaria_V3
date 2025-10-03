# app.py
# -----------------------------------------------------------------------------
# Tecnaria QA Bot – WEB ONLY + CONTATTI dai JSON in CRITICI_DIR
# Endpoints: /ping, /health, /ask (GET/POST), /api/ask (alias), /ui (UI integrata)
# Homepage: templates/index.html (se presente), altrimenti banner JSON
# -----------------------------------------------------------------------------

import os
import re
import json
import unicodedata
from typing import List, Dict, Tuple, Optional
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

# ------------------------------ ENV / CONFIG ---------------------------------
DEBUG               = os.getenv("DEBUG", "0") == "1"

SEARCH_PROVIDER     = os.getenv("SEARCH_PROVIDER", "brave").lower()  # brave | bing
SEARCH_API_ENDPOINT = os.getenv("SEARCH_API_ENDPOINT", "").strip()
BRAVE_API_KEY       = os.getenv("BRAVE_API_KEY", "").strip()
BING_API_KEY        = os.getenv("BING_API_KEY", "").strip() or os.getenv("AZURE_BING_KEY", "").strip()
PREFERRED_DOMAINS   = [d.strip() for d in os.getenv("PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com").split(",") if d.strip()]

MIN_WEB_SCORE       = float(os.getenv("MIN_WEB_SCORE", "0.35"))
WEB_TIMEOUT         = float(os.getenv("WEB_TIMEOUT", "6"))
WEB_RETRIES         = int(os.getenv("WEB_RETRIES", "2"))

CRITICI_DIR         = os.getenv("CRITICI_DIR", "").strip()

TEMPLATES_DIR       = os.getenv("TEMPLATES_DIR", "templates")
STATIC_DIR          = os.getenv("STATIC_DIR", "static")

print("[BOOT] -----------------------------------------------")
print(f"[BOOT] WEB_ONLY; SEARCH_PROVIDER={SEARCH_PROVIDER}; PREFERRED_DOMAINS={PREFERRED_DOMAINS}")
print(f"[BOOT] MIN_WEB_SCORE={MIN_WEB_SCORE} WEB_TIMEOUT={WEB_TIMEOUT}s WEB_RETRIES={WEB_RETRIES}")
print(f"[BOOT] CRITICI_DIR={CRITICI_DIR} TEMPLATES_DIR={TEMPLATES_DIR} STATIC_DIR={STATIC_DIR}")
print("[BOOT] ------------------------------------------------")

# ------------------------------ UTIL -----------------------------------------
P560_PAT = re.compile(r"\bp\s*[- ]?\s*560\b", re.I)
LIC_PAT  = re.compile(r"\b(patentino|abilitazione|formazione)\b", re.I)
CONT_PAT = re.compile(r"\b(contatti|telefono|email|pec)\b", re.I)
CTF_PAT  = re.compile(r"\bctf\b", re.I)

UI_NOISE_PREFIXES = ("chiedi", "pulisci", "copia risposta", "risposta", "connettori ctf", "contatti", "—", "p560")

def normalize(text: str) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t

def clean_ui_noise(text: str) -> str:
    if not text:
        return ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    keep = []
    for l in lines:
        low = l.strip().lower()
        if any(low.startswith(pfx) for pfx in UI_NOISE_PREFIXES):
            continue
        keep.append(l)
    return " ".join(keep).strip()

def domain_of(url: str) -> str:
    from urllib.parse import urlparse as _urlparse
    try:
        return _urlparse(url).netloc.lower()
    except Exception:
        return ""

def prefer_score_for_domain(url: str) -> float:
    dom = domain_of(url)
    if not dom:
        return 0.0
    for pd in PREFERRED_DOMAINS:
        if pd in dom:
            return 0.25
    return 0.0

def short_text(text: str, n: int = 900) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return (t[:n] + "…") if len(t) > n else t

# ------------------------------ WEB SEARCH / FETCH ---------------------------
def brave_search(q: str, topk: int = 5, timeout: float = WEB_TIMEOUT) -> List[Dict]:
    if not BRAVE_API_KEY:
        return []
    headers = {"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY}
    params = {"q": q, "count": topk}
    url = "https://api.search.brave.com/res/v1/web/search"
    r = requests.get(url, headers=headers, params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return [
        {"title": it.get("title") or "", "url": it.get("url") or "", "snippet": it.get("description") or ""}
        for it in data.get("web", {}).get("results", [])
    ]

def bing_search(q: str, topk: int = 5, timeout: float = WEB_TIMEOUT) -> List[Dict]:
    key = BING_API_KEY
    endpoint = SEARCH_API_ENDPOINT or "https://api.bing.microsoft.com/v7.0/search"
    if not key:
        return []
    headers = {"Ocp-Apim-Subscription-Key": key}
    params = {"q": q, "count": topk, "responseFilter": "Webpages"}
    r = requests.get(endpoint, headers=headers, params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return [
        {"title": it.get("name") or "", "url": it.get("url") or "", "snippet": it.get("snippet") or ""}
        for it in data.get("webPages", {}).get("value", [])
    ]

def web_search(q: str, topk: int = 5) -> List[Dict]:
    return bing_search(q, topk=topk) if SEARCH_PROVIDER == "bing" else brave_search(q, topk=topk)

def fetch_text(url: str, timeout: float = WEB_TIMEOUT) -> str:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n")
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        return text.strip()
    except Exception:
        return ""

def rank_results(q: str, results: List[Dict]) -> List[Dict]:
    nq = normalize(q)
    for it in results:
        score = prefer_score_for_domain(it.get("url", ""))
        sn = normalize((it.get("title") or "") + " " + (it.get("snippet") or ""))
        for w in set(nq.split()):
            if w and w in sn:
                score += 0.4
        if P560_PAT.search(sn): score += 0.5
        if CTF_PAT.search(sn):  score += 0.4
        it["score"] = score
    return sorted(results, key=lambda x: x.get("score", 0.0), reverse=True)

def web_lookup(q: str,
               min_score: float = MIN_WEB_SCORE,
               timeout: float = WEB_TIMEOUT,
               retries: int = WEB_RETRIES,
               domains: Optional[List[str]] = None) -> Tuple[str, List[str], float]:
    doms = domains or PREFERRED_DOMAINS
    sources: List[str] = []
    best_score = 0.0

    if (SEARCH_PROVIDER == "brave" and not BRAVE_API_KEY) or (SEARCH_PROVIDER == "bing" and not BING_API_KEY):
        return "", [], 0.0

    for _ in range(retries + 1):
        results = web_search(q, topk=7) or []
        if doms:
            results = [r for r in results if any(d in domain_of(r["url"]) for d in doms)]
        ranked = rank_results(q, results)
        if not ranked:
            continue
        top = ranked[0]
        best_score = top.get("score", 0.0)
        if best_score < min_score:
            continue
        txt = fetch_text(top["url"], timeout=timeout)
        if not txt:
            continue
        sources.append(top["url"])
        ans = (
            "OK\n"
            f"- **Riferimento**: {top.get('title') or 'pagina tecnica'}.\n"
            "- **Sintesi**: contenuti tecnici pertinenti trovati su fonte preferita.\n"
            "- **Nota**: verificare sempre le istruzioni ufficiali aggiornate.\n"
        )
        return ans, sources, best_score

    return "", sources, best_score

# ------------------------------ CONTATTI da CRITICI --------------------------
def _fmt(val):
    return str(val).strip() if val is not None else ""

def load_contacts_from_critici() -> Optional[str]:
    if not CRITICI_DIR or not os.path.isdir(CRITICI_DIR):
        return None
    import glob
    for pat in ["*contatti*.json", "*contacts*.json", "*.json"]:
        for p in glob.glob(os.path.join(CRITICI_DIR, pat)):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            candidates = data if isinstance(data, list) else [data]
            for obj in candidates:
                if not isinstance(obj, dict):
                    continue
                rs  = obj.get("ragione_sociale") or obj.get("ragioneSociale") or obj.get("azienda") or ""
                piva= obj.get("piva") or obj.get("partita_iva") or obj.get("partitaIva") or ""
                sdi = obj.get("sdi")  or obj.get("SDI") or ""
                ind = obj.get("indirizzo") or obj.get("address") or ""
                tel = obj.get("telefono")  or obj.get("phone") or ""
                em  = obj.get("email")     or obj.get("mail") or ""
                pec = obj.get("pec")       or ""
                if any([rs, tel, em]):
                    lines = [
                        "OK",
                        f"- **Ragione sociale**: {_fmt(rs) or '—'}",
                        f"- **P.IVA**: {_fmt(piva) or '—'}   **SDI**: {_fmt(sdi) or '—'}",
                        f"- **Indirizzo**: {_fmt(ind) or '—'}",
                        f"- **Telefono**: {_fmt(tel) or '—'}",
                        f"- **Email**: {_fmt(em) or '—'}",
                        f"- **PEC**: {_fmt(pec) or '—'}",
                        "",
                        "**Fonti**",
                        f"- file critici · {os.path.basename(p)}"
                    ]
                    return "\n".join(lines)
    return None

def answer_contacts() -> str:
    crit = load_contacts_from_critici()
    if crit:
        return crit
    return (
        "OK\n"
        "- **Ragione sociale**: TECNARIA S.p.A.\n"
        "- **Telefono**: +39 0424 502029\n"
        "- **Email**: info@tecnaria.com\n"
    )

# ------------------------------ FORMATTER -------------------------------------
def format_as_bot(core_text: str, sources: Optional[List[str]] = None) -> str:
    core = core_text or ""
    if core.strip().startswith("OK"):
        if sources and "\n**Fonti**" not in core:
            core += "\n\n**Fonti**\n" + "\n".join(f"- {u}" for u in sources) + "\n"
        return core
    out = "OK\n" + core.strip()
    if sources:
        out += "\n\n**Fonti**\n" + "\n".join(f"- {u}" for u in sources) + "\n"
    return out

def answer_p560_template() -> str:
    return (
        "OK\n"
        "- **Abilitazione/Patentino**: Non è richiesto un patentino specifico per la **SPIT P560**. "
        "È necessaria una **formazione interna** secondo le istruzioni del costruttore.\n"
        "- **Formazione minima**: scelta propulsori, **taratura potenza**, prova su campione, verifica **ancoraggio** dei chiodi, gestione inceppamenti.\n"
        "- **DPI e sicurezza**: **occhiali**, **guanti**, **protezione udito**; operare su **lamiera ben aderente**; rispettare distanze dai bordi; non sparare su supporti deformati/non idonei.\n"
        "- **Procedura di posa**: 1 connettore **CTF** = **2 chiodi HSBR14** con **P560**; nessuna preforatura; potenza regolata in funzione di lamiera/trave.\n"
        "- **Supporto Tecnaria**: disponibili **istruzioni di posa** e indicazioni pratiche per il cantiere.\n"
    )

def build_p560_from_web(sources: List[str]) -> str:
    base = answer_p560_template()
    base += "\n**Fonti**\n" + ("\n".join(f"- {u}" for u in sources) + "\n" if sources else "- web (tecnaria.com)\n")
    return base

# ------------------------------ ROUTING --------------------------------------
def route_question_to_answer(raw_q: str) -> str:
    if not raw_q or not raw_q.strip():
        return "OK\n- **Domanda vuota**: inserisci una richiesta valida.\n"

    cleaned = clean_ui_noise(raw_q)
    nq = normalize(cleaned)

    # CONTATTI -> SOLO dai JSON critici
    if CONT_PAT.search(nq):
        return answer_contacts()

    # P560 + (patentino|formazione) -> web + template tecnico
    if P560_PAT.search(nq) and LIC_PAT.search(nq):
        ans, srcs, _ = web_lookup(cleaned, min_score=MIN_WEB_SCORE, timeout=WEB_TIMEOUT, retries=WEB_RETRIES, domains=PREFERRED_DOMAINS)
        return build_p560_from_web(srcs)

    # GENERALE: WEB ONLY
    ans, srcs, _ = web_lookup(cleaned, min_score=MIN_WEB_SCORE, timeout=WEB_TIMEOUT, retries=WEB_RETRIES)
    if ans:
        return format_as_bot(ans, srcs)

    # Web non disponibile o nessun risultato -> fallback
    return (
        "OK\n"
        "- **Non ho trovato una risposta affidabile sul web** (o la ricerca non è configurata). "
        "Indicami meglio la parola chiave oppure scrivimi per contatto diretto.\n"
    )

# ------------------------------ FASTAPI APP ----------------------------------
app = FastAPI(title="Tecnaria QA Bot", version="3.1.0")

# static + templates
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# Homepage: se c'è index.html servilo, altrimenti banner JSON. Con ?q=... risponde subito.
@app.get("/", response_class=HTMLResponse)
def root(request: Request, q: Optional[str] = Query(None)):
    if q and q.strip():
        ans = route_question_to_answer(q)
        return JSONResponse({"ok": True, "answer": ans})
    index_path = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.isfile(index_path):
        return templates.TemplateResponse("index.html", {"request": request})
    return JSONResponse({"service": "Tecnaria QA Bot", "endpoints": ["/ping", "/health", "/ask (GET q=... | POST JSON/Form/Text)", "/ui"]})

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
        "critici": {"dir": CRITICI_DIR, "exists": bool(CRITICI_DIR and os.path.isdir(CRITICI_DIR))},
    }

# Estrazione q robusta (POST)
def _extract_q_sync(body_bytes: bytes, content_type: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower() if content_type else ""
    # JSON
    if "application/json" in ct:
        try:
            data = json.loads(body_bytes.decode("utf-8", errors="ignore") or "{}")
            q = (data.get("q") or data.get("question") or "").strip()
            if q: return q
        except Exception:
            pass
    # Form
    if "application/x-www-form-urlencoded" in ct or "multipart/form-data" in ct:
        try:
            s = body_bytes.decode("utf-8", errors="ignore")
            d = parse_qs(s, keep_blank_values=True)
            q = (d.get("q", [""])[0] or d.get("question", [""])[0]).strip()
            if q: return q
        except Exception:
            pass
    # Text / sconosciuto
    if "text/plain" in ct or not ct:
        q = (body_bytes.decode("utf-8", errors="ignore") or "").strip()
        if q: return q
    return ""

# /ask – POST
@app.post("/ask")
async def ask_post(req: Request):
    q = ""
    try:
        data = await req.json()
        q = (data.get("q") or data.get("question") or "").strip()
    except Exception:
        pass
    if not q:
        body = await req.body()
        q = _extract_q_sync(body, req.headers.get("content-type") or "")
    ans = route_question_to_answer(q)
    return JSONResponse({"ok": True, "answer": ans})

# /ask – GET
@app.get("/ask")
async def ask_get(q: str = Query("", description="Domanda")):
    ans = route_question_to_answer((q or "").strip())
    return JSONResponse({"ok": True, "answer": ans})

# alias API
@app.post("/api/ask")
async def api_ask_post(req: Request):
    return await ask_post(req)

@app.get("/api/ask")
async def api_ask_get(q: str = Query("", description="Domanda")):
    return await ask_get(q)

# ------------------------------ UI INTEGRATA (ROBUSTA) ----------------------
@app.get("/ui", response_class=HTMLResponse)
def inline_ui():
    # UI minimale con funzione send() robusta (gestisce JSON o testo)
    html = """<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <title>Tecnaria QA Bot – UI</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; }
    body { margin: 0; background:#0b1220; color:#e7eaf3; }
    .wrap { max-width: 980px; margin: 0 auto; padding: 24px; }
    h1 { margin: 0 0 16px; font-size: 22px; font-weight: 700; }
    .card { background:#121a2b; border:1px solid #1e2a44; border-radius: 14px; padding: 16px; }
    .row { display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
    textarea { width:100%; min-height:120px; resize:vertical; padding:12px; border-radius:10px; border:1px solid #1e2a44; background:#0f1626; color:#fff; }
    .btn { background:#2b5cff; color:#fff; border:none; padding:10px 14px; border-radius:10px; cursor:pointer; font-weight:600; }
    .btn.secondary { background:#1f2a45; color:#cdd6f4; }
    .btn:disabled { opacity:.6; cursor:not-allowed; }
    .tags { display:flex; gap:8px; flex-wrap:wrap; }
    .tag { background:#192543; border:1px solid #2a3a63; color:#cdd6f4; padding:6px 10px; border-radius:999px; cursor:pointer; }
    .answer { white-space:pre-wrap; background:#0f1626; border:1px solid #1e2a44; border-radius:10px; padding:12px; min-height:140px; }
    .muted { color:#99a6c4; font-size:12px; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Tecnaria QA Bot – UI</h1>

    <div class="card" style="margin-bottom:16px">
      <div class="tags">
        <span class="tag" data-q="Devo usare la chiodatrice P560 per fissare i CTF. Serve un patentino o formazione speciale?">P560 + CTF</span>
        <span class="tag" data-q="Che differenza c’è tra un connettore CTF e il sistema Diapason?">CTF vs Diapason</span>
        <span class="tag" data-q="Qual è la densità consigliata di connettori CTF per metro quadrato?">Densità CTF</span>
        <span class="tag" data-q="contatti">Contatti</span>
      </div>
    </div>

    <div class="card" style="margin-bottom:16px">
      <textarea id="q" placeholder="Scrivi qui la tua domanda…"></textarea>
      <div class="row" style="margin-top:12px">
        <button id="btnAsk" class="btn">Chiedi</button>
        <button id="btnClear" class="btn secondary">Pulisci</button>
        <button id="btnCopy" class="btn secondary">Copia risposta</button>
        <span id="status" class="muted"></span>
      </div>
      <div class="row" style="margin-top:8px">
        <label class="muted"><input type="checkbox" id="useGet" /> usa GET (debug)</label>
      </div>
    </div>

    <div class="card">
      <div id="answer" class="answer">La risposta apparirà qui…</div>
      <div style="margin-top:8px" class="muted">Endpoint: <code>/ask</code> (POST JSON { q } oppure GET ?q=…)</div>
    </div>
  </div>

  <script>
    const $ = sel => document.querySelector(sel);
    const qEl = $("#q");
    const ansEl = $("#answer");
    const statusEl = $("#status");
    const btnAsk = $("#btnAsk");
    const btnClear = $("#btnClear");
    const btnCopy = $("#btnCopy");
    const useGet = $("#useGet");

    document.querySelectorAll(".tag").forEach(t => {
      t.addEventListener("click", () => { qEl.value = t.dataset.q || ""; qEl.focus(); });
    });

    btnCopy.addEventListener("click", async () => {
      const txt = ansEl.textContent || "";
      try { await navigator.clipboard.writeText(txt); flash("Risposta copiata"); }
      catch { flash("Copia non disponibile"); }
    });

    btnClear.addEventListener("click", () => {
      qEl.value = "";
      ansEl.textContent = "La risposta apparirà qui…";
      flash("");
    });

    qEl.addEventListener("keydown", e => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") send();
    });

    btnAsk.addEventListener("click", send);

    async function send() {
      const domanda = (qEl.value || "").trim();
      if (!domanda) { ansEl.textContent = "OK\\n- **Domanda vuota**: inserisci una richiesta valida."; return; }

      btnAsk.disabled = true; flash("Invio in corso…");
      try {
        let res;
        if (useGet.checked) {
          res = await fetch("/ask?q=" + encodeURIComponent(domanda), { method: "GET" });
        } else {
          res = await fetch("/ask", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ q: domanda })   // <<<<<< CHIAVE GIUSTA
          });
        }

        // Robust parsing: prova JSON; se fallisce, prova testo
        let data = null, text = null;
        const ct = (res.headers.get("content-type") || "").toLowerCase();
        if (ct.includes("application/json")) {
          data = await res.json().catch(() => null);
        } else {
          text = await res.text().catch(() => "");
        }

        if (res.ok && data && typeof data.answer === "string" && data.answer.trim()) {
          ansEl.textContent = data.answer;
        } else if (res.ok && text) {
          ansEl.textContent = text;
        } else {
          ansEl.textContent = "Errore di risposta (" + res.status + "): " + (text || JSON.stringify(data || {}));
        }

        flash("");
      } catch (e) {
        ansEl.textContent = "Errore di rete: " + (e?.message || e);
        flash("errore");
      } finally {
        btnAsk.disabled = false;
      }
    }

    function flash(msg){ statusEl.textContent = msg || ""; }
  </script>
</body>
</html>"""
    return HTMLResponse(html)

# ------------------------------ MAIN (dev) -----------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
