# documenti_utils.py

import os

def estrai_testo_dai_documenti(domanda: str) -> str:
    """
    Scansiona i file .txt nella cartella 'documenti' e restituisce una concatenazione dei contenuti
    rilevanti rispetto alla domanda fornita.
    """
    cartella = 'documenti'
    if not os.path.exists(cartella):
        return "❌ Nessun documento trovato nella cartella."

    testi_rilevanti = []

    for nome_file in os.listdir(cartella):
        if nome_file.endswith(".txt"):
            percorso = os.path.join(cartella, nome_file)
            try:
                with open(percorso, 'r', encoding='utf-8') as f:
                    testo = f.read()
                    if domanda.lower() in testo.lower():
                        testi_rilevanti.append(f"📄 {nome_file}:\n{testo}")
            except Exception as e:
                testi_rilevanti.append(f"⚠️ Errore leggendo {nome_file}: {str(e)}")

    if testi_rilevanti:
        return "\n\n".join(testi_rilevanti)
    else:
        return "Nessun documento contiene informazioni rilevanti rispetto alla tua domanda."
