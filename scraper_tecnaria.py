# -*- coding: utf-8 -*-
"""
SCRAPER TECNARIA - versione ultra-semplice e affidabile
- 1 file TXT = 1 blocco, niente chunking complicato
- Nessuna soglia: restituisce sempre il best match
- Ignora la parola "tecnaria"
- Percorso robusto: usa la cartella documenti_gTab accanto al file
- Debug disponibile con funzione debug_info()
"""

import os, re, glob
from dataclasses import dataclass
from typing import List, Tuple, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOC_DIR = os.getenv("DOC_DIR") or os.path.join(BASE_DIR, "documenti_gTab")
DEBUG    = os.getenv("DEBUG", "1") == "1"

STOPWORDS = {"tecnaria","spa","s.p.a.","il","lo","la","i","gli","le","un","uno","una",
             "di","del","della","dell","dei","degli","delle","e","ed","o","con","per",
             "su","tra","fra","in","da","al","allo","ai","agli","alla","alle","che",
             "come","quale","quali","dove","quando","anche"}

# Intent map: forziamo alcune domande a certi file
INTENTS = {
    "contatti": {
        "keywords": ["contatti","telefono","email","orari","sede","indirizzo"],
        "prefer_file_substr": ["contatti","orari"],
    },
    "stabilimenti": {
        "keywords": ["stabilimenti","produzione","reparto","logistica","fabbrica","impianto"],
        "prefer_file_substr": ["stabilimenti","produzione"],
    },
    "certificazioni": {
        "keywords": ["certificazioni","iso","ce","dop","eurocodici","ntc"],
        "prefer_file_substr": ["certificaz"],
    },
    "profilo": {
        "keywords": ["profilo","chi siete","chi siamo","azienda","storia","presentazione"],
        "prefer_file_substr": ["profilo"],
    },
    "vision": {
        "keywords": ["vision","visione","futuro","strategia"],
        "prefer_file_substr": ["vision"],
    },
    "mission": {
        "keywords": ["mission","missione","valori","obiettivi"],
        "prefer_file_substr": ["mission"],
    },
}

@dataclass
class Doc:
    name: str
    tags: List[str]
    body: str

_INDEX: List[Doc] = []
_LAST_LOAD_SUMMARY = ""

def _dbg(msg: str):
    if DEBUG:
        print(f"[DEBUG] {msg}")

def _tokenize(s: str) -> List[str]:
    s = s.lower()
    s = re.sub(r"[^a-z0-9àèéìòóùüç]+"," ", s)
    return [t for t in s.split() if t and t not in STOPWORDS]

def _extract_tags_and_body(text: str) -> Tuple[List[str], str]:
    tags: List[str] = []
    lines = text.splitlines()
    if lines and lines[0].strip().lower().startswith("[tags:"):
        m = re.match(r"^\[tags:\s*(.*?)\s*\]\s*$", lines[0].strip(), flags=re.IGNORECASE)
        if m:
            raw = m.group(1)
            tags = [t.strip().lower() for t in re.split(r"[;,]", raw) if t.strip()]
        body = "\n".join(lines[1:]).strip()
        return tags, body
    return tags, text.strip()

def reload_index() -> None:
    global _INDEX, _LAST_LOAD_SUMMARY
    _INDEX = []
    pattern = os.path.join(DOC_DIR, "**", "*.txt")
    files = sorted(glob.glob(pattern, recursive=True))
    lines = [f"DOC_DIR={DOC_DIR}", f"FILES={len(files)}"]
    _dbg(f"DOC_DIR={DOC_DIR}")
    _dbg(f"Trovati {len(files)} file")
    for fp in files:
        try:
            raw = open(fp, "r", encoding="utf-8").read()
        except Exception as e:
            _dbg(f"Impossibile leggere {fp}: {e}")
            continue
        tags, body = _extract_tags_and_body(raw)
        _INDEX.append(Doc(name=os.path.basename(fp), tags=tags, body=body))
        _dbg(f" - {os.path.basename(fp)} tags={tags} bytes={len(raw)}")
        lines.append(f" - {os.path.basename(fp)} tags={tags} bytes={len(raw)}")
    _LAST_LOAD_SUMMARY = "\n".join(lines)

def _intent_router(q: str) -> Optional[str]:
    qlow = q.lower()
    for intent, cfg in INTENTS.items():
        if any(k in qlow for k in cfg["keywords"]):
            for d in _INDEX:
                if any(p in d.name.lower() for p in cfg["prefer_file_substr"]):
                    _dbg(f"Intent '{intent}' → {d.name}")
                    return d.body.strip()
            q_toks = set(_tokenize(qlow))
            for d in _INDEX:
                if q_toks & set(d.tags):
                    _dbg(f"Intent '{intent}' via TAGS → {d.name}")
                    return d.body.strip()
    return None

def _keyword_overlap_score(q: str, doc: Doc) -> float:
    q_toks = _tokenize(q)
    d_toks = set(_tokenize(" ".join(doc.tags) + " " + doc.body[:1500]))
    if not q_toks or not d_toks:
        return 0.0
    hits = sum(1 for t in q_toks if t in d_toks)
    return hits / max(1, len(set(q_toks)))

def risposta_document_first(domanda: str) -> str:
    domanda = (domanda or "").strip()
    if not domanda:
        return ""
    if not _INDEX:
        reload_index()
    # 1) intent router
    fast = _intent_router(domanda)
    if fast:
        return fast
    # 2) overlap su tutti i file
    scores = [(_keyword_overlap_score(domanda, d), d) for d in _INDEX]
    scores.sort(key=lambda x: x[0], reverse=True)
    if not scores or scores[0][0] == 0:
        _dbg(f"Nessun overlap per: {domanda}")
        return ""
    best = scores[0][1]
    _dbg(f"Best by overlap → {best.name} score={scores[0][0]:.2f}")
    return best.body.strip()

def debug_info(query: Optional[str] = None, top_k: int = 5) -> str:
    if not _INDEX:
        reload_index()
    out = ["# DEBUG SNAPSHOT", _LAST_LOAD_SUMMARY or "(no load info)"]
    if query:
        pairs = [(_keyword_overlap_score(query, d), d) for d in _INDEX]
        pairs.sort(key=lambda x: x[0], reverse=True)
        out.append(f"\n# QUERY: {query}\n")
        for i,(s,d) in enumerate(pairs[:top_k],1):
            out.append(f"{i}. {d.name}  overlap={s:.2f}  tags={d.tags}")
    return "\n".join(out)

try:
    reload_index()
except Exception as e:
    _dbg(f"reload_index() failed: {e}")
