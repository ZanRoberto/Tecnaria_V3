# scraper_tecnaria.py — robust NLU per documenti Tecnaria (.txt)
# - Alias per domande ([ALIASES: ...])
# - Sinonimi & espansione query
# - Fuzzy match (difflib) + token overlap
# - Fallback per intenti (chi siamo, contatti, catalogo, p560, ecc.)

import os, re, json, unicodedata
from pathlib import Path
from collections import defaultdict
from difflib import SequenceMatcher

# ----------------- CONFIG -----------------
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.28"))
TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))
DEBUG = os.environ.get("DEBUG", "0") == "1" or os.environ.get("DEBUG_SCRAPER", "0") == "1"

STOPWORDS = {
    "il","lo","la","i","gli","le","un","uno","una","di","del","della","dell","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da","al","allo","ai","agli","alla","alle",
    "che","come","quale","quali","dove","quando","anche","mi","dei","dal","dai","de","d'"
}

# Sinonimi/lessico di espansione (tieni pure ad aggiungere voci nel tempo)
SYNONYMS = {
    "p560": {"p560","p 560","p-560","pistola","sparachiodi","sparachiodi spit","spit p560"},
    "contatti": {"contatti","telefono","tel","telefonico","mail","email","e-mail","indirizzo","sede","orari","apertura","chiusura","uffici"},
    "catalogo": {"catalogo","prodotti","connettori","codici","lista","elenco"},
    "manuali": {"manuali","manuale","posa","istruzioni","scheda posa"},
    "certificazioni": {"certificazioni","ce","eta","dop","marcatura","dichiarazione di prestazione"},
    "assistenza": {"assistenza","supporto","help","cantiere","tecnico","referente","contatto tecnico"},
    "noleggio": {"noleggio","affitto","a noleggio","rent"},
    "ordine": {"ordine","acquisto","comprare","compra","ordina","preventivo","prezzo","costo","listino"},
    "chi_siamo": {"chi è tecnaria","chi e tecnaria","chi siete","profilo aziendale","chi siamo","azienda tecnaria","storia","valori","mission","vision"},
    "codici": {"codici","elenco codici","lista codici","codici connettori"},
    "hbv": {"hbv","x-hbv","xhbv"},
    "ctf": {"ctf","piolo","pioli"},
    "cem": {"cem-e","cem","mini cem-e","mini cem"},
    "ctl": {"ctl","ct-l"},
    "fva": {"fva","fva-l"},
}

INTENT_ORDER = [
    "chi_siamo","contatti","catalogo","codici","p560","hbv","ctf","cem","ctl","fva",
    "manuali","certificazioni","assistenza","noleggio","ordine"
]

# ----------------- NORMALIZZAZIONE -----------------

def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def _norm(s: str) -> str:
    if not s: return ""
    s = s.lower().strip()
    s = _strip_accents(s)
    s = re.sub(r"[^\w\s:/\-\+\.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _tok(s: str):
    s = _norm(s)
    return [t for t in s.split() if t and t not in STOPWORDS]

def _expand_tokens(tokens):
    """Espande con sinonimi: se appare un membro di una famiglia, aggiungo gli altri."""
    expanded = set(tokens)
    joined = " ".join(tokens)
    for key, variants in SYNONYMS.items():
        if any(v.replace(" ", "") in joined.replace(" ", "") for v in variants):
            for v in variants:
                expanded.update(_tok(v))
    return list(expanded)

# ----------------- PARSING BLOCCHI -----------------

def _split_blocks(raw: str, path: str):
    """
    Blocchi separati da linee di '─'/'-'/'—'.
    Supporta:
      [TAGS: ...]
      [ALIASES: parafrasi1; parafrasi2, ...]
      D: domanda
      R: risposta
    """
    parts = re.split(r"\n[─\-—_]{5,}\n", raw, flags=re.MULTILINE)
    out = []
    line_counter = 1
    for part in parts:
        p = part.strip()
        if not p:
            line_counter += 1
            continue

        # Parse TAGS
        tags = []
        m_tags = re.search(r"\[TAGS\s*:\s*(.*?)\]", p, flags=re.IGNORECASE|re.DOTALL)
        if m_tags:
            tags_raw = m_tags.group(1)
            tags = [t.strip() for t in re.split(r"[;,]", tags_raw) if t.strip()]

        # Parse ALIASES
        aliases = []
        m_alias = re.search(r"\[ALIASES\s*:\s*(.*?)\]", p, flags=re.IGNORECASE|re.DOTALL)
        if m_alias:
            a_raw = m_alias.group(1)
            # separatore ; o , per praticità
            aliases = [a.strip() for a in re.split(r"[;,]", a_raw) if a.strip()]

        # Q/A
        m_q = re.search(r"^\s*D\s*:\s*(.+)$", p, flags=re.IGNORECASE|re.MULTILINE)
        m_a = re.search(r"^\s*R\s*:\s*(.+)$", p, flags=re.IGNORECASE|re.DOTALL|re.MULTILINE)
        qtxt = m_q.group(1).strip() if m_q else ""
        atxt = m_a.group(1).strip() if m_a else ""

        out.append({
            "path": path,
            "line": line_counter,
            "raw": p,
            "q": qtxt,
            "a": atxt,
            "tags": tags,
            "aliases": aliases,
            "norm_q": _norm(qtxt),
            "norm_a": _norm(atxt),
            "norm_tags": _norm(" ".join(tags)) if tags else "",
            "norm_aliases": [_norm(a) for a in aliases] if aliases else []
        })
        line_counter += p.count("\n") + 1
    return out

def _extract_answer_text(raw_block: str) -> str:
    if not raw_block:
        return ""
    m = re.search(r"^\s*R\s*:\s*(.+)$", raw_block, flags=re.IGNORECASE|re.DOTALL|re.MULTILINE)
    if m:
        return m.group(1).strip()
    # rimuove TAGS/ALIASES/D:
    raw = re.sub(r"^\s*\[TAGS.*?\]\s*\n?", "", raw_block, flags=re.IGNORECASE|re.DOTALL|re.MULTILINE)
    raw = re.sub(r"^\s*\[ALIASES.*?\]\s*\n?", "", raw, flags=re.IGNORECASE|re.DOTALL|re.MULTILINE)
    raw = re.sub(r"^\s*D\s*:\s*.*?$", "", raw, flags=re.IGNORECASE|re.MULTILINE)
    return raw.strip()

# ----------------- SCORING -----------------

def _fuzzy(a: str, b: str) -> float:
    if not a or not b: return 0.0
    return SequenceMatcher(None, a, b).ratio()  # 0..1

def _score_block(entry: dict, q_norm: str, q_tokens: list) -> float:
    # Overlap grezzo
    text = " ".join([
        entry.get("norm_q",""),
        entry.get("norm_a",""),
        entry.get("norm_tags",""),
        _norm(entry.get("raw",""))
    ])
    score = 0.0
    for t in set(q_tokens):
        if not t: continue
        if re.search(rf"\b{re.escape(t)}\b", text):
            score += 1.0

    # Fuzzy vs Q, aliases, risposta (prime 300 chars)
    fuzz_q = _fuzzy(q_norm, entry.get("norm_q",""))
    fuzz_a = _fuzzy(q_norm, (entry.get("norm_a","")[:300]))
    fuzz_al = max([_fuzzy(q_norm, a) for a in entry.get("norm_aliases", [])] + [0.0])
    score += 1.2*fuzz_q + 0.8*fuzz_a + 1.3*fuzz_al

    # Boost prodotto/categoria
    if re.search(r"\b(ctf|p560|hbv|xhbv|x\-hbv|cem|cem\-e|mini\s*cem|ctl|ct\-l|fva|fva\-l)\b", q_norm):
        score += 0.8

    # Smorza rumori brevissimi
    if len(entry.get("raw","")) < 80:
        score *= 0.7

    return score

# ----------------- INDICIZZAZIONE -----------------

def build_index(docs_dir: str) -> dict:
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
            raw = fp.read_text(encoding="latin-1", errors="ignore")
        blocks = _split_blocks(raw, str(fp))
        data.extend(blocks)
        total_lines += raw.count("\n") + 1
        if DEBUG:
            print(f"[scraper_tecnaria] Indicizzati {len(blocks)} blocchi da {fp}")

    # invertito leggero
    inverted = defaultdict(list)
    for i, entry in enumerate(data):
        bag = set(
            _tok(entry.get("raw","")) +
            _tok(entry.get("q") or "") +
            _tok(entry.get("a") or "") +
            _tok(" ".join(entry.get("tags") or [])) +
            sum([_tok(a) for a in entry.get("aliases") or []], [])
        )
        for t in bag:
            inverted[t].append((i, 1))

    if DEBUG:
        print(f"[scraper_tecnaria] FINITO: blocks={len(data)} files={len(files)} lines={total_lines}")

    return {
        "data": data,
        "blocks": len(data),
        "files": len(files),
        "lines": total_lines,
        "inverted": inverted
    }

# ----------------- FALLBACK INTENTI -----------------

def _detect_intents(q_norm: str):
    intents = []
    for intent in INTENT_ORDER:
        keys = SYNONYMS.get(intent, set())
        if any(k.replace(" ", "") in q_norm.replace(" ", "") for k in keys):
            intents.append(intent)
    # aggiungo pattern liberi
    if re.search(r"\bcodic|elenco codic|lista codic\b", q_norm):
        if "codici" not in intents: intents.append("codici")
    return intents

def _fallback_by_intent(index: dict, intents: list):
    data = index.get("data") or []
    if not data: return None
    # Cerca un blocco con tag/alias coerenti con l’intento (in ordine)
    for intent in intents:
        for entry in data:
            bucket = " ".join(entry.get("tags") or []) + " " + " ".join(entry.get("aliases") or []) + " " + (entry.get("q") or "")
            n = _norm(bucket)
            if intent == "chi_siamo" and re.search(r"\b(chi siamo|profilo aziendale|azienda tecnaria|bassano del grappa)\b", n):
                ans = _extract_answer_text(entry.get("raw","")) or entry.get("a","")
                if ans: return (ans, True, 0.34, entry.get("path"), entry.get("line"))
            if intent == "contatti" and re.search(r"\b(contatti|telefono|email|mail|indirizzo|orari|sede)\b", n):
                ans = _extract_answer_text(entry.get("raw","")) or entry.get("a","")
                if ans: return (ans, True, 0.34, entry.get("path"), entry.get("line"))
            if intent in {"catalogo","codici"} and re.search(r"\b(catalogo|prodotti|connettori|codici|elenco|lista)\b", n):
                ans = _extract_answer_text(entry.get("raw","")) or entry.get("a","")
                if ans: return (ans, True, 0.34, entry.get("path"), entry.get("line"))
            if intent == "p560" and re.search(r"\b(p560|pistola|sparachiodi)\b", n):
                ans = _extract_answer_text(entry.get("raw","")) or entry.get("a","")
                if ans: return (ans, True, 0.34, entry.get("path"), entry.get("line"))
            if intent == "manuali" and re.search(r"\b(manuali|posa|istruzioni)\b", n):
                ans = _extract_answer_text(entry.get("raw","")) or entry.get("a","")
                if ans: return (ans, True, 0.34, entry.get("path"), entry.get("line"))
            if intent == "certificazioni" and re.search(r"\b(ce|eta|dop|certificaz)\b", n):
                ans = _extract_answer_text(entry.get("raw","")) or entry.get("a","")
                if ans: return (ans, True, 0.34, entry.get("path"), entry.get("line"))
            if intent == "assistenza" and re.search(r"\b(assistenza|supporto|cantiere|tecnico)\b", n):
                ans = _extract_answer_text(entry.get("raw","")) or entry.get("a","")
                if ans: return (ans, True, 0.34, entry.get("path"), entry.get("line"))
            if intent == "noleggio" and re.search(r"\b(noleggio|affitto)\b", n):
                ans = _extract_answer_text(entry.get("raw","")) or entry.get("a","")
                if ans: return (ans, True, 0.34, entry.get("path"), entry.get("line"))
            if intent == "ordine" and re.search(r"\b(ordine|acquisto|prezzo|costo|preventivo|listino)\b", n):
                ans = _extract_answer_text(entry.get("raw","")) or entry.get("a","")
                if ans: return (ans, True, 0.34, entry.get("path"), entry.get("line"))
    return None

# ----------------- RICERCA PRINCIPALE -----------------

def search_best_answer(index: dict, question: str):
    if not index or not isinstance(index, dict):
        return ("", False, 0.0, None, None)
    data = index.get("data") or []
    if not data:
        return ("", False, 0.0, None, None)

    q_norm = _norm(question)
    q_tokens = _expand_tokens(_tok(question))

    # shortlist da indice invertito
    inverted = index.get("inverted") or {}
    shortlist = set()
    for t in q_tokens:
        if t in inverted:
            for doc_id, _ in inverted[t]:
                shortlist.add(doc_id)
    pool = shortlist if shortlist else range(len(data))

    # scoring
    scored = []
    for doc_id in pool:
        entry = data[doc_id]
        s = _score_block(entry, q_norm, q_tokens)
        if s > 0:
            scored.append((s, doc_id))
    scored.sort(reverse=True)
    scored = scored[:max(5, TOPK)]

    if not scored:
        # fallback diretto su intenti
        intents = _detect_intents(q_norm)
        fb = _fallback_by_intent(index, intents)
        if fb: return fb
        return ("", False, 0.0, None, None)

    # normalizzazione soft e soglia
    best_s, best_id = scored[0]
    norm_score = min(1.0, best_s / (best_s + 4.0))

    # se sotto soglia → prova intenti
    if norm_score < SIM_THRESHOLD:
        intents = _detect_intents(q_norm)
        fb = _fallback_by_intent(index, intents)
        if fb: return fb
        # altrimenti restituisce “non preciso”
        entry = data[best_id]
        return ("", False, float(norm_score), entry.get("path"), entry.get("line"))

    entry = data[best_id]
    answer = _extract_answer_text(entry.get("raw","")) or entry.get("a","")
    answer = re.sub(r"\n{3,}", "\n\n", answer.strip())

    if DEBUG:
        print(f"[scraper_tecnaria][SEARCH] q={question!r} -> score={norm_score:.3f} {entry.get('path')}:{entry.get('line')}")
    return (answer, True, float(norm_score), entry.get("path"), entry.get("line"))
