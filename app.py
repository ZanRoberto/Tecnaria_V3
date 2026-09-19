import os
import json
import re
import math
from collections import Counter
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

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = (os.getenv("DEEPSEEK_MODEL", "deepseek-chat") or "deepseek-chat").strip()
DEEPSEEK_BASE_URL = (
    os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    or "https://api.deepseek.com"
).strip()

# Selettore del motore documentale:
# - deepseek_local: indice locale + DeepSeek
# - openai_vector: Vector Store + OpenAI
# - automatic: prova OpenAI Vector e, in caso di errore, passa a DeepSeek locale
SEARCH_ENGINE = (os.getenv("SEARCH_ENGINE", "deepseek_local") or "deepseek_local").strip().lower()
if SEARCH_ENGINE not in {"deepseek_local", "openai_vector", "automatic"}:
    print(f"[WARN] SEARCH_ENGINE non valido: {SEARCH_ENGINE}; uso deepseek_local")
    SEARCH_ENGINE = "deepseek_local"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_VECTOR_STORE_ID = os.getenv("OPENAI_VECTOR_STORE_ID", "").strip()
OPENAI_DOCUMENT_MODEL = (
    os.getenv("OPENAI_DOCUMENT_MODEL", "gpt-4o") or "gpt-4o"
).strip()

# Archivio locale indicizzato. Non dipende da OpenAI o da un Vector Store.
DOCUMENT_INDEX_PATH = os.path.join(DATA_DIR, "document_index_COMPLETO.txt")

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

# Interruttore commerciale: per impostazione predefinita la funzione non e'
# visibile. Su Render puo' essere attivata impostando il valore a "true".
ENABLE_COMMERCIAL_PROPOSAL = os.getenv(
    "ENABLE_COMMERCIAL_PROPOSAL", "false"
).strip().lower() in {"1", "true", "yes", "on"}

client: Optional[OpenAI] = None
if DEEPSEEK_API_KEY:
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

openai_client: Optional[OpenAI] = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)

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

# ============================================================
# NORMALIZZAZIONE TESTO
# ============================================================

def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\sàèéìòóùç]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ============================================================
# INDICE DOCUMENTALE LOCALE
# ============================================================

DOCUMENT_PAGES: List[Dict[str, Any]] = []

SEARCH_STOPWORDS = {
    "a", "ad", "al", "alla", "alle", "anche", "che", "con", "da", "dal",
    "dalla", "de", "dei", "del", "della", "delle", "di", "e", "ed", "gli",
    "ha", "i", "il", "in", "la", "le", "lo", "ma", "mi", "non", "o", "per",
    "piu", "quale", "quali", "questa", "questo", "sono", "su", "tra", "un",
    "una", "uno", "the", "and", "or", "of", "to", "for", "with", "is", "are",
    "this", "that", "from", "what", "which", "please", "indica", "proponi",
    "spiega", "soluzione", "alternative", "alternativa", "prodotto", "prodotti",
}


def search_tokens(text: str) -> List[str]:
    """Token robusti per codici, misure e termini in italiano/inglese."""
    folded = normalize(text.replace(",", "."))
    tokens = re.findall(r"[a-zàèéìòóùç0-9][a-zàèéìòóùç0-9_.-]*", folded)
    return [t for t in tokens if len(t) >= 2 and t not in SEARCH_STOPWORDS]


def load_document_index() -> None:
    global DOCUMENT_PAGES
    DOCUMENT_PAGES = []
    if not os.path.exists(DOCUMENT_INDEX_PATH):
        print(f"[WARN] indice documentale non trovato: {DOCUMENT_INDEX_PATH}")
        return

    try:
        with open(DOCUMENT_INDEX_PATH, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()

        marker = re.compile(r"(?m)^## PAGINA PDF\s+(\d+)\s*$")
        matches = list(marker.finditer(raw))
        for index, match in enumerate(matches):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
            page_text = raw[start:end].strip()
            page_number = int(match.group(1))
            page_token_list = search_tokens(page_text)
            DOCUMENT_PAGES.append({
                "page": page_number,
                "text": page_text,
                "normalized": normalize(page_text),
                "token_counts": Counter(page_token_list),
                "token_set": set(page_token_list),
            })

        print(
            f"[INFO] indice locale caricato: {len(DOCUMENT_PAGES)} pagine, "
            f"{len(raw)} caratteri"
        )
    except Exception as e:
        print(f"[ERROR] caricando indice locale: {e}")
        DOCUMENT_PAGES = []


def expand_multilingual_query(question: str) -> str:
    """Traduce solo le parole di ricerca quando la domanda usa un alfabeto non latino."""
    if not re.search(r"[\u0400-\u04ff\u0370-\u03ff\u0600-\u06ff]", question):
        return question
    if client is None:
        return question
    try:
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Converti la richiesta in una riga di parole chiave italiane e inglesi "
                        "utili per cercare in un catalogo bilingue. Conserva esattamente numeri, "
                        "misure e codici. Non rispondere alla domanda e non aggiungere spiegazioni."
                    ),
                },
                {"role": "user", "content": question},
            ],
            temperature=0.0,
            max_tokens=180,
        )
        keywords = (response.choices[0].message.content or "").strip()
        return f"{question}\n{keywords}" if keywords else question
    except Exception as e:
        print(f"[WARN] espansione multilingua non riuscita: {e}")
        return question


def retrieve_local_evidence(query: str, max_pages: int = 10) -> str:
    """Recupera localmente pagine verificabili senza servizi vettoriali esterni."""
    if not DOCUMENT_PAGES:
        return ""

    expanded = expand_multilingual_query(query)
    tokens = search_tokens(expanded)
    codes = set(re.findall(r"\b[A-Z0-9][A-Z0-9_-]{3,}\b", query.upper()))
    numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", query))

    token_df = {
        token: sum(1 for page in DOCUMENT_PAGES if token in page["token_set"])
        for token in set(tokens)
    }
    scored: List[tuple[float, int]] = []
    for idx, page in enumerate(DOCUMENT_PAGES):
        text_norm = page["normalized"]
        text_upper = page["text"].upper()
        score = 0.0

        for token in set(tokens):
            occurrences = page["token_counts"].get(token, 0)
            if occurrences:
                rarity = math.log((len(DOCUMENT_PAGES) + 1) / (token_df[token] + 1)) + 1
                weight = 3.0 if any(ch.isdigit() for ch in token) else 1.0
                score += weight * rarity * (1.0 + math.log(occurrences))

        for code in codes:
            if code in text_upper:
                score += 80.0
        for number in numbers:
            normalized_number = number.replace(",", ".")
            if normalized_number in page["token_set"]:
                score += 2.0

        phrase = normalize(query)
        if phrase and len(phrase) > 8 and phrase in text_norm:
            score += 100.0
        if score > 0:
            scored.append((score, idx))

    scored.sort(key=lambda item: item[0], reverse=True)
    selected: List[int] = []

    # Per confronti generici tra mobili TV recupera l'intera famiglia di schede prodotto.
    # Le misure richieste sono limiti: non devono essere cercate come valori esatti.
    query_norm = normalize(expanded)
    if re.search(r"\btv\b", query_norm):
        for idx, page in enumerate(DOCUMENT_PAGES):
            header = normalize(page["text"][:900])
            if "tv units" in header and "optional optionals" not in header:
                selected.append(idx)

    for score, idx in scored:
        if idx not in selected:
            selected.append(idx)
        if len(selected) >= max_pages and not re.search(r"\btv\b", query_norm):
            break

    # Per i codici esatti includi anche la pagina adiacente, spesso sede di optional/note.
    if codes:
        for _, idx in scored[:4]:
            for neighbour in (idx - 1, idx + 1):
                if 0 <= neighbour < len(DOCUMENT_PAGES) and neighbour not in selected:
                    selected.append(neighbour)
                if len(selected) >= max_pages + 2:
                    break

    if not selected:
        return ""

    blocks: List[str] = []
    total_chars = 0
    max_chars = 220000 if re.search(r"\btv\b", query_norm) else 70000
    for idx in selected:
        page = DOCUMENT_PAGES[idx]
        block = f"\n===== PAGINA PDF {page['page']} =====\n{page['text']}\n"
        if total_chars + len(block) > max_chars:
            remaining = max_chars - total_chars
            if remaining > 1500:
                blocks.append(block[:remaining])
            break
        blocks.append(block)
        total_chars += len(block)

    return "".join(blocks).strip()

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
load_document_index()

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


def call_deepseek(prompt_system: str, question: str, temperature: float = 0.3) -> str:
    """
    Wrapper unico per chiamare DeepSeek tramite API compatibile OpenAI.
    """
    if client is None:
        return "Il motore esterno non è disponibile (DEEPSEEK_API_KEY mancante)."

    try:
        completion = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": prompt_system},
                {"role": "user", "content": question},
            ],
            temperature=temperature,
            top_p=1.0,
        )
        return (completion.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[ERROR] chiamando DeepSeek: {e}")
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


def is_contextual_followup(question: str) -> bool:
    """Riconosce una domanda che dipende esplicitamente dalla risposta precedente."""
    q = normalize(question)
    markers = (
        "soluzione consigliata", "proposta consigliata", "soluzione precedente",
        "risposta precedente", "alternativa compatibile", "le alternative",
        "tra le soluzioni", "tra la soluzione", "tra i prodotti",
        "quella consigliata", "quello consigliato", "la prima", "la seconda",
        "il primo", "il secondo", "entrambe", "entrambi", "queste soluzioni",
        "questi prodotti", "approfondisci", "confrontale", "confrontali",
    )
    return any(marker in q for marker in markers)


def build_document_query(
    question: str,
    previous_question: str = "",
    previous_answer: str = "",
) -> tuple[str, bool]:
    """Aggiunge memoria soltanto quando la nuova domanda richiama il turno precedente."""
    has_memory = bool(previous_question.strip() and previous_answer.strip())
    followup = has_memory and is_contextual_followup(question)
    if not followup:
        return question, False

    query = (
        "DOMANDA ATTUALE:\n"
        f"{question}\n\n"
        "CONTESTO VINCOLANTE DEL TURNO PRECEDENTE:\n"
        f"Domanda precedente: {previous_question}\n"
        f"Risposta precedente: {previous_answer}\n\n"
        "La domanda attuale e' un approfondimento. Cerca e verifica gli stessi prodotti, "
        "codici e alternative nominati nella risposta precedente. Non sostituirli con altri "
        "prodotti, salvo richiesta esplicita dell'utente."
    )
    return query, True


CONSTRAINT_VALIDATOR_PROMPT = """
Sei il CONTROLLORE UNIVERSALE DEI VINCOLI di un sistema documentale professionale.
Ricevi la richiesta originale e una bozza di risposta gia' prodotta.

Devi restituire direttamente la risposta finale corretta, senza descrivere il controllo.

REGOLE ASSOLUTE:
1. Individua tutti i vincoli tassativi espressi dall'utente: massimi, minimi, intervalli,
   uguaglianze, quantita', dimensioni, peso, capacita', prezzo, date, caratteristiche,
   compatibilita', esclusioni e condizioni obbligatorie.
2. Controlla matematicamente ogni numero. Un valore superiore a un massimo o inferiore a
   un minimo e' incompatibile, anche quando lo scostamento e' piccolo.
3. Non trasformare mai una tolleranza in una deroga. Esempio generale: se il massimo e' X,
   qualsiasi valore maggiore di X deve essere escluso.
4. Applica ogni limite soltanto alla grandezza e all'unita' cui si riferisce. Non confondere
   larghezza, altezza, profondita', peso, prezzo, quantita' o altre proprieta'.
5. Se le unita' sono convertibili, convertile prima del confronto.
6. Elimina dalla proposta principale e dalle alternative ogni candidato che viola anche un
   solo vincolo tassativo.
7. Non inventare un sostituto, un codice, un prezzo o una caratteristica. Se la bozza non
   contiene piu' una soluzione sicuramente conforme, dichiaralo chiaramente e chiedi il dato
   necessario oppure indica che serve una nuova ricerca documentale.
8. Correggi anche frasi logicamente contraddittorie come "184 e' entro 180" o "rispetta tutti
   i limiti" quando i valori riportati dimostrano il contrario.
9. Conserva lingua, riferimenti documentali, disclaimer e informazioni corrette della bozza.
10. Non citare questo controllo, modelli, API, strumenti o infrastrutture.

La conformita' ai vincoli viene prima dell'eleganza della risposta.
"""


def validate_document_answer(
    question: str,
    draft_answer: str,
    provider: str,
) -> str:
    """Revisione strutturale universale prima di consegnare la risposta all'utente."""
    if not draft_answer.strip():
        return draft_answer

    validation_input = (
        "RICHIESTA ORIGINALE:\n"
        f"{question}\n\n"
        "BOZZA DA CONTROLLARE:\n"
        f"{draft_answer}\n\n"
        "Restituisci soltanto la risposta finale corretta."
    )
    try:
        if provider == "openai_vector":
            if openai_client is None:
                return draft_answer
            response = openai_client.responses.create(
                model=OPENAI_DOCUMENT_MODEL,
                instructions=CONSTRAINT_VALIDATOR_PROMPT,
                input=validation_input,
                max_output_tokens=1600,
            )
            checked = (response.output_text or "").strip()
        else:
            if client is None:
                return draft_answer
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": CONSTRAINT_VALIDATOR_PROMPT},
                    {"role": "user", "content": validation_input},
                ],
                temperature=0.0,
                max_tokens=1600,
            )
            checked = (response.choices[0].message.content or "").strip()
        return checked or draft_answer
    except Exception as e:
        print(f"[WARN] controllo universale dei vincoli non riuscito: {e}")
        return draft_answer


def call_document_quick_local(
    question: str,
    previous_question: str = "",
    previous_answer: str = "",
) -> str:
    """Modalita' predefinita: ricerca locale e risposta breve DeepSeek."""
    if client is None:
        return "Il motore esterno non è disponibile (DEEPSEEK_API_KEY mancante)."
    if not DOCUMENT_PAGES:
        return "Archivio documentale locale non configurato."

    try:
        document_query, is_followup = build_document_query(
            question, previous_question, previous_answer
        )
        dossier = retrieve_local_evidence(document_query, max_pages=10)
        if not dossier:
            return (
                "Informazione non trovata nel documento collegato.\n\n"
                + DOCUMENT_DISCLAIMER
            )

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
"""

        if is_followup:
            quick_response_rules += """
CONTINUITA' CONVERSAZIONALE OBBLIGATORIA:
- La richiesta attuale dipende dal turno precedente.
- Rispondi sugli stessi prodotti e codici presenti nella risposta precedente.
- Non introdurre o sostituire prodotti, famiglie o codici, salvo richiesta esplicita.
- Se un dato necessario non e' documentato, dichiaralo senza cambiare candidato.
- Mantieni validi i vincoli obbligatori gia' stabiliti dall'utente.
"""

        response_input = (
            "RICHIESTA ORIGINALE:\n"
            f"{document_query}\n\n"
            "EVIDENZE DOCUMENTALI RECUPERATE:\n"
            f"{dossier}\n\n"
            "Formula ora la risposta consigliata usando esclusivamente queste evidenze."
        )
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (DOCUMENT_RESPONDER_PROMPT + quick_response_rules).format(
                        document_disclaimer=DOCUMENT_DISCLAIMER,
                    ),
                },
                {"role": "user", "content": response_input},
            ],
            temperature=0.1,
            max_tokens=1300,
        )
        answer = (response.choices[0].message.content or "").strip()
        if not answer:
            return "Informazione non trovata nel documento collegato."
        return validate_document_answer(document_query, answer, "deepseek_local")
    except Exception as e:
        print(f"[ERROR] risposta documentale rapida: {e}")
        return "Si è verificato un errore durante la ricerca documentale."


def call_document_retrieval(question: str) -> str:
    """Compatibilita': restituisce direttamente le pagine recuperate localmente."""
    dossier = retrieve_local_evidence(question, max_pages=12)
    return dossier or "Informazione non trovata nel documento collegato."


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


def call_narratore_risponditore_local(
    question: str,
    previous_question: str = "",
    previous_answer: str = "",
) -> str:
    """Analisi completa: ricerca locale e una chiamata DeepSeek."""
    if client is None:
        return "Il motore esterno non è disponibile (DEEPSEEK_API_KEY mancante)."
    if not DOCUMENT_PAGES:
        return "Archivio documentale locale non configurato."

    try:
        document_query, is_followup = build_document_query(
            question, previous_question, previous_answer
        )
        full_instructions = DOCUMENT_FULL_PROMPT.format(
            document_context=DOCUMENT_CONTEXT,
            document_disclaimer=DOCUMENT_DISCLAIMER,
        )
        if is_followup:
            full_instructions += """
\nCONTINUITA' CONVERSAZIONALE OBBLIGATORIA:
La domanda attuale richiama il turno precedente. Mantieni gli stessi prodotti, codici,
alternative e vincoli gia' stabiliti. Non sostituirli con altri candidati salvo richiesta
esplicita. Se manca una prova documentale, dichiaralo senza cambiare prodotto.
"""

        dossier = retrieve_local_evidence(document_query, max_pages=14)
        if not dossier:
            return (
                "Informazione non trovata nel documento collegato.\n\n"
                + DOCUMENT_DISCLAIMER
            )
        response_input = (
            "RICHIESTA:\n"
            f"{document_query}\n\n"
            "ESTRATTI DOCUMENTALI CON PAGINE:\n"
            f"{dossier}\n\n"
            "Rispondi utilizzando esclusivamente gli estratti sopra riportati."
        )
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": full_instructions},
                {"role": "user", "content": response_input},
            ],
            temperature=0.1,
            max_tokens=3000,
        )
        answer = (response.choices[0].message.content or "").strip()
        if not answer:
            return "Informazione non trovata nel documento collegato."
        return validate_document_answer(document_query, answer, "deepseek_local")
    except Exception as e:
        print(f"[ERROR] analisi documentale completa: {e}")
        return "Si è verificato un errore durante l'analisi documentale completa."


def call_openai_vector_retrieval(question: str, max_results: int = 30) -> str:
    """Cerca le evidenze nel Vector Store OpenAI. Solleva l'errore per consentire il fallback."""
    if openai_client is None:
        raise RuntimeError("OPENAI_API_KEY mancante")
    if not OPENAI_VECTOR_STORE_ID:
        raise RuntimeError("OPENAI_VECTOR_STORE_ID mancante")

    response = openai_client.responses.create(
        model=OPENAI_DOCUMENT_MODEL,
        instructions=DOCUMENT_RETRIEVAL_PROMPT.format(
            document_context=DOCUMENT_CONTEXT,
        ),
        input=question,
        tools=[{
            "type": "file_search",
            "vector_store_ids": [OPENAI_VECTOR_STORE_ID],
            "max_num_results": max_results,
        }],
        include=["file_search_call.results"],
    )
    dossier = (response.output_text or "").strip()
    return dossier or "Informazione non trovata nel documento collegato."


def call_document_quick_vector(
    question: str,
    previous_question: str = "",
    previous_answer: str = "",
) -> str:
    """Risposta consigliata tramite OpenAI Vector Store."""
    document_query, is_followup = build_document_query(
        question, previous_question, previous_answer
    )
    dossier = call_openai_vector_retrieval(document_query, max_results=30)

    quick_response_rules = """
MODALITA' RISPOSTA CONSIGLIATA:
- Produci una risposta breve, normalmente entro 300 parole.
- Apri con una sola proposta principale documentata.
- Riporta nome/codice, dati determinanti, prezzo pertinente, documento e pagina.
- Spiega in massimo quattro punti perche' e' adatta e il compromesso principale.
- Mostra al massimo due alternative realmente differenti.
- Non inventare misure, prezzi, dotazioni o compatibilita'.
- Concludi con una sola domanda che possa cambiare concretamente la scelta.
- Scrivi in testo semplice, senza Markdown e senza asterischi.
"""
    if is_followup:
        quick_response_rules += """
CONTINUITA' CONVERSAZIONALE OBBLIGATORIA:
- Rispondi sugli stessi prodotti e codici presenti nella risposta precedente.
- Non sostituire prodotti o alternative salvo richiesta esplicita.
- Mantieni validi i vincoli obbligatori gia' stabiliti.
"""

    response_input = (
        "RICHIESTA ORIGINALE:\n"
        f"{document_query}\n\n"
        "EVIDENZE DOCUMENTALI RECUPERATE:\n"
        f"{dossier}\n\n"
        "Formula la risposta usando esclusivamente queste evidenze."
    )
    response = openai_client.responses.create(
        model=OPENAI_DOCUMENT_MODEL,
        instructions=(DOCUMENT_RESPONDER_PROMPT + quick_response_rules).format(
            document_disclaimer=DOCUMENT_DISCLAIMER,
        ),
        input=response_input,
        max_output_tokens=1300,
    )
    answer = (response.output_text or "").strip()
    if not answer:
        return "Informazione non trovata nel documento collegato."
    return validate_document_answer(document_query, answer, "openai_vector")


def call_narratore_risponditore_vector(
    question: str,
    previous_question: str = "",
    previous_answer: str = "",
) -> str:
    """Analisi completa tramite OpenAI Vector Store."""
    document_query, is_followup = build_document_query(
        question, previous_question, previous_answer
    )
    instructions = DOCUMENT_FULL_PROMPT.format(
        document_context=DOCUMENT_CONTEXT,
        document_disclaimer=DOCUMENT_DISCLAIMER,
    )
    if is_followup:
        instructions += """
\nCONTINUITA' CONVERSAZIONALE OBBLIGATORIA:
Mantieni gli stessi prodotti, codici, alternative e vincoli del turno precedente.
Non sostituirli salvo richiesta esplicita dell'utente.
"""

    response = openai_client.responses.create(
        model=OPENAI_DOCUMENT_MODEL,
        instructions=instructions,
        input=document_query,
        tools=[{
            "type": "file_search",
            "vector_store_ids": [OPENAI_VECTOR_STORE_ID],
            "max_num_results": 40,
        }],
        include=["file_search_call.results"],
        max_output_tokens=3000,
    )
    answer = (response.output_text or "").strip()
    if not answer:
        return "Informazione non trovata nel documento collegato."
    return validate_document_answer(document_query, answer, "openai_vector")


def active_document_engine() -> str:
    """Restituisce il motore effettivamente configurato, senza esporre chiavi o ID."""
    if SEARCH_ENGINE == "automatic":
        vector_ready = bool(openai_client and OPENAI_VECTOR_STORE_ID)
        return "automatic_vector_first" if vector_ready else "automatic_local_fallback"
    return SEARCH_ENGINE


def call_document_quick(
    question: str,
    previous_question: str = "",
    previous_answer: str = "",
) -> str:
    """Seleziona il motore rapido configurato e gestisce l'eventuale fallback."""
    if SEARCH_ENGINE == "deepseek_local":
        return call_document_quick_local(question, previous_question, previous_answer)
    try:
        return call_document_quick_vector(question, previous_question, previous_answer)
    except Exception as e:
        print(f"[ERROR] OpenAI Vector rapido: {e}")
        if SEARCH_ENGINE == "automatic":
            print("[INFO] fallback automatico a DeepSeek locale")
            return call_document_quick_local(question, previous_question, previous_answer)
        return "Si è verificato un errore durante la ricerca documentale."


def call_narratore_risponditore(
    question: str,
    previous_question: str = "",
    previous_answer: str = "",
) -> str:
    """Seleziona il motore completo configurato e gestisce l'eventuale fallback."""
    if SEARCH_ENGINE == "deepseek_local":
        return call_narratore_risponditore_local(
            question, previous_question, previous_answer
        )
    try:
        return call_narratore_risponditore_vector(
            question, previous_question, previous_answer
        )
    except Exception as e:
        print(f"[ERROR] OpenAI Vector completo: {e}")
        if SEARCH_ENGINE == "automatic":
            print("[INFO] fallback automatico a DeepSeek locale")
            return call_narratore_risponditore_local(
                question, previous_question, previous_answer
            )
        return "Si è verificato un errore durante l'analisi documentale completa."

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
        "status": "Narratore-Risponditore LAGO attivo",
        "kb_blocks": len(KB_BLOCKS),
        "comm_blocks": len(COMM_ITEMS),
        "document_pages": len(DOCUMENT_PAGES),
        "document_index_loaded": bool(DOCUMENT_PAGES),
        "engine": active_document_engine(),
        "engine_requested": SEARCH_ENGINE,
        "deepseek_ready": bool(client and DOCUMENT_PAGES),
        "openai_vector_ready": bool(openai_client and OPENAI_VECTOR_STORE_ID),
        "universal_constraint_validator": True,
        "narratore_risponditore": "attivo",
        "commercial_proposal_enabled": ENABLE_COMMERCIAL_PROPOSAL,
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
    previous_question = (req.previous_question or "").strip()
    previous_answer = (req.previous_answer or "").strip()
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

            if document_mode == "globale":
                document_answer = call_narratore_risponditore(
                    document_question, previous_question, previous_answer
                )
            else:
                document_answer = call_document_quick(
                    document_question, previous_question, previous_answer
                )

            return AnswerResponse(
                answer=document_answer,
                source="narratore_risponditore",
                meta={
                    "mode": document_mode,
                    "engine": active_document_engine(),
                    "used_previous_context": bool(
                        previous_question
                        and previous_answer
                        and is_contextual_followup(document_question)
                    ),
                },
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
            analisi_narratore = call_deepseek(
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
            risposta_super = call_deepseek(
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
                    "used_deepseek": True,
                },
            )

        # 3) DOMANDE TECNICHE DIRETTE → DEEPSEEK GOLD TECNARIA
        gpt_answer = call_deepseek(SYSTEM_PROMPT_GOLD, question_raw, temperature=0.2)
        kb_block = match_from_kb(question_raw)
        kb_id = kb_block.get("id") if kb_block else None

        return AnswerResponse(
            answer=gpt_answer,
            source="deepseek_gold_tecnaria",
            meta={
                "used_deepseek": True,
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
