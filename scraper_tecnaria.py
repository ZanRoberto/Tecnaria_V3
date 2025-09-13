# -*- coding: utf-8 -*-
"""
scraper_tecnaria.py — TEC-SINAPSI-2025-09-13-QA-FILELEVEL
Motore locale (TF-IDF + cosine) con:
- QA a livello FILE (non per chunk) -> seleziona sempre la R corretta rispetto alla D migliore.
- Output: mostra al massimo UNA volta la domanda selezionata ("Domanda: ..."), poi SOLO la risposta.
- Pulizia forzata: rimuove 'D:'/'R:' e qualunque '[TAGS: ...]' in uscita.
- Boost su P560 + sinonimi + tag/nome file.
- Arricchimento Sinapsi (append-only) se abilitato e file presente.

API compat:
  - build_index(doc_dir) -> int
  - reload_index() -> None
  - is_ready() -> bool
  - search_best_answer(q, threshold=None, topk=None) -> dict
  - INDEX (compat per app.py; preview chunks)
"""

from __future__ import annotations
import os, re, glob, json, math, time, unicodedata
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
from collections import Counter, defaultdict

__SCRAPER_VERSION__ = "TEC-SINAPSI-2025-09-13-QA-FILELEVEL"

# =================== ENV (Render only, no .env) ===================
DOC_DIR = os.getenv("DOC_DIR", "documenti_gTab")
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.30"))
MIN_CHARS_PER_CHUNK = int(os.getenv("MIN_CHARS_PER_CHUNK", "500"))  # il tuo valore
OVERLAP_CHARS = int(os.getenv("OVERLAP_CHARS", "50"))
TOP_K = int(os.getenv("TOP_K", "5"))
MAX_ANSWER_CHARS = int(os.getenv("MAX_ANSWER_CHARS", "1200"))
DEBUG = os.getenv("DEBUG", "0") == "1"
SINAPSI_ENABLE = os.getenv("SINAPSI_ENABLE", "1") == "1"
SINAPSI_PATH = os.getenv("SINAPSI_BOT_JSON", "SINAPSI_BOT.JSON")
SHOW_MATCHED_QUESTION = os.getenv("SHOW_MATCHED_QUESTION", "1") == "1"  # mostra UNA volta la domanda

# =================== Regex / normalizzazione ===================
STOPWORDS = {
    "tecnaria","spa","s.p.a.",
    "il","lo","la","i","gli","le","un","uno","una","di","del","della","dell","dei","degli","delle",
    "e","ed","o","oppure","con","per","su","tra","fra","in","da","al","allo","ai","agli","alla","alle",
    "che","come","qual","quale","quali","dove","quando","quindi","anche","mi","ti","si","ci","vi"
}
WS = re.compile(r"\s+", flags=re.UNICODE)
TAGS_RE = re.compile(r"^\s*\[TAGS\s*:\s*(.*?)\]\s*$", re.IGNORECASE)
D_RE = re.compile(r"^\s*(D|DOMANDA)\s*:\s*(.*)$", re.IGNORECASE)
R_RE = re.compile(r"^\s*(R|RISPOSTA)\s*:\s*(.*)$", re.IGNORECASE)
Q_NAKED_RE = re.compile(r"^\s*(?:[-•*]\s*)?.{3,160}\?\s*$")

def _strip_accents(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))

def _clean_spaces(s: str) -> str:
    s = s.replace("\r", "\n")
    s = WS.sub(" ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def _tokenize(s: str) -> List[str]:
    s = s.lower()
    s = _strip_accents(s)
    s = re.sub(r"[^a-z0-9àèéìòóùüç]+", " ", s)
    toks = [t for t in s.split() if t and t not in STOPWORDS and len(t) > 1]
    return toks

def _normalize_query(q: str) -> str:
    toks = _tokenize(q)
    return " ".join(toks) if toks else (q or "").strip().lower()

# =================== Strutture ===================
@dataclass
class DocChunk:
    doc: str
    section: str
    text: str
    tags: List[str]
    tfidf: Dict[str, float]

@dataclass
class DocFile:
    name: str
    tags: List[str]
    raw: str
    qa_pairs: List[Tuple[str,str]]  # (D, R)

INDEX: List[Dict[str, any]] = []   # compat con app.py (preview)
_CHUNKS: List[DocChunk] = []
_FILES: Dict[str, DocFile] = {}
_IDF: Dict[str, float] = {}
_SINAPSI: Dict[str, any] = {}

def _log_start():
    if DEBUG:
        print(f"[SCRAPER] Loaded {__SCRAPER_VERSION__} | exports INDEX=True")
_log_start()

# =================== Parsing ===================
def _extract_tags_and_body(text: str) -> Tuple[List[str], str]:
    lines = text.splitlines()
    tags: List[str] = []
    if lines and lines[0].strip().lower().startswith("[tags:"):
        m = TAGS_RE.match(lines[0].strip())
        if m:
            raw = m.group(1)
            tags = [t.strip().lower() for t in re.split(r"[;,]", raw) if t.strip()]
        body = "\n".join(lines[1:]).strip()
        return tags, body
    return [], text.strip()

def _split_by_sections(text: str) -> List[Tuple[str,str]]:
    text = _clean_spaces(text)
    parts = re.split(r"^=+\s*(.*?)\s*=+\s*$", text, flags=re.MULTILINE)
    out: List[Tuple[str,str]] = []
    if len(parts) > 1 and len(parts) % 2 == 1:
        pre = parts[0].strip()
        if pre:
            out.append(("Intro", pre))
        for i in range(1, len(parts), 2):
            sec = parts[i].strip() or f"Sezione_{i//2}"
            body = (parts[i+1] or "").strip()
            if not body:
                continue
            for sub in _sliding_chunks(body, MIN_CHARS_PER_CHUNK, OVERLAP_CHARS):
                out.append((sec, sub))
        return out
    for sub in _sliding_chunks(text, MIN_CHARS_PER_CHUNK, OVERLAP_CHARS):
        out.append(("Body", sub))
    return out

def _sliding_chunks(text: str, min_len: int, overlap: int) -> List[str]:
    text = text.strip()
    if len(text) <= min_len:
        return [text]
    res = []
    start = 0
    while start < len(text):
        end = start + min_len
        res.append(text[start:end])
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return res

def _extract_qa_pairs_fulltext(text: str) -> List[Tuple[str,str]]:
    """Estrae coppie (D,R) a livello file (robusto)."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    pairs: List[Tuple[str,str]] = []
    cur_q: Optional[str] = None
    cur_a: List[str] = []
    in_answer = False

    def _flush():
        nonlocal cur_q, cur_a, in_answer
        if cur_q and cur_a:
            a = "\n".join(cur_a).strip()
            if a:
                pairs.append((cur_q.strip(), a))
        cur_q, cur_a, in_answer = None, [], False

    for i, ln in enumerate(lines):
        if TAGS_RE.match(ln):
            continue
        m_d = D_RE.match(ln)
        m_r = R_RE.match(ln)
        if m_d:
            _flush()
            cur_q = m_d.group(2).strip() or ""
            in_answer = False
            continue
        if m_r:
            in_answer = True
            rest = m_r.group(2).strip()
            if rest:
                cur_a.append(rest)
            continue
        if Q_NAKED_RE.match(ln):
            nxt = lines[i+1].strip() if i+1 < len(lines) else ""
            if R_RE.match(nxt):
                _flush()
                cur_q = ln.strip(" ?")
                in_answer = False
                continue
        if in_answer:
            cur_a.append(ln)
    _flush()
    return pairs

def _strip_QA_from_text(text: str) -> str:
    """Rimuove 'D:'/'R:' e '[TAGS: ...]' dal testo generico (non QA)."""
    out = []
    has_r = any(R_RE.match(ln) for ln in text.splitlines())
    for ln in text.splitlines():
        if TAGS_RE.match(ln): continue
        if D_RE.match(ln): continue
        if has_r and Q_NAKED_RE.match(ln): continue
        ln2 = R_RE.sub("", ln).strip()
        if ln2:
            out.append(ln2)
    return "\n".join(out).strip()

# =================== TF-IDF ===================
def _build_tfidf(chunks: List[DocChunk]) -> None:
    global _IDF
    df = defaultdict(int)
    for ch in chunks:
        toks = set(_tokenize(ch.text))
        for t in toks:
            df[t] += 1
    N = max(1, len(chunks))
    _IDF = {t: math.log((N + 1) / (df_t + 1)) + 1.0 for t, df_t in df.items()}
    for ch in chunks:
        tf = Counter(_tokenize(ch.text))
        vec = {}
        norm = 0.0
        for t, cnt in tf.items():
            idf = _IDF.get(t)
            if not idf: 
                continue
            w = (1 + math.log(cnt)) * idf
            vec[t] = w
            norm += w*w
        norm = math.sqrt(norm) if norm > 0 else 1.0
        ch.tfidf = {t: w / norm for t, w in vec.items()}

def _cosine(qv: Dict[str,float], dv: Dict[str,float]) -> float:
    if not qv or not dv:
        return 0.0
    if len(qv) > len(dv):
        qv, dv = dv, qv
    s = 0.0
    for t, wq in qv.items():
        wd = dv.get(t)
        if wd:
            s += wq * wd
    return s

def _qvec(q: str) -> Dict[str, float]:
    toks = _tokenize(q)
    tf = Counter(toks)
    vec, norm = {}, 0.0
    for t, cnt in tf.items():
        idf = _IDF.get(t)
        if not idf: 
            continue
        w = (1 + math.log(cnt)) * idf
        vec[t] = w
        norm += w*w
    norm = math.sqrt(norm) if norm > 0 else 1.0
    return {t: w / norm for t, w in vec.items()}

# =================== Query expansion / boost ===================
QUERY_SYNONYMS = {
    "p560": {"spit", "pistola", "chiodatrice", "sparachiodi", "p-560"},
    "ctf": {"piolo", "pioli", "connettore", "connettori", "collaborante"},
    "diapason": {"connettore", "connettori"},
    "cem": {"cem-e", "mini", "collegamento", "riprese", "getto"},
}
def _expand_query_tokens(toks: List[str]) -> List[str]:
    out = set(toks)
    for t in list(toks):
        syns = QUERY_SYNONYMS.get(t)
        if syns: out |= syns
    return list(out)

def _tag_boost(q_tokens: List[str], tags: List[str]) -> float:
    if not tags or not q_tokens: return 0.0
    tset = set(tags)
    hits = sum(1 for t in q_tokens if t in tset)
    return min(0.40, 0.08*hits)

def _name_boost(q_tokens: List[str], filename: str) -> float:
    base = os.path.splitext((filename or "").lower())[0]
    if not base or not q_tokens: return 0.0
    if "p560" in q_tokens and "p560" in base: return 0.50
    hits = sum(1 for t in q_tokens if t and t in base)
    return min(0.30, 0.10*hits)

# =================== Build index ===================
def build_index(doc_dir: Optional[str] = None) -> int:
    global _CHUNKS, _FILES, _SINAPSI, INDEX
    _CHUNKS, _FILES, INDEX = [], {}, []
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), doc_dir or DOC_DIR))

    try:
        files = sorted(glob.glob(os.path.join(base, "**", "*.txt"), recursive=True))
    except Exception as e:
        if DEBUG: print(f"[ERROR] Glob fallita su {base}: {e}")
        files = []

    if DEBUG:
        print(f"[SCRAPER] Indicizzazione da: {base}")
        print(f"[SCRAPER] Trovati {len(files)} file .txt")

    total_chunks = 0
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()
            tags, body = _extract_tags_and_body(raw)
            qa_pairs = _extract_qa_pairs_fulltext(body)
            _FILES[os.path.basename(fp)] = DocFile(
                name=os.path.basename(fp),
                tags=tags,
                raw=body,
                qa_pairs=qa_pairs
            )
            for section, chunk in _split_by_sections(body):
                cleaned = _strip_QA_from_text(chunk or "")
                _CHUNKS.append(DocChunk(
                    doc=os.path.basename(fp),
                    section=section or "Body",
                    text=cleaned,
                    tags=tags,
                    tfidf={}
                ))
                total_chunks += 1
        except Exception as e:
            if DEBUG: print(f"[WARN] parsing fallito {fp}: {e}")

    try:
        _build_tfidf(_CHUNKS)
    except Exception as e:
        if DEBUG: print(f"[WARN] build tfidf error: {e}")
        for ch in _CHUNKS: ch.tfidf = {}

    INDEX = [{"file": ch.doc, "section": ch.section, "text": ch.text, "tags": ch.tags} for ch in _CHUNKS]

    if SINAPSI_ENABLE:
        sin_path = os.path.abspath(os.path.join(os.path.dirname(__file__), SINAPSI_PATH))
        _SINAPSI = _safe_load_json(sin_path)
        if DEBUG:
            r = len((_SINAPSI.get("rules") or [])) if _SINAPSI else 0
            t = len((_SINAPSI.get("topics") or {})) if _SINAPSI else 0
            print(f"[SCRAPER] Sinapsi
