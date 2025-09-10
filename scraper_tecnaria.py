# scraper_tecnaria.py
# ------------------------------------------------------------
# Knowledge base loader + simple semantic retrieval for Tecnaria
# - Indicizza automaticamente TUTTI i .txt in documenti_gTab/
# - Formato atteso: prima riga opzionale [TAGS: ...], poi 1 riga = 1 risposta
# - Nessuna "Q:" nelle risposte. Il cliente vede solo la risposta.
# - Stopwords MINIME: "tecnaria", "spa", "s.p.a." (Tecnaria è scontata).
# - Soglia morbida: scegliamo sempre il best match (no silenzi inutili).
# - Utilities: reload(), answer(), answer_with_source(), debug_candidates()
# ------------------------------------------------------------

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Tuple

# -----------------------------
# Configurazione cartelle
# -----------------------------
BASE_DIR = Path(__file__).parent.resolve()
DOC_DIR = BASE_DIR / "documenti_gTab"

# -----------------------------
# Stopwords (minimali)
# -----------------------------
STOPWORDS = {"tecnaria", "spa", "s.p.a."}

# Regex per tokenizzazione semplice (parole in minuscolo)
TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+")

# -----------------------------
# Dataclass per una riga indicizzata
# -----------------------------
@dataclass
class KBEntry:
    text: str                 # la riga/risposta
    filename: str             # nome file
    tags: List[str]           # tags puliti
    tokens: List[str]         # token della riga
    tag_tokens: List[str]     # token dai TAGS

# -----------------------------
# Utility di normalizzazione
# -----------------------------
def normalize_text(s: str) -> str:
    return " ".join(s.strip().split())

def tokenize(s: str) -> List[str]:
    toks = [t.lower() for t in TOKEN_RE.findall(s)]
    return [t for t in toks if t not in STOPWORDS]

def parse_tags(line: str) -> List[str]:
    # line es.: [TAGS: a, b, c]
    inside = line.strip()[1:-1]  # rimuove [ ]
    after = inside.split(":", 1)[1] if ":" in inside else inside
    parts = [p.strip() for p in after.split(",")]
    return [p for p in parts if p]

# -----------------------------
# Indicizzazione
# -----------------------------
class KnowledgeBase:
    def __init__(self, doc_dir: Path):
        self.doc_dir = Path(doc_dir)
        self.entries: List[KBEntry] = []
        self.files_loaded: List[str] = []
        self._index()

    def _index(self):
        self.entries.clear()
        self.files_loaded.clear()

        if not self.doc_dir.exists():
            print(f"[scraper_tecnaria] ATTENZIONE: cartella non trovata: {self.doc_dir}")
            return

        for path in sorted(self.doc_dir.rglob("*.txt")):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                print(f"[scraper_tecnaria] ERRORE lettura {path}: {e}")
                continue

            lines = [normalize_text(l) for l in text.splitlines()]
            lines = [l for l in lines if l]  # rimuove righe vuote

            tags: List[str] = []
            tag_tokens: List[str] = []

            # Se la prima riga è [TAGS: ...], estrai
            if lines and lines[0].startswith("[TAGS:") and lines[0].endswith("]"):
                tags = parse_tags(lines[0])
                tag_tokens = tokenize(" ".join(tags))
                # resto delle righe sono risposte
                content_lines = lines[1:]
            else:
                content_lines = lines

            # Indicizza ogni riga come risposta autonoma
            for line in content_lines:
                if not line:
                    continue
                entry = KBEntry(
                    text=line,
                    filename=path.name,
                    tags=tags,
                    tokens=tokenize(line),
                    tag_tokens=tag_tokens,
                )
                self.entries.append(entry)

            self.files_loaded.append(path.name)

        print(f"[scraper_tecnaria] Indicizzati {len(self.entries)} blocchi da {len(self.files_loaded)} file.")

    # -------------------------
    # Scoring semplice + boost TAGS
    # -------------------------
    @staticmethod
    def _overlap_score(q_tokens: List[str], e_tokens: List[str]) -> float:
        if not q_tokens or not e_tokens:
            return 0.0
        q = set(q_tokens)
        e = set(e_tokens)
        inter = len(q & e)
        # piccola normalizzazione per non favorire frasi lunghissime
        return inter / (0.5 * (len(q) + len(e)))

    @staticmethod
    def _bigram_boost(q_tokens: List[str], e_tokens: List[str]) -> float:
        # boost se compaiono bigrammi interi della query nel testo
        def bigrams(toks):
            return set(zip(toks, toks[1:])) if len(toks) > 1 else set()
        q_bi = bigrams(q_tokens)
        e_bi = bigrams(e_tokens)
        if not q_bi or not e_bi:
            return 0.0
        inter = len(q_bi & e_bi)
        return 0.15 * inter  # boost moderato

    @staticmethod
    def _tag_boost(q_tokens: List[str], tag_tokens: List[str]) -> float:
        if not q_tokens or not tag_tokens:
            return 0.0
        q = set(q_tokens)
        t = set(tag_tokens)
        inter = len(q & t)
        # i TAGS sono un forte segnale: boost lineare
        return 0.25 * inter

    def _score_entry(self, q_tokens: List[str], e: KBEntry) -> float:
        base = self._overlap_score(q_tokens, e.tokens)
        return base + self._bigram_boost(q_tokens, e.tokens) + self._tag_boost(q_tokens, e.tag_tokens)

    # -------------------------
    # API pubbliche
    # -------------------------
    def reload(self) -> Dict[str, int]:
        self._index()
        return {"files": len(self.files_loaded), "entries": len(self.entries)}

    def answer(self, query: str) -> str:
        """
        Restituisce SOLO la risposta (testo della riga migliore).
        Mai menzionare 'documentazione locale' o il nome file al cliente.
        """
        if not self.entries:
            return "Nessun documento caricato. Aggiungi file in 'documenti_gTab/' e ricarica l'indice."

        q_tokens = tokenize(query)
        # se la query è troppo corta, non scartiamo nulla: prendiamo comunque il best match
        best_score = -1.0
        best_entry = None

        for e in self.entries:
            s = self._score_entry(q_tokens, e)
            if s > best_score:
                best_score = s
                best_entry = e

        # Ritorna sempre il best match, anche se scarso
        return best_entry.text if best_entry else "Non ho trovato contenuti utili."

    def answer_with_source(self, query: str) -> Tuple[str, str, float]:
        """
        Come answer(), ma restituisce anche (filename, punteggio) per debug/log.
        NON mostrare filename all'utente finale.
        """
        if not self.entries:
            return ("Nessun documento caricato. Aggiungi file in 'documenti_gTab/' e ricarica l'indice.", "", 0.0)

        q_tokens = tokenize(query)
        best_score = -1.0
        best_entry = None

        for e in self.entries:
            s = self._score_entry(q_tokens, e)
            if s > best_score:
                best_score = s
                best_entry = e

        if best_entry is None:
            return ("Non ho trovato contenuti utili.", "", 0.0)

        return (best_entry.text, best_entry.filename, best_score)

    def debug_candidates(self, query: str, top: int = 5) -> List[Tuple[str, str, float]]:
        """
        Ritorna i TOP candidati con (filename, testo, punteggio) per ispezione.
        Utile per endpoint /debug del tuo backend.
        """
        q_tokens = tokenize(query)
        scored = []
        for e in self.entries:
            s = self._score_entry(q_tokens, e)
            scored.append((e, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        out = []
        for e, s in scored[:top]:
            out.append((e.filename, e.text, round(s, 4)))
        return out

# Istanza globale pronta da importare
kb = KnowledgeBase(DOC_DIR)

# -----------------------------
# CLI di test (opzionale)
# -----------------------------
if __name__ == "__main__":
    print(">>> Tecnaria KB - Test rapido")
    print(f"Cartella documenti: {DOC_DIR}")
    print(f"File indicizzati: {len(kb.files_loaded)} | Righe indicizzate: {len(kb.entries)}\n")

    import sys
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "mi dai i contatti?"
    ans, src, score = kb.answer_with_source(q)
    print(f"Q: {q}")
    print(f"A: {ans}")
    print(f"[DEBUG] source={src} score={score:.4f}")
