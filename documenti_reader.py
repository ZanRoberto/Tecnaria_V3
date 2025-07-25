# documenti_reader.py

from pathlib import Path
from langdetect import detect
from deep_translator import GoogleTranslator

DOCUMENTI_PATH = Path("documenti")

def estrai_testo_dai_documenti():
    """Estrae testo da tutti i file .txt nella cartella 'documenti'."""
    testo_completo = ""
    if not DOCUMENTI_PATH.exists():
        print("📂 Cartella 'documenti' non trovata.")
        return testo_completo

    for file in DOCUMENTI_PATH.glob("*.txt"):
        try:
            with open(file, 'r', encoding='utf-8') as f:
                testo = f.read()
                testo_completo += f"\n📄 [Documento: {file.name}]\n" + testo
        except Exception as e:
            print(f"Errore nella lettura di {file.name}: {e}")
    
    return testo_completo.strip()

def rileva_lingua(testo):
    """Rileva la lingua del testo."""
    try:
        return detect(testo)
    except Exception:
        return "unknown"

def traduci_in_italiano(testo):
    """Traduce in italiano se rilevata lingua diversa."""
    try:
        lingua = rileva_lingua(testo)
        if lingua != "it":
            return GoogleTranslator(source='auto', target='it').translate(testo)
    except Exception as e:
        print(f"Errore nella traduzione: {e}")
    return testo
