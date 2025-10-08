# -*- coding: utf-8 -*-
"""
Tecnaria Sinapsi – app.py (v4.3)
- Classifier semantico (ruolo entità + intento) + router pesato
- Regole cross: CTF↔P560; incompatibilità CTF/P560 su legno -> CTL
- Boost: GTS (preforo), P560 (taratura/lamiera 1,5)
- Picker: preferenza overview + tag-matching operativo
- Narrativa: formatter Tecnaria + ENRICH_NARRATIVE=1 per tono più "umano" (locale, no GPT)
- UI nera/arancione, bottone “Chiedi”, disclaimer stile ChatGPT
- Endpoints: /health, /selfcheck, /debug, /ask, /company, /ui
"""
import json, re, os
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

# ====== CONFIG ======
BASE_PATH = Path("static/data")
CONTACTS_FILE = BASE_PATH / "contatti.json"
BANK_FILE = BASE_PATH / "bancari.json"

FAMILIES = ["ctf", "gts", "diapason", "ctl", "mini-cem-e", "spit-p560"]

# Parole chiave router
KEYWORDS: Dict[str, List[str]] = {
    "ctf": [
        "ctf","connettore","solaio","collaborante","acciaio","calcestruzzo",
        "lamiera","lamiera grecata","trave","trave in acciaio"
    ],
    "gts": [
        "gts","manicotto","manicotti","giunzione","giunzioni","spine","spina",
        "tiranti","camicia","preforo","preforare","coppia","chiave dinamometrica","filettato","filettati"
    ],
    "diapason": ["diapason","soletta leggera","cappa","rinforzo laterocemento","laterocemento"],
    "ctl": ["ctl","vite","viti","legno calcestruzzo","tavolato","tetto","travi in legno","solaio in legno","trave in legno","legno"],
    "mini-cem-e": ["mini-cem-e","minicem","camicia","consolidamento","iniezione","boiacca"],
    "spit-p560": [
        "p560","spit","chiodatrice","sparachiodi","propulsore","propulsori",
        "taratura","regolazione","potenza","hsbr14","hsbr 14","hsb r14",
        "chiodo","chiodi","lamiera 1,5","lamiera 15","1.5 mm","1,5 mm"
    ],
}

# Dizionario semantico
ENTITY_DICT = {
    "tool": ["p560", "spit", "chiodatrice", "sparachiodi", "propulsore", "propulsori"],
    "component": ["ctf", "connettore", "gts", "manicotto", "diapason", "ctl", "mini-cem-e", "minicem"],
    "material": ["legno", "acciaio", "calcestruzzo", "lamiera", "lamiera grecata", "laterocemento"],
    "action": ["posa", "fissare", "infissione", "tarare", "taratura", "regolare", "montare", "iniezione", "preforo", "preforare"],
}

# Intents
INTENT_PATTERNS = {
    "explain": ["parlami", "che cos", "cos'è", "cos e", "descrivi", "spiegami"],
    "usage": ["posso", "come si", "come va", "si può", "si puo", "serve", "necessario", "obbligatorio"],
    "compare": [" vs ", "contro", "meglio", "convenienza", "conviene", "oppure", "differenza", "differenze"],
    "verify": ["come verifico", "come controllo", "controllo", "verifica", "taratura", "tarare"],
    "safety": ["sicurezza", "dpi", "protezione", "occhiali", "guanti", "cuffie"],
}

# ====== Utils ======
WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+", re.UNICODE)
def norm(text: str) -> str:
    return " ".join(w.lower() for w in WORD_RE.findall(text or ""))

def contains_any_norm(text: str, kws: List[str]) -> bool:
    t = norm(text)
    return any(k in t for k in kws)

# ====== IO ======
def load_json(path: Path) -> Any:
    if not path or not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"__error__": f"{path}: {e.__class__.__name__}: {e}"}

def extract_qa(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict) and "__error__" in payload:
        return []
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("qa"), list):
        return payload["qa"]
    acc = []
    for key in ("items", "dataset", "data", "entries"):
        arr = payload.get(key)
        if isinstance(arr, list):
            for it in arr:
                if isinstance(it, dict) and isinstance(it.get("qa"), list):
                    acc.extend(it["qa"])
    return acc

def dataset_candidates_for_code(code: Optional[str]) -> List[Path]:
    if not code:
        return []
    names = [f"tecnaria_{code}_qa500.json"]
    code_u = code.replace("-", "_")
    names += [
        f"tecnaria_{code_u}_qa500.json",
        f"{code}_qa500.json",
        f"{code_u}_qa500.json",
        f"tecnaria{code_u}_qa500.json",
        f"tecnaria_{code.replace('_','-')}_qa500.json",
    ]
    if code == "mini-cem-e":
        names += ["tecnaria_minicemE_qa500.json", "tecnaria_miniceme_qa500.json"]
    return [BASE_PATH / n for n in names]

def load_family_dataset(code: Optional[str]) -> Tuple[List[Dict[str, Any]], Optional[Path], Optional[str]]:
    for p in dataset_candidates_for_code(code):
        if p.exists():
            raw = load_json(p)
            qa = extract_qa(raw)
            err = raw.get("__error__") if isinstance(raw, dict) else None
            return qa, p, err
    return [], None, None

# ====== Classifier ======
def detect_entities(question: str) -> Dict[str, List[str]]:
    t = norm(question)
    found = {"tool": [], "component": [], "material": [], "action": []}
    for role, kws in ENTITY_DICT.items():
        for k in kws:
            if k in t:
                found[role].append(k)
    return found

def detect_intent(question: str) -> str:
    t = norm(question)
    if contains_any_norm(t, INTENT_PATTERNS["compare"]):
        return "compare"
    for intent, pats in INTENT_PATTERNS.items():
        if intent == "compare":
            continue
        if contains_any_norm(t, pats):
            return intent
    if any(a in t for a in ("come","posso","si puo","si può","serve","necessario")):
        return "usage"
    return "explain"

def base_scores_from_keywords(question: str) -> Dict[str, float]:
    t = norm(question)
    scores = {fam: 0.0 for fam in FAMILIES}
    for fam, kws in KEYWORDS.items():
        for k in kws:
            if k in t:
                scores[fam] += 1.0
    if contains_any_norm(question, KEYWORDS["spit-p560"]):
        scores["spit-p560"] += 1.5
    if contains_any_norm(question, ["parlami", "che cos", "cos e", "cos'è", "cos'e"]):
        for fam in FAMILIES:
            scores[fam] += 0.05
    return scores

def route_insight(question: str) -> Dict[str, Any]:
    t = norm(question)
    intent = detect_intent(question)
    entities = detect_entities(question)
    base_scores = base_scores_from_keywords(question)

    mentions_tool      = len(entities.get("tool", [])) > 0
    mentions_component = entities.get("component", [])
    mentions_materials = entities.get("material", [])

    # Priorità per componenti esplicite
    primary = None
    explicit_comp = None
    for comp in ("gts","ctl","ctf","diapason","mini-cem-e"):
        if comp in [c.lower() for c in mentions_component]:
            explicit_comp = comp
            break

    if explicit_comp:
        primary = explicit_comp
    elif mentions_tool and not mentions_component:
        primary = "spit-p560"
    else:
        primary = max(base_scores, key=lambda k: base_scores[k]) if any(base_scores.values()) else None

    needs_p560_for_ctf = (primary == "ctf") and (
        mentions_tool or any(k in t for k in ["chiodatrice","sparachiodi","propulsore","propulsori","taratura","potenza","chiodo","chiodi"])
    )

    incompatible_ctf_on_wood = ("legno" in [m.lower() for m in mentions_materials]) and (primary in ("ctf","spit-p560"))

    # Compare: top2 famiglie
    sorted_by_score = sorted(base_scores.items(), key=lambda x: x[1], reverse=True)
    compare_candidates = [k for k,v in sorted_by_score if v>0][:2]
    explicit_components = [c for c in ("ctf","ctl","gts","diapason","mini-cem-e") if c in [cc.lower() for cc in mentions_component]]
    if intent == "compare" and explicit_components:
        compare_candidates = explicit_components[:2]

    return {
        "intent": intent,
        "entities": entities,
        "base_scores": base_scores,
        "primary": primary,
        "secondary": sorted([fam for fam in FAMILIES if fam != primary and base_scores[fam] > 0], key=lambda x: base_scores[x], reverse=True),
        "needs_p560_for_ctf": needs_p560_for_ctf,
        "incompatible_ctf_on_wood": incompatible_ctf_on_wood,
        "compare_candidates": compare_candidates
    }

# ====== Ranker + picker ======
def score_item(q: str, item: Dict[str, Any]) -> float:
    tq = set(norm(q).split())
    iq = set(norm(item.get("q","")).split())
    ia = set(norm(item.get("a","")).split())
    overlap = len(tq & (iq | ia)) / (len(tq | iq | ia) or 1)
    bonus = 0.0

    cat = (item.get("category") or "").lower()
    tags = [str(t).lower() for t in item.get("tags", []) if isinstance(t, (str,int,float))]

    # Preferisci overview
    if cat in ("prodotto_base","overview"):
        bonus += 0.15
    if any(t in ("overview","alias") for t in tags):
        bonus += 0.15
    if "parlami" in norm(q) and (cat == "prodotto_base" or "overview" in tags):
        bonus += 0.2

    # Se i tag matchano termini domanda (taratura, preforo, lamiera 1,5 …)
    if any(t in tq for t in tags):
        bonus += 0.25

    # Micro-boost per contenuti operativi
    if any(k in tq for k in ["taratura","regolazione","preforo","verifica","potenza","propulsori","coppia"]):
        if cat in ("procedura","posa","uso","sicurezza"):
            bonus += 0.2

    return overlap + bonus

def semantic_pick(q: str, qa: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not qa:
        return None
    t = norm(q)
    if any(x in t for x in ("parlami","overview","che cos","cos e","cos'è","cos'e")):
        overviews = [it for it in qa if (it.get("category","").lower() in ("prodotto_base","overview")
                                         or "overview" in [str(tt).lower() for tt in it.get("tags",[] )])]
        if overviews:
            return max(overviews, key=lambda it: len(it.get("a","")))
    return max(qa, key=lambda it: score_item(q, it))

# ====== Narrativa ======
FAMILY_TITLES = {
    "ctf": "CTF – Connettori acciaio–calcestruzzo",
    "gts": "GTS – Manicotti filettati",
    "diapason": "DIAPASON – Rinforzo solai laterocemento",
    "ctl": "CTL – Sistema legno–calcestruzzo",
    "mini-cem-e": "MINI CEM-E – Camicie/Iniezioni",
    "spit-p560": "SPIT P560 – Chiodatrice per CTF",
}

def narrativize(answer: str, primary: str, intent: str) -> str:
    """Intro+outro Tecnaria (senza inventare)."""
    answer = (answer or "").strip()
    if not answer:
        return answer
    title = FAMILY_TITLES.get(primary, primary.upper() if primary else "Tecnaria")
    intro_map = {
        "explain": f"**{title}** — panoramica operativa:",
        "usage": f"**{title}** — indicazioni d’uso:",
        "compare": f"**{title}** — confronto sintetico:",
        "verify": f"**{title}** — controlli e verifiche:",
        "safety": f"**{title}** — sicurezza e DPI:",
    }
    intro = intro_map.get(intent, f"**{title}** — indicazioni Tecnaria:")
    outro = "\n\n*Riferimento: documentazione e schede ufficiali Tecnaria. In caso di dubbio, attenersi alle indicazioni del progettista strutturale.*"
    if "schede" in answer.lower() and "tecnaria" in answer.lower():
        outro = ""
    return f"{intro}\n\n{answer}{outro}"

# Patch “smart” per l’arricchimento locale
SPLIT_SENT_RE = re.compile(r'(?<=[\.\!\?])\s+')
def enrich_narrative(answer: str, primary: str, intent: str) -> str:
    """
    Arricchimento narrativo locale (deterministico, compatibile Render).
    - Niente "Inoltre," sulla prima frase o subito dopo "No."
    - Evita doppioni; normalizza "Inoltre, per..." minuscolo
    - Inserisce "in pratica," una volta dopo ';' se utile
    """
    if not answer or len(answer) < 80:
        return answer

    def tidy_spaces(s: str) -> str:
        s = re.sub(r'\s{2,}', ' ', s)
        s = re.sub(r'\s+([;:,])', r'\1', s)
        return s.strip()

    paragraphs = answer.split("\n\n")
    new_paragraphs = []
    for p in paragraphs:
        p = p.strip()
        if not p or p.startswith("**"):
            new_paragraphs.append(p)
            continue

        if "; " in p and "in pratica" not in p.lower():
            p = p.replace("; ", "; in pratica, ", 1)

        sentences = SPLIT_SENT_RE.split(p)
        if len(sentences) <= 1:
            new_paragraphs.append(tidy_spaces(p))
            continue

        rebuilt = []
        for i, s in enumerate(sentences):
            s_stripped = s.strip()
            if i == 0:
                rebuilt.append(s_stripped)
                continue

            prev = sentences[i-1].strip().lower()
            starts_bad = s_stripped.lower().startswith(("inoltre,", "in pratica,"))

            if not starts_bad:
                if not (prev in ("no.", "no", "non.") or len(prev) <= 3):
                    s_stripped = "Inoltre, " + s_stripped

            s_stripped = re.sub(r'Inoltre,\s+Per\b', 'Inoltre, per', s_stripped)
            rebuilt.append(s_stripped)

        np = " ".join(rebuilt)
        new_paragraphs.append(tidy_spaces(np))

    txt = "\n\n".join(new_paragraphs)
    tail = "\n\n_In sintesi, questa è la linea operativa coerente con le indicazioni Tecnaria._"
    if "linea operativa coerente" not in txt.lower():
        txt += tail
    return txt

def apply_narrative(answer: str, primary: str, intent: str) -> str:
    base = narrativize(answer, primary, intent)
    if os.getenv("ENRICH_NARRATIVE", "0") == "1":
        return enrich_narrative(base, primary, intent)
    return base

# ====== APP ======
app = FastAPI(title="Tecnaria Sinapsi", version="4.3")

@app.get("/health")
def health():
    datasets = {}
    for code in FAMILIES:
        existing = [str(p) for p in dataset_candidates_for_code(code) if p.exists()]
        raw = load_json(Path(existing[0])) if existing else {}
        err = raw.get("__error__") if isinstance(raw, dict) else None
        qa = extract_qa(raw)
        datasets[code] = {
            "used_path": existing[0] if existing else None,
            "qa_count": len(qa),
            "json_error": err,
        }
    return {
        "status": "ok",
        "router": True,
        "contacts": Path(CONTACTS_FILE).exists(),
        "datasets": datasets,
        "endpoints": {"ui": "/ui", "ask": "/ask?q=...", "debug": "/debug?q=...", "selfcheck": "/selfcheck"}
    }

@app.get("/selfcheck")
def selfcheck():
    checks = []
    probes = {
        "ctf": "Parlami dei connettori CTF",
        "gts": "Parlami del manicotto GTS",
        "diapason": "Parlami del sistema Diapason",
        "ctl": "Parlami del sistema CTL",
        "mini-cem-e": "Parlami del Mini-Cem-E",
        "spit-p560": "Parlami della SPIT P560",
    }
    for code in FAMILIES:
        qa, path, err = load_family_dataset(code)
        hit = semantic_pick(probes[code], qa) if qa else None
        checks.append({
            "family": code,
            "used_path": str(path) if path else None,
            "qa_count": len(qa),
            "probe_q": probes[code],
            "hit_q": (hit or {}).get("q"),
            "preview_a": ((hit or {}).get("a","")[:200] + ("…" if (hit and len(hit.get('a',''))>200) else "")) if hit else None
        })
    return {"status":"ok","checks":checks}

@app.get("/debug")
def debug(q: str = Query(..., description="Domanda per il debug")):
    insight = route_insight(q)
    primary = insight["primary"]
    qa, used, err = load_family_dataset(primary)
    hit = semantic_pick(q, qa) if qa else None

    p560_note = None
    if insight["needs_p560_for_ctf"]:
        qa_p, used_p, err_p = load_family_dataset("spit-p560")
        hit_p = semantic_pick(q, qa_p) if qa_p else None
        p560_note = {
            "used_path": str(used_p) if used_p else None,
            "hit_q": (hit_p or {}).get("q"),
            "preview_a": ((hit_p or {}).get("a","")[:200] + ("…" if (hit_p and len(hit_p.get('a',''))>200) else "")) if hit_p else None
        }

    return {
        "query": q,
        "insight": insight,
        "primary_used_path": str(used) if used else None,
        "primary_qa_count": len(qa),
        "json_error": err,
        "hit_q": (hit or {}).get("q"),
        "preview_a": ((hit or {}).get("a","")[:220] + ("…" if (hit and len(hit.get('a',''))>220) else "")) if hit else None,
        "p560_augmented": p560_note
    }

@app.get("/ask")
def ask(q: str):
    insight = route_insight(q)
    intent = insight["intent"]

    # Legno + CTF/P560 => negazione e pivot a CTL
    if insight.get("incompatible_ctf_on_wood"):
        qa_ctl, used_ctl, err_ctl = load_family_dataset("ctl")
        hit_ctl = semantic_pick("overview ctl legno calcestruzzo", qa_ctl) if qa_ctl else None
        ctl_line = ("\n\n**Alternativa corretta (CTL):**\n" + hit_ctl.get("a","")) if hit_ctl else ""
        answer = (
            "No. I connettori CTF e la chiodatrice SPIT P560 sono sistemi per acciaio–calcestruzzo; "
            "su travi o solai in legno non sono applicabili. "
            "Per solai lignei si utilizza il sistema CTL con viti strutturali e soletta collaborante."
            + ctl_line
        )
        return {"answer": apply_narrative(answer, "ctl", "usage")}

    # Confronto (due famiglie)
    if intent == "compare" and insight.get("compare_candidates"):
        candidates = insight["compare_candidates"][:2]
        qa_a, used_a, err_a = load_family_dataset(candidates[0]) if len(candidates) > 0 else ([], None, None)
        qa_b, used_b, err_b = load_family_dataset(candidates[1]) if len(candidates) > 1 else ([], None, None)
        hit_a = semantic_pick(q, qa_a) if qa_a else None
        hit_b = semantic_pick(q, qa_b) if qa_b else None

        parts = []
        if hit_a:
            parts.append(f"**{candidates[0].upper()} — Sintesi:**\n{hit_a.get('a','').strip()}")
        if hit_b:
            parts.append(f"**{candidates[1].upper()} — Sintesi:**\n{hit_b.get('a','').strip()}")
        compare_note = "\n\n**Confronto sintetico:** valuta azioni, costi di posa, attrezzature e vincoli; preferire la tecnologia che soddisfa i vincoli geometrici e di accesso in cantiere."
        return {"answer": apply_narrative("\n\n".join(parts) + compare_note, candidates[0], "compare")}

    # Flusso single-family
    primary = insight["primary"]
    qa, used, err = load_family_dataset(primary)
    if err:
        return {"answer": f"Dataset non disponibile per {primary}. Errore file: {err}"}
    if not qa:
        return {"answer": f"Nessuna base dati per {primary} (file: {used})."}

    hit = semantic_pick(q, qa)
    if not hit:
        return {"answer": "Non trovo una risposta precisa nei dati. Consulta le schede ufficiali Tecnaria."}

    answer = hit.get("a","").strip()

    # Cross-note: se è CTF e si parla di macchina/propulsori -> aggiungi nota P560
    if primary == "ctf" and insight["needs_p560_for_ctf"]:
        qa_p, used_p, err_p = load_family_dataset("spit-p560")
        if qa_p and not err_p:
            hit_p = semantic_pick(q, qa_p) or semantic_pick("P560 chiodatrice per CTF", qa_p)
            if hit_p and isinstance(hit_p.get("a"), str):
                p560_txt = hit_p["a"].strip()
                if p560_txt and p560_txt not in answer:
                    answer = (
                        answer
                        + "\n\n— **Nota P560 (obbligatoria per CTF)** —\n"
                        + p560_txt
                    )

    return {"answer": apply_narrative(answer, primary, intent)}

@app.get("/company")
def company():
    contacts = load_json(CONTACTS_FILE)
    bank = load_json(BANK_FILE)
    if isinstance(bank, dict) and "__error__" in bank:
        bank = {}
    return {"contacts": contacts, "bank": bank if isinstance(bank, dict) else {}}

# ====== UI (nera/arancione, bottone "Chiedi", disclaimer) ======
UI_HTML = """<!doctype html>
<html lang="it"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Tecnaria Sinapsi</title>
<link rel="icon" href="data:,">
<style>
:root { --orange:#f26522; --black:#111; --ink:#222; --muted:#666; --bg:#fafafa; }
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,sans-serif;background:linear-gradient(180deg,#111 0%,#111 40%,#f26522 100%);}
.container{max-width:1100px;margin:0 auto;padding:24px;}
.header{display:flex;align-items:center;gap:12px;color:#fff}
.logo{width:34px;height:34px;background:#000;border:2px solid #fff;border-radius:6px;display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff}
.h1{font-size:36px;font-weight:800;color:#fff;margin:12px 0 6px}
.sub{color:#ffd;opacity:.9;margin-bottom:18px}
.card{background:#fff;border-radius:16px;box-shadow:0 8px 24px rgba(0,0,0,.15);padding:18px}
.row{display:grid;grid-template-columns:1fr auto;gap:12px;margin:16px 0}
.input{width:100%;padding:14px 16px;border-radius:12px;border:1px solid #ddd;font-size:16px}
.btn{background:#000;color:#fff;border:0;border-radius:12px;padding:14px 18px;font-weight:700;cursor:pointer}
.btn:hover{opacity:.9}
.badges{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px}
.badge{background:rgba(255,255,255,.25);border:1px solid rgba(255,255,255,.4);color:#fff;border-radius:999px;padding:6px 10px;font-size:12px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}
.small{color:#777;font-size:13px}
.answer{white-space:pre-wrap;line-height:1.45}
.footer{color:#eee;padding:24px 0;text-align:center;font-size:12px}
.section{margin-top:14px}
.mono{font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace}
.disclaimer{margin-top:8px;color:#555;font-size:12px}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="logo">T</div>
    <div>
      <div class="h1">Tecnaria Sinapsi</div>
      <div class="sub">Risposte tecniche. Voce ufficiale Tecnaria.</div>
    </div>
  </div>

  <div class="card">
    <div class="row">
      <input id="q" class="input" placeholder="Scrivi qui la tua domanda (es. “Si può usare una qualsiasi chiodatrice per i CTF?”)"/>
      <button id="ask" class="btn">Chiedi</button>
    </div>
    <div class="disclaimer">Le risposte possono contenere inesattezze. Verifica informazioni importanti nelle schede ufficiali e con il progettista.</div>
    <div class="badges">
      <div class="badge">CTF</div><div class="badge">GTS</div><div class="badge">Diapason</div>
      <div class="badge">CTL</div><div class="badge">Mini-Cem-E</div><div class="badge">SPIT P560</div>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <div class="section"><b>Risposta</b></div>
      <div id="out" class="answer small">—</div>
    </div>
    <div class="card">
      <div class="section"><b>Impostazioni</b></div>
      <div class="kv small">Base URL: <code id="base" class="mono"></code></div>
      <div class="kv small" id="health">Stato: …</div>
      <div class="section"><b>Contatti & Dati aziendali</b></div>
      <div id="contacts" class="small">—</div>
    </div>
  </div>

  <div class="footer">© Tecnaria S.p.A. — Bassano del Grappa (VI)</div>
</div>
<script>
const BASE = location.origin;
document.getElementById('base').textContent = BASE;
async function ping(){
  try{
    const r = await fetch(BASE + "/health");
    const j = await r.json();
    const ds = j.datasets || {};
    document.getElementById('health').textContent =
      "CTF:"+(ds.ctf?.qa_count||0)+" | GTS:"+(ds.gts?.qa_count||0)+" | CTL:"+(ds["ctl"]?.qa_count||0)+" | P560:"+(ds["spit-p560"]?.qa_count||0);
    const c = await fetch(BASE + "/company"); const cj = await c.json();
    const ct = cj.contacts || {};
    const line = [ct.ragione_sociale, ct.indirizzo, ct.email].filter(Boolean).join(" — ");
    document.getElementById('contacts').textContent = line || "—";
  }catch(e){ document.getElementById('health').textContent="KO: "+e; }
}
async function ask(){
  const q = document.getElementById('q').value.trim();
  if(!q) return;
  const t0 = performance.now();
  const r = await fetch(BASE + "/ask?q=" + encodeURIComponent(q));
  const j = await r.json();
  const ms = Math.round(performance.now()-t0);
  document.getElementById('out').innerHTML = (j.answer||"—").replaceAll("\\n","<br/>");
  document.getElementById('out').innerHTML += "<br/><br/><span class='small'>⏱ "+ms+" ms</span>";
}
document.getElementById('ask').onclick = ask;
document.getElementById('q').addEventListener('keydown',e=>{if(e.key==='Enter') ask();});
ping();
</script>
</body></html>
"""

@app.get("/ui")
def ui():
    return HTMLResponse(UI_HTML)
