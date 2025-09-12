# -*- coding: utf-8 -*-
import os, re, glob, threading

# ---- Stato indice + contatori ----
INDEX = []   # lista di blocchi
READY = False
LOCK = threading.Lock()
COUNT_BLOCKS = 0
COUNT_FILES = 0
COUNT_LINES = 0

DOC_EXTS = (".txt", ".TXT")

# Stopwords minime italiane
STOPWORDS_MIN = {
    "il","lo","la","i","gli","le","un","uno","una",
    "di","del","della","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da",
    "al","allo","ai","agli","alla","alle",
    "che","come","dove","quando","anche",
    "mi","ti","si","ci","vi","a","da","de","dal","dall","dalla","dalle",
}

# Sinonimi/varianti minimi per robustezza
SYNONYMS = {
    "p560": {"p560","p 560","p-560","spit p560","pistola p560","sparachiodi p560"},
    "tecnaria": {"tecnaria","azienda tecnaria"},
    "contatti": {"contatto","telefono","telefono tecnaria","mail","email","referenti","ufficio"},
    "prezzi": {"costo","preventivo","listino","prezzo"},
}

COMPANY_HINTS = ("chisiamo", "profilo", "vision", "mission", "valori", "azienda", "storia")
COMPANY_FILE_HINTS = ("chisiamo_", "chisiamo", "profiloaziendale", "visionmission", "contattiorari", "certificazioni")
PRODUCT_FILE_HINTS = (
    "ctf","hbv","cem-e","mini_cem-e","ctl","cls","clsr","fva","x-hbv","diapason","p560"
)

# ---------------------------
# Normalizzazione + utilità
# ---------------------------

def normalize_text(s: str) -> str:
    s = s.lower()
    s = (s.replace("è","e").replace("é","e")
           .replace("à","a").replace("ù","u")
           .replace("ò","o").replace("ì","i"))
    s = re.sub(r"[^a-z0-9\s\-\_/\.]", " ", s)
    tokens = [t for t in s.split() if t and t not in STOPWORDS_MIN]
    extra = []
    for t in tokens:
        for key, variants in SYNONYMS.items():
            if t in variants:
                extra.append(key)
    return " ".join(tokens + extra).strip()

def _is_company_file(path: str) -> bool:
    b = os.path.basename(path).lower()
    return any(h in b for h in COMPANY_FILE_HINTS)

def _is_product_file(path: str) -> bool:
    b = os.path.basename(path).lower()
    return any(h in b for h in PRODUCT_FILE_HINTS)

def _detect_intent(qn: str) -> dict:
    is_company = any(h in qn for h in COMPANY_HINTS) or ("tecnaria" in qn and not any(p in qn for p in PRODUCT_FILE_HINTS))
    has_p560 = ("p560" in qn)
    return {"company": is_company, "p560": has_p560}

# ---------------------------
# Lettura file e parsing
# ---------------------------

def _iter_txt_files(doc_dir: str):
    for ext in DOC_EXTS:
        for p in glob.glob(os.path.join(doc_dir, f"*{ext}")):
            if os.path.isfile(p):
                yield p

def _count_lines(text: str) -> int:
    return text.count("\n") + 1 if text else 0

def _parse_file(path: str):
    blocks = []
    tags = ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
    except Exception:
        return blocks, 0

    # TAGS
    m = re.search(r"\[TAGS:\s*(.*?)\]", raw, flags=re.IGNORECASE | re.DOTALL)
    if m:
        tags = m.group(1).strip()

    # Blocchi QA
    parts = re.split(r"\n\s*D:\s*", raw, flags=re.IGNORECASE)
    for part in parts:
        if not part.strip():
            continue
        m = re.split(r"\n\s*R:\s*", part, maxsplit=1, flags=re.IGNORECASE)
        if len(m) == 2:
            q = m[0].strip()
            r = m[1].strip()
            txt = f"{q}\n{r}"
            blocks.append({
                "path": path,
                "question": q,
                "answer": r,
                "text": txt,
                "tags": tags,
                "norm": normalize_text(q + " " + r + " " + tags + " " + os.path.basename(path))
            })
    return blocks, _count_lines(raw)

# ---------------------------
# Costruzione indice
# ---------------------------

def build_index(doc_dir: str) -> int:
    """Costruisce l'indice in modo ATOMICO e aggiorna i contatori."""
    global INDEX, READY, COUNT_BLOCKS, COUNT_FILES, COUNT_LINES
    tmp = []
    files = list(_iter_txt_files(doc_dir))
    cnt_lines_total = 0

    for fp in files:
        blks, ln = _parse_file(fp)
        tmp.extend(blks)
        cnt_lines_total += ln

    with LOCK:
        INDEX = tmp
        COUNT_BLOCKS = len(tmp)
        COUNT_FILES = len(files)
        COUNT_LINES = cnt_lines_total
        READY = True

    print(f"[scraper_tecnaria] Indicizzati {COUNT_BLOCKS} blocchi / {COUNT_LINES} righe da {COUNT_FILES} file.")
    return COUNT_BLOCKS

def is_ready() -> bool:
    return READY and len(INDEX) > 0

def get_counters():
    return COUNT_BLOCKS, COUNT_FILES, COUNT_LINES

# ---------------------------
# Ranking (keyword + boost)
# ---------------------------

def _score_keyword(qn: str, en: str) -> float:
    qs = set(qn.split())
    es = set(en.split())
    if not qs or not es:
        return 0.0
    inter = len(qs & es)
    # normalizzazione semplice
    return inter / max(4, len(qs))

def _rank_candidates(qn: str, topk: int = 20):
    cands = []
    for e in INDEX:
        s = _score_keyword(qn, e["norm"])
        cands.append({"entry": e, "score": s})
    cands.sort(key=lambda x: x["score"], reverse=True)
    return cands[:topk]

def search_best_answer(question: str, threshold: float = 0.30, topk: int = 20) -> dict:
    if not is_ready():
        return {"found": False, "answer": "Indice non pronto. Riprova tra qualche secondo.", "from": None}

    qn = normalize_text(question)
    intents = _detect_intent(qn)

    cands = _rank_candidates(qn, topk=topk)

    # re-ranking con boost
    for c in cands:
        e = c["entry"]
        s = c["score"]
        p = e["path"].lower()
        tags = e.get("tags","").lower()

        # P560
        if intents["p560"]:
            if "p560" in tags or "p560" in os.path.basename(p):
                s += 0.9

        # Intent azienda: spingi chi siamo, penalizza prodotti
        if intents["company"]:
            if _is_company_file(p) or "chi siamo" in tags or "azienda" in tags:
                s += 0.7
            if _is_product_file(p):
                s -= 0.25

        c["score"] = s

    cands.sort(key=lambda x: x["score"], reverse=True)
    best = cands[0] if cands else None

    if not best or best["score"] < threshold:
        return {
            "found": False,
            "answer": "Non ho trovato una risposta precisa. Prova a riformulare leggermente la domanda.",
            "from": None
        }

    e = best["entry"]
    return {
        "found": True,
        "answer": e.get("answer") or e.get("text",""),
        "from": os.path.basename(e.get("path","")),
        "score": round(best["score"], 3),
        "tags": e.get("tags","")
    }
