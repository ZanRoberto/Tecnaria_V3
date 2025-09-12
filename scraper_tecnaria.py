# -*- coding: utf-8 -*-
# scraper_tecnaria.py — robusto, ibrido, con fallback e post-process
import os
import re
import json
import math
from typing import Any, Dict, List, Optional, Tuple

from collections import defaultdict

# Dipendenze "leggere" (già nel tuo requirements)
from rank_bm25 import BM25Okapi
from rapidfuzz import fuzz, process

# =========================
# Config
# =========================
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")
SIMILARITY_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
TOPK_SEMANTIC = int(os.environ.get("TOPK_SEMANTIC", "20"))

# Post-process risposta
MAX_ANSWER_CHARS = int(os.environ.get("MAX_ANSWER_CHARS", "700"))
ADD_TITLE = os.environ.get("ADD_TITLE", "1") == "1"

# JSON opzionale “memoria” sinaptica
SINAPSI_PATH = os.environ.get("SINAPSI_PATH", "sinapsi_brain.json")

# Stopwords minimali (italiano)
STOPWORDS_MIN = {
    "il","lo","la","i","gli","le","un","uno","una",
    "di","del","della","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da",
    "al","allo","ai","agli","alla","alle",
    "che","come","dove","quando","anche",
    "mi","ti","si","ci","vi","a","da","de","dal","dall","dalla","dalle",
}

# Sinonimi/sostituzioni (mini)
SYNONYMS = {
    r"\bp560\b": "p560 pistola sparachiodi spit",
    r"\bsparachiod[io]\b": "pistola sparachiodi",
    r"\bchiodatrice\b": "pistola sparachiodi",
    r"\bcontatti\b": "contatti recapiti telefono email sede",
    r"\bchi\s*è\s*tecnaria\b": "chi siamo tecnaria azienda profilo",
    r"\bcodici\s+connettori\b": "codici connettori elenco prodotti",
}

# =========================
# Stato globale dell’indice
# =========================
INDEX: List[Dict[str, Any]] = []        # blocchi
_TOKENS: List[List[str]] = []           # token per BM25
_BM25: Optional[BM25Okapi] = None
_READY: bool = False

# =========================
# Utility
# =========================
def normalize_text(s: str) -> str:
    s = s.lower().strip()
    # normalizza accenti base
    s = (s
         .replace("à","a").replace("è","e").replace("é","e")
         .replace("ì","i").replace("ò","o").replace("ù","u"))
    # spazi
    s = re.sub(r"\s+", " ", s)
    return s

def tokenize(s: str) -> List[str]:
    s = normalize_text(s)
    # sinonimi (espansione) soft
    for pat, repl in SYNONYMS.items():
        s = re.sub(pat, repl, s)
    # tokenizzazione
    toks = re.findall(r"[a-z0-9]+", s)
    return [t for t in toks if t not in STOPWORDS_MIN]

def _read_txt_files(doc_dir: str) -> List[Tuple[str, str]]:
    found = []
    if not os.path.isdir(doc_dir):
        return found
    for root, _, files in os.walk(doc_dir):
        for fn in files:
            if fn.lower().endswith(".txt"):
                p = os.path.join(root, fn)
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        found.append((p, f.read()))
                except Exception:
                    # prova latin-1 in fallback
                    try:
                        with open(p, "r", encoding="latin-1") as f:
                            found.append((p, f.read()))
                    except Exception:
                        pass
    return found

def _split_blocks(text: str, filename: str) -> List[Dict[str, Any]]:
    """
    Supporta blocchi stile Q/A:
    [TAGS: ...]
    D: ...
    R: ...
    ────────────────────────────
    Oppure blocchi liberi.
    """
    blocks: List[Dict[str, Any]] = []

    # split su separatori visivi
    parts = re.split(r"(?:^|\n)─{5,}.*?(?:\n|$)", text)
    for part in parts:
        t = part.strip()
        if not t:
            continue

        tags_match = re.search(r"\[TAGS:\s*(.*?)\s*\]", t, flags=re.IGNORECASE|re.DOTALL)
        tags = []
        if tags_match:
            tags = [normalize_text(x).strip() for x in tags_match.group(1).split(",") if x.strip()]

        # Q/A
        q_match = re.search(r"(?:^|\n)\s*(?:D:|Domanda:)\s*(.+?)\s*(?:\n|$)", t, flags=re.IGNORECASE|re.DOTALL)
        a_match = re.search(r"(?:^|\n)\s*(?:R:|Risposta:)\s*(.+?)\s*$", t, flags=re.IGNORECASE|re.DOTALL)

        if q_match and a_match:
            q = q_match.group(1).strip()
            a = a_match.group(1).strip()
            blocks.append({
                "filename": filename,
                "file_base": os.path.basename(filename),
                "q": q,
                "a": a,
                "tags": tags,
                "raw": t
            })
        else:
            # blocco libero
            blocks.append({
                "filename": filename,
                "file_base": os.path.basename(filename),
                "q": None,
                "a": None,
                "text": t,
                "tags": tags,
                "raw": t
            })
    return blocks

def _load_sinapsi_memory(path: str) -> List[Dict[str, Any]]:
    """
    Carica sinapsi_brain.json (o come lo chiami) se esiste.
    Struttura attesa (semplice):
      {
        "faq": [
          {"filename": "P560.txt", "q": "...", "a": "...", "tags": ["..."]},
          ...
        ]
      }
    Se non esiste o non valido: restituisce [] senza errori.
    """
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        faq = data.get("faq") or data.get("blocks") or []
        out = []
        for b in faq:
            out.append({
                "filename": b.get("filename") or "sinapsi.json",
                "file_base": os.path.basename(b.get("filename") or "sinapsi.json"),
                "q": b.get("q"),
                "a": b.get("a"),
                "text": b.get("text"),
                "tags": b.get("tags") or [],
                "raw": b.get("raw") or ""
            })
        return out
    except Exception:
        return []

def _build_bm25_corpus(blocks: List[Dict[str, Any]]) -> Tuple[List[List[str]], Optional[BM25Okapi]]:
    docs_tokens: List[List[str]] = []
    for b in blocks:
        hay = []
        if b.get("q"): hay.append(b["q"])
        if b.get("a"): hay.append(b["a"])
        if b.get("text"): hay.append(b["text"])
        if b.get("tags"): hay.append(" ".join(b["tags"]))
        # filename spinge il contesto
        hay.append(os.path.splitext(b.get("file_base",""))[0])
        tokens = tokenize(" ".join(hay))
        docs_tokens.append(tokens)
    bm25 = BM25Okapi(docs_tokens) if docs_tokens else None
    return docs_tokens, bm25

# =========================
# Costruzione indice
# =========================
def build_index(doc_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Scansiona .txt + sinapsi opzionale, costruisce corpus BM25.
    """
    global INDEX, _TOKENS, _BM25, _READY

    folder = doc_dir or DOC_DIR
    INDEX = []
    _TOKENS = []
    _BM25 = None
    _READY = False

    files = _read_txt_files(folder)
    for path, txt in files:
        INDEX.extend(_split_blocks(txt, path))

    # sinapsi opzionale
    INDEX.extend(_load_sinapsi_memory(SINAPSI_PATH))

    _TOKENS, _BM25 = _build_bm25_corpus(INDEX)
    _READY = len(INDEX) > 0

    print(f"[scraper_tecnaria] Indicizzati {len(INDEX)} blocchi da {len(files)} file (+ sinapsi:{'ok' if os.path.isfile(SINAPSI_PATH) else 'no'})")
    return {"blocks": len(INDEX), "files": len(files)}

def is_ready() -> bool:
    return bool(_READY and INDEX and _BM25)

# =========================
# Scoring ibrido
# =========================
def _boost_from_meta(blk: Dict[str, Any], q_norm: str) -> float:
    """
    Boost se:
      - TAG coincide con stringhe della query
      - nome file sembra pertinene (p560, ctf, chi_siamo, contatti, ecc.)
    """
    boost = 1.0
    tags = " ".join(blk.get("tags") or [])
    fname = os.path.splitext(blk.get("file_base",""))[0]
    hay = f"{tags} {fname}"
    if re.search(r"\bp560\b", q_norm) and re.search(r"\bp560\b", hay):
        boost *= 1.25
    if re.search(r"\b(contatti|recapiti|telefono|email)\b", q_norm) and re.search(r"\bcontatti\b|\bchi_siamo\b", hay):
        boost *= 1.2
    if re.search(r"\bchi\s*siamo|chi\s*e\b", q_norm) and re.search(r"\bchi_siamo\b|\bprofilo\b|\bvision\b", hay):
        boost *= 1.2
    if re.search(r"\bcodici\b|\belenco\b", q_norm) and re.search(r"\bprodotti_elenco\b|\belenco\b", hay):
        boost *= 1.15
    return boost

def _score_block(blk: Dict[str, Any], q_norm: str) -> float:
    """
    Combina:
      - match fuzz su Q
      - match fuzz su TAG/nome file
    """
    score = 0.0
    q = normalize_text(blk.get("q") or "")
    a = normalize_text(blk.get("a") or blk.get("text") or "")
    tags = " ".join(blk.get("tags") or [])
    fname = os.path.splitext(blk.get("file_base",""))[0]

    # fuzzy parziale
    if q:
        score_q = fuzz.partial_ratio(q_norm, q) / 100.0
        score = max(score, score_q * 1.0)
    if tags:
        score_t = fuzz.partial_ratio(q_norm, tags) / 100.0
        score = max(score, score_t * 0.9)
    if fname:
        score_f = fuzz.partial_ratio(q_norm, fname) / 100.0
        score = max(score, score_f * 0.85)

    # piccolo contributo dal corpo risposta
    if a:
        score_a = fuzz.token_set_ratio(q_norm, a) / 100.0
        score = max(score, score_a * 0.75)

    # meta boost
    score *= _boost_from_meta(blk, q_norm)
    return score

def _hybrid_rank(query: str, topk: int = 20) -> List[Tuple[float, int]]:
    """
    BM25 sui token + fuzzy scoring personalizzato.
    """
    if not is_ready():
        return []

    q_norm = normalize_text(query)
    q_tokens = tokenize(query)

    # BM25
    bm = _BM25.get_scores(q_tokens) if _BM25 else [0.0] * len(INDEX)
    # fuzzy
    out: List[Tuple[float, int]] = []
    for i, blk in enumerate(INDEX):
        fscore = _score_block(blk, q_norm)
        # combinazione: 70% fuzzy/meta + 30% BM25 normalizzato
        bm_part = 0.0
        if bm:
            # normalizza BM25 in 0..1
            # safe: prendi 95° percentile come max
            sorted_bm = sorted(bm)
            max_ref = sorted_bm[min(len(sorted_bm)-1, int(len(sorted_bm)*0.95))]
            if max_ref > 0:
                bm_part = min(bm[i] / max_ref, 1.0)
        combined = 0.7 * fscore + 0.3 * bm_part
        out.append((combined, i))

    out.sort(key=lambda x: x[0], reverse=True)
    return out[:max(1, topk)]

# =========================
# Post-process risposta
# =========================
def _format_answer(blk: Dict[str, Any], score: float) -> str:
    """Titolo (opzionale) + testo troncato a fine frase."""
    text = (blk.get("a") or blk.get("text") or "").strip()
    if not text:
        return ""

    # Troncamento elegante
    if MAX_ANSWER_CHARS and len(text) > MAX_ANSWER_CHARS:
        cut = text[:MAX_ANSWER_CHARS]
        # cerca l’ultima frase completa dentro cut
        last = None
        for m in re.finditer(r"[\.!\?](?:\s|$)", cut):
            last = m.end()
        if last and last > 40:
            text = cut[:last].rstrip() + " …"
        else:
            text = cut.rstrip() + " …"

    if ADD_TITLE:
        base = blk.get("file_base") or blk.get("filename") or "Informazioni"
        base = os.path.splitext(base)[0]
        title = re.sub(r"[_\-]+", " ", base).strip().title()
        return f"**{title}**\n\n{text}"
    return text

# =========================
# Entry point di ricerca
# =========================
def search_best_answer(query: str, threshold: Optional[float] = None, topk: Optional[int] = None) -> Dict[str, Any]:
    """
    Ritorna sempre un JSON “safe”. Mai 500.
    """
    try:
        if not query or not query.strip():
            return {"found": False, "answer": "Nessuna domanda ricevuta.", "from": None}

        if not is_ready():
            # prova a costruire
            build_index(DOC_DIR)
            if not is_ready():
                return {"found": False, "answer": "Indice non pronto. Riprova tra qualche secondo.", "from": None}

        thr = SIMILARITY_THRESHOLD if threshold is None else float(max(0.0, threshold))
        k = TOPK_SEMANTIC if topk is None else int(max(1, topk))

        ranked = _hybrid_rank(query, topk=k)
        if not ranked:
            return {"found": False, "answer": "Nessun risultato.", "from": None}

        best_score, idx = ranked[0]
        blk = INDEX[idx]
        answer_text = _format_answer(blk, best_score)

        # soglia “umana”: se troppo basso, prova una seconda chance abbassando thr
        if best_score < thr and thr > 0.25:
            ranked2 = _hybrid_rank(query, topk=k*2)
            if ranked2:
                best_score2, idx2 = ranked2[0]
                if best_score2 > best_score:
                    best_score, idx = best_score2, idx2
                    blk = INDEX[idx]
                    answer_text = _format_answer(blk, best_score)

        found = best_score >= thr
        return {
            "found": bool(found),
            "score": round(float(best_score), 3),
            "from": blk.get("file_base"),
            "tags": blk.get("tags") or [],
            "answer": answer_text,
            "debug": {
                "threshold": thr,
                "topk": k,
                "picked_idx": idx,
            }
        }
    except Exception as e:
        # impedisci 500: ritorna un JSON con motivo
        return {
            "found": False,
            "answer": "Errore interno durante la ricerca.",
            "from": None,
            "debug": {"error": f"{type(e).__name__}: {e}"}
        }
