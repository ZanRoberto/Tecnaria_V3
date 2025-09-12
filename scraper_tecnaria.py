# --- INTENT & BOOST -------------------------------------------------

COMPANY_FILE_HINTS = ("ChiSiamo_", "ChiSiamo", "ProfiloAziendale", "VisionMission", "ContattiOrari")
PRODUCT_FILE_HINTS = ("CTF", "HBV", "CEM-E", "MINI_CEM-E", "CTL", "CLS", "CLSR", "FVA", "X-HBV", "Diapason", "P560")

def _is_company_file(path: str) -> bool:
    b = os.path.basename(path).lower()
    return any(h.lower() in b for h in COMPANY_FILE_HINTS)

def _is_product_file(path: str) -> bool:
    b = os.path.basename(path).lower()
    return any(h.lower() in b for h in PRODUCT_FILE_HINTS)

def _has_tag(entry: dict, needle: str) -> bool:
    t = (entry.get("tags") or "").lower()
    return needle.lower() in t

def _detect_intent(q: str) -> dict:
    ql = q.lower()
    company_triggers = [
        "chi e tecnaria", "chi e' tecnaria", "chi è tecnaria", "chi siamo",
        "parlami di tecnaria", "mi parli di tecnaria", "informazioni su tecnaria",
        "azienda", "profilo", "storia", "mission", "vision", "valori"
    ]
    product_tokens = ["p560","ctf","hbv","diapason","cem-e","mini cem-e","ctl","cls","clsr","fva","x-hbv"]

    is_company = any(tok in ql for tok in company_triggers)
    has_prod = any(tok in ql for tok in product_tokens)

    # se cita "tecnaria" ma non prodotti specifici => aziendale
    if "tecnaria" in ql and not has_prod:
        is_company = True

    return {"company": is_company, "has_prod": has_prod}

# Sostituisci/aggiorna questa funzione nel tuo scraper
def search_best_answer(question: str, threshold: float = 0.35, topk: int = 20) -> dict:
    q_norm = normalize_text(question)
    intents = _detect_intent(q_norm)

    # 1) ranking di base (keyword + eventuale embedding)
    candidates = _rank_candidates(q_norm, topk=topk)  # usa la tua funzione che costruisce la lista [{entry, score}, ...]

    # 2) RERANK con boost/punizione in base all'intento
    for cand in candidates:
        e = cand["entry"]        # blocco indicizzato
        s = cand["score"]        # punteggio base
        p = e.get("path", "")    # percorso file

        if intents["company"]:
            if _is_company_file(p) or _has_tag(e, "chi siamo") or _has_tag(e, "azienda"):
                s += 0.80  # BOOST forte verso ChiSiamo*
            if _is_product_file(p) and not _has_tag(e, "chi siamo"):
                s -= 0.30  # leggera penalità ai prodotti se la domanda è istituzionale

        cand["score"] = s

    # 3) scegli il migliore sopra soglia
    candidates.sort(key=lambda x: x["score"], reverse=True)
    best = candidates[0] if candidates else None
    if not best or best["score"] < threshold:
        return {"found": False, "answer": "Non ho trovato una risposta precisa. Prova a riformulare leggermente la domanda.", "from": None}

    e = best["entry"]
    return {
        "found": True,
        "answer": e.get("answer") or e.get("text", ""),
        "from": os.path.basename(e.get("path","")),
        "score": round(best["score"], 3),
        "tags": e.get("tags")
    }
