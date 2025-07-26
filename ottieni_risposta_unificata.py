# ottieni_risposta_unificata.py

from estrai_dai_documenti import estrai_testo_dai_documenti
from estrai_dal_sito import estrai_contenuto_dal_sito
from documenti_utils import normalizza_testo

def ottieni_risposta_unificata(domanda):
    # Carica il file HTML con immagini invece del .txt
    with open("documenti/connettori_legno_tecnaria_immagini.html", "r", encoding="utf-8") as f:
        testo_locale = f.read()

    testo_locale = normalizza_testo(testo_locale)

    risposta_documenti = estrai_testo_dai_documenti(domanda, testo_locale)
    risposta_online = estrai_contenuto_dal_sito(domanda)

    if risposta_documenti and risposta_online:
        return f"<b>📚 Dai documenti:</b><br>{risposta_documenti}<hr><b>🌐 Dal sito:</b><br>{risposta_online}"
    elif risposta_documenti:
        return f"<b>📚 Dai documenti:</b><br>{risposta_documenti}"
    elif risposta_online:
        return f"<b>🌐 Dal sito:</b><br>{risposta_online}"
    else:
        return "❌ Nessuna risposta trovata nei documenti o online."
