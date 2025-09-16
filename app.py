# app.py
# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, Response
import os, json, requests

app = Flask(__name__)

# ---------- Brand guard: SOLO TECNARIA ----------
SYSTEM_BRAND_GUARD = (
    "Rispondi esclusivamente su prodotti Tecnaria S.p.A. di Bassano del Grappa "
    "(CTF, CTL, Diapason, CEM-E). "
    "Se la domanda riguarda marchi o articoli non Tecnaria, rispondi soltanto: "
    "\"Posso trattare esclusivamente prodotti Tecnaria S.p.A. di Bassano del Grappa.\" "
    "Non inventare codici o prodotti inesistenti. Rispondi in italiano."
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
    return r.json()["choices"][0]["message"]["content"]

# ---------- Endpoint 1: Chat libera (SOLO Tecnaria) ----------
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

# ---------- Endpoint 2: requisiti per altezza connettore ----------
CRITICAL_FIELDS = {"spessore_soletta_mm", "copriferro_mm", "supporto"}

PROMPT_ESTRAZIONE = """Sei un estrattore di requisiti per domande sui connettori Tecnaria (Bassano del Grappa).
Dato il testo utente, estrai in JSON questi campi:

- intento (scegliere_altezza_connettore | preventivo | posa | certificazioni | altro)
- prodotto (CTF | CTL | Diapason | CEM-E | altro)
- spessore_soletta_mm (numero)
- copriferro_mm (numero)
- supporto (lamiera_grecata | soletta_piena)
- classe_fuoco (REI60/REI90) [opzionale]
- note (string) [opzionale]

Se mancano campi CRITICI (spessore_soletta_mm, copriferro_mm, supporto), NON proporre soluzioni.
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

# ---------- Endpoint 3: calcolo altezza/codice ----------
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

# ---------- Pannello HTML interattivo ----------
@app.get("/panel")
def panel():
    html = f"""<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <title>Tecnaria Bot Panel</title>
  <style>
    body{{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:960px;margin:32px auto;padding:0 16px}}
    h1{{margin:0 0 16px}} h2{{margin:24px 0 8px}}
    section{{border:1px solid #e5e5e5;border-radius:12px;padding:16px;margin:16px 0;box-shadow:0 1px 2px rgba(0,0,0,.03)}}
    label{{display:block;margin:8px 0 4px}}
    textarea,input{{width:100%;padding:10px;border:1px solid #ccc;border-radius:8px}}
    button{{padding:10px 16px;border:0;border-radius:10px;cursor:pointer}}
    button.primary{{background:#111;color:#fff}}
    pre{{background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px;white-space:pre-wrap}}
    small{{color:#666}}
    code.k{{background:#f2f2f2;padding:2px 6px;border-radius:6px}}
  </style>
</head>
<body>
  <h1>✅ Tecnaria Bot — Pannello Test</h1>
  <p><small>Base URL: <code class="k">{request.host_url.rstrip('/')}</code></small></p>

  <section>
    <h2>1) Domanda libera (SOLO prodotti Tecnaria)</h2>
    <label>Domanda</label>
    <textarea id="ask_q" rows="2" placeholder="Es: Qual è la differenza tra CTF e Diapason?"></textarea>
    <div style="margin-top:8px">
      <button class="primary" onclick="ask()">Invia</button>
    </div>
    <pre id="ask_out"></pre>
  </section>

  <section>
    <h2>2) Requisiti per altezza connettore (slot-filling)</h2>
    <label>Domanda</label>
    <textarea id="req_q" rows="2" placeholder="Es: Quale altezza per CTF su lamiera con soletta 60 mm?"></textarea>
    <div style="margin-top:8px">
      <button class="primary" onclick="req()">Invia</button>
    </div>
    <pre id="req_out"></pre>
  </section>

  <section>
    <h2>3) Calcolo altezza (quando i dati sono completi)</h2>
    <label>Domanda completa</label>
    <textarea id="calc_q" rows="2" placeholder="Es: CTF su lamiera, soletta 60 mm, copriferro 25 mm."></textarea>
    <div style="margin-top:8px">
      <button class="primary" onclick="calc()">Calcola</button>
    </div>
    <pre id="calc_out"></pre>
  </section>

  <script>
    const base = window.location.origin;

    async function postJSON(url, body){
      const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body || {})});
      let txt = await r.text();
      try { txt = JSON.stringify(JSON.parse(txt), null, 2); } catch(e) {}
      return txt;
    }

    async function ask(){
      const domanda = document.getElementById('ask_q').value.trim();
      const out = document.getElementById('ask_out'); out.textContent = '...';
      out.textContent = await postJSON(base + '/ask_chatgpt', {domanda});
    }

    async function req(){
      const domanda = document.getElementById('req_q').value.trim();
      const out = document.getElementById('req_out'); out.textContent = '...';
      out.textContent = await postJSON(base + '/requisiti_connettore', {domanda});
    }

    async function calc(){
      const domanda = document.getElementById('calc_q').value.trim();
      const out = document.getElementById('calc_out'); out.textContent = '...';
      out.textContent = await postJSON(base + '/altezza_connettore', {domanda});
    }
  </script>
</body>
</html>"""
    return Response(html, mimetype="text/html")

# ---------- Endpoint di debug: elenco rotte ----------
@app.get("/routes")
def routes():
    rules = [str(r) for r in app.url_map.iter_rules()]
    return jsonify({"routes": rules})

# ---------- Debug console ----------
print("ROUTES:", app.url_map)

# ---------- Avvio locale ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
