# -*- coding: utf-8 -*-
"""
Tecnaria Sinapsi – app.py (rules DISATTIVATE)
- API: /, /health, /ask?q=..., /company
- UI elegante su /ui (nero+arancio, logo da static/data/contatti.json)
- Motore: Router -> dataset famiglia -> fallback catalogo (niente rules)
Dati in: static/data/
"""

import json, math, re
from pathlib import Path
from collections import Counter, defaultdict

# =========================
# PATH DATI
# =========================
BASE_PATH     = Path("static/data")
ROUTER_FILE   = BASE_PATH / "tecnaria_router_index.json"
CATALOG_FILE  = BASE_PATH / "tecnaria_catalogo_unico.json"
CONTACTS_FILE = BASE_PATH / "contatti.json"

def dataset_path_for_family(code: str) -> Path:
    return BASE_PATH / f"tecnaria_{code.lower()}_qa500.json"

# =========================
# IO / UTIL
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
# ROUTER -> FAMIGLIA
# =========================
def route_family(query: str) -> str:
    router = load_json(ROUTER_FILE)
    q = norm(query)
    # matching diretto su code / name / family
    for p in router.get("products", []):
        for key in (norm(p.get("code","")), norm(p.get("name","")), norm(p.get("family",""))):
            if key and key in q:
                return p.get("code","")
    # euristiche utili
    if any(k in q for k in ["p560","chiodatrice","propulsori","propulsore"]): return "SPIT-P560"
    if any(k in q for k in ["gts","manicotto","giunzione meccanica"]):       return "GTS"
    if any(k in q for k in ["diapason","laterocemento","rinforzo solaio"]):  return "DIAPASON"
    if any(k in q for k in ["mini-cem-e","minicem","calcestruzzo-calcestruzzo"]): return "MINI-CEM-E"
    if any(k in q for k in ["ctl","legno-calcestruzzo","legno"]):            return "CTL"
    if any(k in q for k in ["ctf","connettore","solaio collaborante"]):      return "CTF"
    return ""

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
            for t in set(toks): self.df[t] += 1
        self.idf = defaultdict(float)
        for t, df in self.df.items():
            self.idf[t] = math.log(1 + (self.N - df + 0.5) / (df + 0.5))

    def score(self, qtok, idx, k1=1.5, b=0.75):
        toks = self.doc_tokens[idx]
        if not toks: return 0.0
        tf, dl = Counter(toks), len(toks)
        avgdl = (sum(len(x) for x in self.doc_tokens)/max(self.N,1)) if self.N else 1
        s = 0.0
        for t in qtok:
            if t not in tf: continue
            idf = self.idf.get(t, 0.0)
            denom = tf[t] + k1*(1 - b + b*dl/avgdl)
            s += idf * (tf[t]*(k1+1)) / (denom if denom else 1.0)
        return s

    def top1(self, query: str):
        qtok = tokenize(query)
        best_doc, best_score = None, -1.0
        for i, d in enumerate(self.docs):
            sc = self.score(qtok, i)
            if sc > best_score:
                best_score, best_doc = sc, d
        return best_doc, best_score

def semantic_pick(query: str, qa_list: list[dict]):
    if not qa_list: return None
    def text_fn(d):
        return " ".join([
            safe_get(d,"q",""), safe_get(d,"a",""),
            safe_get(d,"category",""), " ".join(safe_get(d,"tags",[]))
        ])
    ts = TinySearch(qa_list, text_fn)
    best, score = ts.top1(query)
    return best if score and score > 0.5 else None

# =========================
# NARRATIVA
# =========================
def compose_answer(hit: dict) -> str:
    a = (hit or {}).get("a","").strip()
    if not a: return ""
    if not a.endswith((".", "!", "?")): a += "."
    return a + " — Tecnaria S.p.A., Bassano del Grappa. Per i dettagli operativi: consultare schede e manuali ufficiali."

# =========================
# PIPELINE (NO RULES)
# =========================
METRICS = {"total": 0, "by_family": {}}
def _bump(family: str):
    METRICS["total"] += 1
    k = family or "fallback"
    METRICS["by_family"][k] = METRICS["by_family"].get(k, 0) + 1

def ask(query: str) -> str:
    q = (query or "").strip()

    # 1) routing -> dataset famiglia
    fam = route_family(q)
    if fam:
        data = load_json(dataset_path_for_family(fam))
        hit = semantic_pick(q, data.get("qa", []))
        if hit:
            _bump(fam)
            return compose_answer(hit)

    # 2) fallback sul catalogo unico
    catalog = load_json(CATALOG_FILE)
    all_qa = []
    for it in catalog.get("items", []):
        all_qa.extend(it.get("qa", []))
    hit = semantic_pick(q, all_qa)
    if hit:
        _bump("catalogo")
        return compose_answer(hit)

    # 3) miss finale
    return "Non ho trovato la risposta nei contenuti Tecnaria. Dimmi esattamente cosa ti serve e la aggiungo subito alla base."

# =========================
# FASTAPI + UI
# =========================
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

app = FastAPI(title="Tecnaria Sinapsi", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def root():
    return {"name":"Tecnaria Sinapsi","status":"ok",
            "endpoints":{"health":"/health","ask":"/ask?q=...","company":"/company","docs":"/docs","ui":"/ui"}}

@app.get("/health")
def health():
    return {
        "status": "ok",
        "router": (ROUTER_FILE.exists()),
        "catalog": (CATALOG_FILE.exists()),
        "contacts": (CONTACTS_FILE.exists()),
        "metrics": METRICS
    }

@app.get("/ask")
def http_ask(q: str = Query(..., description="Domanda")):
    q = (q or "").strip()
    if not q or len(q) > 1000:
        return {"answer": "Domanda vuota o troppo lunga."}
    return {"answer": ask(q)}

@app.get("/company")
def company():
    data = load_json(CONTACTS_FILE)
    return data if data else {"error":"contatti.json non trovato"}

# ---------- UI su /ui (nero+arancio, logo automatico) ----------
UI_HTML = r"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Tecnaria Sinapsi</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
:root{ --tec-orange:#F26622; --tec-black:#000000; --tec-white:#FFFFFF; }
.brand-grad{ background: linear-gradient(180deg,var(--tec-black),var(--tec-orange)); }
</style>
</head>
<body class="bg-[var(--tec-white)] text-neutral-900">
<header class="sticky top-0 z-30 border-b border-neutral-200 bg-white/90 backdrop-blur">
  <div class="mx-auto max-w-6xl px-4 py-3 flex items-center gap-4">
    <div class="h-9 flex items-center">
      <img id="logoImg" alt="Tecnaria" class="h-9 hidden" />
      <div id="logoFallback" class="w-9 h-9 rounded bg-[var(--tec-black)] grid place-items-center">
        <span class="text-white font-black text-xl">T</span>
      </div>
    </div>
    <div>
      <h1 class="text-xl font-semibold leading-tight text-neutral-900">Tecnaria Sinapsi</h1>
      <p class="text-xs text-neutral-500">Risposte tecniche. Voce ufficiale Tecnaria.</p>
    </div>
    <div class="ml-auto flex items-center gap-3">
      <a id="docsLink" target="_blank" class="text-sm text-neutral-600 hover:text-neutral-900 underline">API Docs</a>
    </div>
  </div>
</header>

<section class="brand-grad text-white">
  <div class="mx-auto max-w-6xl px-4 py-10">
    <h2 class="text-3xl md:text-4xl font-semibold">Trova la soluzione, in linguaggio Tecnaria.</h2>
    <p class="mt-2 text-white/80">CTF, GTS, Diapason, Mini-Cem-E, SPIT P560, CTL.</p>
    <form id="form" class="mt-6 flex flex-col md:flex-row gap-3">
      <input id="q" placeholder="Scrivi la tua domanda…" class="flex-1 rounded-xl border border-white/20 bg-white/95 px-4 py-3 text-neutral-900 placeholder-neutral-500 focus:outline-none focus:ring-4 focus:ring-white/30"/>
      <button id="askBtn" class="rounded-xl bg-[var(--tec-black)] px-5 py-3 font-medium text-white shadow hover:opacity-90">Chiedi a Sinapsi</button>
    </form>
    <div class="mt-4 flex flex-wrap gap-2 text-sm">
      <button data-preset="Si può usare una qualsiasi chiodatrice per i CTF?" class="rounded-full bg-white/15 px-3 py-1.5 hover:bg-white/25">CTF · Chiodatrice</button>
      <button data-preset="CTL: serve il preforo su abete antico?" class="rounded-full bg-white/15 px-3 py-1.5 hover:bg-white/25">CTL · Preforo abete</button>
      <button data-preset="Diapason vs soletta collaborante tradizionale?" class="rounded-full bg-white/15 px-3 py-1.5 hover:bg-white/25">Diapason · Confronto</button>
      <button data-preset="Taratura P560 su IPE 300 con lamiera 6/10" class="rounded-full bg-white/15 px-3 py-1.5 hover:bg-white/25">P560 · Taratura IPE 300</button>
    </div>
  </div>
</section>

<main class="mx-auto max-w-6xl px-4 py-8 grid grid-cols-1 lg:grid-cols-3 gap-6">
  <aside class="lg:col-span-1 space-y-6">
    <div class="rounded-2xl border border-neutral-200 bg-white p-4 shadow-sm">
      <h3 class="font-semibold">Impostazioni</h3>
      <label class="mt-2 block text-xs text-neutral-600">Base URL servizio</label>
      <input id="baseUrl" class="mt-1 w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"/>
    </div>

    <div class="rounded-2xl border border-neutral-200 bg-white p-4 shadow-sm">
      <h3 class="font-semibold">Contatti & Dati aziendali</h3>
      <div id="companyBox" class="mt-3 text-sm text-neutral-700"><div class="text-neutral-500">Caricamento…</div></div>
      <div class="mt-3 flex flex-wrap gap-2">
        <button id="copyIban" class="rounded-lg border border-neutral-300 bg-neutral-50 px-3 py-1.5 text-sm hover:bg-neutral-100">Copia IBAN</button>
        <a id="mailtoTech" class="rounded-lg border border-neutral-300 bg-neutral-50 px-3 py-1.5 text-sm hover:bg-neutral-100" href="#">Scrivi a tecnico</a>
        <a id="telMain" class="rounded-lg border border-neutral-300 bg-neutral-50 px-3 py-1.5 text-sm hover:bg-neutral-100" href="#">Chiama sede</a>
      </div>
    </div>

    <div class="rounded-2xl border border-neutral-200 bg-white p-4 shadow-sm">
      <h3 class="font-semibold">Domande rapide</h3>
      <div id="quick" class="mt-3 flex flex-wrap gap-2 text-sm"></div>
    </div>
  </aside>

  <section class="lg:col-span-2 space-y-6">
    <div class="rounded-2xl border border-neutral-200 bg-white p-5 shadow-sm min-h-[260px]">
      <div class="flex items-center justify-between">
        <h3 class="font-semibold">Risposta</h3>
        <div class="flex items-center gap-3 text-xs text-neutral-500">
          <span>Latency: <strong id="lat">—</strong></span>
          <button id="copy" class="rounded-lg border border-neutral-300 px-2 py-1 hover:bg-neutral-50">Copia</button>
        </div>
      </div>
      <div id="err" class="hidden mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700"></div>
      <article id="ans" class="prose prose-neutral max-w-none mt-4 whitespace-pre-wrap"></article>
    </div>
  </section>
</main>

<footer class="mt-10 border-t border-neutral-200 bg-white">
  <div class="mx-auto max-w-6xl px-4 py-6 flex flex-col md:flex-row items-center justify-between gap-3">
    <div class="flex items-center gap-3">
      <img id="logoImgFooter" alt="Tecnaria" class="h-7 hidden" />
      <div id="logoFallbackFooter" class="w-7 h-7 rounded bg-[var(--tec-black)] grid place-items-center">
        <span class="text-white font-black">T</span>
      </div>
      <span class="text-sm text-neutral-600">Tecnaria S.p.A. — Bassano del Grappa (VI)</span>
    </div>
    <div class="text-xs text-neutral-500">API: <code id="urlShow"></code></div>
  </div>
</footer>

<script>
const $  = (s)=>document.querySelector(s);
const $$ = (s)=>Array.from(document.querySelectorAll(s));

const DEFAULT_BASE_URL = location.origin;
const quickQs = [
  "CTF: quanti chiodi per connettore?",
  "CTL: spessore minimo soletta collaborante?",
  "GTS: controlli di compressione in camicia?",
  "Diapason: stratigrafia tipica?",
  "Mini-Cem-E: posa su cls vecchio?",
  "P560: quali propulsori usare su IPE?"
];

const baseUrlInput = $("#baseUrl");
baseUrlInput.value = DEFAULT_BASE_URL;
$("#urlShow").textContent = DEFAULT_BASE_URL;
$("#docsLink").href = DEFAULT_BASE_URL + "/docs";

async function loadCompany(){
  try{
    const res = await fetch(baseUrlInput.value + "/company");
    const c = await res.json();
    const box = $("#companyBox");
    if(c.error){ box.innerHTML = `<div class="text-red-600">${c.error}</div>`; return; }
    const depTech = (c.departments||[]).find(d => (d.name||'').toLowerCase().includes('tecnica'));
    const phone   = c.company?.hq?.phone || c.hq?.phone || "";
    const email   = c.company?.hq?.email || c.hq?.email || "";
    const logo    = c.company?.logo_url;

    box.innerHTML = `
      <div class="font-medium">${c.company?.legal_name || "Tecnaria S.p.A."}</div>
      <div class="text-neutral-600">${(c.company?.hq?.address||c.hq?.address||"")} ${(c.company?.hq?.zip||c.hq?.zip||"")} ${(c.company?.hq?.city||c.hq?.city||"")} ${(c.company?.hq?.province||c.hq?.province||"")}</div>
      <div class="mt-1">Email: <a class="underline" href="mailto:${email}">${email}</a></div>
      <div class="mt-1">Telefono: <a class="underline" href="tel:${(phone||'').replace(/\\s+/g,'')}">${phone||''}</a></div>
      <hr class="my-3">
      <div class="font-medium">Dati bancari</div>
      <div class="mt-1">Beneficiario: ${c.banking?.beneficiary||''}</div>
      <div class="mt-1">Banca: ${c.banking?.bank_name||''} ${c.banking?.branch?("— "+c.banking.branch):""}</div>
      <div class="mt-1">IBAN: <code id="ibanText">${c.banking?.iban||''}</code></div>
      <div class="mt-1">BIC/SWIFT: <code>${c.banking?.bic_swift||''}</code></div>
      <div class="mt-1 text-neutral-600">${c.banking?.notes||c.banking?.payment_notes||''}</div>
    `;

    // link rapidi e logo
    $("#mailtoTech").href = depTech?.email ? `mailto:${depTech.email}` : (email?`mailto:${email}`:"#");
    $("#telMain").href    = phone ? `tel:${phone.replace(/\\s+/g,'')}` : "#";

    if(logo){
      const test = new Image();
      test.onload = ()=>{
        const h = document.getElementById("logoImg");
        const hf = document.getElementById("logoFallback");
        h.src = logo; h.classList.remove("hidden"); hf.classList.add("hidden");
        const f = document.getElementById("logoImgFooter");
        const ff= document.getElementById("logoFallbackFooter");
        f.src = logo; f.classList.remove("hidden"); ff.classList.add("hidden");
      };
      test.src = logo;
    }
  }catch(e){
    $("#companyBox").innerHTML = `<div class="text-red-600">Impossibile leggere i dati aziendali</div>`;
  }
}

async function ask(q){
  $("#err").classList.add("hidden"); $("#ans").textContent = "";
  const t0 = performance.now();
  try{
    const res = await fetch(baseUrlInput.value + "/ask?q=" + encodeURIComponent(q));
    if(!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    $("#lat").textContent = Math.round(performance.now() - t0) + " ms";
    $("#ans").textContent = data.answer || "";
  }catch(e){ $("#err").textContent = "Errore: " + (e.message||"imprevisto"); $("#err").classList.remove("hidden"); }
}

$("#form").addEventListener("submit", (ev)=>{ ev.preventDefault(); const q = $("#q").value.trim(); if(q) ask(q); });
$$("[data-preset]").forEach(b=> b.addEventListener("click", ()=>{ $("#q").value = b.dataset.preset; ask(b.dataset.preset); }));
$("#copy").addEventListener("click", async ()=>{ try{ await navigator.clipboard.writeText($("#ans").textContent); alert("Risposta copiata"); }catch{ alert("Impossibile copiare"); }});
baseUrlInput.addEventListener("change", ()=>{ $("#urlShow").textContent = baseUrlInput.value; $("#docsLink").href = baseUrlInput.value + "/docs"; loadCompany(); });
quickQs.forEach(q=>{ const btn=document.createElement("button"); btn.className="rounded-full border border-neutral-300 bg-neutral-50 px-3 py-1.5 hover:bg-neutral-100"; btn.textContent=q; btn.onclick=()=>{ $("#q").value=q; ask(q); }; $("#quick").appendChild(btn); });

loadCompany();
</script>
</body></html>
"""

@app.get("/ui", response_class=HTMLResponse)
def ui():
    return HTMLResponse(UI_HTML, status_code=200)
