# -*- coding: utf-8 -*-
from __future__ import annotations
import os, re, math, glob
from dataclasses import dataclass
from collections import Counter, defaultdict
from typing import List, Dict, Tuple, Optional

# ===========================
# CONFIG
# ===========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOC_DIR = os.getenv("DOC_DIR") or os.path.join(BASE_DIR, "documenti_gTab")

SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.10"))
MIN_CHARS_PER_CHUNK = int(os.getenv("MIN_CHARS_PER_CHUNK", "150"))
OVERLAP_CHARS       = int(os.getenv("OVERLAP_CHARS", "50"))
MAX_TOP_K           = int(os.getenv("TOP_K", "5"))
DEBUG               = os.getenv("DEBUG", "1") == "1"

STOPWORDS = {
    "tecnaria","spa","s.p.a.","il","lo","la","i","gli","le","un","uno","una",
    "di","del","della","dell","dei","degli","delle","e","ed","o","con","per",
    "su","tra","fra","in","da","al","allo","ai","agli","alla","alle","che",
    "come","quale","quali","dove","quando","anche"
}

# Router intent
INTENTS = {
    "contatti": {
        "keywords": ["contatti","telefono","email","orari","sede","indirizzo"],
        "prefer_file_substr": ["contatti","orari"],
    },
    "certificazioni": {
        "keywords": ["certificazioni","iso","ce","dop","eurocodici","ntc"],
        "prefer_file_substr": ["certificaz"],
    },
    "stabilimenti": {
        "keywords": ["stabilimenti","produzione","reparto","logistica","fabbrica","impianto"],
        "prefer_file_substr": ["stabilimenti","produzione"],
    },
    "profilo": {
        "keywords": ["profilo","chi siete","chi siamo","azienda","storia","presentazione"],
        "prefer_file_substr": ["profilo"],
    },
    "vision": {
        "keywords": ["vision","visione","futuro","strategia"],
        "prefer_file_substr": ["vision"],
    },
    "mission": {
        "keywords": ["mission","missione","valori","obiettivi"],
        "prefer_file_substr": ["mission"],
    },
}

# ===========================
# DATA
# ===========================
@dataclass
class DocChunk:
    doc: str
    section: str
    text: str
    tags: List[str]
    tfidf: Dict[str, float]

_INDEX: List[DocChunk] = []
_IDF: Dict[str, float] = {}
_LAST_LOAD_SUMMARY: str = ""

# ===========================
# UTILS
# ===========================
def _dbg(msg: str) -> None:
    if DEBUG:
        print(f"[DEBUG] {msg}")

def _clean(s: str) -> str:
    s = s.replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def _tokenize(s: str) -> List[str]:
    s = s.lower()
    s = re.sub(r"[^a-z0-9àèéìòóùüç]+", " ", s, flags=re.IGNORECASE)
    r
