import re

def normalizza_testo(testo):
    # Rimuove spazi doppi, caratteri speciali inutili, converte tutto in minuscolo
    testo = re.sub(r'\s+', ' ', testo)
    testo = testo.strip().lower()
    return testo
