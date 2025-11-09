def match_item(question, item, dataset_family=None):
    q = question.strip()
    q_low = q.lower()

    # prendi famiglia item
    item_family = (
        item.get("family")
        or item.get("meta_family")
        or dataset_family
        or ""
    )

    # score base come già fai tu (similarità testo, tags, ecc.)
    score = base_similarity(q_low, item)  # qui resta la tua logica attuale

    # 1️⃣ rileva famiglie esplicite nella domanda
    explicit_fams = detect_explicit_families(q)

    if explicit_fams:
        if item_family in explicit_fams:
            # boost molto forte
            score *= 8.0
        else:
            # se l’item è di un’altra famiglia, lo schiacci
            score *= 0.05

    # 2️⃣ micro-regole di instradamento (opzionali ma utili)
    if not explicit_fams:
        # se parla di "pistola", "chiodatrice", "cartucce" → favorisci P560
        if any(k in q_low for k in ["p560", "pistola", "chiodatrice", "cartuccia", "cartucce", "sparo"]):
            if item_family == "P560":
                score *= 4.0
            elif item_family in ["CTF", "VCEM", "CTCEM", "CTL", "CTL MAXI", "DIAPASON"]:
                score *= 0.3

    return score
