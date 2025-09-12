# scraper_tecnaria.py
# Ricerca ibrida per documenti Tecnaria (.txt) dentro documenti_gTab/
# - Normalizzazione IT + sinonimi (interni + opzionali da synonyms.json)
# - Retrieval ibrido: semantico (se disponibile) + keyword (BM25-lite)
# - Boost per TAG e nome file
# - API: build_index(), rebuild_index(), search_best_answer(q)

import os, re, json, math, unicodedata, glob, time
from collections import defaultdict, Counter

# =======================
# Config / Env
# =======================
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab").strip()
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))  # soglia base
TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))
DEBUG = os.environ.get("DEBUG", "0") == "1"
DEBUG_SCRAPER = os.environ.get("DEBUG_SCRAPER", "0") == "1"

# Boost
BOOST_TAG = float(os.environ.get("BOOST_TAG", "0.25"))
BOOST_FILENAME = float(os.environ.get("BOOST_FILENAME", "0.15"))
BOOST_EXACT_CODE = float(os.environ.get("BOOST_EXACT_CODE", "0.20"))  # e.g. "P560" nel testo

# Embeddings (opzionali)
EMBED_MODEL_NAME = os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2")

# Stato globale in memoria (semplice)
INDEX = {
    "blocks": [],         # list of dict: {id, file, text, tags, norm, tokens, ln_len}
    "by_file": defaultdict(list),  # file -> [block_ids]
    "inv": defaultdict(set),       # token -> set(block_id)
    "idf": {},             # token -> idf
    "emb": None,           # np matrix (opzionale)
    "emb_model": None,     # modello (opzionale)
    "token_counts": {},    # block_id -> Counter(token)
    "N": 0,                # numero blocchi
    "files_indexed": 0,
    "lines_total": 0
}

# =======================
# Stopwords & Sinonimi
# =======================
STOPWORDS = {
    "a","ad","al","allo","ai","agli","alla","alle",
    "anche","come","con","da","dal","dallo","dai","dagli","dalla","dalle",
    "di","del","dello","dei","degli","della","delle",
    "e","ed","ma","o","oppure",
    "il","lo","la","i","gli","le","un","uno","una","mi","ti","si","ci","vi",
    "che","chi","cui","quale","quali","dove","quando","quanto","quanti","quanta",
    "per","su","tra","fra","in",
}

# sinonimi base (puoi ampliare liberamente)
BUILTIN_SYNONYMS = {
    "p560": ["p560", "spit p560", "pistola p560", "sparachiodi", "pistola spit"],
    "hbv": ["hbv","x-hbv","xhbv"],
    "connettore": ["connettore","piolo","stud","connettori"],
    "diapason": ["diapason","ctfs","ctfd","connettore diapason"],
    "cem-e": ["cem-e","ceme","ct cem-e"],
    "mini": ["mini","mini cem-e"],
    "ctf": ["ctf","piolo", "stud"],
    "ctl": ["ctl","legno-calcestruzzo","omega ctl","ct-l","ct l"],
    "contatti": ["contatti","telefono","mail","email","sede","indirizzo","orari"],
}

def load_external_synonyms(path="synonyms.json"):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {k.lower(): [w.lower() for w in v] for k, v in data.items()}
    except Exception as e:
        if DEBUG_SCRAPER:
            print(f"[scraper_tecnaria][WARN] synonyms.json error: {e}")
    return {}

SYNONYMS = BUILTIN_SYNONYMS.copy()
SYNONYMS.update(load_external_synonyms())

# =======================
# Normalizzazione
# =======================
def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def normalize_text(s: str) -> str:
    s = s.lower()
    s = strip_accents(s)
    # mantieni lettere/numeri e sigle tipo p560, ct-f ecc.
    s = re.sub(r"[^a-z0-9áéíóúàèìòùç/\-\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def tokenize(s: str):
    s = normalize_text(s)
    toks = [t for t in s.split() if t not in STOPWORDS]
    return toks

def expand_synonyms(toks):
    # per ogni token, aggiungi sinonimi "canonici"
    expanded = set(toks)
    txt = " ".join(toks)
    for key, syns in SYNONYMS.items():
        for s in syns:
            if s in txt:
                expanded.add(key)
                for s2 in syns:
                    expanded.add(s2)
    return list(expanded)

# =======================
# Parser TXT (blocchi + TAGS)
# =======================
TAG_LINE = re.compile(r"^\s*\[TAGS\s*:\s*(.*?)\]\s*$", re.IGNORECASE)

def read_txt_file(path):
    blocks = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    # separatori blocchi: linee di soli simboli "─" o "=", oppure righe vuote multiple
    raw_blocks = re.split(r"\n\s*[─=\-]{5,}\s*\n", content)
    if len(raw_blocks) == 1:
        # fallback: split per doppie righe vuote
        raw_blocks = re.split(r"\n\s*\n", content)

    # estrai TAGS in testa a ciascun blocco se presenti
    for raw in raw_blocks:
        raw = raw.strip()
        if not raw:
            continue

        tags = []
        lines = raw.splitlines()
        if lines:
            m = TAG_LINE.match(lines[0])
            if m:
                tag_str = m.group(1)
                tags = [t.strip().lower() for t in tag_str.split(",") if t.strip()]

        blocks.append({"text": raw, "tags": tags})
    return blocks

# =======================
# Build Index (keyword + opzionale embeddings)
# =======================
def try_load_embeddings():
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
        model = SentenceTransformer(EMBED_MODEL_NAME)
        return model, np
    except Exception as e:
        if DEBUG_SCRAPER:
            print(f"[scraper_tecnaria][INFO] Embedding model NOT available ({e}). Using keyword-only.")
        return None, None

def build_index():
    global INDEX
    t0 = time.time()

    blocks = []
    by_file = defaultdict(list)
    inv = defaultdict(set)
    token_counts = {}
    lines_total = 0

    # raccogli tutti i .txt
    txt_files = []
    for root, _, files in os.walk(DOC_DIR):
        for fn in files:
            if fn.lower().endswith(".txt"):
                txt_files.append(os.path.join(root, fn))

    if DEBUG_SCRAPER:
        print(f"[scraper_tecnaria] Inizio indicizzazione da: {os.path.abspath(DOC_DIR)}\n")
        if txt_files:
            print(f"[scraper_tecnaria] Trovati {len(txt_files)} file .txt:")
            for p in txt_files:
                print(f"  - {p}")
        else:
            print("[scraper_tecnaria] Trovati 0 file .txt:")

    bid = 0
    for path in sorted(txt_files):
        try:
            file_blocks = read_txt_file(path)
        except Exception as e:
            if DEBUG_SCRAPER:
                print(f"[scraper_tecnaria][WARN] Impossibile leggere {path}: {e}")
            continue

        for b in file_blocks:
            text = b["text"]
            tags = b.get("tags", [])
            norm = normalize_text(text)
            toks = [t for t in norm.split() if t not in STOPWORDS]
            toks = expand_synonyms(toks)
            if not toks:
                continue

            block = {
                "id": bid,
                "file": path,
                "file_base": os.path.basename(path).lower(),
                "text": text.strip(),
                "tags": tags,
                "norm": norm,
                "tokens": toks,
                "ln_len": math.log(1 + len(toks)),
            }
            blocks.append(block)
            by_file[path].append(bid)
            token_counts[bid] = Counter(toks)
            for t in set(toks):
                inv[t].add(bid)

            # stima righe per debug
            lines_total += text.count("\n") + 1
            bid += 1

    N = len(blocks)
    # IDF semplice
    idf = {}
    for t, postings in inv.items():
        df = len(postings)
        idf[t] = math.log((N + 1) / (df + 1)) + 1.0

    INDEX["blocks"] = blocks
    INDEX["by_file"] = by_file
    INDEX["inv"] = inv
    INDEX["idf"] = idf
    INDEX["token_counts"] = token_counts
    INDEX["N"] = N
    INDEX["files_indexed"] = len(txt_files)
    INDEX["lines_total"] = lines_total

    # Embeddings (se disponibili)
    model, np = try_load_embeddings()
    if model and N > 0:
        try:
            corpus = [b["text"] for b in blocks]
            emb = model.encode(corpus, normalize_embeddings=True, show_progress_bar=False)
            INDEX["emb"] = emb
            INDEX["emb_model"] = model
            if DEBUG_SCRAPER:
                print(f"[scraper_tecnaria] Embeddings pronti: shape={emb.shape}")
        except Exception as e:
            if DEBUG_SCRAPER:
                print(f"[scraper_tecnaria][WARN] Embedding encode fallita: {e}")
            INDEX["emb"] = None
            INDEX["emb_model"] = None
    else:
        INDEX["emb"] = None
        INDEX["emb_model"] = None

    if DEBUG_SCRAPER:
        print(f"[scraper_tecnaria] Indicizzati {len(blocks)} blocchi / {lines_total} righe da {len(txt_files)} file.")
        print(f"[scraper_tecnaria] Tempo build: {time.time()-t0:.2f}s")

    return {
        "status": "ok",
        "blocks": N,
        "docs": INDEX["files_indexed"],
        "lines": lines_total,
        "ts": time.time()
    }

def rebuild_index():
    return build_index()

# =======================
# Scoring
# =======================
def score_keyword(query_tokens, block_id):
    """
    BM25-lite molto semplice: somma(tf * idf) normalizzata per lunghezza.
    """
    tc = INDEX["token_counts"].get(block_id, {})
    if not tc:
        return 0.0
    s = 0.0
    for t in set(query_tokens):
        if t in tc:
            tf = tc[t]
            idf = INDEX["idf"].get(t, 1.0)
            s += (tf * idf)
    ln = INDEX["blocks"][block_id]["ln_len"] or 1.0
    return s / ln

def score_semantic(query_text):
    # ritorna lista [(block_id, score)]
    model = INDEX.get("emb_model")
    emb = INDEX.get("emb")
    if not model or emb is None:
        return []

    try:
        import numpy as np
        q_emb = model.encode([query_text], normalize_embeddings=True, show_progress_bar=False)[0]
        sims = (emb @ q_emb)  # cosine sim grazie a normalize_embeddings=True
        # prendi TopK indici
        top_idx = sims.argsort()[-TOPK:][::-1]
        return [(int(i), float(sims[i])) for i in top_idx]
    except Exception as e:
        if DEBUG_SCRAPER:
            print(f"[scraper_tecnaria][WARN] score_semantic errore: {e}")
        return []

def apply_boosts(base_score, q_tokens, block):
    score = base_score
    # Boost se il nome file contiene un token chiave (es. p560)
    for t in q_tokens:
        if t and t in block["file_base"]:
            score += BOOST_FILENAME
            break
    # Boost se i TAG del blocco contengono token della query
    tags_txt = " ".join(block.get("tags", [])).lower()
    for t in q_tokens:
        if t and t in tags_txt:
            score += BOOST_TAG
            break
    # Boost se la query contiene "codice esatto" presente nel testo raw
    raw = block["text"].lower()
    for t in q_tokens:
        if t and re.search(rf"\b{re.escape(t)}\b", raw):
            score += BOOST_EXACT_CODE * 0.25  # piccolo premio diffuso
    return score

# =======================
# Ricerca Principale
# =======================
def search_best_answer(question: str):
    """
    Ritorna dict:
    {
      "answer": str,
      "found": bool,
      "score": float,
      "file": "path",
      "block_id": int,
      "tags": [..],
      "question_norm": str
    }
    """
    if not question or not INDEX["blocks"]:
        return {
            "answer": "",
            "found": False,
            "score": 0.0,
            "file": None,
            "block_id": None,
            "tags": [],
            "question_norm": ""
        }

    q_norm = normalize_text(question)
    q_toks = expand_synonyms([t for t in q_norm.split() if t not in STOPWORDS])
    if DEBUG_SCRAPER:
        print(f"[scraper_tecnaria][SEARCH] q='{question}' norm='{q_norm}' toks={q_toks}")

    # 1) SEMANTIC (se disponibile)
    sem_hits = score_semantic(question)  # [(block_id, sem_score)]

    # 2) KEYWORD
    kw_candidates = set()
    for t in q_toks:
        for bid in INDEX["inv"].get(t, []):
            kw_candidates.add(bid)
    # se niente trovato via inv, apri a tutti (fallback)
    if not kw_candidates:
        kw_candidates = set(range(INDEX["N"]))

    kw_scored = []
    for bid in kw_candidates:
        kw_scored.append((bid, score_keyword(q_toks, bid)))

    # 3) Merge & Boost
    base_scores = defaultdict(float)
    # semantici
    for bid, s in sem_hits:
        base_scores[bid] = max(base_scores[bid], s)
    # keywords
    for bid, s in kw_scored:
        base_scores[bid] = max(base_scores[bid], s)

    # applica boost per tag/file
    final_scores = []
    for bid, bscore in base_scores.items():
        blk = INDEX["blocks"][bid]
        fscore = apply_boosts(bscore, q_toks, blk)
        final_scores.append((bid, fscore))

    # ordina per score desc
    final_scores.sort(key=lambda x: x[1], reverse=True)
    if not final_scores:
        return {
            "answer": "",
            "found": False,
            "score": 0.0,
            "file": None,
            "block_id": None,
            "tags": [],
            "question_norm": q_norm
        }

    best_id, best_score = final_scores[0]
    best_blk = INDEX["blocks"][best_id]

    # Soglia decisione (elastica: se matcha filename/tag “forte”, accetta anche sotto base threshold)
    accept = (best_score >= SIM_THRESHOLD) or any(
        t in best_blk["file_base"] for t in q_toks
    ) or any(t in " ".join(best_blk.get("tags", [])).lower() for t in q_toks)

    if DEBUG_SCRAPER:
        print(f"[scraper_tecnaria][RESULT] best_score={best_score:.3f} | file={best_blk['file']} | id={best_id} | accept={accept}")

    if not accept:
        return {
            "answer": "",
            "found": False,
            "score": float(best_score),
            "file": best_blk["file"],
            "block_id": int(best_id),
            "tags": best_blk.get("tags", []),
            "question_norm": q_norm
        }

    # Pulisci eventuale riga TAGS in testa al blocco
    ans = best_blk["text"]
    ans = re.sub(r"^\s*\[TAGS\s*:\s*.*?\]\s*\n", "", ans, flags=re.IGNORECASE)

    return {
        "answer": ans.strip(),
        "found": True,
        "score": float(best_score),
        "file": best_blk["file"],
        "block_id": int(best_id),
        "tags": best_blk.get("tags", []),
        "question_norm": q_norm
    }

# =======================
# Auto-build all'avvio (opzionale)
# =======================
if __name__ == "__main__":
    # Avvio manuale per debug locale
    res = build_index()
    print(json.dumps(res, ensure_ascii=False, indent=2))
    while True:
        try:
            q = input("\nQ> ").strip()
            if not q:
                continue
            r = search_best_answer(q)
            print(json.dumps(r, ensure_ascii=False, indent=2))
        except KeyboardInterrupt:
            break
