# -*- coding: utf-8 -*-
"""
Tecnaria Sinapsi – app.py (router-only, robust + overview-first ranking)
API: /, /health, /ask?q=..., /company, /debug?q=..., /selfcheck, /ui
Dati attesi in static/data/:
  - tecnaria_router_index.json
  - tecnaria_catalogo_unico.json
  - tecnaria_ctf_qa500.json
  - tecnaria_gts_qa500.json
  - tecnaria_diapason_qa500.json
  - tecnaria_mini-cem-e_qa500.json  (accetta anche mini_cem_e / minicem / miniceme)
  - tecnaria_ctl_qa500.json
  - tecnaria_spit-p560_qa500.json   (accetta anche spit_p560 / spitp560)
  - contatti.json
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

def norm(s: str) -> str:
    return (s or "").lower().strip()

# =========================
# IO / UTIL
# =========================
def load_json(path: Path):
    if not path or not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def safe_get(d, k, default=None):
    return d[k] if isinstance(d, dict) and k in d else default

WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9\-\_]+")
def tokenize(text: str):
    return [t for t in WORD_RE.findall(norm(text)) if t]

# =========================
# ROUTER -> FAMIGLIA (robusto)
# =========================
def route_family(query: str) -> str:
    q = norm(query)
    router = load_json(ROUTER_FILE)

    # 1) match su router (code/name/family)
    for p in router.get("products", []):
        for key in (norm(p.get("code","")), norm(p.get("name","")), norm(p.get("family",""))):
            if key and key in q:
                code = norm(p.get("code",""))
                return code or norm(p.get("family",""))

    # 2) euristiche robuste (spazi/segni)
    qc = q.replace(" ", "").replace("_", "").replace("-", "")
    if "p560" in qc or "chiodatrice" in q or "propulsor" in q or "spit" in q:  return "spit-p560"
    if "gts" in qc or "manicotto" in q or "giunzionemeccanica" in qc:         return "gts"
    if "diapason" in qc or "laterocemento" in qc:                               return "diapason"
    if "minicem" in qc or "miniceme" in qc or "minicem-e" in qc:                return "mini-cem-e"
    if "ctl" in qc or "legno-calcestruzzo" in qc or "legno" in q:               return "ctl"
    if "ctf" in qc or "connettore" in q or "solaio collaborante" in q:          return "ctf"
    return ""

# =========================
# DATASET RESOLUTION (robusto)
# =========================
def dataset_candidates_for_code(code: str):
    if not code:
        return []
    c = norm(code)
    forms = {
        c,
        c.replace("-", "_"),
        c.replace("_", "-"),
        c.replace("-", ""),
        c.replace("_", ""),
        c.replace(" ", ""),
    }
    if c == "spit-p560":
        forms.update({"spit_p560","spitp560"})
    if c == "mini-cem-e":
        forms.update({"mini_cem_e","minicem","miniceme"})

    candidates = []
    for f in forms:
        candidates.append(BASE_PATH / f"tecnaria_{f}_qa500.json")
        candidates.append(BASE_PATH / f"{f}_qa500.json")  # legacy
    # de-dup
    seen, ordered = set(), []
    for p in candidates:
        s = str(p)
        if s not in seen:
            ordered.append(p); seen.add(s)
    return ordered

def extract_qa(payload):
    """Estrae QA da varie strutture."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("qa"), list):
        return payload["qa"]
    acc = []
    for key in ("items","dataset","data","entries"):
        arr = payload.get(key)
        if isinstance(arr, list):
            for it in arr:
                qa = it.get("qa") if isinstance(it, dict) else None
                if isinstance(qa, list): acc.extend(qa)
    return acc

def load_family_dataset(code: str):
    """Ritorna (qa_list, used_path) scegliendo il primo file esistente che contenga QA."""
    for p in dataset_candidates_for_code(code):
        if p.exists():
            data = load_json(p)
            qa = extract_qa(data)
            if qa:
                return qa, p
    return [], None

# =========================
# SEMANTICO (BM25 con pesi + overview-first)
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

def semantic_pick(query: str, qa_list: list[dict]):
    """
    Rank ibrido:
    - Se la query è da overview, prova PRIMA a selezionare tra le voci 'prodotto_base' (o tag 'overview').
      Se non presenti, torna all'intero dataset.
    - BM25 con pesi (q 3x, a 1x, category/tags 1x)
    - Boost 'prodotto_base' su overview, penalità duplicati su 'a'
    """
    if not qa_list:
        return None

    def is_overview(q: str) -> bool:
        qn = norm(q)
        keys = ["parlami", "parla di", "cos'è", "che cos", "informazioni", "overview", "presentazione", "scheda"]
        return any(k in qn for k in keys)

    def clean(txt: str) -> str:
        return re.sub(r"\s+", " ", norm(txt or ""))

    def doc_text(d: dict):
        qtxt = safe_get(d, "q", "")
        atxt = safe_get(d, "a", "")
        cat  = safe_get(d, "category", "")
        tags = " ".join(safe_get(d, "tags", []))
        # pesi: domanda 3x, risposta 1x, category/tags 1x
        return (" " + qtxt + " ") * 3 + " " + atxt + " " + (" " + cat + " ") + " " + (" " + tags + " ")

    # --- PREFILTRO OVERVIEW ---
    q_is_overview = is_overview(query)
    pool = qa_list
    if q_is_overview:
        pool_over = []
        for d in qa_list:
            cat = norm(d.get("category",""))
            tags = [norm(t) for t in (d.get("tags") or [])]
            if cat == "prodotto_base" or "overview" in tags:
                pool_over.append(d)
        if pool_over:  # usa solo overview se esistono
            pool = pool_over

    # Rank BM25 con pesi + boost/punizioni
    ts = TinySearch(pool, doc_text)
    qtok = tokenize(query)
    a_clean_counts = Counter(clean(d.get("a","")) for d in pool)

    best_idx, best_sc = None, -1.0
    for i, d in enumerate(pool):
        base_sc = ts.score(qtok, i)
        sc = base_sc

        # boost categoria 'prodotto_base' sulle overview
        if q_is_overview and norm(d.get("category","")) == "prodotto_base":
            sc *= 1.35  # determinismo più alto

        # penalità per risposte duplicate (stessa 'a' ripetuta)
        if a_clean_counts[clean(d.get("a",""))] >= 3:
            sc *= 0.85

        if sc > best_sc:
            best_sc, best_idx = sc, i

    return pool[best_idx] if best_idx is not None else None

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

    # 1) routing -> dataset famiglia (robusto)
    fam = route_family(q)
    if fam:
        qa, used_path = load_family_dataset(fam)
        if qa:
            hit = semantic_pick(q, qa)
            if hit:
                _bump(fam)
                return compose_answer(hit)

    # 2) fallback sul catalogo unico
    catalog = load_json(CATALOG_FILE)
    all_qa = extract_qa(catalog)
    hit = semantic_pick(q, all_qa)
    if hit:
        _bump("catalogo")
        return compose_answer(hit)

    # 3) miss
    return "Non ho trovato la risposta nei contenuti Tecnaria. Dimmi esattamente cosa ti serve e la aggiungo subito alla base."

# =========================
# FASTAPI + UI
# =========================
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

app = FastAPI(title="Tecnaria Sinapsi", version="1.3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def root():
    return {"name":"Tecnaria Sinapsi","status":"ok",
            "endpoints":{"health":"/health","ask":"/ask?q=...","company":"/company","docs":"/docs","ui":"/ui","debug":"/debug?q=...","selfcheck":"/selfcheck"}}

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

# ---------- DEBUG di una singola query ----------
@app.get("/debug")
def debug(q: str = Query(..., description="Domanda per il debug")):
    fam = route_family(q)
    candidates = [str(p) for p in dataset_candidates_for_code(fam)] if fam else []
    existing   = [str(p) for p in dataset_candidates_for_code(fam) if p.exists()] if fam else []
    qa, used   = load_family_dataset(fam) if fam else ([], None)
    hit        = semantic_pick(q, qa) if qa else None
    return {
        "query": q,
        "family": fam,
        "used_path": str(used) if used else None,
        "qa_count": len(qa),
        "candidates": candidates[:8],
        "existing": existing[:8],
        "hit_q": (hit or {}).get("q"),
        "preview_a": ((hit or {}).get("a","")[:200] + ("…" if (hit and len(hit.get('a',""))>200) else "")) if hit else None
    }

# ---------- SELFCHECK (testa tutte le famiglie con query campione) ----------
@app.get("/selfcheck")
def selfcheck():
    probes = [
        ("ctf", "Parlami dei connettori CTF"),
        ("gts", "Parlami del manicotto GTS"),
        ("diapason", "Parlami del sistema Diapason"),
        ("mini-cem-e", "Parlami del Mini-Cem-E"),
        ("ctl", "Parlami del sistema CTL"),
        ("spit-p560", "Parlami della SPIT P560"),
    ]
    out = []
    for code, probe_q in probes:
        qa, used = load_family_dataset(code)
        hit = semantic_pick(probe_q, qa) if qa else None
        out.append({
            "family": code,
            "used_path": str(used) if used else None,
            "qa_count": len(qa),
            "probe_q": probe_q,
            "hit_q": (hit or {}).get("q"),
            "preview_a": ((hit or {}).get("a","")[:200] + ("…" if (hit and len(hit.get('a',""))>200) else "")) if hit else None
        })
    return {"status":"ok","checks": out}

# ---------- UI su /ui ----------
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
document.getElementById("urlShow")?.textContent = DEFAULT_BASE_URL;
document.getElementById("docsLink").href = DEFAULT_BASE_URL + "/docs";

async function loadCompany(){
  try{
    const res = await fetch(baseUrlInput.value + "/company");
    const c = await res.json();
    const box = document.getElementById("companyBox");
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

    if(depTech?.email) document.getElementById("mailtoTech").href = `mailto:${depTech.email}`;
    if(phone) document.getElementById("telMain").href = `tel:${phone.replace(/\\s+/g,'')}`;

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
    document.getElementById("companyBox").innerHTML = `<div class="text-red-600">Impossibile leggere i dati aziendali</div>`;
  }
}

async function ask(q){
  document.getElementById("err").classList.add("hidden");
  document.getElementById("ans").textContent = "";
  const t0 = performance.now();
  try{
    const res = await fetch(baseUrlInput.value + "/ask?q=" + encodeURIComponent(q));
    if(!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    document.getElementById("lat").textContent = Math.round(performance.now() - t0) + " ms";
    document.getElementById("ans").textContent = data.answer || "";
  }catch(e){
    const box = document.getElementById("err");
    box.textContent = "Errore: " + (e.message||"imprevisto");
    box.classList.remove("hidden");
  }
}

document.getElementById("form").addEventListener("submit", (ev)=>{ ev.preventDefault(); const q = document.getElementById("q").value.trim(); if(q) ask(q); });
$$("[data-preset]").forEach(b=> b.addEventListener("click", ()=>{ document.getElementById("q").value = b.dataset.preset; ask(b.dataset.preset); }));
document.getElementById("copy").addEventListener("click", async ()=>{ try{ await navigator.clipboard.writeText(document.getElementById("ans").textContent); alert("Risposta copiata"); }catch{ alert("Impossibile copiare"); }});
baseUrlInput.addEventListener("change", ()=>{ document.getElementById("docsLink").href = baseUrlInput.value + "/docs"; loadCompany(); });
["CTF: quanti chiodi per connettore?","CTL: spessore minimo soletta collaborante?","GTS: controlli di compressione in camicia?","Diapason: stratigrafia tipica?","Mini-Cem-E: posa su cls vecchio?","P560: quali propulsori usare su IPE?"].forEach(q=>{ const btn=document.createElement("button"); btn.className="rounded-full border border-neutral-300 bg-neutral-50 px-3 py-1.5 hover:bg-neutral-100"; btn.textContent=q; btn.onclick=()=>{ document.getElementById("q").value=q; ask(q); }; document.getElementById("quick").appendChild(btn); });

loadCompany();
</script>
</body></html>
"""

@app.get("/ui", response_class=HTMLResponse)
def ui():
    return HTMLResponse(UI_HTML, status_code=200)
