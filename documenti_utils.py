import os
from rapidfuzz import fuzz

CARTELLA_DOCUMENTI = "documenti"

def estrai_testo_dai_documenti(domanda: str, soglia_similitudine: int = 65) -> str:
    if not os.path.exists(CARTELLA_DOCUMENTI):
        return ""

    risultati = []
    for nome_file in os.listdir(CARTELLA_DOCUMENTI):
        if nome_file.endswith(".txt"):
            percorso = os.path.join(CARTELLA_DOCUMENTI, nome_file)
            try:
                with open(percorso, 'r', encoding='utf-8') as f:
                    testo = f.read()
                    score = fuzz.partial_ratio(domanda.lower(), testo.lower())
                    if score >= soglia_similitudine:
                        risultati.append((score, nome_file, testo.strip()))
            except:
                continue

    if risultati:
        risultati.sort(reverse=True)
        return risultati[0][2][:3000]  # massimo 3000 caratteri
    else:
        return ""
