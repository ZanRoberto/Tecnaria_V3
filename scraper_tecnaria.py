# -*- coding: utf-8 -*-
"""
scraper_tecnaria.py
- Indicizza tutti i .txt in DOC_DIR (default: documenti_gTab)
- Estrae TAG e coppie D:/R: (domanda/risposta)
- Retrieval ibrido: BM25 (rank-bm25) + keyword overlap + fuzzy (rapidfuzz) + boost TAG/nome file
- Ritorna SOLO la risposta (mai "D:" in output). Aggiunge opzionale arricchimento Sinapsi (topics/rules).
- Robusto: se BM25/rapidfuzz mancano, cade su keyword senza errori.

API esposte:
- build_index(doc_dir) -> int
- is_ready() -> bool
- search_best_answer(q) -> {answer, found, score, from, tags, matched_question?}
- INDEX -> indice globale (for debugging/log)
"""

from __future__ import annotations
import os
import re
import json
import unicodedata
from typing import Any, Dict, List, Tuple, Optional

# ===== Dipendenze soft =====
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
    class _F:
        @staticmethod
        def token_set_ratio(a, b):
            if not a or not b:
                return 0.0
            if a == b:
                return 100.0
            return 50.0 if (a in b or b in a) else 0.0
    fuzz = _F()

# ===== ENV =====
DOC_DIR = os.getenv("DOC_DIR", "documenti_gTab")
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", os.getenv("SIM_THRESHOLD", "0.30")))
TOP_K = int(os.getenv("TOP_K", os.getenv("TOPK_SEMANTIC", "8")))
MIN_CHARS_PER_CHUNK = int(os.getenv("MIN_CHARS_PER_CHUNK", "500"))
MAX_ANSWER_CHARS = int(os.getenv("MAX_ANSWER_CHARS", "1200"))
DEBUG = os.getenv("DEBUG_SCRAPER", os.getenv("DEBUG", "0")) == "1"

SINAPSI_ENABLE = os.getenv("SINAPSI_ENABLE", "1") == "1"
SINAPSI_PATH = os.getenv("SINAPSI_BOT_JSON", "SINAPSI_BOT.JSON")

# ===== Stato globale =====
INDEX: List[Dict[str, Any]] = []
_BM25: Optional[BM25Okapi] = None
_CORPUS_TOKENS: List[List[str]] = []
_SINAPSI: Dict[str, Any] = {}

# ===== Stopwords / Normalizzazione =====
STOPWORDS_MIN = {
    "il","lo","la","i","gli","le","un","uno","una","di","del","della","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da","al","allo","ai","agli","alla","alle",
    "che","come","dove","quando","anche","mi","ti","si","ci","vi","a","da","de","dal","dall",
    "dalla","dalle","non","piu","meno","solo","qual","quale","quali","quanta","quante","quanto",
    "questa","questo","questi","queste","quella","quello","quelli","quelle"
}
_WHITES = re.compile(r"\s+", flags=re.UNICODE)

SYN_QUERY = {
    "p560": ["p 560","p-560","spit","spit p560","sparachiodi","chiodatrice","pistola a cartuccia","pistola a polvere"],
    "ctl": ["ctlb","ctlm","omega","connettori legno calcestruzzo","connettori legno-calcestruzzo","legno calcestruzzo"],
    "ctf": ["pioli","acciaio calcestruzzo","acciaio-calcestruzzo","lamiera grecata","soletta piena"],
    "diapason": ["connettore diapason","distribuzione carico ampia","ripartizione carico"],
    "cem": ["cem-e","mini cem-e","ripresa getto","calcestruzzo nuovo esistente","nuovo a esistente"],
    "distributori": ["rivenditori","dealer","reseller","dove comprare","europa","ue","eu","acquistare"],
    "documenti": ["documentazione","eta","dop","ce","manuale","relazione","schede"]
}

def strip_accents(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))

def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    s = strip_accents(s)
    s = re.sub(r"[^a-z0-9àèéìòóùç\s\-_]", " ", s)
    s = _WHITES.sub(" ", s).strip()
    toks = [t for t in s.split() if t not in STOPWORDS_MIN]
    return " ".join(toks)

def expand_query_synonyms(q: str) -> str:
    base = normalize_text(q)
    tokens = base.split()
    extra = []
    for t in list(tokens):
        for key, syns in SYN_QUERY.items():
            if t == key or t in syns:
                extra.extend([normalize_text(x) for x in [key] + syns])
    # uniq mantenendo ordine
    seen = set()
    out = []
    for t in tokens + extra:
        if t and t not in seen:
            out.append(t)
            seen.add(t)
    return " ".join(out)

# ===== Parsing TXT =====
_TAGS_RE = re.compile(r"^\s*\[TAGS\s*:\s*(.*?)\]\s*$", re.IGNORECASE)
_D_RE = re.compile(r"^\s*(D|DOMANDA)\s*:\s*(.*)$", re.IGNORECASE)
_R_RE = re.compile(r"^\s*(R|RISPOSTA)\s*:\s*(.*)$", re.IGNORECASE)

def parse_txt_file(path: str) -> Dict[str, Any]:
    tags: List[str] = []
    qas: List[Dict[str, str]] = []
    text_lines: List[str] = []

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.read().splitlines()

    cur_q: Optional[str] = None
    cur_a: List[str] = []

    for raw in lines:
        line = raw.rstrip("\n")

        m_tags = _TAGS_RE.match(line)
        if m_tags:
            tline = m_tags.group(1)
            parts = [t.strip() for t in tline.split(",") if t.strip()]
            tags.extend(parts)
            continue

        m_d = _D_RE.match(line)
        if m_d:
            if cur_q is not None:
                qas.append({"q": (cur_q or "").strip(), "a": "\n".join(cur_a).strip()})
            cur_q = m_d.group(2).strip()
            cur_a = []
            continue

        m_r = _R_RE.match(line)
        if m_r:
            cur_a.append(m_r.group(2))
            continue

        # linea generica
        text_lines.append(line)
        if cur_q is not None:
            cur_a.append(line)

    if cur_q is not None:
        qas.append({"q": (cur_q or "").strip(), "a": "\n".join(cur_a).strip()})

    full_text = "\n".join(text_lines).strip()
    return {
        "file": os.path.basename(path),
        "path": path,
        "name": os.path.splitext(os.path.basename(path))[0].lower(),
        "tags": tags,
        "norm_tags": [normalize_text(t) for t in tags],
        "qas": qas,
        "text": full_text,
        "norm": normalize_text(full_text)
    }

def list_txt_files(doc_dir: str) -> List[str]:
    out = []
    for root, _, files in os.walk(doc_dir):
        for fn in files:
            if fn.lower().endswith(".txt"):
                out.append(os.path.join(root, fn))
    out.sort()
    return out

# ===== Indicizzazione =====
def _build_bm25(items: List[Dict[str, Any]]) -> Tuple[Optional[BM25Okapi], List[List[str]]]:
    if BM25Okapi is None or np is None:
        return None, []
    corpus_tokens: List[List[str]] = []
    for it in items:
        toks = []
        if it.get("norm"):
            toks.extend(it["norm"].split())
        for qa in it.get("qas", []):
            qn = normalize_text(qa.get("q", ""))
            toks.extend(qn.split())
            an = normalize_text(qa.get("a", ""))
            # (opzionale) includiamo anche risposta per migliorare recall
            toks.extend(an.split())
        corpus_tokens.append([t for t in toks if t])
    if not corpus_tokens:
        return None, []
    try:
        bm25 = BM25Okapi(corpus_tokens)
        return bm25, corpus_tokens
    except Exception:
        return None, []

def build_index(doc_dir: Optional[str] = None) -> int:
    """Costruisce indice globale e carica Sinapsi."""
    global INDEX, _BM25, _CORPUS_TOKENS, _SINAPSI
    base = doc_dir or DOC_DIR
    print(f"[SCRAPER] Indicizzazione da: {os.path.abspath(base)}", flush=True)

    if not os.path.exists(base):
        INDEX = []
        _BM25 = None
        _CORPUS_TOKENS = []
        print(f"[SCRAPER][WARN] DOC_DIR non esiste: {base}", flush=True)
        return 0

    paths = list_txt_files(base)
    print(f"[SCRAPER] Trovati {len(paths)} file .txt", flush=True)

    items: List[Dict[str, Any]] = []
    for p in paths:
        try:
            it = parse_txt_file(p)
            # filtra i blocchi troppo corti, ma garantisci almeno 1 item per file
            keep = (len((it.get("text") or "")) >= MIN_CHARS_PER_CHUNK) or it.get("qas")
            if keep:
                items.append(it)
        except Exception as e:
            print(f"[SCRAPER][WARN] Errore parsing {p}: {e}", flush=True)

    # BM25
    _BM25, _CORPUS_TOKENS = _build_bm25(items)

    # carica Sinapsi
    _SINAPSI = {}
    if SINAPSI_ENABLE:
        try:
            if os.path.exists(SINAPSI_PATH):
                with open(SINAPSI_PATH, "r", encoding="utf-8", errors="ignore") as f:
                    _SINAPSI = json.load(f) or {}
                print(f"[SCRAPER] Sinapsi ON (rules={len(_SINAPSI.get('rules', []))}, topics={len(_SINAPSI.get('topics', {}))}) file={SINAPSI_PATH}", flush=True)
            else:
                print(f"[SCRAPER] Sinapsi file non trovato: {os.path.abspath(SINAPSI_PATH)}", flush=True)
        except Exception as e:
            print(f"[SCRAPER][WARN] Errore lettura Sinapsi: {e}", flush=True)

    INDEX = items
    print(f"[SCRAPER] Compat: INDEX len={len(INDEX)}", flush=True)
    return len(INDEX)

def is_ready() -> bool:
    return bool(INDEX)

# ===== Scoring =====
def _keyword_overlap(q: str, doc_norm: str) -> float:
    qset = set(q.split())
    if not qset:
        return 0.0
    dset = set(doc_norm.split())
    if not dset:
        return 0.0
    inter = len(qset & dset)
    return inter / max(1.0, float(len(qset)))

def _boost_name_tags(item: Dict[str, Any], nq: str) -> float:
    boost = 0.0
    name = item.get("name", "")
    if name and (name in nq or nq in name):
        boost += 0.12
    for t in item.get("norm_tags", []):
        if t and (t in nq or nq in t):
            boost += 0.08
            break
    return min(boost, 0.25)

def _pick_best_answer_text(query: str, item: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Ritorna (answer_text, matched_question) senza mai mostrare 'D:' all'utente."""
    nq = normalize_text(query)
    best_ans = ""
    best_q = None
    best_s = -1.0

    # prova a matchare sulle D:
    for qa in item.get("qas", []):
        dq = (qa.get("q") or "").strip()
        dr = (qa.get("a") or "").strip()
        qn = normalize_text(dq)
        s = (fuzz.token_set_ratio(nq, qn) / 100.0) if qn else 0.0
        if s > best_s:
            # ripulisci eventuali "R:" all'inizio
            dr = re.sub(r"^\s*(R|RISPOSTA)\s*:\s*", "", dr, flags=re.IGNORECASE).strip()
            best_ans = dr
            best_q = dq
            best_s = s

    # fallback: usa l'incipit del testo
    if not best_ans:
        raw = (item.get("text") or "").strip()
        raw = re.sub(r"^\s*(D|DOMANDA)\s*:\s*", "", raw, flags=re.IGNORECASE | re.MULTILINE)
        raw = re.sub(r"^\s*(R|RISPOSTA)\s*:\s*", "", raw, flags=re.IGNORECASE | re.MULTILINE)
        best_ans = raw.split("\n\n")[0].strip()
        best_q = None

    # clamp elegante
    if MAX_ANSWER_CHARS and len(best_ans) > MAX_ANSWER_CHARS:
        cut = best_ans[:MAX_ANSWER_CHARS]
        m = re.search(r"(?s)^(.+?[\.!\?])(\s|$)", cut)
        best_ans = (m.group(1) if m else cut).rstrip() + " …"

    return best_ans or "", best_q

# ===== Sinapsi =====
def _sinapsi_enrich(answer: str, query: str) -> str:
    if not SINAPSI_ENABLE or not _SINAPSI:
        return answer
    nq = set(normalize_text(query).split())
    out_parts: List[str] = []

    # topics
    topics = _SINAPSI.get("topics", {}) or {}
    for k, v in topics.items():
        nk = normalize_text(k)
        if nk in nq:
            out_parts.append(str(v).strip())

    # rules
    rules = _SINAPSI.get("rules", []) or []
    for r in rules:
        if_any = [normalize_text(x) for x in (r.get("if_any") or [])]
        if_all = [normalize_text(x) for x in (r.get("if_all") or [])]
        ok_any = (not if_any) or any(t in nq for t in if_any)
        ok_all = all(t in nq for t in if_all) if if_all else True
        if ok_any and ok_all:
            add = str(r.get("add", "")).strip()
            if add:
                out_parts.append(add)

    prefix = str(_SINAPSI.get("prefix", "")).strip()
    suffix = str(_SINAPSI.get("suffix", "")).strip()

    final = answer.strip()
    if prefix:
        final = f"{prefix}\n\n{final}".strip()
    if out_parts:
        final = f"{final}\n\n" + "\n".join([p for p in out_parts if p])
    if suffix:
        final = f"{final}\n\n{suffix}".strip()

    return final.strip()

# ===== Ricerca =====
def search_best_answer(query: str) -> Dict[str, Any]:
    if not INDEX:
        return {"answer": "Indice non pronto.", "found": False, "from": None}

    nq_base = normalize_text(query)
    nq = expand_query_synonyms(nq_base)

    # BM25 una volta sola
    bm_scores = None
    if _BM25 is not None and np is not None:
        try:
            q_tokens = nq.split()
            bm_arr = _BM25.get_scores(q_tokens)
            bm_max = float(np.max(bm_arr)) if getattr(bm_arr, "size", 0) > 0 else 1.0
            bm_scores = (bm_arr / bm_max) if bm_max > 0 else bm_arr
        except Exception:
            bm_scores = None

    # scoring ibrido
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for idx, it in enumerate(INDEX):
        kw = _keyword_overlap(nq, it.get("norm", ""))
        fz = (fuzz.token_set_ratio(nq, it.get("norm", "")) / 100.0) if it.get("norm") else 0.0
        bs = _boost_name_tags(it, nq)
        bm = float(bm_scores[idx]) if bm_scores is not None else 0.0
        score = 0.60*bm + 0.25*kw + 0.15*fz + bs
        scored.append((score, it))

    if not scored:
        return {"answer": "Non ho trovato risposte nei documenti locali.", "found": False, "from": None}

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:max(1, TOP_K)]
    best_score, best_item = top[0]
    norm_score = max(0.0, min(1.0, float(best_score)))

    answer_txt, matched_q = _pick_best_answer_text(query, best_item)
    if not answer_txt:
        return {"answer": "(nessuna risposta)", "found": False, "from": best_item.get("file")}

    # soglia
    if norm_score < SIMILARITY_THRESHOLD:
        # second chance con query base (senza espansione sinonimi)
        bm_scores2 = None
        if _BM25 is not None and np is not None:
            try:
                bm_arr2 = _BM25.get_scores(nq_base.split())
                bm_max2 = float(np.max(bm_arr2)) if getattr(bm_arr2, "size", 0) > 0 else 1.0
                bm_scores2 = (bm_arr2 / bm_max2) if bm_max2 > 0 else bm_arr2
            except Exception:
                bm_scores2 = None

        rescored: List[Tuple[float, Dict[str, Any]]] = []
        for idx, it in enumerate(INDEX):
            kw = _keyword_overlap(nq_base, it.get("norm", ""))
            fz = (fuzz.token_set_ratio(nq_base, it.get("norm", "")) / 100.0) if it.get("norm") else 0.0
            bs = _boost_name_tags(it, nq_base)
            bm = float(bm_scores2[idx]) if bm_scores2 is not None else 0.0
            s = 0.60*bm + 0.25*kw + 0.15*fz + bs
            rescored.append((s, it))
        rescored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_item = rescored[0]
        norm_score = max(0.0, min(1.0, float(best_score)))
        answer_txt, matched_q = _pick_best_answer_text(query, best_item) or (answer_txt, matched_q)

    # Enrichment Sinapsi
    try:
        answer_txt = _sinapsi_enrich(answer_txt, query)
    except Exception:
        pass

    out = {
        "answer": answer_txt.strip(),
        "found": True,
        "score": round(norm_score, 3),
        "from": best_item.get("file"),
        "tags": best_item.get("tags") or []
    }
    # For debugging UI (puoi decidere di nasconderlo in app.py)
    if matched_q:
        out["matched_question"] = matched_q
    return out

# ===== Main (debug locale) =====
if __name__ == "__main__":
    print("[SCRAPER] build_index()…", flush=True)
    n = build_index(DOC_DIR)
    print(f"[SCRAPER] done. docs={n}", flush=True)
    while True:
        try:
            q = input("Q> ").strip()
        except EOFError:
            break
        if not q:
            continue
        print(search_best_answer(q))
