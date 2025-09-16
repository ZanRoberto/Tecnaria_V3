# app.py
# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify, Response
import os, json, requests

app = Flask(__name__)

# ============================================================
# 1) Brand guard: SOLO Tecnaria S.p.A. di Bassano del Grappa
# ============================================================
SYSTEM_BRAND_GUARD = (
    "Rispondi esclusivamente su prodotti Tecnaria S.p.A. di Bassano del Grappa "
    "(esempi: CTF, CTL, Diapason, CEM-E, HBV). "
    "Se la domanda riguarda marchi o articoli non Tecnaria, rispondi soltanto: "
    "\"Posso trattare esclusivamente prodotti Tecnaria S.p.A. di Bassano del Grappa.\" "
    "Non inventare codici o prodotti inesistenti. Rispondi in italiano, in modo conciso e accurato."
)

# ============================================================
# 2) Health check
# ============================================================
@app.get("/")
def health():
    return "ok"

# ============================================================
# 3) LLM adapter (OpenAI compat)
# ============================================================
def _llm_chat(messages, model=None, temperature=0.0, timeout=60):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = (model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")).strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY mancante")
    r = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "temperature": float(temperature), "messages": messages},
        timeout=timeout
    )
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]

def _safe_json_loads(raw: str):
    try:
        return json.loads(raw)
    except Exception:
        return {"status": "ERROR", "raw": (raw or "")[:2000]}

# ============================================================
# 4) (Facoltativo) Enrichment da fonti esterne (es. Google Docs)
#    Imposta KNOWLEDGE_WEBHOOK_URL a un tuo servizio che risponde:
#    POST { query: "...", intent: "..." } -> { snippets: ["...", "..."] }
# ============================================================
def _knowledge_enrich(query: str, intent: str = ""):
    url = os.getenv("KNOWLEDGE_WEBHOOK_URL", "").strip()
    if not url:
        return []
    try:
        resp = requests.post(url, json={"query": query, "intent": intent}, timeout=15)
        if resp.ok:
            data = resp.json()
            return data.get("snippets", [])[:8]  # limita a 8 frammenti
    except Exception:
        pass
    return []

# ============================================================
# 5) Intent & Schema dinamico (estendibile)
#    Aggiungi nuovi intent con campi richiesti/opzionali e prompt di calcolo.
# ============================================================
INTENT_SCHEMAS = {
    "scegliere_altezza_connettore": {
        "description": "Scelta altezza connettore Tecnaria",
        "required_fields": ["prodotto", "spessore_soletta_mm", "copriferro_mm", "supporto"],
        "optional_fields": ["classe_fuoco", "note"],
        "calcolo_prompt": """Sei un configuratore Tecnaria. Dati i parametri, scegli l’altezza corretta del connettore e il codice.
Parametri:
- prodotto: {prodotto}
- spessore_soletta_mm: {spessore_soletta_mm}
- copriferro_mm: {copriferro_mm}
- supporto: {supporto}
- classe_fuoco: {classe_fuoco}

Rispondi SOLO in JSON:
{{
 "altezza_connettore_mm": <numero>,
 "codice_prodotto": "<string>",
 "motivazione": "<breve spiegazione in max 2 frasi>",
 "testo_cliente": "Connettore {{codice_prodotto}} consigliato (altezza {{altezza_connettore_mm}} mm). Confermi?"
}}"""
    },
    # Esempi per futuri intent (estendibili senza cambiare codice):
    "preventivo": {
        "description": "Raccolta dati per preventivo Tecnaria",
        "required_fields": ["prodotto", "quantita", "cantiere_provincia"],
        "optional_fields": ["classe_fuoco", "note"],
        "calcolo_prompt": """Sei un assistente preventivi Tecnaria. Usa i dati per proporre un riepilogo.
Parametri:
- prodotto: {prodotto}
- quantita: {quantita}
- cantiere_provincia: {cantiere_provincia}
- classe_fuoco: {classe_fuoco}
- note: {note}

Rispondi SOLO in JSON:
{{
 "riepilogo": "<testo breve>",
 "prossimi_passi": "<cosa serve per completare il preventivo>"
}}"""
    },
    "posa": {
        "description": "Istruzioni di posa Tecnaria",
        "required_fields": ["prodotto", "supporto"],
        "optional_fields": ["spessore_soletta_mm", "copriferro_mm", "classe_fuoco", "note"],
        "calcolo_prompt": """Sei un tecnico di posa Tecnaria. Fornisci istruzioni sintetiche e aderenti al prodotto.
Parametri:
- prodotto: {prodotto}
- supporto: {supporto}
- spessore_soletta_mm: {spessore_soletta_mm}
- copriferro_mm: {copriferro_mm}
- classe_fuoco: {classe_fuoco}

Rispondi SOLO in JSON:
{{
 "istruzioni": "<max 5 punti elenco brevi>",
 "avvertenze": "<max 2 avvertenze>",
 "note": "<opzionale>"
}}"""
    },
}

# ============================================================
# 6) Prompt base di ESTRAZIONE (dinamico sullo schema)
# ============================================================
PROMPT_ESTRAZIONE_BASE = """Sei un estrattore di requisiti per domande sui prodotti Tecnaria (Bassano del Grappa).
Dato il testo utente e lo SCHEMA, estrai in JSON i campi noti, l'intento, e indica cosa manca.

SCHEMA (intent={INTENT}):
- required_fields: {REQUIRED}
- optional_fields: {OPTIONAL}

Linee guida:
- Se la domanda non è su prodotti Tecnaria, rispondi con: "Posso trattare esclusivamente prodotti Tecnaria S.p.A. di Bassano del Grappa."
- Non proporre soluzioni se mancano campi richiesti.
- Genera al massimo UNA domanda di follow-up mirata ai campi mancanti.
- Rispondi SOLO con JSON valido.

FORMATI:

Caso A - Mancano campi richiesti:
{{
 "status": "MISSING",
 "intent": "{INTENT}",
 "found": {{...campi estratti...}},
 "missing_fields": ["campo1", "campo2"],
 "followup_question": "Un'unica domanda chiara per ottenere i valori mancanti"
}}

Caso B - Tutti i campi richiesti presenti:
{{
 "status": "READY",
 "intent": "{INTENT}",
 "found": {{...campi estratti...}},
 "checklist_ok": ["campo1 fornito", "campo2 fornito"]
}}

TESTO UTENTE:
<<<{DOMANDA}>>>

CONOSCENZA ESTERNA (se presente):
<<<{SNIPPETS}>>>"""

def _estrai_requisiti_dinamico(domanda: str, intent_key: str, snippets=None):
    schema = INTENT_SCHEMAS[intent_key]
    prompt = PROMPT_ESTRAZIONE_BASE \
        .replace("{INTENT}", intent_key) \
        .replace("{REQUIRED}", ", ".join(schema["required_fields"])) \
        .replace("{OPTIONAL}", ", ".join(schema.get("optional_fields", []))) \
        .replace("{DOMANDA}", domanda) \
        .replace("{SNIPPETS}", "\n".join(snippets or []))
    content = _llm_chat(
        messages=[
            {"role": "system", "content": SYSTEM_BRAND_GUARD + " Rispondi SOLO con JSON valido. Niente testo extra."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.0
    )
    return _safe_json_loads(content)

def _calcolo_dinamico(intent_key: str, found: dict):
    schema = INTENT_SCHEMAS[intent_key]
    # Prepara prompt calcolo con tutti i campi (usa string.format su chiavi presenti)
    template = schema["calcolo_prompt"]
    # Per evitare KeyError, prepara un dict con default stringa vuota
    fmt_dict = {k: found.get(k, "") for k in set(
        schema["required_fields"] + schema.get("optional_fields", [])
    )}
    content = _llm_chat(
        messages=[
            {"role": "system", "content": SYSTEM_BRAND_GUARD + " Rispondi SOLO con JSON valido. Niente testo extra."},
            {"role": "user", "content": template.format(**fmt_dict)}
        ],
        temperature=0.0
    )
    return _safe_json_loads(content)

# ============================================================
# 7) Endpoint DOMANDA LIBERA (sempre solo Tecnaria)
# ============================================================
@app.post("/ask_chatgpt")
def ask_chatgpt():
    payload = request.get_json(silent=True) or {}
    domanda = (payload.get("domanda") or "").strip()
    if not domanda:
        return jsonify({"status": "ERROR", "detail": "Campo 'domanda' mancante"}), 400
    try:
        # Enrichment (opzionale)
        snippets = _knowledge_enrich(domanda, intent="")
        preface = ""
        if snippets:
            preface = "Informazioni aggiuntive (fonte interna):\n" + "\n".join(f"- {s}" for s in snippets[:6])
        content = _llm_chat(
            messages=[
                {"role": "system", "content": SYSTEM_BRAND_GUARD},
                {"role": "user", "content": (preface + "\n\n" + domanda).strip()}
            ],
            temperature=0.0
        )
        return jsonify({"status": "OK", "answer": content}), 200
    except Exception as e:
        return jsonify({"status": "ERROR", "detail": str(e)}), 500

# ============================================================
# 8) Endpoint UNIFICATO /assist (dinamico, multi-intent, multi-turno)
#    Body:
#    {
#      "domanda": "...",             # messaggio più recente dell'utente
#      "intent": "scegliere_altezza_connettore" | "preventivo" | "posa" | ...
#      "history": [ {"role":"user"/"assistant","content":"..."} ],   # opzionale
#    }
#    Ritorna:
#      - MISSING + followup_question (dinamico)
#      - READY   + "suggest_next": "calcolo"
#      - OK      + result JSON del calcolo
# ============================================================
@app.post("/assist")
def assist():
    payload = request.get_json(silent=True) or {}
    domanda = (payload.get("domanda") or "").strip()
    intent_key = (payload.get("intent") or "scegliere_altezza_connettore").strip()
    if intent_key not in INTENT_SCHEMAS:
        return jsonify({"status": "ERROR", "detail": f"Intent non supportato: {intent_key}"}), 400
    if not domanda:
        return jsonify({"status": "ERROR", "detail": "Campo 'domanda' mancante"}), 400

    try:
        # Enrichment (opzionale)
        snippets = _knowledge_enrich(domanda, intent=intent_key)

        # 1) Estrazione requisiti secondo schema
        step = _estrai_requisiti_dinamico(domanda, intent_key, snippets)

        if step.get("status") == "MISSING":
            return jsonify({
                "status": "ASK_CLIENT",
                "intent": intent_key,
                "question": step.get("followup_question", "Serve un dato aggiuntivo."),
                "found_partial": step.get("found", {}),
                "missing": step.get("missing_fields", [])
            }), 200

        if step.get("status") == "READY":
            # 2) Se sei in modalità "assist", puoi decidere se calcolare già ora o restituire READY.
            # Qui calcoliamo SUBITO (puoi cambiare in base al tuo UX)
            result = _calcolo_dinamico(intent_key, step.get("found", {}))
            return jsonify({
                "status": "OK",
                "intent": intent_key,
                "params": step.get("found", {}),
                "result": result
            }), 200

        return jsonify({"status": "ERROR", "detail": step}), 500

    except Exception as e:
        return jsonify({"status": "ERROR", "detail": str(e)}), 500

# ============================================================
# 9) Endpoint specifici legacy (compatibilità)
# ============================================================
@app.post("/requisiti_connettore")
def requisiti_connettore():
    payload = request.get_json(silent=True) or {}
    domanda = (payload.get("domanda") or "").strip()
    if not domanda:
        return jsonify({"status":"ERROR","detail":"Campo 'domanda' mancante"}), 400
    try:
        snippets = _knowledge_enrich(domanda, intent="scegliere_altezza_connettore")
        step = _estrai_requisiti_dinamico(domanda, "scegliere_altezza_connettore", snippets)
        if step.get("status") == "MISSING":
            return jsonify({
                "status":"ASK_CLIENT",
                "question": step.get("followup_question","Serve un dato aggiuntivo."),
                "found_partial": step.get("found", {}),
                "missing": step.get("missing_fields", [])
            }), 200
        if step.get("status") == "READY":
            return jsonify({
                "status":"READY",
                "found": step.get("found", {}),
                "checklist_ok": step.get("checklist_ok", []),
                "prossimi_passi": "Ora si può procedere al calcolo tecnico."
            }), 200
        return jsonify({"status":"ERROR","detail": step}), 500
    except Exception as e:
        return jsonify({"status":"ERROR","detail":str(e)}), 500

@app.post("/altezza_connettore")
def altezza_connettore():
    payload = request.get_json(silent=True) or {}
    domanda = (payload.get("domanda") or "").strip()
    if not domanda:
        return jsonify({"status":"ERROR","detail":"Campo 'domanda' mancante"}), 400
    try:
        snippets = _knowledge_enrich(domanda, intent="scegliere_altezza_connettore")
        step = _estrai_requisiti_dinamico(domanda, "scegliere_altezza_connettore", snippets)
        if step.get("status") == "MISSING":
            return jsonify({
                "status":"ASK_CLIENT",
                "question": step.get("followup_question","Serve un dato aggiuntivo."),
                "found_partial": step.get("found", {}),
                "missing": step.get("missing_fields", [])
            }), 200
        if step.get("status") == "READY":
            result = _calcolo_dinamico("scegliere_altezza_connettore", step.get("found", {}))
            return jsonify({"status":"OK", "params": step.get("found", {}), "result": result}), 200
        return jsonify({"status":"ERROR","detail": step}), 500
    except Exception as e:
        return jsonify({"status":"ERROR","detail":str(e)}), 500

# ============================================================
# 10) Pannello HTML (no f-string)
# ============================================================
@app.get("/panel")
def panel():
    html = """<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <title>Tecnaria Bot Panel</title>
  <style>
    body{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:960px;margin:32px auto;padding:0 16px}
    h1{margin:0 0 16px} h2{margin:24px 0 8px}
    section{border:1px solid #e5e5e5;border-radius:12px;padding:16px;margin:16px 0;box-shadow:0 1px 2px rgba(0,0,0,.03)}
    label{display:block;margin:8px 0 4px}
    textarea,input,select{width:100%;padding:10px;border:1px solid #ccc;border-radius:8px}
    button{padding:10px 16px;border:0;border-radius:10px;cursor:pointer}
    button.primary{background:#111;color:#fff}
    pre{background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px;white-space:pre-wrap}
    small{color:#666}
    code.k{background:#f2f2f2;padding:2px 6px;border-radius:6px}
    .row{display:flex;gap:12px;align-items:center}
    .row > *{flex:1}
  </style>
</head>
<body>
  <h1>✅ Tecnaria Bot — Pannello Test</h1>
  <p><small>Base URL: <code class="k" id="base-url"></code></small></p>

  <section>
    <h2>1) Domanda libera (SOLO Tecnaria)</h2>
    <label>Domanda</label>
    <textarea id="ask_q" rows="2" placeholder="Es: CHE CONNETTORI VENDE TECNARIA?"></textarea>
    <div style="margin-top:8px">
      <button class="primary" onclick="ask()">Invia</button>
    </div>
    <pre id="ask_out"></pre>
  </section>

  <section>
    <h2>2) Assistente dinamico (/assist)</h2>
    <div class="row">
      <div>
        <label>Intent</label>
        <select id="intent">
          <option value="scegliere_altezza_connettore" selected>scegliere_altezza_connettore</option>
          <option value="preventivo">preventivo</option>
          <option value="posa">posa</option>
        </select>
      </div>
      <div>
        <label>Domanda / Risposta utente</label>
        <textarea id="assist_q" rows="2" placeholder="Es: Quale altezza per CTF su lamiera, soletta 60 mm?"></textarea>
      </div>
    </div>
    <div style="margin-top:8px">
      <button class="primary" onclick="assist()">Invia</button>
    </div>
    <pre id="assist_out"></pre>
  </section>

  <section>
    <h2>3) Legacy — Requisiti & Calcolo separati</h2>
    <label>Requisiti: domanda</label>
    <textarea id="req_q" rows="2" placeholder="Es: Quale altezza per CTF su lamiera con soletta 60 mm?"></textarea>
    <div style="margin-top:8px">
      <button class="primary" onclick="req()">/requisiti_connettore</button>
    </div>
    <pre id="req_out"></pre>

    <label style="margin-top:12px">Calcolo: domanda completa</label>
    <textarea id="calc_q" rows="2" placeholder="Es: CTF su lamiera grecata, soletta 60 mm, copriferro 25 mm."></textarea>
    <div style="margin-top:8px">
      <button class="primary" onclick="calc()">/altezza_connettore</button>
    </div>
    <pre id="calc_out"></pre>
  </section>

  <script>
    const base = window.location.origin;
    document.getElementById('base-url').textContent = base;

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

    async function assist(){
      const intent = document.getElementById('intent').value;
      const domanda = document.getElementById('assist_q').value.trim();
      const out = document.getElementById('assist_out'); out.textContent = '...';
      out.textContent = await postJSON(base + '/assist', {intent, domanda});
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

# ============================================================
# 11) Debug: elenco rotte
# ============================================================
@app.get("/routes")
def routes():
    rules = [str(r) for r in app.url_map.iter_rules()]
    return jsonify({"routes": rules})

# ============================================================
# 12) Main (locale)
# ============================================================
print("ROUTES:", app.url_map)
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
