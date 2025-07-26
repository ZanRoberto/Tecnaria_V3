import os

def estrai_testo_dai_documenti(cartella):
    testo_completo = ""
    for nome_file in os.listdir(cartella):
        if nome_file.endswith(".txt") or nome_file.endswith(".html"):
            percorso_file = os.path.join(cartella, nome_file)
            with open(percorso_file, "r", encoding="utf-8") as f:
                testo_completo += f.read() + "\n\n"
    return testo_completo
