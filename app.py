import os
import json
import hashlib
import re
import math
import time
from collections import Counter, OrderedDict
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
CATALOG_PDF_PATH = os.getenv(
    "CATALOG_PDF_PATH",
    os.path.join(STATIC_DIR, "catalogo.pdf"),
).strip()

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

app = FastAPI(title="Narratore-Risponditore")

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
    # Parole di servizio della domanda: descrivono COSA restituire, non COSA cercare.
    # Se restano nella ricerca premiano pagine casuali (es. "codice", "pagina", "diversi").
    "cerco", "cerca", "vorrei", "voglio", "serve", "servirebbe", "indicami", "dimmi",
    "spiegami", "mostrami", "elenca", "codice", "codici", "prezzo", "prezzi", "pagina",
    "pagine", "misura", "misure", "catalogo", "esigenza", "esigenze", "meglio", "risponde",
    "possibile", "versione", "versioni", "differenza", "differenze", "diversi", "diverse",
    "stesso", "stessa", "stessi", "stesse", "dello", "degli", "se", "più", "altri", "altre",
    "altro", "esistono", "esiste", "mio", "mia", "miei", "mie", "deve", "devono", "code",
    # unita' di misura: accompagnano un vincolo numerico, non identificano un prodotto
    "cm", "mm", "mt", "kg", "gr", "lt", "ml", "kw", "cm2", "mm2", "m2", "m3", "price", "page", "which", "best",
}

# Parole che introducono un'esclusione: i termini che seguono NON vanno cercati,
# altrimenti il motore premia proprio le pagine che contengono ciò che l'utente rifiuta
# (caso reale: "senza gambe o appoggi a terra" premiava la pagina dei "letti a terra").
NEGATION_CUES = {
    "senza", "non", "no", "né", "niente", "nessun", "nessuna", "nessuno",
    "escludi", "escluso", "esclusa", "eccetto", "tranne", "without", "not", "except",
}


def search_tokens(text: str) -> List[str]:
    """Token robusti per codici, misure e termini in italiano/inglese."""
    folded = normalize(text.replace(",", "."))
    tokens = re.findall(r"[a-zàèéìòóùç0-9][a-zàèéìòóùç0-9_.-]*", folded)
    return [t for t in tokens if len(t) >= 2 and t not in SEARCH_STOPWORDS]


def negated_search_tokens(text: str) -> set[str]:
    """Token alfabetici che l'utente esclude ("senza X", "non ... X") fino alla punteggiatura."""
    excluded: set[str] = set()
    for clause in re.split(r"[.;:!?,\n]", (text or "").lower()):
        words = re.findall(r"[a-zàèéìòóùç0-9]+", clause)
        for i, word in enumerate(words):
            if word in NEGATION_CUES:
                for follower in words[i + 1:i + 11]:
                    if not any(ch.isdigit() for ch in follower):
                        excluded.add(follower)
    return excluded


def compact_page_text(text: str) -> str:
    """Riduce gli spazi dell'impaginazione PDF: stesse informazioni, meta' dei caratteri."""
    lines = [re.sub(r"[ \t]{2,}", "  ", line).strip() for line in (text or "").splitlines()]
    return "\n".join(
        line for line in lines
        if line
        and not line.startswith("## PAGINA PDF")
        and not line.startswith("```")
        and not re.fullmatch(r"PAGINA_PDF\s*:\s*\S*", line)
    )


def _title_word_ok(word: str) -> bool:
    """Parola da titolo: contiene cifre (36e8, V16) o lettere in prevalenza maiuscole."""
    letters = [ch for ch in word if ch.isalpha()]
    if any(ch.isdigit() for ch in word):
        return bool(letters) or len(word) >= 2
    return bool(letters) and sum(ch.isupper() for ch in letters) / len(letters) >= 0.6


def page_title_line(page_text: str) -> str:
    """Prima riga di contenuto della pagina (dopo intestazioni e metadati dell'indice)."""
    for line in page_text.splitlines():
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith("## PAGINA PDF")
            or stripped.startswith("```")
            or re.fullmatch(r"[A-Z_]+\s*:\s*\S*", stripped)
        ):
            continue
        return stripped
    return ""


def page_family_key(page_text: str) -> Optional[tuple]:
    """Unita' documentale dal titolo di pagina, per qualunque impaginazione:
    'FLUTTUA BED' / 'FLUTTUA WILDWOOD BED' / 'FLUTTUA_BED' -> ('FLUTTUA', 'BED');
    '36e8 TV UNITS' e '2658    36e8 TV UNITS' -> ('36E8', 'UNITS'); 'N.O.W. TV UNITS' -> ('NOW', 'UNITS').
    Regole generali: salta metadati e numeri iniziali, taglia a separatori di colonna,
    accetta solo parole da titolo. Se non c'e' un titolo, decide la radice dei codici."""
    content_lines = []
    for line in page_text.splitlines():
        stripped = line.strip()
        # Salta intestazione e metadati dell'indice ("## PAGINA PDF 423",
        # "PAGINA_PDF: 423", "PAGINA_CATALOGO: 423", recinto ```text).
        if (
            not stripped
            or stripped.startswith("## PAGINA PDF")
            or stripped.startswith("```")
            or re.fullmatch(r"[A-Z_]+\s*:\s*\S*", stripped)
        ):
            continue
        content_lines.append(stripped)
        if len(content_lines) >= 3:
            break
    for line in content_lines:
        for segment in re.split(r"\s{2,}|//|\s\|\s|\s[-–]\s", line):
            words = segment.replace("_", " ").split()
            while words and re.fullmatch(r"[\d.,]+", words[0]):
                words.pop(0)  # quote o numeri davanti al titolo
            title_words: List[str] = []
            for word in words:
                if not _title_word_ok(word):
                    break
                title_words.append(re.sub(r"[^A-Z0-9]", "", word.upper()))
            title_words = [w for w in title_words if w]
            if not title_words or not re.search(r"[A-Z]", "".join(title_words)):
                continue
            if len("".join(title_words)) < 3:
                continue
            # singolare/plurale sono lo stesso prodotto (UNIT/UNITS, TABLE/TABLES)
            last = title_words[-1]
            if len(last) > 3 and last.endswith("S") and not last.endswith("SS"):
                last = last[:-1]
            return (title_words[0], last) if len(title_words) > 1 else (title_words[0],)
        # la prima riga con lettere decide: se non e' un titolo, niente titolo
        if re.search(r"[A-Za-z]{3,}", line):
            return None
    return None


def extract_document_codes(text: str) -> set[str]:
    """Estrae codici prodotto plausibili senza scambiare parole normali per codici."""
    candidates = re.findall(r"\b[A-Za-z0-9][A-Za-z0-9_-]{3,}\b", text or "")
    codes: set[str] = set()
    for candidate in candidates:
        # Un codice deve contenere almeno una cifra. I codici solo numerici
        # sono accettati da 5 cifre in su, evitando prezzi, anni e misure.
        if len(candidate) < 5:
            continue
        if not any(ch.isdigit() for ch in candidate):
            continue
        if candidate.isdigit() and len(candidate) < 5:
            continue
        # Una misura (160x200, 90X200X30) non e' un codice: prima veniva cercata come
        # codice e trascinava nel contesto tutte le pagine che la contengono.
        if MEASURE_PATTERN.fullmatch(candidate):
            continue
        codes.add(candidate.upper())
    return codes


MEASURE_PATTERN = re.compile(r"\d+(?:[.,]\d+)?(?:[xX×]\d+(?:[.,]\d+)?)+")
# Misure a 2 o 3 dimensioni in qualunque settore: 160x200, 60 x 120 x 30, 2,5x10.
SIZE_PATTERN = re.compile(
    # confini: niente cifra (o cifra+separatore decimale) prima, niente cifra (o separatore
    # decimale+cifra) dopo; la punteggiatura di fine frase ("180 x 200," / "200.") e' ammessa.
    r"(?<!\d)(?<!\d[.,])(\d{1,4}(?:[.,]\d+)?)\s*[xX×]\s*(\d{1,4}(?:[.,]\d+)?)"
    r"(?:\s*[xX×]\s*(\d{1,4}(?:[.,]\d+)?))?(?!\d)(?![.,]\d)"
)


def find_sizes(text: str) -> set:
    """Misure normalizzate ('60,5 X 120' -> '60.5x120')."""
    return {
        "x".join(part.replace(",", ".") for part in groups if part)
        for groups in SIZE_PATTERN.findall(text or "")
    }
CODE_TOKEN_PATTERN = re.compile(r"\b[A-Z][A-Z0-9_-]*\d[A-Z0-9_-]*\b")
CODE_SIZES: Dict[str, set] = {}


def code_root(code: str) -> str:
    """Radice alfabetica di un codice (FLU0450 -> FLU, CTF090 -> CTF)."""
    match = re.match(r"[A-Z]{2,}", code)
    return match.group(0) if match else ""


def assign_code_root_families() -> None:
    """Per le pagine senza titolo riconoscibile l'unita' documentale e' la radice di codice
    dominante sulla pagina. Cosi' il raggruppamento non dipende dall'impaginazione di un
    singolo editore: funziona con titoli in maiuscolo, con soli codici o con entrambi."""
    for page in DOCUMENT_PAGES:
        if page.get("family"):
            continue
        roots = Counter(code_root(c) for c in page.get("codes", set()) if code_root(c))
        if roots:
            root, count = roots.most_common(1)[0]
            if count >= 2:
                page["family"] = ("CODICI", root)


def build_code_sizes() -> None:
    """Associa a ogni codice le misure scritte sulla sua stessa riga di listino
    (es. 'Q 152 x 203 ... FLU0440' -> 152x203). Serve a filtrare i prodotti
    selezionabili quando l'utente ha fissato una misura."""
    CODE_SIZES.clear()
    for page in DOCUMENT_PAGES:
        for line in page["text"].splitlines():
            sizes = find_sizes(line)
            if len(sizes) != 1:
                continue  # riga ambigua: nessuna associazione
            for code in CODE_TOKEN_PATTERN.findall(line.upper()):
                if MEASURE_PATTERN.fullmatch(code) or len(code) < 5:
                    continue
                CODE_SIZES.setdefault(code, set()).update(sizes)


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
                "compact": compact_page_text(page_text),
                "codes": extract_document_codes(page_text),
                "family": page_family_key(page_text),
                "title_tokens": set(search_tokens(page_title_line(page_text))),
                "normalized": normalize(page_text),
                "token_counts": Counter(page_token_list),
                "token_set": set(page_token_list),
            })

        assign_code_root_families()
        build_code_sizes()
        PAGE_BY_NUMBER.clear()
        PAGE_BY_NUMBER.update({p["page"]: p for p in DOCUMENT_PAGES})
        build_code_rows()
        KNOWN_ROOTS.clear()  # ricalcolate al primo controllo, dopo il caricamento
        families = Counter(p["family"] for p in DOCUMENT_PAGES if p["family"])
        print(
            f"[INFO] indice locale caricato: {len(DOCUMENT_PAGES)} pagine, "
            f"{len(raw)} caratteri, {len(families)} famiglie di prodotto riconosciute, "
            f"{len(CODE_ROWS)} codici con riga di listino"
        )
    except Exception as e:
        print(f"[ERROR] caricando indice locale: {e}")
        DOCUMENT_PAGES = []


# ============================================================
# INDICE STRUTTURATO DELLE RIGHE DI LISTINO
# ============================================================
# Il testo estratto da un PDF impaginato separa le righe dalle intestazioni di colonna:
# "160 x 200  160  208  72  FLU0450  3.336 3.399 ..." non dice quale numero sia il prezzo.
# Per ogni codice si conserva la riga, l'ultima intestazione con "Codice/Code" e l'ultima
# descrizione che la precede. Serve a due cose, entrambe universali:
# 1) dare al modello righe leggibili (intestazione + descrizione + riga);
# 2) verificare in modo deterministico codici, prezzi e pagine citati nella risposta.

CODE_ROWS: Dict[str, List[Dict[str, Any]]] = {}
CODE_HEADER_PATTERN = re.compile(r"\b(?:codice|code|cod\.|art\.|articolo|item)\b", re.I)
NUMBER_TOKEN_PATTERN = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?")


def normalize_number(token: str) -> str:
    """'3.336' e '3336' sono lo stesso importo; '2,5' resta '2,5'."""
    return token.replace(".", "")


def row_numbers(text: str) -> set:
    return {normalize_number(n) for n in NUMBER_TOKEN_PATTERN.findall(text or "")}


def _is_description_line(line: str) -> bool:
    words = re.findall(r"[A-Za-zÀ-ÿ]{3,}", line)
    digits = sum(ch.isdigit() for ch in line)
    return len(words) >= 2 and digits <= len(line) * 0.2 and not CODE_HEADER_PATTERN.search(line)


def build_code_rows() -> None:
    CODE_ROWS.clear()
    for page in DOCUMENT_PAGES:
        header = ""
        description = ""
        title = re.sub(r"\s{2,}", "  ", page_title_line(page["text"]))
        previous_was_header = False
        page_lines = (page.get("compact") or "").splitlines()
        for line_index, line in enumerate(page_lines):
            is_header = CODE_HEADER_PATTERN.search(line) and not extract_document_codes(line)
            # righe di sole sigle di colonna ("A  B  C  P"): completano l'intestazione
            is_column_tags = bool(
                re.fullmatch(r"(?:[A-Z]{1,3}\*{0,2}\s+){1,8}[A-Z]{1,3}\*{0,2}", line)
                or re.search(r"(?:\s+[A-Z]{1,2}\*{0,2}){3,}\s*$", line)
            ) and not extract_document_codes(line)
            if is_header or (is_column_tags and previous_was_header):
                # intestazioni consecutive (italiano, sigle, inglese) si sommano
                header = f"{header} | {line}" if previous_was_header else line
                previous_was_header = True
                continue
            previous_was_header = False
            codes = extract_document_codes(line)
            if not codes:
                if _is_description_line(line):
                    description = line
                continue
            for code in codes:
                CODE_ROWS.setdefault(code, []).append({
                    "page": page["page"],
                    "title": title,
                    "header": header,
                    "description": description,
                    "text": line,
                    "numbers": row_numbers(line),
                    # nei PDF impaginati un prezzo puo' scivolare sulla riga accanto
                    "near_numbers": row_numbers(" ".join(
                        page_lines[max(0, line_index - 1):line_index + 2]
                    )),
                })


def code_pages(code: str) -> set:
    return {row["page"] for row in CODE_ROWS.get(code, [])}


def format_code_rows(codes: List[str], max_chars: int = 14000) -> str:
    """Righe leggibili per il modello: pagina, titolo, intestazione, descrizione, riga."""
    blocks: List[str] = []
    total = 0
    seen = set()
    for code in codes:
        for row in CODE_ROWS.get(code, []):
            key = (code, row["page"], row["text"])
            if key in seen:
                continue
            seen.add(key)
            block = (
                f"- {code} | pagina {row['page']} | {row['title'][:80]}\n"
                f"  colonne: {row['header'][:220]}\n"
                f"  descrizione: {row['description'][:160]}\n"
                f"  riga: {row['text'][:260]}"
            )
            if total + len(block) > max_chars:
                return "\n".join(blocks)
            blocks.append(block)
            total += len(block)
    return "\n".join(blocks)


def codes_in_evidence(dossier: str) -> List[str]:
    """Codici presenti nelle pagine recuperate, nell'ordine in cui compaiono."""
    ordered: List[str] = []
    for page_number in re.findall(r"===== PAGINA PDF (\d+) =====", dossier):
        page = PAGE_BY_NUMBER.get(int(page_number))
        if not page:
            continue
        for line in (page.get("compact") or "").splitlines():
            for code in extract_document_codes(line):
                if code in CODE_ROWS and code not in ordered:
                    ordered.append(code)
    return ordered


PAGE_BY_NUMBER: Dict[int, Dict[str, Any]] = {}
KNOWN_ROOTS: set = set()


SEARCH_PLANNER_PROMPT = """
Sei il PIANIFICATORE di un motore documentale universale.
Documento collegato: {document_context}.
Ricevi la richiesta di un utente ed eventualmente la sua domanda precedente.
NON rispondere alla domanda. Restituisci SOLO un oggetto JSON con queste chiavi:
- "termini": una riga di parole chiave (massimo 25) con cui un documento di quel settore
  descrive le soluzioni che soddisfano la richiesta, in italiano e nelle altre lingue del
  documento: la categoria della soluzione; il nome tecnico delle funzioni espresse a parole
  comuni (esempi di altri settori: "non deve temere la pioggia" -> IP65 impermeabile
  waterproof; "si monta senza forare" -> fissaggio adesivo, adhesive; "regge un carico
  elevato" -> portata, load capacity); taglie o classi commerciali tradotte nei valori
  standard del settore; numeri, misure e codici della richiesta invariati. NON includere
  le parole escluse o negate dall'utente ne' parole di servizio (codice, prezzo, pagina).
- "categoria": le sole parole che nominano il TIPO di prodotto richiesto, in italiano e
  in inglese (esempio: "lampada lamp"). Stringa vuota se la richiesta non nomina un tipo.
- "intento": uno tra "ricerca_esatta" (dati di un codice o di un prodotto nominato),
  "raccomandazione" (trovare la soluzione adatta a un'esigenza), "confronto" (mettere a
  confronto soluzioni nominate), "spiegazione" (come funziona, cosa significa).
- "seguito": true se la richiesta prosegue la domanda precedente (sceglie, modifica o
  approfondisce una soluzione gia' discussa), false se e' una richiesta nuova.
"""

PLAN_CACHE: "OrderedDict[tuple, Dict[str, Any]]" = OrderedDict()
PLAN_INTENTS = {"ricerca_esatta", "raccomandazione", "confronto", "spiegazione"}


def plan_request(question: str, previous_question: str = "") -> Dict[str, Any]:
    """Una sola chiamata breve (T=0) che produce lessico, categoria, intento e seguito.
    In caso di errore restituisce un piano vuoto: il motore deterministico resta attivo."""
    empty = {"termini": "", "categoria": "", "intento": "", "seguito": None}
    key = (question[:3000], previous_question[:800])
    if key in PLAN_CACHE:
        PLAN_CACHE.move_to_end(key)
        return PLAN_CACHE[key]
    if client is None:
        return empty
    user_content = question[:3000]
    if previous_question:
        user_content = f"DOMANDA PRECEDENTE: {previous_question[:800]}\n\nRICHIESTA: {user_content}"
    try:
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SEARCH_PLANNER_PROMPT.format(
                    document_context=DOCUMENT_CONTEXT)},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            max_tokens=260,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        try:
            data = json.loads(raw)
        except Exception:
            data = {"termini": raw}  # risposta non JSON: la si usa come lessico
        plan = {
            "termini": " ".join(str(data.get("termini", "")).split())[:600],
            "categoria": " ".join(str(data.get("categoria", "")).split())[:200],
            "intento": data.get("intento") if data.get("intento") in PLAN_INTENTS else "",
            "seguito": data.get("seguito") if isinstance(data.get("seguito"), bool) else None,
        }
    except Exception as e:
        print(f"[WARN] pianificazione non riuscita: {e}")
        return empty
    PLAN_CACHE[key] = plan
    if len(PLAN_CACHE) > 256:
        PLAN_CACHE.popitem(last=False)
    return plan


def plan_search_terms(question: str) -> str:
    """Compatibilita': solo il lessico del piano."""
    return plan_request(question).get("termini", "")


def expand_multilingual_query(question: str) -> str:
    """Compatibilita': restituisce domanda + lessico del catalogo pianificato."""
    terms = plan_search_terms(question)
    return f"{question}\n{terms}" if terms else question


FAMILY_BREADTH = 8            # quante unita' documentali diverse proporre come alternative
FAMILY_MAX_PAGES = 25          # oltre questa soglia il titolo non identifica un prodotto
LOCAL_CONTEXT_MAX_CHARS = 60000


def retrieve_local_evidence(
    query: str, max_pages: int = 10, planned_terms: Optional[str] = None
) -> str:
    """Recupera localmente pagine verificabili senza servizi vettoriali esterni.

    Principi universali (validi per qualunque documento e settore):
    1. i termini che l'utente esclude ("senza X", "non X") non vengono cercati;
    2. le parole di servizio della domanda (codice, prezzo, pagina...) non pesano;
    3. un pianificatore traduce il bisogno espresso a parole comuni nel lessico tecnico
       del documento collegato;
    4. l'unita' documentale piu' pertinente (stesso prodotto: listino, versioni, schede
       tecniche) viene recuperata intera, perche' i dettagli decisivi stanno spesso nelle
       schede tecniche e non nella pagina del listino.
    """
    if not DOCUMENT_PAGES:
        return ""

    if planned_terms is None:
        planned_terms = plan_search_terms(query)
    # le esclusioni valgono per cio' che chiede l'utente, non per il testo della risposta
    # precedente che accompagna un seguito ("non ha contenitore" non esclude "contenitore")
    excluded = negated_search_tokens(query.split("Risposta precedente:")[0])
    question_tokens = [t for t in search_tokens(query) if t not in excluded]
    planned_tokens = [t for t in search_tokens(planned_terms) if t not in excluded]
    codes = extract_document_codes(query)
    numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", query + " " + planned_terms))

    # Il lessico pianificato vale piu' delle parole libere del cliente.
    token_weights: Dict[str, float] = {}
    for token in question_tokens:
        token_weights[token] = max(token_weights.get(token, 0.0), 1.0)
    for token in planned_tokens:
        token_weights[token] = max(token_weights.get(token, 0.0), 1.6)

    token_df = {
        token: sum(1 for page in DOCUMENT_PAGES if token in page["token_set"])
        for token in token_weights
    }
    scored: List[tuple[float, int]] = []
    phrase = normalize(query)
    for idx, page in enumerate(DOCUMENT_PAGES):
        score = 0.0
        for token, base_weight in token_weights.items():
            occurrences = page["token_counts"].get(token, 0)
            if occurrences:
                rarity = math.log((len(DOCUMENT_PAGES) + 1) / (token_df[token] + 1)) + 1
                # Numeri puri (180, 72) sono vincoli da verificare, non l'identita' di cio'
                # che si cerca: pesano poco. Identificativi alfanumerici (36e8, dn50) pesano molto.
                if re.fullmatch(r"[\d.,]+", token):
                    weight = base_weight * 0.5
                elif any(ch.isdigit() for ch in token):
                    weight = base_weight * 3.0
                else:
                    weight = base_weight
                score += weight * rarity * (1.0 + math.log(occurrences))
                # Il titolo dice che cosa e' la pagina: una parola cercata nel titolo
                # identifica il prodotto, la stessa parola nel corpo e' solo un dettaglio.
                if token in page.get("title_tokens", ()):
                    score += 3.0 * weight * rarity
        score += 80.0 * len(codes & page.get("codes", set()))
        for number in numbers:
            if number.replace(",", ".") in page["token_set"]:
                score += 2.0
        if phrase and len(phrase) > 8 and phrase in page["normalized"]:
            score += 100.0
        if score > 0:
            scored.append((score, idx))
    scored.sort(key=lambda item: item[0], reverse=True)

    selected: List[int] = []

    def add(idx: int) -> None:
        if 0 <= idx < len(DOCUMENT_PAGES) and idx not in selected:
            selected.append(idx)

    query_norm = normalize(query + " " + planned_terms)
    if codes:
        # Approfondimento su codici esatti (tipico secondo turno).
        # 1) pagine che contengono i codici, prima quelle che ne contengono di piu';
        matches = []
        for idx, page in enumerate(DOCUMENT_PAGES):
            hits = len(codes & page.get("codes", set()))
            if hits:
                matches.append((hits, idx))
        matches.sort(key=lambda item: (-item[0], item[1]))
        for _, idx in matches:
            add(idx)
        # 2) la famiglia di quei prodotti, tavole tecniche comprese: i dettagli costruttivi
        #    (quote, appoggi, montaggio) non stanno sulla pagina del listino;
        family_sizes = Counter(p.get("family") for p in DOCUMENT_PAGES if p.get("family"))
        for _, idx in matches[:3]:
            fam = DOCUMENT_PAGES[idx].get("family")
            if fam and family_sizes[fam] <= FAMILY_MAX_PAGES:
                for other_idx, other in enumerate(DOCUMENT_PAGES):
                    if other.get("family") == fam:
                        add(other_idx)
        # 3) poi i punteggi.
        for _, idx in scored:
            if len(selected) >= max(max_pages, 30):
                break
            add(idx)
        max_chars = LOCAL_CONTEXT_MAX_CHARS
    else:
        family_pages: Dict[tuple, List[int]] = {}
        for idx, page in enumerate(DOCUMENT_PAGES):
            if page.get("family"):
                family_pages.setdefault(page["family"], []).append(idx)

        # Categoria richiesta: parole della domanda (o del lessico) che nel documento
        # compaiono nei titoli di piu' pagine, cioe' nomi di categoria ("TV", "BED", ...).
        # Le alternative si cercano prima dentro quella categoria, poi altrove.
        category_tokens = {
            token for token in token_weights
            if not re.fullmatch(r"[\d.,]+", token)
            and sum(1 for page in DOCUMENT_PAGES if token in page.get("title_tokens", ())) >= 2
        }

        def in_category(fam: tuple) -> bool:
            return any(
                category_tokens & DOCUMENT_PAGES[i].get("title_tokens", set())
                for i in family_pages.get(fam, [])
            )

        # Ordine delle famiglie secondo la miglior pagina di ciascuna.
        family_order: List[tuple] = []
        best_page_of_family: Dict[tuple, int] = {}
        for _, idx in scored:
            fam = DOCUMENT_PAGES[idx].get("family")
            if not fam or len(family_pages.get(fam, [])) > FAMILY_MAX_PAGES:
                continue
            if fam not in best_page_of_family:
                best_page_of_family[fam] = idx
                family_order.append(fam)
        if category_tokens:
            family_order = (
                [f for f in family_order if in_category(f)]
                + [f for f in family_order if not in_category(f)]
            )
        family_order = family_order[:FAMILY_BREADTH]

        # 1) ampiezza: la pagina migliore delle prime famiglie (alternative reali);
        for fam in family_order:
            add(best_page_of_family[fam])
        # 2) profondita': la prima famiglia intera, versioni e tavole tecniche comprese;
        if family_order:
            for idx in family_pages[family_order[0]]:
                add(idx)
        # 3) poi il resto per punteggio.
        for _, idx in scored:
            add(idx)
            if len(selected) >= max(max_pages, 30):
                break
        max_chars = LOCAL_CONTEXT_MAX_CHARS

    if not selected:
        return ""

    blocks: List[str] = []
    total_chars = 0
    for idx in selected:
        page = DOCUMENT_PAGES[idx]
        body = page.get("compact") or page["text"]
        block = f"\n===== PAGINA PDF {page['page']} =====\n{body}\n"
        if total_chars + len(block) > max_chars:
            continue  # prova le pagine successive, piu' corte
        blocks.append(block)
        total_chars += len(block)

    print(
        "[RETRIEVAL] pagine="
        + ",".join(str(DOCUMENT_PAGES[i]["page"]) for i in selected[:40])
        + f" esclusi={sorted(excluded)[:12]} lessico='{planned_terms[:160]}'"
    )
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
14. Classifica ogni candidato identificato in quattro stati:
    - VERIFICATO: tutti i requisiti obbligatori sono provati;
    - VERIFICA NECESSARIA: il candidato esiste, nessuna prova lo rende incompatibile, ma
      uno o piu' requisiti non sono documentati;
    - INCOMPATIBILE: almeno un dato documentato viola un requisito;
    - NON IDENTIFICATO: esistenza o identita' non sono dimostrate.
    Non confondere mai assenza di informazione con incompatibilita'.

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
16. Non dedurre la superiorita' di un prodotto da caratteristiche non collegate direttamente
    alla richiesta. Per esempio, una minore altezza esterna non dimostra automaticamente
    maggiore ventilazione, capacita', accessibilita' o migliore gestione elettronica.
17. Se mancano i dati decisivi per stabilire un vincitore su un criterio richiesto, dichiaralo
    chiaramente. Non assegnare comunque un vincitore arbitrario.
18. Distingui sempre quattro stati universali, senza regole legate a una marca o settore:
    - VERIFICATO: esistenza e tutti i requisiti obbligatori sono documentati;
    - VERIFICA NECESSARIA: il prodotto esiste ed e' identificato, nessun dato documentato
      viola i requisiti, ma manca la prova di uno o piu' requisiti;
    - INCOMPATIBILE: almeno un dato documentato viola un requisito obbligatorio;
    - NON IDENTIFICATO: mancano codice o riferimento sufficienti a provare l'esistenza.
    Un dato mancante non equivale mai a un dato contrario.
19. Nelle richieste di scelta, confronto o raccomandazione aggiungi una sezione intitolata
    esattamente "PRODOTTI SELEZIONABILI PER LA PROPOSTA". Inserisci i prodotti VERIFICATI
    e quelli in VERIFICA NECESSARIA. Per ciascuno indica nome, codice, stato e, se necessario,
    i requisiti ancora da confermare. Non inserire prodotti INCOMPATIBILI o NON IDENTIFICATI.
    I prodotti in VERIFICA NECESSARIA possono entrare soltanto in una proposta preliminare,
    che deve riportare chiaramente le verifiche ancora aperte.
20. Se un solo prodotto e' chiaramente preferibile e documentato, scrivi anche:
    "PRODOTTO CONSIGLIATO E SELEZIONABILE PER LA PROPOSTA: [nome e codice]".
    Se invece il criterio decisivo non e' documentato, presenta i prodotti esistenti senza
    fingere una superiorita' e chiedi quale portare in proposta preliminare.


REGOLE UNIVERSALI DI FEDELTA' AL BISOGNO:
A. ESCLUSIONI. Se l'utente esclude un elemento ("senza X", "niente X"), cercalo in TUTTE le
   pagine del prodotto, comprese tavole tecniche, disegni quotati e note di montaggio.
   Se X e' presente in ogni candidato, NON dichiarare il requisito soddisfatto: scrivi che
   nessun prodotto documentato lo rispetta integralmente, proponi quello che lo avvicina di
   piu' e indica con precisione cosa resta di X, con la pagina che lo prova.
B. TAGLIE E CLASSI. Quando l'utente usa una denominazione di taglia o classe (formato,
   taglia, portata, diametro nominale, classe di misura commerciale), la proposta principale
   deve appartenere a quella classe; non proporre mai una classe diversa da quella richiesta.
   Se la corrispondenza tra denominazione e valori non e' scritta nel documento, dichiaralo
   in una frase, usa la corrispondenza standard del settore e mostra le altre classi disponibili.
C. VERSIONI. Pagine con lo stesso nome base di prodotto e codici con la stessa radice sono
   versioni dello stesso prodotto: presentale come versioni (per esempio accessori, altezze,
   finiture, portate), spiegando cosa cambia, e non come prodotti diversi.
D. GRANDEZZE. Rispondi sulla grandezza chiesta. Non ricavare una grandezza da un'altra non
   collegata (l'ingombro totale non e' la quota di un componente, la portata di un elemento
   non e' quella dell'insieme). Cerca la quota anche nelle schede e nei disegni tecnici;
   se non esiste, dichiaralo.
E. COMPONENTI NOMINATI. Un componente citato in tabelle, legende, disegni o note di
   montaggio (per esempio "foro su X", "fissaggio di X", "regolazione di X") esiste nel
   prodotto: dichiaralo come presente con la pagina che lo prova. Non trasformarlo in dubbio
   e non chiedere di verificarlo.
F. CALCOLI DIRETTI. Se il documento fornisce i dati per un calcolo immediato (massimo o minimo,
   numero di posizioni, passo, somma di elementi), esegui il calcolo e mostra il procedimento
   in una riga, indicando le pagine dei dati usati.
G. DOMANDA FINALE. Tutte le verifiche possibili sul documento vanno fatte nella risposta.
   La domanda finale riguarda soltanto una scelta dell'utente (versione, misura, finitura,
   priorita'), mai una verifica che il sistema dovrebbe fare da solo.
H. ESCLUSO O NON INDICATO. Scrivi che un prodotto NON ha una caratteristica soltanto se il
   documento lo dice esplicitamente, citando la dicitura e la pagina. Se il documento tace,
   scrivi "non indicato nel documento" e tieni i due gruppi separati: non riunirli mai nella
   stessa frase o nello stesso elenco.
I. RICERCA ESATTA. Se la richiesta chiede i dati di un codice o di un prodotto nominato,
   rispondi con i dati richiesti e la pagina che li prova (per i prezzi, anche la pagina
   delle condizioni che ne definisce la natura, se presente negli estratti). Niente
   "perche' e' adatta", niente compromessi e niente domanda di scelta: l'utente ha gia'
   scelto. Le versioni dello stesso prodotto si possono citare in una riga. Nella sezione
   PRODOTTI SELEZIONABILI indica soltanto il codice richiesto.
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
    explicit_markers = (
        "soluzione consigliata", "proposta consigliata", "soluzione precedente",
        "risposta precedente", "alternativa compatibile", "le alternative",
        "tra le soluzioni", "tra la soluzione", "tra i prodotti",
        "quella consigliata", "quello consigliato", "la prima", "la seconda",
        "il primo", "il secondo", "entrambe", "entrambi", "queste soluzioni",
        "questi prodotti", "prodotti appena individuati", "prodotti individuati",
        "soluzioni appena individuate", "soluzioni individuate", "prodotti trovati",
        "soluzioni trovate", "quelli trovati", "quelle trovate", "sopra indicati",
        "sopra indicate", "appena indicati", "appena indicate", "stessi codici",
        "classificane", "verificali", "verificale", "approfondisci",
        "confrontale", "confrontali",
    )
    if any(marker in q for marker in explicit_markers):
        return True

    # Continuazioni brevi tipiche di una conversazione: "e per 180x200?", "invece in noce?",
    # "quanto costa?". Si applica solo quando esiste un turno precedente (lo verifica
    # build_document_query), quindi una domanda breve isolata resta una domanda nuova.
    words = q.split()
    if words and words[0] in {"e", "ed", "ma", "invece", "allora", "anche", "oppure", "and"}:
        return True
    if len(words) <= 5 and not extract_document_codes(question):
        return True
    pronouns = {"questo", "questa", "questi", "queste", "quello", "quella", "quelli", "quelle"}
    if len(words) <= 8 and pronouns & set(words):
        return True

    # Riconoscimento universale di riferimenti anaforici: funziona con prodotti,
    # procedure, documenti, soluzioni o codici di qualunque settore.
    reference_words = (
        "appena", "precedente", "precedenti", "sopra", "questo", "questa",
        "questi", "queste", "quello", "quella", "quelli", "quelle",
        "ciascuno", "ciascuna", "entrambi", "entrambe", "stesso", "stessa",
        "stessi", "stesse", "suddetto", "suddetta", "suddetti", "suddette",
    )
    referenced_objects = (
        "prodotto", "prodotti", "soluzione", "soluzioni", "alternativa",
        "alternative", "codice", "codici", "risposta", "risultato", "risultati",
        "documento", "documenti", "procedura", "procedure", "proposta", "proposte",
        "versione", "versioni", "variante", "varianti",
    )
    return (
        any(word in q.split() for word in reference_words)
        and any(obj in q.split() for obj in referenced_objects)
    )


def build_document_query(
    question: str,
    previous_question: str = "",
    previous_answer: str = "",
    planner_followup: Optional[bool] = None,
) -> tuple[str, bool]:
    """Aggiunge memoria soltanto quando la nuova domanda richiama il turno precedente.
    Seguito = frasi di richiamo esplicite OPPURE giudizio del pianificatore."""
    has_memory = bool(previous_question.strip() and previous_answer.strip())
    followup = has_memory and (is_contextual_followup(question) or planner_followup is True)
    if not followup:
        return question, False

    # La memoria serve per continuita', ma non deve trasformarsi in un nuovo catalogo.
    # Conserviamo integralmente la domanda precedente (dove sono espressi i vincoli)
    # e limitiamo la risposta precedente alla parte utile per codici e scelte.
    compact_previous_question = previous_question[:2500]
    compact_previous_answer = previous_answer[:5000]

    query = (
        "DOMANDA ATTUALE:\n"
        f"{question}\n\n"
        "CONTESTO VINCOLANTE DEL TURNO PRECEDENTE:\n"
        f"Domanda precedente: {compact_previous_question}\n"
        f"Risposta precedente: {compact_previous_answer}\n\n"
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
6. Elimina completamente dalla proposta principale e dalle alternative ogni candidato che
   viola anche un solo vincolo tassativo. Non citarlo, non elencarlo e non usarlo come
   alternativa negativa, salvo che l'utente chieda espressamente quali candidati sono stati
   esclusi e perche'.
7. Non inventare un sostituto, un codice, un prezzo o una caratteristica. Se restano meno
   prodotti VERIFICATI di quanti richiesti, conserva anche i prodotti esistenti in VERIFICA
   NECESSARIA e indica con precisione quali requisiti restano da confermare. Non dichiarare
   che non esiste alcuna soluzione quando esistono prodotti identificati senza prove
   documentali di incompatibilita'.
8. Correggi anche frasi logicamente contraddittorie come "184 e' entro 180" o "rispetta tutti
   i limiti" quando i valori riportati dimostrano il contrario.
9. Conserva lingua, riferimenti documentali, disclaimer e informazioni corrette della bozza.
10. Non citare questo controllo, modelli, API, strumenti o infrastrutture.
11. Elimina motivazioni non dimostrate o logicamente scollegate. Una differenza di altezza,
    larghezza, profondita' o prezzo non prova da sola migliore ventilazione, accessibilita',
    capacita' interna o gestione degli apparecchi.
12. Se la bozza attribuisce un vincitore ma i dati decisivi richiesti non sono documentati,
    sostituisci quella conclusione con una dichiarazione di non determinabilita'.
13. Classifica ogni candidato identificato come VERIFICATO, VERIFICA NECESSARIA,
    INCOMPATIBILE o NON IDENTIFICATO. Un'informazione mancante produce VERIFICA NECESSARIA,
    non INCOMPATIBILE. Usa INCOMPATIBILE soltanto quando esiste una prova documentale contraria.
14. Nella sezione "PRODOTTI SELEZIONABILI PER LA PROPOSTA" conserva sia i candidati
    VERIFICATI sia quelli in VERIFICA NECESSARIA. Per questi ultimi indica esattamente cosa
    resta da confermare e specifica che la proposta e' preliminare. Escludi soltanto i
    candidati INCOMPATIBILI o NON IDENTIFICATI.

15. Non sostituire il prodotto principale con un altro prodotto e non cambiarne la misura:
    non disponi delle pagine del documento, quindi puoi solo correggere, non rimpiazzare.
16. Conserva integralmente le frasi che dichiarano un requisito NON rispettato o solo in parte
    (per esempio un elemento escluso dall'utente ma presente nel prodotto): sono corrette.

La conformita' ai vincoli viene prima dell'eleganza della risposta.
"""


# La bozza dell'analisi completa arriva fino a 3000 token: con 1000 il controllo finale
# la troncava. Il limite deve essere almeno pari a quello della bozza.
VALIDATOR_MAX_TOKENS = 3200


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
                max_output_tokens=VALIDATOR_MAX_TOKENS,
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
                max_tokens=VALIDATOR_MAX_TOKENS,
            )
            checked = (response.choices[0].message.content or "").strip()
        return checked or draft_answer
    except Exception as e:
        print(f"[WARN] controllo universale dei vincoli non riuscito: {e}")
        return draft_answer


# ============================================================
# FLUSSO UNIFICATO DEL NARRATORE-SUPERRISPONDITORE
# ============================================================
# piano (lessico, categoria, intento, seguito) -> ricerca -> righe di listino ->
# risposta nel formato dell'intento -> controllo vincoli (solo se ci sono limiti numerici)
# -> controllo documentale deterministico con correzione mirata.

FORMAT_RACCOMANDAZIONE = """
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
- Non dedurre migliore ventilazione, accessibilita', capacita' o gestione elettronica da
  semplici differenze nelle dimensioni esterne, salvo esplicita prova documentale.
- Se i dati richiesti per scegliere un vincitore non sono documentati, dichiaralo e non
  scegliere arbitrariamente.
- Classifica i candidati come VERIFICATO, VERIFICA NECESSARIA, INCOMPATIBILE o NON IDENTIFICATO.
  Un requisito non documentato significa VERIFICA NECESSARIA, non incompatibilita'.
- Nelle richieste di scelta o confronto termina con la sezione esatta
  "PRODOTTI SELEZIONABILI PER LA PROPOSTA". Elenca nome, codice e stato dei candidati
  VERIFICATI e di quelli in VERIFICA NECESSARIA; per questi ultimi indica cosa resta da
  confermare nella proposta preliminare. Non elencare INCOMPATIBILI o NON IDENTIFICATI.
- Chiedi quale prodotto l'utente desidera portare in proposta.
- Scrivi in testo semplice, senza Markdown e senza asterischi.
"""

FORMAT_ESATTA = """
FORMATO RICERCA ESATTA:
- Rispondi subito con i dati richiesti del codice o del prodotto nominato: descrizione,
  misure, varianti di prezzo, prezzo, pagina.
- Usa le RIGHE DI LISTINO per attribuire ogni numero alla sua colonna.
- Versioni dello stesso prodotto: al massimo una riga.
- Niente motivazioni di adeguatezza, compromessi, alternative o domande di scelta.
- Chiudi con la sezione "PRODOTTI SELEZIONABILI PER LA PROPOSTA" con il solo codice
  richiesto, stato VERIFICATO.
- Scrivi in testo semplice, senza Markdown e senza asterischi.
"""

FORMAT_CONFERMA = """
FORMATO CONFERMA DI UNA SCELTA:
- L'utente ha scelto una versione di una soluzione gia' discussa: individua il codice che
  corrisponde esattamente alle scelte indicate (misura, versione, finitura) e confermalo
  con dati, prezzo e pagina, usando le RIGHE DI LISTINO.
- Rispondi a ogni domanda aggiuntiva cercando anche nelle schede tecniche della stessa
  famiglia di prodotto.
- Nessuna alternativa, salvo che la combinazione scelta non esista: in quel caso dillo e
  indica la combinazione documentata piu' vicina.
- Chiudi con "PRODOTTI SELEZIONABILI PER LA PROPOSTA" con il solo codice scelto e
  nessuna domanda di scelta.
- Scrivi in testo semplice, senza Markdown e senza asterischi.
"""

FORMAT_CONFRONTO = """
FORMATO CONFRONTO:
- Confronta le soluzioni nominate sugli stessi attributi, con dati e pagina di ciascuna.
- Evidenzia le differenze; non proclamare un vincitore se l'utente non ha dato un criterio.
- Chiudi con "PRODOTTI SELEZIONABILI PER LA PROPOSTA" con le soluzioni confrontate e UNA
  domanda sul criterio di scelta dell'utente.
- Scrivi in testo semplice, senza Markdown e senza asterischi.
"""

FORMAT_SPIEGAZIONE = """
FORMATO SPIEGAZIONE:
- Spiega con i dati del documento, citando le pagine, in modo chiaro e ordinato.
- La sezione dei prodotti selezionabili serve solo se la spiegazione riguarda prodotti
  da acquistare.
- Scrivi in testo semplice, senza Markdown e senza asterischi.
"""

FOLLOWUP_RULES = """
CONTINUITA' CONVERSAZIONALE OBBLIGATORIA:
- La richiesta attuale dipende dal turno precedente.
- Resta sugli stessi prodotti e sulla stessa famiglia della risposta precedente.
- Non introdurre o sostituire prodotti, famiglie o codici, salvo richiesta esplicita.
- Mantieni validi i vincoli obbligatori gia' stabiliti dall'utente.
"""

FORMAT_BY_INTENT = {
    "ricerca_esatta": FORMAT_ESATTA,
    "conferma": FORMAT_CONFERMA,
    "confronto": FORMAT_CONFRONTO,
    "spiegazione": FORMAT_SPIEGAZIONE,
    "raccomandazione": FORMAT_RACCOMANDAZIONE,
}

CHOICE_PATTERN = re.compile(
    r"\b(?:scelgo|sceglierei|voglio|vorrei|prendo|preferisco|cambio|invece|stessa|stesso|"
    r"confermo|opto|va bene)\b",
    re.I,
)
COMPARE_PATTERN = re.compile(
    r"\b(?:confront\w*|differenz\w*|rispetto a|meglio tra|versus|vs\.?)\b", re.I
)
NUMERIC_LIMIT_PATTERN = re.compile(
    r"\b(?:al massimo|massim[oa]|max|non (?:superi|superare|oltre|piu' di|più di)|entro|"
    r"almeno|minim[oa]|min\.?|fino a|inferiore|superiore|meno di|oltre|tra \d+ e \d+)\b|[<>≤≥]",
    re.I,
)
VALIDATOR_MODE = (os.getenv("VALIDATOR_MODE", "auto") or "auto").strip().lower()


def needs_constraint_validation(question: str, previous_question: str, is_followup: bool) -> bool:
    """Il controllo LLM dei vincoli serve quando la richiesta fissa limiti numerici."""
    if VALIDATOR_MODE == "always":
        return True
    if VALIDATOR_MODE != "auto":
        return False
    text = question + (" " + previous_question if is_followup else "")
    return bool(NUMERIC_LIMIT_PATTERN.search(text))


def detect_intent(question: str, plan: Dict[str, Any], is_followup: bool) -> str:
    """Priorita' ai segnali deterministici, poi al pianificatore. Il confronto viene
    prima: "differenze tra X e Y" o "vorrei confrontare" non sono ne' ricerca ne' scelta."""
    if COMPARE_PATTERN.search(question):
        return "confronto"
    if is_followup and (find_sizes(question) or CHOICE_PATTERN.search(question)):
        return "conferma"
    if not is_followup and extract_document_codes(question) & set(CODE_ROWS):
        return "ricerca_esatta"
    return plan.get("intento") or "raccomandazione"


def absent_category_note(plan: Dict[str, Any]) -> str:
    """Se nessuna parola che nomina il tipo di prodotto compare nel documento, il prodotto
    non c'e': lo si dichiara al modello prima che scriva, per evitare sostituzioni."""
    tokens = [t for t in search_tokens(plan.get("categoria", "")) if not t.isdigit()]
    if not tokens or not DOCUMENT_PAGES:
        return ""
    def stem(token: str) -> str:
        return token[:-1] if len(token) >= 5 else token

    present = [
        t for t in tokens
        if any(any(w.startswith(stem(t)) for w in p["token_set"]) for p in DOCUMENT_PAGES)
    ]
    if present:
        return ""
    return (
        "\n\nTIPO DI PRODOTTO NON PRESENTE: le parole che indicano il tipo di prodotto "
        f"richiesto ({', '.join(tokens)}) non compaiono in nessuna pagina del documento. "
        "Dichiara che il prodotto non e' presente nel documento collegato. Non proporre "
        "prodotti di altro tipo come sostituti; puoi citare al massimo una categoria affine "
        "presente, dichiarandola esplicitamente come prodotto diverso."
    )


def run_local_pipeline(
    question: str, previous_question: str, previous_answer: str, mode: str,
    followup_hint: Optional[bool] = None,
) -> str:
    if client is None:
        return "Il motore esterno non è disponibile (DEEPSEEK_API_KEY mancante)."
    if not DOCUMENT_PAGES:
        return "Archivio documentale locale non configurato."
    try:
        started = time.perf_counter()
        has_memory = bool(previous_question.strip() and previous_answer.strip())
        plan = plan_request(question, previous_question if has_memory else "")
        plan_seconds = time.perf_counter() - started

        document_query, is_followup = build_document_query(
            question, previous_question, previous_answer,
            followup_hint if followup_hint is not None else plan.get("seguito"),
        )
        intent = detect_intent(question, plan, is_followup)

        retrieval_started = time.perf_counter()
        dossier = retrieve_local_evidence(
            document_query, max_pages=14 if mode == "globale" else 10,
            planned_terms=plan.get("termini", ""),
        )
        retrieval_seconds = time.perf_counter() - retrieval_started
        if not dossier:
            return "Informazione non trovata nel documento collegato.\n\n" + DOCUMENT_DISCLAIMER

        # righe leggibili: prima i codici citati da utente e turno precedente
        priority = [c for _, c in codes_mentioned(question + " " + (
            previous_answer if is_followup else ""))]
        row_codes = list(dict.fromkeys(
            [c for c in priority if c in CODE_ROWS] + codes_in_evidence(dossier)
        ))
        rows_text = format_code_rows(row_codes, 12000)

        if mode == "globale" and intent in {"raccomandazione", "confronto"}:
            system_prompt = DOCUMENT_FULL_PROMPT.format(
                document_context=DOCUMENT_CONTEXT, document_disclaimer=DOCUMENT_DISCLAIMER
            )
            max_tokens = 3000
        else:
            system_prompt = (DOCUMENT_RESPONDER_PROMPT + FORMAT_BY_INTENT[intent]).format(
                document_disclaimer=DOCUMENT_DISCLAIMER
            )
            max_tokens = 3000 if mode == "globale" else 1500
        if is_followup:
            system_prompt += FOLLOWUP_RULES

        response_input = (
            f"RICHIESTA ORIGINALE:\n{document_query}\n\n"
            f"TIPO DI RICHIESTA: {intent}"
            + dimension_constraint_note(question, previous_question, is_followup)
            + absent_category_note(plan)
            + (f"\n\nRIGHE DI LISTINO CON INTESTAZIONI DI COLONNA (usale per attribuire "
               f"correttamente ogni numero):\n{rows_text}" if rows_text else "")
            + f"\n\nEVIDENZE DOCUMENTALI (pagine complete):\n{dossier}\n\n"
            "Formula ora la risposta usando esclusivamente queste evidenze."
        )
        generation_started = time.perf_counter()
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": response_input}],
            temperature=0.1,
            max_tokens=max_tokens,
        )
        answer = strip_markdown_emphasis((response.choices[0].message.content or "").strip())
        generation_seconds = time.perf_counter() - generation_started
        if not answer:
            return "Informazione non trovata nel documento collegato."

        needs_validation = needs_constraint_validation(question, previous_question, is_followup)
        validation_started = time.perf_counter()
        if needs_validation:
            validation_request = question
            if is_followup and previous_question:
                validation_request = (
                    "VINCOLI STABILITI NELLA DOMANDA PRECEDENTE:\n"
                    f"{previous_question[:2500]}\n\nDOMANDA ATTUALE:\n{question}"
                )
            answer = strip_markdown_emphasis(
                validate_document_answer(validation_request, answer, "deepseek_local")
            )
        validation_seconds = time.perf_counter() - validation_started

        control_started = time.perf_counter()
        answer = apply_fact_control(answer, "deepseek_local")
        control_seconds = time.perf_counter() - control_started
        print(
            f"[TIMING] {mode} intento={intent} seguito={is_followup} "
            f"piano={plan_seconds:.2f}s ricerca={retrieval_seconds:.2f}s "
            f"stesura={generation_seconds:.2f}s "
            f"vincoli={'si' if needs_validation else 'no'} {validation_seconds:.2f}s "
            f"controllo={control_seconds:.2f}s "
            f"totale={time.perf_counter() - started:.2f}s pagine_chars={len(dossier)} "
            f"righe={len(row_codes)}"
        )
        return answer
    except Exception as e:
        print(f"[ERROR] flusso documentale {mode}: {e}")
        return "Si è verificato un errore durante la ricerca documentale."


def call_document_quick_local(
    question: str,
    previous_question: str = "",
    previous_answer: str = "",
    followup_hint: Optional[bool] = None,
) -> str:
    """Risposta consigliata: flusso unificato in modalita' rapida."""
    return run_local_pipeline(
        question, previous_question, previous_answer, "rapido", followup_hint
    )


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
14. Non dedurre prestazioni o vantaggi non documentati da semplici differenze dimensionali.
15. Classifica i candidati come VERIFICATO, VERIFICA NECESSARIA, INCOMPATIBILE o NON
    IDENTIFICATO. Un dato mancante non e' una prova di incompatibilita'.
16. Nelle richieste di scelta o confronto termina con "PRODOTTI SELEZIONABILI PER LA
    PROPOSTA" ed elenca nome, codice e stato dei candidati VERIFICATI e di quelli in VERIFICA
    NECESSARIA. Per questi ultimi indica le verifiche aperte da riportare nella proposta
    preliminare. Escludi soltanto INCOMPATIBILI e NON IDENTIFICATI.


REGOLE UNIVERSALI DI FEDELTA' AL BISOGNO:
A. ESCLUSIONI. Se l'utente esclude un elemento ("senza X", "niente X"), cercalo in TUTTE le
   pagine del prodotto, comprese tavole tecniche, disegni quotati e note di montaggio.
   Se X e' presente in ogni candidato, NON dichiarare il requisito soddisfatto: scrivi che
   nessun prodotto documentato lo rispetta integralmente, proponi quello che lo avvicina di
   piu' e indica con precisione cosa resta di X, con la pagina che lo prova.
B. TAGLIE E CLASSI. Quando l'utente usa una denominazione di taglia o classe (formato,
   taglia, portata, diametro nominale, classe di misura commerciale), la proposta principale
   deve appartenere a quella classe; non proporre mai una classe diversa da quella richiesta.
   Se la corrispondenza tra denominazione e valori non e' scritta nel documento, dichiaralo
   in una frase, usa la corrispondenza standard del settore e mostra le altre classi disponibili.
C. VERSIONI. Pagine con lo stesso nome base di prodotto e codici con la stessa radice sono
   versioni dello stesso prodotto: presentale come versioni (per esempio accessori, altezze,
   finiture, portate), spiegando cosa cambia, e non come prodotti diversi.
D. GRANDEZZE. Rispondi sulla grandezza chiesta. Non ricavare una grandezza da un'altra non
   collegata (l'ingombro totale non e' la quota di un componente, la portata di un elemento
   non e' quella dell'insieme). Cerca la quota anche nelle schede e nei disegni tecnici;
   se non esiste, dichiaralo.
E. COMPONENTI NOMINATI. Un componente citato in tabelle, legende, disegni o note di
   montaggio (per esempio "foro su X", "fissaggio di X", "regolazione di X") esiste nel
   prodotto: dichiaralo come presente con la pagina che lo prova. Non trasformarlo in dubbio
   e non chiedere di verificarlo.
F. CALCOLI DIRETTI. Se il documento fornisce i dati per un calcolo immediato (massimo o minimo,
   numero di posizioni, passo, somma di elementi), esegui il calcolo e mostra il procedimento
   in una riga, indicando le pagine dei dati usati.
G. DOMANDA FINALE. Tutte le verifiche possibili sul documento vanno fatte nella risposta.
   La domanda finale riguarda soltanto una scelta dell'utente (versione, misura, finitura,
   priorita'), mai una verifica che il sistema dovrebbe fare da solo.
H. ESCLUSO O NON INDICATO. Scrivi che un prodotto NON ha una caratteristica soltanto se il
   documento lo dice esplicitamente, citando la dicitura e la pagina. Se il documento tace,
   scrivi "non indicato nel documento" e tieni i due gruppi separati: non riunirli mai nella
   stessa frase o nello stesso elenco.
I. RICERCA ESATTA. Se la richiesta chiede i dati di un codice o di un prodotto nominato,
   rispondi con i dati richiesti e la pagina che li prova (per i prezzi, anche la pagina
   delle condizioni che ne definisce la natura, se presente negli estratti). Niente
   "perche' e' adatta", niente compromessi e niente domanda di scelta: l'utente ha gia'
   scelto. Le versioni dello stesso prodotto si possono citare in una riga. Nella sezione
   PRODOTTI SELEZIONABILI indica soltanto il codice richiesto.
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
    followup_hint: Optional[bool] = None,
) -> str:
    """Analisi completa: flusso unificato in modalita' globale."""
    return run_local_pipeline(
        question, previous_question, previous_answer, "globale", followup_hint
    )


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
    followup_hint: Optional[bool] = None,
) -> str:
    """Risposta consigliata tramite OpenAI Vector Store."""
    document_query, is_followup = build_document_query(
        question, previous_question, previous_answer, followup_hint
    )
    dossier = call_openai_vector_retrieval(document_query, max_results=30)

    intent = detect_intent(question, {}, is_followup)
    quick_response_rules = FORMAT_BY_INTENT[intent] + (FOLLOWUP_RULES if is_followup else "")

    response_input = (
        "RICHIESTA ORIGINALE:\n"
        f"{document_query}\n\n"
        "EVIDENZE DOCUMENTALI RECUPERATE:\n"
        f"{dossier}\n\n"
        "Formula la risposta usando esclusivamente queste evidenze."
            + dimension_constraint_note(question, previous_question, is_followup)
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
    answer = strip_markdown_emphasis(answer)
    if needs_constraint_validation(question, previous_question, is_followup):
        answer = strip_markdown_emphasis(
            validate_document_answer(document_query, answer, "openai_vector"))
    return apply_fact_control(answer, "openai_vector")


def call_narratore_risponditore_vector(
    question: str,
    previous_question: str = "",
    previous_answer: str = "",
    followup_hint: Optional[bool] = None,
) -> str:
    """Analisi completa tramite OpenAI Vector Store."""
    document_query, is_followup = build_document_query(
        question, previous_question, previous_answer, followup_hint
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
        input=document_query
        + dimension_constraint_note(question, previous_question, is_followup),
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
    answer = strip_markdown_emphasis(answer)
    if needs_constraint_validation(question, previous_question, is_followup):
        answer = strip_markdown_emphasis(
            validate_document_answer(document_query, answer, "openai_vector"))
    return apply_fact_control(answer, "openai_vector")


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
    followup_hint: Optional[bool] = None,
) -> str:
    """Seleziona il motore rapido configurato e gestisce l'eventuale fallback."""
    if SEARCH_ENGINE == "deepseek_local":
        return call_document_quick_local(
            question, previous_question, previous_answer, followup_hint
        )
    try:
        return call_document_quick_vector(
            question, previous_question, previous_answer, followup_hint
        )
    except Exception as e:
        print(f"[ERROR] OpenAI Vector rapido: {e}")
        if SEARCH_ENGINE == "automatic":
            print("[INFO] fallback automatico a DeepSeek locale")
            return call_document_quick_local(
            question, previous_question, previous_answer, followup_hint
        )
        return "Si è verificato un errore durante la ricerca documentale."


def call_narratore_risponditore(
    question: str,
    previous_question: str = "",
    previous_answer: str = "",
    followup_hint: Optional[bool] = None,
) -> str:
    """Seleziona il motore completo configurato e gestisce l'eventuale fallback."""
    if SEARCH_ENGINE == "deepseek_local":
        return call_narratore_risponditore_local(
            question, previous_question, previous_answer, followup_hint
        )
    try:
        return call_narratore_risponditore_vector(
            question, previous_question, previous_answer, followup_hint
        )
    except Exception as e:
        print(f"[ERROR] OpenAI Vector completo: {e}")
        if SEARCH_ENGINE == "automatic":
            print("[INFO] fallback automatico a DeepSeek locale")
            return call_narratore_risponditore_local(
                question, previous_question, previous_answer, followup_hint
            )
        return "Si è verificato un errore durante l'analisi documentale completa."


# ============================================================
# CONTROLLO DOCUMENTALE DETERMINISTICO
# ============================================================
# Ogni codice, prezzo e pagina che la risposta attribuisce a un codice viene confrontato
# con l'indice delle righe. Nessun modello linguistico: e' aritmetica sul documento.
# Se trova incongruenze chiede UNA correzione mirata al modello, con le righe esatte;
# cio' che resta incongruente viene segnalato in chiaro invece di arrivare al cliente
# come dato certo.

# punteggiatura dopo il numero ("3.436, pagina") ammessa: esclusi solo cifra o decimale
PRICE_THOUSANDS = re.compile(r"(?<![\d,.])\d{1,3}(?:\.\d{3})+(?:,\d{2})?(?!\d)(?!,\d)")
PRICE_CUE_WORD = re.compile(
    r"\b(?:prezz\w*|costa|costano|costo|costi|listino|price\w*|preis\w*|prix)\b|€", re.I
)
# un numero NON e' un prezzo attribuito al codice se e' un risultato di calcolo, una
# differenza, una quantita', un anno, una pagina o una misura con unita'
NOT_PRICE_BEFORE = re.compile(
    r"(?:=|\btotale\b|\bsomma\b|\bin (?:meno|piu'|più)\b|\bdifferenza\b|\brisparmi\w*|"
    r"\bper \d+(?:\s+[a-zà-ÿ]+)?|\bx ?\d+\b|\bpag\w*\.?|\bpage\b|\bseite\b|\bp\.)\s*[:]?\s*$",
    re.I,
)
NOT_PRICE_AFTER = re.compile(
    r"^\s*(?:(?:€|euro)\s*)?(?:mm|cm|m\b|%|kg|g\b|w\b|v\b|°|pezz\w*|pz|posti|persone|anni|"
    r"in (?:meno|piu'|più)|di (?:differenza|risparmio))",
    re.I,
)
PRICE_SUFFIX = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+(?:,\d{2})?|\d{2,6}(?:,\d{2})?)\s*(?:€|euro)\b", re.I
)
PAGE_CITE = re.compile(
    r"\bpag(?:ina|ine|\.)?[\s*:]*(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?", re.I
)
SENTENCE_SPLIT = re.compile(r"\n+|(?<=;)\s+|(?<=[.!?])\s+(?=[A-ZÀ-Ý\-•])")


def is_attributed_price(window: str, price: str) -> bool:
    """Vero se almeno un'occorrenza del numero nella finestra e' un prezzo attribuito al
    codice, e non un totale, una differenza, un anno, una pagina o una misura."""
    for occurrence in re.finditer(r"(?<![\d.,])" + re.escape(price) + r"(?![\d])", window):
        before = window[max(0, occurrence.start() - 18):occurrence.start()]
        after = window[occurrence.end():occurrence.end() + 24]
        if NOT_PRICE_BEFORE.search(before) or NOT_PRICE_AFTER.search(after):
            continue
        if re.fullmatch(r"(?:19|20)\d{2}", price):
            continue  # anno (es. "listino 2024")
        return True
    return False


def known_code_roots() -> set:
    counts = Counter(code_root(c) for c in CODE_ROWS if code_root(c))
    return {root for root, count in counts.items() if count >= 2}


def codes_mentioned(text: str) -> List[tuple]:
    """(posizione, codice) dei codici citati: noti all'indice, oppure con radice nota
    (quindi plausibili ma inesistenti, es. FLU9999)."""
    found: List[tuple] = []
    if not KNOWN_ROOTS:
        KNOWN_ROOTS.update(known_code_roots())
    roots = KNOWN_ROOTS
    for match in re.finditer(r"\b[A-Za-z][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*\b|\b\d{5,}\b", text):
        token = match.group(0).upper()
        if MEASURE_PATTERN.fullmatch(token) or len(token) < 5:
            continue
        if token in CODE_ROWS:
            found.append((match.start(), token))
            continue
        if re.search(r"[-/]", token):
            # intervallo o elenco di codici: si verificano le parti, mai il token intero
            offset = 0
            for part in re.split(r"[-/]", token):
                if part in CODE_ROWS:
                    found.append((match.start() + token.find(part, offset), part))
                offset += len(part) + 1
            continue
        if code_root(token) and code_root(token) in roots:
            found.append((match.start(), token))
    return found



def check_answer_facts(answer: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    if not answer or not CODE_ROWS:
        return issues
    seen = set()
    previous_mentions: List[tuple] = []
    for sentence in SENTENCE_SPLIT.split(answer):
        mentions = codes_mentioned(sentence)
        if not mentions:
            # Frase senza codice subito dopo una frase con UN solo codice, che non nomina
            # un altro prodotto: i suoi prezzi si riferiscono a quel codice.
            other_names = re.findall(r"(?<=\s)[A-ZÀ-Ý][a-zà-ÿ]+|[A-Z]{2,}", sentence[1:])
            carry = len({c for _, c in previous_mentions}) == 1 and not other_names
            previous_mentions = []
            if not carry or not sentence.strip():
                continue
            mentions = [(0, prior_code)]
            only_prices = True
        else:
            previous_mentions = mentions
            prior_code = mentions[-1][1]
            only_prices = False
        for index, (position, code) in enumerate(mentions):
            end = mentions[index + 1][0] if index + 1 < len(mentions) else len(sentence)
            window = sentence[position:end]
            if code not in CODE_ROWS:
                key = ("codice", code)
                if key not in seen:
                    seen.add(key)
                    issues.append({"tipo": "codice_inesistente", "codice": code,
                                   "frase": sentence.strip()[:240]})
                continue
            rows = CODE_ROWS[code]
            allowed_numbers = set().union(*(row["near_numbers"] for row in rows))
            pages_of_code = {row["page"] for row in rows}
            families = {PAGE_BY_NUMBER[p].get("family") for p in pages_of_code if p in PAGE_BY_NUMBER}
            # della famiglia valgono solo le schede tecniche (pagine senza codici di listino):
            # una pagina di listino di un'altra versione resta un errore
            family_pages = {
                p["page"] for p in DOCUMENT_PAGES
                if p.get("family") and p.get("family") in families
                and not (p.get("codes", set()) & CODE_ROWS.keys())
            }
            prices = set(PRICE_THOUSANDS.findall(window)) | set(PRICE_SUFFIX.findall(window))
            for cue in PRICE_CUE_WORD.finditer(window):
                # il primo numero dopo la parola "prezzo/costa/euro...", entro 40 caratteri,
                # purche' non sia un numero di pagina
                tail = window[cue.end():cue.end() + 40]
                number = re.search(
                    r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+(?:,\d{2})?|\d{2,6}(?:,\d{2})?)"
                    r"(?!\d)(?![.,]\d)(?!\s*[x×])",
                    tail,
                )
                if number and not re.search(r"pag\w*\.?\s*$", tail[:number.start()], re.I):
                    prices.add(number.group(1))
            for price in prices:
                normalized = normalize_number(price)
                if normalized in allowed_numbers:
                    continue
                if not is_attributed_price(window, price):
                    continue
                key = ("prezzo", code, normalized)
                if key not in seen:
                    seen.add(key)
                    issues.append({"tipo": "prezzo_non_trovato", "codice": code, "valore": price,
                                   "frase": sentence.strip()[:240]})
            cite = None if only_prices else PAGE_CITE.search(window)
            if cite:
                first = int(cite.group(1))
                last = int(cite.group(2)) if cite.group(2) else first
                cited = set(range(min(first, last), max(first, last) + 1))
                if not cited & (pages_of_code | family_pages):
                    key = ("pagina", code, first)
                    if key not in seen:
                        seen.add(key)
                        issues.append({"tipo": "pagina_errata", "codice": code,
                                       "valore": cite.group(0),
                                       "pagine_corrette": sorted(pages_of_code)[:8],
                                       "frase": sentence.strip()[:240]})
    return issues


FACT_REPAIR_PROMPT = """
Sei il CORRETTORE DOCUMENTALE. Ricevi una risposta e un elenco di incongruenze trovate
confrontandola con le righe del documento. Correggi SOLO quei punti usando le righe fornite:
codice inesistente -> sostituiscilo con il codice corretto della stessa riga, oppure togli
l'affermazione; prezzo non trovato -> usa il prezzo presente nella riga del codice, indicando
la colonna; pagina errata -> usa la pagina corretta, salvo che la pagina citata si riferisca
chiaramente a un altro dato (in quel caso rendilo esplicito). Non cambiare nient'altro:
stessa lingua, stessa struttura, stesse sezioni. Restituisci soltanto la risposta corretta.
"""


def repair_answer(answer: str, issues: List[Dict[str, Any]], provider: str) -> str:
    codes = []
    for issue in issues:
        code = issue["codice"]
        if code in CODE_ROWS:
            codes.append(code)
        else:  # codice inesistente: righe dei codici con la stessa radice citati nella frase
            codes.extend(c for _, c in codes_mentioned(issue["frase"]) if c in CODE_ROWS)
    elenco = "\n".join(
        f"- {i['tipo']}: codice {i['codice']}"
        + (f", valore '{i['valore']}'" if i.get("valore") else "")
        + (f", pagine corrette {i['pagine_corrette']}" if i.get("pagine_corrette") else "")
        + f"\n  frase: {i['frase']}"
        for i in issues
    )
    payload = (
        f"RISPOSTA:\n{answer}\n\nINCONGRUENZE:\n{elenco}\n\n"
        f"RIGHE DEL DOCUMENTO:\n{format_code_rows(list(dict.fromkeys(codes)), 6000)}"
    )
    try:
        if provider == "openai_vector" and openai_client is not None:
            response = openai_client.responses.create(
                model=OPENAI_DOCUMENT_MODEL, instructions=FACT_REPAIR_PROMPT,
                input=payload, max_output_tokens=VALIDATOR_MAX_TOKENS,
            )
            return (response.output_text or "").strip() or answer
        if client is not None:
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[{"role": "system", "content": FACT_REPAIR_PROMPT},
                          {"role": "user", "content": payload}],
                temperature=0.0, max_tokens=VALIDATOR_MAX_TOKENS,
            )
            return (response.choices[0].message.content or "").strip() or answer
    except Exception as e:
        print(f"[WARN] correzione documentale non riuscita: {e}")
    return answer


def apply_fact_control(answer: str, provider: str) -> str:
    """Verifica deterministica, una correzione mirata se serve, segnalazione del residuo."""
    issues = check_answer_facts(answer)
    if not issues:
        print("[CONTROLLO] 0 incongruenze")
        return answer
    print(f"[CONTROLLO] {len(issues)} incongruenze: " + "; ".join(
        f"{i['tipo']} {i['codice']} {i.get('valore', '')}" for i in issues))
    repaired = repair_answer(answer, issues, provider)
    residual = check_answer_facts(repaired)
    print(f"[CONTROLLO] dopo correzione: {len(residual)} incongruenze")
    # Le pagine residue possono riferirsi a un altro dato della stessa frase: solo log.
    serious = [i for i in residual if i["tipo"] != "pagina_errata"]
    if serious:
        note = "\n".join(
            f"- {i['codice']}: "
            + ("codice non presente nel documento" if i["tipo"] == "codice_inesistente"
               else f"il valore {i['valore']} non compare nella riga del codice")
            for i in serious
        )
        warning = "DATI DA VERIFICARE SUL DOCUMENTO:\n" + note
        match = find_section(repaired, SELECTABLE_HEADER)
        at = match.start() if match else -1
        # prima della sezione dei selezionabili: resta nel testo, non entra nel preventivo
        repaired = (repaired[:at].rstrip() + "\n\n" + warning + "\n\n" + repaired[at:]
                    if at >= 0 else repaired + "\n\n" + warning)
    return repaired


# Memoria delle risposte: il catalogo e' statico, una domanda identica nello stesso contesto
# riceve la stessa risposta verificata senza ripetere ricerca e chiamate al modello.
ANSWER_CACHE: "OrderedDict[tuple, tuple]" = OrderedDict()

SELECTABLE_HEADER = "PRODOTTI SELEZIONABILI PER LA PROPOSTA"
RECOMMENDED_PREFIX = "PRODOTTO CONSIGLIATO E SELEZIONABILE PER LA PROPOSTA"


def requested_sizes(question: str, previous_question: str = "", followup: bool = False) -> set:
    """Misure fissate esplicitamente dall'utente: prima la domanda attuale, poi,
    solo se e' un approfondimento, quella precedente."""
    current = find_sizes(question)
    if current:
        return current
    if followup:
        return find_sizes(previous_question)
    return set()


def dimension_constraint_note(
    question: str, previous_question: str = "", followup: bool = False
) -> str:
    """Vincolo dimensionale fissato dall'utente, dichiarato al modello PRIMA che scriva."""
    sizes = requested_sizes(question, previous_question, followup)
    if not sizes:
        return ""
    return (
        "\n\nVINCOLO DIMENSIONALE FISSATO DALL'UTENTE: "
        + ", ".join(sorted(sizes))
        + ". Proponi, consiglia ed elenca tra i selezionabili soltanto codici di questa misura. "
        "Un codice di misura diversa puo' essere citato solo per dire che non corrisponde."
    )


def strip_markdown_emphasis(text: str) -> str:
    """La pagina mostra testo semplice: grassetti e titoli Markdown apparirebbero come
    simboli. Il prompt lo vieta, ma il modello non sempre obbedisce: si pulisce qui."""
    if not text:
        return text
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.S)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    return text.replace("**", "")


def canonical_size(size: str) -> str:
    """160x200 e 200x160 sono la stessa misura: si confrontano in forma ordinata."""
    try:
        parts = sorted(size.split("x"), key=float)
    except ValueError:
        return size
    return "x".join(parts)


def find_section(text: str, header: str) -> Optional[re.Match]:
    """Posizione di un'intestazione cercata sul testo originale (non su upper():
    caratteri come ß o legature cambiano lunghezza e sfalserebbero gli indici)."""
    return re.search(re.escape(header), text or "", re.I)


def enforce_selectable_constraints(answer: str, allowed_sizes: set) -> str:
    """Filtro deterministico della sezione PRODOTTI SELEZIONABILI.

    Un codice resta selezionabile solo se la sua misura documentata (riga di listino)
    e' tra quelle fissate dall'utente, in qualunque ordine siano scritte le dimensioni.
    Codici senza misura documentata restano (dato non trovato != incompatibile).
    Ogni elemento dell'elenco porta con se' le sue righe di continuazione.
    Se il filtro togliesse TUTTI i prodotti, la risposta resta invariata: e' piu'
    probabile un disallineamento di scrittura che l'assenza di ogni soluzione.
    Se resta un solo prodotto non si chiede di scegliere di nuovo."""
    header_match = find_section(answer, SELECTABLE_HEADER)
    if not answer or not allowed_sizes or not header_match:
        return answer
    allowed = {canonical_size(size) for size in allowed_sizes}

    def size_ok(code: str) -> bool:
        sizes = CODE_SIZES.get(code)
        return (not sizes) or bool({canonical_size(x) for x in sizes} & allowed)

    def line_codes(line: str) -> List[str]:
        return [c for c in CODE_TOKEN_PATTERN.findall(line.upper()) if c in CODE_SIZES]

    head, section = answer[:header_match.start()], answer[header_match.start():]
    lines = section.splitlines()
    bullet = re.compile(r"\s*(?:[-•*]|\d+[.)])\s+")

    # 1) elementi dell'elenco con le loro righe di continuazione
    items: List[Dict[str, Any]] = []
    tail_lines: List[str] = []
    recommended_code = ""
    in_tail = False
    blank_seen = False
    for line in lines[1:]:
        codes = line_codes(line)
        if re.search(re.escape(RECOMMENDED_PREFIX), line, re.I):
            if codes and size_ok(codes[0]):
                recommended_code = codes[0]
            continue  # riscritta in fondo, coerente con il filtro
        if in_tail:
            tail_lines.append(line)
            continue
        if not line.strip():
            blank_seen = True
            continue
        starts_item = bool(codes) or bool(bullet.match(line))
        if starts_item and not (blank_seen and not codes and not bullet.match(line)):
            items.append({"lines": [line], "codes": codes})
            blank_seen = False
            continue
        if items and not blank_seen and "?" not in line:
            items[-1]["lines"].append(line)  # continuazione dell'elemento precedente
            continue
        in_tail = True
        tail_lines.append(line)

    kept_items = [i for i in items if not i["codes"] or size_ok(i["codes"][0])]
    removed = [i["codes"][0] for i in items if i["codes"] and not size_ok(i["codes"][0])]
    kept_with_code = [i for i in kept_items if i["codes"]]
    if removed and not kept_with_code:
        print(f"[SELEZIONABILI] misura {sorted(allowed)}: nessun codice compatibile, "
              "risposta lasciata invariata")
        return answer

    # 2) anche un "consigliato" scritto prima della sezione deve rispettare la misura
    head_rec = re.search(rf"{re.escape(RECOMMENDED_PREFIX)}\s*:([^\n]*)", head, re.I)
    if head_rec:
        codes = line_codes(head_rec.group(1))
        if codes and not size_ok(codes[0]):
            removed.append(codes[0])
            head = head[:head_rec.start()] + head[head_rec.end():]
        elif codes:
            recommended_code = recommended_code or codes[0]

    # 3) nel testo: via i paragrafi che nominano SOLO codici di misura diversa
    kept_paragraphs: List[str] = []
    for paragraph in re.split(r"\n\s*\n", head):
        codes = line_codes(paragraph)
        if codes and not any(size_ok(c) for c in codes):
            removed.extend(codes)
            continue
        kept_paragraphs.append(paragraph)
    head = "\n\n".join(kept_paragraphs)

    if not removed:
        return answer
    print(f"[SELEZIONABILI] misura richiesta={sorted(allowed)} rimossi={sorted(set(removed))}")

    kept_codes = list(dict.fromkeys(i["codes"][0] for i in kept_with_code))
    if len(kept_codes) == 1:
        recommended_code = kept_codes[0]
        # il prodotto e' gia' determinato: niente nuova richiesta di scelta
        tail_lines = [
            t for t in tail_lines
            if not re.search(
                r"\b(quale|quali|scegli|preferisci|desideri portare|vuoi portare)\b", t, re.I
            )
        ]
    body_lines = [lines[0]] + [line for item in kept_items for line in item["lines"]]
    if recommended_code and recommended_code in kept_codes:
        first_line = next(i["lines"][0] for i in kept_with_code if i["codes"][0] == recommended_code)
        label = bullet.sub("", first_line, count=1).strip()
        label = re.split(r"\s+[-–]\s+(?:VERIFICATO|VERIFICA NECESSARIA)", label, flags=re.I)[0]
        label = re.sub(rf",?\s*(?:codice\s+)?{re.escape(recommended_code)}\b", "", label,
                       flags=re.I).strip(" ,-")
        body_lines.append(f"{RECOMMENDED_PREFIX}: codice {recommended_code} - {label}")
    body = "\n".join(body_lines + ([""] + tail_lines if tail_lines else []))
    return (head.strip() + "\n\n" + body) if head.strip() else body


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


@app.get("/catalogo.pdf")
async def catalog_pdf() -> FileResponse:
    """Serve il documento originale al visualizzatore della pagina catalogo."""
    if not CATALOG_PDF_PATH or not os.path.isfile(CATALOG_PDF_PATH):
        raise HTTPException(status_code=404, detail="Catalogo PDF non disponibile")
    return FileResponse(
        CATALOG_PDF_PATH,
        media_type="application/pdf",
        filename=os.path.basename(CATALOG_PDF_PATH),
        content_disposition_type="inline",
    )


@app.get("/api/status")
async def status():
    """
    Riepilogo rapido dello stato backend.
    """
    return {
        "status": f"Narratore-Risponditore attivo · {DOCUMENT_CONTEXT}",
        "kb_blocks": len(KB_BLOCKS),
        "comm_blocks": len(COMM_ITEMS),
        "document_pages": len(DOCUMENT_PAGES),
        "document_families": len({p.get("family") for p in DOCUMENT_PAGES if p.get("family")}),
        "document_code_rows": len(CODE_ROWS),
        "validator_mode": VALIDATOR_MODE,
        "answer_cache_size": len(ANSWER_CACHE),
        "document_index_loaded": bool(DOCUMENT_PAGES),
        "engine": active_document_engine(),
        "engine_requested": SEARCH_ENGINE,
        "deepseek_ready": bool(client and DOCUMENT_PAGES),
        "openai_vector_ready": bool(openai_client and OPENAI_VECTOR_STORE_ID),
        "universal_constraint_validator": True,
        "narratore_risponditore": "attivo",
        "commercial_proposal_enabled": ENABLE_COMMERCIAL_PROPOSAL,
        "catalog_viewer_ready": bool(
            CATALOG_PDF_PATH and os.path.isfile(CATALOG_PDF_PATH)
        ),
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

            has_memory = bool(previous_question and previous_answer)
            cache_key = (
                document_mode, document_question, previous_question[-800:],
                hashlib.sha1(previous_answer.encode("utf-8")).hexdigest(),
            )
            cached = ANSWER_CACHE.get(cache_key)
            if cached is not None:
                # la memoria si consulta prima di qualunque chiamata al modello
                ANSWER_CACHE.move_to_end(cache_key)
                print("[CACHE] risposta gia' calcolata")
                document_answer, is_followup_turn = cached
            else:
                planner_seguito = None
                if has_memory and SEARCH_ENGINE != "openai_vector":
                    planner_seguito = plan_request(document_question, previous_question).get("seguito")
                is_followup_turn = has_memory and (
                    is_contextual_followup(document_question) or planner_seguito is True
                )
                engine = call_narratore_risponditore if document_mode == "globale" else call_document_quick
                document_answer = engine(
                    document_question, previous_question, previous_answer, is_followup_turn
                )
                document_answer = strip_markdown_emphasis(document_answer)
                document_answer = enforce_selectable_constraints(
                    document_answer,
                    requested_sizes(document_question, previous_question, is_followup_turn),
                )
                # in memoria solo risposte pulite: niente errori, niente avvisi residui,
                # niente risposte nate senza pianificatore (verrebbero congelate peggiori)
                plan_ok = SEARCH_ENGINE == "openai_vector" or (
                    (document_question[:3000], previous_question[:800] if has_memory else "")
                    in PLAN_CACHE
                )
                clean = not re.match(
                    r"(Si è verificato|Informazione non trovata|Il motore esterno|Archivio)",
                    document_answer,
                ) and "DATI DA VERIFICARE" not in document_answer
                if clean and plan_ok:
                    ANSWER_CACHE[cache_key] = (document_answer, is_followup_turn)
                    if len(ANSWER_CACHE) > 256:
                        ANSWER_CACHE.popitem(last=False)

            return AnswerResponse(
                answer=document_answer,
                source="narratore_risponditore",
                meta={
                    "mode": document_mode,
                    "engine": active_document_engine(),
                    "used_previous_context": bool(is_followup_turn),
                    "cached": cached is not None,
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
