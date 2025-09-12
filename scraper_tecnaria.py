# -*- coding: utf-8 -*-
import os
import re
import json
import threading
import time
import unicodedata
from typing import List, Dict, Any, Tuple

# dipendenze leggere (già nel tuo requirements)
from rapidfuzz import fuzz, process as rf_process
from rank_bm25 import BM25Okapi

# ========= Config di base =========
SCRAPER_DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")
MIN_CHARS_PER_CHUNK = int(os.environ.get("MIN_CHARS_PER_CHUNK", "200"))
OVERLAP_CHARS = int(os.environ.get("OVERLAP_CHARS", "50"))

# ========= Stato globale =========
INDEX: List[Dict[str, Any]] = []     # ogni entry: {file, tags:set, question, answer, text, kind, tokens}
BM25 = None
_READY = False
_LOCK = threading.Lock()

# ========= stopwords minime IT (non aggressive) =========
STOPWORDS_MIN = {
    "il","lo","la","i","gli","le","un","uno","una",
    "di","del","della","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da",
    "al","allo","ai","agli","alla","alle",
    "che","come","dove","quando","anche",
    "mi","ti","si","ci","vi","a","da","de","dal","dall","dalla","dalle",
}

# ========= sinonimi/alias soft per domande frequenti =========
SYNONYMS = {
    "p560": ["p 560", "spit p560", "pistola spit", "sparachiodi", "chiodatrice"],
    "chiodatrice": ["sparachiodi", "pistola", "p560"],
    "pistola": ["chiodatrice", "sparachiodi", "p560"],
    "contatti": ["recapiti", "telefono", "mail", "email", "indirizzo"],
    "codici": ["codice", "articoli", "catalogo", "listino", "sku"],
    "connettori": ["connettore", "pioli", "viti", "dispositivi"],
    "posa": ["installazione", "montaggio", "messa in opera"],
}

QA_Q_PAT = re.compile(r"^\s*(?:D|Domanda)\s*:\s*(.+)$", re.IGNORECASE)
QA_A_PAT = re.compile(r"^\s*(?:R|Risposta)\s*:\s*(.+)$", re.IGNORECASE)
TAGS_PAT = re.compile(r"^\s*\[TAGS?\s*:\s*(.+?)\]\s*$", re.IGNORECASE)

# ========= util =========
def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")

def normalize_text(s: str) -> str:
    s = s or ""
    s = strip_accents(s.lower())
    s = re.sub(r"[^\w\s]", " ", s)  # rimuove punteggiatura
    s = re.sub(r"\s+", " ", s).strip()
    return s

def tokenize(s: str) -> List[str]:
    toks = normalize_text(s).split()
    return [t for t in toks if t not in STOPWORDS_MIN]

def expand_query(q: str) -> str:
    base = normalize_text(q)
    toks = base.split()
    extra = []
    for t in toks:
        if t in SYNONYMS:
            extra.extend(SYNONYMS[t])
    if extra:
        base += " " + " ".join(normalize_text(e) for e in extra)
    return base

def _clean_answer_text(s: str) -> str:
    """Pulisce l’output finale: rimuove etichette D:/R:/[TAGS], spazi doppi, ecc."""
    lines = []
    for raw in s.splitlines():
        if TAGS_PAT.match(raw):
            continue
        if raw.strip().startswith("D:") or raw.strip().lower().startswith("domanda:"):
            continue
        line = re.sub(r"^\s*(?:R|Risposta)\s*:\s*", "", raw, flags=re.IGNORECASE)
        lines.append(line)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out

# ========= parsing documenti =========
def _read_txt_files(doc_dir: str) -> List[str]:
    files = []
    for root, _, fns in os.walk(doc_dir):
        for fn in fns:
            if fn.lower().endswith(".txt"):
                files.append(os.path.join(root, fn))
    files.sort()
    return files

def _parse_file(path: str) -> List[Dict[str, Any]]:
    """
    Riconosce blocchi Q/A (D:/R:) e anche paragrafi liberi.
    Restituisce entries normalizzate pronte per l’indicizzazione.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
    except Exception:
        return []

    file_name = os.path.basename(path)
    tags_global: List[str] = []
    entries: List[Dict[str, Any]] = []

    # tags globali (prima occorrenza)
    for line in raw.splitlines():
        m = TAGS_PAT.match(line)
        if m:
            tags_global = [t.strip().lower() for t in m.group(1).split(",") if t.strip()]
            break

    # prova a parsare come Q/A
    lines = raw.splitlines()
    i = 0
    qa_found = False
    while i < len(lines):
        q_match = QA_Q_PAT.match(lines[i])
        if q_match:
            qa_found = True
            q_txt = q_match.group(1).strip()
            i += 1
            # accumula risposta fino alla prossima D:/fine
            a_buf = []
            while i < len(lines):
                if QA_Q_PAT.match(lines[i]):
                    break
                a_buf.append(lines[i])
                i += 1
            a_txt = "\n".join(a_buf).strip()
            # estrai solo la parte della risposta (togli eventuali prefissi "R:")
            a_txt = _clean_answer_text(a_txt)
            entries.append({
                "file": file_name,
                "tags": set(tags_global),
                "question": q_txt,
                "answer": a_txt,
                "text": f"{q_txt}\n{a_txt}",
                "kind": "qa",
            })
        else:
            i += 1

    if qa_found:
        return entries

    # altrimenti spezza in paragrafi “liberi”
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    for p in paragraphs:
        entries.append({
            "file": file_name,
            "tags": set(tags_global),
            "question": "",
            "answer": p,          # qui answer = paragrafo
            "text": p,
            "kind": "text",
        })
    return entries

def _build_bm25_from_entries(entries: List[Dict[str, Any]]):
    docs_tokens = []
    for e in entries:
        docs_tokens.append(tokenize(e["text"]))
    if not docs_tokens:
        return None
    return BM25Okapi(docs_tokens)

# ========= API pubblico =========
def build_index(doc_dir: str = None) -> None:
    global INDEX, BM25, _READY
    with _LOCK:
        _READY = False
    doc_dir = doc_dir or SCRAPER_DOC_DIR
    files = _read_txt_files(doc_dir)
    print(f"[scraper_tecnaria] Inizio indicizzazione da: {os.path.abspath(doc_dir)}\n")
    print(f"[scraper_tecnaria] Trovati {len(files)} file .txt:")
    for p in files:
        print(f"[scraper_tecnaria]   - {p}")

    all_entries: List[Dict[str, Any]] = []
    for p in files:
        all_entries.extend(_parse_file(p))

    # indicizzazione BM25
    bm25 = _build_bm25_from_entries(all_entries)

    with _LOCK:
        INDEX = all_entries
        BM25 = bm25
        _READY = True

    print(f"[scraper_tecnaria] Indicizzati {len(INDEX)} blocchi da {len(files)} file.")

def is_ready() -> bool:
    with _LOCK:
        return bool(_READY and INDEX and BM25 is not None)

def ensure_ready_blocking(timeout_sec: int = 0) -> None:
    """
    Se l’indice non è pronto, avvia la build in un thread.
    Se timeout_sec > 0, attende al massimo quel tempo.
    """
    if is_ready():
        return
    def _runner():
        try:
            build_index(SCRAPER_DOC_DIR)
        except Exception as e:
            print(f"[scraper_tecnaria] ERRORE build_index: {e}", flush=True)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    if timeout_sec and timeout_sec > 0:
        t.join(timeout=timeout_sec)

# ========= Ricerca =========
def _score_entry(q_norm: str, entry: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
    """
    Calcola uno score combinato:
      - match con domanda (se kind=qa) via RapidFuzz
      - match BM25 sul testo completo
      - boost su tag/nome file
    Restituisce (score, breakdown)
    """
    # RapidFuzz sulla domanda (se presente)
    rf_q = 0.0
    if entry["kind"] == "qa" and entry["question"]:
        rf_q = fuzz.token_set_ratio(q_norm, normalize_text(entry["question"])) / 100.0

    # BM25
    bm = 0.0
    if BM25 is not None and INDEX:
        # calcola una volta a livello query
        pass  # normalizzato nella funzione principale

    # Boost su tag/nome file
    boost = 0.0
    q_tokens = set(q_norm.split())
    if entry.get("tags"):
        if any(t in q_tokens for t in entry["tags"]):
            boost += 0.10
    file_low = entry["file"].lower()
    for t in q_tokens:
        if len(t) >= 3 and t in file_low:
            boost += 0.10
            break

    # combinazione (BM25 viene normalizzato esternamente)
    # qui ritorniamo rf_q e boost; bm verrà inserito dopo
    return (rf_q + boost, {"rf": rf_q, "boost": boost})

def _best_qa_answer_for_entry(q_norm: str, entry: Dict[str, Any]) -> str:
    """
    Per una entry QA abbiamo già un’unica coppia D/R.
    Torniamo SOLO la risposta ripulita.
    """
    return _clean_answer_text(entry["answer"])

def search_best_answer(query: str, threshold: float = 0.35, topk: int = 20) -> Dict[str, Any]:
    """
    Ritorna SOLO la risposta (senza “D:”).
    Se non trova sopra soglia, ritorna found=False ma con un piccolo suggerimento.
    """
    if not is_ready():
        return {"answer": "Indice non pronto. Riprova tra pochi secondi.", "found": False, "from": None}

    q_expanded = expand_query(query)
    q_norm = normalize_text(q_expanded)

    # BM25 ranking preliminare su TUTTI i documenti (testo completo)
    # prendiamo topN un po’ alto per avere materiale su cui rifinire con RapidFuzz
    tokens_q = tokenize(q_norm)
    scores = BM25.get_scores(tokens_q) if BM25 is not None else []
    # seleziona top candidati
    idx_scores = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:max(topk, 50)]
    max_bm = idx_scores[0][1] if idx_scores else 0.0
    min_bm = idx_scores[-1][1] if idx_scores else 0.0
    denom = (max_bm - min_bm) if (max_bm - min_bm) > 1e-9 else 1.0

    candidates: List[Tuple[float, int, Dict[str, Any], Dict[str, float]]] = []
    for idx, bm_val in idx_scores:
        entry = INDEX[idx]
        partial, parts = _score_entry(q_norm, entry)
        bm_norm = (bm_val - min_bm) / denom
        # combinazione finale: privilegia matching domanda (se QA) ma tiene BM25 e boost
        final = 0.60 * partial + 0.40 * bm_norm
        parts["bm25"] = bm_norm
        candidates.append((final, idx, entry, parts))

    # ordina per punteggio decrescente
    candidates.sort(key=lambda x: x[0], reverse=True)

    # prendi il migliore sopra soglia
    if candidates and candidates[0][0] >= threshold:
        best_score, _, best_entry, parts = candidates[0]
        if best_entry["kind"] == "qa":
            ans = _best_qa_answer_for_entry(q_norm, best_entry)
        else:
            # blocco generico: pulizia leggera (togli eventuali etichette)
            ans = _clean_answer_text(best_entry["answer"])
        return {
            "answer": ans,
            "found": True,
            "score": round(float(best_score), 3),
            "from": best_entry["file"],
            "tags": sorted(list(best_entry.get("tags", []))) or None,
        }

    # fallback: niente sopra soglia -> prova a restituire il miglior paragrafo “pulito”
    if candidates:
        _, _, best_entry, _ = candidates[0]
        ans = _clean_answer_text(best_entry["answer"])
        return {
            "answer": ans,
            "found": False,
            "from": best_entry["file"],
        }

    return {
        "answer": "Non ho trovato una risposta precisa nei documenti locali.",
        "found": False,
        "from": None,
    }


# ====== bootstrap automatico all’import ======
try:
    ensure_ready_blocking(timeout_sec=0)  # NON bloccare; l’app può già rispondere “indice non pronto”
except Exception as e:
    print(f"[scraper_tecnaria] Bootstrap non riuscito: {e}", flush=True)
