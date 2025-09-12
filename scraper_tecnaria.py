# -*- coding: utf-8 -*-
import os, re, json, math, unicodedata, glob
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple

# opzionali ma leggeri
import numpy as np
from rapidfuzz import fuzz, process
from rank_bm25 import BM25Okapi

# =========================
# Config da ENV
# =========================
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")
MIN_CHARS_PER_CHUNK = int(os.environ.get("MIN_CHARS_PER_CHUNK", "200"))
OVERLAP_CHARS = int(os.environ.get("OVERLAP_CHARS", "50"))
TOPK_SEMANTIC = int(os.environ.get("TOPK_SEMANTIC", "20"))
SIMILARITY_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))

# Sinapsi
SINAPSI_ENABLE = os.environ.get("SINAPSI_ENABLE", "1") == "1"
SINAPSI_PATH = os.environ.get("SINAPSI_BOT_JSON", "sinapsi_bot.json")

# =========================
# Stato globale
# =========================
INDEX: List[Dict[str, Any]] = []
BM25 = None
CORPUS_TOKENS: List[List[str]] = []
SINAPSI_BRAIN: Dict[str, Any] = {}

# =========================
# Utils
# =========================
STOPWORDS_MIN = {
    "il","lo","la","i","gli","le","un","uno","una",
    "di","del","della","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da",
    "al","allo","ai","agli","alla","alle",
    "che","come","dove","quando","anche",
    "mi","ti","si","ci","vi","a","de","dal","dall","dalla","dalle",
    "un’","l’","d’"
}

SYNONYMS = [
    ("p560", "pistola"),
    ("sparachiodi", "pistola"),
    ("hbv", "chiodatrice"),
    ("connettore", "connettori"),
    ("eta", "certificazione"),
    ("dop", "dichiarazione prestazione"),
]

def normalize_text(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("'", " ").replace("’", " ")
    s = re.sub(r"[^a-z0-9àèéìòóù]+", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    # sinonimi semplici
    for a, b in SYNONYMS:
        s = s.replace(a, b)
    # rimuovi stopwords troppo comuni
    tokens = [t for t in s.split() if t not in STOPWORDS_MIN]
    return " ".join(tokens)

def tokenize_for_bm25(s: str) -> List[str]:
    # per BM25 manteniamo tokenizzazione semplice (dopo normalize_text)
    return normalize_text(s).split()

def safe_read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        # tenta latin1 in casi sporadici
        try:
            with open(path, "r", encoding="latin-1", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""

def split_into_blocks(text: str) -> List[str]:
    # separa per righe vuote multiple; mantiene blocchi >= MIN_CHARS_PER_CHUNK
    raw_blocks = re.split(r"\n\s*\n", text)
    blocks = []
    for b in raw_blocks:
        bb = b.strip()
        if len(bb) >= MIN_CHARS_PER_CHUNK:
            blocks.append(bb)
    # se non abbiamo blocchi lunghi, prendi tutto il testo
    if not blocks and text.strip():
        blocks = [text.strip()]
    return blocks

def parse_tags(line: str) -> List[str]:
    # es. [TAGS: a, b, c]
    m = re.match(r"\s*\[tags\s*:\s*(.*?)\]\s*$", line, flags=re.I)
    if not m:
        return []
    raw = m.group(1)
    tags = [t.strip() for t in raw.split(",") if t.strip()]
    return tags

def extract_qa(block: str) -> List[Tuple[str,str]]:
    """
    Estrae coppie (D: domanda, R: risposta) dal blocco.
    Restituisce lista di tuple. Se non ci sono, lista vuota.
    """
    lines = [l.strip() for l in block.splitlines() if l.strip()]
    out = []
    cur_q = None
    cur_a_lines = []
    for ln in lines:
        if re.match(r"^d\s*:\s*", ln, flags=re.I):
            # salva QA precedente
            if cur_q and cur_a_lines:
                out.append((cur_q, "\n".join(cur_a_lines).strip()))
                cur_a_lines = []
            cur_q = re.sub(r"^d\s*:\s*", "", ln, flags=re.I).strip()
        elif re.match(r"^r\s*:\s*", ln, flags=re.I):
            content = re.sub(r"^r\s*:\s*", "", ln, flags=re.I).strip()
            if cur_q is None:
                # caso strano: risposta senza domanda
                cur_q = ""
            cur_a_lines = [content]
        else:
            # corpo risposta (se iniziata)
            if cur_a_lines is not None and len(cur_a_lines) > 0:
                cur_a_lines.append(ln)
    # ultimo
    if cur_q and cur_a_lines:
        out.append((cur_q, "\n".join(cur_a_lines).strip()))
    return out

def best_qa_for_query(q: str, qa_list: List[Tuple[str,str]]) -> Optional[str]:
    if not qa_list:
        return None
    nq = normalize_text(q)
    # scegli la R la cui D è più vicina al q
    best = None
    best_score = -1
    for dq, dr in qa_list:
        s = fuzz.token_set_ratio(nq, normalize_text(dq)) / 100.0
        if s > best_score:
            best_score = s
            best = dr
    return best

def clean_answer_text(text: str) -> str:
    """
    Rimuove eventuali righe 'D:'/'R:' e linee troppo tecniche visibili all'utente.
    """
    # elimina prefissi 'D: ...' e 'R: ...' se presenti
    lines = []
    for ln in text.splitlines():
        if re.match(r"^\s*d\s*:", ln, flags=re.I):
            continue
        ln = re.sub(r"^\s*r\s*:\s*", "", ln, flags=re.I)
        lines.append(ln)
    out = "\n".join(lines).strip()
    # clamp lunghezza e rifiniture minime
    out = re.sub(r"\s{3,}", "  ", out)
    return out

# =========================
# Indicizzazione
# =========================
@dataclass
class DocBlock:
    file: str
    text: str
    norm: str
    tags: List[str]
    qa: List[Tuple[str,str]]

def scan_txt_files(doc_dir: str) -> List[str]:
    patterns = [
        os.path.join(doc_dir, "*.txt"),
        os.path.join(doc_dir, "*.TXT"),
    ]
    paths = []
    for p in patterns:
        paths.extend(glob.glob(p))
    paths = sorted(set(paths))
    return paths

def build_index(doc_dir: str) -> None:
    """
    Costruisce INDEX e BM25.
    """
    global INDEX, BM25, CORPUS_TOKENS, SINAPSI_BRAIN

    print(f"[scraper_tecnaria] Inizio indicizzazione da: {os.path.abspath(doc_dir)}\n")
    files = scan_txt_files(doc_dir)
    print(f"[scraper_tecnaria] Trovati {len(files)} file .txt:")
    for p in files:
        print(f"[scraper_tecnaria]   - {p}")

    new_index: List[Dict[str, Any]] = []
    corpus_tokens = []

    for path in files:
        raw = safe_read(path)
        if not raw.strip():
            continue

        blocks = split_into_blocks(raw)
        for b in blocks:
            # tags?
            first_line = b.splitlines()[0].strip() if b.splitlines() else ""
            tags = parse_tags(first_line)

            qa_list = extract_qa(b)
            norm = normalize_text(b + " " + " ".join(tags) + " " + os.path.basename(path))
            tokens = tokenize_for_bm25(b + " " + " ".join(tags) + " " + os.path.basename(path))

            item = {
                "file": os.path.basename(path),
                "path": path,
                "text": b.strip(),
                "norm": norm,
                "tags": tags,
                "qa": qa_list
            }
            new_index.append(item)
            corpus_tokens.append(tokens)

    # costruisci BM25
    if corpus_tokens:
        BM25 = BM25Okapi(corpus_tokens)
        CORPUS_TOKENS = corpus_tokens
    else:
        BM25 = None
        CORPUS_TOKENS = []

    INDEX = new_index

    # carica Sinapsi (best-effort)
    if SINAPSI_ENABLE:
        try:
            if os.path.exists(SINAPSI_PATH):
                with open(SINAPSI_PATH, "r", encoding="utf-8") as f:
                    SINAPSI_BRAIN = json.load(f)
            else:
                SINAPSI_BRAIN = {}
        except Exception:
            SINAPSI_BRAIN = {}

    print(f"[scraper_tecnaria] Indicizzati {len(INDEX)} blocchi da {len(files)} file.")
    if SINAPSI_ENABLE:
        print(f"[scraper_tecnaria] Sinapsi: {'ON' if SINAPSI_BRAIN else 'ON (vuoto)'}  file={SINAPSI_PATH}")

def is_ready() -> bool:
    try:
        return bool(INDEX) and (BM25 is not None or len(INDEX) > 0)
    except Exception:
        return False

# =========================
# Scoring / Ricerca
# =========================
def keyword_overlap(a: str, b: str) -> float:
    ta = set(a.split())
    tb = set(b.split())
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    denom = min(len(ta), len(tb))
    return inter / max(1.0, float(denom))

def name_tag_boost(item: Dict[str,Any], nq: str) -> float:
    boost = 0.0
    # boost nome file
    fname = normalize_text(item["file"])
    if any(tok in fname for tok in nq.split()):
        boost += 0.1
    # boost TAG
    for t in item.get("tags") or []:
        nt = normalize_text(t)
        if any(tok in nt for tok in nq.split()):
            boost += 0.15
    return boost

def score_item(query: str, item: Dict[str,Any]) -> float:
    nq = normalize_text(query)
    # BM25
    bm = 0.0
    if BM25 is not None:
        bm = BM25.get_scores([nq.split()])[INDEX.index(item)][0] if hasattr(BM25, "get_scores") else 0.0
        # La libreria standard usa get_scores(query_tokens) -> array. Qui adattiamo:
        try:
            q_tokens = nq.split()
            bm = BM25.get_scores(q_tokens)[INDEX.index(item)]
        except Exception:
            bm = 0.0
    # overlap keyword
    kw = keyword_overlap(nq, item["norm"])
    # fuzzy
    fz = fuzz.token_set_ratio(nq, item["norm"]) / 100.0
    # boost
    bs = name_tag_boost(item, nq)
    # composizione (tune semplice)
    score = 0.60*bm + 0.25*kw + 0.15*fz + bs
    return score

def pick_answer_text(query: str, item: Dict[str,Any]) -> str:
    # Se nel blocco ci sono QA, prendi la migliore R per la D più vicina alla query
    qa = item.get("qa") or []
    if qa:
        best_r = best_qa_for_query(query, qa)
        if best_r:
            return clean_answer_text(best_r)
    # fallback: restituisci il blocco “ripulito”
    return clean_answer_text(item["text"])

# =========================
# Sinapsi Hook (arricchimento)
# =========================
def sinapsi_enrich(answer: str, meta: Dict[str,Any], query: str) -> str:
    """
    Arricchisce la risposta con regole semplici prese dal JSON.
    Schema supportato (flessibile):
      {
        "rules": [
          {"if_any": ["p560","pistola"], "add": "Usare cartucce certificate..."},
          {"if_all": ["ctf","lamiera"], "add": "Verifica passo su lamiera grecata..."}
        ],
        "prefix": "…",  # opzionale
        "suffix": "…",  # opzionale
        "topics": {
           "p560": "Descrizione sintetica P560…",
           "ctf": "Campo d’impiego CTF…"
        }
      }
    """
    if not SINAPSI_ENABLE or not SINAPSI_BRAIN:
        return answer

    nq = set(normalize_text(query).split())
    enriched = []

    # prefix
    px = SINAPSI_BRAIN.get("prefix")
    if px:
        enriched.append(px.strip())

    # topics: se una chiave è contenuta nei token della query
    topics = SINAPSI_BRAIN.get("topics", {})
    for k, v in topics.items():
        nk = normalize_text(k)
        if nk in nq:
            enriched.append(str(v).strip())

    # rules
    for r in SINAPSI_BRAIN.get("rules", []):
        if_any = [normalize_text(x) for x in r.get("if_any", [])]
        if_all = [normalize_text(x) for x in r.get("if_all", [])]
        ok_any = (not if_any) or any(t in nq for t in if_any)
        ok_all = all(t in nq for t in if_all) if if_all else True
        if ok_any and ok_all:
            add = str(r.get("add","")).strip()
            if add:
                enriched.append(add)

    base = answer.strip()
    # evita duplicati stupidi
    enriched_text = "\n".join([t for t in enriched if t]) if enriched else ""
    if enriched_text:
        # semplice fusione: arricchimento + base
        final = f"{base}\n\n{enriched_text}".strip()
    else:
        final = base

    # suffix
    sx = SINAPSI_BRAIN.get("suffix")
    if sx:
        final = f"{final}\n\n{sx.strip()}"

    return final.strip()

# =========================
# API di ricerca
# =========================
def search_best_answer(query: str,
                       threshold: float = SIMILARITY_THRESHOLD,
                       topk: int = TOPK_SEMANTIC) -> Dict[str, Any]:
    if not INDEX:
        return {"answer":"Indice non pronto.", "found": False, "from": None}

    # ranking
    scored = []
    for it in INDEX:
        s = score_item(query, it)
        scored.append((s, it))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:max(1, topk)]

    if not top:
        return {"answer":"Non ho trovato risposte nei documenti locali.", "found": False, "from": None}

    best_score, best_item = top[0]
    # normalizza score in [0,1] grezzo (non perfetto, ma utile per gate)
    norm_score = min(1.0, max(0.0, best_score))

    if norm_score < threshold:
        # fallback: prova a cercare domanda più simile tra tutte le D: note del blocco top
        answer = pick_answer_text(query, best_item)
        if not answer:
            return {"answer":"Non ho trovato una risposta precisa.", "found": False, "from": None}
    else:
        answer = pick_answer_text(query, best_item)

    # hook Sinapsi (arricchimento)
    try:
        answer = sinapsi_enrich(answer, {"from": best_item.get("file"), "tags": best_item.get("tags")}, query)
    except Exception:
        pass

    # postprocess finale (titolo, clamp, ecc. – per ora minimal)
    answer = answer.strip()

    return {
        "answer": answer,
        "found": True,
        "score": round(float(norm_score), 3),
        "from": best_item.get("file"),
        "tags": best_item.get("tags") or []
    }
