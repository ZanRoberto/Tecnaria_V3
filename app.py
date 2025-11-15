import os
import json
import glob
import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # gestito a runtime

# -----------------------------------------------------------------------------
# CONFIGURAZIONE DI BASE
# -----------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tecnaria-imbuto")

APP_NAME = "TECNARIA-IMBUTO"
DATA_DIR = "static/data"
MODEL_CHAT_DEFAULT = "gpt-4.1-mini"  # usa quello che hai su Render

# Modalità A4 (aggressiva)
IMBUTO_STAGES = ["top", "middle", "bottom", "post"]

# -----------------------------------------------------------------------------
# STRUTTURE DATI
# -----------------------------------------------------------------------------

@dataclass
class KBItem:
    id: str
    family: str
    lang: str
    question: str
    answer: str
    mode: str
    tags: List[str]
    raw: Dict[str, Any]


@dataclass
class FamilyScore:
    family: str
    score: float
    reasons: List[str]


# -----------------------------------------------------------------------------
# CARICAMENTO KNOWLEDGE BASE (GOLD)
# -----------------------------------------------------------------------------

KB_ITEMS: List[KBItem] = []
KB_BY_FAMILY_LANG: Dict[str, List[KBItem]] = {}
FAMILY_FILES: List[str] = []
CONSOLIDATO_LOADED = False

def _safe_get(d: Dict[str, Any], key: str, default: Any = "") -> Any:
    return d.get(key, default)

def _load_json_items(path: str) -> List[KBItem]:
    """Carica un singolo file JSON (lista di blocchi GOLD). Ignora errori singoli, non manda in crash l'app."""
    items: List[KBItem] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"[KB] Errore JSON nel file {path}: {e}")
        return items
    except FileNotFoundError:
        logger.warning(f"[KB] File non trovato: {path}")
        return items

    if not isinstance(data, list):
        logger.warning(f"[KB] File {path} non contiene una lista JSON, ignorato.")
        return items

    for obj in data:
        if not isinstance(obj, dict):
            continue
        _id = str(_safe_get(obj, "id", "")).strip()
        _family = str(_safe_get(obj, "family", "")).strip()
        _lang = str(_safe_get(obj, "lang", "it")).strip() or "it"
        _question = str(_safe_get(obj, "question", "")).strip()
        _answer = str(_safe_get(obj, "answer", "")).strip()
        _mode = str(_safe_get(obj, "mode", "gold")).strip().lower()
        _tags = _safe_get(obj, "tags", [])
        if not isinstance(_tags, list):
            _tags = []

        if not _id or not _family or not _answer:
            # blocco incompleto, ignoriamo
            continue

        item = KBItem(
            id=_id,
            family=_family.upper(),
            lang=_lang.lower(),
            question=_question,
            answer=_answer,
            mode=_mode,
            tags=[str(t).lower() for t in _tags],
            raw=obj,
        )
        items.append(item)

    logger.info(f"[KB] Caricati {len(items)} blocchi da {path}")
    return items

def load_kb() -> None:
    """Carica tutte le famiglie GOLD e popola le strutture globali."""
    global KB_ITEMS, KB_BY_FAMILY_LANG, FAMILY_FILES, CONSOLIDATO_LOADED

    KB_ITEMS = []
    KB_BY_FAMILY_LANG = {}
    FAMILY_FILES = []
    CONSOLIDATO_LOADED = False

    if not os.path.isdir(DATA_DIR):
        logger.warning(f"[KB] Data dir non trovata: {DATA_DIR}")
        return

    # Carichiamo tutti i JSON di famiglia
    patterns = [
        os.path.join(DATA_DIR, "*.json"),
    ]
    seen_files = set()
    for pattern in patterns:
        for path in glob.glob(pattern):
            name = os.path.basename(path)
            if name.lower() in {"consolidato.json", "tecnaria_gold.json"}:
                continue
            if path in seen_files:
                continue
            seen_files.add(path)
            FAMILY_FILES.append(name)
            items = _load_json_items(path)
            for it in items:
                KB_ITEMS.append(it)
                key = f"{it.family}:{it.lang}"
                KB_BY_FAMILY_LANG.setdefault(key, []).append(it)

    # Eventuale consolidato
    consolidato_path = os.path.join(DATA_DIR, "consolidato.json")
    if os.path.exists(consolidato_path):
        consolidato_items = _load_json_items(consolidato_path)
        for it in consolidato_items:
            KB_ITEMS.append(it)
            key = f"{it.family}:{it.lang}"
            KB_BY_FAMILY_LANG.setdefault(key, []).append(it)
        CONSOLIDATO_LOADED = True

    logger.info(
        f"[IMBUTO] Caricati {len(KB_ITEMS)} blocchi GOLD (famiglie + consolidato se presente)."
    )

# -----------------------------------------------------------------------------
# CLIENT OPENAI (SE DISPONIBILE)
# -----------------------------------------------------------------------------

def get_openai_client() -> Optional["OpenAI"]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or OpenAI is None:
        return None
    try:
        client = OpenAI(api_key=api_key)
        return client
    except Exception as e:
        logger.error(f"[OPENAI] Impossibile inizializzare il client: {e}")
        return None

CLIENT = get_openai_client()
MODEL_CHAT = os.getenv("MODEL_CHAT", MODEL_CHAT_DEFAULT)

def call_chat(messages: List[Dict[str, str]], max_tokens: int = 256) -> Optional[str]:
    """Chiamata LLM robusta: se fallisce, restituisce None, non blocca il sistema."""
    if CLIENT is None:
        return None
    try:
        resp = CLIENT.chat.completions.create(
            model=MODEL_CHAT,
            messages=messages,
            max_tokens=max_tokens,
        )
        txt = resp.choices[0].message.content
        return txt
    except Exception as e:
        logger.error(f"[OPENAI] Errore chiamata chat: {e}")
        return None

# -----------------------------------------------------------------------------
# TEC-ENGINE A4: RICONOSCIMENTO FAMIGLIA & INTENT
# -----------------------------------------------------------------------------

FAMILY_RULES = {
    "CTF_SYSTEM": {
        "keywords": [
            "ctf",
            "connettore ctf",
            "ctf system",
            "ctf_system",
            "ctf system tecnaria",
            "p560",
            "p 560",
            "chiodatrice",
            "pistola",
            "utensile a sparo",
            "sparachiodi",
            "solaio su lamiera",
            "lamiera grecata",
            "lamiera collaborante",
            "lamiera trapezoidale",
        ],
        "signals": [
            "grecata",
            "trapezoidale",
            "lamiera",
            "sparare",
            "sparo",
            "colpo",
            "rimbalza",
            "rebound",
            "chiodo non entra",
            "chiodi non entrano",
            "non entra il chiodo",
            "chiodo",
            "chiodi",
        ],
    },
    "CTL": {
        "keywords": [
            "ctl",
            "connettore legno calcestruzzo",
            "solaio misto legno calcestruzzo",
        ],
        "signals": [
            "travi in legno",
            "soletta in calcestruzzo",
            "solaio in legno",
            "legno vecchio",
            "legno umido",
        ],
    },
    "CTL_MAXI": {
        "keywords": [
            "ctl maxi",
            "ctlmaxi",
        ],
        "signals": [
            "travi grandi",
            "maggiore capacità",
            "prestazioni elevate",
            "solaio importante",
        ],
    },
    "DIAPASON": {
        "keywords": [
            "diapason",
            "solaio laterocemento",
            "laterocemento",
        ],
        "signals": [
            "soletta galleggiante",
            "pignatte",
            "travetti",
            "solaio anni",
            "solaio vecchio in laterocemento",
        ],
    },
    "VCEM": {
        "keywords": [
            "vcem",
            "resina",
            "cartuccia",
            "iniezione",
            "chimico",
        ],
        "signals": [
            "ancoraggio chimico",
            "barra chimica",
            "resina epossidica",
        ],
    },
    "CTCEM": {
        "keywords": [
            "ctcem",
        ],
        "signals": [],
    },
    "COMM": {
        "keywords": [
            "prezzo",
            "costo",
            "codice",
            "listino",
            "listini",
            "ordine",
            "ordinare",
        ],
        "signals": [
            "quanto costa",
            "preventivo",
            "tempistiche",
            "consegna",
        ],
    },
}

def detect_families(question: str) -> List[FamilyScore]:
    q = question.lower()
    scores: Dict[str, FamilyScore] = {}

    for fam, rules in FAMILY_RULES.items():
        score = 0.0
        reasons: List[str] = []
        for kw in rules["keywords"]:
            if kw in q:
                score += 3.0
                reasons.append(f"keyword:{kw}")
        for sig in rules["signals"]:
            if sig in q:
                score += 1.5
                reasons.append(f"signal:{sig}")
        if score > 0:
            scores[fam] = FamilyScore(family=fam, score=score, reasons=reasons)

    # Cross hard rules (A4 aggressivo)
    q_has_lamiera = ("lamiera" in q) or ("grecata" in q)
    q_has_chiodi = ("chiodo" in q) or ("chiodi" in q)
    q_has_sparo = ("sparo" in q) or ("sparare" in q) or ("colpo" in q)

    # Tutto il mondo lamiera/chiodi/sparo → CTF_SYSTEM
    if (q_has_lamiera or q_has_chiodi or q_has_sparo) and "CTF_SYSTEM" not in scores:
        scores["CTF_SYSTEM"] = FamilyScore("CTF_SYSTEM", 4.0, ["lamiera/chiodi/sparo→CTF_SYSTEM"])

    # Resina → VCEM
    if ("resina" in q or "chimico" in q or "cartuccia" in q) and "VCEM" not in scores:
        scores["VCEM"] = FamilyScore("VCEM", 2.5, ["resina→VCEM"])

    # Solaio legno + cls → CTL
    if ("solaio in legno" in q or "travi in legno" in q) and ("soletta" in q or "calcestruzzo" in q) and "CTL" not in scores:
        scores["CTL"] = FamilyScore("CTL", 2.5, ["legno+cls→CTL"])

    # Laterocemento → DIAPASON
    if ("laterocemento" in q or "travetti" in q or "pignatte" in q) and "DIAPASON" not in scores:
        scores["DIAPASON"] = FamilyScore("DIAPASON", 2.5, ["laterocemento→DIAPASON"])

    # Ordina per score decrescente
    ordered = sorted(scores.values(), key=lambda x: x.score, reverse=True)
    return ordered

def detect_intent(question: str) -> str:
    q = question.lower()

    if any(x in q for x in ["quanto costa", "prezzo", "costo", "listino", "codice", "preventivo", "consegna"]):
        return "pricing"
    if any(x in q for x in ["parlami", "cos'è", "cosa è", "che cos", "me ne puoi parlare", "mi puoi parlare"]):
        return "general"
    if any(x in q for x in ["dove si usa", "quando si usa", "in quali casi", "per quali solai", "per quali situazioni"]):
        return "use_case"
    if any(x in q for x in ["come si posa", "posa", "mettere", "fissare", "installare", "passo", "interasse", "passi", "di quanti cm"]):
        return "installation"
    if any(x in q for x in ["non funziona", "problema", "difetto", "non entra", "rimbalza", "non tiene", "si stacca", "non spara", "si inceppa"]):
        return "defect"
    if any(x in q for x in ["sicurezza", "dpi", "pericol", "infortunio", "rumore", "esplosione"]):
        return "safety"
    if any(x in q for x in ["posso usare", "compatibile", "insieme a", "contemporaneamente", "alternativa", "in alternativa", "si può usare con"]):
        return "compatibility"
    if any(x in q for x in ["norma", "eta", "ntc", "eurocodice", "ec2", "ec5", "certificato", "certificazione"]):
        return "regulation"
    if any(x in q for x in ["differenza", "meglio", "vs", "confronto", "pro e contro", "qual è meglio"]):
        return "comparison"

    return "general"

# -----------------------------------------------------------------------------
# DISPATCH TABLE: FAMILY + INTENT → ID BLOCCO GOLD
# -----------------------------------------------------------------------------
# Nota: per CTF_SYSTEM usiamo solo best-match sui blocchi,
# quindi gli ID qui sono tutti None e l'engine passa subito al match testuale.

CTF_SYSTEM_DISPATCH = {
    "general": None,
    "use_case": None,
    "installation": None,
    "design": None,
    "defect": None,
    "safety": None,
    "compatibility": None,
    "*": None,
}

CTL_DISPATCH = {
    "general": "CTL-0001",
    "use_case": "CTL-0005",
    "installation": "CTL-0010",
    "design": "CTL-0020",
    "defect": "CTL-0030",
    "*": "CTL-0001",
}

CTL_MAXI_DISPATCH = {
    "general": "CTL_MAXI-0001",
    "use_case": "CTL_MAXI-0005",
    "installation": "CTL_MAXI-0010",
    "design": "CTL_MAXI-0020",
    "*": "CTL_MAXI-0001",
}

DIAPASON_DISPATCH = {
    "general": "DIAPASON-0001",
    "use_case": "DIAPASON-0005",
    "installation": "DIAPASON-0010",
    "design": "DIAPASON-0020",
    "*": "DIAPASON-0001",
}

VCEM_DISPATCH = {
    "general": "VCEM-0001",
    "use_case": "VCEM-0005",
    "installation": "VCEM-0010",
    "design": "VCEM-0020",
    "compatibility": "VCEM-0030",
    "*": "VCEM-0001",
}

CTCEM_DISPATCH = {
    "general": "CTCEM-0001",
    "*": "CTCEM-0001",
}

COMM_DISPATCH = {
    "pricing": "COMM-0001",
    "general": "COMM-0002",
    "*": "COMM-0002",
}

DISPATCH_TABLE = {
    "CTF_SYSTEM": CTF_SYSTEM_DISPATCH,
    "CTL": CTL_DISPATCH,
    "CTL_MAXI": CTL_MAXI_DISPATCH,
    "DIAPASON": DIAPASON_DISPATCH,
    "VCEM": VCEM_DISPATCH,
    "CTCEM": CTCEM_DISPATCH,
    "COMM": COMM_DISPATCH,
}

def dispatch_block_id(family: str, intent: str) -> Optional[str]:
    fam = family.upper()
    table = DISPATCH_TABLE.get(fam)
    if not table:
        return None
    return table.get(intent) or table.get("*")

# -----------------------------------------------------------------------------
# SELEZIONE BLOCCO DAL KB
# -----------------------------------------------------------------------------

def get_kb_item_by_id(block_id: str, lang: str = "it") -> Optional[KBItem]:
    if block_id is None:
        return None
    block_id = block_id.strip()
    if not block_id:
        return None
    lang = lang.lower()
    for it in KB_ITEMS:
        if it.id == block_id and it.lang == lang:
            return it
    # fallback: ignora lang
    for it in KB_ITEMS:
        if it.id == block_id:
            return it
    return None


def simple_best_match_by_family(question: str, family: str, lang: str = "it") -> Optional[KBItem]:
    """
    TEC_SYSTEM A4+ semantic matching
    """
    import difflib

    q = question.lower().strip()
    q_tokens = set(q.split())

    # collect candidates by family/lang
    key = f"{family.upper()}:{lang.lower()}"
    candidates = KB_BY_FAMILY_LANG.get(key, [])
    if not candidates:
        for k, v in KB_BY_FAMILY_LANG.items():
            fam, tlang = k.split(":")
            if fam == family.upper():
                candidates.extend(v)

    if not candidates:
        return None

    best_score = -999
    best_item = None

    for it in candidates:
        score = 0
        it_q = it.question.lower().strip()
        it_tokens = set(it_q.split())

        # triggers
        for trg in it.raw.get("triggers", []) or []:
            trg_l = trg.lower().strip()
            if trg_l in q:
                score += 40
            ratio = difflib.SequenceMatcher(None, trg_l, q).ratio()
            if ratio > 0.75:
                score += 25

        # tags
        for tag in it.tags:
            tag_l = tag.lower()
            if tag_l in q:
                score += 30
            if tag_l in q_tokens:
                score += 20
            ratio = difflib.SequenceMatcher(None, tag_l, q).ratio()
            if ratio > 0.70:
                score += 12

        # question similarity
        ratio_q = difflib.SequenceMatcher(None, it_q, q).ratio()
        score += ratio_q * 15
        common = len(q_tokens.intersection(it_tokens))
        score += common * 3

        # priorities
        if "p560" in q and "p560" in it.id.lower():
            score += 60
        if "ctf" in q and "ctf" in it.id.lower():
            score += 60

        if score > best_score:
            best_score = score
            best_item = it

    if best_score < 5:
        return None

    return best_item


    q = question.lower()
    best_score = -1.0
    best_item: Optional[KBItem] = None

    for it in candidates:
        score = 0.0
        # match su question + tags
        if it.question:
            for token in it.question.lower().split():
                if token and token in q:
                    score += 1.0
        for t in it.tags:
            if t and t in q:
                score += 1.5
        if score > best_score:
            best_score = score
            best_item = it

    return best_item

# -----------------------------------------------------------------------------
# MOTORE IMBUTO / A4
# -----------------------------------------------------------------------------

def answer_question(question: str, lang: str = "it") -> Dict[str, Any]:
    """Core engine: A4 aggressivo, deterministico quanto possibile."""
    if not question or not question.strip():
        raise HTTPException(status_code=400, detail="Domanda vuota.")

    q = question.strip()

    # 1) Riconosci famiglie
    families = detect_families(q)
    if families:
        top_family = families[0].family
    else:
        # se proprio non capisce, chiedi al modello (se disponibile)
        top_family = None
        llm_family = call_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Sei un classificatore di domande per il bot Tecnaria. "
                        "Rispondi SOLO con una di queste parole: CTF_SYSTEM, CTL, CTL_MAXI, DIAPASON, VCEM, CTCEM, COMM. "
                        "Scegli quella che meglio rappresenta la famiglia di prodotto coinvolta nella domanda."
                    ),
                },
                {"role": "user", "content": q},
            ],
            max_tokens=4,
        )
        if llm_family:
            cand = llm_family.strip().upper()
            if cand in FAMILY_RULES.keys() or cand == "COMM":
                top_family = cand

        if not top_family:
            # fallback finale: rispondi in modo generico usando COMM
            top_family = "COMM"

    # 2) Intent
    intent = detect_intent(q)

    # 3) Blocchi GOLD
    block_id = dispatch_block_id(top_family, intent)
    item: Optional[KBItem] = None
    if block_id:
        item = get_kb_item_by_id(block_id, lang)

    if item is None:
        # fallback su best match famiglia
        item = simple_best_match_by_family(q, top_family, lang)

    if item is None:
        # fallback totale: risposta generica
        text = (
            "Al momento non trovo un blocco GOLD specifico per questa domanda. "
            "Ti consiglio di riformularla indicando il tipo di solaio e la famiglia di connettori "
            "(CTF_SYSTEM per sistemi su lamiera e P560, CTL, CTL MAXI, VCEM, CTCEM, DIAPASON)."
        )
        return {
            "family": top_family,
            "intent": intent,
            "block_id": None,
            "answer": text,
            "mode": "fallback",
        }

    # 4) Risposta base GOLD (modalità A: aziendale tecnica, niente poesia)
    answer_text = item.answer

    return {
        "family": item.family,
        "intent": intent,
        "block_id": item.id,
        "answer": answer_text,
        "mode": item.mode,
    }

# -----------------------------------------------------------------------------
# FASTAPI APP
# -----------------------------------------------------------------------------

app = FastAPI(title=APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # se vuoi restringere, metti il dominio del tuo frontend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# STARTUP
# -----------------------------------------------------------------------------

@app.on_event("startup")
def _startup_event() -> None:
    load_kb()
    logger.info(f"[APP] {APP_NAME} avviata. MODEL_CHAT={MODEL_CHAT}")

# -----------------------------------------------------------------------------
# API: ROOT & CONFIG
# -----------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def root() -> str:
    """UI minimale dark + info di base API."""
    return f"""
<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <title>{APP_NAME}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body {{
      margin: 0;
      padding: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #05060a;
      color: #edf1f7;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
    }}
    .container {{
      width: 100%;
      max-width: 920px;
      padding: 24px;
      box-sizing: border-box;
    }}
    .card {{
      background: radial-gradient(circle at top left, #1f2933, #05060a 60%);
      border-radius: 18px;
      padding: 24px 24px 20px;
      box-shadow: 0 18px 45px rgba(0,0,0,0.75);
      border: 1px solid rgba(255,255,255,0.06);
    }}
    h1 {{
      margin: 0 0 4px;
      font-size: 1.4rem;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: #9ca3ff;
    }}
    .subtitle {{
      font-size: 0.9rem;
      color: #9ca3af;
      margin-bottom: 18px;
    }}
    label {{
      font-size: 0.85rem;
      color: #d1d5db;
      display: block;
      margin-bottom: 6px;
    }}
    textarea {{
      width: 100%;
      min-height: 90px;
      max-height: 260px;
      resize: vertical;
      border-radius: 12px;
      border: 1px solid rgba(148,163,184,0.7);
      background: rgba(15,23,42,0.85);
      color: #f9fafb;
      padding: 10px 12px;
      font-size: 0.95rem;
      outline: none;
      box-sizing: border-box;
    }}
    textarea:focus {{
      border-color: #a5b4fc;
      box-shadow: 0 0 0 1px rgba(129,140,248,0.55);
    }}
    .row {{
      display: flex;
      align-items: center;
      margin-top: 10px;
      gap: 10px;
    }}
    select {{
      background: rgba(15,23,42,0.85);
      border-radius: 999px;
      border: 1px solid rgba(148,163,184,0.7);
      color: #e5e7eb;
      padding: 6px 10px;
      font-size: 0.85rem;
      outline: none;
    }}
    button {{
      border: none;
      border-radius: 999px;
      padding: 8px 18px;
      font-size: 0.9rem;
      cursor: pointer;
      background: linear-gradient(130deg, #4f46e5, #06b6d4);
      color: #f9fafb;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      box-shadow: 0 8px 22px rgba(15,23,42,0.7);
    }}
    button:disabled {{
      opacity: 0.6;
      cursor: default;
      box-shadow: none;
    }}
    .answer-card {{
      margin-top: 18px;
      border-radius: 14px;
      border: 1px solid rgba(55,65,81,0.9);
      background: radial-gradient(circle at top right, #111827, #020617 65%);
      padding: 14px 14px 10px;
      font-size: 0.93rem;
      line-height: 1.5;
      color: #e5e7eb;
      white-space: pre-wrap;
    }}
    .answer-meta {{
      font-size: 0.7rem;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: #9ca3af;
      margin-bottom: 4px;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 2px 7px;
      border-radius: 999px;
      background: rgba(15,23,42,0.95);
      border: 1px solid rgba(148,163,184,0.5);
      font-size: 0.7rem;
    }}
    .badge span {{
      opacity: 0.9;
    }}
    .small-muted {{
      font-size: 0.72rem;
      color: #6b7280;
      margin-top: 10px;
    }}
    a {{
      color: #a5b4fc;
      text-decoration: none;
    }}
    a:hover {{
      text-decoration: underline;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="card">
      <h1>{APP_NAME}</h1>
      <div class="subtitle">
        Assistente tecnico su connettori e sistemi di consolidamento solai Tecnaria.<br/>
        Digita la tua domanda in linguaggio naturale, il motore A4 penserà al resto.
      </div>

      <label for="question">Domanda</label>
      <textarea id="question" placeholder="Esempi:
- Mi parli della P560?
- Che connettori uso per un solaio in legno con soletta in calcestruzzo?
- La pistola rimbalza sulla lamiera grecata, cosa può essere?"></textarea>

      <div class="row">
        <div>
          <select id="lang">
            <option value="it" selected>Italiano</option>
            <option value="en">English</option>
            <option value="fr">Français</option>
            <option value="de">Deutsch</option>
            <option value="es">Español</option>
          </select>
        </div>
        <div style="flex:1"></div>
        <button id="askBtn" onclick="sendQuestion()">
          <span>Chiedi</span>
          <span>➜</span>
        </button>
      </div>

      <div id="answerBox" class="answer-card" style="display:none;">
        <div class="answer-meta">
          <span class="badge">
            <span id="metaFamily">-</span>
          </span>
          &nbsp;&nbsp;
          <span class="badge">
            <span id="metaIntent">-</span>
          </span>
          &nbsp;&nbsp;
          <span class="badge">
            <span id="metaBlock">-</span>
          </span>
        </div>
        <div id="answerText"></div>
      </div>

      <div class="small-muted">
        API disponibili:
        <code>GET /api/config</code>,
        <code>POST /api/ask</code> (JSON).
      </div>
    </div>
  </div>

  <script>
    async function sendQuestion() {{
      const qEl = document.getElementById("question");
      const langEl = document.getElementById("lang");
      const btn = document.getElementById("askBtn");
      const answerBox = document.getElementById("answerBox");
      const answerText = document.getElementById("answerText");
      const metaFamily = document.getElementById("metaFamily");
      const metaIntent = document.getElementById("metaIntent");
      const metaBlock = document.getElementById("metaBlock");

      const q = qEl.value.trim();
      const lang = langEl.value;

      if (!q) {{
        alert("Scrivi una domanda.");
        return;
      }}

      btn.disabled = true;
      btn.innerHTML = "<span>In elaborazione...</span>";

      try {{
        const resp = await fetch("/api/ask", {{
          method: "POST",
          headers: {{
            "Content-Type": "application/json"
          }},
          body: JSON.stringify({{ question: q, lang: lang }})
        }});

        if (!resp.ok) {{
          const err = await resp.json().catch(() => ({{ detail: resp.statusText }}));
          throw new Error(err.detail || "Errore API");
        }}

        const data = await resp.json();
        answerText.textContent = data.answer || "[Nessuna risposta disponibile]";
        metaFamily.textContent = data.family ? ("Famiglia: " + data.family) : "Famiglia: -";
        metaIntent.textContent = data.intent ? ("Intent: " + data.intent) : "Intent: -";
        metaBlock.textContent = data.block_id ? ("Blocco: " + data.block_id) : "Blocco: -";

        answerBox.style.display = "block";
      }} catch (e) {{
        console.error(e);
        answerText.textContent = "Si è verificato un errore durante l'elaborazione della domanda.";
        metaFamily.textContent = "Famiglia: -";
        metaIntent.textContent = "Intent: -";
        metaBlock.textContent = "Blocco: -";
        answerBox.style.display = "block";
      }} finally {{
        btn.disabled = false;
        btn.innerHTML = "<span>Chiedi</span><span>➜</span>";
      }}
    }}

    // Invio con CTRL+Invio
    document.addEventListener("keydown", function(e) {{
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {{
        sendQuestion();
      }}
    }});
  </script>
</body>
</html>
    """

@app.get("/api/config")
def api_config() -> Dict[str, Any]:
    return {
        "app": APP_NAME,
        "model_chat": MODEL_CHAT,
        "gold_items": len(KB_ITEMS),
        "imbuto_stages": IMBUTO_STAGES,
        "data_dir": DATA_DIR,
        "family_files": FAMILY_FILES,
        "consolidato_loaded": CONSOLIDATO_LOADED,
    }

# -----------------------------------------------------------------------------
# API: ASK
# -----------------------------------------------------------------------------

@app.post("/api/ask")
async def api_ask(payload: Dict[str, Any]) -> JSONResponse:
    question = str(payload.get("question", "") or "").strip()
    lang = str(payload.get("lang", "it") or "it").lower()
    try:
        result = answer_question(question, lang=lang)
        return JSONResponse(result)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"[API] Errore /api/ask: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore interno durante l'elaborazione della domanda.")

# -----------------------------------------------------------------------------
# HEALTHCHECK
# -----------------------------------------------------------------------------

@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "app": APP_NAME,
        "items": len(KB_ITEMS),
    }
