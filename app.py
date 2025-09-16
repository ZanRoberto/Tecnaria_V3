# app.py
# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify
import os, json, requests

app = Flask(__name__)

@app.get("/")
def health():
    return "ok"

# ---------- LLM adapter ----------
def _llm_chat(messages, model=None, temperature=0.0, timeout=60):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = (model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")).strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY mancante")
    r = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "temperature": temperature, "messages": messages},
        timeout=timeout
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

# ---------- PROMPT ----------
PROMPT_ESTRAZIONE = """Sei un estrattore di parametri per scelta altezza connettore Tecnaria.
Estrai in JSON:
- prodotto
- spessore_soletta_mm
- copriferro_mm
- supporto (lamiera_grecata | soletta_piena)

Se manca un campo critico (spessore, copriferro o supporto):
{
 "status": "MISSING",
 "found": {...},
 "needed_fields": ["copriferro_mm", ...],
 "followup_question": "Formula UNA sola domanda chiara per ottenere i valori mancanti."
}

Se tutti i campi ci sono:
{
 "status": "READY",
 "found": {...}
}

Testo utente: <<<{DOMANDA_UTENTE}>>>"""

PROMPT_CALCOLO = """Sei un configuratore Tecnaria.
Dati i parametri, scegli l’altezza corretta del connettore e il codice prodotto.

Parametri:
- prodotto: {prodotto}
- spessore_soletta_mm: {spessore}
- copriferro_mm: {copriferro}
- supporto: {supporto}

Rispondi SOLO in JSON:
{
 "altezza_connettore_mm": <numero>,
 "codice_prodotto": "<string>",
 "motivazione": "<breve spiegazione in max 2 frasi>",
 "testo_cliente": "Connettore {codice_prodotto} consigliato (altezza {altezza_connettore_mm} mm). Confermi?"
}"""

def _safe_json(raw):
    try: return json.loads(raw)
    except: return {"status":"ERROR","raw":raw[:500]}

def _estrai(domanda: str):
    content = _llm_chat([
        {"role":"system","content":"Rispondi SOLO in JSON valido."},
        {"role":"user","content":PROMPT_ESTRAZIONE.replace("{DOMANDA_UTENTE}", domanda)}
    ])
    return _safe_json(content)

def _calcola(found: dict):
    p = PROMPT_CALCOLO.format(
        prodotto=found.get("prodotto",""),
        spessore=found.get("spessore_soletta_mm",""),
        copriferro=found.get("copriferro_mm",""),
        supporto=found.get("supporto",""),
    )
    content = _llm_chat([
        {"role":"system","content":"Rispondi SOLO in JSON valido."},
        {"role":"user","content":p}
    ])
    return _safe_json(content)

# ---------- Endpoint principale ----------
@app.post("/altezza_connettore")
def altezza_connettore():
    payload = request.get_json(silent=True) or {}
    domanda = (payload.get("domanda") or "").strip()
    if not domanda:
        return jsonify({"status":"ERROR","detail":"Campo 'domanda' mancante"}), 400

    step1 = _estrai(domanda)

    if step1.get("status") == "MISSING":
        return jsonify({
            "status":"ASK_CLIENT",
            "question": step1.get("followup_question"),
            "found_partial": step1.get("found", {}),
            "missing": step1.get("needed_fields", [])
        }), 200

    if step1.get("status") == "READY":
        result = _calcola(step1["found"])
        return jsonify({"status":"OK","params":step1["found"],"result":result}), 200

    return jsonify({"status":"ERROR","detail":step1}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
