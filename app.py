# app.py
# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, Response, redirect, url_for
import os, json, requests

app = Flask(__name__)

# ---------- Brand guard (soft) ----------
SYSTEM_BRAND_GUARD = (
    "Focalizzati sui prodotti Tecnaria S.p.A. di Bassano del Grappa "
    "(CTF, CTL, Diapason, CEM-E). "
    "Non inventare codici inesistenti. Rispondi sempre in italiano."
)

# ---------- HOME: reindirizza al pannello ----------
@app.get("/")
def root_redirect():
    # invece di mostrare "ok", manda SEMPRE al pannello
    return redirect(url_for('panel'), code=302)

# ---------- LLM adapter ----------
def _llm_chat(messages, model=None, temperature=0.2, timeout=60):
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

# ---------- 1) Chat libera (risposta “stile ChatGPT”) ----------
@app.post("/ask_chatgpt")
def ask_chatgpt_puro():
    payload = request.get_json(silent=True) or {}
    domanda = (payload.get("domanda") or "").strip()
    if not domanda:
        return jsonify({"status": "ERROR", "detail": "Campo 'domanda' mancante"}), 400
    try:
        content = _llm_chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Sei un esperto tecnico-commerciale di connettori da costruzione "
                        "della Tecnaria S.p.A. di Bassano del Grappa. "
                        "Rispondi sempre in italiano, con chiarezza e completezza. "
                        "Usa paragrafi brevi e, quando utile, elenchi puntati. "
                        "Mantieni un tono professionale ma comprensibile a un cliente non tecnico. "
                        "Concentrati su prodotti Tecnaria (CTF, CTL, Diapason, CEM-E). "
                        "Se la domanda è generica, chiarisci ma fornisci comunque indicazioni utili."
                    )
                },
                {"role": "user", "content": domanda}
            ],
            temperature=0.2
        )
        return jsonify({"status": "OK", "answer": content}), 200
    except Exception as e:
        return jsonify({"status": "ERROR", "detail": str(e)}), 500

# ---------- 2) Estrazione requisiti ----------
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

# ---------- 3) Calcolo finale ----------
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

# ---------- 4) Interfaccia HTML (pannello) ----------
@app.get("/panel")
def panel():
    # Nessuna f-string qui: le { } del JS restano testuali
    html = """<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <title>Tecnaria Bot</title>
  <style>
    body{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:900px;margin:32px auto;padding:0 16px}
    textarea{width:100%;padding:10px;border:1px solid #ccc;border-radius:8px}
    button{padding:10px 16px;border:0;border-radius:10px;cursor:pointer;background:#111;color:#fff}
    .card{border:1px solid #e5e5e5;border-radius:12px;padding:16px;margin:16px 0;box-shadow:0 1px 2px rgba(0,0,0,.03)}
    .out{white-space:pre-wrap;border:1px solid #ddd;border-radius:8px;padding:10px;background:#fafafa}
    label{display:block;margin:8px 0 4px}
    input{width:100%;padding:8px;border:1px solid #ccc;border-radius:8px}
  </style>
</head>
<body>
  <h1>✅ Tecnaria Bot</h1>
  <p>Scrivi la domanda. Vedrai subito la risposta “stile ChatGPT”. Se servono dati (copriferro, spessore, supporto), compaiono i campi. Poi calcoli la risposta finale.</p>

  <div class="card">
    <h3>1) Domanda</h3>
    <textarea id="q1" rows="2" placeholder="Es: Che altezza per connettore CTF su base cemento?"></textarea><br><br>
    <button onclick="ask()">Invia</button>
  </div>

  <div class="card">
    <h3>Risposta ChatGPT</h3>
    <div id="out1" class="out"></div>
  </div>

  <div class="card">
    <h3>Dati aggiuntivi richiesti</h3>
    <div id="followup"></div>
  </div>

  <div class="card">
    <h3>Risposta finale</h3>
    <div id="out2" class="out"></div>
  </div>

<script>
const base = window.location.origin;

async function postJSON(url, body){
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})});
  const t = await r.text(); try{return JSON.parse(t)}catch(e){return {raw:t}}
}

async function ask(){
  const domanda=document.getElementById('q1').value.trim();
  if(!domanda){alert("Scrivi una domanda"); return;}

  // 1) Risposta “stile ChatGPT”
  const raw=await postJSON(base+'/ask_chatgpt',{domanda});
  document.getElementById('out1').textContent = raw.answer || JSON.stringify(raw);

  // 2) Analisi requisiti → se mancano campi, genero input
  const req=await postJSON(base+'/requisiti_connettore',{domanda});
  const f = document.getElementById('followup');
  if(req.status==="MISSING"){
    let html="";
    (req.needed_fields||[]).forEach(fld=>{
      html+= "<label>"+fld+"</label><input id='f_"+fld+"' />";
    });
    html+= "<br><button onclick='calc()'>Calcola</button>";
    f.innerHTML = html;
  }else if(req.status==="READY"){
    f.innerHTML = "<button onclick='calc()'>Calcola subito</button>";
  }else{
    f.innerHTML = "<div class='out'>Errore analisi requisiti</div>";
  }
}

async function calc(){
  const domanda=document.getElementById('q1').value.trim();
  const res=await postJSON(base+'/altezza_connettore',{domanda});
  // Mostra la frase già pronta, se presente
  document.getElementById('out2').textContent = res.testo_cliente || JSON.stringify(res, null, 2);
}
</script>
</body>
</html>"""
    return Response(html, mimetype="text/html")

# ---------- 5) Debug rotte ----------
@app.get("/routes")
def routes():
    rules = [str(r) for r in app.url_map.iter_rules()]
    return jsonify({"routes": rules})

# ---------- Avvio locale ----------
if __name__ == "__main__":
    # In locale puoi andare su http://127.0.0.1:5000/ e verrai reindirizzato a /panel
    app.run(host="0.0.0.0", port=5000, debug=True)
