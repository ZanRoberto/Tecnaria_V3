# scraper_tecnaria.py
# Indicizzazione e ricerca su file .txt (documenti_gTab/)
# - Nessuna dipendenza extra (solo stdlib) per restare compatibile con Render.
# - Blocchi separati da una riga "────" oppure per gruppi Q:/R: e [TAGS: ...]
# - Ricerca full-text + peso TAG + piccola fuzzy/overlap per domande libere.
# - Restituisce SOLO la risposta (niente "documentazione locale", niente Q:).
#
# API attese da app.py:
#   build_index(docs_dir) -> dict {blocks, files, lines, data}
#   search_best_answer(index, question) -> (answer, found, score, path, line)
#   (alias) search_answer(index, question) -> come sopra

import os
import re
import unicodedata
from collections import defaultdict, Counter

# -------------------------------
# Config da ENV (valori robusti)
# -------------------------------
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))  # soglia per accettare una risposta
TOPK = int(os.environ.get("TOPK_SEMANTIC", "12"))                      # quanti blocchi tenere in shortlist
MIN_CHARS = int(os.environ.get("MIN_CHARS_PER_CHUNK", "120"))          # evita blocchi troppo corti

DEBUG = os.environ.get("DEBUG_SCRAPER", os.environ.get("DEBUG", "0")) == "1"

# Stopwords essenziali italiane (non rimuovere termini tecnici)
STOPWORDS = {
    "il","lo","la","i","gli","le","un","uno","una","di","del","della","dell","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da","al","allo","ai","agli","alla","alle",
    "che","come","quale","quali","dove","quando","anche","ma","se","si","no","mi","ti","ci","vi",
    "dei","agli","dai","nel","nella","nelle","nei","sul","sulla","sulle","sui"
}

# Regex utili
RE_TAGS   = re.compile(r"^\s*\[TAGS:\s*(.*?)\s*\]\s*$", re.IGNORECASE)
RE_Q      = re.compile(r"^\s*D:\s*(.+)$", re.IGNORECASE)
RE_A      = re.compile(r"^\s*R:\s*(.+)$", re.IGNORECASE)
RE_SPLIT  = re.compile(r"^\s*[\u2500\-_=]{10,}\s*$")  # linee con "────────", "-----", "====="

# -------------------------------
# Utilità testuali
# -------------------------------

def _norm(text: str) -> str:
    """Normalizza (minuscole, rimuove accenti, spazi compatti)."""
    text = text or ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    return text

def _tok(text: str):
    """Tokenizzazione semplice; rimuove stopword."""
    text = _norm(text)
    # tieni lettere/numeri e slash per codici (es. 14/040)
    text = re.sub(r"[^a-z0-9/\.]+", " ", text)
    toks = [t for t in text.split() if t and t not in STOPWORDS]
    return toks

def _contains_hard_keyword(block_text_norm: str, q_norm: str) -> int:
    """Boost se la domanda contiene una parola 'forte' che compare nel blocco (es. P560, CTF, HBV)."""
    hard = []
    for w in re.findall(r"[a-z0-9\-]{2,}", q_norm):
        if w.isdigit():
            continue
        if len(w) <= 2:
            continue
        # parole tecniche/nomi prodotto tipici
        if any(pref in w for pref in ("p560","ctf","cem","hbv","x-hbv","ctl","fva","fva-l","cls","clsr","diapason","t-connect","dop","eta","bassano")):
            hard.append(w)
    score = 0
    for w in set(hard):
        if w in block_text_norm:
            score += 1
    return score

def _extract_answer_text(raw_block: str) -> str:
    """Estrae SOLO la risposta dal blocco:
       - Se c'è 'R:' prende da lì in giù (fino al prossimo separatore o fine blocco)
       - Altrimenti ritorna il blocco ripulito (senza TAGS e senza Q:)."""
    lines = raw_block.splitlines()
    # taglia eventuale riga [TAGS:...]
    lines_wo_tags = []
    for ln in lines:
        if RE_TAGS.match(ln):
            continue
        lines_wo_tags.append(ln)
    # prova a partire da R:
    out = []
    seen_r = False
    for ln in lines_wo_tags:
        mA = RE_A.match(ln)
        if mA:
            out.append(mA.group(1).strip())
            seen_r = True
            continue
        if seen_r:
            # interrompi se trovi un separatore "────"
            if RE_SPLIT.match(ln):
                break
            out.append(ln.strip())
    if seen_r:
        text = "\n".join([l for l in out if l]).strip()
        if text:
            return text
    # fallback: rimuovi eventuali D: e restituisci tutto il resto
    cleaned = []
    for ln in lines_wo_tags:
        if RE_Q.match(ln):
            continue
        cleaned.append(ln.strip())
    text = "\n".join([l for l in cleaned if l]).strip()
    return text

# -------------------------------
# Parser blocchi dai file .txt
# -------------------------------

def _iter_blocks_from_text(text: str):
    """Ritorna blocchi logici separati. Un blocco è:
       - una sezione tra separatori "─────"
       - oppure un gruppo che contiene [TAGS:], e/o D:, R:."""
    if not text:
        return
    # Spezza per grandi separatori
    raw_chunks = re.split(RE_SPLIT, text)
    for chunk in raw_chunks:
        c = chunk.strip()
        if len(c) < MIN_CHARS:
            continue
        yield c

def _parse_block(chunk: str):
    """Estrae tags, domanda/risposta (se presenti) e testo normalizzato."""
    tags = []
    for ln in chunk.splitlines():
        mT = RE_TAGS.match(ln)
        if mT:
            # tag singola riga separati da virgole
            tags = [t.strip().lower() for t in mT.group(1).split(",") if t.strip()]
            break

    # Q/A (non sono obbligatori – a volte c'è solo testo)
    q_text = None
    a_text = None
    for ln in chunk.splitlines():
        mQ = RE_Q.match(ln)
        if mQ and not q_text:
            q_text = mQ.group(1).strip()
        mA = RE_A.match(ln)
        if mA and not a_text:
            a_text = mA.group(1).strip()

    # fallback: se non c'è R: prendi il blocco ripulito
    if not a_text:
        a_text = _extract_answer_text(chunk)

    norm_all = _norm(chunk)
    norm_ans = _norm(a_text or "")
    return {
        "tags": tags,
        "q": q_text,
        "a": a_text,
        "norm_chunk": norm_all,
        "norm_ans": norm_ans,
        "raw": chunk
    }

# -------------------------------
# Indicizzazione
# -------------------------------

def build_index(docs_dir: str):
    """Scansiona docs_dir per .txt, crea lista blocchi con metadata e indice invertito leggero."""
    if DEBUG:
        print(f"[scraper_tecnaria] Inizio indicizzazione da: {docs_dir}\n")

    data = []            # lista di dict: {path, line, tags, q, a, norm_chunk, norm_ans, raw}
    inverted = defaultdict(list)  # token -> list of (doc_id, weight)
    files = 0
    lines_total = 0

    # Trova TUTTI i .txt (senza limiti)
    file_list = []
    for root, _dirs, fnames in os.walk(docs_dir):
        for fn in fnames:
            if fn.lower().endswith(".txt"):
                file_list.append(os.path.join(root, fn))

    if DEBUG:
        print("[scraper_tecnaria] Trovati %d file .txt:" % len(file_list))
        for p in file_list:
            print("  -", p)

    for path in sorted(file_list):
        files += 1
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            lines_total += content.count("\n") + 1
            # Estrai blocchi
            line_cursor = 1
            for chunk in _iter_blocks_from_text(content):
                parsed = _parse_block(chunk)
                entry = {
                    "path": path,
                    "line": line_cursor,
                    **parsed
                }
                data.append(entry)

                # indicizza token della risposta + tag + eventuale domanda
                tokens = _tok(parsed.get("a") or "") + _tok(parsed.get("q") or "")
                for t in _tok(" ".join(parsed.get("tags", []))):
                    tokens.append(t)
                # pesa leggermente i tag
                counts = Counter(tokens)
                for tok, cnt in counts.items():
                    inverted[tok].append((len(data)-1, cnt))

                # aggiorna cursor grossolanamente
                line_cursor += max(1, chunk.count("\n") + 1)

        except Exception as e:
            if DEBUG:
                print(f"[scraper_tecnaria][WARN] Errore su {path}: {e}")

    blocks = len(data)
    if DEBUG:
        print(f"[scraper_tecnaria] Indicizzati {blocks} blocchi / {lines_total} righe da {files} file.")

    return {
        "blocks": blocks,
        "files": files,
        "lines": lines_total,
        "data": data,
        "inverted": inverted
    }

# -------------------------------
# Ricerca
# -------------------------------

def _score_block(entry, q_norm, q_tokens):
    """Punteggio combinato: overlap token, presenza hard keywords, bonus se TAG matcha."""
    # 1) overlap semplice su risposta normalizzata
    ans = entry.get("norm_ans", "")
    if not ans:
        ans = entry.get("norm_chunk", "")

    # conteggio match token
    overlap = 0
    for t in q_tokens:
        if t in ans:
            overlap += 1

    # 2) hard keyword boost (P560, CTF, HBV, ecc.)
    hard_boost = _contains_hard_keyword(ans, q_norm)

    # 3) match su TAG
    tag_boost = 0
    tags = entry.get("tags") or []
    if tags:
        # se QUALSIASI token della domanda è nel tag string → boost
        tag_text = " ".join(tags)
        tag_norm = _norm(tag_text)
        for t in q_tokens:
            if t and t in tag_norm:
                tag_boost += 1

    # 4) bonus se la domanda è molto simile alla "D:" del blocco
    qa_bonus = 0
    if entry.get("q"):
        q_in_block = _norm(entry["q"])
        # mini-somiglianza: conta quanti token della domanda stanno nella q_in_block
        inter = 0
        for t in q_tokens:
            if t in q_in_block:
                inter += 1
        qa_bonus = inter * 0.5

    # combinazione pesata
    score = (
        overlap * 1.0 +
        hard_boost * 1.2 +
        tag_boost * 0.8 +
        qa_bonus
    )

    return score

def search_best_answer(index: dict, question: str):
    """Cerca il blocco migliore e restituisce SOLO la risposta testuale."""
    if not index or not isinstance(index, dict):
        return ("", False, 0.0, None, None)

    data = index.get("data") or []
    if not data:
        return ("", False, 0.0, None, None)

    q_norm = _norm(question)
    q_tokens = _tok(question)

    # Shortlist tramite indice invertito (se possibile)
    shortlist_ids = set()
    inverted = index.get("inverted") or {}

    for t in q_tokens:
        if t in inverted:
            for doc_id, _w in inverted[t]:
                shortlist_ids.add(doc_id)

    # fallback: se indice non aiuta, consideriamo tutti (ma TOPK)
    candidates = []
    if shortlist_ids:
        pool = shortlist_ids
    else:
        pool = range(len(data))

    # Scora i candidati
    for doc_id in pool:
        entry = data[doc_id]
        s = _score_block(entry, q_norm, q_tokens)
        if s > 0:
            candidates.append((s, doc_id))

    # ordina per score
    candidates.sort(reverse=True)
    candidates = candidates[:max(3, TOPK)]

    if not candidates:
        # niente trovato sopra 0
        return ("", False, 0.0, None, None)

    best_score, best_id = candidates[0]
    entry = data[best_id]

    # normalizza score (grezza): portiamo in [0..1] con una curva dolce
    # (solo per dare un'idea nel debug/UI)
    norm_score = min(1.0, best_score / (best_score + 8.0))

    if norm_score < SIM_THRESHOLD:
        return ("", False, float(norm_score), entry.get("path"), entry.get("line"))

    # Estrai SOLO la risposta pulita
    answer = _extract_answer_text(entry.get("raw", "")) or (entry.get("a") or "")
    answer = answer.strip()

    # Taglia spazi multipli
    answer = re.sub(r"\n{3,}", "\n\n", answer)

    if DEBUG:
        print(f"[scraper_tecnaria][SEARCH] q={question!r} -> score={norm_score:.3f} {entry.get('path')}:{entry.get('line')}")

    return (answer, True, float(norm_score), entry.get("path"), entry.get("line"))

# Alias per retro-compatibilità (vedi spiegazione sopra)
def search_answer(index, question):
    return search_best_answer(index, question)
