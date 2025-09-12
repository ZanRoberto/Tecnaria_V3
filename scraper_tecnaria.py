# -*- coding: utf-8 -*-
import os, re, json, math
from collections import defaultdict
from typing import List, Dict, Any

# ===== Config di base =====
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")
SIMILARITY_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
TOPK_SEMANTIC = int(os.environ.get("TOPK_SEMANTIC", "20"))

# ===== Stato globale indice =====
INDEX: List[Dict[str, Any]] = []
BUILDING = False

# ===== Stopwords minime (italiano) =====
STOPWORDS_MIN = {
    "il","lo","la","i","gli","le","un","uno","una",
    "di","del","della","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da",
    "al","allo","ai","agli","alla","alle",
    "che","come","dove","quando","anche",
    "mi","ti","si","ci","vi","a","da","de","dal","dall","dalla","dalle",
}

# ===== Sinonimi semplici per match (aggiungine pure) =====
SYNONYMS = {
    "p560": {"p560", "p 560", "p-560", "pistola", "sparachiodi", "spit"},
    "contatti": {"contatti", "telefono", "telefono tecnaria", "mail", "email", "assistenza"},
    "connettori": {"connettori", "pioli", "dispositivi", "elementi", "connettore"},
    "schede tecniche": {"scheda tecnica", "schede tecniche", "catalogo", "brochure"},
    "dop": {"dop", "dichiarazione di prestazione", "dichiarazioni di prestazione"},
    "eta": {"eta", "valutazione tecnica europea", "european technical assessment"},
    "hbv": {"hbv", "chiodatrice", "pistola hbv"},
    "diapason": {"diapason"},
    "ctf": {"ctf"},
    "cem-e": {"cem-e", "ceme", "cem e"},
    "mini cem-e": {"mini cem-e", "mini ceme", "mini cem e"},
}

def normalize_text(s: str) -> str:
    s = s.lower()
    s = s.replace("à","a").replace("è","e").replace("é","e").replace("ì","i").replace("ò","o").replace("ù","u")
    s = re.sub(r"\s+", " ", s)
    s = s.strip()
    return s

def tokenize(s: str) -> List[str]:
    s = normalize_text(s)
    tokens = re.findall(r"[a-z0-9\-]+", s)
    return [t for t in tokens if t not in STOPWORDS_MIN]

def expand_synonyms(query: str) -> str:
    qn = normalize_text(query)
    bag = set(tokenize(qn))
    for base, group in SYNONYMS.items():
        if bag & group:
            bag |= group
    return " ".join(sorted(bag))

def find_txt_files(doc_dir: str) -> List[str]:
    paths = []
    for root, _, files in os.walk(doc_dir):
        for f in files:
            if f.lower().endswith(".txt"):
                paths.append(os.path.join(root, f))
    return sorted(paths)

def split_blocks(text: str) -> List[str]:
    # blocchi separati da linee di trattini o righe vuote multiple
    parts = re.split(r"\n[-=]{5,}\n|\n{2,}", text.strip(), flags=re.MULTILINE)
    return [p.strip() for p in parts if p.strip()]

def parse_tags(block: str) -> List[str]:
    m = re.search(r"^\s*\[TAGS:\s*(.*?)\]\s*$", block, flags=re.IGNORECASE|re.MULTILINE)
    if not m:
        return []
    raw = m.group(1)
    tags = [normalize_text(x).strip() for x in re.split(r"[;,]", raw) if x.strip()]
    return tags

def score_keyword(query: str, text: str, tags: List[str], filename: str) -> float:
    q = normalize_text(query)
    qexp = expand_synonyms(q)

    # match keyword + sinonimi
    toks_q = set(tokenize(qexp))
    toks_t = set(tokenize(text))
    inter = toks_q & toks_t
    score_kw = len(inter)

    # boost per TAG
    tag_set = set(tags)
    if tag_set & toks_q:
        score_kw += 3.0

    # boost per nome file molto rilevante (es. p560.txt)
    basename = os.path.basename(filename).lower()
    for tok in toks_q:
        if tok and tok in basename:
            score_kw += 2.0

    return score_kw

def search_best_answer(query: str, threshold: float = SIMILARITY_THRESHOLD, topk: int = TOPK_SEMANTIC) -> Dict[str, Any]:
    """
    Solo keyword/BM25-like (senza dipendenze pesanti), con boost TAG/file.
    Restituisce il miglior blocco.
    """
    if not INDEX:
        return {"answer": "Indice vuoto.", "found": False, "from": None}

    best = None
    for doc in INDEX:
        score = score_keyword(query, doc["text"], doc["tags"], doc["path"])
        if best is None or score > best["score"]:
            best = {"score": score, "doc": doc}

    if not best or best["score"] <= 0:
        return {
            "answer": "Non ho trovato una risposta precisa. Prova a riformulare leggermente la domanda.",
            "found": False,
            "from": None
        }

    d = best["doc"]
    # post-process: se il blocco contiene righe D:/R:, scegli R: più pertinente
    ans = extract_best_qa_answer(d["text"], query) or d["text"]

    return {
        "answer": ans.strip(),
        "found": True,
        "score": round(best["score"], 3),
        "from": os.path.basename(d["path"]),
        "tags": d["tags"] or []
    }

def extract_best_qa_answer(block_text: str, query: str) -> str:
    """
    Se nel blocco ci sono più Q/A, prova a scegliere la R: col titolo D: più vicino alla query.
    Formati attesi:
      D: ... \n R: ...
    """
    qn = normalize_text(query)
    pairs = []
    # cattura segmenti D:/R:
    pattern = re.compile(r"(D:\s*(?P<d>.+?)\s*\n+R:\s*(?P<r>.+?))(?=\n{2,}|$)", re.IGNORECASE|re.DOTALL)
    for m in pattern.finditer(block_text):
        d = m.group("d").strip()
        r = m.group("r").strip()
        pairs.append((d, r))

    if not pairs:
        return None

    # punteggio semplice: overlap token tra query e D:
    best = None
    qtok = set(tokenize(qn))
    for d, r in pairs:
        dtok = set(tokenize(d))
        inter = len(qtok & dtok)
        # bonus se il titolo D: inizia con stessa parola chiave (p560, ctf, ecc.)
        bonus = 1 if any(t in dtok for t in qtok) else 0
        s = inter + bonus
        if best is None or s > best[0]:
            best = (s, r)

    return best[1] if best and best[0] > 0 else None

def build_index(doc_dir: str = DOC_DIR) -> None:
    """
    Costruzione SINCRONA. Nessun thread. Popola INDEX una volta sola.
    """
    global INDEX, BUILDING
    if BUILDING:
        return
    BUILDING = True
    try:
        docs: List[Dict[str, Any]] = []
        paths = find_txt_files(doc_dir)
        for p in paths:
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    raw = f.read()
                blocks = split_blocks(raw)
                for b in blocks:
                    if not b or len(b) < 10:
                        continue
                    tags = parse_tags(b)
                    docs.append({
                        "path": p,
                        "text": b,
                        "tags": tags,
                    })
            except Exception:
                # se un file è malformato, proseguiamo
                continue
        INDEX = docs
        print(f"[scraper_tecnaria] Indicizzati {len(INDEX)} blocchi da {len(paths)} file.")
    finally:
        BUILDING = False

def is_ready() -> bool:
    return isinstance(INDEX, list) and len(INDEX) > 0

def docs_count() -> int:
    return len(INDEX)

# build eager all'import
try:
    build_index(DOC_DIR)
except Exception as e:
    print(f"[scraper_tecnaria] build_index all'import fallita: {e}")
