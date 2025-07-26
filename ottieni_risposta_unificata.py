import os
from estrai_dal_sito import cerca_sul_sito
from estrai_testo_dai_documenti import estrai_testo_dai_documenti

def ottieni_risposta_unificata(domanda: str) -> str:
    risposta_doc = estrai_testo_dai_documenti(domanda)
    risposta_sito = cerca_sul_sito(domanda)

    if risposta_doc:
        if risposta_sito:
            return f"📚 Dai documenti Tecnaria:\n{risposta_doc}\n\n🌐 Inoltre dal sito ufficiale:\n{risposta_sito}"
        else:
            return risposta_doc

    if risposta_sito:
        return risposta_sito

    return "❌ Nessuna informazione trovata nei documenti o sul sito ufficiale Tecnaria."
