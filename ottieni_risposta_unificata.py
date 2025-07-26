from estrai_dal_sito import estrai_contenuto_dal_sito
from estrai_dai_documenti import estrai_testo_dai_documenti

def ottieni_risposta_unificata(domanda):
    risposta_doc = estrai_testo_dai_documenti(domanda)
    risposta_web = estrai_contenuto_dal_sito(domanda)

    if risposta_doc and risposta_web:
        return f"📚 Dai documenti:\n{risposta_doc}\n\n🌐 Dal sito:\n{risposta_web}"
    elif risposta_doc:
        return f"📚 Dai documenti:\n{risposta_doc}"
    elif risposta_web:
        return f"🌐 Dal sito:\n{risposta_web}"
    else:
        return "❌ Mi dispiace, non ho trovato informazioni pertinenti nei documenti ufficiali Tecnaria."
