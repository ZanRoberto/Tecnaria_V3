# -*- coding: utf-8 -*-
import os
import re
import json
import math
import unicodedata
from typing import List, Dict, Any, Tuple

# Dipendenze “leggere” presenti nel requirements.txt
try:
    import numpy as np
except Exception:
    np = None

try:
    from rank_bm25 import BM25Okapi
except Exception:
    BM25Okapi = None

try:
    from rapidfuzz import fuzz
except Exception:
    fuzz = None

# =========================
# Config / costanti
# =========================
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")
SINAPSI_JSON = os.environ.get("SINAPSI_BOT_JSON", "SINAPSI_BOT_JSON")

# Stopwords italiane **minimali** (non togliamo termini tecnici)
STOPWORDS_MIN = {
    "il","lo","la","i","gli","le","un","uno","una",
    "di","del","della","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da",
    "al","allo","ai","agli","alla","alle",
    "che","come","dove","quando","anche",
    "mi","ti","si","ci","vi","a","da","de","dal","dall","dalla","dalle",
    "un'", "l'", "d'", "all'", "agl'", "nell'", "sull'", "dell'"
}

# Sinonimi/alias essenziali (espansi in query+tags+filename)
ALIASES = {
    "p560": {"p560", "spit p560", "pistola p560", "sparachiodi p560", "sparachiodi", "pistola spit"},
    "hbv": {"hbv", "chiodatrice", "pistola hbv"},
    "ctf": {"ctf", "piolo", "pioli", "connettori ctf"},
    "cem-e": {"cem-e", "cem e", "ripresa di getto", "riprese di getto", "dowel", "barriera connettori"},
    "diapason": {"diapason", "connettore diapason"},
}

# Pesi del ranking ibrido
W_BM25 = 1.0
W_KEYWORD = 0.9
W_FUZZY_Q = 0.8
W_TAG = 0.6
W_FILENAME = 0.5

# Soglia di sicurezza (fallback in app.py la può abbassare a 0.28)
DEFAULT_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
DEFAULT_TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))

# =========================
# Stato globale
# =========================
INDEX: List[Dict[str, Any]] = []        # lista di blocchi indicizzati
_BM25 = None                             # modello BM25Okapi
_CORPUS_TOKENS: List[List[str]] = []     # corpus tokenizzato
_READY = False                           # indice pronto?

# =========================
# Utility
# =========================
def strip_accents(s: str) -> str:
    # rimuove accenti/diacritici (è → e, ecc.)
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))

def normalize_text(s: str) -> str:
    s = s or ""
    s = strip_accents(s)
    s = s.lower()
    # separa punteggiatura comune
    s = re.sub(r"[\.,;:!?\(\)\[\]\{\}/\\\-\+\*#\"']", " ", s)
    # spazi multipli
    s = re.sub(r"\s+", " ", s).strip()
    return s

def tokenize(s: str) -> List[str]:
    s = normalize_text(s)
    toks = [t for t in s.split() if t and t not in STOPWORDS_MIN]
    return toks

def _expand_aliases(tokens: List[str]) -> List[str]:
    """ Aggiunge sinonimi/alias se compaiono termini 'chiave'. """
    out = set(tokens)
    joined = " ".join(tokens)
    for key, alset in ALIASES.items():
        for alias in alset:
            alias_norm = normalize_text(alias)
            # match su parola intera (grezzo ma efficace)
            if alias_norm in joined:
                out.update(alset)
    return list(out)

def _score_keyword_overlap(q_toks: List[str], doc_toks: List[str]) -> float:
    if not q_toks or not doc_toks:
        return 0.0
    qs = set(q_toks)
    ds = set(doc_toks)
    inter = len(qs & ds)
    # Jaccard leggermente smorzato
    denom = len(qs | ds) or 1
    return inter / denom

def _safe_fuzzy(a: str, b: str) -> float:
    if not fuzz or not a or not b:
        return 0.0
    # rapidfuzz ratio 0..100 → 0..1
    try:
        return fuzz.token_set_ratio(a, b) / 100.0
    except Exception:
        return 0.0

def _read_text_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(path, "r", encoding="latin-1", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""

def _iter_txt_paths(root: str) -> List[str]:
    res = []
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith(".txt"):
                res.append(os.path.join(dirpath, fn))
    return sorted(res)

# =========================
# Parser blocchi TXT
# =========================
_BLOCK_SPLIT = re.compile(r"\n{2,}", re.MULTILINE)

def _parse_file(path: str) -> List[Dict[str, Any]]:
    """
    Parser tollerante:
      - [TAGS: a, b, c] (opzionale)
      - D: Domanda (opzionale)
      - R: Risposta (obbligatoria per blocchi QA)
      - altrimenti blocco “plain” (tutto risposta)
    Ritorna lista di blocchi: {text, q, a, tags, filename, ntext, qn, an, taglist}
    """
    txt = _read_text_file(path)
    if not txt.strip():
        return []

    filename = os.path.basename(path)
    base = os.path.splitext(filename)[0]
    base_norm = normalize_text(base)

    chunks = _BLOCK_SPLIT.split(txt.strip())
    out = []
    for raw in chunks:
        raw = raw.strip()
        if not raw:
            continue

        tags = []
        # Estrai TAGS
        m_tags = re.search(r"^\s*\[TAGS?\s*:\s*(.*?)\]\s*$", raw, flags=re.IGNORECASE | re.MULTILINE)
        if m_tags:
            tag_str = m_tags.group(1)
            tags = [normalize_text(t).strip() for t in re.split(r"[;,/]", tag_str) if t.strip()]
            # togli riga TAGS dal blocco
            raw_wo_tags = re.sub(r"^\s*\[TAGS?.*?\]\s*$", "", raw, flags=re.IGNORECASE | re.MULTILINE).strip()
        else:
            raw_wo_tags = raw

        # Estrai D: e R:
        q, a = "", ""
        m_q = re.search(r"^\s*D\s*:\s*(.+)$", raw_wo_tags, flags=re.IGNORECASE | re.MULTILINE)
        m_r = re.search(r"^\s*R\s*:\s*([\s\S]+)$", raw_wo_tags, flags=re.IGNORECASE)
        if m_q:
            q = m_q.group(1).strip()
        if m_r:
            a = m_r.group(1).strip()

        # Se non c'è R:, trattiamo l'intero blocco come “testo risposta”
        if not a:
            a = raw_wo_tags

        # Normalizzati
        ntext = normalize_text(raw_wo_tags)
        qn = normalize_text(q)
        an = normalize_text(a)
        taglist = [t for t in tags if t]

        out.append({
            "text": raw_wo_tags,
            "q": q,
            "a": a,
            "tags": taglist,
            "filename": filename,
            "file_base": base,
            "file_base_norm": base_norm,
            "ntext": ntext,
            "qn": qn,
            "an": an,
        })
    return out

# =========================
# SINAPSI loader (opzionale)
# =========================
def _load_sinapsi_json() -> List[Dict[str, Any]]:
    """
    Se esiste SINAPSI_BOT_JSON in root (o path in env), carica QA addizionali.
    Formati accettati:
      - lista di { "q": "...", "a": "...", "tags": [...], "source": "..." }
      - dizionario { domanda: risposta }
    """
    paths = [SINAPSI_JSON, os.path.join(os.getcwd(), SINAPSI_JSON)]
    for p in paths:
        if os.path.isfile(p):
            try:
                raw = _read_text_file(p)
                data = json.loads(raw)
                blocks = []
                if isinstance(data, dict):
                    for k, v in data.items():
                        q = str(k).strip()
                        a = str(v).strip()
                        blocks.append({
                            "text": f"D: {q}\nR: {a}",
                            "q": q,
                            "a": a,
                            "tags": [],
                            "filename": os.path.basename(p),
                            "file_base": os.path.basename(p),
                            "file_base_norm": normalize_text(os.path.basename(p)),
                            "ntext": normalize_text(a),
                            "qn": normalize_text(q),
                            "an": normalize_text(a),
                        })
                elif isinstance(data, list):
                    for it in data:
                        q = normalize_text(it.get("q","")).strip()
                        a = it.get("a","").strip()
                        tags = [normalize_text(t) for t in it.get("tags", []) if t]
                        src = it.get("source") or os.path.basename(p)
                        blocks.append({
                            "text": f"D: {it.get('q','')}\nR: {a}",
                            "q": it.get("q",""),
                            "a": a,
                            "tags": tags,
                            "filename": src,
                            "file_base": src,
                            "file_base_norm": normalize_text(src),
                            "ntext": normalize_text(a),
                            "qn": q,
                            "an": normalize_text(a),
                        })
                print(f"[sinapsi] Caricati {len(blocks)} QA da {p}")
                return blocks
            except Exception as e:
                print(f"[sinapsi] Errore nel parsing di {p}: {e}")
                return []
    return []

# =========================
# Build index
# =========================
def build_index(doc_dir: str = DOC_DIR) -> None:
    """
    Scansiona tutti i .txt/.TXT, crea i blocchi e prepara BM25.
    """
    global INDEX, _BM25, _CORPUS_TOKENS, _READY

    INDEX = []
    _CORPUS_TOKENS = []
    _BM25 = None
    _READY = False

    abs_dir = os.path.abspath(doc_dir)
    print(f"[scraper_tecnaria] Inizio indicizzazione da: {abs_dir}\n")

    paths = _iter_txt_paths(abs_dir)
    print(f"[scraper_tecnaria] Trovati {len(paths)} file .txt:")
    for p in paths:
        print(f"[scraper_tecnaria]   - {p}")

    total_blocks = 0
    for p in paths:
        blocks = _parse_file(p)
        if not blocks:
            continue
        INDEX.extend(blocks)
        total_blocks += len(blocks)

    # Aggiungi SINAPSI (se presente)
    sin_blocks = _load_sinapsi_json()
    if sin_blocks:
        INDEX.extend(sin_blocks)
        total_blocks += len(sin_blocks)

    # Prepara corpus tokens (per BM25 e keyword overlap)
    for b in INDEX:
        toks = tokenize(b["text"])
        # arricchisci con tag e nome file
        toks += tokenize(" ".join(b.get("tags", [])))
        toks += tokenize(b.get("file_base", ""))
        toks = _expand_aliases(toks)
        _CORPUS_TOKENS.append(toks)

    # BM25 opzionale (se la libreria è presente e ci sono documenti)
    if BM25Okapi and _CORPUS_TOKENS:
        try:
            _BM25 = BM25Okapi(_CORPUS_TOKENS)
        except Exception as e:
            print(f"[scraper_tecnaria] BM25 init fallita: {e}")
            _BM25 = None

    print(f"[scraper_tecnaria] Indicizzati {total_blocks} blocchi da {len(paths)} file.")
    _READY = total_blocks > 0

def is_ready() -> bool:
    return bool(_READY and INDEX)

# =========================
# Ricerca ibrida
# =========================
def _hybrid_score(q: str, topk: int = DEFAULT_TOPK) -> List[Tuple[float, int]]:
    """
    Restituisce lista [(score, idx_blocco)] ordinata (desc).
    Punteggio ibrido: BM25 + keyword overlap + fuzzy(Q) + boost TAG/nome file.
    """
    if not INDEX:
        return []

    q_norm = normalize_text(q)
    q_tokens = _expand_aliases(tokenize(q_norm))

    # BM25
    bm25_scores = [0.0] * len(INDEX)
    if _BM25 is not None and q_tokens:
        try:
            raw = _BM25.get_scores(q_tokens)
            # normalizzazione z-score suave
            arr = np.array(raw, dtype=float) if np is not None else raw
            if np is not None and len(arr) > 1:
                mu = arr.mean()
                sd = arr.std() or 1.0
                arr = (arr - mu) / sd
                arr = 1 / (1 + np.exp(-arr))  # squash logistica 0..1
                bm25_scores = arr.tolist()
            else:
                # scaletta grezza
                m = max(raw) if raw else 1.0
                bm25_scores = [r / (m or 1.0) for r in raw]
        except Exception as e:
            print(f"[search] BM25 error: {e}")

    # Keyword overlap + fuzzy + boost tag/file
    results: List[Tuple[float, int]] = []
    for i, b in enumerate(INDEX):
        doc_toks = _CORPUS_TOKENS[i] if i < len(_CORPUS_TOKENS) else tokenize(b["text"])

        s_bm = bm25_scores[i] if i < len(bm25_scores) else 0.0
        s_kw = _score_keyword_overlap(q_tokens, doc_toks)

        # fuzzy solo sulla domanda dichiarata del blocco, se c'è
        s_fz = 0.0
        if b.get("q"):
            s_fz = _safe_fuzzy(q_norm, b["qn"])

        # boost da TAG/nome file
        tag_hit = 0.0
        if b.get("tags"):
            # se almeno un tag compare nei token della query, boost
            bt = set(b["tags"])
            if bt & set(q_tokens):
                tag_hit = 1.0

        file_hit = 0.0
        fb = b.get("file_base_norm", "")
        if fb and any(tok in fb.split() for tok in q_tokens):
            file_hit = 1.0

        score = (
            W_BM25 * s_bm +
            W_KEYWORD * s_kw +
            W_FUZZY_Q * s_fz +
            W_TAG * tag_hit +
            W_FILENAME * file_hit
        )

        results.append((score, i))

    results.sort(key=lambda x: x[0], reverse=True)
    return results[:max(5, topk)]

def _best_block(q: str, threshold: float, topk: int) -> Dict[str, Any]:
    ranked = _hybrid_score(q, topk=topk)
    if not ranked:
        return {"found": False}

    best_score, idx = ranked[0]
    blk = INDEX[idx]

    # Heuristic: se il blocco ha D:/R: e la domanda è vagamente simile → mostra solo la R pulita
    answer_text = blk.get("a") or blk.get("text", "")

    return {
        "found": bool(best_score >= threshold),
        "score": float(round(best_score, 3)),
        "from": blk.get("filename"),
        "tags": blk.get("tags") or [],
        "answer": answer_text.strip(),
        "debug": {
            "top": [
                {
                    "score": float(round(s, 3)),
                    "file": INDEX[i].get("filename"),
                    "q": INDEX[i].get("q"),
                    "tags": INDEX[i].get("tags"),
                } for (s, i) in ranked[:5]
            ]
        }
    }

def search_best_answer(query: str, threshold: float = DEFAULT_THRESHOLD, topk: int = DEFAULT_TOPK) -> Dict[str, Any]:
    """
    API usata da app.py
    """
    if not is_ready():
        build_index(DOC_DIR)

    # Prima passata
    res = _best_block(query, threshold, topk)
    if res.get("found"):
        return res

    # Piccolo fallback: riprova abbassando soglia se top1 è molto “tematico”
    if res.get("debug") and res["debug"]["top"]:
        # Se il top ha un filename/tag con match esplicito, consenti soglia più bassa
        top0 = res["debug"]["top"][0]
        if top0 and top0.get("score", 0) > 0.20:
            return _best_block(query, threshold=0.25, topk=topk)

    # Niente da fare
    return {
        "found": False,
        "score": float(res.get("debug", {}).get("top", [{}])[0].get("score", 0.0)) if res else 0.0,
        "from": None,
        "tags": [],
        "answer": "Non ho trovato una risposta precisa nei documenti locali.",
        "debug": res.get("debug") if res else {},
    }

# Se importato, non esegue nulla. Se eseguito direttamente, costruisce l’indice.
if __name__ == "__main__":
    build_index(DOC_DIR)
    print(json.dumps({"ready": is_ready(), "docs": len(INDEX)}, ensure_ascii=False))
