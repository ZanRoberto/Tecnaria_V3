from estrai_dai_documenti import estrai_testo_dai_documenti
from estrai_dal_sito import estrai_contenuto_dal_sito
from documenti_utils import normalizza_testo
from deep_translator import GoogleTranslator
from langdetect import detect
from rapidfuzz import fuzz

def ottieni_risposta_unificata(domanda):
    # Normalizza domanda
    domanda_originale = domanda
    domanda = normalizza_testo(domanda)

    # Determina lingua per traduzione automatica
    lingua = detect(domanda)

    # Estrai testo da fonti locali (documenti .txt, html ecc.)
    testo_documenti = estrai_testo_dai_documenti()

    # Estrai contenuto dal sito ufficiale (se vuoi abilitare anche questa funzione)
    url_tecnaria = "https://www.tecnaria.com/it/connettori-solai-legno.html"
    testo_sito = estrai_contenuto_dal_sito(url_tecnaria)

    # Combina i testi
    testo_completo = f"{testo_documenti}\n\n{testo_sito}"

    # Valuta pertinenza (grezza, puoi perfezionare con semantica GPT se vuoi)
    if fuzz.partial_ratio(domanda, testo_completo) < 20:
        return "❌ Nessuna risposta trovata tra i documenti ufficiali e il sito Tecnaria."

    # Traduzione se necessario
    if lingua != "it":
        domanda = GoogleTranslator(source='auto', target='it').translate(domanda)

    # Risposta grezza
    if domanda in testo_completo:
        return domanda

    # Risposta tramite estrazione approssimata
    righe = testo_completo.split('\n')
    migliori = sorted(righe, key=lambda r: fuzz.partial_ratio(domanda, r), reverse=True)

    # Prendi le 3 righe più pertinenti
    risposta = "\n".join(migliori[:3])
    return risposta.strip()
