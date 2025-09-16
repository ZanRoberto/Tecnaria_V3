# -*- coding: utf-8 -*-
"""
configuratore_connettori.py
Pipeline a due step per ordini connettori Tecnaria:
1) Estrazione parametri critici (slot-filling)
2) Calcolo finale altezza + codice connettore
"""

import os
import json
from typing import Dict, Any, Optional

# ===========
# LLM ADAPTER
# ===========
# Compatibile con OpenAI API-style. Configura via .env:
# OPENAI_API_KEY=...
# OPENAI_BASE_URL=... (opzionale; default https://api.openai.com/v1)
# OPENAI_MODEL=gpt-4o-mini (o altro modello)
#
# Se usi provider compatibile (es. DeepSeek-compat), basta impostare OPENAI_BASE_URL.

def ask_chatgpt(prompt: str) -> str:
    import requests

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    if not api_key:
        # Fallback hard (per ambienti senza chiave): restituisco errore JSON valido
        return json.dumps({"status": "ERROR", "detail": "OPENAI_API_KEY mancante"})

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = {
        "model": model,
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": "Rispondi SOLO in JSON quando richiesto. Non aggiungere testo extra."},
            {"role": "user", "content": prompt},
        ],
    }
    resp = requests.post(url, headers=headers, json=data, timeout=60)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return content


# ==================
# PROMPT: ESTRAZIONE
# ==================
PROMPT_ESTRAZIONE = """Sei un estrattore di parametri per ordine connettori Tecnaria (Bassano del Grappa).
Dato il testo utente, estrai in JSON questi campi:

- prodotto (CTF | CTL | Diapason | CEM-E | altro)
- spessore_soletta_mm (numero)
- copriferro_mm (numero)
- supporto (lamiera_grecata | soletta_piena)
- classe_fuoco (es. REI60/REI90) [opzionale]
- note (string)

Se un campo critico per la scelta dell’altezza manca (es. spessore_soletta_mm o copriferro_mm), NON proporre soluzioni.
Restituisci ESCLUSIVAMENTE uno di questi JSON:

Caso A - Mancano campi critici:
{
 "status": "MISSING",
 "found": {...campi trovati...},
 "needed_fields": ["copriferro_mm", ...],
 "followup_question": "Una SOLA domanda chiara per ottenere i valori mancanti."
}

Caso B - Tutti i campi per decidere sono presenti:
{
 "status": "READY",
 "found": {
   "prodotto": "...",
   "spessore_soletta_mm": ...,
   "copriferro_mm": ...,
   "supporto": "...",
   "classe_fuoco": "...",
   "note": "..."
 }
}

Testo utente: <<<{DOMANDA_UTENTE}>>>"""

# =================
# PROMPT: SOLUZIONE
# =================
PROMPT_SOLUZIONE = """Sei un configuratore Tecnaria (Bassano del Grappa).
Scegli l’ALTEZZA corretta del connettore e il relativo CODICE, usando SOLO i parametri forniti.

Parametri:
- prodotto: {prodotto}
- spessore_soletta_mm: {spessore}
- copriferro_mm: {copriferro}
- supporto: {supporto}
- classe_fuoco: {classe_fuoco}

Output in JSON (senza testo extra):
{
 "soluzione": {
   "altezza_connettore_mm": <numero>,
   "codice_prodotto": "<string>",
   "motivazione_breve": "<max 3 frasi>",
   "avvertenze": ["<string>", "..."]
 },
 "mostra_al_cliente": "Testo conciso e chiaro per conferma ordine"
}

Se i parametri sono insufficienti, restituisci:
{{
 "status": "INSUFFICIENT"
}}"""


CRITICAL_FIELDS = {"spessore_soletta_mm", "copriferro_mm", "supporto"}

def _safe_json_loads(raw: str) -> Dict[str, Any]:
    try:
        return json.loads(raw)
    except Exception:
        # Provo a tagliare eventuali pre/post testo non JSON
        raw_stripped = raw.strip()
        # fallback estremamente prudente
        return {"status": "ERROR", "raw": raw_stripped[:2000]}

def estrai_parametri(domanda: str) -> Dict[str, Any]:
    prompt = PROMPT_ESTRAZIONE.replace("{DOMANDA_UTENTE}", domanda)
    raw = ask_chatgpt(prompt)
    return _safe_json_loads(raw)

def calcola_soluzione(found: Dict[str, Any]) -> Dict[str, Any]:
    p = PROMPT_SOLUZIONE.format(
        prodotto=str(found.get("prodotto", "")),
        spessore=str(found.get("spessore_soletta_mm", "")),
        copriferro=str(found.get("copriferro_mm", "")),
        supporto=str(found.get("supporto", "")),
        classe_fuoco=str(found.get("classe_fuoco", "")),
    )
    raw = ask_chatgpt(p)
    return _safe_json_loads(raw)

# ===========================
# DEFAULTS (opzionali, da .env)
# ===========================
def get_defaults() -> Dict[str, Any]:
    """
    Puoi impostare default in .env, es:
      TEC_DEFAULT_SUPPORTO=lamiera_grecata
      TEC_DEFAULT_COPRIFERRO_MM=25
    Attenzione: i default vengono usati SOLO se mancano campi critici.
    """
    d: Dict[str, Any] = {}
    if os.getenv("TEC_DEFAULT_SUPPORTO"):
        d["supporto"] = os.getenv("TEC_DEFAULT_SUPPORTO").strip()
    if os.getenv("TEC_DEFAULT_COPRIFERRO_MM"):
        try:
            d["copriferro_mm"] = int(os.getenv("TEC_DEFAULT_COPRIFERRO_MM"))
        except:
            pass
    if os.getenv("TEC_DEFAULT_SPESSORE_SA_MM"):
        try:
            d["spessore_soletta_mm"] = int(os.getenv("TEC_DEFAULT_SPESSORE_SA_MM"))
        except:
            pass
    return d

# ===========================
# PIPELINE ORDINATORE
# ===========================
def pipeline_connettore(domanda_utente: str,
                        defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    defaults = defaults or get_defaults()
    step1 = estrai_parametri(domanda_utente)

    if step1.get("status") == "READY" and isinstance(step1.get("found"), dict):
        return {
            "status": "OK",
            "input_params": step1["found"],
            "result": calcola_soluzione(step1["found"])
        }

    if step1.get("status") == "MISSING":
        found = step1.get("found", {}) or {}
        needed = set(step1.get("needed_fields", []) or [])

        # 1) Provo a riempire con defaults
        for k in list(needed):
            if k in defaults and defaults[k] not in (None, ""):
                found[k] = defaults[k]
                needed.discard(k)

        # 2) Se ancora mancano campi critici → ritorno follow-up per il cliente
        if any(k in CRITICAL_FIELDS for k in needed):
            return {
                "status": "ASK_CLIENT",
                "question": step1.get("followup_question", "Servono dati aggiuntivi."),
                "found_partial": found,
                "missing": sorted(list(needed)),
            }

        # 3) Parametri completi → calcolo
        return {
            "status": "OK",
            "input_params": found,
            "result": calcola_soluzione(found)
        }

    # Fallback errore
    return {"status": "ERROR", "detail": step1}
