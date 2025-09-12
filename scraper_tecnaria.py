# scraper_tecnaria.py
# Indicizzazione semplice di .txt in documenti_gTab/ con ricerca fuzzy + fallback brand-aware

import os, re, math, json, unicodedata
from pathlib import Path
from collections import defaultdict

# ---- Config da ENV (tutte opzionali) ----
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.30"))
TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))
MIN_CHARS_PER_CHUNK = int(os.environ.get("MIN_CHARS_PER_CHUNK", "200"))
OVERLAP_CHARS = int(os.environ.get("OVERLAP_CHARS", "50"))
DEBUG = os.environ.get("DEBUG", "0") == "1" or os.environ.get("DEBUG_SCRAPER", "0") == "1"

# Stopwords “morbide” (non togliamo parole chiave tipo 'tecnaria', 'p560', ecc.)
STOPWORDS = {
    "il","lo","la","i","gli","le","un","uno","una","di","del","della","dell","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da","al","allo","ai","agli","alla","alle",
    "che","come","quale","quali","dove","quando","anche","mi","dei","dal","dai"
}

# ----------------- Utility -----------------

def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def _norm(s: str) -> str:
    s = s.lower().strip()
    s = _strip_accents(s)
    s = re.sub(r"[^\w\s:/\-\+\.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _tok(s: str):
    s = _norm(s)
    toks = [t for t in s.split() if t and t not in STOPWORDS]
    return toks

def _split_blocks(raw: str, path: str):
    """Divide un file in blocchi separati da una riga di soli '─' oppure 80+ '-'."""
    # accetta anche '—' e '─' miste
    parts = re.split(r"\n[─\-—_]{5,}\n", raw, flags=re.MULTILINE)
    out = []
    line_counter = 1
    for part in parts:
        p = part.strip()
        if not p:
            line_counter += 1
            continue
        # Tenta parsing Q/A + TAGS
        tags = []
        qtxt, atxt = None, None

        # [TAGS: ...]
        m_tags = re.search(r"\[TAGS\s*:\s*(.*?)\]", p, flags=re.IGNORECASE|re.DOTALL)
        if m_tags:
            tags_raw = m_tags.group(1)
            tags = [t.strip() for t in re.split(r"[;,]", tags_raw) if t.strip()]
        # D: ... / R: ...
        m_q = re.search(r"^\s*D\s*:\s*(.+)$", p, flags=re.IGNORECASE|re.MULTILINE)
        m_a = re.search(r"^\s*R\s*:\s*(.+)$", p, flags=re.IGNORECASE|re.DOTALL|re.MULTILINE)
        if m_q:
            qtxt = m_q.group(1).strip()
        if m_a:
            atxt = m_a.group(1).strip()

        out.append({
            "path": path,
            "line": line_counter,
            "raw": p,
            "q": qtxt,
            "a": atxt,
            "tags": tags,
            "norm_q": _norm(qtxt) if qtxt else "",
            "norm_a": _norm(atxt) if atxt else "",
            "norm_tags": " ".join(_tok(" ".join(tags))) if tags else "",
        })
        # avanzamento grezzo
        line_counter += p.count("\n") + 1
    return out

def _extract_answer_text(raw_block: str) -> str:
    """Estrae solo la risposta 'R:' dal blocco se presente, altrimenti restituisce il testo ripulito."""
    if not raw_block:
        return ""
    m = re.search(r"^\s*R\s*:\s*(.+)$", raw_block, flags=re.IGNORECASE|re.DOTALL|re.MULTILINE)
    if m:
        return m.group(1).strip()
    # fallback: togli linee TAGS e D:
    raw = re.sub(r"^\s*\[TAGS.*?\]\s*\n?", "", raw_block, flags=re.IGNORECASE|re.DOTALL|re.MULTILINE)
    raw = re.sub(r"^\s*D\s*:\s*.*?$", "", raw, flags=re.IGNORECASE|re.MULTILINE)
    return raw.strip()

def _score_block(entry: dict, q_norm: str, q_tokens: list) -> float:
    """Scoring semplice: overlap nei campi norm_q/norm_a/tags + boost su termini di prodotto/categoria."""
    text = " ".join([
        entry.get("norm_q",""),
        entry.get("norm_a",""),
        entry.get("norm_tags",""),
        _norm(entry.get("raw",""))
    ])
    score = 0.0

    # Overlap token per token
    for t in set(q_tokens):
        if not t: 
            continue
        if re.search(rf"\b{re.escape(t)}\b", text):
            score += 1.0

    # Bonus se matcha “tecnaria” o codici/prodotti noti
    if "tecnaria" in q_norm or re.search(r"\b(ctf|p560|hbv|x\-hbv|cem\-e|mini\s*cem\-e|ctl|fva|fva\-l|ct\-l)\b", q_norm):
        score += 1.2

    # Bonus per keyword frequenti
    if re.search(r"\b(chi\s+siamo|chi\s+e|profilo|azienda|contatti|orari|telefono|email|catalogo|prodotti|codici|connettori)\b", q_norm):
        score += 0.6

    # Piccolo damp per blocchi troppo brevi (rumore)
    if len(entry.get("raw","")) < 80:
        score *= 0.7

    return score

# ----------------- Indicizzazione -----------------

def build_index(docs_dir: str) -> dict:
    """Legge TUTTI i .txt in docs_dir e costruisce un indice leggero."""
    base = Path(docs_dir)
    if not base.exists():
        if DEBUG: print(f"[scraper_tecnaria] Directory non trovata: {docs_dir}")
        return {"data": [], "blocks": 0, "files": 0, "lines": 0, "inverted": {}}

    files = sorted(base.rglob("*.txt"))
    data = []
    total_lines = 0
    for fp in files:
        try:
            raw = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            # tenta latin-1
            raw = fp.read_text(encoding="latin-1", errors="ignore")
        blocks = _split_blocks(raw, str(fp))
        data.extend(blocks)
        total_lines += raw.count("\n") + 1
        if DEBUG:
            print(f"[scraper_tecnaria] Indicizzati {len(blocks)} blocchi da {fp}")

    # indice invertito super-light (per shortlist)
    inverted = defaultdict(list)
    for i, entry in enumerate(data):
        bag = set(_tok(entry.get("raw","")) + _tok(entry.get("q") or "") + _tok(entry.get("a") or "") + _tok(" ".join(entry.get("tags") or [])))
        for t in bag:
            inverted[t].append((i,1))

    if DEBUG:
        print(f"[scraper_tecnaria] FINITO: blocks={len(data)} files={len(files)} lines={total_lines}")

    return {
        "data": data,
        "blocks": len(data),
        "files": len(files),
        "lines": total_lines,
        "inverted": inverted
    }

# ----------------- Ricerca -----------------

def search_best_answer(index: dict, question: str):
    """Cerca il blocco migliore e restituisce la risposta; se non supera la soglia, fallback brand-aware."""
    if not index or not isinstance(index, dict):
        return ("", False, 0.0, None, None)

    data = index.get("data") or []
    if not data:
        return ("", False, 0.0, None, None)

    q_norm = _norm(question)
    q_tokens = _tok(question)

    # shortlist
    shortlist_ids = set()
    inverted = index.get("inverted") or {}
    for t in q_tokens:
        if t in inverted:
            for doc_id, _w in inverted[t]:
                shortlist_ids.add(doc_id)
    pool = shortlist_ids if shortlist_ids else range(len(data))

    candidates = []
    for doc_id in pool:
        entry = data[doc_id]
        s = _score_block(entry, q_norm, q_tokens)
        if s > 0:
            # piccolo kick per domande corte
            s += 0.8
            candidates.append((s, doc_id))

    candidates.sort(reverse=True)
    candidates = candidates[:max(3, TOPK)]

    if not candidates:
        fb = _brand_fallback(index, q_norm, q_tokens)
        if fb:
            return fb
        return ("", False, 0.0, None, None)

    best_score, best_id = candidates[0]
    entry = data[best_id]

    # normalizzazione “calda”
    norm_score = min(1.0, best_score / (best_score + 4.0))

    if norm_score < SIM_THRESHOLD:
        fb = _brand_fallback(index, q_norm, q_tokens)
        if fb:
            return fb
        return ("", False, float(norm_score), entry.get("path"), entry.get("line"))

    answer = _extract_answer_text(entry.get("raw","")) or (entry.get("a") or "")
    answer = re.sub(r"\n{3,}", "\n\n", answer.strip())

    if DEBUG:
        print(f"[scraper_tecnaria][SEARCH] q={question!r} -> score={norm_score:.3f} {entry.get('path')}:{entry.get('line')}")
    return (answer, True, float(norm_score), entry.get("path"), entry.get("line"))

def _brand_fallback(index: dict, q_norm: str, q_tokens: list):
    """Fallback per domande base dove vogliamo SEMPRE una risposta."""
    data = index.get("data") or []
    if not data:
        return None

    def contains_any(txt, words):
        return any(w in txt for w in words)

    # CHI SIAMO
    if contains_any(q_norm, ["chi e tecnaria", "chi è tecnaria", "chi siete", "parlami di tecnaria", "profilo aziendale", "chi siamo", "azienda tecnaria"]):
        for entry in data:
            tags = " ".join(entry.get("tags") or [])
            tnorm = _norm(tags + " " + (entry.get("q") or "") + " " + entry.get("raw",""))
            if contains_any(tnorm, ["chi siamo","profilo aziendale","chi e tecnaria","tecnaria e","bassano del grappa","azienda tecnaria"]):
                ans = _extract_answer_text(entry.get("raw","")) or (entry.get("a") or "")
                ans = ans.strip()
                if ans:
                    return (ans, True, 0.35, entry.get("path"), entry.get("line"))

    # CONTATTI / ORARI
    if contains_any(q_norm, ["contatti","telefono","email","mail","orari","sede","indirizzo"]):
        for entry in data:
            tags = " ".join(entry.get("tags") or [])
            tnorm = _norm(tags + " " + (entry.get("q") or "") + " " + entry.get("raw",""))
            if contains_any(tnorm, ["contatti","orari","sede","indirizzo","telefono","email"]):
                ans = _extract_answer_text(entry.get("raw","")) or (entry.get("a") or "")
                ans = ans.strip()
                if ans:
                    return (ans, True, 0.35, entry.get("path"), entry.get("line"))

    # ELENCO PRODOTTI / CODICI
    if contains_any(q_norm, ["catalogo","prodotti","connettori","elenco codici","codici connettori","lista codici"]):
        for entry in data:
            tags = " ".join(entry.get("tags") or [])
            tnorm = _norm(tags + " " + (entry.get("q") or "") + " " + entry.get("raw",""))
            if contains_any(tnorm, ["prodotti","catalogo","elenco","codici","connettori"]):
                ans = _extract_answer_text(entry.get("raw","")) or (entry.get("a") or "")
                ans = ans.strip()
                if ans:
                    return (ans, True, 0.35, entry.get("path"), entry.get("line"))

    # P560 (domande ricorrenti)
    if contains_any(q_norm, ["p560","p 560","p-560","pistola"]):
        for entry in data:
            tnorm = _norm((entry.get("q") or "") + " " + " ".join(entry.get("tags") or []) + " " + entry.get("raw",""))
            if contains_any(tnorm, ["p560","p 560","p-560","pistola","sparachiodi"]):
                ans = _extract_answer_text(entry.get("raw","")) or (entry.get("a") or "")
                ans = ans.strip()
                if ans:
                    return (ans, True, 0.35, entry.get("path"), entry.get("line"))

    return None
