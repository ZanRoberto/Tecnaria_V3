def search_best_answer(index: dict, question: str):
    """Cerca il blocco migliore e restituisce SOLO la risposta testuale.
       Se non supera la soglia, usa un fallback brand-aware su TAG/patterns comuni."""
    if not index or not isinstance(index, dict):
        return ("", False, 0.0, None, None)

    data = index.get("data") or []
    if not data:
        return ("", False, 0.0, None, None)

    q_norm = _norm(question)
    q_tokens = _tok(question)

    # --- Shortlist tramite indice invertito
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

        # Piccolo “kick” se c’è almeno una overlap (aiuta domande corte tipo “chi è tecnaria”)
        if s > 0:
            s += 0.8

        if s > 0:
            candidates.append((s, doc_id))

    candidates.sort(reverse=True)
    candidates = candidates[:max(3, TOPK)]

    if not candidates:
        # brand fallback diretto
        fb = _brand_fallback(index, q_norm, q_tokens)
        if fb:
            return fb

        return ("", False, 0.0, None, None)

    best_score, best_id = candidates[0]
    entry = data[best_id]

    # Normalizzazione più “calda”
    norm_score = min(1.0, best_score / (best_score + 4.0))

    if norm_score < SIM_THRESHOLD:
        # Se sotto soglia, prova fallback brand-aware
        fb = _brand_fallback(index, q_norm, q_tokens)
        if fb:
            return fb
        return ("", False, float(norm_score), entry.get("path"), entry.get("line"))

    answer = _extract_answer_text(entry.get("raw", "")) or (entry.get("a") or "")
    answer = re.sub(r"\n{3,}", "\n\n", answer.strip())

    if DEBUG:
        print(f"[scraper_tecnaria][SEARCH] q={question!r} -> score={norm_score:.3f} {entry.get('path')}:{entry.get('line')}")

    return (answer, True, float(norm_score), entry.get("path"), entry.get("line"))


def _brand_fallback(index: dict, q_norm: str, q_tokens: list):
    """Se la ricerca non supera la soglia, prova a coprire i casi base:
       - 'chi è tecnaria', 'parlami di tecnaria', 'chi siete'
       - 'contatti', 'orari', 'telefono', 'email'
       - 'catalogo', 'prodotti', 'elenco connettori'
    """
    data = index.get("data") or []
    if not data:
        return None

    def contains_any(txt, words):
        return any(w in txt for w in words)

    # profilo aziendale
    if contains_any(q_norm, ["chi e tecnaria", "chi è tecnaria", "chi siete", "parlami di tecnaria", "profilo aziendale", "chi siamo", "azienda tecnaria"]):
        for entry in data:
            tags = " ".join(entry.get("tags") or [])
            tnorm = _norm(tags + " " + (entry.get("q") or "") + " " + entry.get("raw", ""))
            if contains_any(tnorm, ["chi siamo","profilo aziendale","chi e tecnaria","tecnaria e","bassano del grappa","azienda tecnaria"]):
                ans = _extract_answer_text(entry.get("raw","")) or (entry.get("a") or "")
                ans = ans.strip()
                if ans:
                    return (ans, True, 0.25, entry.get("path"), entry.get("line"))

    # contatti/orari
    if contains_any(q_norm, ["contatti","telefono","email","mail","orari","sede","indirizzo"]):
        for entry in data:
            tags = " ".join(entry.get("tags") or [])
            tnorm = _norm(tags + " " + (entry.get("q") or "") + " " + entry.get("raw", ""))
            if contains_any(tnorm, ["contatti","orari","sede","indirizzo","telefono","email"]):
                ans = _extract_answer_text(entry.get("raw","")) or (entry.get("a") or "")
                ans = ans.strip()
                if ans:
                    return (ans, True, 0.25, entry.get("path"), entry.get("line"))

    # elenco prodotti/catalogo
    if contains_any(q_norm, ["catalogo","prodotti","connettori","elenco codici","codici connettori"]):
        for entry in data:
            tags = " ".join(entry.get("tags") or [])
            tnorm = _norm(tags + " " + (entry.get("q") or "") + " " + entry.get("raw", ""))
            if contains_any(tnorm, ["prodotti","catalogo","elenco","codici","connettori"]):
                ans = _extract_answer_text(entry.get("raw","")) or (entry.get("a") or "")
                ans = ans.strip()
                if ans:
                    return (ans, True, 0.25, entry.get("path"), entry.get("line"))

    return None
