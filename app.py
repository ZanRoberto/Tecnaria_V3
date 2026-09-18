import os
import json
import re
import html
import secrets
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
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

# Identificativo documentale custodito esclusivamente tra le variabili riservate
# del servizio: non deve essere scritto nel codice o inviato al browser.
OPENAI_VECTOR_STORE_ID = os.getenv("OPENAI_VECTOR_STORE_ID", "").strip()
OPENAI_DOCUMENT_MODEL = (
    os.getenv("OPENAI_DOCUMENT_MODEL", "gpt-5.6-sol") or "gpt-5.6-sol"
).strip()

# Il motore NAR/SUP resta universale. Questi valori descrivono soltanto
# l'azienda e il patrimonio documentale collegati alla singola installazione.
DOCUMENT_CONTEXT = os.getenv(
    "DOCUMENT_CONTEXT",
    "Catalogo LAGO ELEMENTS February 2024 IT/EN",
).strip()
DOCUMENT_DISCLAIMER = os.getenv(
    "DOCUMENT_DISCLAIMER",
    (
        "I dati appartengono al catalogo LAGO ELEMENTS February 2024 IT/EN; "
        "prezzi e condizioni devono essere verificati commercialmente prima "
        "di formulare un'offerta definitiva."
    ),
).strip()

# Area amministrativa separata dall'interfaccia pubblica. Se la variabile non
# e' configurata, le relative pagine restano completamente disabilitate.
INDEX_ADMIN_TOKEN = os.getenv("INDEX_ADMIN_TOKEN", "").strip()
INDEX_WORK_DIR = Path(os.getenv("INDEX_WORK_DIR", "/tmp/narratore-indexer"))
INDEX_WORK_DIR.mkdir(parents=True, exist_ok=True)

client: Optional[OpenAI] = None
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)

# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(title="Narratore-Risponditore - LAGO ELEMENTS DEMO")

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
    previous_question: Optional[str] = None
    previous_answer: Optional[str] = None


class AnswerResponse(BaseModel):
    answer: str
    source: str
    meta: Dict[str, Any]


INDEX_JOB: Dict[str, Any] = {
    "state": "idle",
    "message": "Nessuna indicizzazione avviata.",
    "started_at": None,
    "finished_at": None,
    "output": None,
    "log": "",
}
INDEX_JOB_LOCK = threading.Lock()


def _admin_allowed(token: str) -> bool:
    return bool(INDEX_ADMIN_TOKEN) and secrets.compare_digest(
        str(token or ""), INDEX_ADMIN_TOKEN
    )


def _set_index_job(**values: Any) -> None:
    with INDEX_JOB_LOCK:
        INDEX_JOB.update(values)


def _run_index_job(pdf_path: Path, mode: str) -> None:
    output_path = INDEX_WORK_DIR / f"indice-{pdf_path.stem}-{int(time.time())}.md"
    checkpoint_path = output_path.with_suffix(".checkpoint.jsonl")
    command = [
        sys.executable,
        str(Path(BASE_DIR) / "document_indexer.py"),
        str(pdf_path),
        "--output",
        str(output_path),
        "--checkpoint",
        str(checkpoint_path),
        "--batch-size",
        "3",
    ]
    if mode == "pilot":
        command.extend(["--printed-pages", "31,36,43,51,52"])

    _set_index_job(
        state="running",
        message=(
            "Analisi campione in corso: pagine 31, 36, 43, 51 e 52."
            if mode == "pilot"
            else "Indicizzazione completa in corso."
        ),
        started_at=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        finished_at=None,
        output=None,
        log="",
    )
    try:
        completed = subprocess.run(
            command,
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=60 * 60 * 6,
            check=False,
        )
        combined = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
        if completed.returncode == 0 and output_path.exists():
            _set_index_job(
                state="completed",
                message="Nuovo indice generato. Ora deve essere verificato prima della sostituzione.",
                finished_at=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                output=str(output_path),
                log=combined[-12000:],
            )
        else:
            _set_index_job(
                state="failed",
                message="Indicizzazione non completata. L'indice attuale non e' stato modificato.",
                finished_at=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                log=combined[-12000:],
            )
    except Exception as exc:
        _set_index_job(
            state="failed",
            message="Indicizzazione interrotta. L'indice attuale non e' stato modificato.",
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            log=str(exc),
        )

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

SYSTEM_PROMPT_NARRATORE = """
Sei il Narratore di Tecnaria S.p.A.
Il tuo compito è leggere la descrizione di una situazione di cantiere o di un problema tecnico
e identificare:
1. Cosa è chiaro nella situazione descritta
2. Cosa manca per poter dare una risposta tecnica corretta
3. Il rischio principale se si procede senza i dati mancanti

Rispondi SEMPRE in italiano con questa struttura esatta:

SITUAZIONE: [cosa hai capito in 2-3 righe]
DATI MANCANTI: [lista puntata dei dati necessari]
RISCHIO: [cosa succede se si procede senza quei dati]
DOMANDA CRITICA: [UNA sola domanda — la più importante da fare adesso]

Sii diretto e tecnico. Mai vago. Mai generico.
"""

SYSTEM_PROMPT_SUPERRISPONDITORE = """
Sei il Superrisponditore tecnico di Tecnaria S.p.A.
Ricevi:
- La descrizione originale del cliente
- L'analisi del Narratore con i dati mancanti
- La domanda critica identificata

Il tuo compito è dare la risposta tecnica più completa possibile
basandoti sui dati disponibili, indicando chiaramente
cosa è certo e cosa richiede verifica in loco.

Regole:
1. Non inventare valori numerici — se non li conosci usa:
   "Verificare nelle istruzioni Tecnaria o con l'Ufficio Tecnico"
2. Distingui sempre tra dati certi e ipotesi
3. Se i dati sono insufficienti, spiega cosa raccogliere prima di procedere
4. Concludi sempre con i passi concreti successivi

Stile: tecnico, diretto, aziendale. Niente marketing.
"""


def is_situational(question: str) -> bool:
    """
    Rileva se la domanda descrive una situazione
    invece di fare una domanda tecnica diretta.
    """
    q = question.lower()
    triggers = [
        "ho un solaio", "abbiamo un solaio", "c'è un solaio",
        "ho un cantiere", "abbiamo un cantiere",
        "vorrei rinforzare", "voglio rinforzare", "devo rinforzare",
        "ho delle travi", "abbiamo delle travi",
        "il cliente ha", "il progettista chiede",
        "situazione", "caso", "problema",
        "ho solo", "abbiamo solo", "non abbiamo",
        "non so", "non siamo sicuri",
        "edificio", "palazzo", "capannone", "villa",
        "anni '", "anni 6", "anni 7", "anni 8", "anni 9",
        "struttura esistente", "struttura vecchia",
        "foto", "rilievo", "stratigrafia",
    ]
    return any(t in q for t in triggers)


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


DOCUMENT_RETRIEVAL_PROMPT = """
Sei il RICERCATORE DOCUMENTALE di un motore professionale universale.
L'ambiente documentale corrente e': {document_context}.

Usa esclusivamente i documenti collegati. Non usare memoria generale, web o supposizioni.
Il tuo compito non e' formulare la risposta finale, ma preparare un DOSSIER DI EVIDENZE
completo, verificabile e utile al Narratore.

METODO UNIVERSALE:
1. Comprendi se la richiesta e' una ricerca esatta, una spiegazione, un confronto oppure
   una richiesta di consiglio/soluzione.
2. Per una ricerca esatta verifica prima la corrispondenza esatta di codici e riferimenti.
3. Per confronti o consigli non fermarti al primo risultato compatibile: recupera piu'
   candidati pertinenti, fino a 8 quando disponibili.
4. Per ogni candidato riporta soltanto dati documentati: identificativo, descrizione,
   funzione/destinazione, caratteristiche, misure, condizioni, prezzo, documento e pagina.
5. Conserva le differenze importanti tra candidati. Non decidere che tutti i risultati che
   superano una soglia minima siano equivalenti.
6. Un limite massimo o minimo definisce l'ammissibilita', ma non autorizza a massimizzare o
   minimizzare automaticamente quel valore. Cerca varianti distribuite nell'intervallo
   ammissibile quando la preferenza dell'utente non e' esplicita.
7. Distingui un dato mancante da un dato non applicabile e da un dato contraddittorio.
8. Se fonti o versioni discordano, riportale entrambe con documento, pagina e versione.
9. Non combinare nella stessa affermazione valori provenienti da prodotti, righe o pagine
   differenti, salvo che il documento dichiari esplicitamente la relazione.
10. Non inventare mai numeri, caratteristiche, compatibilita', motivazioni o relazioni.
11. Indica sempre documento e pagina/riferimento quando disponibili.

Restituisci un dossier leggibile e neutrale. Non scegliere ancora il vincitore e non citare
mai strumenti, infrastruttura, API, modelli o identificativi tecnici.
"""

DOCUMENT_NARRATOR_PROMPT = """
Sei il NARRATORE ANALITICO di un motore universale. Lavori in qualsiasi settore e non devi
applicare regole fisse legate a uno specifico prodotto, documento o mercato.

Ricevi la richiesta originale e un dossier di sole evidenze documentali. Devi costruire
internamente la struttura decisionale prima che il Superrisponditore parli con l'utente.

METODO OBBLIGATORIO:
1. Classifica la richiesta: ricerca_esatta, spiegazione, confronto o raccomandazione.
2. Identifica l'obiettivo reale dell'utente.
3. Separa:
   - vincoli obbligatori;
   - preferenze e priorita';
   - valori approssimativi e tolleranze;
   - destinazione d'uso;
   - informazioni mancanti capaci di cambiare la decisione.
4. Valuta ogni candidato su quattro livelli:
   - AMMISSIBILITA': rispetta tutti i vincoli obbligatori?
   - PERTINENZA: soddisfa realmente il bisogno e la funzione richiesta?
   - OPPORTUNITA': e' preferibile alle alternative documentate?
   - DIMOSTRABILITA': ogni affermazione e' sostenuta da documento e riferimento?
5. Distingui sempre "formalmente compatibile" da "realmente consigliabile".
6. Non trasformare automaticamente un limite massimo/minimo in un valore obiettivo.
7. Ottimizza un valore soltanto se l'utente lo richiede esplicitamente o se la destinazione
   d'uso lo rende necessario sulla base di evidenze documentali.
8. Espressioni vaghe come "basso", "leggero", "economico" o "compatto" non significano
   automaticamente "il piu' basso", "il piu' leggero", "il meno caro" o "il piu' piccolo".
9. Non usare un singolo attributo per decidere quando la richiesta contiene piu' esigenze.
10. Penalizza dati mancanti, funzione incerta, scostamenti rilevanti e prove insufficienti.
11. Se piu' candidati ammissibili rappresentano usi o compromessi sostanzialmente diversi,
    individua una proposta provvisoria equilibrata, conserva le varianti e prepara come
    domanda critica quella che puo' cambiare la graduatoria. Non chiedere prima il budget
    se funzione, dimensione, prestazione o capacita' sono ancora indeterminate.
12. Se manca un dato decisivo, non simulare certezza: prepara una sola domanda critica.
13. Se non esiste una soluzione dimostrabile, dichiaralo invece di forzare una proposta.

Produci un'analisi interna concisa con: tipo richiesta, obiettivo, vincoli, preferenze,
candidati esclusi e motivo, graduatoria dei candidati ammissibili, scelta motivata,
alternative, dati mancanti, eventuale domanda critica e riferimenti documentali.
Non aggiungere conoscenze esterne e non citare l'infrastruttura.
"""

DOCUMENT_RESPONDER_PROMPT = """
Sei il SUPERRISPONDITORE DOCUMENTALE. Trasforma il lavoro del Narratore in una risposta
professionale, utile e comprensibile, valida in qualsiasi settore.

REGOLE OBBLIGATORIE:
1. Usa soltanto il dossier documentale e il controllo del Narratore.
2. Rispondi direttamente all'obiettivo dell'utente, non limitarti a ripetere parole trovate.
3. Per una ricerca esatta restituisci il dato esatto e la sua prova.
4. Per una raccomandazione presenta come principale soltanto una soluzione che superi
   ammissibilita', pertinenza, opportunita' e dimostrabilita'.
5. Spiega perche' la soluzione e' adatta e quali compromessi presenta.
6. Se utile, presenta alternative chiarendo quando sarebbero preferibili.
7. Non presentare come assolutamente migliore una soluzione quando una preferenza non
   dichiarata puo' cambiare la scelta. In quel caso chiamala "soluzione inizialmente piu'
   equilibrata" e mostra fino a tre varianti realmente differenti.
8. Non trasformare un limite in un obiettivo di massimizzazione o minimizzazione.
9. La domanda finale deve riguardare prima l'informazione che modifica maggiormente la
   scelta; budget e finitura vengono dopo funzione, compatibilita', prestazione, dimensione
   o capacita', salvo che l'utente abbia indicato il prezzo come priorita'.
10. Se manca un dato decisivo, comunica cio' che e' gia' certo e poni una sola domanda finale.
11. Se le fonti sono contraddittorie, mostra il conflitto senza scegliere arbitrariamente.
12. Se una informazione non e' documentata, scrivi: "Informazione non trovata nel documento collegato."
13. Riporta documento e pagina/riferimento per le affermazioni determinanti.
14. Non rimandare genericamente l'utente alla consultazione del documento: fornisci la
    soluzione e usa il riferimento come prova.
15. Non mostrare l'analisi interna e non usare parole come dossier, ranking interno,
    punteggio, pipeline o evidenze recuperate. Non citare Vector Store, File Search, OpenAI,
    modelli, embedding, API o identificativi tecnici.

Rispondi nella stessa lingua usata dall'utente, salvo sua diversa richiesta.
Mantieni invariati codici, prezzi, misure, unita', nomi propri e riferimenti.
Scrivi in testo semplice, senza Markdown e senza asterischi.
In chiusura aggiungi questa nota, senza modificarne il significato:
{document_disclaimer}
"""

DOCUMENT_QUICK_PROMPT = """
Sei il NARRATORE-RISPONDITORE DOCUMENTALE in modalita' RAPIDA GUIDATA.
L'ambiente documentale corrente e': {document_context}.

Usa esclusivamente i documenti collegati. Non usare memoria generale, web o supposizioni.
Devi comprendere il bisogno dell'utente, confrontare i risultati pertinenti e dare una risposta
utile in una sola elaborazione, senza mostrare il ragionamento interno.

METODO OBBLIGATORIO:
1. Separa vincoli tassativi, preferenze, valori approssimativi e destinazione d'uso.
2. Escludi i candidati che violano un vincolo tassativo.
3. Non trasformare automaticamente un limite massimo/minimo nel valore da massimizzare o
   minimizzare. Parole come basso, compatto o economico non significano automaticamente
   il piu' basso, il piu' piccolo o il meno caro.
4. Se l'utente dichiara di non avere ancora scelto tra due priorita' opposte (per esempio
   profilo molto basso oppure maggiore contenimento), NON scegliere uno dei due estremi.
   La proposta principale deve essere una variante intermedia documentata che conservi
   entrambe le possibilita'. Gli estremi vanno mostrati soltanto come alternative. Se non
   esiste una variante intermedia dimostrabile, poni la domanda decisiva invece di forzare
   una scelta estrema.
5. Scegli una proposta principale equilibrata valutando insieme funzione, misure,
   caratteristiche, prezzo e qualita' delle prove documentali.
6. Distingui sempre una soluzione formalmente compatibile da una realmente consigliabile.
7. Non combinare valori appartenenti a prodotti o pagine differenti.
8. Non inventare dati. Se un'informazione richiesta non e' documentata, scrivi:
   "Informazione non trovata nel documento collegato."
9. Riporta sempre documento e numero di pagina per la proposta principale e per le
   alternative. Un codice o il nome di una sezione non sostituiscono il numero di pagina.
   Se la pagina non e' presente nei risultati, dichiaralo esplicitamente.
10. Non rimandare l'utente a leggere il documento: dai direttamente la risposta.
11. Non citare strumenti, Vector Store, File Search, OpenAI, API, modelli o identificativi tecnici.
12. Non inventare misure interne, volume utile, numero di oggetti contenibili o dotazioni
    non dichiarate. Puoi confrontare normalmente le dimensioni esterne documentate. Parla
    dell'assenza delle misure interne soltanto quando quel dato e' davvero decisivo per la
    richiesta; in tal caso usa una frase semplice e concreta, senza formule tecniche.
13. Non definire una variante piu' bassa, alta, economica o capiente se i dati riportati
    sono uguali o non consentono il confronto.

FORMATO RAPIDO OBBLIGATORIO:
- Apri con una sola proposta principale: nome/codice, dati determinanti, prezzo se pertinente,
  documento e pagina.
- Spiega in massimo 4 punti perche' e' adatta e segnala il compromesso principale.
- Mostra al massimo 2 alternative, ciascuna in 2-3 righe, solo se davvero significative.
- Concludi con UNA domanda guidata che possa cambiare la scelta o avviare l'approfondimento.
- Non superare normalmente 260 parole. Evita tabelle estese e liste complete di tutte le finiture;
  fornisci gli altri dettagli soltanto se l'utente li chiede.
- Rispondi nella stessa lingua usata dall'utente, salvo sua diversa richiesta.
  Mantieni invariati codici, prezzi, misure, unita', nomi propri e riferimenti.
  Scrivi in testo semplice, senza Markdown e senza asterischi.

In chiusura aggiungi questa nota, senza modificarne il significato:
{document_disclaimer}
"""


def call_document_quick(
    question: str,
    previous_question: str = "",
    previous_answer: str = "",
) -> str:
    """Modalita' predefinita: recupero verificabile, poi risposta breve guidata."""
    if client is None:
        return "Il motore esterno non è disponibile (OPENAI_API_KEY mancante)."
    if not OPENAI_VECTOR_STORE_ID:
        return "Archivio documentale non configurato."

    try:
        # La ricerca viene separata dalla redazione della risposta. In questo modo
        # il modello non puo' sacrificare i candidati documentali per rispondere
        # velocemente o fermarsi alla prima corrispondenza.
        has_conversation_context = bool(previous_question and previous_answer)
        if has_conversation_context:
            retrieval_question = (
                "RICERCA DI APPROFONDIMENTO VINCOLATA. "
                "Verifica esclusivamente i prodotti, i codici e le varianti gia' "
                "presenti nella RISPOSTA PRECEDENTE. Non cercare prodotti sostitutivi. "
                "Mantieni tutti i vincoli della DOMANDA PRECEDENTE.\n\n"
                f"DOMANDA PRECEDENTE:\n{previous_question}\n\n"
                f"RISPOSTA PRECEDENTE:\n{previous_answer}\n\n"
                f"RICHIESTA ATTUALE:\n{question}"
            )
        else:
            retrieval_question = question

        dossier = call_document_retrieval(retrieval_question)
        if dossier.startswith("Si è verificato un errore"):
            return dossier

        quick_response_rules = """
MODALITA' RISPOSTA CONSIGLIATA:
- Produci una risposta breve, normalmente entro 300 parole.
- Apri con una sola proposta principale documentata.
- Se l'utente e' indeciso tra priorita' opposte, la proposta principale deve essere
  una soluzione intermedia documentata; gli estremi sono soltanto alternative.
- Riporta nome/codice, dati determinanti, prezzo pertinente, documento e pagina.
- Spiega in massimo quattro punti perche' e' adatta e il compromesso principale.
- Mostra al massimo due alternative realmente differenti.
- Non inventare misure o volume interni. Confronta normalmente le dimensioni esterne e
  menziona l'assenza delle misure interne soltanto se e' determinante per la scelta.
- Concludi con una sola domanda che possa cambiare concretamente la scelta.
- Scrivi in testo semplice, senza Markdown e senza asterischi.
- Se sono presenti DOMANDA PRECEDENTE e RISPOSTA PRECEDENTE, usale per risolvere
  riferimenti come "questa soluzione", "le alternative", "confrontale" o "approfondisci".
- La RICHIESTA ATTUALE resta sempre l'istruzione da eseguire. Non chiedere nuovamente
  codici o nomi gia' presenti nella RISPOSTA PRECEDENTE.
"""

        if has_conversation_context:
            quick_response_rules += """
MODALITA' PROSECUZIONE VINCOLATA:
- Confronta o approfondisci ESCLUSIVAMENTE i prodotti e i codici presenti nella
  RISPOSTA PRECEDENTE.
- Mantieni tutti i vincoli tassativi espressi nella DOMANDA PRECEDENTE.
- Non introdurre, proporre o sostituire altri prodotti, salvo richiesta esplicita
  dell'utente di cercare nuove alternative.
- Se un dato di uno dei prodotti non e' verificabile nelle evidenze, dichiaralo per
  quel dato specifico senza cambiare prodotto.
- Non trasformare la richiesta in una nuova selezione iniziale.
"""

        conversation_block = ""
        if has_conversation_context:
            conversation_block = (
                "DOMANDA PRECEDENTE (contiene anche i vincoli da mantenere):\n"
                f"{previous_question}\n\n"
                "RISPOSTA PRECEDENTE (insieme dei prodotti bloccato):\n"
                f"{previous_answer}\n\n"
            )

        response_input = (
            f"{conversation_block}"
            "RICHIESTA ATTUALE:\n"
            f"{question}\n\n"
            "EVIDENZE DOCUMENTALI RECUPERATE:\n"
            f"{dossier}\n\n"
            "Formula ora la risposta usando esclusivamente queste evidenze e rispettando "
            "l'eventuale insieme bloccato della risposta precedente."
        )
        response = client.responses.create(
            model=OPENAI_DOCUMENT_MODEL,
            instructions=(DOCUMENT_RESPONDER_PROMPT + quick_response_rules).format(
                document_disclaimer=DOCUMENT_DISCLAIMER,
            ),
            input=response_input,
            reasoning={"effort": "medium"},
            max_output_tokens=1100,
        )
        answer = (response.output_text or "").strip()
        return answer or "Informazione non trovata nel documento collegato."
    except Exception as e:
        print(f"[ERROR] risposta documentale rapida: {e}")
        return "Si è verificato un errore durante la ricerca documentale."


def call_document_retrieval(question: str) -> str:
    """Costruisce un dossier di evidenze, senza formulare la risposta finale."""
    if client is None:
        return "Il motore esterno non è disponibile (OPENAI_API_KEY mancante)."
    if not OPENAI_VECTOR_STORE_ID:
        return "Archivio documentale non configurato."

    try:
        response = client.responses.create(
            model=OPENAI_DOCUMENT_MODEL,
            instructions=DOCUMENT_RETRIEVAL_PROMPT.format(
                document_context=DOCUMENT_CONTEXT,
            ),
            input=question,
            tools=[{
                "type": "file_search",
                "vector_store_ids": [OPENAI_VECTOR_STORE_ID],
                "max_num_results": 30,
            }],
            include=["file_search_call.results"],
        )
        dossier = (response.output_text or "").strip()
        return dossier or "Informazione non trovata nel documento collegato."
    except Exception as e:
        print(f"[ERROR] ricerca documentale: {e}")
        return "Si è verificato un errore durante la ricerca documentale."


def is_document_followup(question: str) -> bool:
    """Riconosce una prosecuzione senza trasformare ogni nuova domanda in memoria."""
    q = normalize(question)
    markers = (
        "adesso", "ora confronta", "confronta le", "confronta i", "confrontale",
        "queste soluzioni", "questi prodotti", "le tre soluzioni", "i tre prodotti",
        "quella", "quello", "la prima", "la seconda", "la terza", "approfondisci",
        "appena proposto", "appena indicato", "senza chiedermi nuovamente",
        "tra queste", "tra questi", "delle alternative", "della soluzione",
    )
    return any(marker in q for marker in markers)


DOCUMENT_FULL_PROMPT = """
Sei il NARRATORE-SUPERRISPONDITORE DOCUMENTALE in modalita' ANALISI COMPLETA.
L'ambiente documentale corrente e': {document_context}.

Usa esclusivamente i documenti collegati. In un'unica elaborazione devi cercare, verificare,
confrontare e spiegare le soluzioni pertinenti, senza mostrare passaggi o ragionamenti interni.

REGOLE:
1. Separa vincoli tassativi, preferenze, tolleranze, destinazione d'uso e dati mancanti.
2. Escludi le soluzioni che violano vincoli tassativi e non confondere compatibilita'
   dimensionale con reale opportunita'.
3. Non trasformare limiti massimi o minimi in obiettivi automatici.
4. Se l'utente e' indeciso tra priorita' opposte, la proposta principale deve essere una
   soluzione intermedia documentata, non uno degli estremi. Mostra gli estremi come scenari
   alternativi. Se non esiste una soluzione intermedia dimostrabile, poni la domanda decisiva.
5. Presenta una proposta principale equilibrata e fino a 5 alternative realmente diverse,
   organizzate per scenario o priorita'. Evita varianti ridondanti.
6. Per ogni soluzione riporta codice, dati determinanti, prezzo pertinente, compromesso,
   nome del documento e numero di pagina.
7. Un codice o una sezione non sostituiscono la pagina. Se la pagina non e' disponibile,
   scrivi: "Numero di pagina non trovato nel documento collegato."
8. Non inventare dati e non combinare valori di prodotti o pagine differenti.
9. Non inventare misure interne, volume utile o dotazioni non dichiarate. Confronta
   normalmente le dimensioni esterne documentate e segnala l'assenza di misure interne
   soltanto quando e' realmente determinante per rispondere alla domanda.
10. Non definire una soluzione piu' bassa, alta, economica o capiente se i dati sono uguali
   o insufficienti per dimostrarlo.
11. Non usare conoscenze esterne, non rimandare genericamente al catalogo e non citare
    strumenti, Vector Store, File Search, OpenAI, API, modelli o identificativi tecnici.
12. Concludi con una sintesi netta e UNA domanda capace di cambiare la scelta.
13. Non superare normalmente 700 parole.

Rispondi nella stessa lingua usata dall'utente, salvo sua diversa richiesta.
Mantieni invariati codici, prezzi, misure, unita', nomi propri e riferimenti.
Scrivi in testo semplice, senza Markdown e senza asterischi.
In chiusura aggiungi questa nota, senza modificarne il significato:
{document_disclaimer}
"""


def call_narratore_risponditore(question: str) -> str:
    """Analisi completa in una sola chiamata documentale."""
    if client is None:
        return "Il motore esterno non è disponibile (OPENAI_API_KEY mancante)."
    if not OPENAI_VECTOR_STORE_ID:
        return "Archivio documentale non configurato."

    try:
        response = client.responses.create(
            model=OPENAI_DOCUMENT_MODEL,
            instructions=DOCUMENT_FULL_PROMPT.format(
                document_context=DOCUMENT_CONTEXT,
                document_disclaimer=DOCUMENT_DISCLAIMER,
            ),
            input=question,
            tools=[{
                "type": "file_search",
                "vector_store_ids": [OPENAI_VECTOR_STORE_ID],
                "max_num_results": 14,
            }],
            reasoning={"effort": "low"},
            max_output_tokens=2600,
        )
        answer = (response.output_text or "").strip()
        return answer or "Informazione non trovata nel documento collegato."
    except Exception as e:
        print(f"[ERROR] analisi documentale completa: {e}")
        return "Si è verificato un errore durante l'analisi documentale completa."

# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/admin/documenti", response_class=HTMLResponse)
async def document_admin() -> HTMLResponse:
    if not INDEX_ADMIN_TOKEN:
        raise HTTPException(status_code=404, detail="Area non configurata")
    page = """<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gestione documenti</title><style>
body{font-family:Arial,sans-serif;background:#121212;color:#f4f0e9;margin:0;padding:32px}
main{max-width:760px;margin:auto;background:#1d222b;border:1px solid #3c424d;border-radius:18px;padding:28px}
h1{margin-top:0}label{display:block;margin:18px 0 7px;font-weight:700}input,select,button{box-sizing:border-box;width:100%;padding:13px;border-radius:10px;border:1px solid #777;font-size:16px}
button{margin-top:22px;background:#eadbc7;color:#111;font-weight:800;cursor:pointer}.note{color:#c8c8c8;line-height:1.45}.safe{color:#71e7a1;font-weight:700}
</style></head><body><main>
<h1>Gestione documenti</h1>
<p class="safe">L'indice attualmente in uso non verrà cancellato o sostituito.</p>
<form action="/admin/documenti/avvia" method="post" enctype="multipart/form-data">
<label>Codice amministratore</label><input name="token" type="password" required autocomplete="off">
<label>Documento PDF</label><input name="document" type="file" accept="application/pdf,.pdf" required>
<label>Tipo di prova</label><select name="mode"><option value="pilot">Prova controllata sulle pagine campione</option><option value="full">Indicizzazione completa</option></select>
<button type="submit">Carica e avvia l'analisi</button></form>
<p class="note">Inizia con la prova controllata. Il risultato verrà verificato prima di qualsiasi sostituzione.</p>
</main></body></html>"""
    return HTMLResponse(page)


@app.post("/admin/documenti/avvia", response_class=HTMLResponse)
async def start_document_indexing(
    token: str = Form(...),
    mode: str = Form("pilot"),
    document: UploadFile = File(...),
) -> HTMLResponse:
    if not _admin_allowed(token):
        raise HTTPException(status_code=403, detail="Codice amministratore non valido")
    if mode not in {"pilot", "full"}:
        raise HTTPException(status_code=400, detail="Modalita' non valida")
    if INDEX_JOB.get("state") == "running":
        raise HTTPException(status_code=409, detail="Una indicizzazione e' gia' in corso")
    filename = Path(document.filename or "documento.pdf").name
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="E' richiesto un file PDF")

    destination = INDEX_WORK_DIR / f"{uuid.uuid4().hex}-{filename}"
    size = 0
    with destination.open("wb") as stream:
        while True:
            chunk = await document.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > 200 * 1024 * 1024:
                stream.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="PDF superiore a 200 MB")
            stream.write(chunk)
    await document.close()
    if size < 5 or destination.read_bytes()[:5] != b"%PDF-":
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Il file caricato non e' un PDF valido")

    threading.Thread(target=_run_index_job, args=(destination, mode), daemon=True).start()
    safe_token = html.escape(token, quote=True)
    return HTMLResponse(f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5;url=/admin/documenti/stato?token={safe_token}">
<style>body{{font-family:Arial;background:#121212;color:#fff;padding:40px}}main{{max-width:700px;margin:auto}}</style></head>
<body><main><h1>Documento ricevuto</h1><p>Analisi avviata in sicurezza.</p>
<p>Tra pochi secondi verrà mostrato lo stato dell'elaborazione.</p></main></body></html>""")


@app.get("/admin/documenti/stato", response_class=HTMLResponse)
async def document_indexing_status(token: str) -> HTMLResponse:
    if not _admin_allowed(token):
        raise HTTPException(status_code=403, detail="Codice amministratore non valido")
    with INDEX_JOB_LOCK:
        job = dict(INDEX_JOB)
    refresh = "<meta http-equiv='refresh' content='8'>" if job["state"] == "running" else ""
    download = ""
    if job.get("state") == "completed" and job.get("output"):
        download = f"<p><a href='/admin/documenti/risultato?token={html.escape(token, quote=True)}'>Scarica il nuovo indice da verificare</a></p>"
    return HTMLResponse(f"""<!doctype html><html lang="it"><head><meta charset="utf-8">{refresh}
<style>body{{font-family:Arial;background:#121212;color:#fff;padding:40px}}main{{max-width:800px;margin:auto}}pre{{white-space:pre-wrap;background:#07090c;padding:18px;border-radius:10px}}</style></head>
<body><main><h1>Stato: {html.escape(str(job.get('state')))}</h1>
<p>{html.escape(str(job.get('message')))}</p><p>Avvio: {html.escape(str(job.get('started_at') or '-'))}</p>
{download}<pre>{html.escape(str(job.get('log') or 'Elaborazione in corso...'))}</pre></main></body></html>""")


@app.get("/admin/documenti/risultato")
async def download_indexing_result(token: str) -> FileResponse:
    if not _admin_allowed(token):
        raise HTTPException(status_code=403, detail="Codice amministratore non valido")
    output = INDEX_JOB.get("output")
    if INDEX_JOB.get("state") != "completed" or not output or not Path(output).exists():
        raise HTTPException(status_code=404, detail="Risultato non disponibile")
    return FileResponse(output, filename=Path(output).name, media_type="text/markdown")

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
        "status": "Narratore-Risponditore LAGO attivo",
        "kb_blocks": len(KB_BLOCKS),
        "comm_blocks": len(COMM_ITEMS),
        "narratore_risponditore": "attivo",
    }


@app.post("/api/ask", response_model=AnswerResponse)
async def api_ask(req: QuestionRequest):
    """
    Tre modalità:
    1. COMM    — domande aziendali/commerciali → COMM.json
    2. ORACOLO — descrizioni situazionali → Narratore → Superrisponditore
    3. GOLD    — domande tecniche dirette → GPT GOLD
    """
    question_raw = (req.question or "").strip()
    if not question_raw:
        raise HTTPException(status_code=400, detail="Domanda vuota")

    q_norm = question_raw.lower()

    try:
        # MODALITA' DOCUMENTALE:
        # - /catalogo, /lago, /listino e /rapido: risposta rapida guidata (1 chiamata)
        # - /globale: analisi estesa completa (1 chiamata)
        document_prefix = next(
            (
                prefix
                for prefix in ("/catalogo", "/lago", "/listino", "/rapido", "/globale")
                if q_norm.startswith(prefix)
            ),
            None,
        )
        if document_prefix:
            document_question = question_raw[len(document_prefix):].strip()
            document_mode = "globale" if document_prefix == "/globale" else "rapido"

            # Consente anche: /catalogo /globale domanda...
            nested_mode = next(
                (
                    mode_prefix
                    for mode_prefix in ("/globale", "/rapido")
                    if document_question.lower().startswith(mode_prefix)
                ),
                None,
            )
            if nested_mode:
                document_mode = "globale" if nested_mode == "/globale" else "rapido"
                document_question = document_question[len(nested_mode):].strip()

            if not document_question:
                return AnswerResponse(
                    answer=(
                        "Scrivi la domanda dopo /catalogo. "
                        "Usa /globale soltanto quando desideri l'analisi completa."
                    ),
                    source="narratore_risponditore",
                    meta={"mode": document_mode},
                )

            previous_question = (req.previous_question or "").strip()
            previous_answer = (req.previous_answer or "").strip()
            has_saved_context = bool(previous_question and previous_answer)
            has_context = has_saved_context and (
                document_mode == "globale" or is_document_followup(document_question)
            )

            if document_mode == "globale":
                if has_context:
                    global_question = (
                        "CONFRONTO VINCOLATO: mantieni i prodotti e tutti i vincoli "
                        "della conversazione precedente. Non introdurre altri prodotti.\n\n"
                        f"DOMANDA PRECEDENTE:\n{previous_question}\n\n"
                        f"RISPOSTA PRECEDENTE:\n{previous_answer}\n\n"
                        f"RICHIESTA ATTUALE:\n{document_question}"
                    )
                else:
                    global_question = document_question
                document_answer = call_narratore_risponditore(global_question)
            else:
                document_answer = call_document_quick(
                    document_question,
                    previous_question=previous_question if has_context else "",
                    previous_answer=previous_answer if has_context else "",
                )

            return AnswerResponse(
                answer=document_answer,
                source="narratore_risponditore",
                meta={"mode": document_mode, "conversation_context_used": has_context},
            )

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
                return AnswerResponse(
                    answer=(
                        "Le informazioni richieste rientrano nei dati aziendali/commerciali. "
                        "Per sicurezza è necessario fare riferimento ai canali ufficiali Tecnaria."
                    ),
                    source="json_comm_fallback",
                    meta={},
                )

        # 2) DESCRIZIONE SITUAZIONALE → NARRATORE + SUPERRISPONDITORE
        if is_situational(question_raw):
            # Step 1: Narratore legge la situazione
            analisi_narratore = call_openai(
                SYSTEM_PROMPT_NARRATORE,
                question_raw,
                temperature=0.2
            )

            # Step 2: Superrisponditore risponde con contesto completo
            contesto_super = (
                f"DESCRIZIONE CLIENTE:\n{question_raw}\n\n"
                f"ANALISI NARRATORE:\n{analisi_narratore}\n\n"
                f"Ora dai la risposta tecnica completa."
            )
            risposta_super = call_openai(
                SYSTEM_PROMPT_SUPERRISPONDITORE,
                contesto_super,
                temperature=0.2
            )

            risposta_finale = (
                f"📋 ANALISI SITUAZIONE\n\n{analisi_narratore}"
                f"\n\n{'─' * 40}\n\n"
                f"💡 RISPOSTA TECNICA\n\n{risposta_super}"
            )

            return AnswerResponse(
                answer=risposta_finale,
                source="oracolo_narratore_superrisponditore",
                meta={
                    "narratore": analisi_narratore,
                    "superrisponditore": risposta_super,
                    "used_chatgpt": True,
                },
            )

        # 3) DOMANDE TECNICHE DIRETTE → CHATGPT GOLD TECNARIA
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
