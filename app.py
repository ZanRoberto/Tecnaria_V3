import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ---------------------------------------------------------
# CONFIGURAZIONE DI BASE
# ---------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_ROOT = BASE_DIR / "static" / "data"

CONFIG_PATH = DATA_ROOT / "config.runtime.json"

# caricheremo TUTTI i contenuti GOLD (famiglie + patch)
ITEMS: List[Dict[str, Any]] = []
NORMALIZED_TRIGGERS: Dict[str, List[str]] = {}  # id -> [trigger_norm1, trigger_norm2, ...]

# fallback COMM
FALLBACK_FAMILY = "COMM"
FALLBACK_ID = "COMM-FALLBACK-NOANSWER-0001"
FALLBACK_MESSAGE = (
    "Per questa domanda non trovo una risposta GOLD consolidata. "
    "Meglio un confronto diretto con l’ufficio tecnico Tecnaria, indicando tipo di solaio, travi, spessori e vincoli."
)

logger = logging.getLogger("tecnaria")
logging.basicConfig(level=logging.INFO)


# ---------------------------------------------------------
# MODELLI Pydantic
# ---------------------------------------------------------

class AskRequest(BaseModel):
    q: str
    lang: str = "it"
    mode: Optional[str] = "gold"


class AskResponse(BaseModel):
    ok: bool
    answer: str
    family: str
    id: str
    mode: str
    lang: str


# ---------------------------------------------------------
# UTILITY: normalizzazione testo
# ---------------------------------------------------------

def norm_text(text: str) -> str:
    """minuscole + spazi ridotti; niente magie."""
    return " ".join(text.lower().strip().split())


# ---------------------------------------------------------
# CARICAMENTO CONTENUTI GOLD
# ---------------------------------------------------------

def is_item_like(obj: Any) -> bool:
    """Riconosce 'blocchi' di risposta dai JSON (famiglie/patch)."""
    if not isinstance(obj, dict):
        return False
    if "id" not in obj or "family" not in obj:
        return False
    # cerchiamo almeno un payload di risposta
    if "response_variants" in obj:
        return True
    if "answer" in obj or "risposta" in obj:
        return True
    if "gold" in obj:
        return True
    return False


def extract_items_from_data(data: Any) -> List[Dict[str, Any]]:
    """Estrae lista di item da vari formati JSON (list o {items: [...]})"""
    items: List[Dict[str, Any]] = []

    if isinstance(data, dict):
        if "items" in data and isinstance(data["items"], list):
            for it in data["items"]:
                if is_item_like(it):
                    items.append(it)
        else:
            # qualche file potrebbe essere un singolo item
            if is_item_like(data):
                items.append(data)

    elif isinstance(data, list):
        for it in data:
            if is_item_like(it):
                items.append(it)

    return items


def load_all_items() -> None:
    """
    Scansiona:
      - static/data/*.json
      - static/data/patches/*.json
    e raccoglie tutti gli item 'GOLD-like'.
    """
    global ITEMS, NORMALIZED_TRIGGERS

    ITEMS = []
    NORMALIZED_TRIGGERS = {}

    search_dirs = [DATA_ROOT, DATA_ROOT / "patches"]

    for sdir in search_dirs:
        if not sdir.exists():
            continue
        for path in sdir.glob("*.json"):
            # saltiamo file chiaramente non di contenuto (tests, expected, ecc.)
            if "test" in path.name.lower():
                continue
            if "expected_patterns" in path.name.lower():
                continue
            if "index_tecnaria" in path.name.lower():
                # l'indice lo useremo in futuro, qui ci bastano i contenuti
                continue

            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                items_here = extract_items_from_data(data)
                if items_here:
                    logger.info(f"[LOAD] {path.name}: +{len(items_here)} item")
                    ITEMS.extend(items_here)
            except Exception as e:
                logger.warning(f"[LOAD] errore caricando {path}: {e}")

    # precomputiamo i trigger normalizzati
    for it in ITEMS:
        it_id = str(it.get("id", ""))
        triggers: List[str] = []

        # 1) campo 'triggers'
        raw_tr = it.get("triggers")
        if isinstance(raw_tr, list):
            triggers.extend([str(t) for t in raw_tr if t])

        # 2) meta.triggers
        meta = it.get("meta") or {}
        raw_tr2 = meta.get("triggers")
        if isinstance(raw_tr2, list):
            triggers.extend([str(t) for t in raw_tr2 if t])

        # 3) fallback: domanda / titolo
        if not triggers:
            for key in ("q", "question", "domanda", "title", "titolo"):
                if key in it and isinstance(it[key], str):
                    triggers.append(it[key])

        norm_list = [norm_text(t) for t in triggers if t]
        NORMALIZED_TRIGGERS[it_id] = norm_list

    logger.info(f"[LOAD] Totale item caricati: {len(ITEMS)}")


def get_gold_answer(it: Dict[str, Any]) -> Optional[str]:
    """
    Estrae la risposta GOLD in italiano da un item.
    """
    rv = it.get("response_variants") or {}
    gold = rv.get("gold") or rv.get("GOLD") or {}
    ans_it = gold.get("it") or gold.get("IT")
    if isinstance(ans_it, str):
        return ans_it

    # fallback: campo 'answer' o 'risposta'
    if isinstance(it.get("answer"), str):
        return it["answer"]
    if isinstance(it.get("risposta"), str):
        return it["risposta"]

    return None


# ---------------------------------------------------------
# MATCHING: trova il miglior item per la domanda
# ---------------------------------------------------------

def score_match(question: str, item_id: str) -> float:
    """
    Matching super semplice ma robusto:
      - match quasi-esatto -> punteggio alto
      - contenimento reciproco -> medio
    """
    qn = norm_text(question)
    best = 0.0
    triggers = NORMALIZED_TRIGGERS.get(item_id, [])
    if not triggers:
        return 0.0

    for t in triggers:
        if not t:
            continue
        if qn == t:
            best = max(best, 3.0)
        elif qn in t:
            best = max(best, 2.0)
        elif t in qn:
            best = max(best, 1.5)
        else:
            # micro bonus se condividono molte parole
            q_words = set(qn.split())
            t_words = set(t.split())
            inter = len(q_words & t_words)
            if inter >= 3:
                best = max(best, 1.0)

    return best


def pick_best_item(question: str) -> Optional[Dict[str, Any]]:
    """
    Ritorna l'item meglio matchato, oppure None se non c'è niente di credibile.
    """
    best_it = None
    best_score = 0.0

    for it in ITEMS:
        it_id = str(it.get("id", ""))
        if not it_id:
            continue
        s = score_match(question, it_id)
        if s > best_score:
            best_score = s
            best_it = it

    # soglia minima per considerare attendibile
    if best_score < 1.0:
        return None
    return best_it


# ---------------------------------------------------------
# FASTAPI
# ---------------------------------------------------------

app = FastAPI(title="Tecnaria Sinapsi GOLD")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# carichiamo i contenuti una volta sola all'avvio
load_all_items()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/init")
def api_init():
    """
    Endpoint usato dalla UI per verificare che il backend sia vivo e che la modalità GOLD sia attiva.
    """
    return {
        "ok": True,
        "message": "Tecnaria Sinapsi – GOLD attivo",
        "gold_mode": True,
    }


@app.get("/api/config")
def api_config():
    """
    Ritorna il contenuto di config.runtime.json così com'è (compatibile con la UI esistente).
    """
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="config.runtime.json non trovato")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore leggendo config.runtime.json: {e}")
    return cfg


@app.post("/api/ask", response_model=AskResponse)
def api_ask(req: AskRequest):
    """
    Cuore del BOT:
      - prende q/lang/mode
      - cerca la miglior risposta GOLD nei JSON caricati
      - se non trova nulla di credibile: fallback COMM (ma SEMPRE 200, niente 400)
    """
    question = req.q or ""
    if not question.strip():
        raise HTTPException(status_code=400, detail="Campo 'q' mancante o vuoto")

    # 1) cerchiamo il miglior item
    item = pick_best_item(question)

    if item is None:
        # Fallback COMM (NESSUN 400)
        return AskResponse(
            ok=False,
            answer=FALLBACK_MESSAGE,
            family=FALLBACK_FAMILY,
            id=FALLBACK_ID,
            mode=req.mode or "gold",
            lang=req.lang or "it",
        )

    family = str(item.get("family", FALLBACK_FAMILY))
    item_id = str(item.get("id", "UNKNOWN-ID"))
    answer = get_gold_answer(item)

    if not answer:
        # se l'item è matchato ma senza testo utilizzabile -> fallback
        return AskResponse(
            ok=False,
            answer=FALLBACK_MESSAGE,
            family=FALLBACK_FAMILY,
            id=FALLBACK_ID,
            mode=req.mode or "gold",
            lang=req.lang or "it",
        )

    return AskResponse(
        ok=True,
        answer=answer,
        family=family,
        id=item_id,
        mode=req.mode or "gold",
        lang=req.lang or "it",
    )


# opzionale: per debug locale
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
