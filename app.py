# app.py
# TECNARIA — Sinapsi + Camilla + NLM (COOPERATIVO)
# --------------------------------------------------------
# 3 agenti entrano SEMPRE:
#   1) SINAPSI → regole dure / cantiere / casi killer
#   2) NLM → semantica, linguaggio naturale
#   3) CAMILLA → tono, tipo di domanda, famiglia desiderata
#
# Poi un FUSION-ENGINE mette insieme i punteggi e sceglie.
#
# Avvio:
#   uvicorn app:app --host 0.0.0.0 --port 8000
#
# Requisiti consigliati:
#   pip install fastapi uvicorn
#   pip install sentence-transformers torch   (opzionale, ma migliora NLM)
#
# File dati:
#   static/data/tecnaria_gold.json   (il tuo v32)

import os
import json
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI
from pydantic import BaseModel


# --------------------------------------------------------
# CONFIG
# --------------------------------------------------------
DATA_PATH = os.environ.get("TECNARIA_GOLD_PATH", "static/data/tecnaria_gold.json")

# pesi di fusione (puoi alzarli/abbassarli)
W_SINAPSI = 0.45   # peso del motore regole Tecnaria
W_NLM     = 0.35   # peso del motore semantico
W_CAMILLA = 0.20   # peso dell’intento/tono

# se tutti sono bassi → fallback
FUSION_MIN_SCORE = 0.30


# --------------------------------------------------------
# MODELLI I/O
# --------------------------------------------------------
class AskIn(BaseModel):
    question: str
    lang: Optional[str] = "it"


class AskOut(BaseModel):
    answer: str
    source_id: str
    family: str
    score: float
    debug: Dict[str, Any]


# --------------------------------------------------------
# UTILI
# --------------------------------------------------------
def normalize(text: str) -> str:
    text = text.lower()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    text = re.sub(r"[^a-z0-9àèéìòùüç\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# --------------------------------------------------------
# CARICAMENTO DATASET
# --------------------------------------------------------
if not os.path.exists(DATA_PATH):
    raise FileNotFoundError(f"File Tecnaria non trovato: {DATA_PATH}")

with open(DATA_PATH, "r", encoding="utf-8") as f:
    RAW = json.load(f)

ITEMS: List[Dict[str, Any]] = RAW.get("items", [])
META: Dict[str, Any] = RAW.get("_meta", {})


# --------------------------------------------------------
# NLM (opzionale)
# --------------------------------------------------------
embedding_ok = False
embed_model = None

try:
    from sentence_transformers import SentenceTransformer
    try:
        embed_model = SentenceTransformer("sentence-transformers/all-mpnet-base-v2")
    except Exception:
        embed_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    embedding_ok = True
except Exception:
    embedding_ok = False

if embedding_ok:
    import torch
    for it in ITEMS:
        trig = it.get("trigger", {})
        kws = trig.get("keywords", [])
        rep = it.get("domanda", "") + " " + " ".join(kws)
        rep = normalize(rep)
        it["_emb"] = embed_model.encode(rep, convert_to_tensor=True)
else:
    for it in ITEMS:
        it["_emb"] = None


# --------------------------------------------------------
# 1) SINAPSI — motore regole (restituisce PIÙ candidati)
# --------------------------------------------------------
# questi sono i “casi killer” e i “tipici cantiere”
SINAPSI_PATTERNS = [
    ("KILLER-STRESS-0100", ["vcem con p560", "sparato vcem", "chiodato vcem", "pistola su vcem"], 1.0),
    ("CTF-STRESS-0001", ["1 chiodo", "un solo chiodo", "secondo chiodo non entra"], 0.95),
    ("CTF-STRESS-0002", ["lamiera staccata", "5 mm", "lamiera non aderente"], 0.92),
    ("ACC-STRESS-0001", ["senza distanziatori", "posso gettare lo stesso", "rete giu"], 0.9),
    ("ACCESSORI-STRESS-0100", ["senza rete", "non ho la rete", "posso evitare la rete"], 0.9),
    ("P560-NL-0001", ["p560 non sparava", "p560 si inceppa", "pistola non va"], 0.85),
    ("CTL-STRESS-0100", ["ctl su acciaio", "ho solo ctl", "non ho ctf"], 0.9),
]

def sinapsi_candidates(q_norm: str, limit: int = 5) -> List[Tuple[float, Dict[str, Any], str]]:
    """
    Torna una lista di (score, item, reason) trovati da regole.
    """
    cands: List[Tuple[float, Dict[str, Any], str]] = []
    for pattern_id, words, base_score in SINAPSI_PATTERNS:
        for w in words:
            w_norm = normalize(w)
            if w_norm in q_norm:
                # trova item con id simile
                for it in ITEMS:
                    if pattern_id in it.get("id", ""):
                        cands.append((base_score, it, f"sinapsi:{pattern_id}"))
                        break
    # se non ha trovato niente, prova “famiglia per keyword”
    if not cands:
        # fallback: se trova “ctf” ecc.
        fam_words = {
            "ctf": "CTF",
            "ctl": "CTL",
            "vcem": "VCEM",
            "ctcem": "CTCEM",
            "p560": "P560",
            "diapason": "DIAPASON",
            "gts": "GTS",
        }
        for token, fam in fam_words.items():
            if token in q_norm:
                # prendi il primo item della famiglia
                for it in ITEMS:
                    if it.get("family", "").upper() == fam:
                        cands.append((0.55, it, f"sinapsi:fam:{fam}"))
                        break
                break

    return cands[:limit]


# --------------------------------------------------------
# 2) CAMILLA — tono / tipo / famiglia desiderata
# --------------------------------------------------------
def camilla_profile(q_norm: str) -> Dict[str, Any]:
    """
    Riconosce il "perché" della domanda.
    """
    if any(x in q_norm for x in ["per errore", "ho sbagliato", "mi e scappato", "non entra", "ho chiodato"]):
        mood = "error"
    elif any(x in q_norm for x in ["codici", "ordinare", "fornitura", "inox", "versione speciale"]):
        mood = "comm"
    else:
        mood = "ask"

    # famiglia desiderata dle tono
    fam_hint = None
    if "p560" in q_norm:
        fam_hint = "P560"
    elif "lamiera" in q_norm or "chiodo" in q_norm:
        fam_hint = "CTF"
    elif "legno" in q_norm or "ctl" in q_norm:
        fam_hint = "CTL"
    elif "vcem" in q_norm or "ctcem" in q_norm:
        fam_hint = "VCEM"

    return {
        "mood": mood,
        "fam_hint": fam_hint,
        # bonus che Camilla dà ai candidati che rispettano questo
        "bonus": 0.12 if mood == "error" else 0.06
    }


# --------------------------------------------------------
# 3) NLM — semantico (restituisce PIÙ candidati)
# --------------------------------------------------------
def cosine(q_vec, i_vec) -> float:
    import torch
    if q_vec is None or i_vec is None:
        return 0.0
    return torch.nn.functional.cosine_similarity(q_vec, i_vec, dim=0).item()

def keyword_score(q_norm: str, item: Dict[str, Any]) -> float:
    trig = item.get("trigger", {})
    peso = float(trig.get("peso", 0.0))
    kws = trig.get("keywords", [])
    best = 0.0
    for kw in kws:
        kw_norm = normalize(kw)
        if kw_norm and kw_norm in q_norm:
            frac = len(kw_norm) / (len(q_norm) + 1)
            sc = peso * frac
            if sc > best:
                best = sc
    return best

def nlm_candidates(q_raw: str, limit: int = 5) -> List[Tuple[float, Dict[str, Any], str]]:
    q_norm = normalize(q_raw)
    q_vec = None
    if embedding_ok:
        q_vec = embed_model.encode(q_norm, convert_to_tensor=True)

    scored: List[Tuple[float, Dict[str, Any], str]] = []
    for it in ITEMS:
        ks = keyword_score(q_norm, it)
        if embedding_ok:
            cs = cosine(q_vec, it["_emb"])
        else:
            cs = 0.0
        final = (ks * 0.6) + (cs * 0.4)
        if final > 0.0:
            scored.append((final, it, "nlm"))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:limit]


# --------------------------------------------------------
# 4) FUSION ENGINE — i tre non si escludono, SI AIUTANO
# --------------------------------------------------------
def fuse_candidates(
    q_raw: str,
    sinapsi_cands: List[Tuple[float, Dict[str, Any], str]],
    nlm_cands: List[Tuple[float, Dict[str, Any], str]],
    camilla_ctx: Dict[str, Any]
) -> Tuple[float, Dict[str, Any], Dict[str, Any]]:
    """
    Per ogni candidato visto da uno dei 2 motori, calcoliamo:
    - score_sinapsi
    - score_nlm
    - score_camilla
    poi facciamo media pesata.
    Vince chi ha lo score finale più alto.
    """
    # mettiamo tutti i candidati in una mappa per id
    pool: Dict[str, Dict[str, Any]] = {}

    # aggiungi candidati Sinapsi
    for sc, it, reason in sinapsi_cands:
        iid = it.get("id", "")
        pool[iid] = {
            "item": it,
            "score_sinapsi": sc,
            "score_nlm": 0.0,
            "reasons": [reason]
        }

    # aggiungi candidati NLM
    for sc, it, reason in nlm_cands:
        iid = it.get("id", "")
        if iid in pool:
            pool[iid]["score_nlm"] = sc
            pool[iid]["reasons"].append(reason)
        else:
            pool[iid] = {
                "item": it,
                "score_sinapsi": 0.0,
                "score_nlm": sc,
                "reasons": [reason]
            }

    # ora calcoliamo il punteggio di Camilla per ciascuno
    mood = camilla_ctx["mood"]
    fam_hint = camilla_ctx["fam_hint"]
    bonus = camilla_ctx["bonus"]

    best_final = 0.0
    best_entry: Optional[Dict[str, Any]] = None

    for iid, entry in pool.items():
        it = entry["item"]
        fam = it.get("family", "").upper()

        # camilla score: se l’utente aveva un “error” e la risposta è una STRESS/KILLER → bonus
        score_camilla = 0.0
        if mood == "error":
            if "STRESS" in iid or "KILLER" in iid:
                score_camilla = 1.0
            else:
                score_camilla = 0.4
        elif mood == "comm":
            if fam in ("COMM", "ACCESSORI"):
                score_camilla = 1.0
            else:
                score_camilla = 0.3
        else:  # ask
            # se c'è un hint di famiglia e coincide → bonus
            if fam_hint and fam == fam_hint:
                score_camilla = 0.9
            else:
                score_camilla = 0.4

        # punteggio finale pesato
        final_score = (
            entry["score_sinapsi"] * W_SINAPSI +
            entry["score_nlm"] * W_NLM +
            score_camilla * W_CAMILLA
        )

        if final_score > best_final:
            best_final = final_score
            best_entry = {
                "item": it,
                "score_final": final_score,
                "sinapsi": entry["score_sinapsi"],
                "nlm": entry["score_nlm"],
                "camilla": score_camilla,
                "reasons": entry["reasons"]
            }

    return best_final, best_entry, {
        "mood": mood,
        "fam_hint": fam_hint,
        "bonus": bonus
    }


# --------------------------------------------------------
# 5) REGINA — fallback
# --------------------------------------------------------
def regina_fallback(q: str) -> AskOut:
    return AskOut(
        answer=(
            "Non posso confermare al 100% la posa con i dati che hai scritto. "
            "Manda foto e contesto a Tecnaria S.p.A. (info@tecnaria.com) indicando famiglia, supporto e lamiera."
        ),
        source_id="REGINA-FALLBACK",
        family="COMM",
        score=0.0,
        debug={"question": q}
    )


# --------------------------------------------------------
# FASTAPI
# --------------------------------------------------------
app = FastAPI(title="Tecnaria — 3 alleati (Sinapsi + Camilla + NLM)", version="1.0.0")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "items": len(ITEMS),
        "embedding": embedding_ok,
        "meta": META,
        "mode": "3-allies"
    }


@app.post("/api/ask", response_model=AskOut)
def api_ask(payload: AskIn):
    q = payload.question
    q_norm = normalize(q)

    # 1) tutti e tre entrano
    sinapsi_cands = sinapsi_candidates(q_norm, limit=5)
    nlm_cands = nlm_candidates(q, limit=5)
    camilla_ctx = camilla_profile(q_norm)

    # 2) fusione
    final_score, best_entry, camilla_used = fuse_candidates(q, sinapsi_cands, nlm_cands, camilla_ctx)

    # 3) decisione
    if not best_entry or final_score < FUSION_MIN_SCORE:
        return regina_fallback(q)

    item = best_entry["item"]
    return AskOut(
        answer=item.get("risposta", "—"),
        source_id=item.get("id", "UNKNOWN"),
        family=item.get("family", "UNKNOWN"),
        score=round(final_score, 4),
        debug={
            "sinapsi_score": round(best_entry["sinapsi"], 4),
            "nlm_score": round(best_entry["nlm"], 4),
            "camilla_score": round(best_entry["camilla"], 4),
            "reasons": best_entry["reasons"],
            "camilla_ctx": camilla_used
        }
    )
