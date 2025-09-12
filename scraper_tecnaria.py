# scraper_tecnaria.py
# -*- coding: utf-8 -*-
import os
import re
import math
import json
import unicodedata
from collections import Counter, defaultdict

# ============ CONFIG ============
DEFAULT_EMBED_MODEL = os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2")
MIN_CHARS_PER_BLOCK = int(os.environ.get("MIN_CHARS_PER_BLOCK", "60"))  # ignora pezzi minuscoli
VERBOSE = os.environ.get("DEBUG_SCRAPER", "1") == "1"

# Soglie di boost
BOOST_TAG = 0.12
BOOST_FILENAME = 0.08

# Stopwords minimali IT (non aggressive)
STOPWORDS_MIN = {
    "il","lo","la","i","gli","le","un","uno","una",
    "di","del","della","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da",
    "al","allo","ai","agli","alla","alle",
    "che","come","dove","quando","anche",
    "mi","ti","si","ci","vi","a","da","de","dal","dall","dalla","dalle",
}

# Sinonimi utili (chiave = lemma normalizzato)
SYNONYMS = {
    "p560": {"p560","p 560","p-560","pistola","sparachiodi","sparachiodi p560","spit p560","spit","pistola spit"},
    "contatti": {"contatti","telefono","tel","email","mail","e-mail","sede","ufficio","orari","orario","assistenza","supporto"},
    "chiodatrice": {"chiodatrice","hbv","pistola","sparachiodi"},
    "ctf": {"ctf","pioli","connettori acciaio calcestruzzo","piolo","piolo ctf"},
    "cem e": {"cem-e","cem e","riprese di getto","ripresa di getto","vite con piastra","piastra dentata"},
    "mini cem e": {"mini cem-e","mini cem e"},
    "diapason": {"diapason","staffa","lamiera","staffe"},
    "cls": {"cls","soletta calcestruzzo","calcestruzzo"},
    "clsr": {"clsr","rinforzo","cappa sottile","soletta sottile"},
    "fva": {"fva","fva-l","fva l","viti autoperforanti"},
    "documentazione": {"dop","eta","schede tecniche","manuali di posa","relazioni di calcolo","certificazioni","qualita","iso 9001"},
    "chi siamo": {"chi siamo","chi e tecnaria","tecnaria chi","profilo aziendale","storia","mission","vision","valori"},
}

# ====== Stato globale indice ======
INDEX = []          # lista di blocchi {text, q, a, tags, path, line, title, norm_text, tok, emb}
EMB_MODEL = None    # modello sentence-transformers, se disponibile


# ============ Utility ============

def log(msg, *args):
    if VERBOSE:
        print(f"[scraper_tecnaria] {msg}", *args, flush=True)

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
    # spazi per separare / _ -
    s = s.replace("/", " ").replace("_", " ").replace("-", " ")
    # rimuovi tutto ciò che non è lettera/digit/spazio
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def tokenize(s: str):
    return [t for t in normalize_text(s).split() if t and t not in STOPWORDS_MIN]

def expand_with_synonyms(tokens):
    extra = set()
    joined = " ".join(tokens)
    # match per chiavi con spazio (es. "cem e", "chi siamo")
    for key, syns in SYNONYMS.items():
        key_norm = normalize_text(key)
        if key_norm in joined or any(s in joined for s in (normalize_text(x) for x in syns)):
            for s in syns:
                extra.update(tokenize(s))
    return list(set(tokens) | extra)


# ============ Embeddings opzionali ============

def _load_embedder():
    global EMB_MODEL
    if EMB_MODEL is not None:
        return EMB_MODEL
    try:
        from sentence_transformers import SentenceTransformer
        log(f"[EMB] Carico modello: {DEFAULT_EMBED_MODEL}")
        EMB_MODEL = SentenceTransformer(DEFAULT_EMBED_MODEL)
        return EMB_MODEL
    except Exception as e:
        log(f"[EMB] Modello non disponibile, userò solo keyword. Dettaglio: {e}")
        EMB_MODEL = None
        return None

def embed(texts):
    model = _load_embedder()
    if model is None:
        return None
    try:
        return model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    except Exception as e:
        log(f"[EMB] Errore durante encode, disattivo embedding. {e}")
        return None


# ============ Parsing file .txt ============

TAG_RE = re.compile(r"^\s*\[?\s*TAGS?\s*:\s*(.+?)\s*\]?\s*$", re.I)
Q_RE = re.compile(r"^\s*(D:|Domanda:)\s*(.*)$", re.I)
A_RE = re.compile(r"^\s*(R:|Risposta:)\s*(.*)$", re.I)
SEP_RE = re.compile(r"^[-=─]{6,}\s*$")

def parse_txt_file(path):
    """Parsa un .txt in blocchi Q/A con TAG. Ritorna lista di blocchi."""
    blocks = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception as e:
        log(f"[parse] Errore lettura {path}: {e}")
        return blocks

    cur_tags = []
    cur_q = None
    cur_a = []
    file_title = os.path.splitext(os.path.basename(path))[0]

    def push_block(line_no_end):
        # Salva blocco corrente se ha contenuto
        nonlocal cur_q, cur_a, cur_tags
        a_text = "\n".join(cur_a).strip()
        if (cur_q and len(a_text) >= MIN_CHARS_PER_BLOCK) or (cur_q and a_text):
            text = f"{cur_q}\n{a_text}"
            block = {
                "path": path,
                "title": file_title,
                "tags": cur_tags[:],
                "q": cur_q.strip(),
                "a": a_text,
                "text": text,
                "line": line_no_end,
            }
            blocks.append(block)
        cur_q = None
        cur_a = []

    for i, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")

        # TAGS
        mtag = TAG_RE.match(line)
        if mtag:
            tags_csv = mtag.group(1)
            # split su virgola/;  gestisci spazi
            cur_tags = [t.strip() for t in re.split(r"[;,]", tags_csv) if t.strip()]
            continue

        # separatori: chiudono blocco in corso
        if SEP_RE.match(line):
            if cur_q is not None:
                push_block(i)
            continue

        # domanda
        mq = Q_RE.match(line)
        if mq:
            # se c'era un blocco aperto, chiudilo
            if cur_q is not None:
                push_block(i)
            cur_q = mq.group(2).strip() or "Domanda"
            continue

        # risposta
        ma = A_RE.match(line)
        if ma:
            # inizia risposta su nuova riga (il resto della riga dopo "R:" lo metto subito)
            rest = ma.group(2)
            cur_a.append(rest.strip())
            continue

        # corpo risposta (se siamo dentro una risposta)
        if cur_q is not None:
            cur_a.append(line)

    # chiusura finale
    if cur_q is not None:
        push_block(len(lines))

    # fallback: se non ci sono blocchi D/R, crea blocco unico con il contenuto utile
    if not blocks:
        text = "".join(lines).strip()
        if len(text) >= MIN_CHARS_PER_BLOCK:
            blocks.append({
                "path": path,
                "title": file_title,
                "tags": cur_tags[:],
                "q": file_title,
                "a": text,
                "text": text,
                "line": 1,
            })

    # arricchisci con campi normalizzati
    for b in blocks:
        # norm_text include: tags, titolo file, domanda, risposta
        norm = " ".join([
            " ".join(b.get("tags") or []),
            b.get("title",""),
            b.get("q",""),
            b.get("a",""),
        ])
        b["norm_text"] = normalize_text(norm)
        b["tok"] = tokenize(norm)

    return blocks


def find_txt_files(root_dir):
    """Scansiona ricorsivamente root_dir e ritorna tutti i file .txt (case-insensitive)."""
    txts = []
    for base, _, files in os.walk(root_dir):
        for name in files:
            if name.lower().endswith(".txt"):
                txts.append(os.path.join(base, name))
    txts.sort()
    return txts


# ============ Indicizzazione ============

def build_index(doc_dir="documenti_gTab"):
    """Costruisce INDEX globale (sovrascrive)."""
    global INDEX

    abs_dir = os.path.abspath(doc_dir)
    log(f"Inizio indicizzazione da: {abs_dir}\n")
    files = find_txt_files(doc_dir)
    if not files:
        log("Trovati 0 file .txt:")
        INDEX = []
        log("Indicizzati 0 blocchi / 0 righe da 0 file.")
        return

    log(f"Trovati {len(files)} file .txt:")
    for p in files:
        log(f"  - {p}")

    all_blocks = []
    total_lines = 0
    for p in files:
        bks = parse_txt_file(p)
        all_blocks.extend(bks)
        # conta linee solo per statistica
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                total_lines += sum(1 for _ in f)
        except Exception:
            pass

    # Embedding (opzionale)
    embs = None
    if all_blocks:
        embs = embed([b["norm_text"] for b in all_blocks])
        if embs is not None:
            for b, e in zip(all_blocks, embs):
                b["emb"] = e

    INDEX = all_blocks
    log(f"Indicizzati {len(INDEX)} blocchi / {total_lines} righe da {len(files)} file.")


# ============ Scoring & ricerca ============

def cosine(u, v):
    # vettori già normalizzati dal modello -> dot product
    return float((u @ v)) if u is not None and v is not None else 0.0

def keyword_overlap_score(q_tokens, doc_tokens):
    if not q_tokens or not doc_tokens:
        return 0.0
    qset = set(q_tokens)
    dset = set(doc_tokens)
    inter = len(qset & dset)
    # Jaccard “soft”
    denom = (len(qset) + len(dset) - inter)
    if denom == 0:
        return 0.0
    return inter / denom

def boost_by_tags_filename(q_norm, block):
    boost = 0.0
    # match rudimentale se una parola della query è dentro i tag
    tags_norm = normalize_text(" ".join(block.get("tags") or []))
    if any(t in tags_norm for t in q_norm.split()):
        boost += BOOST_TAG
    # match su titolo/nome file
    title_norm = normalize_text(block.get("title",""))
    if any(t in title_norm for t in q_norm.split()):
        boost += BOOST_FILENAME
    return boost

def search_best_answer(question: str, threshold: float = 0.35, topk: int = 20):
    """
    Ritorna dict:
      {
        "answer": "...",
        "found": bool,
        "score": float,
        "from": {"path":..., "line":..., "title":..., "tags":[...]},
        "question": "... (domanda originale o stimata)",
      }
    """
    if not INDEX:
        return {"answer":"Indice vuoto.", "found": False, "from": None}

    q_norm = normalize_text(question)
    q_tok = tokenize(question)
    q_tok = expand_with_synonyms(q_tok)

    # Embedding della query (se disponibile)
    q_emb = None
    if any("emb" in b for b in INDEX):
        # se almeno un blocco ha 'emb' assumo che l'embedding sia attivo
        e = embed([ " ".join(q_tok) or q_norm ])
        q_emb = e[0] if e is not None else None

    scored = []
    for b in INDEX:
        kw = keyword_overlap_score(q_tok, b.get("tok", []))
        em = cosine(q_emb, b.get("emb")) if q_emb is not None and "emb" in b else 0.0

        # fusione: se embedding c'è -> 0.65*emb + 0.35*kw, altrimenti solo kw
        base = 0.65*em + 0.35*kw if q_emb is not None else kw

        # boost su TAG e nome file
        base += boost_by_tags_filename(q_norm, b)

        scored.append((base, b))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:max(1, topk)]

    if not top:
        return {"answer":"", "found": False, "from": None}

    best_score, best = top[0]
    log(f"[SEARCH] q='{question}' -> score={best_score:.3f} {best.get('path')}:{best.get('line')}")

    if best_score >= threshold:
        ans = best.get("a") or best.get("text") or ""
        meta = {
            "path": best.get("path"),
            "line": best.get("line"),
            "title": best.get("title"),
            "tags": best.get("tags"),
        }
        return {
            "answer": ans.strip(),
            "found": True,
            "score": float(best_score),
            "from": meta,
            "question": best.get("q") or "",
        }

    # non sopra soglia -> prova a restituire comunque un “hint” minimale
    return {
        "answer": "",
        "found": False,
        "score": float(best_score),
        "from": {
            "path": best.get("path"),
            "line": best.get("line"),
            "title": best.get("title"),
            "tags": best.get("tags"),
        },
        "question": "",
    }
