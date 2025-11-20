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
OPENAI_MODEL_ENV = (os.getenv("OPENAI_MODEL", "gpt-4o") or "gpt-4o").strip()

client: Optional[OpenAI] = None
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)

# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(title="Tecnaria Sinapsi – GOLD")

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
# MODELLI Pydantic
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
# CARICAMENTO KB TECNICA (per meta / debug)
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
    if not q_words or not b_words:
        return 0.0

    common = q_words & b_words
    if not common:
        return 0.0

    return len(common) / max(len(q_words), 1)


def match_from_kb(question: str, threshold: float = 0.18) -> Optional[Dict[str, Any]]:
    if not KB_BLOCKS:
        return None
    qn = normalize(question)
    best_block: Optional[Dict[str, Any]] = None
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
        "dati aziendali", "dati societari", "azienda tecnaria",
    ]
    return any(k in q for k in keywords)


def match_comm(question: str) -> Optional[Dict[str, Any]]:
    if not COMM_ITEMS:
        return None

    q = normalize(question)
    best: Optional[Dict[str, Any]] = None
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
# LLM: PROMPT TECNARIA GOLD
# ============================================================

SYSTEM_PROMPT_GOLD = """
Sei un tecnico–commerciale senior di Tecnaria S.p.A. con più di 20 anni di esperienza
su tutti i sistemi:
- CTF + P560 per solai misti acciaio–calcestruzzo
- VCEM / CTCEM per solai in laterocemento
- CTL / CTL MAXI per solai legno–calcestruzzo
- DIAPASON per travetti in laterocemento
- GTS, accessori e fissaggi correlati
- procedure di posa, verifica colpi, card, limiti, normativa, casi di non validità.

REGOLE OBBLIGATORIE:

1. Rispondi esclusivamente nel mondo Tecnaria S.p.A.
   Non parlare mai di prodotti di altre aziende (trattori, proiettori, macchine da cucire, ecc.).

2. Per i CTF cita sempre la chiodatrice P560 e i "chiodi idonei Tecnaria".

3. Per il sistema DIAPASON:
   - NON utilizza chiodi.
   - Si fissa con UNA vite strutturale in ogni piastra.
   - Non citare mai P560 o chiodi in relazione ai DIAPASON.

4. Se la domanda riguarda più famiglie (es. CTF + DIAPASON), distingui sempre in modo netto i due sistemi
   e spiega le differenze operative.

5. Non inventare MAI valori numerici se non sono confermati dalle istruzioni Tecnaria:
   - numero di chiodi
   - passo
   - spessori
   - lunghezze
   - profondità
   - resistenze
   - distanze
   - quantità
   Se il dato non è certo, usa esattamente la frase:
   "Questo valore va verificato nelle istruzioni Tecnaria o con l’Ufficio Tecnico."

6. Se invece il valore numerico è presente nella documentazione Tecnaria, DEVI riportarlo esattamente.
   Non usare formulazioni vaghe.

7. Non inventare mai dati aziendali (indirizzo, P.IVA, SDI, telefono, nominativi).
   Se arrivano domande su questo, vengono gestite da un modulo COMM separato.

8. Stile della risposta:
   - tecnico-ingegneristico
   - chiaro, aziendale, senza marketing
   - se utile, usa elenchi puntati
   - spiega sempre perché la soluzione è corretta
   - evita frasi generiche tipo "dipende": specifica sempre cosa dipende da cosa.

9. Se la domanda è fuori dal mondo Tecnaria, scrivi:
   "Il sistema risponde solo su prodotti, posa e applicazioni Tecnaria S.p.A."

10. Se la domanda contiene un errore tecnico evidente, correggilo gentilmente
    e spiega la versione corretta.

Questo è un sistema GOLD: precisione massima, nessuna invenzione,
risposte chiare, determinate e ingegneristiche.
"""

def call_openai(prompt_system: str, question: str, temperature: float = 0.3) -> str:
    """
    Wrapper unico per chiamare OpenAI.
    Modello FORZATO a gpt-5.1 (ignora OPENAI_MODEL_ENV).
    """
    if client is None:
        return "Il motore esterno non è disponibile (OPENAI_API_KEY mancante)."

    try:
        completion = client.chat.completions.create(
            model="gpt-5.1",
            messages=[
                {"role": "system", "content": prompt_system},
                {"role": "user", "content": question},
            ],
            temperature=temperature,
            top_p=1.0,
        )
        return (completion.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[ERROR] chiamando OpenAI: {e}")
        return "Si è verificato un errore nella chiamata al motore esterno."

# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/")
async def root() -> FileResponse:
    """
    Serve l'interfaccia HTML (static/index.html).
    """
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=500, detail="index.html non trovato")
    return FileResponse(index_path)


@app.get("/api/status")
async def status():
    """
    Riepilogo rapido dello stato backend.
    """
    return {
        "status": "Tecnaria Bot attivo (GOLD only)",
        "kb_blocks": len(KB_BLOCKS),
        "comm_blocks": len(COMM_ITEMS),
        "openai_api_key_present": bool(OPENAI_API_KEY),
        "openai_model_env": OPENAI_MODEL_ENV,
        "openai_model_effective": "gpt-5.1",
    }


@app.post("/api/ask", response_model=AnswerResponse)
async def api_ask(req: QuestionRequest):
    """
    Modalità GOLD Tecnaria (tecnica, con prompt strutturale).
    """
    question_raw = (req.question or "").strip()
    if not question_raw:
        raise HTTPException(status_code=400, detail="Domanda vuota")

    q_norm = question_raw.lower()

    try:
        # 1) DOMANDE AZIENDALI / COMMERCIALI → SOLO COMM.JSON
        if is_commercial_question(q_norm):
            comm_block = match_comm(q_norm)
            if comm_block:
                answer = comm_block.get("response_variants", {}).get("gold", {}).get("it")
                if not answer:
                    answer = comm_block.get("answer_it") or comm_block.get("answer", "")
                return AnswerResponse(
                    answer=answer,
                    source="json_comm",
                    meta={"comm_id": comm_block.get("id")},
                )
            else:
                fallback = (
                    "Le informazioni richieste rientrano nei dati aziendali/commerciali. "
                    "Per sicurezza è necessario fare riferimento ai canali ufficiali Tecnaria."
                )
                return AnswerResponse(
                    answer=fallback,
                    source="json_comm_fallback",
                    meta={},
                )

        # 2) DOMANDE TECNICHE → CHATGPT GOLD TECNARIA
        gpt_answer = call_openai(SYSTEM_PROMPT_GOLD, question_raw, temperature=0.2)
        kb_block = match_from_kb(question_raw)
        kb_id = kb_block.get("id") if kb_block else None

        return AnswerResponse(
            answer=gpt_answer,
            source="chatgpt_gold_tecnaria",
            meta={
                "used_chatgpt": True,
                "kb_id": kb_id,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] /api/ask: {e}")
        return AnswerResponse(
            answer="Si è verificato un problema interno. Contatta l’Ufficio Tecnico Tecnaria.",
            source="error",
            meta={"exception": str(e)},
        )
