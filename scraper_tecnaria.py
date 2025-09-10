# scraper_tecnaria.py
# ------------------------------------------------------------
# KB Tecnaria: indicizza TUTTI i .txt della cartella documenti (anche sottocartelle)
# Percorso robusto:
#   1) env TECNARIA_DOC_DIR (assoluto o relativo)
#   2) ./documenti_gTab accanto a questo file
#   3) ./documenti_gTab dalla working dir attuale (CWD)
# Log dettagliati per capire subito cosa sta succedendo su Render.
# ------------------------------------------------------------

from __future__ import annotations
import os, re, sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Tuple

# -----------------------------
# Stopwords MINIME (Tecnaria scontata)
# -----------------------------
STOPWORDS = {"tecnaria", "spa", "s.p.a."}
TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+")

@dataclass
class KBEntry:
    text: str
    filename: str
    tags: List[str]
    tokens: List[str]
    tag_tokens: List[str]

def normalize_text(s: str) -> str:
    return " ".join(s.strip().split())

def tokenize(s: str) -> List[str]:
    toks = [t.lower() for t in TOKEN_RE.findall(s)]
    return [t for t in toks if t not in STOPWORDS]

def parse_tags(line: str) -> List[str]:
    # [TAGS: a, b, c]
    inside = line.strip()[1:-1]
    after = inside.split(":", 1)[1] if ":" in inside else inside
    parts = [p.strip() for p in after.split(",")]
    return [p for p in parts if p]

def _resolve_documents_dir() -> Path:
    # 1) variabile d'ambiente (consigliato su Render)
    env_dir = os.environ.get("TECNARIA_DOC_DIR", "").strip()
    candidates: List[Path] = []
    if env_dir:
        p = Path(env_dir)
        candidates.append(p if p.is_absolute() else Path.cwd() / p)

    # 2) ./documenti_gTab accanto a questo file
    here = Path(__file__).parent.resolve()
    candidates.append(here / "documenti_gTab")

    # 3) ./documenti_gTab dalla CWD
    candidates.append(Path.cwd() / "documenti_gTab")

    for c in candidates:
        if c.exists() and c.is_dir():
            print(f"[scraper_tecnaria] Cartella documenti selezionata: {c}")
            return c

    # ultimo fallback: ritorna la prima ipotesi (anche se non esiste) per messaggio chiaro
    fallback = candidates[0]
    print(f"[scraper_tecnaria] ATTENZIONE: nessuna cartella valida trovata. Tentativo: {fallback}")
    return fallback

class KnowledgeBase:
    def __init__(self, doc_dir: Path | None = None):
        self.doc_dir = doc_dir or _resolve_documents_dir()
        self.entries: List[KBEntry] = []
        self.files_loaded: List[str] = []
        self._index()

    def _iter_txt_files(self, root: Path):
        # rglob con filtro case-insensitive su estensione
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() == ".txt":
                yield p

    def _index(self):
        self.entries.clear()
        self.files_loaded.clear()

        print(f"[scraper_tecnaria] Inizio indicizzazione da: {self.doc_dir}")
        if not self.doc_dir.exists():
            print(f"[scraper_tecnaria] ERRORE: cartella inesistente -> {self.doc_dir}")
            return

        all_files = list(self._iter_txt_files(self.doc_dir))
        if not all_files:
            print(f"[scraper_tecnaria] Nessun .txt trovato in: {self.doc_dir}")
        else:
            print(f"[scraper_tecnaria] Trovati {len(all_files)} file .txt:")
            for f in all_files:
                print(f"  - {f}")

        for path in all_files:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                print(f"[scraper_tecnaria] ERRORE lettura {path}: {e}")
                continue

            lines = [normalize_text(l) for l in text.splitlines()]
            lines = [l for l in lines if l]

            tags: List[str] = []
            tag_tokens: List[str] = []

            if lines and lines[0].startswith("[TAGS:") and lines[0].endswith("]"):
                tags = parse_tags(lines[0])
                tag_tokens = tokenize(" ".join(tags))
                content_lines = lines[1:]
            else:
                content_lines = lines

            for line in content_lines:
                if not line:
                    continue
                self.entries.append(
                    KBEntry(
                        text=line,
                        filename=path.name,
                        tags=tags,
                        tokens=tokenize(line),
                        tag_tokens=tag_tokens,
                    )
                )

            self.files_loaded.append(path.name)

        print(f"[scraper_tecnaria] Indicizzati {len(self.entries)} righe da {len(self.files_loaded)} file.")

    # -------------------------
    # Scoring (overlap + bigram + TAG boost)
    # -------------------------
    @staticmethod
    def _overlap_score(q_tokens: List[str], e_tokens: List[str]) -> float:
        if not q_tokens or not e_tokens:
            return 0.0
        q, e = set(q_tokens), set(e_tokens)
        inter = len(q & e)
        return inter / (0.5 * (len(q) + len(e)))

    @staticmethod
    def _bigram_boost(q_tokens: List[str], e_tokens: List[str]) -> float:
        def bigrams(toks):
            return set(zip(toks, toks[1:])) if len(toks) > 1 else set()
        q_bi, e_bi = bigrams(q_tokens), bigrams(e_tokens)
        if not q_bi or not e_bi:
            return 0.0
        inter = len(q_bi & e_bi)
        return 0.15 * inter

    @staticmethod
    def _tag_boost(q_tokens: List[str], tag_tokens: List[str]) -> float:
        if not q_tokens or not tag_tokens:
            return 0.0
        q, t = set(q_tokens), set(tag_tokens)
        inter = len(q & t)
        return 0.25 * inter

    def _score_entry(self, q_tokens: List[str], e: KBEntry) -> float:
        return (
            self._overlap_score(q_tokens, e.tokens)
            + self._bigram_boost(q_tokens, e.tokens)
            + self._tag_boost(q_tokens, e.tag_tokens)
        )

    # -------------------------
    # API
    # -------------------------
    def reload(self) -> Dict[str, int]:
        # Rileggi eventualmente la variabile d’ambiente (se cambiata su Render)
        self.doc_dir = _resolve_documents_dir()
        self._index()
        return {"files": len(self.files_loaded), "entries": len(self.entries), "doc_dir": str(self.doc_dir)}

    def answer(self, query: str) -> str:
        if not self.entries:
            return "Nessun documento caricato. Verifica la cartella documenti e ricarica l'indice."
        q_tokens = tokenize(query)
        best, best_s = None, -1.0
        for e in self.entries:
            s = self._score_entry(q_tokens, e)
            if s > best_s:
                best_s, best = s, e
        return best.text if best else "Non ho trovato contenuti utili."

    def answer_with_source(self, query: str) -> Tuple[str, str, float]:
        if not self.entries:
            return ("Nessun documento caricato. Verifica la cartella documenti e ricarica l'indice.", "", 0.0)
        q_tokens = tokenize(query)
        best, best_s = None, -1.0
        for e in self.entries:
            s = self._score_entry(q_tokens, e)
            if s > best_s:
                best_s, best = s, e
        if not best:
            return ("Non ho trovato contenuti utili.", "", 0.0)
        return (best.text, best.filename, best_s)

    def debug_candidates(self, query: str, top: int = 5) -> List[Tuple[str, str, float]]:
        q_tokens = tokenize(query)
        scored = []
        for e in self.entries:
            scored.append((e, self._score_entry(q_tokens, e)))
        scored.sort(key=lambda x: x[1], reverse=True)
        out = []
        for e, s in scored[:top]:
            out.append((e.filename, e.text, round(s, 4)))
        return out

# Istanza globale
kb = KnowledgeBase()

if __name__ == "__main__":
    print(">>> Tecnaria KB - Test rapido")
    info = kb.reload()
    print(f"[INFO] {info}")
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "orari sede"
    a, src, sc = kb.answer_with_source(q)
    print(f"Q: {q}")
    print(f"A: {a}")
    print(f"[DEBUG] source={src} score={sc:.4f}")
