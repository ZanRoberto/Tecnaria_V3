from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
from openai import OpenAI
import os
import re

# --------------------
# Config di servizio
# --------------------
MODEL = os.getenv("OPENAI_MODEL", "gpt-5")

# TEMPERATURE:
# - Metti "default" (o lascia vuoto) per usare il default del modello (consigliato per gpt-5)
# - Oppure un numero (es. "0.2") per modelli che lo supportano
_env_temp = os.getenv("TEMPERATURE", "").strip().lower()
if _env_temp in ("", "default", "none", "null"):
    TEMPERATURE = None  # => non passeremo il parametro a OpenAI
else:
    try:
        TEMPERATURE = float(_env_temp)
    except ValueError:
        TEMPERATURE = None

SYSTEM_MESSAGE = {
    "role": "system",
    "content": (
        "Sei un esperto dei prodotti Tecnaria S.p.A. di Bassano del Grappa. "
        "Rispondi solo sui prodotti ufficiali Tecnaria (connettori CTF, CTL, CEM-E, Diapason; "
        "chiodatrice Spit P560 e accessori; posa, certificazioni, capitolati, assistenza). "
        "Se la richiesta non riguarda Tecnaria, dillo brevemente e chiedi di riformulare."
    )
}

# --------------------
# App & OpenAI client
# --------------------
app = Flask(__name__)
CORS(app)
client = OpenAI()  # legge OPENAI_API_KEY dall'ambiente

# --------------------
# Classificatore deterministico (famiglia connettore)
# --------------------
def classify_family(q: str):
    txt = (q or "").lower()

    k_riprese = [
        r"ripresa\s+di\s+getto", r"nuovo\s+su\s+esistente",
        r"collegare\s+due\s+getti", r"calcestruzzo[-\s]*calcestruzzo",
        r"cucitura\s+calcestruzzo"
    ]
    k_ripart_legno = [
        r"ripartizion(e|i)\s+carico", r"distribuzione\s+carichi",
        r"\bdiapason\b", r"piastra\s+su\s+legno"
    ]
    k_acc_cem = [
        r"acciaio[-\s]*calcestruzzo", r"acciaio\s*-\s*calcestruzzo",
        r"lamiera\s*grecata", r"\bgrecata\b", r"trave in acciaio", r"profilo in acciaio"
    ]
    k_legno_cem = [
        r"legno[-\s]*calcestruzzo", r"legno\s*-\s*calcestruzzo",
        r"solaio in legno", r"trav(i|e)\s*in\s*legno", r"tavolato"
    ]

    def any_match(keys):
        return any(re.search(p, txt) for p in keys)

    if any_match(k_riprese):
        return {"family": "CEM-E", "why": "Ripresa di getto / nuovo su esistente → collegamento calcestruzzo-calcestruzzo."}
    if any_match(k_ripart_legno):
        return {"family": "Diapason", "why": "Ripartizione carichi su legno-calcestruzzo → piastra Diapason."}
    if any_match(k_acc_cem):
        return {"family": "CTF", "why": "Acciaio-calcestruzzo / lamiera grecata → connettore a piolo CTF."}
    if any_match(k_legno_cem):
        return {"family": "CTL", "why": "Legno-calcestruzzo → connettore CTL (base liscia o dentellata)."}

    return {"family": None, "why": "Serve specificare: acciaio/lamiera, legno-calcestruzzo o ripresa di getto."}

def fuse_answer(llm_text: str, hint: dict):
    tail = "\n\n—\n"
    if hint and hint.get("family"):
        tail += (
            f"🔎 **Suggerimento connettore (deterministico)**: **{hint['family']}**\n"
            f"Motivo: {hint['why']}\n"
            "Nota: altezze/codici esatti dipendono da spessori/luci e dalla verifica del progettista."
        )
    else:
        tail += (
            "🔎 **Suggerimento connettore (deterministico)**: non definibile.\n"
            f"Motivo: {hint['why'] if hint else 'N/A'}\n"
            "Indica se è acciaio-calcestruzzo (CTF), legno-calcestruzzo (CTL) o ripresa di getto (CEM-E)."
        )
    return (llm_text or "").strip() + tail

# --------------------
# Helpers OpenAI
# --------------------
def call_openai(messages):
    # Montiamo la chiamata senza temperature se None (evita 400 su modelli che non la supportano)
    kwargs = {
        "model": MODEL,
        "messages": messages,
    }
    if TEMPERATURE is not None:
        kwargs["temperature"] = TEMPERATURE
    return client.chat.completions.create(**kwargs)

# --------------------
# Endpoints
# --------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": MODEL,
        "temperature": ("default (model-managed)" if TEMPERATURE is None else TEMPERATURE)
    }

@app.post("/ask")
def ask():
    try:
        data = request.get_json(force=True, silent=True) or {}
        question = (data.get("question") or "").strip()
        mode = (data.get("mode") or "fused").strip().lower()  # 'raw' | 'fused'
        if not question:
            return jsonify({"error": "Manca 'question'"}), 400
        if mode not in ("raw", "fused"):
            mode = "fused"

        # Chiamata OpenAI (protetta)
        try:
            chat = call_openai([SYSTEM_MESSAGE, {"role": "user", "content": question}])
            llm_text = chat.choices[0].message["content"]
        except Exception as e:
            return jsonify({"error": "OpenAI call failed", "details": str(e)}), 502

        if mode == "raw":
            final_text = llm_text
            hint = None
        else:
            hint = classify_family(question)
            final_text = fuse_answer(llm_text, hint)

        return jsonify({
            "question": question,
            "mode": mode,
            "answer": final_text,
            "raw_llm_answer": llm_text if mode == "fused" else None,
            "deterministic_hint": hint,
            "model": MODEL,
            "temperature": ("default (model-managed)" if TEMPERATURE is None else TEMPERATURE)
        })
    except Exception as e:
        return jsonify({"error": "Server error", "details": str(e)}), 500

# Error handlers globali (sempre JSON)
@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal server error", "details": str(e)}), 500

# --------------------
# Interfaccia Web (UI)
# --------------------
@app.get("/")
def home():
    html = f"""
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Tecnaria QA Bot</title>
<style>
  :root {{
    --bg:#0b1020; --card:#121833; --text:#eef1ff; --muted:#9aa4c7; --accent:#5b8cff; --ok:#38d39f; --err:#ff6b6b;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:linear-gradient(160deg,#0b1020,#0e1630 50%,#101a36); color:var(--text); font-family: Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial; }}
  .wrap {{ max-width: 960px; margin: 32px auto; padding: 0 16px; }}
  .header {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px; }}
  h1 {{ font-size: 22px; margin:0; letter-spacing:.2px; }}
  .pill {{ background:rgba(91,140,255,.15); color:#c9d7ff; padding:4px 10px; border-radius:999px; font-size:12px; }}
  .card {{ background: var(--card); border:1px solid rgba(255,255,255,.06); border-radius:16px; padding:16px; box-shadow:0 10px 30px rgba(0,0,0,.3); }}
  .row {{ display:flex; gap:10px; align-items:center; }}
  input[type=text] {{ flex:1; padding:12px 14px; background:#0e1330; border:1px solid rgba(255,255,255,.08); color:var(--text); border-radius:12px; outline:none; }}
  input[type=text]::placeholder {{ color:#8c96bf; }}
  button {{ padding:12px 16px; border:0; border-radius:12px; background:var(--accent); color:#fff; cursor:pointer; font-weight:600; }}
  button:disabled {{ opacity:.6; cursor:wait; }}
  label {{ display:flex; align-items:center; gap:8px; font-size:14px; color:var(--muted); user-select:none; }}
  .log {{ white-space:pre-wrap; background:#0e1330; border:1px solid rgba(255,255,255,.08); padding:14px; border-radius:12px; margin-top:12px; min-height:120px; }}
  .footer {{ margin-top:14px; color:#8c96bf; font-size:12px; }}
  .ok {{ color:var(--ok); }} .err {{ color:var(--err); }}
</style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <h1>Tecnaria QA Bot</h1>
      <span class="pill">{MODEL} • temp {("default" if TEMPERATURE is None else TEMPERATURE)}</span>
    </div>
    <div class="card">
      <div class="row" style="margin-bottom:10px;">
        <input id="q" type="text" placeholder="Es. Su lamiera grecata con trave in acciaio, che connettore uso?" autofocus />
        <button id="btn" onclick="ask()">Chiedi</button>
      </div>
      <label><input type="checkbox" id="raw"/> Solo risposta LLM (confronto diretto con ChatGPT)</label>
      <div id="out" class="log" style="display:none"></div>
      <div id="aux" class="footer"></div>
    </div>
    <div class="footer">API: <code>POST /ask</code> con JSON <code>{{'{{'}}"question":"...", "mode":"raw|fused"{{'}}'}}</code> • Health: <code>/health</code></div>
  </div>

<script>
async function ask(){{
  const btn = document.getElementById('btn');
  const q   = document.getElementById('q').value.trim();
  const out = document.getElementById('out');
  const aux = document.getElementById('aux');
  const raw = document.getElementById('raw').checked;

  if(!q) return;
  btn.disabled = true; out.style.display='block';
  out.textContent = "⏳ Sto pensando..."; aux.textContent = "";

  try {{
    const r = await fetch('/ask', {{
      method:'POST',
      headers: {{ 'Content-Type':'application/json' }},
      body: JSON.stringify({{ question: q, mode: raw ? 'raw' : 'fused' }})
    }});
    const ct = r.headers.get('content-type') || '';

    if(ct.includes('application/json')) {{
      const j = await r.json();
      if (j.error) {{
        out.innerHTML = "❌ <span class='err'>" + j.error + "</span>" + (j.details ? (" — " + j.details) : "");
      }} else {{
        out.textContent = j.answer || "";
        const fam = j.deterministic_hint && j.deterministic_hint.family ? j.deterministic_hint.family : "—";
        aux.innerHTML = (raw ? "Modo: RAW (solo LLM)" : "Modo: FUSED (LLM + suggerimento)") + " • Hint famiglia: <b>" + fam + "</b>";
      }}
    }} else {{
      const t = await r.text();
      out.innerHTML = "⚠️ Risposta non-JSON dal server:<br><pre>"+ t.replace(/[&<>]/g, s => ({{'&':'&amp;','<':'&lt;','>':'&gt;'}})[s]) +"</pre>";
    }}
  }} catch(e) {{
    out.innerHTML = "❌ <span class='err'>Errore di rete</span>: " + e.message;
  }} finally {{
    btn.disabled = false;
  }}
}}

document.getElementById('q').addEventListener('keydown', ev => {{
  if(ev.key==='Enter') ask();
}});
</script>
</body>
</html>
"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
