# ottieni_risposta_unificata.py

from estrai_dal_sito import estrai_contenuto_dal_sito
from estrai_dai_documenti import estrai_testo_dai_documenti
from documenti_utils import normalizza_testo

def ottieni_risposta_unificata(domanda):
    try:
        with open("documenti/connettori_legno_tecnaria_immagini.html", "r", encoding="utf-8") as f:
            testo_documento = f.read()
    except FileNotFoundError:
        testo_documento = ""

    contenuto_sito = estrai_contenuto_dal_sito("https://www.tecnaria.com/it/connettori-solai-legno.html")
    
    testo_completo = normalizza_testo(testo_documento + "\n" + contenuto_sito)

    if "immagine" in domanda.lower() or ".jpg" in testo_completo.lower():
        return testo_completo  # Mantiene tag HTML intatti per visualizzare immagini
    else:
        return testo_completo.replace("<", "").replace(">", "")  # per sicurezza, se non HTML

