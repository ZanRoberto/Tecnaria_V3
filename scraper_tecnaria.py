# -*- coding: utf-8 -*-
import os
import re
import glob
import json
import math
import logging
import unicodedata
from threading import Lock

# ============ LOG ============
LOG = logging.getLogger("scraper_tecnaria")
if os.environ.get("DEBUG_SCRAPER", "0") == "1":
    LOG.setLevel(logging.DEBUG)
else:
    LOG.setLevel(logging.INFO)

# ============ CONFIG ============
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")
EMBED_MODEL_NAME = os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2")

# Soglie di default (lato app puoi sovrascriverle via env)
DEFAULT_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
DEFAULT_TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))

# Stopwords minimali IT (non aggressivo: lasciamo termini tecnici)
STOPWORDS_MIN = {
    "il","lo","la","i","gli","le","un","uno","una",
    "di","del","della","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da",
    "al","allo","ai","agli","alla","alle",
    "che","come","dove","quando","anche",
    "mi","ti","si","ci","vi","a","de","dal","dall","dalla","dalle",
    "dei","degli","all","dell","nel","nella","nelle","negli","nei"
}

# Sinonimi/alias semplici per aumentare recall
SYNONYMS = {
    r"\bp560\b": ["p-560", "spit p560", "pistola p560", "sparachiodi", "sparachiodi p560"],
    r"\bctf\b": ["connettore ctf", "piolo ctf", "pioli ctf"],
    r"\bcem[- ]e\b": ["cem e", "connettore cem-e", "ripresa di getto", "riprese di getto"],
    r"\bmini[- ]cem[- ]e\b": ["mini cem e", "mini-cem-e"],
    r"\bctl\b": ["connettori ctl", "legno calcestruzzo", "legno-calcestruzzo"],
    r"\bhbv\b": ["chiodatrice hbv", "sistema hbv"],
    r"\bdiapason\b": ["connettore diapason"],
    r"\bcls\b": ["soletta cls"],
    r"\bclsr\b": ["soletta clsr"],
    r"\bfva\b": ["fva tecnaria"],
    r"\bfva[- ]l\b": ["fva l", "fva-l tecnaria"],
    r"\bct[- ]l\b": ["ct l", "ct-l tecnaria"],
    r"\bx[- ]hbv\b": ["x hbv", "x-hbv tecnaria"],
}

# ============ STATO GLOBALE ============
INDEX = []           # lista di blocchi indicizzati
_EMBEDDER = None     # modello sentence-transformers o None
_INDEX_LOCK = Lock() # evita race in build_index

# ============ UTILITY ============

def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )

def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    s = strip_accents(s)
    s = re.sub(r"[^\w\s]", " ", s)    # rimuove punteggiatura
    tokens = [t for t in s.split() if t and t not in STOPWORDS_MIN]
    return " ".join(tokens)

def expand_with_synonyms(q_norm: str) -> str:
    text = q_norm
    for pat, alts in SYNONYMS.items():
        if re.search(pat, text):
            text += " " + " ".join(alts)
    return text

def cosine_sim(u, v):
    # u, v: liste/tuple di float
    if not u or not v or len(u) != len(v):
        return 0.0
    num = sum(a*b for a, b in zip(u, v))
    den = math.sqrt(sum(a*a for a in u)) * math.sqrt(sum(b*b for b in v))
    if den == 0:
        return 0.0
    return num / den

def safe_read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        LOG.warning("[read] Impossibile leggere %s: %s", path, e)
        return ""

def parse_blocks_from_text(text: str):
    """
    Ritorna una lista di blocchi (dict).
    Regole:
      - TAGS opzionali in testa: [TAGS: a,b,c]
      - separator '────────────────────────────' divide blocchi
      - pattern Domanda/Risposta (D: ...  / R: ...) mantenuto nel blocco
    """
    tags = []
    lines = text.strip().splitlines()
    # Leggi TAGS dalla prima riga se presente
    if lines and lines[0].strip().lower().startswith("[tags:"):
        tag_line = lines[0]
        m = re.search(r"\[tags:(.*?)\]", tag_line, flags=re.I)
        if m:
            tags = [t.strip() for t in m.group(1).split(",") if t.strip()]
        # rimuovi la riga TAGS per il resto del parsing
        text = "\n".join(lines[1:])

    # split per separatore grafico
    parts = re.split(r"\n?[\u2500\-─]{10,,}\n?", text)  # linea lunga di trattini/box
    # Se split non ha separato nulla, usa il testo intero come unico blocco
    if len(parts) <= 1:
        parts = [text]

    blocks = []
    for p in parts:
        content = p.strip()
        if not content:
            continue
        blocks.append({
            "text": content,
            "tags": tags[:]  # copia
        })
    return blocks

def try_load_embedder():
    global _EMBEDDER
    if _EMBEDDER is not None:
        return _EMBEDDER
    try:
        from sentence_transformers import SentenceTransformer
        _EMBEDDER = SentenceTransformer(EMBED_MODEL_NAME)
        LOG.info("[embed] Modello caricato: %s", EMBED_MODEL_NAME)
    except Exception as e:
        _EMBEDDER = None
        LOG.warning("[embed] Impossibile usare embedding (%s). Uso solo keyword.", e)
    return _EMBEDDER

def embed_texts(texts):
    mdl = try_load_embedder()
    if mdl is None:
        return None
    try:
        # ritorna lista di liste (float)
        embs = mdl.encode(texts, show_progress_bar=False, normalize_embeddings=True)
        return [list(vec) for vec in embs]
    except Exception as e:
        LOG.warning("[embed] encode fallita: %s. Disabilito embedding.", e)
        return None

# ============ BUILD INDEX ============

def build_index(doc_dir: str = None) -> int:
    """Scansiona DOC_DIR e costruisce INDEX in-place. Ritorna numero blocchi indicizzati."""
    global INDEX
    with _INDEX_LOCK:
        base = os.path.abspath(doc_dir or DOC_DIR)
        if not os.path.isdir(base):
            LOG.warning("[index] Cartella non trovata: %s", base)
            INDEX = []
            return 0

        # Trova .txt / .TXT
        paths = sorted(
            list(glob.glob(os.path.join(base, "*.txt"))) +
            list(glob.glob(os.path.join(base, "*.TXT")))
        )

        LOG.info("[scraper_tecnaria] Inizio indicizzazione da: %s", base)
        LOG.info("[scraper_tecnaria] Trovati %d file .txt:", len(paths))
        for p in paths:
            LOG.info("[scraper_tecnaria]   - %s", p)

        new_index = []
        for path in paths:
            raw = safe_read_text(path)
            if not raw:
                continue
            blocks = parse_blocks_from_text(raw)
            filename = os.path.basename(path)
            fname_norm = normalize_text(os.path.splitext(filename)[0])

            # Prepara embedding (se disponibili) per testo dei blocchi
            block_texts = [b["text"] for b in blocks]
            # Normalizzati per matching keyword
            blocks_norm = [normalize_text(t) for t in block_texts]
            # Embedding (opzionale)
            block_embs = embed_texts(block_texts)

            for i, b in enumerate(blocks):
                rec = {
                    "path": path,
                    "file": filename,
                    "line": i + 1,
                    "text": b["text"],
                    "norm": blocks_norm[i],
                    "tags": b.get("tags", []),
                    "tags_norm": normalize_text(" ".join(b.get("tags", []))) if b.get("tags") else "",
                    "file_norm": fname_norm,
                    "emb": block_embs[i] if block_embs is not None else None,
                }
                new_index.append(rec)

        INDEX[:] = new_index  # in-place per chi importa il modulo
        LOG.info("[scraper_tecnaria] Indicizzati %d blocchi da %d file.", len(INDEX), len(paths))
        return len(INDEX)

# ============ SEARCH ============

def keyword_score(q_norm: str, rec) -> float:
    """
    Semplice overlap-based scoring con boost su TAG e file name.
    """
    if not q_norm:
        return 0.0
    q_tokens = set(q_norm.split())
    score = 0.0

    # Overlap nel testo normalizzato
    text_tokens = set(rec["norm"].split())
    overlap = q_tokens & text_tokens
    score += 0.5 * (len(overlap) / (len(q_tokens) + 1e-6))  # normalizzato

    # Boost su TAG
    if rec["tags_norm"]:
        tag_tokens = set(rec["tags_norm"].split())
        if q_tokens & tag_tokens:
            score += 0.25

    # Boost su nome file (es. "p560" nella domanda + file P560.txt)
    file_tokens = set(rec["file_norm"].split())
    if q_tokens & file_tokens:
        score += 0.25

    # Boost specifico se la domanda contiene key di famiglia
    q = " " + q_norm + " "
    specials = [
        (" p560 ", 0.25),
        (" ctf ", 0.20),
        (" diapason ", 0.20),
        (" cem e ", 0.20),
        (" mini cem e ", 0.20),
        (" ctl ", 0.20),
        (" hbv ", 0.20),
        (" cls ", 0.15),
        (" clsr ", 0.15),
        (" fva ", 0.15),
        (" fva l ", 0.15),
        (" ct l ", 0.15),
        (" x hbv ", 0.15),
        (" x hbv ", 0.15),
    ]
    for needle, add in specials:
        if needle in q and needle.strip() in rec["file_norm"]:
            score += add

    return min(score, 1.0)

def semantic_score(q_text: str, rec) -> float:
    """
    Cosine similarity tra embedding, se disponibili.
    """
    if _EMBEDDER is None or rec.get("emb") is None:
        return 0.0
    # embed query on the fly (cached model)
    embs = embed_texts([q_text])
    if not embs:
        return 0.0
    return max(0.0, min(1.0, float(cosine_sim(embs[0], rec["emb"]))))

def combine_scores(kw: float, sem: float) -> float:
    """
    Fusione semplice: se ho embedding, 0.6*sem + 0.4*kw, altrimenti solo kw.
    """
    if _EMBEDDER is None:
        return kw
    return 0.6 * sem + 0.4 * kw

def _extract_answer_text(rec_text: str) -> str:
    """
    Se il blocco è in formato Q/A con D:/R:, ritorna solo la parte di risposta,
    altrimenti ritorna l'intero blocco.
    """
    # Cerca pattern "R:" (prima risposta), eventualmente dopo una "D:"
    # Se non presente, restituisci intero
    m = re.search(r"(^|\n)\s*R:\s*(.*)", rec_text, flags=re.I | re.S)
    if m:
        # Prendi fino alla prossima D: o fino alla fine
        ans = m.group(2).strip()
        # taglia su una nuova "D:" successiva se c'è
        cut = re.split(r"\n\s*D:\s*", ans, maxsplit=1, flags=re.I)
        return cut[0].strip()
    return rec_text.strip()

def _first_block_of_file(fname_key: str):
    """Ritorna il primo record dell'indice appartenente al file la cui base contiene fname_key (normalizzato)."""
    key = normalize_text(fname_key)
    for rec in INDEX:
        if key in rec["file_norm"]:
            return rec
    return None

def search_best_answer(question: str, threshold: float = None, topk: int = None):
    """
    Ricerca ibrida: keyword + (opz.) embedding + boost TAG/file.
    Ritorna sempre un dict con chiavi: answer, found, score, path, line, from.
    """
    if not INDEX:
        return {"answer": "Indice vuoto. Caricare i .txt e ricostruire l’indice.", "found": False, "from": None}

    thr = DEFAULT_THRESHOLD if threshold is None else float(threshold)
    k = DEFAULT_TOPK if topk is None else int(topk)

    q_norm = normalize_text(question)
    q_expanded = expand_with_synonyms(q_norm)

    scored = []
    for rec in INDEX:
        kw = keyword_score(q_expanded, rec)
        sem = semantic_score(question, rec)  # usa testo originale domanda
        score = combine_scores(kw, sem)
        scored.append((score, rec))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:max(3, k)]

    # trova il migliore sopra soglia
    best = next(((s, r) for (s, r) in top if s >= thr), None)

    # fallback1: se niente sopra soglia ma c'è un match forte per filename es. "p560"
    if best is None:
        for key in ["p560", "ctf", "cem e", "mini cem e", "ctl", "hbv", "diapason", "cls", "clsr", "fva", "fva l", "ct l", "x hbv"]:
            if f" {key} " in (" " + q_expanded + " "):
                cand = _first_block_of_file(key)
                if cand:
                    best = (0.29, cand)  # poco sotto soglia base, ma sufficiente a rispondere
                    break

    if best is None:
        # ultimo fallback: dai il best assoluto anche se sotto soglia (ma segna found=False)
        s, r = scored[0]
        return {
            "answer": "Non ho trovato una risposta precisa. Prova a riformulare leggermente la domanda.",
            "found": False,
            "score": round(float(s), 3),
            "path": r["path"],
            "line": r["line"],
            "from": os.path.basename(r["path"]),
        }

    s, r = best
    answer_txt = _extract_answer_text(r["text"])
    return {
        "answer": answer_txt,
        "found": True if s >= thr else False,  # se sotto soglia, segnala comunque found=False
        "score": round(float(s), 3),
        "path": r["path"],
        "line": r["line"],
        "from": os.path.basename(r["path"]),
        "tags": r["tags"]
    }

# ============ AUTORUN (opzionale) ============
if __name__ == "__main__":
    # Test locale rapido
    cnt = build_index(DOC_DIR)
    print(json.dumps({
        "built_blocks": cnt,
        "doc_dir": os.path.abspath(DOC_DIR)
    }, ensure_ascii=False, indent=2))

    # Prova domanda
    q = "mi parli della p560?"
    print(json.dumps(search_best_answer(q), ensure_ascii=False, indent=2))
