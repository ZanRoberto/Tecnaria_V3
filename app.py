# app.py  — Tecnaria_V3 (local-only, MICROPATCH)
# FastAPI backend per /api/ask che risponde usando i metadati in static/data/
# Patch: famiglie con sinonimi + comparativi (A vs B) + filtro semantico morbido
# Compatibile con stress test: restituisce ok, text, match_id, ms, score

from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Any, List, Tuple
from pathlib import Path
import json, csv, re, time, unicodedata

APP_NAME = "Tecnaria_V3 (local-only)"
DATA_DIR = Path(__file__).parent / "static" / "data"

# -------------------------
# Utilità
# -------------------------

def norm_txt(s: str) -> str:
    if not s: return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u2019","'").replace("\u2013","-").replace("\u2014","-")
    return s.strip()

def remove_diacritics(s: str) -> str:
    if not s: return ""
    nf = unicodedata.normalize("NFD", s)
    return "".join(c for c in nf if unicodedata.category(c) != "Mn")

def lower_noacc(s: str) -> str:
    return remove_diacritics(norm_txt(s)).lower()

def safe_json_load(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def load_all_json(data_dir: Path) -> Dict[str, Any]:
    bag: Dict[str, Any] = {}
    for p in sorted(data_dir.glob("*.json")):
        obj = safe_json_load(p)
        if obj is not None:
            bag[p.stem] = obj
    return bag

def load_faq_csv(path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str,str]] = []
    if not path.exists(): return rows
    try:
        with path.open("r", encoding="utf-8") as f:
            sniffer = csv.Sniffer()
            data = f.read()
            f.seek(0)
            dialect = sniffer.sniff(data.splitlines()[0] + "\n") if data else csv.excel
            f.seek(0)
            reader = csv.DictReader(f, dialect=dialect)
            for r in reader:
                rows.append({k.strip(): (v or "").strip() for k, v in r.items()})
    except Exception:
        # fallback semplice
        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append({k.strip(): (v or "").strip() for k, v in r.items()})
    return rows

# -------------------------
# Config dominio Tecnaria
# -------------------------
FAMS = ["CTF","CTL","VCEM","CEM-E","CTCEM","GTS","P560"]

TOKENS = {
    "CTF": ["ctf","lamiera","trave","p560","hsbr14","propulsori","sparare"],
    "CTL": ["ctl","legno","soletta","calcestruzzo","collaborazione"],
    "VCEM":["vcem","vite","preforo","legno","hardwood","70-80%"],
    "CEM-E":["ceme","laterocemento","secco","senza resine"],
    "CTCEM":["ctcem","laterocemento","secco","senza resine"],
    "GTS":["gts","manicotto","filettato","giunzioni","secco"],
    "P560":["p560","chiodatrice","ctf","hsbr14","propulsori"]
}

# NEW: sinonimi famiglie (per citazioni libere nelle query)
FAMILY_SYNONYMS: Dict[str, set] = {
    "CTF": {"CTF"},
    "CTL": {"CTL"},
    "VCEM": {"VCEM"},
    "CEM-E": {"CEM-E","CEME","CEM E"},
    "CTCEM": {"CTCEM","CT-CEM"},
    "GTS": {"GTS","TRALICCIO","TRALICCIATI","GIUNTO TRALICCIO"},
    "P560": {"P560","SPIT P560","SPIT"}
}

COMPARE_HINTS = {" vs ","vs","contro","differenza","differenze","meglio","oppure"," o ","paragone","confronto"}

# Regole sintetiche (failsafe)
DOMAIN_RULES = {
    "CTF_CHIODATRICE": {
        "pattern": r"\b(ctf).*?(chiodatrice|sparo|pistola)\b",
        "answer": {
            "it": ("No. Per i CTF Tecnaria è ammessa **solo** la chiodatrice **SPIT P560** "
                   "con **kit/adattatori Tecnaria**. Ogni connettore va posato con **2 chiodi HSBR14**. "
                   "I **propulsori P560** si scelgono in base a **trave** e **lamiera**."),
            "en": ("No. For Tecnaria CTF connectors you must use the **SPIT P560** nailer "
                   "with **Tecnaria adapters** only. Each connector requires **2 HSBR14 nails**. "
                   "**P560 cartridges** must be selected based on **beam** and **deck**."),
            "fr": ("Non. Pour les CTF Tecnaria on utilise **uniquement** la cloueuse **SPIT P560** "
                   "avec **adaptateurs Tecnaria**. Chaque connecteur: **2 clous HSBR14**. "
                   "Choisir les **charges P560** selon **poutre** et **tôle**."),
            "es": ("No. Para los CTF de Tecnaria se usa **solo** la clavadora **SPIT P560** "
                   "con **adaptadores Tecnaria**. Cada conector: **2 clavos HSBR14**. "
                   "Elegir los **propulsores P560** según **viga** y **chapa**."),
            "de": ("Nein. Für CTF von Tecnaria ist **nur** der **SPIT P560** mit "
                   "**Tecnaria-Adaptern** zugelassen. Je Verbinder **2 HSBR14-Nägel**. "
                   "**P560-Antriebe** je nach **Träger** und **Trapezblech** wählen.")
        },
        "match_id": "CTF-POSA-0002"
    },
    "VCEM_PREFORO": {
        "pattern": r"\b(vcem).*(preforo|pre[- ]?fori|pilot|taladro)\b|\b(70)\s*[–-]?\s*(80)\s*%",
        "answer": {
            "it": ("Su essenze dure è raccomandato **preforo pari al 70–80%** del diametro vite. "
                   "Riduce fessurazioni e consente coppia di serraggio corretta."),
            "en": ("On hardwoods, use a **pilot hole of 70–80%** of screw diameter. "
                   "This limits splitting and ensures correct torque."),
            "fr": ("Sur bois durs: **avant-trou 70–80%** du diamètre de la vis. "
                   "Réduit les fissures et garantit le couple."),
            "es": ("En maderas duras: **pre-taladro 70–80%** del diámetro del tornillo. "
                   "Evita fisuras y asegura el par de apriete."),
            "de": ("Bei Hartholz: **Vorbohren 70–80%** des Schraubendurchmessers. "
                   "Verringert Risse und sichert das Anzugsmoment.")
        },
        "match_id": "VCEM-Q-HARDWOOD-PREFORO"
    },
    "CEME_SECCO": {
        "pattern": r"\b(cem[- ]?e|ctcem)\b.*\b(secco|resine|senza)\b",
        "answer": {
            "it": ("**CEM-E/CTCEM**: sistema **a secco**, **senza resine**, pensato per **laterocemento**."),
            "en": ("**CEM-E/CTCEM**: **dry system**, **no resins**, for **hollow-block concrete slabs**."),
            "fr": ("**CEM-E/CTCEM** : système **à sec**, **sans résines**, pour **planchers hourdis**."),
            "es": ("**CEM-E/CTCEM**: sistema **en seco**, **sin resinas**, para **forjados de bovedillas**."),
            "de": ("**CEM-E/CTCEM**: **Trockenmontage**, **ohne Harze**, für **Hohlkörperdecken**.")
        },
        "match_id": "CEME-CODICI-0001"
    },
    "GTS_OV": {
        "pattern": r"\b(gts)\b",
        "answer": {
            "it": ("**GTS**: manicotto metallico **filettato** per **giunzioni meccaniche a secco**; "
                   "collegamenti **acciaio–acciaio**, **acciaio–legno**, **legno–legno**."),
            "en": ("**GTS**: threaded **sleeve** for **dry mechanical joints**; steel-steel, steel-timber, timber-timber."),
            "fr": ("**GTS** : **manchon fileté** pour **liaisons mécaniques à sec** ; acier-acier, acier-bois, bois-bois."),
            "es": ("**GTS**: **manguito roscado** para **uniones mecánicas en seco**; acero-acero, acero-madera, madera-madera."),
            "de": ("**GTS**: **Gewindemuffe** für **trockene mechanische Verbindungen**; Stahl-Stahl, Stahl-Holz, Holz-Holz.")
        },
        "match_id": "tecnaria_gts_qa500.json::overview"
    }
}

# -------------------------
# Lingua
# -------------------------
LANGS = ["it","en","fr","es","de"]

def detect_lang(q: str) -> str:
    ql = lower_noacc(q)
    markers = [
        ("it", ["cosa","qual","posare","lamiera","trave","soletta","calcestruzzo"]),
        ("en", ["what","how","can i","deck","beam"]),
        ("fr", ["quoi","comment","peut-on","béton","dalle"]),
        ("es", ["que","como","puedo","viga","losa"]),
        ("de", ["was","wie","darf","trapezblech","träger"]) 
    ]
    for lang, keys in markers:
        for k in keys:
            if k in ql: return lang
    return "it"

# -------------------------
# Intent routing
# -------------------------
INTENT_PATTERNS = [
    ("compare", r"\b(vs|contro|confronto|parag(o|ó)n|vergleich)\b"),
    ("checklist", r"\b(checklist|check list|lista|steps|fasi|stufen)\b"),
    ("errors", r"\b(errori|evitare|pitfalls|erreurs|errores|fehler)\b"),
    ("controls", r"\b(cosa controllare|controlli|precheck|verifiche)\b"),
    ("overview", r"\b(overview|introduzione|cos'?è|che cosa|spiega|descrivi)\b"),
    ("spec", r".*")  # fallback
]

def detect_intent(q: str) -> str:
    ql = lower_noacc(q)
    for name, pat in INTENT_PATTERNS:
        if re.search(pat, ql):
            return name
    return "spec"

# SINGLE family (legacy)
def detect_family(q: str) -> str:
    ql = lower_noacc(q)
    for fam in FAMS:
        if fam.lower() in ql:
            return fam
    for fam, toks in TOKENS.items():
        if any(t in ql for t in toks):
            return fam
    return ""

# NEW: MULTI famiglia (per confronti)
def detect_families(q: str) -> List[str]:
    qU = lower_noacc(q).upper()
    out: List[str] = []
    # match diretti sui sinonimi
    for fam, syns in FAMILY_SYNONYMS.items():
        if any(s in qU for s in syns):
            out.append(fam)
    # fallback: token
    if not out:
        for fam, toks in TOKENS.items():
            if any(t.upper() in qU for t in toks):
                out.append(fam)
    # dedup preservando ordine
    seen, ret = set(), []
    for f in out:
        if f not in seen:
            ret.append(f); seen.add(f)
    return ret

# -------------------------
# Motore di risposta
# -------------------------
class AskIn(BaseModel):
    q: str

def score_tokens(text: str, expected: List[str]) -> float:
    tl = lower_noacc(text)
    if not expected: return 0.0
    hits = sum(1 for t in expected if t in tl)
    return hits / len(expected)

def pick_faq_entry(faq: List[Dict[str,str]], q: str, fam: str, intent: str, lang: str) -> Tuple[Dict[str,str], float]:
    best = None
    best_score = 0.0
    ql = lower_noacc(q)
    for r in faq:
        qx = lower_noacc(r.get("q",""))
        famx = r.get("fam","").upper().strip()
        intentx = r.get("intent","").lower().strip()
        langx = (r.get("lang","it").strip() or "it").lower()
        score = 0.0
        if fam and famx == fam: score += 0.4
        if intent and intentx == intent: score += 0.3
        toks = re.findall(r"[a-z0-9]+", ql)
        score += 0.3 * sum(1 for t in toks if t and t in qx) / (len(toks) or 1)
        if langx == lang: score += 0.05
        if score > best_score:
            best_score = score; best = r
    return (best or {}), best_score

def compose_from_rule(q: str, fam: str, lang: str) -> Tuple[str,str]:
    ql = lower_noacc(q)
    for key, rule in DOMAIN_RULES.items():
        if re.search(rule["pattern"], ql):
            ans = rule["answer"].get(lang) or rule["answer"]["it"]
            return ans, rule["match_id"]
    return "", ""

def compose_from_json(bag: Dict[str, Any], fam: str, intent: str, lang: str) -> Tuple[str,str]:
    pri = []
    for name, obj in bag.items():
        name_l = name.lower()
        if fam and fam.lower() in name_l: pri.append((name, obj))
    if not pri:
        for name, obj in bag.items():
            if any(k in name.lower() for k in ["tecnaria","qa","catalogo","codici","rules","critici","overview","compare"]):
                pri.append((name, obj))
    for name, obj in pri:
        try:
            if isinstance(obj, dict):
                if "overview" in obj:
                    txt = obj["overview"].get(lang) or obj["overview"].get("it") or ""
                    if txt and intent in ["overview","spec"]:
                        return norm_txt(txt), f"{name}::overview"
                if "faq" in obj and isinstance(obj["faq"], list):
                    for it in obj["faq"]:
                        ff = (it.get("fam","") or "").upper()
                        ii = (it.get("intent","") or "").lower()
                        ll = (it.get("lang","it") or "it").lower()
                        if (not fam or ff == fam) and (not intent or ii == intent):
                            txt = it.get("a","") if lang==ll else it.get(f"a_{lang}","")
                            if not txt: txt = it.get("a","")
                            if txt:
                                return norm_txt(txt), f"{name}::faq::{ff}::{ii}"
            if isinstance(obj, list):
                for it in obj:
                    qx = lower_noacc(it.get("q",""))
                    ax = it.get("a","")
                    ff = (it.get("fam","") or "").upper()
                    if (fam and ff==fam) or (fam and fam.lower() in qx):
                        if ax:
                            return norm_txt(ax), f"{name}::list"
        except Exception:
            continue
    return "", ""

def enrich_technical(text: str, fam: str, intent: str, lang: str) -> str:
    if not text: return text
    # niente arricchimento per comparativi (gestito a parte)
    if intent == "compare":
        return text
    tokens = TOKENS.get(fam, [])
    need = [t for t in tokens if t not in lower_noacc(text)]
    addon = ""
    if need:
        if lang == "it":
            addon = "\n\n— Note rapide: " + "; ".join(need[:3])
        elif lang == "en":
            addon = "\n\n— Quick notes: " + "; ".join(need[:3])
        elif lang == "fr":
            addon = "\n\n— Notes rapides : " + "; ".join(need[:3])
        elif lang == "es":
            addon = "\n\n— Notas rápidas: " + "; ".join(need[:3])
        else:
            addon = "\n\n— Kurzhinweise: " + "; ".join(need[:3])
    tail = {
        "it": "\n\nConsultare sempre progetto/documentazione Tecnaria per il caso specifico.",
        "en": "\n\nAlways check project/official Tecnaria documentation for the specific case.",
        "fr": "\n\nToujours vérifier projet/documentation Tecnaria pour le cas spécifique.",
        "es": "\n\nRevisar siempre el proyecto/documentación Tecnaria para el caso específico.",
        "de": "\n\nBitte stets Projekt/Technaria-Unterlagen für den Einzelfall prüfen."
    }[lang]
    return text + addon + tail

# -------------------------
# App FastAPI
# -------------------------
app = FastAPI(title=APP_NAME)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Carica dataset all’avvio
JSON_BAG = load_all_json(DATA_DIR)
FAQ_ROWS = load_faq_csv(DATA_DIR / "faq.csv")

@app.post("/api/ask")
def api_ask(payload: AskIn = Body(...)):
    t0 = time.perf_counter()
    q = norm_txt(payload.q or "")
    lang = detect_lang(q)
    fam  = detect_family(q)
    intent = detect_intent(q)

    # NEW: intercetta confronto A vs B prima di tutto
    fams = detect_families(q)
    q_spaced = f" {lower_noacc(q)} "
    is_compare_q = any(h in q_spaced for h in COMPARE_HINTS) and len(set(fams)) >= 2

    if is_compare_q:
        L, R = list(dict.fromkeys(fams))[:2]  # prime due famiglie distinte
        # prova a prendere una descrizione da JSON per ciascun lato
        def side_best(fam_side: str) -> Tuple[str,str]:
            txt, mid = compose_from_json(JSON_BAG, fam_side, "overview", lang)
            if not txt:
                # seconda chance: regole
                t2, m2 = compose_from_rule(q + " " + fam_side, fam_side, lang)
                if t2: txt, mid = t2, m2
            if not txt:
                # fallback stringa sintetica
                txt = {
                    "it": f"{fam_side}: criteri, ambiti applicativi, posa, controlli.",
                    "en": f"{fam_side}: criteria, use cases, installation, checks."
                }.get(lang, f"{fam_side}: overview.")
                mid = f"OVERVIEW::{fam_side}"
            return txt, mid

        Ltxt, Lmid = side_best(L)
        Rtxt, Rmid = side_best(R)
        html = ("<div><h2>Confronto</h2><div style='display:flex;gap:24px;flex-wrap:wrap'>"
                f"<div class='side' style='flex:1;min-width:320px'><h3>{L}</h3><p>{Ltxt}</p>"
                f"<p><small>Fonte: <b>{Lmid}</b></small></p></div>"
                f"<div class='side' style='flex:1;min-width:320px'><h3>{R}</h3><p>{Rtxt}</p>"
                f"<p><small>Fonte: <b>{Rmid}</b></small></p></div>"
                "</div></div>")
        ms = int(round((time.perf_counter() - t0)*1000))
        return JSONResponse({
            "ok": True,
            "text": "",  # per retrocompatibilità con client che si aspettano text
            "html": html,
            "lang": lang,
            "family": f"{L}+{R}",
            "intent": "compare",
            "match_id": f"COMPARE::{L}_VS_{R}",
            "source": "compare",
            "score": 92.0,
            "ms": ms
        })

    # 1) regole sintetiche
    txt, mid = compose_from_rule(q, fam, lang)
    source = "rules"
    score = 0.0

    # 2) JSON locali
    if not txt:
        txt, mid = compose_from_json(JSON_BAG, fam, intent, lang)
        source = "json"

    # 3) FAQ CSV
    if not txt and FAQ_ROWS:
        row, s = pick_faq_entry(FAQ_ROWS, q, fam, intent, lang)
        if row:
            txt = row.get(f"a_{lang}") or row.get("a_it") or row.get("a","")
            mid = row.get("match_id") or f"faq::{row.get('fam','')}::{row.get('intent','')}"
            score = s
            source = "faq"

    # 4) fallback neutro
    if not txt:
        neutral = {
            "it": "Richiesta ricevuta. Per indicazioni tecniche puntuali servono famiglia/prodotto e contesto (materiale base, spessori, posa).",
            "en": "Request received. To provide precise technical guidance we need family/product and context (base material, thicknesses, installation).",
            "fr": "Reçu. Pour une réponse technique précise, indiquez famille/produit et contexte (support, épaisseurs, pose).",
            "es": "Recibido. Para una guía técnica precisa, indique familia/producto y contexto (soporte, espesores, instalación).",
            "de": "Erhalten. Für eine präzise Auskunft benötigen wir Familie/Produkt und Kontext (Untergrund, Dicken, Montage).",
        }[lang]
        txt = neutral
        mid = "FALLBACK::NEUTRAL"
        source = "fallback"

    # 5) arricchimento tecnico
    txt = enrich_technical(txt, fam or "", intent, lang)

    ms = int(round((time.perf_counter() - t0)*1000))
    score_tokens_list = TOKENS.get(fam or "", [])
    token_score = score_tokens(txt, score_tokens_list) if score_tokens_list else 0.0
    overall = round(max(score, token_score) * 100, 2) if (score>0 or token_score>0) else 80.0

    return {
        "ok": True,
        "text": txt,
        "lang": lang,
        "family": fam,
        "intent": intent,
        "match_id": mid,
        "source": source,
        "score": overall,
        "ms": ms
    }

@app.get("/")
def root():
    return {"app": APP_NAME, "status": "ok", "data_dir": str(DATA_DIR), "json_loaded": list(JSON_BAG.keys()), "faq_rows": len(FAQ_ROWS)}
