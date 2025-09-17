# --- app_snippet_tecnaria_abc.py ---
# Esempio minimale di integrazione A/B/C nel tuo bot (adatta al tuo framework).

from pathlib import Path

TEMPLATES_DIR = Path("templates")
TEMPLATES = {
    "breve": (TEMPLATES_DIR / "TEMPLATE_A_BREVE.txt").read_text(encoding="utf-8"),
    "standard": (TEMPLATES_DIR / "TEMPLATE_B_STANDARD.txt").read_text(encoding="utf-8"),
    "dettagliata": (TEMPLATES_DIR / "TEMPLATE_C_DETTAGLIATA.txt").read_text(encoding="utf-8"),
}

def build_prompt(mode: str, question: str, context: str | None = None) -> str:
    tpl = TEMPLATES.get(mode, TEMPLATES["dettagliata"])  # default C tecnico
    return tpl.replace("{question}", question).replace("{context}", context or "")

# Guardrail: se modalità 'dettagliata' ma mancano dati chiave, chiedi solo i mancanti.
import re

CRITICAL_KEYS = ("passo gola", "V_L,Ed", "cls", "direzione lamiera")
def missing_critical_inputs(text: str) -> list[str]:
    found = []
    # Euristiche semplici (adatta/estendi per il tuo dominio):
    if re.search(r"\b(gola|passo\s*gola|rib|pitch)\b", text, re.I): found.append("passo gola")
    if re.search(r"\bV\s*L\s*,?\s*Ed|kN/m\b", text, re.I): found.append("V_L,Ed")
    if re.search(r"\bC(\d{2}/\d{2})\b|\bcls\b", text, re.I): found.append("cls")
    if re.search(r"\btrasversal(e|i)|longitudinal(e|i)|direzione\s*lamiera\b", text, re.I): found.append("direzione lamiera")
    missing = [k for k in CRITICAL_KEYS if k not in found]
    return missing

def prepare_input(mode: str, question: str, context: str | None = None) -> str:
    if mode == "dettagliata":
        missing = missing_critical_inputs((question + " " + (context or "")).strip())
        if len(missing) == len(CRITICAL_KEYS):
            # Se manca tutto, forza una richiesta chiara di dati
            return f"Per procedere servono: {', '.join(CRITICAL_KEYS)}. Indicali e riprova."
    return build_prompt(mode, question, context)

# Esempio d'uso nel tuo handler:
# prompt = prepare_input(mode, question, context)
# result = llm.chat(system=..., user=prompt)
# return result.text
