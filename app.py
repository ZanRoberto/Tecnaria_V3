# app.py
# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, Response
import os, json, requests

app = Flask(__name__)

# ---------- Brand guard ----------
SYSTEM_BRAND_GUARD = (
    "Focalizzati sui prodotti Tecnaria S.p.A. di Bassano del Grappa "
    "(CTF, CTL, Diapason, CEM-E). "
    "Se l'utente cita altri marchi, rispondi in modo neutro e generale senza promuoverli. "
    "Non inventare codici inesistenti. Rispondi sempre in italiano."
)

# ---------- Health check ----------
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
    j = r.json()
    return j["choices"][0]["message"]["content"]

# ---------- 1) Chat libera ----------
@app.post("/ask_chatgpt")
def ask_chatgpt_puro():
    payload = request.get_json(silent=True) or {}
    domanda = (payload.get("domanda") or "").strip()
    if not domanda:
        return jsonify({"status": "ERROR", "detail": "Campo 'domanda' mancante"}), 400
    try:
        content = _llm_chat(
            messages=[
                {"role": "system", "content": SYSTEM_BRAND_GUARD},
                {"role": "user", "content": domanda}
            ],
            temperature=0.0
        )
        return jsonify({"status": "OK", "answer": content}), 200
    except Exception as e:
        return jsonify({"status": "ERROR", "detail": str(e)}), 500

# ---------- 2) Requisiti ----------
PROMPT_ESTRAZIONE = """Sei un estrattore di requisiti per domande sui connettori Tecnaria.
Dato il testo utente, estrai in JSON questi campi:

- prodotto (CTF | CTL | Diapason | CEM-E | altro)
- spessore_soletta_mm
- copriferro_mm
- supporto (lamiera_grecata | soletta_piena)

Se mancano campi critici (spessore_soletta_mm, copriferro_mm, supporto), restituisci:
{
 "status": "MISSING",
 "found": {...},
 "needed_fields": [...],
 "followup_question": "Domanda chiara per ottenere i valori mancanti"
}

Se tutti i campi sono presenti:
{
 "status": "READY",
 "found": {...}
}
"""

def _safe_json_loads(raw: str):
    try:
        return json.loads(raw)
    except:
        return {"status": "ERROR", "raw": (raw or "")[:2000]}

def _estrai_requisiti(domanda: str):
    content = _llm_chat(
        messages=[
            {"role": "system", "content": SYSTEM_BRAND_GUARD + " Rispondi SOLO con JSON valido."},
            {"role": "user", "content": PROMPT_ESTRAZIONE.replace("{DOMANDA_UTENTE}", domanda)}
        ],
        temperature=0.0
    )
    return _safe_json_loads(content)

@app.post("/requisiti_connettore")
def requisiti_connettore():
    payload = request.get_json(silent=True) or {}
    domanda = (payload.get("domanda") or "").strip()
    if not domanda:
        return jsonify({"status": "ERROR", "detail": "Campo 'domanda' mancante"}), 400
    try:
        step = _estrai_requisiti(domanda)
        return jsonify(step), 200
    except Exception as e:
        return jsonify({"status": "ERROR", "detail": str(e)}), 500

# ---------- 3) Calcolo ----------
PROMPT_CALCOLO = """Sei un configuratore Tecnaria.
Parametri:
- prodotto: {prodotto}
- spessore_soletta_mm: {spessore}
- copriferro_mm: {copriferro}
- supporto: {supporto}

Rispondi SOLO in JSON:
{
 "altezza_connettore_mm": <numero>,
 "codice_prodotto": "<string>",
 "motivazione": "<max 2 frasi>",
 "testo_cliente": "Connettore {codice_prodotto} consigliato (altezza {altezza_connettore_mm} mm). Confermi?"
}"""

def _calcola(found: dict):
    p = PROMPT_CALCOLO.format(
        prodotto=found.get("prodotto", ""),
        spessore=found.get("spessore_soletta_mm", ""),
        copriferro=found.get("copriferro_mm", ""),
        supporto=found.get("supporto", ""),
    )
    content = _llm_chat(
        messages=[
            {"role": "system", "content": SYSTEM_BRAND_GUARD + " Rispondi SOLO con JSON valido."},
            {"role": "user", "content": p},
        ],
        temperature=0.0
    )
    return _safe_json_loads(content)

@app.post("/altezza_connettore")
def altezza_connettore():
    payload = request.get_json(silent=True) or {}
    domanda = (payload.get("domanda") or "").strip()
    if not domanda:
        return jsonify({"status": "ERROR", "detail": "Campo 'domanda' mancante"}), 400
    step = _estrai_requisiti(domanda)
    if step.get("status") == "MISSING":
        return jsonify(step), 200
    if step.get("status") == "READY":
        result = _calcola(step["found"])
        return jsonify(result), 200
    return jsonify({"status": "ERROR", "detail": step}), 500

# ---------- 4) Interfaccia HTML ----------
@app.get("/panel")
def panel():
    html = """<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <title>Tecnaria Bot</title>
</head>
<body>
  <h1>✅ Tecnaria Bot</h1>
  <p>Fai la tua domanda. Vedrai la risposta ChatGPT, poi (se servono) i campi da compilare e infine la risposta completa.</p>

  <textarea id="q1" rows="2" cols="60" placeholder="Es: Che altezza per connettore CTF su base cemento?"></textarea><br>
  <button onclick="ask()">Invia</button>

  <h3>Risposta ChatGPT</h3>
  <div id="out1" style="white-space:pre-wrap; border:1px solid #ccc; padding:8px;"></div>

  <h3>Dati aggiuntivi</h3>
  <div id="followup"></div>

  <h3>Risposta finale</h3>
  <div id="out2" style="white-space:pre-wrap; border:1px solid #ccc; padding:8px;"></div>

<script>
const base = window.location.origin;

async function postJSON(url, body){
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})});
  const t = await r.text(); try{return JSON.parse(t)}catch(e){return {raw:t}}
}

async function ask(){
  const domanda=document.getElementById('q1').value.trim();
  if(!domanda){alert("Scrivi una domanda"); return;}
  
  // Risposta grezza
  const raw=await postJSON(base+'/ask_chatgpt',{domanda});
  document.getElementById('out1').textContent = raw.answer || JSON.stringify(raw);

  // Analisi requisiti
  const req=await postJSON(base+'/requisiti_connettore',{domanda});
  if(req.status==="MISSING"){
    let html="";
    req.needed_fields.forEach(f=>{
      html+=f+": <input id='f_"+f+"' /><br>";
    });
    html+="<button onclick='calc()'>Calcola</button>";
    document.getElementById('followup').innerHTML=html;
  }else if(req.status==="READY"){
    document.getElementById('followup').innerHTML="<button onclick='calc()'>Calcola subito</button>";
  }else{
    document.getElementById('followup').innerHTML="Errore analisi requisiti";
  }
}

async function calc(){
  const domanda=document.getElementById('q1').value.trim();
  const extra={};
  document.querySelectorAll('#followup input').forEach(el=>{
    extra[el.id.replace('f_','')]=el.value;
  });
  const final=await postJSON(base+'/altezza_connettore',{domanda:domanda});
  document.getElementById('out2').textContent = final.testo_cliente || JSON.stringify(final);
}
</script>
</body>
</html>"""
    return Response(html, mimetype="text/html")

# ---------- 5) Debug ----------
@app.get("/routes")
def routes():
    rules = [str(r) for r in app.url_map.iter_rules()]
    return jsonify({"routes": rules})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
