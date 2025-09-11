# scraper_tecnaria.py
import os, re, glob, time
from typing import List, Dict, Any, Tuple

# === Config ===
DOCS_FOLDER = os.environ.get("DOCS_FOLDER", "documenti_gTab")
SIM_THRESHOLD = float(os.environ.get("SIM_THRESHOLD", "0.60"))  # più morbida per domande libere
MAX_RETURN_CHARS = int(os.environ.get("MAX_RETURN_CHARS", "1400"))
DEBUG = os.environ.get("DEBUG_SCRAPER", "1") == "1"

# === Stato indice ===
# Ogni entry è un blocco del tipo:
# {
#   "path": percorso_file, "start_line": int,
#   "tags": "…", "question": "…", "answer": "…",
#   "text_for_match": "tags + question + answer_compatta"
# }
_index: List[Dict[str, Any]] = []
_last_build_info: Dict[str, Any] = {"count": 0, "lines": 0, "blocks": 0, "ts": 0.0}

# === Stopwords (Italiano essenziale) ===
STOPWORDS = {
    "il","lo","la","i","gli","le","un","uno","una","di","del","della","dell","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da","al","allo","ai","agli","alla","alle",
    "che","come","quale","quali","dove","quando","anche","mi","dei","degli","delle","dei"
}
# Nota: non blocchiamo "tecnaria": non è richiesta, ma se appare non danneggia.

_token_pat = re.compile(r"[^a-z0-9àèéìòóùç\-/ ]+", flags=re.IGNORECASE)

def _clean(s: str) -> str:
    s = s.lower()
    s = _token_pat.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _tokenize(s: str) -> List[str]:
    toks = _clean(s).split()
    return [t for t in toks if t not in STOPWORDS]

def _score(q_tokens: List[str], text: str) -> float:
    t_tokens = _tokenize(text)
    if not t_tokens:
        return 0.0
    A, B = set(q_tokens), set(t_tokens)
    inter = len(A & B)
    union = len(A | B) or 1
    return inter / union

def _finish_block(cur: Dict[str, Any], blocks: List[Dict[str, Any]]):
    if not cur:
        return
    # compattiamo risposta e testo per match
    ans = (cur.get("answer") or "").strip()
    if MAX_RETURN_CHARS > 0 and len(ans) > MAX_RETURN_CHARS:
        ans = ans[:MAX_RETURN_CHARS].rstrip() + "…"
    cur["answer"] = ans
    tag = cur.get("tags", "")
    q = cur.get("question", "")
    # per il match usiamo anche un riassunto della risposta (prime 300 chars)
    ans_compact = (cur["answer"][:300] + "…") if len(cur["answer"]) > 300 else cur["answer"]
    cur["text_for_match"] = " ".join([tag, q, ans_compact]).strip()
    if cur.get("question") or cur.get("answer"):
        blocks.append(cur)

def parse_txt_file(path: str) -> List[Dict[str, Any]]:
    """
    Parser robusto per i file formattati:
      [TAGS: ...]
      D: ...
      R: ...
    Supporta separatori '────' e blocchi multipli.
    """
    blocks: List[Dict[str, Any]] = []
    tags = ""
    cur: Dict[str, Any] = {}
    line_no = 0
    total_lines = 0

    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                total_lines += 1
                line_no += 1
                line = raw_line.rstrip("\n")

                if not line.strip():
                    continue

                # TAGS
                if line.strip().startswith("[TAGS"):
                    m = re.search(r"\[TAGS\s*:\s*(.*?)\]$", line.strip(), flags=re.IGNORECASE)
                    tags = m.group(1).strip() if m else line.strip()
                    continue

                # Separatore blocchi
                if set(line.strip()) in [{"─"}, {"-"}, {"_"}, {"·"}]:
                    continue
                if line.strip().startswith("──") or line.strip().startswith("---"):
                    continue

                # Domanda
                if line.lstrip().startswith("D:"):
                    # chiudiamo eventuale blocco precedente
                    _finish_block(cur, blocks)
                    q = line.split("D:", 1)[1].strip()
                    cur = {"path": path, "start_line": line_no, "tags": tags, "question": q, "answer": "" }
                    continue

                # Risposta
                if line.lstrip().startswith("R:"):
                    r = line.split("R:", 1)[1].strip()
                    if "answer" not in cur:
                        cur = {"path": path, "start_line": line_no, "tags": tags, "question": "", "answer": r}
                    else:
                        cur["answer"] = (cur.get("answer","") + ("\n" if cur.get("answer") else "") + r)
                    continue

                # Riga qualunque: se esiste un blocco aperto con domanda ma senza prefisso, trattala come parte della risposta
                if cur:
                    cur["answer"] = (cur.get("answer","") + ("\n" if cur.get("answer") else "") + line)

        # chiudiamo l’ultimo blocco
        _finish_block(cur, blocks)

    except Exception as e:
        print(f"[scraper_tecnaria][WARN] Impossibile leggere/parsing {path}: {e}")

    return blocks, total_lines

def build_index(folder: str = DOCS_FOLDER) -> Dict[str, Any]:
    """Indicizza tutti i .txt in blocchi D:/R: + TAGS."""
    global _index, _last_build_info
    _index = []
    tot_lines = 0
    tot_blocks = 0
    folder_abs = os.path.abspath(folder)
    if DEBUG:
        print(f"[scraper_tecnaria] Inizio indicizzazione da: {folder_abs}")
    files = sorted(glob.glob(os.path.join(folder_abs, "*.txt")))
    if DEBUG:
        print(f"[scraper_tecnaria] Trovati {len(files)} file .txt:")
        for p in files:
            print(f"  - {p}")
    for p in files:
        blocks, lines = parse_txt_file(p)
        tot_lines += lines
        tot_blocks += len(blocks)
        _index.extend(blocks)

    _last_build_info = {"count": len(files), "lines": tot_lines, "blocks": tot_blocks, "ts": time.time()}
    if DEBUG:
        print(f"[scraper_tecnaria] Indicizzati {tot_blocks} blocchi / {tot_lines} righe da {len(files)} file.")
    return _last_build_info

def reload_index() -> Dict[str, Any]:
    """Ricarica l'indice a richiesta (route /reload)."""
    return build_index(DOCS_FOLDER)

def list_index() -> Dict[str, Any]:
    """Elenco file/righe/blocchi (route /ls)."""
    return _last_build_info

def search_best_answer(query: str) -> Dict[str, Any]:
    """Restituisce la MIGLIORE RISPOSTA (non la domanda) sopra soglia."""
    q_tokens = _tokenize(query)
    best: Tuple[int, float] = (-1, -1.0)  # (idx, score)
    for i, blk in enumerate(_index):
        sc = _score(q_tokens, blk["text_for_match"])
        if sc > best[1]:
            best = (i, sc)

    if best[0] < 0:
        return {"found": False, "score": 0.0, "path": None, "line": None, "question": "", "answer": ""}

    blk = _index[best[0]]
    result = {
        "found": best[1] >= SIM_THRESHOLD,
        "score": round(best[1], 3),
        "path": blk["path"],
        "line": blk["start_line"],
        "question": blk.get("question",""),
        "answer": blk.get("answer",""),
        "tags": blk.get("tags","")
    }
    if DEBUG:
        print(f"[scraper_tecnaria][SEARCH] q='{query}' -> score={result['score']} {result['path']}:{result['line']}")
        if not result["found"]:
            print(f"[scraper_tecnaria][SEARCH] Nessun blocco sopra soglia ({SIM_THRESHOLD}).")
    return result
