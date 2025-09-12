# -*- coding: utf-8 -*-
import os, re, json, unicodedata
from difflib import SequenceMatcher

# =========================
# Config base (via env)
# =========================
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")
MIN_CHARS_PER_CHUNK = int(os.environ.get("MIN_CHARS_PER_CHUNK", "120"))
OVERLAP_CHARS = int(os.environ.get("OVERLAP_CHARS", "40"))
DEFAULT_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
TOPK_DEFAULT = int(os.environ.get("TOPK_SEMANTIC", "20"))

DEBUG = os.environ.get("DEBUG_SCRAPER", "1") == "1"

# =========================
# Stopwords minimali (IT)
# =========================
STOPWORDS = {
    "il","lo","la","i","gli","le","un","uno","una",
    "di","del","della","dello","dei","degli","delle",
    "e","o","con","per","su","tra","fra","in","da","al","ai","agli","alla","alle",
    "che","come","dove","quando","anche","de","dal","dall","dalle","agli"
}

# =========================
# Sinonimi / intent router
# =========================
INTENT_SYNONYMS = {
    # P560 / chiodatrice
    "p560": ["p560","p 560","p-560","spit p560","pistola","sparachiodi","sparachiodi p560","pistola spit"],
    "hbv_chiodatrice": ["chiodatrice hbv","chiodatrice","hbv tool"],
    # Contatti
    "contatti": ["contatti","telefono","email","mail","sede","orari","come contattarvi","assistenza","dove siete"],
    # Codici / elenco prodotti
    "codici": ["codici","catalogo","elenco prodotti","lista codici","codici connettori","catalogo tecnaria"],
    # Chi siamo
    "chisiamo": ["chi è tecnaria","chi è tecnaria?","chi siete","chi siamo","profilo aziendale","storia tecnaria","vision","mission"]
}

# Mappa intent -> priorità file (ordine conta)
INTENT_TO_FILES = {
    "p560": ["P560.txt","HBV_Chiodatrice.txt","CTF.txt","Diapason.txt"],
    "hbv_chiodatrice": ["HBV_Chiodatrice.txt","P560.txt","CTF.txt"],
    "contatti": ["ChiSiamo_ContattiOrari.txt"],
    "codici": ["Prodotti_Elenco.txt","CTF.txt","CEM-E.txt","MINI_CEM-E.txt","CTL.txt","Diapason.txt","X-HBV.txt"],
    "chisiamo": ["ChiSiamo_ProfiloAziendale.txt","ChiSiamo_VisionMission.txt","ChiSiamo_Certificazioni.txt"]
}

# =========================
# Indice globale
# =========================
INDEX = []  # ogni voce: {path, file, line, text, tags(list), title}

# =========================
# Utility di testo
# =========================
def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\u200b","")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    # tieni lettere, numeri e spazi
    s = re.sub(r"[^a-z0-9àèéìòóùü\s\-\_\/\.]", " ", s)  # prima di rimuovere accenti
    # dopo NFKD gli accenti sono separati; ripulisci residui
    s = re.sub(r"[^a-z0-9\s\-\_\/\.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def tokenize(s: str):
    s = normalize_text(s)
    toks = [t for t in s.split() if t and t not in STOPWORDS]
    return toks

def read_file_utf8(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def detect_tags_and_blocks(full_text: str):
    """
    Restituisce lista di blocchi. Ogni blocco: {"text":..., "tags":[...]}
    Split per separatori '────' o '====' o righe vuote multiple.
    Rileva TAGS nella prima riga in forma: [TAGS: a, b, c]
    """
    tags = []
    # estrai riga TAGS se c'è
    m = re.search(r"^\s*\[TAGS\s*:\s*(.*?)\]\s*$", full_text, flags=re.IGNORECASE|re.M)
    if m:
        raw = m.group(1).strip()
        tags = [t.strip().lower() for t in re.split(r"[;,/]", raw) if t.strip()]

    # split in blocchi
    parts = re.split(r"(?:\n[\-\=]{5,}\n)|(?:\n\s*\n\s*\n+)", full_text)
    blocks = []
    for p in parts:
        p = p.strip()
        if len(p) < MIN_CHARS_PER_CHUNK:
            continue
        blocks.append({"text": p, "tags": tags[:]})
    return blocks

def file_priority_score(filename: str, question: str) -> float:
    """
    Boost se il filename è coerente con l'intent riconosciuto.
    """
    qn = normalize_text(question)
    score = 0.0
    for intent, keys in INTENT_SYNONYMS.items():
        if any(k in qn for k in keys):
            wanted = INTENT_TO_FILES.get(intent, [])
            # boost decrescente per posizione
            for rank, fname in enumerate(wanted):
                if fname.lower() == filename.lower():
                    score += 0.6 - 0.05*rank  # 0.6, 0.55, 0.5, ...
                    break
    return score

def tag_boost(tags, question: str) -> float:
    if not tags:
        return 0.0
    qn = normalize_text(question)
    boost = 0.0
    for t in tags:
        tn = normalize_text(t)
        if not tn:
            continue
        if tn in qn:
            boost += 0.12
        # sinonimi grezzi: p560 ↔ pistola/sparachiodi
        if ("p560" in qn or "sparachiodi" in qn or "pistola" in qn) and ("p560" in tn or "pistola" in tn or "sparachiodi" in tn):
            boost += 0.18
    return boost

def keyword_overlap_score(q: str, text: str) -> float:
    q_toks = set(tokenize(q))
    t_toks = set(tokenize(text))
    if not q_toks or not t_toks:
        return 0.0
    inter = len(q_toks & t_toks)
    base = inter / (len(q_toks) ** 0.75)  # favorisci match delle parole-chiave della query
    return min(1.0, base)

def fuzzy_score(q: str, text: str, maxlen: int = 400) -> float:
    a = normalize_text(q)
    b = normalize_text(text[:maxlen])
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()  # 0..1

# =========================
# Indicizzazione
# =========================
def build_index(doc_dir: str = DOC_DIR):
    global INDEX
    INDEX = []
    root = os.path.abspath(doc_dir)
    if DEBUG:
        print(f"[scraper_tecnaria] Inizio indicizzazione da: {root}\n")
    txts = []
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith(".txt"):
                txts.append(os.path.join(dirpath, fn))
    if DEBUG:
        if txts:
            print(f"[scraper_tecnaria] Trovati {len(txts)} file .txt:")
            for p in txts:
                print(f"  - {p}")
        else:
            print("[scraper_tecnaria] Trovati 0 file .txt:")

    total_blocks, total_lines = 0, 0
    for path in txts:
        file = os.path.basename(path)
        try:
            raw = read_file_utf8(path)
        except Exception as e:
            if DEBUG:
                print(f"[scraper_tecnaria] ERRORE lettura {path}: {e}")
            continue

        blocks = detect_tags_and_blocks(raw)
        line_num = 1
        for b in blocks:
            text = b["text"]
            lines = text.count("\n") + 1
            INDEX.append({
                "path": path,
                "file": file,
                "line": line_num,
                "text": text,
                "tags": b.get("tags", []),
                "title": os.path.splitext(file)[0]
            })
            total_blocks += 1
            total_lines += lines
            line_num += lines

    if DEBUG:
        print(f"[scraper_tecnaria] Indicizzati {total_blocks} blocchi / {total_lines} righe da {len(txts)} file.")

# =========================
# Ricerca
# =========================
def _score_block(q: str, blk: dict) -> float:
    kw = keyword_overlap_score(q, blk["text"])
    fz = fuzzy_score(q, blk["text"])
    tagb = tag_boost(blk.get("tags", []), q)
    fileb = file_priority_score(blk.get("file",""), q)
    # combinazione pesata; file e tag sono forti
    score = 0.45*kw + 0.35*fz + tagb + fileb
    return score

def _intent_candidates(question: str):
    qn = normalize_text(question)
    preferred = []
    for intent, keys in INTENT_SYNONYMS.items():
        if any(k in qn for k in keys):
            preferred.extend(INTENT_TO_FILES.get(intent, []))
    # dedup preservando ordine
    seen = set()
    out = []
    for f in preferred:
        fl = f.lower()
        if fl not in seen:
            out.append(fl)
            seen.add(fl)
    return out  # es. ["p560.txt","hbv_chiodatrice.txt",...]

def search_best_answer(question: str, threshold: float = None, topk: int = None):
    """
    Restituisce: {
      "answer": "...",
      "found": bool,
      "from": {"file":..., "path":..., "line":..., "score": ...},
      "score": float,
      "tags": "...",
      "question": domanda_normalizzata
    }
    """
    if not INDEX:
        return {
            "answer": "Indice vuoto. Carica i .txt in documenti_gTab/ e ricarica.",
            "found": False,
            "from": None,
            "score": 0.0,
            "question": "",
        }

    thr = DEFAULT_THRESHOLD if threshold is None else threshold
    k = TOPK_DEFAULT if topk is None else topk

    # Priorità: blocks dei file candidati (se l'intent è chiaro)
    preferred_files = _intent_candidates(question)

    scored = []
    # 1) scorri due volte: prima i preferiti, poi gli altri
    def iter_blocks():
        if preferred_files:
            for blk in INDEX:
                if blk["file"].lower() in preferred_files:
                    yield blk
        for blk in INDEX:
            if blk["file"].lower() not in preferred_files:
                yield blk

    for blk in iter_blocks():
        s = _score_block(question, blk)
        scored.append((s, blk))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:k]

    # prendi il migliore sopra soglia
    for s, blk in top:
        if s >= thr:
            answer = extract_best_snippet(blk["text"], question)
            if DEBUG:
                print(f"[scraper_tecnaria][SEARCH] q='{question}' -> score={s:.3f} {blk['path']}:{blk['line']}")
            return {
                "answer": answer,
                "found": True,
                "from": {"file": blk["file"], "path": blk["path"], "line": blk["line"], "score": round(s,3)},
                "score": round(s,3),
                "tags": ", ".join(blk.get("tags", [])),
                "question": question.strip()
            }

    # Fallback: abbassa soglia una volta
    if thr > 0.24:
        return search_best_answer(question, threshold=0.24, topk=k)

    # Nessun risultato convincente
    if DEBUG and top:
        best_s, best_blk = top[0]
        print(f"[scraper_tecnaria][SEARCH] Nessun blocco sopra soglia ({thr}). Best={best_s:.3f} {best_blk['file']}:{best_blk['line']}")
    return {
        "answer": "Non ho trovato una risposta precisa. Prova a riformulare leggermente la domanda.",
        "found": False,
        "from": None,
        "score": 0.0,
        "question": question.strip()
    }

def extract_best_snippet(block_text: str, question: str) -> str:
    """
    Se il blocco ha coppie D:/R: restituisce la/e R: più vicina/e.
    Altrimenti ritorna il blocco intero (troncato gentilmente).
    """
    # Cerca segmenti Q/A
    qa = re.split(r"\n\s*D\s*:\s*", block_text, flags=re.IGNORECASE)
    if len(qa) > 1:
        best = []
        for seg in qa[1:]:
            parts = re.split(r"\n\s*R\s*:\s*", seg, flags=re.IGNORECASE, maxsplit=1)
            if len(parts) == 2:
                q_txt = parts[0].strip()
                r_txt = parts[1].strip()
                sim = SequenceMatcher(None, normalize_text(question), normalize_text(q_txt)).ratio()
                best.append((sim, r_txt))
        if best:
            best.sort(key=lambda x: x[0], reverse=True)
            # Fonde le risposte con sim > 0.82 (molto vicine) per completezza
            top_sim = best[0][0]
            outs = [r for sim, r in best if sim >= max(0.82, top_sim - 0.03)]
            return "\n".join(outs)

    # Se non è un blocco QA, ritorna testo (gentile truncate)
    txt = block_text.strip()
    # taglia eventuali titoli inutili
    txt = re.sub(r"^\s*\[TAGS:.*?\]\s*\n", "", txt, flags=re.IGNORECASE|re.S).strip()
    # non troncare se non lunghissimo
    if len(txt) <= 1400:
        return txt
    return txt[:1200].rstrip() + "\n…"

# =========================
# Esecuzione diretta
# =========================
if __name__ == "__main__":
    build_index(DOC_DIR)
    print(f"[scraper_tecnaria] Pronto: blocchi={len(INDEX)} file indicizzati.")
