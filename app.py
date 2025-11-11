import os
import json
import math
import re
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from openai import OpenAI

# =========================================================
# CONFIGURAZIONE BASE
# =========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "static", "data")

DEFAULT_MODE = "gold"  # GOLD di default
SUPPORTED_FAMILIES_DEFAULT = [
    "COMM",
    "CTCEM",
    "CTF",
    "CTL",
    "CTL_MAXI",
    "DIAPASON",
    "P560",
    "TECNARIA_GOLD",
    "VCEM",
]

client = OpenAI()

app = FastAPI(title="TECNARIA Sinapsi Backend", version="3.0")

# Static (interfaccia)
static_dir = os.path.join(BASE_DIR, "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


# =========================================================
# LETTURA CONFIG
# =========================================================

def load_config() -> Dict[str, Any]:
    """
    Legge config.runtime.json se esiste, altrimenti usa defaults.
    """
    candidate_paths = [
        os.path.join(DATA_DIR, "config.runtime.json"),
        os.path.join(BASE_DIR, "config.runtime.json"),
    ]
    for p in candidate_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                return cfg
            except Exception:
                pass

    # fallback
    return {
        "ok": True,
        "mode": DEFAULT_MODE,
        "families": SUPPORTED_FAMILIES_DEFAULT,
    }


CONFIG = load_config()


# =========================================================
# CARICAMENTO DATASET
# =========================================================

def load_family_file(name: str) -> List[Dict[str, Any]]:
    """
    Carica il JSON di una famiglia da static/data/<name>.json se esiste.
    Se manca, ritorna lista vuota.
    """
    filename = f"{name}.json"
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def load_all_items() -> List[Dict[str, Any]]:
    families = CONFIG.get("families", SUPPORTED_FAMILIES_DEFAULT)
    all_items: List[Dict[str, Any]] = []
    for fam in families:
        items = load_family_file(fam)
        for it in items:
            if "family" not in it:
                it["family"] = fam
            all_items.append(it)
    return all_items


DATA_ITEMS = load_all_items()


# =========================================================
# UTILS TESTO / LINGUA
# =========================================================

def normalize(text: str) -> List[str]:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9àèéìòóùüäößçñ ]+", " ", text)
    return [t for t in text.split() if t]


def detect_language(q: str) -> str:
    """
    Rilevamento grezzo:
    - IT, EN, FR, DE, ES
    - fallback EN per lingue non riconosciute (es. russo).
    """
    s = q.lower()

    it_markers = [" il ", " lo ", " la ", " che ", " con ", " dove ", "posso", "soletta", "trave", "calcestruzzo"]
    en_markers = [" the ", " can ", " what ", " where ", " beam", "slab", "concrete"]
    fr_markers = [" le ", " la ", " les ", " des ", " avec ", " poutre", "dalle"]
    de_markers = [" der ", " die ", " das ", " und ", " mit ", "träger", "platte"]
    es_markers = [" el ", " la ", " los ", " las ", " con ", "viga", "forjado"]

    def has_any(markers): return any(m in s for m in markers)

    if has_any(it_markers):
        return "it"
    if has_any(en_markers):
        return "en"
    if has_any(fr_markers):
        return "fr"
    if has_any(de_markers):
        return "de"
    if has_any(es_markers):
        return "es"
    # fallback: meglio inglese che italiano per richieste esotiche
    return "en"


# =========================================================
# ESTRAZIONE TESTO DAI BLOCCHI
# =========================================================

def extract_gold_text(item: Dict[str, Any], lang: str) -> Optional[str]:
    rv = item.get("response_variants") or item.get("variants") or {}
    gold = rv.get("gold") or rv.get("GOLD") or rv.get("gold_it")

    # Caso gold come dict lingue
    if isinstance(gold, dict):
        if lang in gold and gold[lang]:
            return gold[lang]
        if "it" in gold and gold["it"]:
            return gold["it"]
        # qualsiasi lingua disponibile
        for v in gold.values():
            if v:
                return v

    # Caso gold come stringa
    if isinstance(gold, str):
        return gold

    # Fallback su campi legacy
    for key in ["answer_gold", "gold_answer", "answer_it", "answer"]:
        if isinstance(item.get(key), str) and item[key].strip():
            return item[key].strip()

    # Fallback su canonical se proprio non c'è altro
    canonical = item.get("canonical")
    if isinstance(canonical, str) and canonical.strip():
        return canonical.strip()

    return None


def extract_canonical_text(item: Dict[str, Any], lang: str) -> Optional[str]:
    # 1. canonical dedicato
    canonical = item.get("canonical")
    if isinstance(canonical, dict):
        # supporto eventuale canonical per lingue
        if lang in canonical and canonical[lang]:
            return canonical[lang]
        if "it" in canonical and canonical["it"]:
            return canonical["it"]
    if isinstance(canonical, str) and canonical.strip():
        return canonical.strip()

    # 2. risposte sintetiche legacy
    for key in ["answer_it", "answer", "short"]:
        if isinstance(item.get(key), str) and item[key].strip():
            return item[key].strip()

    # 3. se non c'è nulla, fallback a gold
    return extract_gold_text(item, lang)


# =========================================================
# MATCHING / INSTRADAMENTO
# =========================================================

KEYWORDS_FAMILY = {
    "CTF": ["ctf", "trave acciaio", "acciaio", "lamiera grecata", "piolo a sparo", "p560"],
    "VCEM": ["vcem", "piolo", "viti in calcestruzzo", "soletta", "nuovo solaio laterocemento"],
    "CTCEM": ["ctcem", "laterocemento", "travetti in calcestruzzo", "rinforzo solaio"],
    "CTL": ["ctl", "legno-calcestruzzo", "trave in legno", "solaio in legno"],
    "CTL_MAXI": ["ctl maxi", "maxi", "trave importante", "legno pesante"],
    "DIAPASON": ["diapason", "recupero laterocemento", "senza demolizione", "chiodi idonei"],
    "P560": ["p560", "chiodatrice", "chiodi a sparo", "cartucce"],
    "COMM": ["contatto", "assistenza", "tecnaria", "supporto tecnico"],
}

def guess_family_boost(q: str, item: Dict[str, Any]) -> float:
    """
    Piccolo boost se la domanda contiene parole chiave della famiglia.
    """
    ql = q.lower()
    fam = (item.get("family") or "").upper()
    boost = 0.0

    if fam in KEYWORDS_FAMILY:
        for kw in KEYWORDS_FAMILY[fam]:
            if kw in ql:
                boost += 1.5

    # P560/CTF coerenza forte
    if "p560" in ql and fam == "P560":
        boost += 2.0
    if "p560" in ql and fam == "CTF":
        boost += 1.0
    if "legno" in ql and fam in ("CTL", "CTL_MAXI"):
        boost += 1.5
    if "laterocemento" in ql and fam in ("VCEM", "CTCEM", "DIAPASON"):
        boost += 1.5

    return boost


def score_item(question: str, item: Dict[str, Any]) -> float:
    """
    Matching semplice ma robusto.
    """
    q_tokens = normalize(question)
    if not q_tokens:
        return 0.0

    text_parts = []

    # tag
    tags = item.get("tags", [])
    if isinstance(tags, list):
        text_parts.extend([str(t) for t in tags])

    # canonical / risposte
    for key in ["canonical", "answer", "answer_it"]:
        v = item.get(key)
        if isinstance(v, str):
            text_parts.append(v)
    rv = item.get("response_variants") or {}
    if isinstance(rv, dict):
        for v in rv.values():
            if isinstance(v, str):
                text_parts.append(v)
            elif isinstance(v, dict):
                text_parts.extend([str(x) for x in v.values() if isinstance(x, str)])

    corpus = normalize(" ".join(text_parts))
    if not corpus:
        return 0.0

    # overlap
    overlap = len(set(q_tokens) & set(corpus))
    score = overlap / math.sqrt(len(corpus) + 1)

    # family boost
    score += guess_family_boost(question, item)

    return score


def find_best_item(question: str, mode: str) -> Optional[Dict[str, Any]]:
    """
    Trova il blocco migliore su TUTTE le famiglie abilitate.
    """
    best = None
    best_score = 0.0

    for item in DATA_ITEMS:
        s = score_item(question, item)
        if s > best_score:
            best_score = s
            best = item

    # soglia minima per evitare risposte totalmente a caso
    if not best or best_score < 0.05:
        return None

    best["_match_score"] = round(best_score, 4)
    return best


# =========================================================
# GENERAZIONE RISPOSTA (CALL OPENAI)
# =========================================================

def build_system_prompt(mode: str, target_lang: str) -> str:
    base = (
        "Sei TECNARIA Sinapsi, assistente tecnico per connettori e sistemi misti "
        "Tecnaria (CTF, VCEM, CTCEM, CTL, CTL MAXI, DIAPASON, P560). "
        "Rispondi in modo coerente con i dati ufficiali Tecnaria, senza invenzioni."
    )

    if mode == "gold":
        extra = (
            " Modalità GOLD: risposta completa, strutturata, tecnica, chiara, con riferimenti a condizioni d'uso, limiti, "
            "compatibilità tra sistemi. Niente chiacchiere inutili."
        )
    else:
        extra = (
            " Modalità CANONICO: risposta tecnica sintetica, diretta, 1-3 frasi."
        )

    lang_map = {
        "it": "Rispondi in italiano.",
        "en": "Answer in English.",
        "fr": "Réponds en français.",
        "de": "Antworte auf Deutsch.",
        "es": "Responde en español.",
    }
    lang_msg = lang_map.get(target_lang, "Answer in English.")

    return base + extra + " " + lang_msg


def generate_answer_with_openai(
    question: str,
    base_text: str,
    mode: str,
    target_lang: str,
    family: str,
    item_id: str,
) -> str:
    """
    Usa OpenAI per trasformare il blocco GOLD/ CANONICO in risposta finale.
    Se fallisce, ritorna base_text.
    """
    # safety: se manca base_text, meglio rispondere minimo
    if not base_text:
        return "Al momento non è disponibile una risposta tecnica adeguata. Contatta il supporto Tecnaria."

    try:
        system_prompt = build_system_prompt(mode, target_lang)

        # Prompt utente: vincoliamo in modo esplicito i punti critici (es. P560 / CTF)
        user_prompt = f"""
Domanda utente:
{question}

Testo base dal dataset (famiglia {family}, id {item_id}):
\"\"\"{base_text}\"\"\"

Istruzioni:
- Usa il testo base come riferimento vincolante.
- Se la domanda riguarda CTF: specifica che la posa corretta è con chiodatrice a polvere P560 Tecnaria con accessori e chiodi idonei, salvo diversa indicazione ufficiale.
- Non estendere l'uso di P560 a sistemi non previsti (CTL, CTL MAXI, VCEM, CTCEM, DIAPASON).
- Se più famiglie sono citate insieme, separa chiaramente i campi di utilizzo.
- Non inventare prodotti, certificazioni o valori numerici non presenti in documentazione.
- In modalità GOLD: dai una risposta completa e strutturata.
- In modalità CANONICO: sii sintetico ma tecnicamente corretto.
- Mantieni la risposta nella lingua richiesta dal sistema.
"""
        completion = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            temperature=0.2 if mode == "canonical" else 0.35,
            max_tokens=600 if mode == "gold" else 220,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = completion.choices[0].message.content.strip()
        if not text:
            return base_text
        return text
    except Exception:
        # fallback se API non risponde
        return base_text


# =========================================================
# SCHEMA RICHIESTE
# =========================================================

class AskRequest(BaseModel):
    question: str
    mode: Optional[str] = None  # "gold" o "canonical" dal frontend (toggle)


# =========================================================
# ENDPOINTS
# =========================================================

@app.get("/", response_class=HTMLResponse)
async def index():
    """
    Serve l'interfaccia. Se manca index.html, mostra un placeholder.
    """
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    # fallback minimale (per sicurezza)
    return """
<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8" />
  <title>TECNARIA Sinapsi</title>
</head>
<body>
  <h1>TECNARIA Sinapsi backend attivo</h1>
  <p>Interfaccia non trovata. Verifica i file statici.</p>
</body>
</html>
"""


@app.get("/api/config", response_class=JSONResponse)
async def get_config():
    """
    Usato dalla UI per capire se il backend è vivo e quali famiglie sono abilitate.
    """
    return JSONResponse({
        "ok": True,
        "message": "TECNARIA Sinapsi backend attivo",
        "mode": DEFAULT_MODE,
        "families": CONFIG.get("families", SUPPORTED_FAMILIES_DEFAULT),
    })


@app.post("/api/ask", response_class=JSONResponse)
async def ask(req: AskRequest):
    raw_q = (req.question or "").strip()
    if not raw_q:
        return JSONResponse(
            {
                "ok": False,
                "error": "Domanda vuota.",
            },
            status_code=400,
        )

    # -----------------------------------------------------
    # Gestione GOLD / CANONICO da testo
    # -----------------------------------------------------
    q_lower = raw_q.lower()

    # Prefissi forzanti
    forced_mode = None
    if q_lower.startswith("gold:"):
        forced_mode = "gold"
        raw_q = raw_q[5:].strip()
    elif q_lower.startswith("canonic:") or q_lower.startswith("canonico:") or q_lower.startswith("canonical:"):
        forced_mode = "canonical"
        raw_q = re.sub(r"^(canonic:|canonico:|canonical:)", "", q_lower, 1, flags=re.IGNORECASE).strip()

    # Mode finale:
    mode = (forced_mode or (req.mode or "")).lower()
    if mode not in ("gold", "canonical"):
        mode = DEFAULT_MODE  # default sempre GOLD

    # Lingua target
    target_lang = detect_language(raw_q)

    # Trova blocco migliore
    item = find_best_item(raw_q, mode)
    if not item:
        # Nessun match sensato
        fallback_msg = {
            "it": "Non trovo una risposta tecnica adatta nei dati disponibili. Contatta il supporto Tecnaria con maggiori dettagli.",
            "en": "No suitable technical answer found in the available data. Please contact Tecnaria support with more details.",
        }
        return JSONResponse(
            {
                "ok": True,
                "answer": fallback_msg.get(target_lang, fallback_msg["en"]),
                "meta": {
                    "family": None,
                    "id": None,
                    "score": 0.0,
                    "lang": target_lang,
                    "mode": mode.upper(),
                },
            }
        )

    family = item.get("family")
    item_id = item.get("id") or ""
    score = float(item.get("_match_score", 0.0))

    # Estrai testo base
    if mode == "gold":
        base_text = extract_gold_text(item, target_lang)
    else:
        base_text = extract_canonical_text(item, target_lang)

    if not base_text:
        # fallback minimo hard se manca tutto
        base_text = "Per questo argomento è necessaria una verifica diretta con l'ufficio tecnico Tecnaria."

    # Chiamata modello per risposta finale
    final_answer = generate_answer_with_openai(
        question=raw_q,
        base_text=base_text,
        mode=mode,
        target_lang=target_lang,
        family=family or "",
        item_id=item_id,
    )

    return JSONResponse(
        {
            "ok": True,
            "answer": final_answer,
            "meta": {
                "family": family,
                "id": item_id,
                "score": score,
                "lang": target_lang,
                "mode": mode.upper(),
            },
        }
    )


# =========================================================
# HEALTHCHECK SEMPLICE
# =========================================================

@app.get("/health", response_class=JSONResponse)
async def health():
    return JSONResponse({
        "ok": True,
        "message": "TECNARIA Sinapsi backend attivo",
        "families_loaded": list(sorted({i.get("family") for i in DATA_ITEMS if i.get("family")})),
        "items_count": len(DATA_ITEMS),
        "mode_default": DEFAULT_MODE,
    })


# =========================================================
# MAIN (solo locale)
# =========================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=10000, reload=True)
