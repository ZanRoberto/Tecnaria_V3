# -*- coding: utf-8 -*-
import os
import re
import json
import unicodedata
from collections import defaultdict, Counter
from typing import List, Dict, Any, Optional

# ===== Log base =====
DEBUG = os.environ.get("DEBUG_SCRAPER", "0") == "1"

def log(msg: str):
    if DEBUG:
        print(f"[scraper_tecnaria] {msg}")

# ===== Normalizzazione & stopwords minime =====
STOPWORDS = {
    "tecnaria",  # brand, meglio ignorarlo per non inquinare i match
    "spa", "s.p.a.",
    "il","lo","la","i","gli","le","un","uno","una",
    "di","del","della","dell","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da",
    "al","allo","ai","agli","alla","alle",
    "che","dove","quando","anche","come","quale","quali",
    "è","e'", "essere"
}

def normalize_text(s: str) -> str:
    # minuscolo
    s = s.lower()
    # normalizza accenti
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    # rimuovi punteggiatura di troppo
    s = re.sub(r"[^\w\s\-]", " ", s)
    # compattamento spazi
    s = re.sub(r"\s+", " ", s).strip()
    return s

def tokenize(s: str) -> List[str]:
    s = normalize_text(s)
    tokens = [t for t in re.split(r"[^\w]+", s) if t]
    return [t for t in tokens if t not in STOPWORDS]

# ===== Sinonimi per query robusta =====
SYNONYMS = {
    "p560": {"p 560", "p-560", "pistola", "sparachiodi", "spit"},
    "ctf": {"ctf", "pioli", "piolo", "acciaio calcestruzzo"},
    "cem": {"cem-e", "cem", "riprese di getto"},
    "mini": {"mini cem-e", "minicem", "mini-cem"},
    "hbv": {"hbv", "chiodatrice hbv"},
    "xhbv": {"x-hbv", "xhbv"},
    "diapason": {"diapason"},
    "ctl": {"ctl", "legno calcestruzzo"},
    "fva": {"fva"},
    "fva-l": {"fva-l"},
}

def expand_query_terms(q: str) -> List[str]:
    toks = tokenize(q)
    extra = set()
    for t in toks:
        for key, syns in SYNONYMS.items():
            if t == key or t in syns:
                extra |= syns | {key}
    return toks + list(extra)

# ===== Struttura indice in memoria =====
# Ogni item: {
#   "path": fullpath,
#   "file": filename,
#   "tags": "stringa tag",
#   "q": "Domanda" (opzionale),
#   "a": "Risposta",
#   "block_text": "testo intero del blocco",
#   "norm": "block_text normalizzato",
#   "line": line_number
# }
INDEX: List[Dict[str, Any]] = []

# ===== Lettura & split dei blocchi =====
SEP = "────────────────────────────"

def _list_txt_files(doc_dir: str) -> List[str]:
    root = os.path.abspath(doc_dir or "documenti_gTab")
    if not os.path.isdir(root):
        log(f"ATTENZIONE: cartella non trovata: {root}")
        return []
    files = []
    for n in os.listdir(root):
        p = os.path.join(root, n)
        if os.path.isfile(p) and n.lower().endswith(".txt"):
            files.append(p)
    log(f"Trovati {len(files)} file .txt in {root}")
    for f in files:
        log(f"  - {f}")
    return files

def _split_blocks(content: str) -> List[str]:
    # Separa su linee SEP; se non presente, usa l'intero file come 1 blocco
    parts = [p.strip() for p in content.split(SEP)]
    parts = [p for p in parts if p]
    if not parts:
        return [content.strip()] if content.strip() else []
    return parts

TAG_RE = re.compile(r'^\s*\[TAGS:(.*?)\]\s*$', re.IGNORECASE)

def _parse_block(block: str) -> Dict[str, Any]:
    """
    Estrae TAG (se c'è la prima riga [TAGS: ...]),
    poi cerca D:/R: (FAQ) oppure usa tutto come 'a'.
    """
    lines = [l.strip() for l in block.splitlines() if l.strip()]
    tags = ""
    start_idx = 0
    if lines and TAG_RE.match(lines[0]):
        tags = TAG_RE.match(lines[0]).group(1).strip()
        start_idx = 1

    q_text = ""
    a_text = ""
    rest = lines[start_idx:]
    # prova formato FAQ
    for i, ln in enumerate(rest):
        if ln.lower().startswith("d:"):
            q_text = ln[2:].strip()
            # cerca "r:" nelle righe successive
            for j in range(i+1, len(rest)):
                if rest[j].lower().startswith("r:"):
                    a_text = rest[j][2:].strip()
                    # includi anche eventuali righe dopo come parte della risposta
                    tail = rest[j+1:]
                    if tail:
                        a_text += "\n" + "\n".join(tail)
                    break
            break

    if not a_text:
        # fallback: tutto il blocco come risposta
        a_text = "\n".join(rest)

    block_text = (("\n".join(lines[start_idx:])) or "").strip()
    norm = normalize_text(block_text)

    return {
        "tags": tags,
        "q": q_text,
        "a": a_text,
        "block_text": block_text,
        "norm": norm,
    }

def build_index(doc_dir: str) -> None:
    """
    Ricostruisce INDEX leggendo tutti i .txt in doc_dir.
    """
    global INDEX
    INDEX = []

    files = _list_txt_files(doc_dir)
    count_blocks = 0
    count_lines = 0

    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception as e:
            log(f"ERRORE lettura {fp}: {e}")
            continue

        blocks = _split_blocks(content)
        for idx, b in enumerate(blocks, start=1):
            parsed = _parse_block(b)
            parsed["path"] = fp
            parsed["file"] = os.path.basename(fp)
            parsed["line"] = idx
            INDEX.append(parsed)
            count_blocks += 1
            count_lines += len(b.splitlines())

    log(f"Indicizzati {count_blocks} blocchi / {count_lines} righe da {len(files)} file.")

# ===== Scoring ibrido =====
def _keyword_score(query_terms: List[str], item: Dict[str, Any]) -> float:
    """
    Punteggio semplice basato sulla sovrapposizione termini nel norm del blocco.
    """
    if not query_terms:
        return 0.0
    text_terms = set(tokenize(item.get("block_text", "")))
    overlap = len(text_terms.intersection(query_terms))
    if overlap == 0:
        return 0.0
    # piccolo boost se il blocco contiene una "D:" coerente
    if item.get("q"):
        q_terms = set(tokenize(item["q"]))
        overlap += 0.5 * len(q_terms.intersection(query_terms))
    return float(overlap)

def _tag_filename_boost(query: str, item: Dict[str, Any]) -> float:
    """
    Boost se il query matcha TAG o nome file (es. 'p560', 'hbv', etc.).
    """
    qn = normalize_text(query)
    bonus = 0.0

    tags = normalize_text(item.get("tags", ""))
    fname = normalize_text(item.get("file", ""))

    # match diretti
    if "p560" in qn:
        if "p560" in tags or "p560" in fname:
            bonus += 2.0
    if "hbv" in qn:
        if "hbv" in tags or "hbv" in fname:
            bonus += 1.5
    if "ctf" in qn:
        if "ctf" in tags or "ctf" in fname:
            bonus += 1.5
    if "cem" in qn or "cem e" in qn:
        if "cem" in tags or "cem e" in tags or "cem" in fname:
            bonus += 1.5
    if "diapason" in qn:
        if "diapason" in tags or "diapason" in fname:
            bonus += 1.5
    if "ctl" in qn:
        if "ctl" in tags or "ctl" in fname:
            bonus += 1.2

    # match generici
    if any(k in qn for k in ["contatti", "orari", "telefono", "email"]):
        if "contatti" in tags or "orari" in tags or "contatti" in fname:
            bonus += 1.2

    return bonus

# opzionale: embedding se disponibile
_EMBEDDER = None
def _ensure_embedder():
    global _EMBEDDER
    if _EMBEDDER is not None:
        return _EMBEDDER
    try:
        from sentence_transformers import SentenceTransformer
        model_name = os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2")
        _EMBEDDER = SentenceTransformer(model_name)
        log(f"Embeddings attivi: {model_name}")
    except Exception as e:
        _EMBEDDER = None
        log(f"Embeddings NON disponibili (ok in fallback): {e}")
    return _EMBEDDER

def _cosine(a, b):
    import math
    na = math.sqrt(sum(x*x for x in a))
    nb = math.sqrt(sum(x*x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x*y for x,y in zip(a,b)) / (na*nb)

def _embedding_scores(query: str, items: List[Dict[str, Any]]) -> Optional[List[float]]:
    emb = _ensure_embedder()
    if emb is None:
        return None
    try:
        texts = [it.get("block_text","") for it in items]
        qv = emb.encode([query], show_progress_bar=False)[0]
        dvs = emb.encode(texts, show_progress_bar=False)
        return [_cosine(qv, dv) for dv in dvs]
    except Exception as e:
        log(f"Errore embeddings: {e}")
        return None

def search_best_answer(query: str, threshold: float = 0.30, topk: int = 20) -> Dict[str, Any]:
    """
    Retrieval ibrido:
    - keyword overlap su testo e domanda
    - boost su TAG e nome file
    - opzionale: embeddings se disponibili
    Ritorna il miglior blocco sopra soglia, con debug.
    """
    if not INDEX:
        return {"found": False, "answer": "", "from": None}

    terms = expand_query_terms(query)
    kw_scores = []
    for it in INDEX:
        s = _keyword_score(terms, it) + _tag_filename_boost(query, it)
        kw_scores.append(s)

    # normalizza keyword score in [0,1]
    if kw_scores:
        maxkw = max(kw_scores) or 1.0
        kw_scores = [s / maxkw for s in kw_scores]

    # embeddings (opzionale)
    emb_scores = _embedding_scores(query, INDEX)
    if emb_scores is None:
        final_scores = kw_scores
    else:
        # combina: 0.6 keyword + 0.4 embedding (tunable)
        # prima normalizza embeddings in [0,1]
        if emb_scores:
            mn = min(emb_scores); mx = max(emb_scores)
            if mx - mn > 1e-9:
                emb_norm = [(e - mn) / (mx - mn) for e in emb_scores]
            else:
                emb_norm = [0.0 for _ in emb_scores]
        else:
            emb_norm = [0.0 for _ in kw_scores]
        final_scores = [0.6*k + 0.4*e for k,e in zip(kw_scores, emb_norm)]

    # seleziona topk
    ranked = sorted(
        [{"idx": i, "score": sc} for i, sc in enumerate(final_scores)],
        key=lambda x: x["score"],
        reverse=True
    )[:max(1, topk)]

    best = ranked[0] if ranked else {"idx": 0, "score": 0.0}
    best_item = INDEX[best["idx"]]

    found = best["score"] >= threshold
    answer = best_item.get("a") or best_item.get("block_text") or ""
    origin = f"{best_item.get('path')}:{best_item.get('line')}"

    # debug compatto
    debug = {
        "query": query,
        "score": round(best["score"], 3),
        "path": best_item.get("path"),
        "file": best_item.get("file"),
        "line": best_item.get("line"),
        "tags": best_item.get("tags"),
        "q_in_block": bool(best_item.get("q")),
        "threshold": threshold
    }

    return {
        "found": bool(found),
        "answer": answer.strip(),
        "from": origin,
        "debug": debug
    }
