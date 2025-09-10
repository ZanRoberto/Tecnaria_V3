# -*- coding: utf-8 -*-
from __future__ import annotations
import os, re, math, glob
from dataclasses import dataclass
from collections import Counter, defaultdict
from typing import List, Dict, Tuple

# ===========================
# CONFIG
# ===========================
DOC_DIR = os.getenv("DOC_DIR", "documenti_gTab")
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.2"))  # soglia bassa
MIN_CHARS_PER_CHUNK = int(os.getenv("MIN_CHARS_PER_CHUNK", "400"))
OVERLAP_CHARS = int(os.getenv("OVERLAP_CHARS", "100"))
MAX_TOP_K = int(os.getenv("TOP_K", "5"))

STOPWORDS = {"tecnaria", "spa", "s.p.a."}

@dataclass
class DocChunk:
    doc: str
    section: str
    text: str
    tags: List[str]
    tfidf: Dict[str, float]

_INDEX: List[DocChunk] = []
_IDF: Dict[str, float] = {}
_VOCAB: set = set()

# ===========================
# UTILS
# ===========================
def _tokenize(s: str) -> List[str]:
    s = s.lower()
    s = re.sub(r"[^a-z0-9àèéìòóùüç]+", " ", s)
    toks = [t for t in s.split() if t and t not in STOPWORDS]
    return toks

def _extract_tags_and_body(text: str) -> Tuple[List[str], str]:
    tags = []
    lines = text.splitlines()
    if lines and lines[0].strip().lower().startswith("[tags:"):
        m = re.match(r"\[tags:\s*(.*?)\s*\]", lines[0].strip(), flags=re.IGNORECASE)
        if m:
            raw = m.group(1)
            tags = [t.strip().lower() for t in re.split(r"[;,]", raw) if t.strip()]
        body = "\n".join(lines[1:]).strip()
        return tags, body
    return tags, text.strip()

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
# TF-IDF
# ===========================
def _build_tfidf(chunks: List[DocChunk]) -> None:
    global _IDF, _VOCAB
    df = defaultdict(int)
    for ch in chunks:
        toks = set(_tokenize(ch.text))
        for t in toks:
            df[t] += 1
    N = max(1, len(chunks))
    _IDF = {t: math.log((N + 1) / (df_t + 1)) + 1.0 for t, df_t in df.items()}
    _VOCAB = set(_IDF.keys())

    for ch in chunks:
        tf = Counter(_tokenize(ch.text))
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
# INDEXING
# ===========================
def reload_index() -> None:
    global _INDEX
    _INDEX = []
    pattern = os.path.join(DOC_DIR, "**", "*.txt")
    files = sorted(glob.glob(pattern, recursive=True))
    print(f"[DEBUG] Carico {len(files)} file da {DOC_DIR}")
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception as e:
            print(f"[WARN] Impossibile leggere {fp}: {e}")
            continue

        tags, body = _extract_tags_and_body(raw)
        for chunk in _sliding_chunks(body, MIN_CHARS_PER_CHUNK, OVERLAP_CHARS):
            _INDEX.append(DocChunk(
                doc=os.path.basename(fp),
                section="",
                text=chunk,
                tags=tags,
                tfidf={}
            ))
    print(f"[DEBUG] Creati {_INDEX and len(_INDEX) or 0} chunk totali")
    _build_tfidf(_INDEX)

# ===========================
# SEARCH
# ===========================
def risposta_document_first(domanda: str) -> str:
    domanda = domanda.strip()
    if not domanda:
        return ""
    if not _INDEX:
        reload_index()

    vec_q = _make_query_vec(domanda)
    scored = []
    for ch in _INDEX:
        score = _cosine_score(vec_q, ch.tfidf)
        # leggero boost se matcha tag
        if ch.tags:
            q_toks = set(_tokenize(domanda))
            if q_toks & set(ch.tags):
                score += 0.2
        if score > 0:
            scored.append((score, ch))
    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        print(f"[DEBUG] Nessun match per domanda: {domanda}")
        return ""
    best_score, best_chunk = scored[0]
    print(f"[DEBUG] Miglior match {best_chunk.doc} score={best_score:.3f}")
    if best_score < SIMILARITY_THRESHOLD:
        print(f"[DEBUG] Scartato per soglia (score {best_score:.3f} < {SIMILARITY_THRESHOLD})")
        return ""
    return best_chunk.text.strip()

# ===========================
# CLI
# ===========================
if __name__ == "__main__":
    print(f"[INFO] DOC_DIR={DOC_DIR}, soglia={SIMILARITY_THRESHOLD}")
    reload_index()
    while True:
        try:
            q = input("Domanda> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        ans = risposta_document_first(q)
        print("--- RISPOSTA ---")
        print(ans or "[vuota]")
        print()
