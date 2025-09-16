# app.py
# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify
import os
import json
import requests
from typing import Dict, Any, Optional

# -----------------------------
# Flask app (visibile a Gunicorn)
# -----------------------------
app = Flask(__name__)

# -----------------------------
# Health check
# -----------------------------
@app.get("/")
def health():
    return "ok"


# =========================================
#            LLM ADAPTER (OpenAI-style)
# =========================================
def _llm_chat(messages, model: Optional[str] = None, temperature: float = 0.0, timeout: int = 60) -> str:
    """
    Adapter minimale per endpoint compatibile con OpenAI Chat Completions.
    Richiede:
      - OPENAI_API_KEY
      - OPENAI_MODEL (default: gpt-4o-mini)
      - OPENAI_BASE_URL (default: https://api.openai.com/v1)
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = (model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")).strip()

    if not api_key:
        # Rispondiamo sempre JSON di errore coerente con le API del progetto
        raise RuntimeError("OPENAI_API_KEY mancante")

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
    }
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


# =========================================
#         ENDPOINT PURO /ask_chatgpt
# =========================================
@app.post("/ask_chatgpt")
def ask_chatgpt_puro():
    """
    Pass-through: inoltra la domanda in formato libero a ChatGPT.
    Body JSON:
      {
        "domanda": "testo libero",
        "model": "gpt-4o-mini"   # opzionale
      }
    """
    payload = request.get_json(silent=True) or {}
    domanda = (payload.get("domanda") or "").strip()
    model = (payload.get("model") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")).strip()

    if not domanda:
        return jsonify({"status": "ERROR", "detail": "Campo 'domanda' mancante"}), 400

    try:
        content = _llm_chat(
            messages=[
                {"role": "system", "content": "Rispondi in modo conciso, accurato e utile."},
                {"role": "user", "content": domanda},
            ],
            model=model,
            temperature=0.0
        )
        return jsonify({"status": "OK", "answer": content}), 200
    except Exception as e:
        return jsonify({"status": "ERROR", "detail": str(e)}), 500


# =========================================
#        CONFIGURATORE CONNETTORI
#        (slot-filling a due step)
# =========================================

# Prompt di ESTRAZIONE parametri (step 1)
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

# Prompt di CALCOLO soluzione (step 2)
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
        return {"status": "ERROR", "raw": (raw or "")[:2000]}

def _estrai_parametri(domanda: str) -> Dict[str, Any]:
    prompt = PROMPT_ESTRAZIONE.replace("{DOMANDA_UTENTE}", domanda)
    content = _llm_chat(
        messages=[
            {"role": "system", "content": "Rispondi SOLO con JSON valido quando richiesto. Niente testo extra."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0
    )
    return _safe_json_loads(content)

def _calcola_soluzione(found: Dict[str, Any]) -> Dict[str, Any]:
    p = PROMPT_SOLUZIONE.format(
        prodotto=str(found.get("prodotto", "")),
        spessore=str(found.get("spessore_soletta_mm", "")),
        copriferro=str(found.get("copriferro_mm", "")),
        supporto=str(found.get("supporto", "")),
        classe_fuoco=str(found.get("classe_fuoco", "")),
    )
    content = _llm_chat(
        messages=[
            {"role": "system", "content": "Rispondi SOLO con JSON valido. Niente testo extra."},
            {"role": "user", "content": p},
        ],
        temperature=0.0
    )
    return _safe_json_loads(content)

def _get_defaults() -> Dict[str, Any]:
    """
    Default opzionali da Environment / .env:
      - TEC_DEFAULT_SUPPORTO=lamiera_grecata | soletta_piena
      - TEC_DEFAULT_COPRIFERRO_MM=25
      - TEC_DEFAULT_SPESSORE_SA_MM=60
    """
    d: Dict[str, Any] = {}
    if os.getenv("TEC_DEFAULT_SUPPORTO"):
        d["supporto"] = os.getenv("TEC_DEFAULT_SUPPORTO").strip()
    if os.getenv("TEC_DEFAULT_COPRIFERRO_MM"):
        try:
            d["copriferro_mm"] = int(os.getenv("TEC_DEFAULT_COPRIFERRO_MM"))
        except Exception:
            pass
    if os.getenv("TEC_DEFAULT_SPESSORE_SA_MM"):
        try:
            d["spessore_soletta_mm"] = int(os.getenv("TEC_DEFAULT_SPESSORE_SA_MM"))
        except Exception:
            pass
    return d

def _pipeline_connettore(domanda_utente: str, defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    defaults = defaults or _get_defaults()
    step1 = _estrai_parametri(domanda_utente)

    if step1.get("status") == "READY" and isinstance(step1.get("found"), dict):
        found = step1["found"]
        return {"status": "OK", "input_params": found, "result": _calcola_soluzione(found)}

    if step1.get("status") == "MISSING":
        found = step1.get("found", {}) or {}
        needed = set(step1.get("needed_fields", []) or [])

        # Provo a riempire con default
        for k in list(needed):
            if k in defaults and defaults[k] not in (None, ""):
                found[k] = defaults[k]
                needed.discard(k)

        # Se mancano ancora campi critici, chiedo al cliente UNA sola cosa
        if any(k in CRITICAL_FIELDS for k in needed):
            return {
                "status": "ASK_CLIENT",
                "question": step1.get("followup_question", "Servono dati aggiuntivi."),
                "found_partial": found,
                "missing": sorted(list(needed)),
            }

        # Altrimenti, ho tutto: calcolo
        return {"status": "OK", "input_params": found, "result": _calcola_soluzione(found)}

    return {"status": "ERROR", "detail": step1}


# =========================================
#      ENDPOINT JSON /ordina_connettore
# =========================================
@app.post("/ordina_connettore")
def ordina_connettore():
    """
    Body JSON:
    {
      "domanda": "Ordina connettore CTF su lamiera grecata; soletta 60 mm; copriferro 25 mm; REI60",
      "defaults": {
        "supporto": "lamiera_grecata",
        "copriferro_mm": 25
      }
    }
    """
    payload = request.get_json(silent=True) or {}
    domanda = (payload.get("domanda") or "").strip()
    defaults = payload.get("defaults") or _get_defaults()

    if not domanda:
        return jsonify({"status": "ERROR", "detail": "Campo 'domanda' mancante"}), 400

    try:
        data = _pipeline_connettore(domanda, defaults=defaults)
        return jsonify(data), 200
    except Exception as e:
        return jsonify({"status": "ERROR", "detail": str(e)}), 500


# =========================================
#               MAIN (locale)
# =========================================
if __name__ == "__main__":
    # Avvio per test locale: python app.py
    app.run(host="0.0.0.0", port=5000, debug=True)
