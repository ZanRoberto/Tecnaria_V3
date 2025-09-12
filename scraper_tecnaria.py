# -*- coding: utf-8 -*-
import os, re, glob, threading

# ===== Stato indice + contatori =====
INDEX = []            # lista di blocchi (dict)
READY = False
LOCK = threading.Lock()
COUNT_BLOCKS = 0
COUNT_FILES = 0
COUNT_LINES = 0

DOC_EXTS = (".txt", ".TXT")

# ===== Stopwords minime (IT) =====
STOPWORDS_MIN = {
    "il","lo","la","i","gli","le","un","uno","una",
    "di","del","della","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da",
    "al","allo","ai","agli","alla","alle",
    "che","come","dove","quando","anche",
    "mi","ti","si","ci","vi","a","da","de","dal","dall","dalla","dalle",
}

# ===== Sinonimi soft (non aggressivi) =====
SYNONYMS = {
    # manteniamo sinonimi SOLO quando è già presente il token chiave,
    # così non “sporchiamo” le query generiche.
    "p560": {"p560","p-560","p 560","spit p560","pistola p560","sparachiodi p560"},
    "tecnaria": {"tecnaria","azienda tecnaria"},
    "contatti": {"contatto","telefono","mail","email","referenti","ufficio"},
    "prezzi": {"costo","preventivo","listino","prezzo"},
}

# ===== Hint per instradare “azienda” vs “prodotto” =====
COMPANY_HINTS = ("chisiamo","profilo","vision","mission","valori","azienda","storia")
COMPANY_FILE_HINTS = ("chisiamo_", "chisiamo", "profiloaziendale", "visionmission",
                      "contattiorari", "certificazioni")

PRODUCT_FILE_HINTS = (
    "ctf","hbv","cem-e","mini_cem-e","ctl","cls","clsr",
    "fva","x-hbv","diapason","p560"
)

# ===== Entità “codice prodotto/file” → instradamento forte =====
# Chiave = “slug” file (basenome senza estensione), Valori = varianti ammessi
ENTITY_MAP = {
    "p560": {"p560","p-560","p 560"},
    "diapason": {"diapason"},
    "ctf": {"ctf"},
    "hbv": {"hbv"},
    "cem-e": {"cem-e","cem e"},
    "mini_cem-e": {"mini_cem-e","mini cem-e","mini cem e"},
    "ctl": {"ctl"},
    "cls": {"cls"},
    "clsr": {"clsr"},
    "fva": {"fva","fva-l","fva l"},
    "x-hbv": {"x-hbv","x hbv"},
}

# =====================================
# Normalizzazione & utilità
# =====================================

def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    s = (s.replace("è","e").replace("é","e")
           .replace("à","a").replace("ù","u")
           .replace("ò","o").replace("ì","i"))
    s = re.sub(r"[^a-z0-9\s\-\_/\.]", " ", s)
    tokens = [t for t in s.split() if t and t not in STOPWORDS_MIN]

    # aggiungo la “chiave” di sinonimo SOLO se ho effettivamente il sinonimo
    extra = []
    for t in tokens:
        for key, variants in SYNONYMS.items():
            if t in variants:
                extra.append(key)

    return " ".join(tokens + extra).strip()

def _base_slug(path: str) -> str:
    b = os.path.splitext(os.path.basename(path))[0].lower()
    return b

def _is_company_file(path: str) -> bool:
    b = os.path.basename(path).lower()
    return any(h in b for h in COMPANY_FILE_HINTS)

def _is_product_file(path: str) -> bool:
    b = os.path.basename(path).lower()
    return any(h in b for h in PRODUCT_FILE_HINTS)

def _detect_intent(q_norm: str) -> dict:
    is_company = any(h in q_norm for h in COMPANY_HINTS) or ("tecnaria" in q_norm and not any(p in q_norm for p in PRODUCT_FILE_HINTS))
    return {"company": is_company}

def _extract_entity_slug(q_norm: str) -> str | None:
    """
    Se nella domanda c'è un riferimento esplicito a un “codice prodotto”,
    ritorna lo slug del file corrispondente (es. 'p560' → P560.txt).
    Regola fondamentale: per P560 richiedo la presenza del token P560.
    """
    # match esplicito P560
    if "p560" in q_norm:
        return "p560"

    # altri (non forziamo mai senza citazione esplicita, per evitare falsi positivi)
    for slug, variants in ENTITY_MAP.items():
        for v in variants:
            if v in q_norm:
                return slug
    return None

# =====================================
# Lettura file & parsing
# =====================================

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

    # Blocchi in formato:
    #   D: ...
    #   R: ...
    parts = re.split(r"\n\s*D:\s*", raw, flags=re.IGNORECASE)
    for part in parts:
        if not part.strip():
            continue
        m2 = re.split(r"\n\s*R:\s*", part, maxsplit=1, flags=re.IGNORECASE)
        if len(m2) == 2:
            q = m2[0].strip()
            r = m2[1].strip()
            txt = f"{q}\n{r}"
            blocks.append({
                "path": path,
                "file": os.path.basename(path),
                "slug": _base_slug(path),
                "question": q,
                "answer": r,
                "text": txt,
                "tags": tags,
                "norm": normalize_text(q + " " + r + " " + tags + " " + os.path.basename(path)),
            })
    return blocks, _count_lines(raw)

# =====================================
# Costruzione indice (atomica)
# =====================================

def build_index(doc_dir: str) -> int:
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

# =====================================
# Ranking (keyword + boost) + ROUTING per entità
# =====================================

def _score_keyword(qn: str, en: str) -> float:
    qs = set(qn.split())
    es = set(en.split())
    if not qs or not es:
        return 0.0
    inter = len(qs & es)
    return inter / max(4, len(qs))

def _rank_candidates(qn: str, entries, topk: int = 20):
    cands = []
    for e in entries:
        s = _score_keyword(qn, e["norm"])
        cands.append({"entry": e, "score": s})
    cands.sort(key=lambda x: x["score"], reverse=True)
    return cands[:topk]

def search_best_answer(question: str, threshold: float = 0.30, topk: int = 20) -> dict:
    if not is_ready():
        return {"found": False, "answer": "Indice non pronto. Riprova tra qualche secondo.", "from": None}

    qn = normalize_text(question)
    intents = _detect_intent(qn)
    entity = _extract_entity_slug(qn)  # <-- qui capiamo se c'è “p560”, ecc.

    # 1) ROUTING PER ENTITÀ (es. P560) → filtra PRIMA i blocchi del file giusto
    entries = INDEX
    if entity:
        preferred = [e for e in INDEX if e.get("slug") == entity]
        if preferred:
            entries = preferred  # ranking solo nel file giusto
        # se non ci sono blocchi per quel file, lascio entries = INDEX (fallback)

    # 2) Ranking keyword
    cands = _rank_candidates(qn, entries, topk=topk)

    # 3) Re-ranking con boost/punizioni
    for c in cands:
        e = c["entry"]
        s = c["score"]
        base = e["slug"]
        tags = (e.get("tags") or "").lower()

        # boost fortissimo se il nome file coincide con l'entità (es. P560.txt)
        if entity and base == entity:
            s += 2.0
        # boost se i TAG contengono l’entità (debole rispetto al filename)
        if entity and entity in tags:
            s += 0.4

        # intent azienda: favorisci “chi siamo”/profilo; penalizza file di prodotto
        if intents["company"]:
            if _is_company_file(e["path"]) or "chi siamo" in tags or "azienda" in tags:
                s += 0.8
            if _is_product_file(e["path"]):
                s -= 0.3

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
        "from": e.get("file") or os.path.basename(e.get("path","")),
        "score": round(best["score"], 3),
        "tags": e.get("tags","")
    }
