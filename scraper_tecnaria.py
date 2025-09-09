# scraper_tecnaria.py
# -*- coding: utf-8 -*-
"""
Ricerca document-first veloce:
- Indicizza una sola volta all'avvio (RAM)
- Prefiltro rapido su mini-testo
- RapidFuzz solo su pochi candidati
- LRU cache per query ripetute
- Snippet centrato sui termini
- reload_index() per ricaricare senza redeploy
"""

from __future__ import annotations
import os, re, time
from typing import List, Dict, Optional, Tuple
from functools import lru_cache
from dotenv import load_dotenv
from rapidfuzz import fuzz, process
from knowledge_loader import load_with_cache

load_dotenv()

# ===== Config da ENV =====
KNOWLEDGE_DIR = os.getenv("KNOWLEDGE_DIR", "./documenti_gTab")
SIM_THRESHOLD = int(os.getenv("SIMILARITY_THRESHOLD", "65"))
MAX_MATCHES   = int(os.getenv("MAX_MATCHES", "6"))

# Tuning performance
MAX_TEXT_PER_DOC   = int(os.getenv("MAX_TEXT_PER_DOC",  "20000"))
MINI_TEXT_PER_DOC  = int(os.getenv("MINI_TEXT_PER_DOC", "3000"))
CANDIDATES_K       = int(os.getenv("CANDIDATES_K",      "30"))
MIN_TOKEN_LEN      = int(os.getenv("MIN_TOKEN_LEN",     "3"))

# ===== Indice in memoria =====
CORPUS: Dict[str, str] = {}
MINI: Dict[str, str] = {}
DOCS_LIST: List[Tuple[str, str]] = []
INDEX_STAMP: float = 0.0

def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def _tokenize(s: str) -> List[str]:
    tokens = re.findall(r"[a-zA-ZÀ-ÖØ-öø-ÿ0-9_]+", (s or "").lower())
    return [t for t in tokens if len(t) >= MIN_TOKEN_LEN]

def _build_index() -> None:
    global CORPUS, MINI, DOCS_LIST, INDEX_STAMP
    recs = load_with_cache(KNOWLEDGE_DIR)
    corpus = {}
    mini   = {}
    for r in recs:
        rel = r["relpath"]
        t = (r.get("text") or "")
        if not t:
            continue
        t = t[:MAX_TEXT_PER_DOC]
        t_low = _normalize_text(t)
        corpus[rel] = t_low
        mini[rel]   = t_low[:MINI_TEXT_PER_DOC]
    CORPUS = corpus
    MINI = mini
    DOCS_LIST = list(CORPUS.items())
    INDEX_STAMP = time.time()

# Costruisci indice all'import
_build_index()

def reload_index() -> int:
    """Ricarica l'indice (dopo nuovi file). Ritorna quanti documenti indicizzati."""
    _build_index()
    _search_cached.cache_clear()
    return len(CORPUS)

def _prefilter_candidates(domanda: str, k: int) -> List[str]:
    """Candidati veloci via overlap token nel mini-testo."""
    if not MINI:
        return []
    q_tokens = set(_tokenize(domanda))
    if not q_tokens:
        return [rel for rel, _ in DOCS_LIST[:k]]
    scored: List[Tuple[str, int]] = []
    for rel, txt in MINI.items():
        hit = 0
        for t in q_tokens:
            if t in txt:
                hit += 1
        if hit > 0:
            scored.append((rel, hit))
    if not scored:
        return [rel for rel, _ in DOCS_LIST[:k]]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [rel for rel, _ in scored[:k]]

def _best_snippet(text: str, domanda: str, width: int = 360) -> str:
    """Snippet centrato sul primo token della query."""
    if not text:
        return ""
    t = text
    tokens = _tokenize(domanda)
    pos = -1
    for tok in tokens:
        pos = t.find(tok)
        if pos != -1:
            break
    if pos == -1:
        return (t[:width] + "…") if len(t) > width else t
    start = max(0, pos - width // 2)
    end = min(len(t), pos + width // 2)
    snippet = t[start:end]
    if start > 0: snippet = "…" + snippet
    if end < len(t): snippet = snippet + "…"
    return snippet

@lru_cache(maxsize=128)
def _search_cached(domanda: str, th: int, k: int) -> List[Dict]:
    if not (domanda or "").strip():
        return []
    candidates = _prefilter_candidates(domanda, CANDIDATES_K)
    if not candidates:
        return []
    cand_corpus = {rel: CORPUS.get(rel, "") for rel in candidates if CORPUS.get(rel)}
    if not cand_corpus:
        return []
    results = process.extract(
        domanda, cand_corpus,
        scorer=fuzz.token_set_ratio,
        limit=max(k, 10),
        score_cutoff=th
    )
    hits: List[Dict] = []
    for rel, score, txt in results:
        hits.append({
            "file": rel,
            "score": int(score),
            "snippet": _best_snippet(txt, domanda, width=360)
        })
        if len(hits) >= k:
            break
    return hits

def cerca_nei_documenti(domanda: str, threshold: Optional[int] = None, max_matches: Optional[int] = None) -> List[Dict]:
    th = int(threshold or SIM_THRESHOLD)
    k  = int(max_matches or MAX_MATCHES)
    return _search_cached((domanda or "").strip(), th, k)

def risposta_document_first(domanda: str) -> Optional[str]:
    hits = cerca_nei_documenti(domanda)
    if not hits:
        return None
    blocchi = [f"📄 **{h['file']}** (score: {h['score']})\n\n{h['snippet']}" for h in hits]
    return "\n\n---\n\n".join(blocchi)
