def enrich_if_needed(base_text: str, question: str, style: str) -> str:
    """
    Se lo stile è B o C, usa l'LLM per espandere il testo deterministico
    SENZA cambiare codici o altezze già dichiarati (es. CTF***, CTL***).
    In caso di deviazioni, ripristina i codici fissati (lock).
    """
    style_hint = STYLE_HINTS.get(style, "")
    lang = detect_lang(question)
    if style == "A":
        return base_text

    # 🔒 Rileva eventuali codici già "decisi" nel testo base (per famiglia)
    # Esempi catturati: CTF060, CTF 60, CTL120, CTL 120
    locks = {}
    for m in re.finditer(r"\b(CTF|CTL)\s*0?(\d{2,3})\b", base_text, flags=re.IGNORECASE):
        fam = m.group(1).upper()
        num = m.group(2).zfill(3)
        locks.setdefault(fam, f"{fam}{num}")  # prima occorrenza per famiglia fa fede

    # Istruzioni al modello: non cambiare i codici fissati
    lock_msg_it = lock_msg_en = ""
    if locks:
        fixed_list = ", ".join(locks.values())
        lock_msg_it = (f"\n\nIMPORTANTE: NON cambiare i codici fissati ({fixed_list}) "
                       f"e NON proporre codici/alternative diverse.")
        lock_msg_en = (f"\n\nIMPORTANT: Do NOT change the fixed codes ({fixed_list}) "
                       f"and do NOT propose different codes/alternatives.")

    if lang == "en":
        user = (f"Expand this answer to style {style}. Keep technical accuracy and structure."
                f"\n\nOriginal question: {question}\n\nAnswer to expand:\n{base_text}"
                f"\n\n{style_hint}{lock_msg_en}")
    else:
        user = (f"Espandi questa risposta in stile {style}. Mantieni accuratezza tecnica e struttura."
                f"\n\nDomanda originale: {question}\n\nRisposta da espandere:\n{base_text}"
                f"\n\n{style_hint}{lock_msg_it}")

    try:
        expanded = call_model(user, style) or base_text
    except Exception:
        return base_text

    # 🧹 Post-check: se l’LLM avesse inserito codici diversi, li riportiamo ai lock
    # Per ogni famiglia bloccata (CTF, CTL), qualsiasi codice diverso viene riscritto nel codice fisso
    def _rewrite_codes(match):
        fam = match.group(1).upper()
        num = match.group(2).zfill(3)
        code = f"{fam}{num}"
        if fam in locks:
            return locks[fam]  # sostituisci qualunque variante col codice fissato
        return code

    expanded = re.sub(r"\b(CTF|CTL)\s*0?(\d{2,3})\b", _rewrite_codes, expanded, flags=re.IGNORECASE)
    return expanded
