from fastapi import FastAPI

# --- FIX CRITICO: istanzia sempre l'app all'import ---
try:
    app  # se già definita, non fare nulla
except NameError:
    app = FastAPI(title="Tecnaria_V3")
# ------------------------------------------------------
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
    # FIX GTS: rimosso 'traliccio/tralicciati/giunto traliccio', aggiunti termini corretti
    "GTS":["gts","manicotto","manicotti","filettato","giunzioni","raccordo","sleeve","coupler","barre filettate"],
    "P560":["p560","chiodatrice","ctf","hsbr14","propulsori"]
}

# NEW: sinonimi famiglie (per citazioni libere nelle query)
FAMILY_SYNONYMS: Dict[str, set] = {
    "CTF": {"CTF"},
    "CTL": {"CTL"},
    "VCEM": {"VCEM"},
    "CEM-E": {"CEM-E","CEME","CEM E"},
    "CTCEM": {"CTCEM","CT-CEM"},
    # FIX GTS: rimosso 'TRALICCIO/...' e sostituito con sinonimi pertinenti
    "GTS": {"GTS","MANICOTTO","MANICOTTI","GIUNZIONE FILETTATA","RACCORDO FILETTATO","SLEEVE","COUPLER","BAR COUPLER"},
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
        intent
