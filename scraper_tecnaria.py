# -*- coding: utf-8 -*-
from __future__ import annotations
import os, re, math, glob
from dataclasses import dataclass
from collections import Counter, defaultdict
from typing import List, Dict, Tuple

# ===========================
# CONFIG
# ===========================
DOC_DIR = os.getenv("DOC_DIR", "documenti_gTab")  # cartella con i .txt
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.45"))
MIN_CHARS_PER_CHUNK = int(os.getenv("MIN_CHARS_PER_CHUNK", "400"))
OVERLAP_CHARS = int(os.getenv("OVERLAP_CHARS", "100"))
MAX_TOP_K = int(os.getenv("TOP_K", "5"))

# Parole da ignorare SEMPRE nelle ricerche (la parola 'tecnaria' è scontata).
STOPWORDS = {
    "tecnaria", "tecnaria.", "tecnaria,", "s.p.a.", "spa",
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una",
    "di", "del", "della", "dell", "dei", "degli", "delle",
    "e", "ed", "o", "oppure", "con", "per", "su", "tra", "fra",
    "in", "da", "al", "allo", "ai", "agli", "alla", "alle",
    "che", "come", "quale", "qual", "quali", "quanta", "quanto",
    "dove", "quando", "quindi", "anche"
}

# Keyword helper per scorciatoie (matching di intent evidenti)
KW_MAP = {
    "contatti": ["contatti", "telefono", "email", "orari", "sede", "indirizzo"],
    "telefono": ["contatti", "telefono"],
    "email":    ["contatti", "email"],
    "orari":    ["contatti", "orari"],
    "sede":     ["contatti", "sede", "indirizzo"],
    "certificazioni": ["certificazioni", "iso", "ce", "dop", "eurocodici", "ntc"],
    "stabilimenti": ["stabilimenti", "produzione", "reparto produttivo", "logistica"],
    "profilo": ["profilo", "chi siete", "chi siamo", "azienda", "storia"],
    "vision":  ["vision", "visione", "futuro", "strategia"],
    "mission": ["mission", "missione", "valori", "obiettivi"],
}

# ===========================
# DATA STRUCTURES
# ===========================
@dataclass
class DocChunk:
    doc: str             # nome file
    section: str         # nome sezione (se presente) o fallback
    text: str            # testo del chunk
    tags: List[str]      # lista tag lower
    tfidf: Dict[str, float]  # vettore tf-idf

# Indici globali in memoria
_INDEX: List[DocChunk] = []
_IDF: Dict[str, float] = {}
_VOCAB: set = set()

# ===========================
# NORMALIZZAZIONE TESTO
# ===========================
def _clean(s: str) -> str:
    # rimuove markdown-like separatori, normalizza spazi
    s = s.replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def _tokenize(s: str) -> List[str]:
    s = s.lower()
    # mantieni lettere e numeri italiani
    s = re.sub(r"[^a-z0-9àèéìòóùüç]+", " ", s, flags=re.IGNORECASE)
    toks = [t for t in s.split() if t and t not in STOPWORDS and len(t) > 1]
    return toks

def _extract_tags_and_body(text: str) -> Tuple[List[str], str]:
    # Legge una prima riga tipo: [TAGS: a, b, c]
    tags = []
    lines = text.splitlines()
    if lines and lines[0].strip().lower().startswith("[tags:"):
        m = re.match(r"\[tags:\s*(.*?)\s*\]\s*$", lines[0].strip(), flags=re.IGNORECASE)
        if m:
            raw = m.group(1)
            tags = [t.strip().lower() for t in re.split(r"[;,]", raw) if t.strip()]
        body = "\n".join(lines[1:]).strip()
        return tags, body
    return tags, text.strip()

def _split_into_chunks(text: str) -> List[Tuple[str, str]]:
    """
    Spezza il documento in chunk:
    - se trova linee '===== TITOLO =====' usa quelle come sezioni
    - altrimenti spezza per blocchi di ~MIN_CHARS_PER_CHUNK con overlap
    Restituisce [(section_name, chunk_text), ...]
    """
    text = _clean(text)

    # split su sezioni esplicite
    parts = re.split(r"^=+\s*(.*?)\s*=+\s*$", text, flags=re.MULTILINE)
    chunks: List[Tuple[str, str]] = []

    if len(parts) > 1 and len(parts) % 2 == 1:
        # formato: [pre, title1, body1, title2, body2, ...]
        pre = parts[0].strip()
        if pre:
            # se c'è testo prima di una prima sezione, mettilo come 'Intro'
            chunks.append(("Intro", pre))
        for i in range(1, len(parts), 2):
            sec = parts[i].strip() or f"Sezione_{i//2}"
            body = parts[i+1].strip()
            if not body:
                continue
            # se il body è lungo, sottospezza
            for sub in _sliding_chunks(body, MIN_CHARS_PER_CHUNK, OVERLAP_CHARS):
                chunks.append((sec, sub))
        return chunks

    # fallback: spezza a blocchi scorrevoli
    for sub in _sliding_chunks(text, MIN_CHARS_PER_CHUNK, OVERLAP_CHARS):
        chunks.append(("Body", sub))
    return chunks

def _sliding_chunks(text: str, min_len: int, overlap: int) -> List[str]:
    if len(text) <= min_len:
        return [text]
    res = []
    start = 0
    while start < len(text):
        end = start + min_len
        res.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
        if start < 0:
            start = 0
    return res

# ===========================
# TF-IDF SIMPLE ENGINE
# ===========================
def _build_tfidf(chunks: List[DocChunk]) -> None:
    global _IDF, _VOCAB
    # document frequency
    df = defaultdict(int)
    docs_tokens: List[set] = []
    for ch in chunks:
        toks = set(_tokenize(ch.text))
        docs_tokens.append(toks)
        for t in toks:
            df[t] += 1
    N = max(1, len(chunks))
    _IDF = {t: math.log((N + 1) / (df_t + 1)) + 1.0 for t, df_t in df.items()}
    _VOCAB = set(_IDF.keys())

    for ch in chunks:
        tf = Counter(_tokenize(ch.text))
        # tf-idf pesato
        vec = {}
        norm = 0.0
        for t, cnt in tf.items():
            if t not in _IDF:
                continue
            w = (1 + math.log(cnt)) * _IDF[t]
            vec[t] = w
            norm += w * w
        norm = math.sqrt(norm) if norm > 0 else 1.0
        ch.tfidf = {t: w / norm for t, w in vec.items()}

def _cosine_score(vec_q: Dict[str, float], vec_d: Dict[str, float]) -> float:
    # prodotto scalare
    if not vec_q or not vec_d:
        return 0.0
    if len(vec_q) > len(vec_d):
        vec_q, vec_d = vec_d, vec_q
    s = 0.0
    for t, wq in vec_q.items():
        wd = vec_d.get(t)
        if wd:
            s += wq * wd
    return s

def _make_query_vec(q: str) -> Dict[str, float]:
    toks = _tokenize(q)
    tf = Counter(toks)
    vec = {}
    norm = 0.0
    for t, cnt in tf.items():
        idf = _IDF.get(t)
        if not idf:
            continue
        w = (1 + math.log(cnt)) * idf
        vec[t] = w
        norm += w * w
    norm = math.sqrt(norm) if norm > 0 else 1.0
    return {t: w / norm for t, w in vec.items()}

# ===========================
# LOADING / INDEX
# ===========================
def reload_index() -> None:
    """Scansiona DOC_DIR e ricostruisce l'indice in memoria."""
    global _INDEX
    _INDEX = []

    pattern = os.path.join(DOC_DIR, "**", "*.txt")
    files = sorted(glob.glob(pattern, recursive=True))

    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception:
            continue

        tags, body = _extract_tags_and_body(raw)
        for section, chunk in _split_into_chunks(body):
            _INDEX.append(DocChunk(
                doc=os.path.basename(fp),
                section=section,
                text=chunk,
                tags=tags,
                tfidf={}
            ))

    if not _INDEX:
        # nessun file trovato: indice "vuoto" con un placeholder
        _INDEX.append(DocChunk(doc="__empty__", section="__empty__", text="", tags=[], tfidf={}))

    _build_tfidf(_INDEX)

# ===========================
# SEARCH
# ===========================
def _normalize_query(q: str) -> str:
    q = q.lower().strip()
    # rimuovi stopwords esplicite
    toks = [t for t in re.split(r"\s+", q) if t and t not in STOPWORDS]
    return " ".join(toks).strip() or q

def _expand_keywords(q: str) -> List[str]:
    """Se becca keyword chiare, espande la query con sinonimi utili."""
    q_low = q.lower()
    expansions = []
    for k, arr in KW_MAP.items():
        if k in q_low:
            expansions.extend(arr)
    if expansions:
        expansions.append(q_low)
        return list(dict.fromkeys(expansions))  # dedup, mantiene ordine
    return [q_low]

def _tag_boost(q_tokens: List[str], tags: List[str]) -> float:
    """Boost semplice: +0.08 per ogni match query-token ∩ tags (max 0.4)."""
    if not tags or not q_tokens:
        return 0.0
    tagset = set(tags)
    hits = sum(1 for t in q_tokens if t in tagset)
    return min(0.4, hits * 0.08)

def _search(q: str, top_k: int = MAX_TOP_K) -> List[Tuple[float, DocChunk]]:
    if not _INDEX:
        reload_index()

    q_norm = _normalize_query(q)
    queries = _expand_keywords(q_norm)

    # calcola un vettore tfidf per ogni variante di query
    q_vecs = [(_make_query_vec(qv), _tokenize(qv)) for qv in queries]

    scored: List[Tuple[float, DocChunk]] = []

    for ch in _INDEX:
        best = 0.0
        for vec_q, toks_q in q_vecs:
            base = _cosine_score(vec_q, ch.tfidf)
            boost = _tag_boost(toks_q, ch.tags)
            score = base + boost
            if score > best:
                best = score
        if best > 0:
            scored.append((best, ch))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_k]

# ===========================
# PUBLIC API
# ===========================
def risposta_document_first(domanda: str) -> str:
    """
    Ritorna il miglior testo di risposta pescato dai documenti locali.
    - Ignora 'tecnaria' e altre stopwords (quindi la parola è scontata).
    - Usa TF-IDF + cosine + boost TAGS.
    - Applica una soglia SIMILARITY_THRESHOLD.
    """
    domanda = domanda or ""
    domanda = domanda.strip()
    if not domanda:
        return ""

    results = _search(domanda, top_k=MAX_TOP_K)
    if not results:
        return ""

    best_score, best_chunk = results[0]
    if best_score < SIMILARITY_THRESHOLD:
        return ""

    # Restituiamo solo il testo "pulito" (nessuna intestazione automatica)
    return best_chunk.text.strip()

# Carica indice all'import, pronto all'uso
try:
    reload_index()
except Exception:
    # non bloccare l'app, l'utente potrà chiamare reload_index manualmente
    pass

# ===========================
# CLI UTILE (facoltativo)
# ===========================
if __name__ == "__main__":
    print(f"[INFO] DOC_DIR={DOC_DIR}  threshold={SIMILARITY_THRESHOLD}")
    while True:
        try:
            q = input("Domanda> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break
        if not q:
            continue
        ans = risposta_document_first(q)
        if ans:
            print("\n--- RISPOSTA ---")
            print(ans)
        else:
            print("\n[WARN] Nessun match sufficiente. Prova a riformulare.")
        print()
