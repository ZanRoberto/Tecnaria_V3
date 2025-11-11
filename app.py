import os
import json
import re
from typing import Dict, List, Any, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Se usi il nuovo SDK OpenAI:
try:
    from openai import OpenAI
    openai_client = OpenAI()
except Exception:
    openai_client = None  # Evita crash se la key non è presente: la traduzione diventa no-op.


# ============================================================
# CONFIGURAZIONE BASE
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(STATIC_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.runtime.json")

DEFAULT_FAMILIES = ["COMM", "CTCEM", "CTF", "CTL", "CTL_MAXI", "DIAPASON", "P560", "VCEM"]

app = FastAPI()

# Monta static per index/logo/css/js
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Stato globale modalità (rimane finché non cambi)
CURRENT_MODE = "gold"

# Knowledge base in memoria: {family: {"items": [...], "meta": {...}}}
KB: Dict[str, Dict[str, Any]] = {}


# ============================================================
# HELPER
# ============================================================

def load_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    return cfg


def load_family_file(family: str) -> Optional[Dict[str, Any]]:
    """
    Carica il JSON di una famiglia.
    Accetta struttura:
    - { "items": [...], "meta": {...} }
    - oppure direttamente [ {...}, {...} ]
    """
    path = os.path.join(DATA_DIR, f"{family}.json")
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    # Normalizza
    if isinstance(data, dict) and "items" in data:
        items = data.get("items", [])
        meta = data.get("meta", {})
    elif isinstance(data, list):
        items = data
        meta = {}
    else:
        items = []
        meta = {}

    # Assicura lista
    if not isinstance(items, list):
        items = []

    return {"items": items, "meta": meta}


def init_kb():
    cfg = load_config()
    families = cfg.get("families") or DEFAULT_FAMILIES

    KB.clear()
    for fam in families:
        fam = str(fam).strip()
        if not fam:
            continue
        loaded = load_family_file(fam)
        if loaded:
            KB[fam] = loaded


def norm(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9àèéìòóùüäößçñ\- ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_lang_simple(q: str) -> str:
    """
    Rilevamento molto semplice:
    - se vede pattern chiaramente inglesi -> en
    - francesi -> fr
    - tedeschi -> de
    - spagnoli -> es
    - altrimenti it
    """
    s = q.lower()

    # grezzi ma sufficienti per instradare la traduzione
    if any(w in s for w in [" the ", " can i ", " what ", "which ", "beam", "connectors", "use "]):
        return "en"
    if any(w in s for w in [" quelle ", " pourquoi ", " poutre ", "béton", "acier"]):
        return "fr"
    if any(w in s for w in [" warum ", " welche ", " stahl", "holz", "verbund"]):
        return "de"
    if any(w in s for w in [" por qué ", " cuál ", " losa ", "hormigón", "madera"]):
        return "es"
    # se contiene solo caratteri latini standard e molte parole italiane tipiche:
    if any(w in s for w in [" soletta", "trave", "calcestruzzo", "laterocemento", "legno", "quando ", "come "]):
        return "it"

    # default
    return "it"


def extract_all_variants(item: Dict[str, Any]) -> List[str]:
    """
    Raccoglie tutte le possibili varianti di testo utilizzabili da un blocco.
    Supporta tutti i formati che hai usato nelle varie versioni.
    """
    variants: List[str] = []

    # campi base singoli
    for key in ["answer_it", "answer", "canonical", "gold", "text"]:
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            variants.append(val.strip())

    # answers come dict
    answers = item.get("answers")
    if isinstance(answers, dict):
        for v in answers.values():
            if isinstance(v, str) and v.strip():
                variants.append(v.strip())

    # response_variants come lista o dict
    rv = item.get("response_variants")
    if isinstance(rv, list):
        for v in rv:
            if isinstance(v, str) and v.strip():
                variants.append(v.strip())
    elif isinstance(rv, dict):
        for v in rv.values():
            if isinstance(v, str) and v.strip():
                variants.append(v.strip())

    return [v for v in variants if v]


def choose_gold_text(item: Dict[str, Any]) -> str:
    """
    GOLD = sceglie la variante più completa:
    per semplicità: la più lunga tra quelle disponibili.
    """
    variants = extract_all_variants(item)
    if not variants:
        return ""
    # scegliamo la più lunga (approssimazione sicura per GOLD)
    return max(variants, key=lambda s: len(s))


def choose_canonical_text(item: Dict[str, Any]) -> str:
    """
    CANONICO = tecnico sintetico.
    Se esiste 'canonical', usiamo quello.
    Altrimenti prendiamo la variante più corta non banale.
    """
    canonical = item.get("canonical")
    if isinstance(canonical, str) and canonical.strip():
        return canonical.strip()

    variants = extract_all_variants(item)
    if not variants:
        return ""
    # scegliamo la più corta sopra una certa soglia minima
    candidates = [v for v in variants if len(v) > 40]
    if not candidates:
        candidates = variants
    return min(candidates, key=len)


def score_item(q_norm: str, item: Dict[str, Any]) -> float:
    """
    Scoring veloce basato su overlap parole con:
    - questions
    - paraphrases
    - tags
    """
    def collect_texts(it: Dict[str, Any]) -> List[str]:
        texts = []
        for key in ["questions", "question", "paraphrases", "tags"]:
            val = it.get(key)
            if isinstance(val, list):
                texts.extend([str(v) for v in val])
            elif isinstance(val, str):
                texts.append(val)
        return texts

    texts = collect_texts(item)
    if not texts:
        return 0.0

    best = 0.0
    q_tokens = set(q_norm.split())
    if not q_tokens:
        return 0.0

    for t in texts:
        t_norm = norm(str(t))
        if not t_norm:
            continue
        t_tokens = set(t_norm.split())
        inter = q_tokens & t_tokens
        if not inter:
            continue
        score = len(inter) / max(len(q_tokens), 3)
        if score > best:
            best = score

    return best


def find_best_item(question: str) -> Tuple[Optional[str], Optional[Dict[str, Any]], float]:
    """
    Cerca il miglior blocco tra tutte le famiglie.
    Ritorna (family, item, score)
    """
    q_norm = norm(question)
    best_family = None
    best_item = None
    best_score = 0.0

    for fam, data in KB.items():
        for item in data.get("items", []):
            s = score_item(q_norm, item)
            if s > best_score:
                best_score = s
                best_family = fam
                best_item = item

    # soglia minima, altrimenti niente
    if best_score < 0.30:
        return None, None, best_score

    return best_family, best_item, best_score


def translate_runtime(text: str, target_lang: str) -> str:
    """
    Traduzione online: se possibile usa OpenAI.
    Se qualcosa va storto o manca client/key → ritorna text originale.
    """
    if not text or target_lang == "it":
        return text

    if target_lang not in ["it", "en", "fr", "de", "es"]:
        # lingue non coperte → fallback inglese
        target_lang = "en"

    if openai_client is None:
        return text  # nessuna traduzione se client non disponibile

    try:
        msg = [
            {
                "role": "system",
                "content": f"Translate the following technical answer for structural connectors into {target_lang}. Keep it precise, no marketing, no extra comments."
            },
            {"role": "user", "content": text},
        ]
        resp = openai_client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            messages=msg,
            temperature=0.2,
            max_tokens=800,
        )
        out = resp.choices[0].message.content.strip()
        return out or text
    except Exception:
        return text


# ============================================================
# MODELLI API
# ============================================================

class AskRequest(BaseModel):
    q: str
    mode: Optional[str] = None  # opzionale: "gold" / "canonical"


# ============================================================
# ROUTES
# ============================================================

@app.on_event("startup")
def startup_event():
    init_kb()


@app.get("/", response_class=HTMLResponse)
def index():
    """
    Serve l'interfaccia HTML.
    """
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    # fallback minimale se manca index.html
    return HTMLResponse(
        """
        <html>
        <head><title>TECNARIA Sinapsi</title></head>
        <body>
          <h1>TECNARIA Sinapsi backend attivo</h1>
          <p>Usa /api/ask per interrogare il motore Q/A.</p>
        </body>
        </html>
        """,
        status_code=200,
    )


@app.get("/api/config")
def get_config():
    cfg = load_config()
    families = list(KB.keys())
    mode = CURRENT_MODE
    return {
        "ok": True,
        "message": "TECNARIA Sinapsi backend attivo",
        "mode": mode,
        "families": families,
        "config": cfg,
    }


@app.post("/api/ask")
def api_ask(payload: AskRequest):
    global CURRENT_MODE

    raw_q = (payload.q or "").strip()
    if not raw_q:
        return JSONResponse(
            {"ok": False, "message": "Domanda vuota."},
            status_code=200,
        )

    # Gestione comandi GOLD / CANONICO in testa domanda
    q = raw_q

    upper = q.upper().strip()
    if upper.startswith("GOLD:"):
        CURRENT_MODE = "gold"
        q = q.split(":", 1)[1].strip() or ""
        if not q:
            return {"ok": True, "message": "Modalità aggiornata a 'GOLD'. Inserisci la domanda successiva.", "mode": CURRENT_MODE}
    elif upper.startswith("CANONICO:") or upper.startswith("CANONICAL:"):
        CURRENT_MODE = "canonical"
        q = q.split(":", 1)[1].strip() or ""
        if not q:
            return {"ok": True, "message": "Modalità aggiornata a 'CANONICO'. Inserisci la domanda successiva.", "mode": CURRENT_MODE}

    # Se payload.mode esplicito, sovrascrive (facoltativo)
    if payload.mode:
        m = payload.mode.lower()
        if m in ("gold", "canonical"):
            CURRENT_MODE = m

    mode = CURRENT_MODE

    # Trova blocco migliore
    family, item, score = find_best_item(q)

    if not item or not family:
        return JSONResponse(
            {
                "ok": False,
                "message": "Per questa domanda non è ancora presente una risposta GOLD nei file Tecnaria. Va aggiunto un blocco nel JSON.",
                "mode": mode,
                "score": float(score),
            },
            status_code=200,
        )

    # Estrai testo in base alla modalità
    if mode == "canonical":
        base_text = choose_canonical_text(item)
    else:
        base_text = choose_gold_text(item)

    if not base_text:
        # Qui PRIMA vedevi il messaggio “blocco trovato ma senza risposta valida”
        # Ora lo rendiamo esplicito.
        return JSONResponse(
            {
                "ok": False,
                "message": f"Blocco {item.get('id')} trovato ma senza testo utilizzabile (schema JSON incompleto). Controllare i campi canonical/answer/response_variants.",
                "family": family,
                "id": item.get("id"),
                "mode": mode,
                "score": float(score),
            },
            status_code=200,
        )

    # Multilingua runtime
    q_lang = detect_lang_simple(q)
    answer_text = base_text
    if q_lang != "it":
        answer_text = translate_runtime(base_text, q_lang)

    return {
        "ok": True,
        "family": family,
        "id": item.get("id"),
        "mode": mode,
        "score": float(score),
        "lang": q_lang,
        "text": answer_text,
    }
