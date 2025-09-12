# -*- coding: utf-8 -*-
"""
scraper_tecnaria.py
Indicizza i .txt in documenti_gTab/ e cerca la miglior risposta
in base a TAGS, Domanda/Risposta e blocchi separati.

Env utili:
  DOC_DIR / DOCS_FOLDER / KNOWLEDGE_DIR  -> cartella dei .txt (default: ./documenti_gTab)
  DEBUG=1 oppure DEBUG_SCRAPER=1         -> log estesi
  REINDEX_ON_STARTUP=1                   -> ricostruisce l'indice all'avvio
  SIMILARITY_THRESHOLD=0.25..0.60        -> soglia di accettazione (default 0.30)
"""

import os
import re
import glob
import json
from typing import List, Dict, Any, Optional, Tuple
from difflib import SequenceMatcher

# -----------------------------------------------------------------------------
# Config & Debug
# -----------------------------------------------------------------------------
DEBUG = os.environ.get("DEBUG", "0") == "1" or os.environ.get("DEBUG_SCRAPER", "0") == "1"
DOC_DIR = (
    os.environ.get("DOC_DIR")
    or os.environ.get("DOCS_FOLDER")
    or os.environ.get("KNOWLEDGE_DIR")
    or "./documenti_gTab"
)
REINDEX_ON_STARTUP = os.environ.get("REINDEX_ON_STARTUP", "1") == "1"

# Soglia: 0.30 è un buon compromesso. Puoi alzare a 0.40 se vuoi essere più “selettivo”.
try:
    SIMILARITY_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.30"))
except Exception:
    SIMILARITY_THRESHOLD = 0.30

TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))

# -----------------------------------------------------------------------------
# Stopwords minimale + normalizzazione
# -----------------------------------------------------------------------------
STOPWORDS = {
    "il", "lo", "la", "i", "gli", "le",
    "un", "una", "uno",
    "di", "a", "da", "in", "con", "su", "per", "tra", "fra",
    "che", "e", "ed", "o"
}

def normalize_text(text: str) -> str:
    """Minuscole, normalizza accenti/quote, rimuove punteggiatura, toglie stopwords."""
    text = text.lower()

    # Normalizza accenti e apostrofi comuni (evita mismatch tra 'è' ed 'e’')
    replacements = {
        "à": "a", "è": "e", "é": "e", "ì": "i", "ò": "o", "ó": "o", "ù": "u",
        "’": "'", "‘": "'", "“": '"', "”": '"'
    }
    for k, v in replacements.items():
        text = text.replace(k, v)

    # Tieni solo lettere/numeri/spazi
    text = re.sub(r"[^a-z0-9\s\-_/\.]", " ", text)

    # Token + rimozione stopwords
    words = [w for w in text.split() if w not in STOPWORDS]
    return " ".join(words)

# -----------------------------------------------------------------------------
# Riconoscimento famiglie/prodotti per boost file
# -----------------------------------------------------------------------------
FAMILY_HINTS: Dict[str, List[str]] = {
    "p560":        ["p560", "p 560", "p-560", "pistola", "sparachiodi", "spit"],
    "ctf":         ["ctf", "piolo", "pioli", "piastra", "acciaio calcestruzzo", "lamiera grecata"],
    "cem-e":       ["cem-e", "ceme", "cem e", "riprese di getto", "piastra dentata"],
    "mini_cem-e":  ["mini cem-e", "minicem", "mini-cem-e", "spessori sottili", "soletta 20", "soletta 30", "soletta 40"],
    "hbv":         ["hbv", "connettori hbv", "hbv bulloni", "bulloni legno calcestruzzo"],
    "x-hbv":       ["x-hbv", "x hbv", "connessione legno calcestruzzo diagonale"],
    "ctl":         ["ctl", "ct-l", "ct l", "connettori legno calcestruzzo", "tirafondo", "omega"],
    "fva":         ["fva", "fva-l", "fva l", "lamiera grecata fva"],
}

FAMILY_FILE_MAP: Dict[str, List[str]] = {
    "p560":       ["p560.txt", "hbv_chiodatrice.txt"],
    "ctf":        ["ctf.txt", "diapason.txt", "p560.txt"],
    "cem-e":      ["cem-e.txt"],
    "mini_cem-e": ["mini_cem-e.txt", "cem-e.txt"],
    "hbv":        ["hbv.txt", "x-hbv.txt", "manuali_di_posa.txt"],
    "x-hbv":      ["x-hbv.txt", "hbv.txt"],
    "ctl":        ["ctl.txt", "manuali_di_posa.txt"],
    "fva":        ["fva.txt", "fva-l.txt", "schede_tecniche.txt"],
}

# -----------------------------------------------------------------------------
# Parsing files in blocchi, TAGS, Q/R
# -----------------------------------------------------------------------------
DELIM_RE = re.compile(r"^[\s\-–—_=]{6,}$")  # linee con molti trattini/linee
TAGS_RE  = re.compile(r"^\s*\[TAGS\s*:\s*(.*?)\]\s*$", re.IGNORECASE)

def _split_blocks(txt: str) -> List[str]:
    """Divide un file in blocchi sugli separatori ('────', linee, ecc.)."""
    lines = txt.splitlines()
    blocks = []
    cur = []
    for ln in lines:
        if DELIM_RE.match(ln.strip()):
            if cur:
                blocks.append("\n".join(cur).strip())
                cur = []
        else:
            cur.append(ln)
    if cur:
        blocks.append("\n".join(cur).strip())
    # Se non ci sono separatori, ritorna l'intero testo come 1 blocco
    if not blocks and txt.strip():
        blocks = [txt.strip()]
    return blocks

def _parse_block(block: str) -> Dict[str, Any]:
    """Estrae TAGS, domanda/risposta (se presenti), e testo normalizzato per scoring."""
    tags: List[str] = []
    lines = [l for l in block.splitlines() if l.strip()]
    # estrai TAGS dalla prima riga utile
    if lines:
        m = TAGS_RE.match(lines[0])
        if m:
            raw = m.group(1)
            tags = [t.strip() for t in raw.split(",") if t.strip()]

    # estrai D: / R:
    q_text = None
    a_text = None
    # cerca pattern "D: ..." e "R: ..."
    d_re = re.compile(r"^\s*d\s*:\s*(.+)$", re.IGNORECASE)
    r_re = re.compile(r"^\s*r\s*:\s*(.+)$", re.IGNORECASE)

    for i, ln in enumerate(lines):
        md = d_re.match(ln)
        if md:
            q_text = md.group(1).strip()
            # la risposta può essere nelle righe successive fino a un'eventuale nuova domanda
            # cerchiamo "R:" dopo
            for j in range(i + 1, len(lines)):
                mr = r_re.match(lines[j])
                if mr:
                    # prendi tutto da R: fino al prossimo D: o fine
                    a_lines = [mr.group(1).strip()]
                    for k in range(j + 1, len(lines)):
                        if d_re.match(lines[k]):
                            break
                        a_lines.append(lines[k])
                    a_text = "\n".join(a_lines).strip()
                    break
            break

    # Se non c'è D:/R:, usa il blocco intero come "contenuto"
    text_for_index = a_text or q_text or block
    norm_block = normalize_text(text_for_index)

    return {
        "tags": tags,
        "q": q_text,
        "a": a_text,
        "raw": block,
        "norm": norm_block
    }

# -----------------------------------------------------------------------------
# Indicizzazione
# -----------------------------------------------------------------------------
INDEX: List[Dict[str, Any]] = []
INDEX_META: Dict[str, Any] = {"files": 0, "blocks": 0, "lines": 0}

def build_index(doc_dir: Optional[str] = None) -> Dict[str, Any]:
    """Legge tutti i .txt e popola INDEX e INDEX_META."""
    global INDEX, INDEX_META
    base = doc_dir or DOC_DIR
    path = os.path.abspath(base)

    if DEBUG:
        print(f"[scraper_tecnaria] Inizio indicizzazione da: {path}\n")

    files = sorted(glob.glob(os.path.join(path, "*.txt")))
    if DEBUG:
        if files:
            print(f"[scraper_tecnaria] Trovati {len(files)} file .txt:")
            for f in files:
                print(f"  - {f}")
        else:
            print("[scraper_tecnaria] Trovati 0 file .txt:")

    INDEX = []
    tot_lines = 0

    for fpath in files:
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                txt = fh.read()
        except Exception as e:
            if DEBUG:
                print(f"[scraper_tecnaria][WARN] Impossibile leggere {fpath}: {e}")
            continue

        blocks = _split_blocks(txt)
        for b in blocks:
            rec = _parse_block(b)
            rec["file"] = fpath
            rec["file_name"] = os.path.basename(fpath)
            INDEX.append(rec)
            tot_lines += len(b.splitlines())

    INDEX_META = {"files": len(files), "blocks": len(INDEX), "lines": tot_lines}

    if DEBUG:
        print(f"[scraper_tecnaria] Indicizzati {INDEX_META['blocks']} blocchi / {INDEX_META['lines']} righe da {INDEX_META['files']} file.")

    return {"status": "ok", **INDEX_META}

# -----------------------------------------------------------------------------
# Scoring
# -----------------------------------------------------------------------------
def _token_overlap_score(q_norm: str, t_norm: str) -> float:
    qset = set(q_norm.split())
    tset = set(t_norm.split())
    if not qset or not tset:
        return 0.0
    inter = len(qset & tset)
    return inter / max(1, len(qset))

def _fuzzy(s1: str, s2: str) -> float:
    return SequenceMatcher(None, s1, s2).ratio()

def _any_hint_in_text(q_norm: str, hints: List[str]) -> bool:
    q = " " + q_norm + " "
    for h in hints:
        h_norm = normalize_text(h)
        if " " + h_norm + " " in q:
            return True
    return False

def _family_boost(question: str, file_name: str) -> float:
    """Se la domanda contiene indizi di famiglia/prodotto, boosta i file giusti."""
    qn = normalize_text(question)
    fname = file_name.lower()
    for fam, hints in FAMILY_HINTS.items():
        if _any_hint_in_text(qn, hints):
            expected_files = FAMILY_FILE_MAP.get(fam, [])
            for ef in expected_files:
                if ef.lower() == fname:
                    return 0.15  # boost moderato
    return 0.0

def _tags_boost(q_norm: str, tags: List[str]) -> float:
    if not tags:
        return 0.0
    tnorm = normalize_text(" ".join(tags))
    # overlap su TAGS
    ov = _token_overlap_score(q_norm, tnorm)
    return min(0.20, ov * 0.40)  # fino a +0.20 max

def _question_hint_boost(q_user: str, rec_q: Optional[str]) -> float:
    """Se il blocco ha una 'D:' simile alla domanda, aggiungi un piccolo boost."""
    if not rec_q:
        return 0.0
    qn = normalize_text(q_user)
    rn = normalize_text(rec_q)
    fz = _fuzzy(qn, rn)
    if fz >= 0.70:
        return 0.10
    if fz >= 0.55:
        return 0.05
    return 0.0

def _score_record(question: str, rec: Dict[str, Any]) -> float:
    qn = normalize_text(question)
    score = 0.0

    # 1) Overlap keyword tra domanda e contenuto del blocco
    score += 0.60 * _token_overlap_score(qn, rec["norm"])

    # 2) Fuzzy tra domanda e blocco
    score += 0.25 * _fuzzy(qn, rec["norm"])

    # 3) Boost TAGS (+0..0.20)
    score += _tags_boost(qn, rec.get("tags", []))

    # 4) Boost se la domanda assomiglia alla D: del blocco (+0..0.10)
    score += _question_hint_boost(question, rec.get("q"))

    # 5) Boost per file di famiglia/prodotto atteso (+0..0.15)
    score += _family_boost(question, rec.get("file_name", ""))

    return score

# -----------------------------------------------------------------------------
# Ricerca principale
# -----------------------------------------------------------------------------
def search_best_answer(question: str, topk: int = 1, threshold: Optional[float] = None) -> Dict[str, Any]:
    """Restituisce il miglior match (o i topk) sopra la soglia."""
    if not INDEX:
        if DEBUG:
            print("[scraper_tecnaria][SEARCH] Indice vuoto. Esegui build_index().")
        return {
            "found": False,
            "answer": "",
            "score": 0.0,
            "path": None,
            "line": None,
            "question": "",
            "tags": "",
            "debug_top": []
        }

    th = SIMILARITY_THRESHOLD if threshold is None else threshold
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for rec in INDEX:
        s = _score_record(question, rec)
        scored.append((s, rec))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[: max(1, topk)]

    debug_top = []
    for sc, rc in top[:5]:
        debug_top.append({
            "score": round(sc, 3),
            "file": rc.get("file_name"),
            "tags": rc.get("tags"),
            "has_Q": bool(rc.get("q")),
            "has_A": bool(rc.get("a"))
        })

    best_score, best = top[0]
    if DEBUG:
        print(f"[scraper_tecnaria][SEARCH] q='{question}' -> best={round(best_score,3)} {best.get('file_name')}")

    if best_score < th:
        return {
            "found": False,
            "answer": "",
            "score": float(best_score),
            "path": best.get("file"),
            "line": None,
            "question": best.get("q"),
            "tags": ", ".join(best.get("tags", [])),
            "debug_top": debug_top
        }

    # preferisci la risposta 'R:' se presente; altrimenti l'intero blocco
    answer = best.get("a") or best.get("raw") or ""
    return {
        "found": True,
        "answer": answer.strip(),
        "score": float(best_score),
        "path": best.get("file"),
        "line": None,
        "question": best.get("q"),
        "tags": ", ".join(best.get("tags", [])),
        "debug_top": debug_top
    }

# -----------------------------------------------------------------------------
# Helper per status/health
# -----------------------------------------------------------------------------
def get_index_status() -> Dict[str, Any]:
    return {
        "status": "ok",
        "files": INDEX_META.get("files", 0),
        "blocks": INDEX_META.get("blocks", 0),
        "lines": INDEX_META.get("lines", 0),
    }

# -----------------------------------------------------------------------------
# Auto-build all'avvio (se richiesto)
# -----------------------------------------------------------------------------
if REINDEX_ON_STARTUP:
    try:
        build_index(DOC_DIR)
    except Exception as e:
        if DEBUG:
            print(f"[scraper_tecnaria][ERROR] build_index fallita all'avvio: {e}")
