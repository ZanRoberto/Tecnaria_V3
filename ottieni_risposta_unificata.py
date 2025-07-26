from documenti_utils import estrai_testo_dai_documenti
from sito_utils import estrai_contenuto_dal_sito

def ottieni_risposta_unificata(domanda):
    risposta_doc = estrai_testo_dai_documenti(domanda)
    risposta_web = estrai_contenuto_dal_sito(domanda)

    if not risposta_doc and not risposta_web:
        return "❌ Nessuna informazione trovata nei documenti né sul sito Tecnaria."

    if risposta_doc and risposta_web:
        return f"📚 Dai documenti:\n{risposta_doc}\n\n🌐 Dal sito:\n{risposta_web}"
    elif risposta_doc:
        return f"📚 Dai documenti:\n{risposta_doc}"
    else:
        return f"🌐 Dal sito:\n{risposta_web}"
