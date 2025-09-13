# -*- coding: utf-8 -*-
"""
scraper_tecnaria.py — TEC-SINAPSI-2025-09-13-FULL+SAN
Motore di ricerca locale (TF-IDF + cosine) con arricchimento opzionale Sinapsi.

Punti chiave:
- Export pubblico `INDEX` (compat col tuo app.py).
- Auto-build indice alla prima ricerca se vuoto.
- Pulizia Q/A robusta: rimuove sempre "D:"/"R:" e domande nude.
- Final sanitize: PRIMA di rispondere ripulisce ancora (in caso il testo sorgente fosse formattato in modo anomalo).
- Ranking: TF-IDF + tag boost + name boost (P560.txt sale), sinonimi query (p560/spit/chiodatrice/sparachiodi).
- Sinapsi: se SINAPSI_ENABLE=1 e SINAPSI_BOT_JSON punta a un file esistente, arricchisce; se manca, non influisce.
- Nessun .env: legge SOLO environment (Render).

API:
  build_index(doc_dir: str|None) -> int
  reload_index() -> None
  is_ready() -> bool
  search_best_answer(q: str, threshold: float|None=None, topk: int|None=None) -> dict
  risposta_document_first(q: str) -> str
  INDEX (alias pubblico)
"""

from __future__ import annotations
import os, re, glob, json, math, time, unicodedata
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
from collections import Counter, defaultdict

__SCRAPER_VERSION__ = "TEC-SINAPSI-2025-09-13-FULL+SAN"

# =============== ENV (no .env file reading) ===============
DOC_DIR = os.getenv("DOC_DIR", "documenti_gTab")
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.30"))
MIN_CHARS_PER_CHUNK = int(os.getenv("MIN_CHARS_PER_CHUNK", "200"))
OVERLAP_CHARS = int(os.getenv("OVERLAP_CHARS", "50"))
TOP_K = int(os.getenv("TOP_K", "5"))
MAX_ANSWER_CHARS = int(os.getenv("MAX_ANSWER_CHARS", "1200"))
DEBUG = os.getenv("DEBUG", "0") == "1"
SINAPSI_ENABLE = os.getenv("SINAPSI_ENABLE", "1") == "1"
SINAPSI_PATH = os.getenv("SINAPSI_BOT_JSON", "SINAPSI_BOT.JSON")

# =============== Normalizzazione / Regex ===============
STOPWORDS = {
    "tecnaria","spa","s.p.a.",
    "il","lo","la","i","gli","le","un","uno","una","di","del","della","dell","dei","degli","delle",
    "e","ed","o","oppure","con","per","su","tra","fra","in","da","al","allo","ai","agli","alla","alle",
    "che","come","qual","quale","quali","dove","quando","quindi","anche","mi","ti","si","ci","vi"
}
WS = re.compile(r"\s+", flags=re.UNICODE)
_TAGS_RE = re.compile(r"^\s*\[TAGS\s*:\s*(.*?)\]\s*$", re.IGNORECASE)
_D_RE = re.compile(r"^\s*(D|DOMANDA)\s*:\s*(.*)$", re.IGNORECASE)
_R_RE = re.compile(r"^\s*(R|RISPOSTA)\s*:\s*(.*)$", re.IGNORECASE)
_Q_NAKED_RE = re.compile(r"^\s*(?:[-•*]\s*)?.{3,160}\?\s*$")

def _strip_accents(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))

def _clean(s: str) -> str:
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

# =============== Indice dati ===============
@dataclass
class DocChunk:
    doc: str
    section: str
    text: str
    tags: List[str]
    tfidf: Dict[str, float]

_INDEX: List[DocChunk] = []
_IDF: Dict[str, float] = {}
_SINAPSI: Dict[str, any] = {}

# Alias pubblico per compatibilità
INDEX: List[Dict[str, any]] = []

# =============== Log banner ===============
def _log_import_banner():
    if DEBUG:
        print(f"[SCRAPER] Loaded {__SCRAPER_VERSION__} | exports INDEX=True")

_log_import_banner()

def _update_public_index() -> None:
    global INDEX
    INDEX = [{"file": ch.doc, "section": ch.section, "text": ch.text, "tags": ch.tags} for ch in _INDEX]
    if DEBUG:
        print(f"[SCRAPER] Compat: INDEX len={len(INDEX)}")

# =============== Parsing file / sezioni ===============
def _extract_tags_and_body(text: str) -> Tuple[List[str], str]:
    lines = text.splitlines()
    tags: List[str] = []
    if lines and lines[0].strip().lower().startswith("[tags:"):
        m = _TAGS_RE.match(lines[0].strip())
        if m:
            raw = m.group(1)
            tags = [t.strip().lower() for t in re.split(r"[;,]", raw) if t.strip()]
        body = "\n".join(lines[1:]).strip()
        return tags, body
    return [], text.strip()

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

def _split_by_sections(text: str) -> List[Tuple[str,str]]:
    text = _clean(text)
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

# =============== TF-IDF ===============
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
            norm += w * w
        norm = math.sqrt(norm) if norm > 0 else 1.0
        ch.tfidf = {t: w / norm for t, w in vec.items()}

def _cosine(qv: Dict[str,float], dv: Dict[str,float]) -> float:
    if not qv or not dv:
        return 0.0
    # iteriamo sul vettore più corto
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
        norm += w * w
    norm = math.sqrt(norm) if norm > 0 else 1.0
    return {t: w / norm for t, w in vec.items()}

# =============== QA extraction / cleanup ===============
def _extract_qa_pairs(text: str) -> List[Tuple[str,str]]:
    """Estrae coppie (Q,A). Supporta sia 'D:'/'R:' sia 'domanda?\nR:'."""
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
        m_d = _D_RE.match(ln)
        m_r = _R_RE.match(ln)
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
        if _Q_NAKED_RE.match(ln):
            nxt = lines[i+1].strip() if i+1 < len(lines) else ""
            if _R_RE.match(nxt):
                _flush()
                cur_q = ln.strip(" ?")
                in_answer = False
                continue
        if in_answer:
            cur_a.append(ln)

    _flush()
    return pairs

def _strip_QA(text: str) -> str:
    """Rimuove domande e prefissi 'R:' dal testo."""
    lines = text.splitlines()
    has_r = any(_R_RE.match(ln) for ln in lines)
    out = []
    for ln in lines:
        if _TAGS_RE.match(ln):
            continue
        if _D_RE.match(ln):
            continue  # elimina "D:"
        if has_r and _Q_NAKED_RE.match(ln):
            continue  # elimina domanda nuda se c'è una risposta nel blocco
        ln2 = _R_RE.sub("", ln).strip()  # rimuove "R:" lasciando il contenuto
        if ln2:
            out.append(ln2)
    return "\n".join(out).strip()

def _final_sanitize(ans: str) -> str:
    """Pulizia extra a valle, per evitare qualsiasi 'D:'/'R:' o domanda nuda residua."""
    if not ans:
        return ans
    # rimuovi eventuale riga TAGS
    ans = "\n".join(ln for ln in ans.splitlines() if not _TAGS_RE.match(ln))
    # togli prefissi R:
    ans = "\n".join(_R_RE.sub("", ln).strip() for ln in ans.splitlines())
    # elimina righe che iniziano con 'D:' o 'Domanda:'
    ans = "\n".join(ln for ln in ans.splitlines() if not _D_RE.match(ln))
    # elimina domande nude iniziali (solo finché capitano in testa)
    lines = ans.splitlines()
    out = []
    dropping_head = True
    for ln in lines:
        if dropping_head and _Q_NAKED_RE.match(ln):
            continue
        dropping_head = False
        out.append(ln)
    ans = "\n".join(out).strip()
    return ans

def _best_qa_answer_for_query(query: str, text: str) -> Optional[str]:
    pairs = _extract_qa_pairs(text)
    if not pairs:
        return None
    qnorm = set(_tokenize(query))
    best_s = -1.0
    best_a = None
    for q, a in pairs:
        qn = set(_tokenize(q))
        inter = len(qnorm & qn)
        denom = max(1, min(len(qnorm), len(qn)))
        s = inter / denom
        if s > best_s:
            best_s = s
            best_a = a
    return best_a

# =============== Query expansion / boost ===============
_QUERY_SYNONYMS = {
    "p560": {"spit", "pistola", "chiodatrice", "sparachiodi", "p-560"},
    "ctf": {"piolo", "pioli", "connettore", "connettori", "collaborante"},
    "diapason": {"connettore", "connettori"},
    "cem": {"cem-e", "mini", "collegamento", "riprese", "getto"},
}

def _expand_query_tokens(toks: List[str]) -> List[str]:
    out = set(toks)
    for t in list(toks):
        syns = _QUERY_SYNONYMS.get(t)
        if syns:
            out |= syns
    return list(out)

def _tag_boost(q_tokens: List[str], tags: List[str]) -> float:
    if not tags or not q_tokens:
        return 0.0
    tset = set(tags)
    hits = sum(1 for t in q_tokens if t in tset)
    return min(0.40, 0.08 * hits)

def _name_boost(q_tokens: List[str], filename: str) -> float:
    base = os.path.splitext((filename or "").lower())[0]
    if not base or not q_tokens:
        return 0.0
    if "p560" in q_tokens and "p560" in base:
        return 0.50
    hits = sum(1 for t in q_tokens if t and t in base)
    return min(0.30, 0.10 * hits)

# =============== I/O util ===============
def _abspath(base: str) -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, base))

def _safe_load_json(path: str) -> dict:
    try:
        if not os.path.exists(path):
            if DEBUG: print(f"[SCRAPER] Sinapsi file non trovato: {path}")
            return {}
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return json.load(f)
    except Exception as e:
        if DEBUG: print(f"[WARN] Sinapsi JSON invalido ({path}): {e}")
        return {}

# =============== Build / Ready ===============
def build_index(doc_dir: Optional[str] = None) -> int:
    global _INDEX, _SINAPSI, _IDF, INDEX
    _INDEX, _SINAPSI, _IDF, INDEX = [], {}, {}, []

    base = _abspath(doc_dir or DOC_DIR)
    try:
        files = sorted(glob.glob(os.path.join(base, "**", "*.txt"), recursive=True))
    except Exception as e:
        if DEBUG: print(f"[ERROR] Glob fallita su {base}: {e}")
        files = []

    if DEBUG:
        print(f"[SCRAPER] Indicizzazione da: {base}")
        print(f"[SCRAPER] Trovati {len(files)} file .txt")

    tot_chunks = 0
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()
            tags, body = _extract_tags_and_body(raw)
            for section, chunk in _split_by_sections(body):
                cleaned = _strip_QA(chunk or "")
                _INDEX.append(DocChunk(
                    doc=os.path.basename(fp),
                    section=section or "Body",
                    text=cleaned,
                    tags=[(t or "").lower() for t in tags],
                    tfidf={}
                ))
                tot_chunks += 1
        except Exception as e:
            if DEBUG: print(f"[WARN] Lettura/parsing fallita {fp}: {e}")

    try:
        _build_tfidf(_INDEX)
    except Exception as e:
        if DEBUG: print(f"[WARN] build tfidf error: {e}")
        for ch in _INDEX: ch.tfidf = {}

    _update_public_index()

    if SINAPSI_ENABLE:
        _SINAPSI = _safe_load_json(_abspath(SINAPSI_PATH))

    if DEBUG:
        r = len((_SINAPSI.get("rules") or [])) if _SINAPSI else 0
        t = len((_SINAPSI.get("topics") or {})) if _SINAPSI else 0
        print(f"[SCRAPER] Sinapsi {'ON' if _SINAPSI else 'OFF'} (rules={r}, topics={t}) file={_abspath(SINAPSI_PATH)}")
        print(f"[SCRAPER] Chunk indicizzati: {tot_chunks}")
    return tot_chunks

def reload_index() -> None:
    build_index(DOC_DIR)

def is_ready() -> bool:
    return bool(_INDEX) or bool(INDEX)

# =============== Search ===============
def _intent_expand(q: str) -> List[str]:
    return [q.lower()]

def _search_raw(q: str, top_k: int) -> List[Tuple[float, DocChunk]]:
    if not _INDEX:
        try:
            build_index(DOC_DIR)
        except Exception as e:
            if DEBUG: print(f"[ERROR] build_index in _search_raw: {e}")
            return []
    try:
        q_norm = _normalize_query(q or "")
        variants = _intent_expand(q_norm)
        q_tokens = _expand_query_tokens(_tokenize(q_norm))
        q_vecs = [(_qvec(v), _tokenize(v)) for v in variants]
    except Exception as e:
        if DEBUG: print(f"[WARN] normalize/query vec error: {e}")
        return []

    scored: List[Tuple[float, DocChunk]] = []
    try:
        for ch in _INDEX:
            best = 0.0
            for vec, toks in q_vecs:
                base = _cosine(vec, ch.tfidf or {})
                boost = _tag_boost(toks, ch.tags or [])
                nboost = _name_boost(q_tokens, ch.doc)
                s = base + boost + nboost
                if "p560" in q_tokens and "p560" in (ch.doc.lower()):
                    s += 0.20
                if s > best:
                    best = s
            if best > 0:
                scored.append((best, ch))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:max(1, top_k or 5)]
    except Exception as e:
        if DEBUG: print(f"[ERROR] scoring error: {e}")
        return []

def _sinapsi_enrich(answer: str, query: str, meta: Dict[str, any]) -> str:
    if not (SINAPSI_ENABLE and _SINAPSI):
        return answer
    def _tok(s: str) -> set:
        return set(_tokenize(s or ""))
    nq = _tok(query)
    extra: List[str] = []
    px = _SINAPSI.get("prefix")
    if px: extra.append(str(px).strip())
    for k, v in (_SINAPSI.get("topics") or {}).items():
        if _tok(str(k)) & nq:
            extra.append(str(v).strip())
    for r in (_SINAPSI.get("rules") or []):
        any_ = _tok(" ".join(r.get("if_any", [])))
        all_ = _tok(" ".join(r.get("if_all", [])))
        ok_any = (not any_) or bool(any_ & nq)
        ok_all = (not all_) or all(t in nq for t in all_)
        if ok_any and ok_all:
            add = str(r.get("add","")).strip()
            if add:
                extra.append(add)
    enrichment = "\n".join([t for t in extra if t])
    final = (answer.strip() + ("\n\n" + enrichment if enrichment else "")).strip()
    sx = _SINAPSI.get("suffix")
    if sx:
        final = f"{final}\n\n{str(sx).strip()}".strip()
    return final

def search_best_answer(query: str,
                       threshold: Optional[float] = None,
                       topk: Optional[int] = None) -> Dict[str, any]:
    thr = SIMILARITY_THRESHOLD if threshold is None else float(threshold)
    k = TOP_K if topk is None else int(topk)
    try:
        scored = _search_raw(query or "", k)
        if not scored:
            if DEBUG: print(f"[SCRAPER] Nessun candidato per: {query}")
            return {"answer": "", "found": False, "from": None}
        best_score, best_chunk = scored[0]

        if best_score < max(0.0, thr):
            if best_score < max(0.15, thr * 0.7):
                return {"answer": "", "found": False, "from": None}

        # Risposta: prova Q/A, altrimenti testo ripulito
        ans = _best_qa_answer_for_query(query, best_chunk.text)
        if not ans:
            ans = best_chunk.text

        # Arricchimento Sinapsi
        try:
            ans = _sinapsi_enrich(ans, query, {
                "file": best_chunk.doc,
                "section": best_chunk.section,
                "tags": best_chunk.tags
            })
        except Exception as e:
            if DEBUG: print(f"[WARN] enrich error: {e}")

        # Final sanitize SEMPRE (togli D:/R: e domande nude residue)
        ans = _final_sanitize(ans)

        # Taglio elegante
        if MAX_ANSWER_CHARS and len(ans) > MAX_ANSWER_CHARS:
            cut = ans[:MAX_ANSWER_CHARS]
            m = re.search(r"(?s)^(.+?[\.!\?])(\s|$)", cut)
            ans = (m.group(1) if m else cut).rstrip() + " …"

        return {
            "answer": ans.strip(),
            "found": True,
            "score": round(float(best_score), 3),
            "from": best_chunk.doc,
            "tags": best_chunk.tags or []
        }
    except Exception as e:
        if DEBUG: print(f"[ERROR] search_best_answer fatal: {e}")
        return {"answer": "", "found": False, "from": None}

def risposta_document_first(domanda: str) -> str:
    try:
        domanda = (domanda or "").strip()
        if not domanda:
            return ""
        out = search_best_answer(domanda)
        ans = (out.get("answer") or "").strip()
        return ans if ans else "Non ho trovato riferimenti nei documenti locali."
    except Exception as e:
        if DEBUG: print(f"[ERROR] risposta_document_first: {e}")
        return "Non ho trovato riferimenti nei documenti locali."

if __name__ == "__main__":
    print(f"[INFO] DOC_DIR={os.path.abspath(DOC_DIR)} thr={SIMILARITY_THRESHOLD} topk={TOP_K} debug={DEBUG} ver={__SCRAPER_VERSION__}")
    t0 = time.time()
    n = build_index(DOC_DIR)
    print(f"[INFO] Indice pronto: {n} chunk in {time.time()-t0:.2f}s")
    try:
        while True:
            q = input("Domanda> ").strip()
            if not q:
                continue
            res = search_best_answer(q)
            print("\n--- RISPOSTA ---")
            print(res.get("answer") or "[vuota]")
            print(f"\n[from={res.get('from')} score={res.get('score')} tags={res.get('tags')}]")
            print()
    except (EOFError, KeyboardInterrupt):
        print("\nBye.")
