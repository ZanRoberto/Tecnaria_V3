import json
from typing import Dict, Any

# ---- Adattare a tua funzione LLM esistente ----
def ask_chatgpt(prompt: str) -> str:
    # TODO: sostituisci con la tua chiamata OpenAI/compatibile
    raise NotImplementedError

CRITICAL_FIELDS = ["spessore_soletta_mm", "copriferro_mm", "supporto"]

def estrai_parametri(domanda: str) -> Dict[str, Any]:
    prompt_estrazione = f"""[QUI IL PROMPT 1 SOPRA]""".replace("{{DOMANDA_UTENTE}}", domanda)
    raw = ask_chatgpt(prompt_estrazione)
    try:
        data = json.loads(raw)
    except:
        data = {"status": "ERROR", "raw": raw}
    return data

def calcola_soluzione(found: Dict[str, Any]) -> Dict[str, Any]:
    p = f"""[QUI IL PROMPT 2 SOPRA]"""
    p = (p.replace("{{prodotto}}", str(found.get("prodotto","")))
           .replace("{{spessore}}", str(found.get("spessore_soletta_mm","")))
           .replace("{{copriferro}}", str(found.get("copriferro_mm","")))
           .replace("{{supporto}}", str(found.get("supporto","")))
           .replace("{{classe_fuoco}}", str(found.get("classe_fuoco",""))))
    raw = ask_chatgpt(p)
    try:
        return json.loads(raw)
    except:
        return {"status": "ERROR", "raw": raw}

def pipeline_connettore(domanda_utente: str, defaults: Dict[str, Any] = None) -> Dict[str, Any]:
    defaults = defaults or {}
    step1 = estrai_parametri(domanda_utente)
    if step1.get("status") == "READY":
        return calcola_soluzione(step1["found"])

    if step1.get("status") == "MISSING":
        found = step1.get("found", {})
        needed = step1.get("needed_fields", [])

        # 1) Prova a riempire dai default/DB
        for k in needed[:]:
            if k in defaults and defaults[k] is not None:
                found[k] = defaults[k]
                needed.remove(k)

        # 2) Se ancora mancano campi critici → restituisci la follow-up question da mostrare al cliente
        if any(f in CRITICAL_FIELDS for f in needed):
            return {
                "status": "ASK_CLIENT",
                "question": step1.get("followup_question"),
                "found_partial": found
            }

        # 3) Altrimenti calcola
        return calcola_soluzione(found)

    return {"status": "ERROR", "detail": step1}
