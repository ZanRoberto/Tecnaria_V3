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

app = FastAPI(title="Tecnaria Bot v15.0")

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
    Matching semplice su COMM.json basato sui tag.
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
# LLM: PROMPT TECNARIA GOLD + MODALITÀ FURBA
# ============================================================

SYSTEM_PROMPT = """
Sei un tecnico–commerciale senior di Tecnaria S.p.A. con più di 20 anni di esperienza
su tutti i sistemi:
- CTF + P560 per solai misti acciaio–calcestruzzo
- VCEM / CTCEM per solai in laterocemento
- CTL / CTL MAXI per solai legno–calcestruzzo
- DIAPASON per travetti in laterocemento
- GTS, accessori e fissaggi correlati
- procedure di posa, verifica colpi, card, limiti, normativa, casi di non validità.

REGOLE OBBLIGATORIE E NON DEROGABILI:

1. Rispondi esclusivamente nel mondo Tecnaria S.p.A. Usare esempi, numeri, metodi o strumenti
   di altri produttori è vietato.

2. Per i CTF cita sempre la chiodatrice P560 e i “chiodi idonei Tecnaria”.

3. Per il sistema DIAPASON: NON utilizza chiodi. Si fissa con UNA vite strutturale in ogni piastra.
   Non citare mai P560 o chiodi in relazione ai DIAPASON.

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
   Se il dato non è certo, usa la frase:
   “Questo valore va verificato nelle istruzioni Tecnaria o con l’Ufficio Tecnico.”

6. Se invece il valore numerico è presente nella documentazione Tecnaria, DEVI riportarlo esattamente.
   Non usare formulazioni vaghe. Indica il numero con precisione.

7. Non inventare mai dati aziendali (indirizzo, P.IVA, SDI, telefono, nominativi).
   Se arrivano domande su questo, vengono gestite da un modulo COMM separato.

8. Stile della risposta:
   - tecnico-ingegneristico
   - chiaro, aziendale, senza marketing
   - se utile, usa elenchi puntati
   - spiega sempre perché la soluzione è corretta
   - evita frasi generiche tipo “dipende”: specifica sempre cosa dipende da cosa.

9. Se la domanda è fuori dal mondo Tecnaria, scrivi:
   “Il sistema risponde solo su prodotti, posa e applicazioni Tecnaria S.p.A.”

10. Se la domanda contiene un errore tecnico evidente, correggilo gentilmente
    e spiega la versione corretta.

Questo è un sistema GOLD: precisione massima, nessuna invenzione,
risposte chiare, determinate e ingegneristiche.
"""

FURBA_SYSTEM_PROMPT = """
Sei ChatGPT in modalità conversazionale generale.
Rispondi in modo chiaro, fluido e completo, come nell'interfaccia ufficiale di ChatGPT.
Non essere eccessivamente prudente: prova a dare sempre una risposta utile e ben formulata.
"""

def call_chatgpt(question: str) -> str:
    """
    Versione tecnica GOLD: profilo Tecnaria, prudente e coerente con le regole interne.
    """
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
        print(f"[ERROR] chiamando ChatGPT (GOLD): {e}")
        return (
            "Si è verificato un errore nella chiamata al motore esterno in modalità GOLD. "
            "Per sicurezza, contatta direttamente l'Ufficio Tecnico Tecnaria."
        )


def call_chatgpt_furba(question: str) -> str:
    """
    Versione 'furba': usa il modello in modo molto simile alla ChatGPT app,
    senza vincoli Tecnaria, massima fluidità e completezza.
    Utile per demo o confronto con ChatGPT 'puro'.
    """
    if client is None:
        return "Il motore ChatGPT esterno non è disponibile (OPENAI_API_KEY mancante)."

    try:
        completion = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": FURBA_SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            temperature=0.4,
            top_p=1.0,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        print(f"[ERROR] chiamando ChatGPT (furba): {e}")
        return "Si è verificato un errore nella chiamata al motore esterno in modalità furba."

# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/")
async def root() -> FileResponse:
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=500, detail="index.html non trovato")
    return FileResponse(index_path)


@app.get("/api/status")
async def status():
    return {
        "status": "Tecnaria Bot v15.0 attivo",
        "kb_blocks": len(KB_BLOCKS),
        "comm_blocks": len(COMM_ITEMS),
        "openai_enabled": bool(OPENAI_API_KEY),
        "model": OPENAI_MODEL,
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
        # 1️⃣ DOMANDE AZIENDALI / COMMERCIALI → SOLO COMM.JSON
        if is_commercial_question(q_norm):
            comm_block = match_comm(q_norm)
            if comm_block:
                answer = comm_block["response_variants"]["gold"]["it"]
                return AnswerResponse(
                    answer=answer,
                    source="json_comm",
                    meta={"comm_id": comm_block.get("id")}
                )
            else:
                fallback = (
                    "Le informazioni richieste rientrano nei dati aziendali/commerciali. "
                    "Non risultano però disponibili nel modulo corrente; per sicurezza "
                    "è necessario fare riferimento ai canali ufficiali Tecnaria."
                )
                return AnswerResponse(
                    answer=fallback,
                    source="json_comm_fallback",
                    meta={}
                )

        # 2️⃣ DOMANDE TECNICHE → CHATGPT GOLD TECNARIA
        gpt_answer = call_chatgpt(question_raw)

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


@app.post("/api/ask_furba", response_model=AnswerResponse)
async def api_ask_furba(req: QuestionRequest):
    """
    Endpoint 'furbo': usa direttamente il modello in stile ChatGPT app,
    senza passare da COMM o dalla KB Tecnaria.
    Default della nuova interfaccia.
    """
    question_raw = (req.question or "").strip()
    if not question_raw:
        raise HTTPException(status_code=400, detail="Domanda vuota")

    try:
        answer = call_chatgpt_furba(question_raw)
        return AnswerResponse(
            answer=answer,
            source="chatgpt_furba",
            meta={"used_chatgpt_furba": True},
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] /api/ask_furba: {e}")
        return AnswerResponse(
            answer="Si è verificato un problema interno in modalità furba.",
            source="error_furba",
            meta={"exception": str(e)},
        )
