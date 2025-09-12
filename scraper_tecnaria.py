# -*- coding: utf-8 -*-
import os, re, unicodedata, glob, json
from collections import defaultdict
from rapidfuzz import fuzz, process
from rank_bm25 import BM25Okapi

# =========================
# Config "leggera"
# =========================
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")

# soglie di matching (tuneabili anche da env)
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
TOPK_SEMANTIC = int(os.environ.get("TOPK_SEMANTIC", "20"))

# stopwords minimali italiane (non togliamo termini tecnici)
STOPWORDS_MIN = {
    "il","lo","la","i","gli","le","un","uno","una",
    "di","del","della","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da",
    "al","allo","ai","agli","alla","alle",
    "che","come","dove","quando","anche",
    "mi","ti","si","ci","vi","a","de","dal","dall","dalla","dalle"
}

# sinonimi/espansioni basiche per le query più comuni
SYNONYMS = {
    "p560": ["spit p560", "pistola p560", "sparachiodi p560", "pistola a cartuccia", "sparachiodi"],
    "ctf": ["pioli", "connettori a piolo", "piolo su piastra", "acciaio calcestruzzo"],
    "cem-e": ["riprese di getto", "vite con piastra dentata", "connettore per calcestruzzo"],
    "mini cem-e": ["mini cem", "mini ripresa getto", "spessori ridotti"],
    "diapason": ["lamiera a diapason", "ali con barre", "connettore lamiera acciaio"],
    "hbv": ["chiodatrice", "pistola chiodi", "chiodi hbv"],
    "contatti": ["telefono", "email", "assistenza", "uffici", "orari"],
    "chi è tecnaria": ["chi siamo", "azienda", "profilo aziendale", "mission", "storia", "valori"],
    "codici": ["codici prodotti", "catalogo", "codici connettori", "lista prodotti", "elenco"]
}

# =========================
# Utilità testo
# =========================
def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("’", "'").replace("“","\"").replace("”","\"")
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    # separa punteggiatura con spazi
    s = re.sub(r"[^\w\s]", " ", s)
    # comprime spazi
    s = re.sub(r"\s+", " ", s).strip()
    return s

def tokenize(s: str):
    s = normalize_text(s)
    tokens = [t for t in s.split() if t and t not in STOPWORDS_MIN]
    return tokens

def expand_query(q: str) -> str:
    base = normalize_text(q)
    terms = base.split()
    expanded = set(terms)
    # espansione per chiave sinonimo contenuta nella query
    for k, vs in SYNONYMS.items():
        if k in base or any(t == k for t in terms):
            expanded.add(k)
            for v in vs:
                expanded.update(tokenize(v))
    return " ".join(sorted(expanded))

# =========================
# Caricamento documenti .txt
# =========================
# INDEX: lista di dict con:
# { "file":..., "tags": set([...]), "q": str, "a": str, "raw": str, "tokens": [...], "title_score": int }
INDEX = []
BM25 = None
DOC_TOKENS = []  # corpus tokenizzato per BM25

QA_SPLIT_RE = re.compile(r"^\s*D\s*:\s*(.+?)\s*\n\s*R\s*:\s*(.+)$", re.DOTALL | re.IGNORECASE)

def _extract_blocks_from_text(txt: str):
    """
    Estrae blocchi Q/A formattati e anche blocchi generici separati da righe di '─' come fallback.
    Ritorna lista di (question, answer, tags_set, raw_block).
    """
    blocks = []

    # 1) TAGS
    tags = set()
    mt = re.search(r"\[TAGS:\s*(.+?)\s*\]", txt, re.IGNORECASE)
    if mt:
        rawtags = mt.group(1)
        for t in re.split(r"[,\|;]", rawtags):
            tt = t.strip()
            if tt:
                tags.add(normalize_text(tt))

    # 2) blocchi Q/A "D: ... R: ..."
    for m in QA_SPLIT_RE.finditer(txt):
        q = m.group(1).strip()
        a = m.group(2).strip()
        raw = f"D: {q}\nR: {a}"
        blocks.append((q, a, set(tags), raw))

    # 3) fallback: split su separatori lunghi (se non ci sono D:/R:)
    if not blocks:
        parts = re.split(r"\n\s*[─\-_=]{6,}\s*\n", txt)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            # prova a creare pseudo-QA: prima riga = domanda, resto = risposta
            lines = [l for l in part.splitlines() if l.strip()]
            if len(lines) >= 2:
                q = lines[0].strip()
                a = "\n".join(lines[1:]).strip()
                blocks.append((q, a, set(tags), part))
            else:
                # tutto come "risposta" generica
                blocks.append(("informazioni", part, set(tags), part))

    return blocks

def _score_title_hint(file_name: str, tags_set: set):
    """
    Boost se il file o i TAG contengono parole chiave note.
    """
    f = normalize_text(file_name)
    score = 0
    # boost tematici
    if "p560" in f: score += 2
    if "diapason" in f: score += 1
    if "ctf" in f: score += 1
    if "cem" in f: score += 1
    if "hbv" in f: score += 1
    if "chi siamo" in f or "profilo" in f or "vision" in f or "mission" in f: score += 2
    if "contatti" in f or "orari" in f: score += 2
    # boost da TAG
    tagstr = " ".join(sorted(tags_set))
    if "p560" in tagstr: score += 2
    if "diapason" in tagstr: score += 1
    if "ctf" in tagstr: score += 1
    if "cem e" in tagstr or "cem-e" in tagstr: score += 1
    if "contatti" in tagstr: score += 2
    if "chi siamo" in tagstr or "azienda" in tagstr: score += 2
    return score

def build_index(doc_dir: str = DOC_DIR):
    """
    Scansiona tutti i .txt/.TXT, crea INDEX e costruisce BM25.
    """
    global INDEX, BM25, DOC_TOKENS

    INDEX = []
    DOC_TOKENS = []

    doc_dir = doc_dir or DOC_DIR
    absdir = os.path.abspath(doc_dir)

    # trova .txt e .TXT
    files = []
    files += glob.glob(os.path.join(absdir, "*.txt"))
    files += glob.glob(os.path.join(absdir, "*.TXT"))

    print(f"[scraper_tecnaria] Inizio indicizzazione da: {absdir}\n[scraper_tecnaria] Trovati {len(files)} file .txt/.TXT:")
    for p in files:
        print(f"[scraper_tecnaria]   - {p}")

    total_blocks = 0
    total_lines = 0

    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            try:
                with open(path, "r", encoding="latin-1", errors="ignore") as f:
                    content = f.read()
            except Exception as e:
                print(f"[scraper_tecnaria] ERRORE lettura {path}: {e}")
                continue

        blocks = _extract_blocks_from_text(content)
        file_name = os.path.basename(path)
        for (q, a, tags_set, raw_block) in blocks:
            qn = normalize_text(q)
            an = normalize_text(a)
            toks = tokenize(qn + " " + an)
            hint = _score_title_hint(file_name, tags_set)
            INDEX.append({
                "file": file_name,
                "path": path,
                "tags": tags_set,
                "q": q.strip(),
                "a": a.strip(),
                "raw": raw_block,
                "tokens": toks,
                "title_score": hint
            })
            DOC_TOKENS.append(toks)
            total_blocks += 1
            total_lines += raw_block.count("\n") + 1

    # costruisci BM25
    if DOC_TOKENS:
        BM25 = BM25Okapi(DOC_TOKENS)
    else:
        BM25 = None

    print(f"[scraper_tecnaria] Indicizzati {total_blocks} blocchi / {total_lines} righe da {len(files)} file.")
    return {"blocks": total_blocks, "lines": total_lines, "files": len(files)}

# =========================
# Ricerca
# =========================
def _bm25_candidates(query: str, topk: int = 20):
    if not BM25 or not INDEX:
        return []
    q_expanded = expand_query(query)
    q_tokens = tokenize(q_expanded)
    if not q_tokens:
        return []
    scores = BM25.get_scores(q_tokens)  # lista di score allineata a INDEX
    # prendi i migliori topk
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:topk]
    return ranked  # (idx, score)

def _keyword_boost(query: str, item):
    """
    Boost “intelligente”: presenza di parole della query in q/a/tags/file + title_score.
    Fuzzy per tollerare piccole differenze (accenti/plurali).
    """
    qn = normalize_text(query)
    score = 0.0

    # match con il titolo/file/tags via fuzzy
    target = " ".join([
        normalize_text(item.get("q","")),
        normalize_text(item.get("a","")),
        normalize_text(" ".join(item.get("tags", []))),
        normalize_text(item.get("file",""))
    ])

    # fuzzy token set ratio (rapido e robusto)
    fscore = fuzz.token_set_ratio(qn, target) / 100.0  # 0..1
    score += 0.6 * fscore

    # bonus per match diretti di termini chiave (p560, ctf, ecc.)
    key_hits = 0
    for kw in ["p560","ctf","cem e","cem-e","diapason","hbv","contatti","chi siamo","codici","catalogo"]:
        if kw in qn and kw in target:
            key_hits += 1
    score += 0.15 * key_hits

    # hint da titolo/file/tag
    score += 0.25 * float(item.get("title_score", 0))

    return score

def search_best_answer(query: str, threshold: float = SIM_THRESHOLD, topk: int = TOPK_SEMANTIC):
    """
    Combina BM25 (lexicale) + fuzzy + boost su TAG/nome file.
    Ritorna la miglior risposta.
    """
    if not INDEX:
        return {"found": False, "answer": "", "from": None}

    # 1) candidati BM25
    ranked = _bm25_candidates(query, topk=topk)
    # se per qualche motivo BM25 è assente, usa tutti i documenti come fallback
    if not ranked:
        ranked = list(enumerate([0.0]*len(INDEX)))[:topk]

    # 2) ricalcola uno score ibrido
    scored = []
    for idx, bm25_score in ranked:
        item = INDEX[idx]
        hybrid = 0.7 * (bm25_score if isinstance(bm25_score, (int, float)) else 0.0) + 0.3 * _keyword_boost(query, item)
        scored.append((idx, hybrid))

    # 3) prendi il migliore
    scored.sort(key=lambda x: x[1], reverse=True)
    best_idx, best_score = scored[0]

    item = INDEX[best_idx]
    answer = item["a"] if item.get("a") else item.get("raw","")
    if not answer:
        return {"found": False, "answer": "", "from": None}

    # threshold “elastico”: scala con distribuzione top-3 per evitare non-trovato troppo aggressivo
    top3 = [s for _, s in scored[:3]]
    dyn_thr = min(threshold, max(0.15, (sum(top3)/len(top3))*0.5)) if top3 else threshold

    found = best_score >= dyn_thr

    # tags sintetici
    tags = sorted(list(item.get("tags", []))) if item.get("tags") else None

    return {
        "found": bool(found),
        "score": float(round(best_score, 3)),
        "from": item.get("file"),
        "tags": tags if tags else None,
        "answer": answer.strip()
    }
