# -*- coding: utf-8 -*-
import os, re, unicodedata, json
from collections import defaultdict, Counter
from difflib import SequenceMatcher

# =========================
# Config
# =========================
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")
MIN_CHARS_PER_CHUNK = int(os.environ.get("MIN_CHARS_PER_CHUNK", "120"))
OVERLAP_CHARS = int(os.environ.get("OVERLAP_CHARS", "0"))
DEBUG = os.environ.get("DEBUG_SCRAPER", "1") == "1"

# Soglia di "trovato"
DEFAULT_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))

# Stopwords MINIMALI (italiano) – niente parole tecniche
STOPWORDS = {
    "e","ed","o","oppure","di","del","della","dello","dei","degli","delle",
    "a","al","allo","ai","agli","alla","alle","da","dal","dallo","dai","dagli","dalla","dalle",
    "in","nel","nello","nei","negli","nella","nelle","su","sul","sullo","sui","sugli","sulla","sulle",
    "per","tra","fra","il","lo","la","i","gli","le","un","uno","una","che","come","dove","quando","quanto","quale",
    "mi","ti","ci","vi","si","di","de","dei","degli","delle"
}

# Sinonimi / espansioni dominio Tecnaria
SYNONYMS = {
    # P560 / chiodatrice
    "p560": ["spit p560","pistola p560","pistola","sparachiodi","chiodatrice","chiodatrice hbv","utensile p560"],
    "sparachiodi": ["pistola","p560","spit p560","chiodatrice"],
    "chiodatrice": ["pistola","p560","spit p560","sparachiodi","chiodatrice hbv"],
    # Famiglie connettori
    "ctf": ["connettori ctf","piolo","pioli","acc aio calcestruzzo","acciaio calcestruzzo","lamiera grecata"],
    "cem-e": ["ceme","ripresa di getto","connettore calcestruzzo calcestruzzo","riprese di getto"],
    "mini": ["mini cem-e","mini ceme"],
    "diapason": ["ctfs diapason","staffa diapason","connettore diapason"],
    "ctl": ["connettore legno calcestruzzo","legno calcestruzzo","tasselli legno"],
    "hbv": ["connettori hbv","x-hbv","legno calcestruzzo","viti"],
    # Info aziendali / contatti
    "contatti": ["telefono","email","indirizzo","sede","orari","come contattarvi","assistenza"],
    "chi": ["chi è tecnaria","chi siete","profilo aziendale","chi siamo","storia","valori","mission","vision"],
    "ordine": ["acquisto","come comprare","prezzi","preventivo","ordine","rivenditori"],
    "noleggio": ["noleggiare","noleggio p560","noleggio pistola"]
}

# Frasi chiave (bigrammi/trigrammi) che aiutano il match semantico
KEY_PHRASES = [
    "pistola sparachiodi", "spit p560", "chiodatrice hbv",
    "solai collaboranti", "ripresa di getto", "legno calcestruzzo",
    "acciaio calcestruzzo", "calcestruzzo calcestruzzo",
    "dichiarazione di prestazione", "marcatura ce", "schede tecniche",
    "manuali di posa", "relazioni di calcolo", "orari uffici", "sede bassano del grappa"
]

# =========================
# Normalizzazione & token
# =========================
def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("’","'").replace("–","-").replace("—","-")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^\w\s\-\.@/]", " ", s)    # lascia @ / . per email/url/codici
    s = re.sub(r"\s+", " ", s).strip()
    return s

def tokenize(s: str):
    s = normalize_text(s)
    toks = [t for t in re.split(r"[^\w@/\.]+", s) if t and t not in STOPWORDS]
    return toks

def expand_with_synonyms(tokens):
    out = set(tokens)
    for t in tokens:
        base = t
        if base in SYNONYMS:
            for syn in SYNONYMS[base]:
                out.update(tokenize(syn))
    return list(out)

# =========================
# Lettura & parsing file
# =========================
BLOCK_SPLIT_RE = re.compile(r"^\s*[\u2500\-_=]{6,}\s*$")  # linee tipo ───── o -----
TAGS_RE = re.compile(r"^\s*\[tags\s*:\s*(.*?)\]\s*$", re.IGNORECASE)

def read_txt(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except:
        try:
            with open(path, "r", encoding="latin-1") as f:
                return f.read()
        except:
            return ""

def split_blocks(text):
    """
    Spezza in blocchi su righe separatrici o Q/A.
    Ritorna lista di blocchi (stringhe) filtrando i troppo corti.
    """
    if not text:
        return []

    parts = []
    cur = []
    lines = text.splitlines()
    for ln in lines:
        if BLOCK_SPLIT_RE.match(ln):
            if cur:
                parts.append("\n".join(cur).strip())
                cur = []
        else:
            cur.append(ln)
    if cur:
        parts.append("\n".join(cur).strip())

    # se non c'erano separatori, usa come 1 blocco
    if not parts:
        parts = [text.strip()]

    # filtra blocchi troppo corti
    parts = [p for p in parts if len(p) >= MIN_CHARS_PER_CHUNK]
    return parts

def extract_tags(header_or_block):
    """
    Cerca la riga [TAGS: ...] all'inizio del testo blocco/file.
    """
    tags = []
    for raw in header_or_block.splitlines()[:5]:
        m = TAGS_RE.match(raw.strip())
        if m:
            inside = m.group(1)
            tags = [t.strip() for t in re.split(r"[,\|;]", inside) if t.strip()]
            break
    return tags

# =========================
# Indice globale
# =========================
INDEX = []            # lista di blocchi {id, path, line, title, text, norm_text, tags, tokens, term_freq}
INVERTED = defaultdict(list)  # term -> [id...]
DF = Counter()        # document frequency per term
N_DOCS = 0

def add_to_index(path, blocks):
    global INDEX, INVERTED, DF
    filename = os.path.basename(path)
    root_title = os.path.splitext(filename)[0]

    # tags del file (prima riga)
    file_tags = extract_tags(blocks[0]) if blocks else []

    for i, raw in enumerate(blocks, start=1):
        tags = extract_tags(raw)
        if not tags:
            tags = file_tags

        # titolo euristico: prima riga non vuota, altrimenti nome file
        first_non_empty = next((l.strip() for l in raw.splitlines() if l.strip()), "")
        title = first_non_empty if first_non_empty and len(first_non_empty) <= 120 else root_title

        # normalizzato e tokenizzato
        norm = normalize_text(raw)
        toks = tokenize(norm)
        tf = Counter(toks)

        bid = len(INDEX)
        entry = {
            "id": bid,
            "path": path,
            "line": i,
            "title": title,
            "text": raw.strip(),
            "norm_text": norm,
            "tags": [normalize_text(t) for t in tags],
            "tokens": toks,
            "term_freq": tf,
            "file": filename
        }
        INDEX.append(entry)

        # inverted & df
        unique_terms = set(toks)
        for t in unique_terms:
            INVERTED[t].append(bid)
            DF[t] += 1

def build_index(doc_dir=None):
    """
    Scansiona DOC_DIR e costruisce INDEX, INVERTED, DF.
    """
    global INDEX, INVERTED, DF, N_DOCS
    basedir = os.path.abspath(doc_dir or DOC_DIR)
    INDEX = []
    INVERTED = defaultdict(list)
    DF = Counter()
    N_DOCS = 0

    if DEBUG:
        print(f"[scraper_tecnaria] Inizio indicizzazione da: {basedir}\n")

    txt_files = []
    for root, _, files in os.walk(basedir):
        for fn in files:
            if fn.lower().endswith(".txt"):
                txt_files.append(os.path.join(root, fn))

    if DEBUG:
        if txt_files:
            print(f"[scraper_tecnaria] Trovati {len(txt_files)} file .txt:")
            for p in txt_files:
                print(f"  - {p}")
        else:
            print("[scraper_tecnaria] Trovati 0 file .txt:")

    for p in sorted(txt_files):
        raw = read_txt(p)
        blocks = split_blocks(raw)
        add_to_index(p, blocks)

    N_DOCS = len(INDEX)
    total_lines = sum(1 for e in INDEX for _ in [e["line"]])
    if DEBUG:
        print(f"[scraper_tecnaria] Indicizzati {N_DOCS} blocchi / {sum(len(read_txt(p).splitlines()) for p in txt_files)} righe da {len(txt_files)} file.")
    return True

# =========================
# Scoring ibrido
# =========================
def idf(term):
    df = DF.get(term, 0)
    if df <= 0:
        return 0.0
    # idf liscio
    return max(0.0, (1.0 + ( (len(INDEX) + 1) / (df + 0.5) )) )

def phrase_hits(question_norm, text_norm):
    score = 0.0
    for ph in KEY_PHRASES:
        phn = normalize_text(ph)
        if phn in question_norm and phn in text_norm:
            score += 1.0
    return score

def tag_boost(q_tokens, entry):
    # boost se qualche token della domanda appare nei TAG o nel nome file/titolo
    tags = entry.get("tags", [])
    title = normalize_text(entry.get("title",""))
    filebase = normalize_text(os.path.splitext(entry.get("file",""))[0])

    hit = 0.0
    for t in q_tokens:
        if t in tags:
            hit += 1.4
        if t and t in title:
            hit += 0.8
        if t and t in filebase:
            hit += 0.8
    return hit

def keyword_overlap_score(q_tokens, entry):
    # somma degli idf per i termini in comune
    tf = entry["term_freq"]
    score = 0.0
    for t in q_tokens:
        if t in tf:
            score += idf(t)
    return score

def fuzzy_score(q_norm, entry_norm):
    # similarità carattere-carattere – blanda ma robusta
    return SequenceMatcher(None, q_norm, entry_norm[:2000]).ratio()  # limita per velocità

def hybrid_score(q, q_tokens, entry):
    # componenti
    kw = keyword_overlap_score(q_tokens, entry)
    fz = fuzzy_score(q, entry["norm_text"])
    ph = phrase_hits(q, entry["norm_text"])
    tg = tag_boost(q_tokens, entry)

    # pesi tarati
    score = (1.35 * kw) + (0.85 * fz) + (1.10 * ph) + (1.40 * tg)
    return score

# =========================
# Ricerca
# =========================
def _candidates_from_inverted(q_tokens):
    # prendi un set di candidati dai termini indicizzati
    cand = set()
    for t in q_tokens:
        for bid in INVERTED.get(t, []):
            cand.add(bid)
    # se zero candidati (domanda generica), considera tutti (bounded by TOPK*20)
    if not cand:
        return range(len(INDEX))
    return cand

def search_best_answer(question: str, threshold: float = None, topk: int = None):
    """
    Ritorna: {answer, found, score, path, line, from, tags, question}
    """
    if threshold is None:
        threshold = DEFAULT_THRESHOLD
    if topk is None:
        topk = TOPK

    q_raw = question or ""
    q_norm = normalize_text(q_raw)
    base_tokens = tokenize(q_norm)
    q_tokens = expand_with_synonyms(base_tokens)

    if DEBUG:
        print(f"[scraper_tecnaria][SEARCH] q='{question}' (norm='{q_norm}')")
        print(f"[scraper_tecnaria][SEARCH] tokens={base_tokens} -> expanded={q_tokens}")

    if not INDEX:
        return {
            "answer": "Indice non pronto.",
            "found": False,
            "score": 0.0,
            "path": None,
            "line": None,
            "from": None,
            "tags": None,
            "question": q_raw
        }

    # Candidati
    cand = _candidates_from_inverted(q_tokens)

    # Ranking
    scored = []
    for bid in cand:
        entry = INDEX[bid]
        s = hybrid_score(q_norm, q_tokens, entry)
        scored.append((s, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:max(50, topk)]  # prendi un po' più ampio, poi decidi

    # prendi il migliore sopra soglia
    if not top:
        return {
            "answer": "",
            "found": False,
            "score": 0.0,
            "path": None,
            "line": None,
            "from": None,
            "tags": None,
            "question": q_raw
        }

    best_score, best = top[0]

    if DEBUG:
        print(f"[scraper_tecnaria][SEARCH] BEST score={best_score:.3f} -> {best['path']}:{best['line']}  title='{best['title']}'")

    found = best_score >= threshold
    # risposta: se il blocco ha formato Q/A, restituisci solo la parte “R: …” altrimenti tutto il blocco
    txt = best["text"].strip()
    # estrai “Risposta:” o “R:” se presenti
    m = re.search(r"(?im)^\s*(risposta|r)\s*:\s*(.+)$", txt)
    if m:
        answer = m.group(2).strip()
    else:
        # se è blocco con D:/R:, prova a prendere dalla prima “R:” in poi
        m2 = re.search(r"(?im)^\s*r\s*:\s*(.+)$", txt)
        answer = m2.group(1).strip() if m2 else txt

    result = {
        "answer": answer,
        "found": bool(found),
        "score": float(best_score),
        "path": best["path"],
        "line": best["line"],
        "from": os.path.basename(best["path"]),
        "tags": best.get("tags", []),
        "question": q_raw
    }
    return result

# =========================
# Auto-build se eseguito direttamente
# =========================
if __name__ == "__main__":
    ok = build_index(DOC_DIR)
    print(json.dumps({
        "status": "ok" if ok else "error",
        "docs": len(INDEX),
        "dir": os.path.abspath(DOC_DIR)
    }, ensure_ascii=False))
