# -*- coding: utf-8 -*-
"""
scraper_tecnaria.py
- Indicizza tutti i .txt / .TXT in documenti_gTab (o cartella da ENV DOC_DIR)
- Estrae blocchi Q/A stile:
    [TAGS: tag1, tag2]
    D: domanda...
    R: risposta...
  ma funziona anche su testo libero (usa il paragrafo come blocco).
- Retrieval ibrido: BM25 (rank_bm25) + fuzzy (rapidfuzz) + TAG/file boost + (opzionale) embeddings
- Selettore "Sinapsi": semaforo GREEN/YELLOW/RED + gating con EDGE/margine
- API pubbliche:
    - build_index(doc_dir)       → costruisce l'indice
    - is_ready()                 → bool (ci sono documenti?)
    - search_best_answer(q, ...) → dict(answer, found, score, from, tags)
    - INDEX                      → stato globale
"""

from __future__ import annotations
import os, re, unicodedata, math, json
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict

# --- Dipendenze obbligatorie (leggere) ---
from rapidfuzz import fuzz, process
from rank_bm25 import BM25Okapi

# --- Embedding opzionali (auto-disattivati se mancano) ---
_HAS_EMB = False
_EMB_MODEL = None
try:
    from sentence_transformers import SentenceTransformer, util as st_util
    _HAS_EMB = True
except Exception:
    _HAS_EMB = False
    SentenceTransformer = None
    st_util = None

# =========================
# Config da ENV (tunable)
# =========================
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")

# Pesi componenti (0..1)
W_BM25   = float(os.environ.get("W_BM25",   "0.55"))
W_FUZZY  = float(os.environ.get("W_FUZZY",  "0.20"))
W_TAG    = float(os.environ.get("W_TAG",    "0.15"))
W_EMB    = float(os.environ.get("W_EMB",    "0.30"))

# Soglie Sinapsi
SINAPSI_GREEN  = float(os.environ.get("SINAPSI_GREEN",  "0.60"))  # punteggio assoluto per GREEN
SINAPSI_YELLOW = float(os.environ.get("SINAPSI_YELLOW", "0.40"))  # per YELLOW
EDGE_MIN       = float(os.environ.get("EDGE_MIN",       "0.08"))  # margine top1-top2

# Fallback soglia / topK richiesti dall'app
DEFAULT_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
DEFAULT_TOPK      = int(os.environ.get("TOPK_SEMANTIC", "20"))

# Debug esteso
DEBUG = os.environ.get("DEBUG_SCRAPER", "1") == "1"

# =========================
# Stopwords + normalizzazione
# =========================
STOPWORDS_MIN = {
    "il","lo","la","i","gli","le","un","uno","una",
    "di","del","della","dei","degli","delle",
    "e","ed","o","con","per","su","tra","fra","in","da",
    "al","allo","ai","agli","alla","alle",
    "che","come","dove","quando","anche",
    "mi","ti","si","ci","vi","a","da","de","dal","dall","dalla","dalle",
    "un'", "l'", "d'", "all'", "nell'", "sull'", "dell'",
}

def normalize_text(s: str) -> str:
    # minuscole
    s = s.lower()
    # rimuovi accenti
    s = ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))
    # apostrofi tipici
    s = s.replace("’", "'")
    # separa non-alfanumerico
    s = re.sub(r"[^a-z0-9]+", " ", s)
    # stopwords minime
    toks = [t for t in s.split() if t and t not in STOPWORDS_MIN]
    return " ".join(toks)

# =========================
# Sinonimi/alias semplici
# =========================
SYNONYMS = {
    "p560": ["p 560", "p-560", "pistola spit", "sparachiodi p560", "spit p560"],
    "contatti": ["telefono", "mail", "email", "indirizzo", "contattare", "assistenza"],
    "chi siamo": ["chi e tecnaria", "chi è tecnaria", "azienda", "profilo aziendale", "mission", "vision", "storia"],
    "hbv": ["chiodatrice", "pistola", "hbv chiodatrice"],
    "disegni": ["manuali", "manuale posa", "relazioni di calcolo", "schede tecniche"],
    "ctf": ["connettore ctf", "piolo su piastra", "acciaio calcestruzzo"],
    "cem-e": ["cem e", "ripresa di getto", "calcestruzzo calcestruzzo"],
    "mini cem-e": ["mini cem e", "spessori 20 40"],
    "diapason": ["connettore diapason"],
}

def expand_with_synonyms(q: str) -> List[str]:
    nq = normalize_text(q)
    out = [q, nq]
    for k, vals in SYNONYMS.items():
        if k in nq or any(v in nq for v in vals):
            out.extend([k] + vals)
    # rimuovi duplicati preservando ordine
    seen=set(); dedup=[]
    for s in out:
        if s not in seen:
            seen.add(s); dedup.append(s)
    return dedup

# =========================
# Prior di famiglia (bias file)
# =========================
PRIORS = {
    "p560":                {"files": ["P560.txt", "HBV_Chiodatrice.txt"], "boost": 0.18},
    "contatti":            {"files": ["ChiSiamo_ContattiOrari.txt"],      "boost": 0.20},
    "chi siamo":           {"files": ["ChiSiamo_ProfiloAziendale.txt","ChiSiamo_VisionMission.txt"], "boost": 0.16},
    "ctf":                 {"files": ["CTF.txt"], "boost": 0.14},
    "cem-e":               {"files": ["CEM-E.txt","MINI_CEM-E.txt"], "boost": 0.14},
    "mini cem-e":          {"files": ["MINI_CEM-E.txt"], "boost": 0.18},
    "diapason":            {"files": ["Diapason.txt"], "boost": 0.14},
    "qualita":             {"files": ["Qualita_ISO9001.txt"], "boost": 0.12},
    "spedizioni":          {"files": ["Logistica_Spedizioni.txt"], "boost": 0.12},
    "manuale posa":        {"files": ["Manuali_di_Posa.txt"], "boost": 0.12},
    "schede tecniche":     {"files": ["Schede_Tecniche.txt"], "boost": 0.12},
    "relazioni di calcolo":{"files": ["Relazioni_di_Calcolo.txt"], "boost": 0.12},
}

def prior_boost(q_norm: str, filename: str) -> float:
    b = 0.0
    for key, conf in PRIORS.items():
        if key in q_norm:
            if any(filename.endswith(f) for f in conf["files"]):
                b = max(b, conf["boost"])
    return b

# =========================
# Parsing file TXT → blocchi
# =========================
QA_TAGS_RE = re.compile(r"^\s*\[tags\s*:\s*(.*?)\]\s*$", re.IGNORECASE)
Q_RE = re.compile(r"^\s*d\s*:\s*(.*)$", re.IGNORECASE)
A_RE = re.compile(r"^\s*r\s*:\s*(.*)$", re.IGNORECASE)

def parse_txt_to_blocks(path: str) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        # tenta latin-1 in emergenza
        with open(path, "r", encoding="latin-1", errors="ignore") as f:
            raw = f.read()

    # spezza per sezioni vuote o separatori
    parts = re.split(r"(?:\n-{3,}\n|\n={3,}\n|\n\s*\n)", raw)

    current_tags = []
    for part in parts:
        lines = [ln.strip() for ln in part.strip().splitlines() if ln.strip()]

        if not lines:
            continue

        tags_local = []
        q_text = None
        a_text = None
        # prima passata: se ha struttura TAG/Q/R
        for ln in lines:
            mtag = QA_TAGS_RE.match(ln)
            if mtag:
                tags_local = [t.strip() for t in mtag.group(1).split(",") if t.strip()]
                continue
            mq = Q_RE.match(ln)
            if mq:
                q_text = mq.group(1).strip()
                continue
            ma = A_RE.match(ln)
            if ma:
                a_text = ma.group(1).strip()
                continue

        if q_text or a_text:
            blocks.append({
                "question": q_text or "",
                "answer": a_text or "",
                "raw": part.strip(),
                "tags": tags_local,
            })
        else:
            # testo libero → crea blocco con raw; domanda vuota; risposta = paragrafo
            blocks.append({
                "question": "",
                "answer": "",
                "raw": part.strip(),
                "tags": [],
            })
    # fallback: se nessun blocco trovato, crea unico blocco con tutto
    if not blocks and raw.strip():
        blocks = [{
            "question": "",
            "answer": "",
            "raw": raw.strip(),
            "tags": [],
        }]
    return blocks

# =========================
# INDEX globale
# =========================
INDEX: Dict[str, Any] = {
    "docs": [],     # lista di blocchi con metadata
    "bm25": None,   # BM25Okapi
    "emb": None,    # embedding matrix opzionale
    "model": None,  # modello SentenceTransformer
}

def _scan_files(doc_dir: str) -> List[str]:
    found = []
    doc_dir_abs = os.path.abspath(doc_dir)
    for root, _, files in os.walk(doc_dir_abs):
        for fn in files:
            if fn.lower().endswith(".txt"):
                found.append(os.path.join(root, fn))
    found.sort()
    if DEBUG:
        print(f"[scraper_tecnaria] Inizio indicizzazione da: {doc_dir_abs}\n[scraper_tecnaria] Trovati {len(found)} file .txt:")
        for p in found:
            print(f"[scraper_tecnaria]   - {p}")
    return found

def _prepare_bm25_corpus(docs: List[Dict[str, Any]]) -> Tuple[BM25Okapi, List[List[str]]]:
    tokenized_corpus = []
    for d in docs:
        txt = " ".join(filter(None, [
            d.get("question",""),
            d.get("answer",""),
            d.get("raw",""),
            " ".join(d.get("tags",[]))
        ]))
        tokenized_corpus.append(normalize_text(txt).split())
    bm25 = BM25Okapi(tokenized_corpus)
    return bm25, tokenized_corpus

def _maybe_load_embeddings(docs: List[Dict[str, Any]]) -> Tuple[Optional[Any], Optional[Any]]:
    if not _HAS_EMB:
        if DEBUG: print("[scraper_tecnaria][EMB] sentence-transformers non disponibile: uso solo keyword/fuzzy.")
        return None, None
    try:
        model_name = os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2")
        model = SentenceTransformer(model_name)
        texts = []
        for d in docs:
            txt = " ".join(filter(None, [d.get("question",""), d.get("answer",""), d.get("raw","")]))
            texts.append(txt[:1000])  # limite soft
        emb = model.encode(texts, convert_to_tensor=True, normalize_embeddings=True)
        if DEBUG: print("[scraper_tecnaria][EMB] Caricati embeddings per", len(docs), "blocchi.")
        return model, emb
    except Exception as e:
        if DEBUG: print("[scraper_tecnaria][EMB][WARN]", repr(e), "→ disattivo embeddings.")
        return None, None

def build_index(doc_dir: Optional[str]=None) -> None:
    """Costruisce l'indice globale."""
    global INDEX
    doc_dir = doc_dir or DOC_DIR
    files = _scan_files(doc_dir)

    docs: List[Dict[str, Any]] = []
    total_blocks, total_lines = 0, 0

    for path in files:
        blocks = parse_txt_to_blocks(path)
        for b in blocks:
            total_blocks += 1
            text_for_count = " ".join([b.get("question",""), b.get("answer",""), b.get("raw","")]).strip()
            line_count = text_for_count.count("\n") + 1 if text_for_count else 0
            total_lines += line_count

            docs.append({
                "file": os.path.basename(path),
                "file_path": path,
                "question": b.get("question",""),
                "answer": b.get("answer",""),
                "raw": b.get("raw",""),
                "tags": b.get("tags",[]),
                # campi normalizzati di supporto
                "_q_norm": normalize_text(b.get("question","")),
                "_a_norm": normalize_text(b.get("answer","")),
                "_raw_norm": normalize_text(b.get("raw","")),
                "_tags_norm": [normalize_text(t) for t in b.get("tags",[])],
            })

    if DEBUG:
        print(f"[scraper_tecnaria] Indicizzati {total_blocks} blocchi / {total_lines} righe da {len(files)} file.")

    bm25, _ = _prepare_bm25_corpus(docs)
    model, emb = _maybe_load_embeddings(docs)

    INDEX["docs"] = docs
    INDEX["bm25"] = bm25
    INDEX["model"] = model
    INDEX["emb"] = emb

def is_ready() -> bool:
    try:
        return bool(INDEX and INDEX.get("docs") and len(INDEX["docs"])>0 and INDEX.get("bm25") is not None)
    except Exception:
        return False

# =========================
# Scoring componenti
# =========================
def _score_bm25(query: str) -> List[float]:
    bm25: BM25Okapi = INDEX["bm25"]
    toks = normalize_text(query).split()
    if not toks:
        return [0.0]*len(INDEX["docs"])
    scores = bm25.get_scores(toks)
    # normalizza 0..1
    if not scores.any():
        return [0.0]*len(scores)
    mx = float(scores.max())
    return [float(s)/mx if mx>0 else 0.0 for s in scores]

def _score_fuzzy(query: str) -> List[float]:
    qn = normalize_text(query)
    out = []
    for d in INDEX["docs"]:
        cand = " ".join(filter(None, [d["_q_norm"], d["_a_norm"], " ".join(d["_tags_norm"])]))
        # ratio parziale per domande corte; token_sort_ratio per ordine libero
        r1 = fuzz.partial_ratio(qn, cand)
        r2 = fuzz.token_sort_ratio(qn, cand)
        out.append(max(r1, r2)/100.0)
    return out

def _score_tags_and_file(query: str) -> List[float]:
    qn = normalize_text(query)
    out = []
    for d in INDEX["docs"]:
        boost = 0.0
        # match su tags
        if d["_tags_norm"]:
            tag_hit = max((fuzz.partial_ratio(qn, t)/100.0) for t in d["_tags_norm"])
            boost = max(boost, 0.6*tag_hit)
        # match su filename “p560” → P560.txt
        fname = normalize_text(d["file"].replace(".txt",""))
        fn_hit = fuzz.partial_ratio(qn, fname)/100.0
        boost = max(boost, 0.7*fn_hit)
        # prior di famiglia
        boost += prior_boost(qn, d["file"])
        # clamp
        out.append(min(boost, 1.0))
    return out

def _score_embeddings(query: str) -> Optional[List[float]]:
    if INDEX.get("model") is None or INDEX.get("emb") is None:
        return None
    try:
        q_emb = INDEX["model"].encode([query], convert_to_tensor=True, normalize_embeddings=True)
        sims = st_util.cos_sim(q_emb, INDEX["emb"]).cpu().numpy()[0]  # [-1..1]
        # porta 0..1
        sims = (sims + 1.0) / 2.0
        mx = float(sims.max()) if sims.size else 1.0
        return [float(s)/mx if mx>0 else 0.0 for s in sims]
    except Exception as e:
        if DEBUG: print("[scraper_tecnaria][EMB][ERR]", repr(e))
        return None

# =========================
# Selettore SINAPSI
# =========================
def _sinapsi_select(q: str, topk: int = DEFAULT_TOPK) -> Dict[str, Any]:
    N = len(INDEX["docs"])
    if N == 0:
        return {"found": False, "reason": "no_docs"}

    # componi punteggi
    s_bm25 = _score_bm25(q)             # 0..1
    s_fuz  = _score_fuzzy(q)            # 0..1
    s_tag  = _score_tags_and_file(q)    # 0..1
    s_emb  = _score_embeddings(q) or [0.0]*N

    scores = []
    for i in range(N):
        total = W_BM25*s_bm25[i] + W_FUZZY*s_fuz[i] + W_TAG*s_tag[i] + W_EMB*s_emb[i]
        scores.append((total, i))

    scores.sort(reverse=True, key=lambda x: x[0])

    # calcolo EDGE (margine top1-top2)
    top1 = scores[0]
    top2 = scores[1] if len(scores) > 1 else (0.0, -1)
    edge = top1[0] - top2[0]

    # semaforo + gating
    label = "RED"
    if top1[0] >= SINAPSI_GREEN and edge >= EDGE_MIN:
        label = "GREEN"
    elif top1[0] >= SINAPSI_YELLOW:
        label = "YELLOW"

    if DEBUG:
        best = INDEX["docs"][top1[1]]
        print(f"[sinapsi] {label} · score={top1[0]:.3f} edge={edge:.3f} · chosen={best['file']}")

    # se proprio scarso: proponi alternative (senza rispondere perentoriamente)
    candidates = scores[:max(3, min(topk, 10))]
    return {
        "label": label,
        "edge": edge,
        "scores": scores,
        "candidates": candidates,
    }

def _extract_answer(block: Dict[str, Any]) -> str:
    # preferisci una R:, altrimenti question+raw compattati
    ans = (block.get("answer") or "").strip()
    if ans:
        return ans
    # se non esiste R:, usa il paragrafo "raw" (prime 1200 battute)
    raw = (block.get("raw") or "").strip()
    if raw:
        return raw[:1200]
    # fallback: domanda (se c'è)
    q = (block.get("question") or "").strip()
    return q or "(nessun testo disponibile)"

# =========================
# Ricerca pubblica
# =========================
def search_best_answer(query: str,
                       threshold: float = DEFAULT_THRESHOLD,
                       topk: int = DEFAULT_TOPK) -> Dict[str, Any]:
    """
    Ritorna: {answer, found, score, from, tags}
    - Usa Sinapsi per selezionare top1; se GREEN → risponde
    - Se YELLOW → risponde se top1 >= threshold o edge sufficiente
    - Se RED → not found (ma restituisce suggerimenti in 'debug' interno se DEBUG=True)
    """
    if not is_ready():
        return {"answer": "Indice non pronto. Riprova tra qualche secondo.", "found": False, "from": None}

    # Espansione con sinonimi (aiuta BM25/fuzzy)
    q_variants = expand_with_synonyms(query)
    best_overall = None

    # Valuta la migliore variante (massimizza top1 score)
    for qv in q_variants:
        sin = _sinapsi_select(qv, topk=topk)
        top1_score, top1_idx = sin["scores"][0]
        if best_overall is None or top1_score > best_overall["top1_score"]:
            best_overall = {"q": qv, "sin": sin, "top1_score": top1_score, "top1_idx": top1_idx}

    sin = best_overall["sin"]
    top1_score, top1_idx = sin["scores"][0]
    chosen = INDEX["docs"][top1_idx]
    label = sin["label"]
    edge = sin["edge"]

    # log sorgente
    if DEBUG:
        print(f"[scraper_tecnaria][SEARCH] q='{query}' → top1={chosen['file']} score={top1_score:.3f} label={label} edge={edge:.3f}")

    # regole di risposta
    if label == "GREEN":
        return {
            "answer": _extract_answer(chosen),
            "found": True,
            "score": round(float(top1_score), 3),
            "from": chosen["file"],
            "tags": chosen.get("tags", []),
        }
    elif label == "YELLOW":
        # Se comunque sopra threshold o buon margine, rispondi
        if top1_score >= threshold or edge >= EDGE_MIN:
            return {
                "answer": _extract_answer(chosen),
                "found": True,
                "score": round(float(top1_score), 3),
                "from": chosen["file"],
                "tags": chosen.get("tags", []),
            }
        # altrimenti proponi (qui teniamo “not found” per interfaccia semplice)
        return {
            "answer": "Non ho trovato una risposta precisa. Prova a riformulare leggermente la domanda.",
            "found": False,
            "from": None
        }
    else:
        # RED → niente risposta
        return {
            "answer": "Non ho trovato una risposta precisa. Prova a riformulare leggermente la domanda.",
            "found": False,
            "from": None
        }

# =========================
# Esecuzione diretta (debug)
# =========================
if __name__ == "__main__":
    print("[scraper_tecnaria] Avvio test locale…")
    build_index(DOC_DIR)
    print("[scraper_tecnaria] READY:", is_ready(), "docs:", len(INDEX.get("docs", [])))
    # probe
    for q in ["mi parli della P560?", "contatti tecnaria", "chi è Tecnaria?", "codici connettori"]:
        res = search_best_answer(q)
        print("Q:", q)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        print("─"*60)
