# scraper_tecnaria.py
# ---------------------------------------------------------
# Indicizza i .txt in documenti_gTab/ e cerca la risposta migliore.
# Restituisce SOLO le risposte (R:), mai le domande (D:).
# ---------------------------------------------------------

from __future__ import annotations
import os
import re
import glob
import time
import unicodedata
from typing import List, Dict, Any, Tuple, Optional
from difflib import SequenceMatcher

# =======================
# Config da ENV
# =======================
DOC_DIR = os.environ.get("DOC_DIR") or os.environ.get("DOCS_FOLDER") or os.environ.get("KNOWLEDGE_DIR") or "./documenti_gTab"
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.28"))  # 0.25–0.30 consigliato
DEBUG = os.environ.get("DEBUG_SCRAPER", "0") in ("1", "true", "True")

# =======================
# Stato indicizzazione
# =======================
INDEX: Dict[str, Any] = {
    "blocks": [],   # lista di dict: {file, path, line, tags, text_raw, answer_only, tokens}
    "files": set(), # set di nomi file base (es: "P560.txt")
    "built_ts": 0.0
}

# =======================
# Costanti/stopwords
# =======================
STOPWORDS = {
    "spa","s.p.a.","il","lo","la","i","gli","le","un","uno","una",
    "di","del","della","dell","dei","degli","delle","e","ed","o","con","per",
    "su","tra","fra","in","da","al","allo","ai","agli","alla","alle","che",
    "dove","quando","anche","come","quale","quali","quanta","quanto","quanti",
    "mi","ti","ci","vi","si","non","piu","meno","dei"
}
SEP_LINE = "────────────────────────────"

# =======================
# Utility
# =======================
def log(msg: str):
    if DEBUG:
        print(f"[scraper_tecnaria] {msg}", flush=True)

def strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', s)
                   if unicodedata.category(c) != 'Mn')

def norm(s: str) -> str:
    s = s.lower()
    s = strip_accents(s)
    s = re.sub(r"[^\w\s\-\/]", " ", s)  # manteniamo - e / per codici
    s = re.sub(r"\s+", " ", s).strip()
    return s

def tokenize(s: str) -> List[str]:
    s = norm(s)
    toks = [t for t in s.split() if t not in STOPWORDS]
    return toks

def jaccard(a: List[str], b: List[str]) -> float:
    if not a or not b:
        return 0.0
    A, B = set(a), set(b)
    inter = len(A & B)
    union = len(A | B)
    return inter / union if union else 0.0

def fuzzy(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, norm(a), norm(b)).ratio()

def extract_tags(head: str) -> List[str]:
    m = re.search(r"\[tags:\s*(.*?)\]", head, flags=re.I | re.S)
    if not m:
        return []
    raw = m.group(1)
    parts = [norm(x) for x in re.split(r"[,;]\s*|\s{2,}", raw) if x.strip()]
    parts = [p for p in parts if p and p not in STOPWORDS]
    return parts

def split_pairs(text: str) -> List[Tuple[str, str, int]]:
    """
    Ritorna lista di tuple (D_text, R_text, start_line).
    Usa SEP_LINE per separare blocchi. Accetta D:/R: multipli.
    """
    lines = text.splitlines()
    results = []
    cur_q, cur_a = [], []
    have_q = False
    start_line = 1
    i = 0

    def flush_pair():
        nonlocal cur_q, cur_a, start_line
        if cur_q or cur_a:
            q = "\n".join(cur_q).strip()
            a = "\n".join(cur_a).strip()
            if q or a:
                results.append((q, a, start_line))
        cur_q, cur_a = [], []

    while i < len(lines):
        line = lines[i]
        if SEP_LINE in line:
            flush_pair()
            have_q = False
            start_line = i + 2
            i += 1
            continue

        mD = re.match(r"^\s*D:\s*(.*)$", line, flags=re.I)
        mR = re.match(r"^\s*R:\s*(.*)$", line, flags=re.I)
        if mD:
            if cur_q or cur_a:
                flush_pair()
            have_q = True
            start_line = i + 1
            cur_q = [mD.group(1).strip()]
            cur_a = []
        elif mR:
            if not have_q and (cur_q or cur_a):
                flush_pair()
                start_line = i + 1
            have_q = True
            cur_a.append(mR.group(1).strip())
        else:
            if have_q:
                cur_a.append(line.strip())
        i += 1

    flush_pair()
    return results

# =======================
# Parser dei file
# =======================
def parse_file_to_blocks(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        log(f"[PARSE][ERRORE] {path}: {e}")
        return []

    filename = os.path.basename(path)

    # TAGS globali (prima del primo separatore)
    head_match = re.match(r"(?s)^(.*?)\n"+re.escape(SEP_LINE), content.strip())
    head_text = head_match.group(1) if head_match else ""
    global_tags = extract_tags(head_text) if head_text else []

    pairs = split_pairs(content)
    blocks = []

    for (q_text, a_text, start_line) in pairs:
        local_tags = extract_tags(q_text + "\n" + a_text)
        all_tags = list(dict.fromkeys(global_tags + local_tags))  # uniq + order

        tokens = tokenize(" ".join([q_text, a_text] + all_tags))
        answer_only = a_text.strip()
        text_raw = (q_text + "\n" + a_text).strip()

        blocks.append({
            "file": filename,
            "path": path,
            "line": start_line,
            "tags": all_tags,
            "text_raw": text_raw,
            "answer_only": answer_only,
            "tokens": tokens
        })

    return blocks

# =======================
# Routing verso file giusti
# =======================
def allowed_files_for(question: str, all_files: set) -> List[str]:
    q = norm(question)

    # Intent espliciti
    if re.search(r"\bp\s*560\b|\bp560\b", q):
        lst = [f for f in ("P560.txt","HBV_Chiodatrice.txt") if f in all_files]
        if lst: return lst

    if re.search(r"(contatti|telefono|e-?mail|email|mail|indirizzo|sede|orari|apertura|chiusura)", q):
        lst = [f for f in ("ChiSiamo_ContattiOrari.txt",) if f in all_files]
        if lst: return lst

    if re.search(
        r"(chi\s*(?:è|e)\s*tecnaria|parlami\s+di\s+tecnaria|mi\s+parli\s+di\s+tecnaria|chi\s+siete|profilo\s+aziendale|azienda\s+tecnaria|storia|valori|mission|vision)",
        q
    ):
        pref = [x for x in sorted(all_files) if x.startswith("ChiSiamo_")]
        if pref: return pref

    prefs: List[str] = []
    def add_if_present(names):
        for n in names:
            if n in all_files and n not in prefs:
                prefs.append(n)

    if re.search(r"\bhbv\b", q):           add_if_present(["HBV.txt","X-HBV.txt"])
    if re.search(r"x[-\s]?hbv", q):        add_if_present(["X-HBV.txt","HBV.txt"])
    if re.search(r"\bctf\b", q) or "diapason" in q:
                                           add_if_present(["CTF.txt","Diapason.txt"])
    if re.search(r"\bmini\s*cem[-\s]?e\b|\bmini\s*cem\b", q):
                                           add_if_present(["MINI_CEM-E.txt","CEM-E.txt","Schede_Tecniche.txt"])
    if re.search(r"\bcem[-\s]?e\b", q):    add_if_present(["CEM-E.txt","MINI_CEM-E.txt","Schede_Tecniche.txt"])
    if re.search(r"(ctl|legno.*calcestruzzo|omega|tirafondo)", q):
                                           add_if_present(["CTL.txt"])
    if re.search(r"\bfva-?l\b", q):        add_if_present(["FVA-L.txt","FVA.txt"])
    if re.search(r"\bfva\b", q):           add_if_present(["FVA.txt","FVA-L.txt"])
    if re.search(r"(manuali|posa|istruzioni)", q):
                                           add_if_present(["Manuali_di_Posa.txt"])
    if re.search(r"(capitolat|computi|voci)", q):
                                           add_if_present(["Capitolati_e_Computi.txt"])
    if re.search(r"(qualit|iso\s*9001)", q):
                                           add_if_present(["Qualita_ISO9001.txt"])
    if re.search(r"(tracciabilit|lotti|lotto)", q):
                                           add_if_present(["Tracciabilita_Lotti.txt"])
    if re.search(r"(schede\s*tecnich|scheda\s*tecnica)", q):
                                           add_if_present(["Schede_Tecniche.txt"])
    if re.search(r"\bfaq\b", q):
                                           add_if_present(["FAQ_Generali.txt"])
    if re.search(r"(formazione|corso|webinar)", q):
                                           add_if_present(["Formazione.txt"])
    if re.search(r"(assistenza|cantiere|supporto)", q):
                                           add_if_present(["Assistenza_Cantiere.txt","Supporto_Tecnico.txt"])
    if re.search(r"(logistica|spedizion)", q):
                                           add_if_present(["Logistica_Spedizioni.txt"])
    if re.search(r"(normativ|ntc|eurocod)", q):
                                           add_if_present(["Normative_Riferimento.txt"])
    if re.search(r"(relazioni.*calcolo|verifiche|esempi\s*calcolo)", q):
                                           add_if_present(["Relazioni_di_Calcolo.txt"])
    if re.search(r"(codici|elenco\s+prodotti|catalogo|tutti\s+i\s+connettori)", q):
                                           add_if_present(["Prodotti_Elenco.txt","Schede_Tecniche.txt"])

    return prefs  # se vuoto → useremo tutti i file

# =======================
# Indicizzazione
# =======================
def build_index(doc_dir: str = None) -> Dict[str, int]:
    """
    Scansiona doc_dir per *.txt, crea blocchi Q/A e aggiorna INDEX.
    Ritorna: {"blocks":N, "files":N, "lines":M}
    """
    base = doc_dir or DOC_DIR
    base = os.path.abspath(base)
    log(f"Inizio indicizzazione da: {base}\n")

    paths = sorted(glob.glob(os.path.join(base, "*.txt")))
    if not paths:
        log("Trovati 0 file .txt:")
        INDEX["blocks"] = []
        INDEX["files"] = set()
        INDEX["built_ts"] = time.time()
        return {"blocks": 0, "files": 0, "lines": 0}

    log(f"Trovati {len(paths)} file .txt:")
    for p in paths:
        print("  - " + p, flush=True)

    all_blocks: List[Dict[str, Any]] = []
    total_lines = 0
    files_set = set()

    for p in paths:
        filename = os.path.basename(p)
        files_set.add(filename)
        blocks = parse_file_to_blocks(p)
        all_blocks.extend(blocks)
        try:
            with open(p, "r", encoding="utf-8") as f:
                total_lines += sum(1 for _ in f)
        except:
            pass

    INDEX["blocks"] = all_blocks
    INDEX["files"] = files_set
    INDEX["built_ts"] = time.time()

    log(f"Indicizzati {len(all_blocks)} blocchi / {total_lines} righe da {len(files_set)} file.")
    return {"blocks": len(all_blocks), "files": len(files_set), "lines": total_lines}

# =======================
# Ranking / Ricerca
# =======================
def score_block(query: str, block: Dict[str, Any]) -> float:
    """
    Ranking ibrido: jaccard sui token + fuzzy full-text + piccoli boost su tag/filename.
    """
    qtoks = tokenize(query)
    btoks = block.get("tokens", [])
    base = 0.0

    base += 0.65 * jaccard(qtoks, btoks)
    base += 0.35 * fuzzy(query, block.get("text_raw", ""))

    fn = norm(block.get("file", ""))
    tags = " ".join(block.get("tags", []))
    if any(t in tags for t in qtoks):
        base += 0.05
    if any(t in fn for t in qtoks):
        base += 0.05

    return max(0.0, min(1.0, base))

def pick_best_block(query: str, candidate_blocks: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], float]:
    best, best_score = None, 0.0
    for b in candidate_blocks:
        s = score_block(query, b)
        if s > best_score:
            best, best_score = b, s
    return best, best_score

def search_best_answer(question: str) -> Dict[str, Any]:
    """
    Output:
    {
      found: bool, answer: str, score: float,
      path: str|None, line: int|None, question: "", tags: str
    }
    """
    if not INDEX["blocks"]:
        return {
            "found": False, "answer": "", "score": 0,
            "path": None, "line": None, "question": "",
            "tags": "", "error": "Indice vuoto"
        }

    all_files = INDEX["files"]
    allowed = allowed_files_for(question, all_files)
    if allowed:
        cand = [b for b in INDEX["blocks"] if b["file"] in allowed]
        if DEBUG:
            log(f"[SEARCH][ROUTER] '{question}' -> files={allowed} (cand={len(cand)})")
    else:
        cand = INDEX["blocks"]
        if DEBUG:
            log(f"[SEARCH][ROUTER] '{question}' -> all files (cand={len(cand)})")

    if not cand:
        return {
            "found": False, "answer": "", "score": 0,
            "path": None, "line": None, "question": "",
            "tags": "", "error": "Nessun candidato"
        }

    best, score = pick_best_block(question, cand)

    if DEBUG:
        if best:
            log(f"[SEARCH] q='{question}' -> score={score:.3f} {best['path']}:{best['line']}")
        else:
            log(f"[SEARCH] q='{question}' -> nessun match")

    if not best or score < SIM_THRESHOLD:
        return {
            "found": False,
            "answer": "",
            "score": float(f"{score:.3f}"),
            "path": best["path"] if best else None,
            "line": best["line"] if best else None,
            "question": "",
            "tags": " ".join(best["tags"]) if best else ""
        }

    answer = best.get("answer_only", "").strip()
    if not answer:
        raw = best.get("text_raw", "")
        lines = []
        for ln in raw.splitlines():
            m = re.match(r"^\s*R:\s*(.*)$", ln, flags=re.I)
            if m:
                lines.append(m.group(1).strip())
        answer = "\n".join(lines).strip() if lines else raw

    answer = answer.replace(SEP_LINE, "").strip()

    return {
        "found": True,
        "answer": answer,
        "score": float(f"{score:.3f}"),
        "path": best["path"],
        "line": best["line"],
        "question": "",
        "tags": " ".join(best["tags"])
    }

# Alias retro-compatibilità
def search_answer(q: str) -> Dict[str, Any]:
    return search_best_answer(q)
