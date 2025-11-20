import os
import json
import re
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from openai import OpenAI

# ============================================================
# CONFIG BASE
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(STATIC_DIR, "data")

MASTER_PATH = os.path.join(DATA_DIR, "ctf_system_COMPLETE_GOLD_master.json")
COMM_PATH = os.path.join(DATA_DIR, "COMM.json")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o").strip() or "gpt-4o"

client = None
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)

# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(title="Tecnaria Bot v14.7")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if not os.path.isdir(STATIC_DIR):
    os.makedirs(STATIC_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ============================================================
# MODELLI
# ============================================================

class QuestionRequest(BaseModel):
    question: str


class AnswerResponse(BaseModel):
    answer: str
    source: str
    meta: Dict[str, Any]

# ============================================================
# NORMALIZZAZIONE TESTO
# ============================================================

def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\sàèéìòóùç]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

# ============================================================
# CARICAMENTO KB TECNICA (per futuro uso, ora solo meta)
# ============================================================

KB_BLOCKS: List[Dict[str, Any]] = []


def load_kb() -> None:
    global KB_BLOCKS
    if not os.path.exists(MASTER_PATH):
        print(f"[WARN] MASTER_PATH non trovato: {MASTER_PATH}")
        KB_BLOCKS = []
        return

    try:
        with open(MASTER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "blocks" in data:
            KB_BLOCKS = data["blocks"]
        elif isinstance(data, list):
            KB_BLOCKS = data
        else:
            KB_BLOCKS = []
        print(f"[INFO] KB caricata: {len(KB_BLOCKS)} blocchi")
    except Exception as e:
        print(f"[ERROR] caricando KB: {e}")
        KB_BLOCKS = []


def score_block(question_norm: str, block: Dict[str, Any]) -> float:
    triggers = " ".join(block.get("triggers", []))
    q_it = block.get("question_it", "")
    text = normalize(triggers + " " + q_it)
    if not text:
        return 0.0

    q_words = set(question_norm.split())
    b_words = set(text.split())
    common = q_words & b_words
    if not common:
        return 0.0

    return len(common) / max(len(q_words), 1)


def match_from_kb(question: str, threshold: float = 0.18) -> Optional[Dict[str, Any]]:
    if not KB_BLOCKS:
        return None
    qn = normalize(question)
    best_block = None
    best_score = 0.0
    for b in KB_BLOCKS:
        s = score_block(qn, b)
        if s > best_score:
            best_score = s
            best_block = b
    if best_score < threshold:
        return None
    return best_block


load_kb()

# ============================================================
# CARICAMENTO COMM (dati aziendali/commerciali)
# ============================================================

COMM_ITEMS: List[Dict[str, Any]] = []


def load_comm() -> None:
    global COMM_ITEMS
    if not os.path.exists(COMM_PATH):
        print(f"[WARN] COMM_PATH non trovato: {COMM_PATH}")
        COMM_ITEMS = []
        return

    try:
        with open(COMM_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "items" in data:
            COMM_ITEMS = data["items"]
        elif isinstance(data, list):
            COMM_ITEMS = data
        else:
            COMM_ITEMS = []
        print(f"[INFO] COMM caricata: {len(COMM_ITEMS)} blocchi COMM")
    except Exception as e:
        print(f"[ERROR] caricando COMM: {e}")
        COMM_ITEMS = []


def is_commercial_question(q: str) -> bool:
    """
    Riconosce domande aziendali/commerciali (dati azienda, contatti, P.IVA, indirizzo, SDI, orari, ecc.)
    che devono essere risposte SOLO da COMM.json.
    """
    q = q.lower()
    keywords = [
        "partita iva", "p.iva", "p iva", "codice fiscale",
        "rea", "registro imprese", "camera di commercio",
        "indirizzo", "sede", "dove si trova tecnaria",
        "telefono", "numero di telefono", "recapito",
        "email", "mail", "posta elettronica",
        "orari", "orario", "apertura", "chiusura",
        "codice sdi", "sdi", "codice destinatario",
        "fatturazione elettronica",
        "dati aziendali", "dati societari", "azienda tecnaria"
    ]
    return any(k in q for k in keywords)


def match_comm(question: str) -> Optional[Dict[str, Any]]:
    """
    Matching molto semplice su COMM.json basato sui tag.
    """
    if not COMM_ITEMS:
        return None

    q = normalize(question)
    best = None
    best_score = 0

    for item in COMM_ITEMS:
        local_score = 0
        for tag in item.get("tags", []):
            tag_norm = tag.lower()
            if tag_norm and tag_norm in q:
                local_score += 1

        if local_score > best_score:
            best_score = local_score
            best = item

    return best if best_score >= 1 else None


load_comm()

# ============================================================
# LLM: CHATGPT CON PROMPT TECNARIA GOLD
# ============================================================

SYSTEM_PROMPT = """
Sei un tecnico–commerciale senior di Tecnaria S.p.A. con più di 20 anni di esperienza
su:
- CTF + P560 + lamiera grecata per solai misti acciaio–cls,
- VCEM / CTCEM per solai in laterocemento,
- CTL / CTL MAXI per solai legno–calcestruzzo,
- DIAPASON per travetti in laterocemento,
- sistemi di posa, controllo colpi, card, limiti di validità e normativa collegata.

REGOLE OBBLIGATORIE (NON DEROGABILI):

1. Rispondi SOLO nel mondo Tecnaria S.p.A. (prodotti, sistemi, posa, normative correlate).
   Non usare mai esempi generici o riferiti ad altri produttori.
   Se il dato numerico non è certo o non è esplicitamente noto, scrivi chiaramente:
   "Questo valore va verificato nelle istruzioni Tecnaria o con l’Ufficio Tecnico."
3. Per i CTF cita SEMPRE la chiodatrice P560 e i “chiodi idonei Tecnaria”.
   Non dire mai che DIAPASON utilizza chiodi o la P560.
5. Se nella domanda compaiono più famiglie (es. CTF e DIAPASON), distingui SEMPRE le due famiglie
   e spiega separatamente il loro funzionamento.
6. Non inventare mai dati aziendali (indirizzi, orari, nominativi, numeri di telefono):
   questi aspetti sono gestiti da moduli dedicati del sistema.
7. Stile di risposta:
   - linguaggio tecnico–ingegneristico, chiaro, aziendale;
   - niente marketing, niente frasi vaghe;
   - quando serve, usa elenco puntato per chiarezza;
   - resta concentrato sul problema specifico della domanda.

Se la domanda è palesemente fuori dal mondo Tecnaria, spiega che il sistema è pensato
solo per rispondere su prodotti e applicazioni Tecnaria.
"""

# Prompt "furbo" in stile ChatGPT app (generale, fluido)
FURBA_SYSTEM_PROMPT = """
Sei ChatGPT in modalità conversazionale generale.
Rispondi in modo chiaro, fluido e completo, come nell'interfaccia ufficiale di ChatGPT.
Non essere eccessivamente prudente: prova a dare sempre una risposta utile e ben formulata.
"""

def call_chatgpt(question: str) -> str:
    if client is None:
        return (
            "Al momento il motore ChatGPT esterno non è disponibile "
            "(OPENAI_API_KEY mancante). Contatta l'Ufficio Tecnico Tecnaria."
        )

    try:
        completion = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            temperature=0.2,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        print(f"[ERROR] chiamando ChatGPT: {e}")
        return (
            "Si è verificato un errore nella chiamata al motore esterno. "
            "Per sicurezza, contatta direttamente l’Ufficio Tecnico Tecnaria."
        )


def call_chatgpt_furba(question: str) -> str:
    """
    Versione 'furba': usa il modello in modo molto simile alla ChatGPT app,
    senza vincoli Tecnaria, massima fluidità e completezza.
    """
    if client is None:
        return (
            "Il motore ChatGPT esterno non è disponibile "
            "(OPENAI_API_KEY mancante)."
        )

    try:
        completion = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": FURBA_SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            temperature=0.4,  # un po' più “vivace” della versione tecnica
            top_p=1.0,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        print(f"[ERROR] chiamando ChatGPT (furba): {e}")
        return (
            "Si è verificato un errore nella chiamata al motore
