# app.py
# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, Response
import os, json, requests

app = Flask(__name__)

# ---------- Brand guard (soft, non blocca) ----------
SYSTEM_BRAND_GUARD = (
    "Focalizzati sui prodotti Tecnaria S.p.A. di Bassano del Grappa "
    "(CTF, CTL, Diapason, CEM-E). "
    "Se l'utente cita altri marchi, rispondi in modo neutro e generale senza promuoverli, "
    "e quando possibile riferisci l'equivalenza o la terminologia Tecnaria. "
    "Non inventare codici inesistenti. Rispondi in italiano."
)

# ---------- Health check ----------
@app.get("/")
def health():
    return "ok"

# ---------- LLM adapter (OpenAI/compat) ----------
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

# ---------- 1) Chat libera (risposta grezza stile ChatGPT) ----------
@app.post("/ask_chatgpt")
def ask_chatgpt_puro():
    payload = request.get_json(silent=True) or {}
    domanda = (payload.get("domanda") or "").strip()
    model = (payload.get("model") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")).strip()
    if not domanda:
        return jsonify({"status":"ERROR","detail":"Campo 'domanda' mancante"}), 400
    try:
        content = _llm_chat(
            messages=[
                {"role":"system","content": SYSTEM_BRAND_GUARD + " Rispondi in modo conciso, accurato e utile."},
                {"role":"user","content": domanda}
            ],
            model=model, temperature=0.0
        )
        return jsonify({"status":"OK","answer":content}), 200
    except Exception as e:
        return jsonify({"status":"ERROR","detail":str(e)}), 500

# ---------- 2) Requisiti per altezza connettore (slot-filling) ----------
CRITICAL_FIELDS = {"spessore_soletta_mm", "copriferro_mm", "supporto"}

PROMPT_ESTRAZIONE = """Sei un estrattore di requisiti per domande sui connettori Tecnaria.
Dato il testo utente, estrai in JSON questi campi:

- intento (scegliere_altezza_connettore | preventivo | posa | certificazioni | altro)
- prodotto (CTF | CTL | Diapason | CEM-E | altro)
- spessore_soletta_mm (numero)
- copriferro_mm (numero)
- supporto (lamiera_grecata | soletta_piena)
- classe_fuoco (REI60/REI90) [opzionale]
- note (string) [opzionale]

Se mancano campi CRITICI per 'scegliere_altezza_connettore' (spessore_soletta_mm, copriferro_mm, supporto), NON proporre soluzioni.
Restituisci:

Caso A - Mancano campi:
{
 "status": "MISSING",
 "found": {...},
 "needed_fields": ["copriferro_mm", ...],
 "followup_question": "Formula UNA sola domanda chiara per ottenere i valori mancanti."
}

Caso B - Tutti i campi presenti:
{
 "status": "READY",
 "found": {...},
 "checklist_ok": ["spessore_soletta_mm fornito", "copriferro_mm fornito", "supporto fornito"],
 "prossimi_passi": "Ora si può procedere al calcolo tecnico."
}

Testo utente: <<<{DOMANDA_UTENTE}>>>"""

def _safe_json_loads(raw: str):
    try: return json.loads(raw)
    except: return {"status":"ERROR","raw": (raw or "")[:2000]}

def _estrai_requisiti(domanda: str):
    content = _llm_chat(
        messages=[
            {"role":"system","content": SYSTEM_BRAND_GUARD + " Rispondi SOLO con JSON valido. Niente testo extra."},
            {"role":"user","content": PROMPT_ESTRAZIONE.replace("{DOMANDA_UTENTE}", domanda)}
        ],
        temperature=0.0
    )
    return _safe_json_loads(content)

@app.post("/requisiti_connettore")
def requisiti_connettore():
    payload = request.get_json(silent=True) or {}
    domanda = (payload.get("domanda") or "").strip()
    if not domanda:
        return jsonify({"status":"ERROR","detail":"Campo 'domanda' mancante"}), 400
    try:
        step = _estrai_requisiti(domanda)
        if step.get("status") == "MISSING":
            return jsonify({
                "status":"ASK_CLIENT",
                "question": step.get("followup_question","Serve un dato aggiuntivo."),
                "found_partial": step.get("found", {}),
                "missing": step.get("needed_fields", [])
            }), 200
        if step.get("status") == "READY":
            return jsonify({
                "status":"READY",
                "found": step.get("found", {}),
                "checklist_ok": step.get("checklist_ok", []),
                "prossimi_passi": step.get("prossimi_passi","Ora si può procedere al calcolo tecnico.")
            }), 200
        return jsonify({"status":"ERROR","detail": step}), 500
    except Exception as e:
        return jsonify({"status":"ERROR","detail":str(e)}), 500

# ---------- 3) Calcolo altezza/codice (solo con dati completi) ----------
PROMPT_CALCOLO = """Sei un configuratore Tecnaria. Dati i parametri, scegli l’altezza corretta del connettore e il codice.
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

def _calcola(found: dict):
    p = PROMPT_CALCOLO.format(
        prodotto=found.get("prodotto",""),
        spessore=found.get("spessore_soletta_mm",""),
        copriferro=found.get("copriferro_mm",""),
        supporto=found.get("supporto",""),
    )
    content = _llm_chat(
        messages=[
            {"role":"system","content": SYSTEM_BRAND_GUARD + " Rispondi SOLO con JSON valido. Niente testo extra."},
            {"role":"user","content": p},
        ],
        temperature=0.0
    )
    return _safe_json_loads(content)

@app.post("/altezza_connettore")
def altezza_connettore():
    payload = request.get_json(silent=True) or {}
    domanda = (payload.get("domanda") or "").strip()
    if not domanda:
        return jsonify({"status":"ERROR","detail":"Campo 'domanda' mancante"}), 400
    step = _estrai_requisiti(domanda)
    if step.get("status") == "MISSING":
        return jsonify({
            "status":"ASK_CLIENT",
            "question": step.get("followup_question","Serve un dato aggiuntivo."),
            "found_partial": step.get("found", {}),
            "missing": step.get("needed_fields", [])
        }), 200
    if step.get("status") == "READY":
        result = _calcola(step["found"])
        return jsonify({"status":"OK","params": step["found"], "result": result}), 200
    return jsonify({"status":"ERROR","detail":step}), 500

# ---------- 4) Pannello HTML neutro (2 giri) ----------
@app.get("/panel")
def panel():
    # NESSUNA f-string: così le { } JS/CSS non rompono Python
    html = """<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <title>Tecnaria — Demo due giri</title>
  <style>
    body{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:960px;margin:32px auto;padding:0 16px}
    h1{margin:0 0 8px} h2{margin:24px 0 8px}
    section{border:1px solid #e5e5e5;border-radius:12px;padding:16px;margin:16px 0;box-shadow:0 1px 2px rgba(0,0,0,.03)}
    .row{display:flex;gap:12px;align-items:flex-start}
    label{display:block;margin:8px 0 4px}
    textarea,input,select{width:100%;padding:10px;border:1px solid #ccc;border-radius:8px}
    button{padding:10px 16px;border:0;border-radius:10px;cursor:pointer}
    button.primary{background:#111;color:#fff}
    pre{background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px;white-space:pre-wrap}
    .hint{color:#666;font-size:12px}
    .chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
    .chip{border:1px solid #ddd;border-radius:20px;padding:4px 10px;font-size:12px;color:#333}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
  </style>
</head>
<body>
  <h1>✅ Tecnaria — Flusso a due giri</h1>
  <p class="hint">1) Scrivi la domanda → vedi la <b>risposta ChatGPT (grezza)</b> e compaiono i <b>campi necessari</b>. 2) Compila i campi → ottieni la <b>risposta completa</b>.</p>

  <section id="s1">
    <h2>1) Domanda del cliente</h2>
    <label>Domanda</label>
    <textarea id="q1" rows="2" placeholder="Es: Che altezza per connettore CTF su base cemento?"></textarea>
    <div style="margin-top:8px">
      <button class="primary" id="btn1">Invia</button>
    </div>
    <div class="chips" id="chips"></div>
    <h3 style="margin-top:16px">Risposta ChatGPT (grezza)</h3>
    <pre id="raw_out"></pre>
    <h3>Dati da completare (se servono)</h3>
    <div id="missing_wrap"></div>
  </section>

  <section id="s2" style="display:none">
    <h2>2) Completa e calcola</h2>
    <div class="grid" id="dyn_fields"></div>
    <div style="margin-top:8px">
      <button class="primary" id="btn2">Calcola risposta completa</button>
    </div>
    <h3 style="margin-top:16px">Risposta completa</h3>
    <pre id="final_out"></pre>
  </section>

  <script>
    const base = window.location.origin;

    const postJSON = async (url, body) => {
      const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})});
      const txt = await r.text();
      try{ return JSON.parse(txt) }catch{ return {raw: txt} }
    };

    let foundPartial = {};
    let missing = [];
    let domandaUtente = "";

    const FIELD_META = {
      "spessore_soletta_mm": {label:"Spessore soletta (mm)", type:"number", placeholder:"Es. 60"},
      "copriferro_mm":       {label:"Copriferro (mm)",        type:"number", placeholder:"Es. 25"},
      "supporto":            {label:"Supporto",               type:"select", options:[
                                {value:"lamiera_grecata", label:"Lamiera grecata"},
                                {value:"soletta_piena",   label:"Soletta piena (cemento)"},
                              ]},
      "classe_fuoco":        {label:"Classe di fuoco (opz.)", type:"text", placeholder:"Es. REI60"},
      "prodotto":            {label:"Prodotto (opz.)",        type:"text", placeholder:"Es. CTF"}
    };

    document.getElementById('btn1').addEventListener('click', async () => {
      const q = document.getElementById('q1').value.trim();
      if(!q){ alert("Scrivi la domanda del cliente"); return; }
      domandaUtente = q;

      const raw = await postJSON(base + '/ask_chatgpt', {domanda: q});
      document.getElementById('raw_out').textContent = JSON.stringify(raw, null, 2);

      const req = await postJSON(base + '/requisiti_connettore', {domanda: q});

      const chips = document.getElementById('chips');
      chips.innerHTML = "";
      const chip = (t)=>{ const s=document.createElement('span'); s.className='chip'; s.textContent=t; return s; };

      if(req.status === "ASK_CLIENT"){
        chips.appendChild(chip("Mancano dati"));
        foundPartial = req.found_partial || {};
        missing = req.missing || [];
        renderMissing(missing);
        document.getElementById('s2').style.display = 'block';
      } else if(req.status === "READY"){
        chips.appendChild(chip("Dati completi"));
        foundPartial = req.found || {};
        missing = [];
        renderMissing([]);
        document.getElementById('s2').style.display = 'block';
      } else {
        chips.appendChild(chip("Errore"));
        document.getElementById('missing_wrap').innerHTML = "<div class='hint'>Impossibile analizzare i requisiti.</div>";
        document.getElementById('s2').style.display = 'none';
      }
    });

    function renderMissing(miss){
      const wrap = document.getElementById('missing_wrap');
      const dyn = document.getElementById('dyn_fields');
      wrap.innerHTML = "";
      dyn.innerHTML = "";

      if(Object.keys(foundPartial).length){
        const pre = document.createElement('pre');
        pre.textContent = "Raccolto: " + JSON.stringify(foundPartial, null, 2);
        wrap.appendChild(pre);
      }

      if(!miss || miss.length===0){
        const div = document.createElement('div');
        div.innerHTML = "<div class='hint'>Non risultano campi obbligatori da chiedere. Puoi calcolare subito.</div>";
        wrap.appendChild(div);
        return;
      }

      miss.forEach(field => {
        const meta = FIELD_META[field] || {label: field, type: "text", placeholder: ""};
        const container = document.createElement('div');

        let inner = "<label>"+meta.label+"</label>";
        if(meta.type === "select"){
          inner += "<select id='f_"+field+"'>";
          (meta.options||[]).forEach(opt=>{
            inner += "<option value='"+opt.value+"'>"+opt.label+"</option>";
          });
          inner += "</select>";
        } else {
          inner += "<input id='f_"+field+"' type='"+meta.type+"' placeholder='"+(meta.placeholder||"")+"' />";
        }
        container.innerHTML = inner;
        dyn.appendChild(container);
      });
    }

    document.getElementById('btn2').addEventListener('click', async () => {
      const newVals = {};
      (missing||[]).forEach(f => {
        const el = document.getElementById('f_'+f);
        if(el){
          newVals[f] = (el.tagName === "SELECT") ? el.value : (el.value || "").trim();
        }
      });

      const params = Object.assign({}, foundPartial, newVals);

      const parts = [];
      if(params.prodotto){ parts.push(params.prodotto); } else { parts.push("connettore CTF"); }
      if(params.supporto){ parts.push("su " + (params.supporto==="lamiera_grecata"?"lamiera grecata":"soletta piena")); }
      if(params.spessore_soletta_mm){ parts.push("soletta " + params.spessore_soletta_mm + " mm"); }
      if(params.copriferro_mm){ parts.push("copriferro " + params.copriferro_mm + " mm"); }
      const domandaCompleta = parts.length ? parts.join(", ") : domandaUtente;

      const res = await postJSON(base + '/altezza_connettore', {domanda: domandaCompleta});
      document.getElementById('final_out').textContent = JSON.stringify(res, null, 2);
    });
  </script>
</body>
</html>"""
    return Response(html, mimetype="text/html")

# ---------- 5) Debug: elenco rotte ----------
@app.get("/routes")
def routes():
    rules = [str(r) for r in app.url_map.iter_rules()]
    return jsonify({"routes": rules})

# ---------- Debug console ----------
print("ROUTES:", app.url_map)

# ---------- Avvio locale ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
