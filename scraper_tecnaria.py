# scraper_tecnaria.py
# Indicizza tutti i .txt in documenti_gTab/ e risponde con la "migliore risposta"
# usando routing per intento + ranking semplice (keyword overlap + boost filename/tag).

import os, re, json, glob
from collections import Counter, defaultdict
from typing import List, Dict, Any, Tuple

# =========================
# Config da ENV (con default)
# =========================
DOC_DIR = os.environ.get("DOC_DIR") or os.environ.get("DOCS_FOLDER") or os.environ.get("KNOWLEDGE_DIR") or "./documenti_gTab"
SIMILARITY_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.28"))
TOPK_SEMANTIC = int(os.environ.get("TOPK_SEMANTIC", "20"))
MIN_CHARS_PER_CHUNK = int(os.environ.get("MIN_CHARS_PER_CHUNK", "180"))
OVERLAP_CHARS = int(os.environ.get("OVERLAP_CHARS", "50"))
DEBUG_SCRAPER = os.environ.get("DEBUG_SCRAPER", "0") == "1"

STOPWORDS = {
    "tecnaria","spa","s.p.a.","il","lo","la","i","gli","le","un","uno","una",
    "di","del","della","dell","dei","degli","delle","e","ed","o","con","per",
    "su","tra","fra","in","da","al","allo","ai","agli","alla","alle","che",
    "come","quale","quali","dove","quando","anche","mi","dei","dai","dalle",
    "dalla","agli","agli","degli","dei","dello","dalla","dell'","dell", "de"
}

# =========================
# Stato globale dell'indice
# =========================
INDEX: List[Dict[str, Any]] = []   # lista di blocchi
FILES: List[str] = []              # nomi file indicizzati

# =========================
# Utilità testuali
# =========================
def norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^\wàèéìòóùç\-_/ ]+", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def tokenize(s: str) -> List[str]:
    return [t for t in re.split(r"[^\wàèéìòóùç\-]+", norm(s)) if t and t not in STOPWORDS]

def any_in(text: str, words: List[str]) -> bool:
    t = norm(text)
    return any(w in t for w in words)

# =========================
# Parsing blocchi dai .txt
# =========================
DELIM_RE = re.compile(r"^\s*[\u2500\-_=]{8,}\s*$")  # linee di separatori "─────" o "------"

def extract_tags(lines: List[str]) -> List[str]:
    if not lines:
        return []
    first = lines[0].strip()
    m = re.match(r"^\s*\[TAGS\s*:\s*(.+?)\s*\]\s*$", first, flags=re.IGNORECASE)
    if not m:
        return []
    # split per virgola
    tags = [norm(x) for x in m.group(1).split(",")]
    tags = [t for t in tags if t]
    return tags

def extract_only_answers(block_text: str) -> str:
    """
    Restituisce SOLO le risposte (linee che iniziano con 'R:' o che seguono 'R:' nelle righe),
    altrimenti l'intero blocco se non ci sono 'R:'.
    """
    out = []
    has_r = False
    for line in block_text.splitlines():
        if re.match(r"^\s*R\s*:", line, flags=re.IGNORECASE):
            # prendi tutto dopo 'R:'
            has_r = True
            out.append(re.sub(r"^\s*R\s*:\s*", "", line, flags=re.IGNORECASE))
        # se ci sono righe successive senza D: in un blocco "risposta", le tengo
        elif has_r and not re.match(r"^\s*D\s*:", line, flags=re.IGNORECASE):
            out.append(line)
    if has_r:
        text = "\n".join([l.rstrip() for l in out]).strip()
        # Ripulisci eventuali residui di separatori
        text = re.sub(DELIM_RE, "", text).strip()
        return text
    # fallback: togli eventuali D:
    cleaned = []
    for line in block_text.splitlines():
        if re.match(r"^\s*D\s*:", line, flags=re.IGNORECASE):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()

def split_blocks_from_text(text: str) -> List[str]:
    """
    Se il testo contiene separatori '────', splitta lì. Altrimenti usa doppie righe vuote come boundary.
    """
    lines = text.splitlines()
    blocks = []
    current = []
    for ln in lines:
        if DELIM_RE.match(ln):
            if current:
                blocks.append("\n".join(current).strip())
                current = []
        else:
            current.append(ln)
    if current:
        blocks.append("\n".join(current).strip())
    # Se è troppo poco segmentato, prova a spezzare per paragrafi
    if len(blocks) <= 1:
        chunks = []
        para = []
        for ln in lines:
            if ln.strip() == "":
                if para:
                    p = "\n".join(para).strip()
                    if p:
                        chunks.append(p)
                    para = []
            else:
                para.append(ln)
        if para:
            chunks.append("\n".join(para).strip())
        # riaccorpa paragrafi corti
        if chunks:
            acc, buf = [], ""
            for c in chunks:
                if len(buf) < MIN_CHARS_PER_CHUNK:
                    buf = (buf + "\n\n" + c).strip()
                else:
                    acc.append(buf)
                    buf = c
            if buf:
                acc.append(buf)
            # usa acc se più favorevole
            if len(acc) > len(blocks):
                return acc
    return blocks

# =========================
# Indicizzazione
# =========================
def build_index(doc_dir: str = None) -> Dict[str, Any]:
    """Indicizza TUTTI i .txt in documenti_gTab/."""
    global INDEX, FILES
    doc_dir = doc_dir or DOC_DIR
    path = os.path.abspath(doc_dir)
    if DEBUG_SCRAPER:
        print(f"[scraper_tecnaria] Inizio indicizzazione da: {path}\n")
    INDEX = []
    FILES = []

    # Trova file
    files = sorted(glob.glob(os.path.join(path, "*.txt")))
    if DEBUG_SCRAPER:
        print(f"[scraper_tecnaria] Trovati {len(files)} file .txt:")
        for f in files:
            print(f"  - {f}")
    FILES = [os.path.basename(f) for f in files]

    # Legge e splitta
    total_lines = 0
    total_blocks = 0
    for fpath in files:
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                raw = fh.read()
        except:
            # tenta latin-1
            with open(fpath, "r", encoding="latin-1") as fh:
                raw = fh.read()

        blocks = split_blocks_from_text(raw)
        fn = os.path.basename(fpath)
        # tags dal 1° blocco/righe iniziali
        first_lines = raw.splitlines()[:2]
        tags = extract_tags(first_lines)

        line_no = 1
        for blk in blocks:
            if not blk.strip():
                continue
            # estrai SOLO RISPOSTE
            answer_text = extract_only_answers(blk)
            # token
            toks = tokenize(answer_text)
            # salva
            INDEX.append({
                "file": fn,
                "line": line_no,
                "text": answer_text,
                "norm": norm(answer_text),
                "tokens": toks,
                "tags": tags
            })
            # avanza contatori
            total_blocks += 1
            line_no += max(1, answer_text.count("\n") + 1)
            total_lines += max(1, answer_text.count("\n") + 1)

    if DEBUG_SCRAPER:
        print(f"[scraper_tecnaria] Indicizzati {total_blocks} blocchi / {total_lines} righe da {len(files)} file.")
    return {"status":"ok","files":len(files),"blocks":total_blocks,"lines":total_lines, "doc_dir": path}


# =========================
# Router d’intento
# =========================
def allowed_files_for(question: str, all_files: set) -> List[str]:
    """Instrada la domanda verso un sottoinsieme di file in base all'intento."""
    q = norm(question)

    # HARD intents
    if re.search(r"\bp\s*560\b|\bp560\b", q):
        hard = [f for f in ("P560.txt","HBV_Chiodatrice.txt") if f in all_files]
        if hard:
            return hard

    if re.search(r"(contatti|telefono|e-?mail|email|mail|indirizzo|sede|orari|apertura|chiusura)", q):
        hard = [f for f in ("ChiSiamo_ContattiOrari.txt",) if f in all_files]
        if hard:
            return hard

    if re.search(r"(chi\s*(?:è|e)\s*tecnaria|parlami\s+di\s+tecnaria|chi\s+siete|profilo\s+aziendale)", q):
        pref = [x for x in sorted(all_files) if x.startswith("ChiSiamo_")]
        if pref:
            return pref

    prefs = []
    def add_if_present(names):
        for n in names:
            if n in all_files and n not in prefs:
                prefs.append(n)

    if re.search(r"\bhbv\b", q):
        add_if_present(["HBV.txt","X-HBV.txt"])
    if re.search(r"x[-\s]?hbv", q):
        add_if_present(["X-HBV.txt","HBV.txt"])
    if re.search(r"\bctf\b", q) or "diapason" in q:
        add_if_present(["CTF.txt","Diapason.txt"])
    if re.search(r"\bmini\s*cem[-\s]?e\b|\bmini\s*cem\b", q):
        add_if_present(["MINI_CEM-E.txt","CEM-E.txt","Schede_Tecniche.txt"])
    if re.search(r"\bcem[-\s]?e\b", q):
        add_if_present(["CEM-E.txt","MINI_CEM-E.txt","Schede_Tecniche.txt"])
    if re.search(r"(ctl|legno.*calcestruzzo|omega|tirafondo)", q):
        add_if_present(["CTL.txt"])
    if re.search(r"\bfva-?l\b", q):
        add_if_present(["FVA-L.txt","FVA.txt"])
    if re.search(r"\bfva\b", q):
        add_if_present(["FVA.txt","FVA-L.txt"])
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

    return prefs  # può essere vuoto: significa "nessun filtro"

# =========================
# Scoring blocchi
# =========================
def jaccard(a: List[str], b: List[str]) -> float:
    if not a or not b: 
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    uni = len(sa | sb)
    if uni == 0:
        uni = 1
    return inter/uni

def score_block(q: str, blk: Dict[str, Any]) -> float:
    qn = norm(q)
    qtok = tokenize(q)
    if not qtok:
        return 0.0

    # base overlap
    base = jaccard(qtok, blk.get("tokens", []))

    # tag boost
    boost_tag = 0.0
    tags = blk.get("tags", []) or []
    if tags:
        for t in tags:
            if t and t in qn:
                boost_tag += 0.10

    # filename boost
    boost_file = 0.0
    fn = blk.get("file","").lower()

    # P560 deve vincere su corporate
    if ("p560" in qn or re.search(r"\bp\s*560\b", qn)) and "p560.txt" in fn:
        boost_file += 0.60
    if ("p560" in qn or re.search(r"\bp\s*560\b", qn)) and "hbv_chiodatrice.txt" in fn:
        boost_file += 0.35

    if re.search(r"\bctf\b", qn) and "ctf" in fn:
        boost_file += 0.30
    if re.search(r"\bcem[-\s]?e\b", qn) and "cem-e" in fn:
        boost_file += 0.30
    if "mini" in qn and "mini_cem-e" in fn:
        boost_file += 0.25
    if "diapason" in qn and "diapason" in fn:
        boost_file += 0.30
    if re.search(r"\bhbv\b", qn) and "hbv" in fn and "chiodatrice" not in fn:
        boost_file += 0.25
    if "chiodatrice" in qn and "hbv_chiodatrice" in fn:
        boost_file += 0.30
    if "legno" in qn and "ctl" in fn:
        boost_file += 0.22
    if any_in(q, ["contatti","orari","telefono","email","sede","indirizzo"]) and "contattiorari" in fn:
        boost_file += 0.45
    if any_in(q, ["chi siete","chi è tecnaria","parlami di tecnaria","profilo","chi e tecnaria"]) and fn.startswith("chisiamo"):
        boost_file += 0.50
    if any_in(q, ["codici","elenco prodotti","catalogo","tutti i connettori"]) and "prodotti_elenco" in fn:
        boost_file += 0.40

    return base + boost_tag + boost_file

# =========================
# Ricerca principale
# =========================
def search_best_answer(question: str) -> Dict[str, Any]:
    """
    Restituisce il blocco migliore (solo RISPOSTA), con debug.
    """
    if DEBUG_SCRAPER:
        print(f"[scraper_tecnaria][SEARCH] q='{question}'")

    if not INDEX:
        return {"found": False, "score": 0.0, "answer": "", "path": None, "line": None, "question": None, "tags": ""}

    # Limita i candidati in base all'intento
    files_set = set(FILES)
    preferred = allowed_files_for(question, files_set)
    candidates = [b for b in INDEX if (not preferred or b["file"] in preferred)]

    # Se nessun candidato (per qualche motivo), usa tutto l'indice
    if not candidates:
        candidates = INDEX

    # Rank
    scored = []
    for blk in candidates:
        s = score_block(question, blk)
        scored.append((s, blk))
    scored.sort(key=lambda x: x[0], reverse=True)

    if DEBUG_SCRAPER and scored:
        best_s, best_b = scored[0]
        print(f"[scraper_tecnaria][SEARCH] best={best_s:.3f} {best_b['file']}:{best_b['line']}")

    # Applica soglia
    if not scored or scored[0][0] < SIMILARITY_THRESHOLD:
        return {
            "found": False,
            "score": (scored[0][0] if scored else 0.0),
            "answer": "",
            "path": (scored[0][1]["file"] if scored else None),
            "line": (scored[0][1]["line"] if scored else None),
            "question": "",
            "tags": ""
        }

    top_score, top_blk = scored[0]
    ans = top_blk.get("text","").strip()
    # Finitura: togli doppie righe
    ans = re.sub(r"\n{3,}", "\n\n", ans).strip()

    return {
        "found": True,
        "score": round(float(top_score), 3),
        "answer": ans,
        "path": top_blk.get("file"),
        "line": top_blk.get("line"),
        "question": "",  # non mostriamo Q:
        "tags": ", ".join(top_blk.get("tags", [])) if top_blk.get("tags") else ""
    }

# alias retro-compatibile
def search_answer(question: str) -> Dict[str, Any]:
    return search_best_answer(question)
