# scraper_tecnaria.py
# Modalità "document-only" per Tecnaria: ricerca esclusiva nei .txt locali.

import os
import glob
import re
from typing import List, Tuple

DOC_FOLDER = os.getenv("DOC_FOLDER", "./documenti_gTab")
BOT_OFFLINE_ONLY = os.getenv("BOT_OFFLINE_ONLY", "true").lower() == "true"

# Cache documenti: lista di tuple (path, text)
_DOCS_CACHE: List[Tuple[str, str]] = []


# -------------------------
# Caricamento / indicizzazione
# -------------------------
def _read_txt(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def _build_cache() -> None:
    global _DOCS_CACHE
    paths = sorted(glob.glob(os.path.join(DOC_FOLDER, "*.txt")))
    _DOCS_CACHE = [(p, _read_txt(p)) for p in paths]

# carica all'import
_build_cache()

def reload_index() -> int:
    """
    Ricarica l'indice dei .txt.
    Richiamare dopo aver aggiunto/modificato file in documenti_gTab/.
    Ritorna il numero di documenti indicizzati.
    """
    _build_cache()
    return len(_DOCS_CACHE)


# -------------------------
# Ricerca locale (no web)
# -------------------------
def _score(query: str, text: str) -> float:
    """
    Semplice scoring: somma delle occorrenze dei token della query nel testo,
    con bonus per match esatto della query completa.
    """
    q = query.lower().strip()
    t = text.lower()
    words = set(re.findall(r"\w+", q))
    overlap = sum(t.count(w) for w in words if len(w) > 2)
    exact = 2.0 if q and q in t else 0.0
    return overlap + exact

def _top_hits(query: str, k: int = 5):
    """
    Restituisce i migliori k documenti come lista di tuple (score, path, snippet).
    Lo snippet è un estratto intorno alla prima occorrenza della query.
    """
    results = []
    ql = query.lower()
    for path, text in _DOCS_CACHE:
        s = _score(query, text)
        if s <= 0:
            continue
        idx = text.lower().find(ql)
        if idx < 0:
            idx = 0
        s0 = max(0, idx - 320)
        s1 = min(len(text), idx + 320)
        snippet = text[s0:s1].strip()
        results.append((s, path, snippet))
    results.sort(key=lambda x: x[0], reverse=True)
    return results[:k]


# -------------------------
# Risposta SOLO da documenti
# -------------------------
def risposta_document_first(question: str) -> dict:
    """
    Risponde ESCLUSIVAMENTE usando i .txt locali in documenti_gTab/.
    Nessun accesso web. Nessun fallback esterno.
    """
    hits = _top_hits(question, k=5)
    THRESHOLD = 3.0  # alza/abbassa se vuoi più/meno prudenza

    if not hits or hits[0][0] < THRESHOLD:
        return {
            "found": False,
            "answer": (
                "Non trovo riferimenti sufficienti nella documentazione locale Tecnaria "
                "per questa domanda. Per favore specifica prodotto/argomento Tecnaria "
                "oppure riformula la richiesta."
            ),
            "sources": []
        }

    # Costruzione risposta con fonti
    parts = []
    sources = []
    for _, path, snippet in hits:
        title = os.path.basename(path).replace(".txt", "")
        parts.append(f"📄 **{title}**\n{snippet}")
        sources.append(path)

    answer = "Risposta basata su documentazione Tecnaria locale:\n\n" + "\n\n---\n\n".join(parts)
    return {"found": True, "answer": answer, "sources": sources}
