import json
import math

ALT_NOTE_FILE = os.path.join(NOTE_DIR, "CTF", "altezze_ctf.txt")

def _extract_mm(text: str, key: str) -> list[int]:
    """
    Pesca numeri in mm vicino a una parola (es. 'soletta 80 mm', 'copriferro 25').
    Ritorna lista di interi trovati (può essercene più di uno).
    """
    t = text.lower()
    nums = []
    # es: '... soletta 80 mm ...', 'soletta: 80', 'spessore soletta 80'
    patt = rf"{key}\s*[:=]?\s*(\d{{2,3}})\s*(?:mm|m\s*m)?"
    for m in re.finditer(patt, t):
        try: nums.append(int(m.group(1)))
        except: pass
    return nums

def _find_ctf_code_in_line(line: str) -> str | None:
    """
    Estrae un codice tipo CTF090 / CTF105 / CTF125 da una riga.
    """
    m = re.search(r"\bCTF\s*0?(\d{2,3})\b", line, re.IGNORECASE)
    if m:
        return "CTF" + m.group(1).zfill(3)
    return None

def _deterministic_from_note(soletta_mm: int, copriferro_mm: int) -> str | None:
    """
    Cerca nel file altezze_ctf.txt una riga che contenga sia la soletta che il copriferro
    e un codice CTF***. Se non trova match “esatto”, prova match parziale (prima soletta, poi copriferro).
    """
    if not os.path.isfile(ALT_NOTE_FILE):
        return None
    try:
        with open(ALT_NOTE_FILE, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.readlines() if ln.strip()]
    except Exception:
        return None

    s_str = str(soletta_mm)
    c_str = str(copriferro_mm)

    # 1) match riga con entrambi i numeri
    for ln in lines:
        if s_str in ln and c_str in ln:
            code = _find_ctf_code_in_line(ln)
            if code:
                return code

    # 2) match riga con soletta sola (se spesso il copriferro è 25 standard)
    for ln in lines:
        if s_str in ln:
            code = _find_ctf_code_in_line(ln)
            if code:
                return code

    # 3) match riga con copriferro solo
    for ln in lines:
        if c_str in ln:
            code = _find_ctf_code_in_line(ln)
            if code:
                return code

    return None

def deterministic_ctf_height_answer(question: str) -> str | None:
    """
    Se la domanda è del tipo:
      - 'che altezza/altezzE per CTF con soletta 80 mm e copriferro 25 mm?'
    restituisce una RISPOSTA CERTA basata su altezze_ctf.txt.
    """
    q = (question or "").lower()
    # trigger solo se topic = CTF e si parla di altezza
    if "ctf" not in q and "cft" not in q:
        return None
    if not ("altezza" in q or "altezze" in q):
        return None

    # estrai numeri vicino alle parole chiave
    so = _extract_mm(q, r"soletta")
    co = _extract_mm(q, r"copriferro|copri\s*ferro|copri\-?ferro")
    if not so:
        return None  # servono i numeri

    soletta = so[0]
    copri   = co[0] if co else 25  # default prudente 25 se omesso

    code = _deterministic_from_note(soletta, copri)
    if not code:
        return None

    # risposta certa + sintesi logica + nota locale (il resto lo aggiunge già attach_local_note)
    return (
        f"**Altezza consigliata CTF: {code}**\n"
        f"- Dati ricevuti: soletta **{soletta} mm**, copriferro **{copri} mm**.\n"
        f"- Abbinamento ricavato da *altezze_ctf.txt* (regola interna Tecnaria).\n"
        f"Se vuoi verifico anche passo, densità e interferenze impianti."
    )
