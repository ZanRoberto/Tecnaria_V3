# -*- coding: utf-8 -*-
import os
import re
import unicodedata
from typing import List, Dict, Any, Optional

# Embedding opzionali (se installed)
EMB_AVAILABLE = False
try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    EMB_AVAILABLE = True
except Exception:
    EMB_AVAILABLE = False

DEBUG = os.environ.get("DEBUG_SCRAPER", "1") == "1"

def log(msg: str):
    if DEBUG:
        print(f"[scraper_tecnaria] {msg}")

# ===== Normalizzazione e util =====

STOPWORDS_MIN = {
    "il","lo","la","i","gli","le","un","uno","una",
    "di","del","della","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da",
    "al","allo","ai","agli","alla","alle",
    "che","come","dove","quando","anche",
    "mi","ti","si","ci","vi","a","da","de","dal","dall","dalla","dalle",
}

def strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', s)
                   if unicodedata.category(c) != 'Mn')

def normalize_text(s: str) -> str:
    s = s.lower()
    s = strip_accents(s)
    s = re.sub(r"[^a-z0-9\s\-_/\.]", " ", s)   # tieni alfanum + separatori utili
    s = re.sub(r"\s+", " ", s).strip()
    # rimuovi stopwords semplici
    tokens = [t for t in s.split() if t not in STOPWORDS_MIN]
    return " ".join(tokens)

# Sinonimi/alias (espansi nella query)
SYNONYMS = {
    "p560": ["p 560", "p-560", "spit p560", "pistola", "sparachiodi", "pistola spit"],
    "contatti": ["telefono", "email", "indirizzo", "sede", "orari", "come contattarvi", "assistenza"],
    "connettori": ["ctf", "cem-e", "mini cem-e", "diapason", "ctl", "hbv", "x-hbv", "fva", "fva-l", "ct-l", "cls", "clsr"],
    "tecnaria": ["azienda", "chi siete", "chi e tecnaria", "profilo aziendale", "chi è tecnaria", "chi siete tecnaria"],
    "hbv": ["chiodatrice hbv", "pistola hbv"],
}

def expand_with_synonyms(q_norm: str) -> List[str]:
    tokens = q_norm.split()
    expanded = set([q_norm])
    for key, syns in SYNONYMS.items():
        if key in tokens:
            for s in syns:
                expanded.add(q_norm + " " + normalize_text(s))
    return list(expanded)

# ===== Indice in memoria =====

INDEX: List[Dict[str, Any]] = []
EMB_MODEL = None
EMB_MATRIX = None  # numpy array di shape (N, D) se disponibili

DELIM = "────────────────────────────"

TAG_RE = re.compile(r"^\s*\[TAGS?\s*:\s*(.*?)\]\s*$", re.IGNORECASE)

def is_txt_file(path: str) -> bool:
    name = os.path.basename(path)
    if name.startswith("."):
        return False
    return name.lower().endswith(".txt")

def read_all_txt(doc_dir: str) -> List[str]:
    found = []
    for root, dirs, files in os.walk(doc_dir):
        for f in files:
            full = os.path.join(root, f)
            if is_txt_file(full):
                found.append(full)
    return sorted(found)

def split_blocks(content: str) -> List[str]:
    # Split su riga delimitatore o su doppio newline “forte”
    parts = re.split(rf"\n\s*{re.escape(DELIM)}\s*\n", content, flags=re.MULTILINE)
    # ripulisci e filtra vuoti
    blocks = []
    for p in parts:
        p = p.strip()
        if p:
            blocks.append(p)
    return blocks

def extract_tags_firstline(block: str) -> (List[str], str):
    """
    Se la prima riga è [TAGS: ...], estrai i tag e ritorna (tags, testo_senza_tags)
    """
    lines = block.splitlines()
    if not lines:
        return [], block
    m = TAG_RE.match(lines[0].strip())
    if m:
        raw = m.group(1).strip()
        tags = [t.strip().lower() for t in re.split(r"[;,]", raw) if t.strip()]
        return tags, "\n".join(lines[1:]).strip()
    return [], block

def build_index(doc_dir: str):
    global INDEX, EMB_MODEL, EMB_MATRIX

    doc_dir = os.path.abspath(doc_dir)
    log(f"Inizio indicizzazione da: {doc_dir}\n")

    paths = read_all_txt(doc_dir)
    if not paths:
        log("Trovati 0 file .txt:")
        INDEX = []
        EMB_MATRIX = None
        return

    log(f"Trovati {len(paths)} file .txt:")
    for p in paths:
        log(f"  - {p}")

    rows: List[Dict[str, Any]] = []
    total_lines = 0
    file_count = 0

    for p in paths:
        file_count += 1
        try:
            with open(p, "r", encoding="utf-8") as fh:
                content = fh.read()
        except Exception:
            with open(p, "r", encoding="latin-1", errors="ignore") as fh:
                content = fh.read()

        blocks = split_blocks(content)
        for b in blocks:
            tags, body = extract_tags_firstline(b)
            if not body.strip():
                continue
            norm = normalize_text(body)
            file_name = os.path.basename(p).lower()
            rows.append({
                "path": p,
                "file": file_name,
                "text": body,
                "norm": norm,
                "tags": tags,
            })
            total_lines += len(body.splitlines())

    INDEX = rows
    log(f"Indicizzati {len(INDEX)} blocchi / {total_lines} righe da {file_count} file.")

    # Embedding opzionale
    EMB_MATRIX = None
    if EMB_AVAILABLE and len(INDEX) > 0:
        try:
            model_name = os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2")
            EMB_MODEL = SentenceTransformer(model_name)
            texts = [r["norm"] for r in INDEX]
            EMB_MATRIX = EMB_MODEL.encode(texts, normalize_embeddings=True)
            log(f"Embedding attivati: modello={model_name}, shape={EMB_MATRIX.shape}")
        except Exception as e:
            EMB_MODEL = None
            EMB_MATRIX = None
            log(f"Embedding non disponibili ({e}). Uso ricerca solo-keyword.")

def _keyword_score(q_tokens: List[str], r_tokens: List[str]) -> float:
    if not q_tokens or not r_tokens:
        return 0.0
    qs = set(q_tokens)
    rs = set(r_tokens)
    inter = len(qs & rs)
    if inter == 0:
        return 0.0
    # Jaccard semplice
    return inter / float(len(qs | rs))

def _boost_from_meta(q_norm: str, rec: Dict[str, Any]) -> float:
    score = 0.0
    tokens = set(q_norm.split())
    file_tokens = set(rec["file"].replace(".txt", "").split("_"))
    tag_tokens = set(rec.get("tags") or [])
    # boost se nome file o tag combaciano con query
    if tokens & file_tokens:
        score += 0.10
    if tokens & tag_tokens:
        score += 0.15
    # casi speciali utili
    if "p560" in tokens and ("p560" in rec["file"] or "p560" in tag_tokens or "pistola" in rec["norm"]):
        score += 0.20
    if "contatti" in tokens and ("contatti" in rec["file"] or "contatti" in tag_tokens):
        score += 0.20
    return score

def _embed_query(qs: List[str]):
    """Ritorna il vettore embedding medio su varianti/sinonimi."""
    if not EMB_AVAILABLE or EMB_MODEL is None:
        return None
    vecs = EMB_MODEL.encode(qs, normalize_embeddings=True)
    return np.mean(vecs, axis=0)

def _cos_sim(a, B):
    return (B @ a)

def search_best_answer(question: str, threshold: float = 0.35, topk: int = 20) -> Dict[str, Any]:
    """
    Retrieval ibrido:
      score = 0.7*embedding (se disponibile) + 0.3*keyword + boost(meta)
    Ritorna il blocco migliore o not-found con debug.
    """
    if not INDEX:
        return {"found": False, "from": None, "answer": "Indice vuoto.", "debug": {"reason": "no-index"}}

    q_norm = normalize_text(question)
    q_variants = expand_with_synonyms(q_norm)

    # Keyword scores
    q_tokens = q_norm.split()

    # Embedding query (se presenti)
    emb_scores = None
    if EMB_AVAILABLE and EMB_MATRIX is not None:
        q_vec = _embed_query(q_variants)
        if q_vec is not None:
            emb_scores = _cos_sim(q_vec, EMB_MATRIX)  # numpy array

    # Calcola punteggi
    scored: List[Dict[str, Any]] = []
    for i, rec in enumerate(INDEX):
        kw = _keyword_score(q_tokens, rec["norm"].split())
        em = float(emb_scores[i]) if emb_scores is not None else 0.0
        meta = _boost_from_meta(q_norm, rec)
        score = 0.7 * em + 0.3 * kw + meta
        scored.append({
            "score": score,
            "rec": rec
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:max(1, topk)]
    best = top[0]

    # Costruisci risposta
    if best["score"] >= threshold:
        rec = best["rec"]
        return {
            "found": True,
            "answer": rec["text"],
            "from": {
                "file": rec["file"],
                "path": rec["path"],
                "score": round(float(best["score"]), 3),
                "tags": rec.get("tags"),
            },
            "debug": {
                "query_norm": q_norm,
                "threshold": threshold
            }
        }
    else:
        # non trovato: fornisci dettaglio debug con top candidato
        rec = best["rec"]
        return {
            "found": False,
            "answer": "Non ho trovato una risposta precisa. Prova a riformulare leggermente la domanda.",
            "from": None,
            "debug": {
                "query_norm": q_norm,
                "best_file": rec["file"],
                "best_score": round(float(best["score"]), 3),
                "threshold": threshold
            }
        }

# Autoseed se usato come script
if __name__ == "__main__":
    doc_dir = os.environ.get("DOC_DIR", "documenti_gTab")
    build_index(doc_dir)
