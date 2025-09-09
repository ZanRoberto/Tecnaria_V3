# -*- coding: utf-8 -*-
"""
scraper_tecnaria.py — versione performance:
- Indicizza TUTTI i documenti una volta sola all'avvio (RAM)
- Prefiltro veloce con keyword overlap su mini-test (prime N chars)
- RapidFuzz solo sui candidati (K piccoli)
- LRU cache per query ripetute
- Snippet centrato sui termini della query
- /reload gestito via funzione reload_index() (chiamabile da app.py)
"""

from __future__ import annotations
import os, re, time
from typing import List, Dict, Optional, Tuple
from functools import lru_cache
from dotenv import load_dotenv
from rapidfuzz import fuzz, process
from knowledge_loader import load_with_cache

load_dotenv()

# ====== Config da ENV ======
KNOWLEDGE_DIR = os.getenv("KNOWLEDGE_DIR", "./documenti_gTab")
SIM_THRESHOLD = int(os.getenv("SIMILARITY_THRESHOLD", "65"))
MAX_MATCHES   = int(os.getenv("MAX_MATCHES", "6"))

# Limiti per performance (puoi regolare da ENV se vuoi)
MAX_TEXT_PER_DOC   = int(os.getenv("MAX_TEXT_PER_DOC", "20000"))   # usa al max 20k char/doc per scoring
MINI_TEXT_PER_DOC  = int(os.getenv("MINI_TEXT_PER_DOC", "3000"))   # mini-test per prefiltrare
CANDIDATES_K       = int(os.getenv("CANDIDATES_K", "30"))          # quanti doc passano al fuzzy
MIN_TOKEN_LEN      = int(os.getenv("MIN_TOKEN_LEN", "3"))          # token corti sono rumorosi

# ====== Indice in memoria ======
# CORPUS: {relpath: testo_lower_limitato}
# MINI:   {relpath: mini_testo_lower}  (prefiltro velocissimo)
CORPUS: Dict[str, str] = {}
MINI: Dict[str, str] = {}
DOCS_LIST: List[Tuple[str, str]] = []  # (relpath, testo_lower_limitato)
INDEX_STAMP: float = 0.0


def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def _tokenize(s: str) -> List[str]:
    # tokenizza per parole alfanumeriche, filtra token brevi
    tokens = re.findall(r"[a-zA-ZÀ-ÖØ-öø-ÿ0-9_]+", s.lower())
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
        mini[rel]   = t_l_
