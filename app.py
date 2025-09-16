from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
from openai import OpenAI
import re
import os

app = Flask(__name__)
CORS(app)

# 🔑 OpenAI: legge la chiave da OPENAI_API_KEY
client = OpenAI()

MODEL = "gpt-5"
TEMPERATURE = 0

SYSTEM_MESSAGE = {
    "role": "system",
    "content": (
        "Sei un esperto dei prodotti Tecnaria S.p.A. di Bassano del Grappa. "
        "Rispondi solo sui prodotti ufficiali Tecnaria (connettori CTF, CTL, CEM-E, Diapason; "
        "chiodatrice Spit P560 e accessori; posa, certificazioni, capitolati, assistenza). "
        "Se la richiesta non riguarda Tecnaria, dillo brevemente e chiedi di riformulare."
    )
}

# ——— Classificatore semplice per suggerire la famiglia connettore
def classify_family(q: str):
    txt = q.lower()

    k_riprese = [r"ripresa\s+di\s+getto", r"nuovo\s+su\s+esistente", r"collegare\s+due\s+getti",
                 r"calcestruzzo[-\s]*calcestruzzo", r"cucitura\s+calcestruzzo"]
    k_ripart_legno = [r"ripartizion(e|i)\s+carico", r"distribuzione\s+carichi", r"\bdiapason\b", r"piastra\s+su\s+legno"]
    k_acc_cem = [r"acciaio[-\s]*calcestruzzo", r"acciaio\s*-\s*calcestruzzo", r"lamiera\s*grecata",
                 r"\bgrecata\b", r"trave in acciaio", r"profilo in acciaio"]
    k_legno_cem = [r"legno[-\s]*calcestruzzo", r"legno\s*-\s*calcestruzzo", r"solaio in legno",
                   r"trav(i|e)\s*in\s*legno", r"tavolato"]

    def any_match(keys): return any(re.search(p, txt) for p in keys)

    if any_match(k_riprese):
        return {"family": "CEM-E", "why": "Ripresa di getto / nuovo su esistente → collegamento calcestruzzo-calcestruzzo."}
    if any_match(k_ripart_legno):
        return {"family": "Diapason", "why": "Ripartizione carichi su legno-calcestruzzo → piastra Diapason."}
    if any_match(k_acc_cem):
        return {"family": "CTF", "why": "Acciaio-calcestruzzo / lamiera grecata → connettore a piolo CTF."}
    if any_match(k_legno_cem):
        return {"family": "CTL", "why": "Legno-calcestruzzo → connettore CTL (base liscia o dentellata)."}

    return {"family": None, "why": "Manca contesto: specifica acciaio/lamiera, legno-calcestruzzo o ripresa di getto."}

def fuse_answer(llm_text: str, hint: dict):
    tail = "\n\n—\n"
    if hint["family"]:
        tail += (f"🔎 **Suggerimento connettore (deterministico)**: **{hint['family']}**\n"
                 f"Motivo: {hint['why']}\n"
                 "Nota: altezze/codici esatti dipendono da spessori/luci e verifica del progettista.")
    else:
        tail += ("🔎 **Suggerimento connettore (deterministico)**: non definibile.\n"
                 f"Motivo: {hint['why']}\n"
                 "Indica se è acciaio-calcestruzzo (CTF), legno-calcestruzzo (CTL) o ripresa di getto (CEM-E).")
    return llm_text + tail

@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL, "temperature": TEMPERATURE}

@app.post("/ask")
def ask():
    data = request.get_json(force=True) or {}
    question = (data.get("question") or "").strip()
    mode = (data.get("mode") or "fused").strip().lower()  # 'raw' oppure 'fused'
    if not question:
        return jsonify({"error": "Manca 'question'"}), 400
    if mode not in ("raw", "fused"):
        mode = "fused"

    # 1) Risposta del modello (domanda libera)
    chat = client.chat.completions.create(
        model=MODEL,
        messages=[SYSTEM_MESSAGE, {"role": "user", "content": question}],
        temperature=TEMPERATURE
    )
    llm_text = chat.choices[0].message["content"]

    # 2) In base a mode, restituisco:
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
        "raw_llm_answer": llm_text if mode == "fused" else None,  # comodo per confronto
        "deterministic_hint": hint,
        "model": MODEL,
        "temperature": TEMPERATURE
    })

# ——— Interfaccia web con switch RAW/FUSED
@app.get("/")
def home():
    html = f"""
<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Tecnaria QA Bot</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 24px; }}
    .wrap {{ max-width: 920px; margin: 0 auto; }}
    h1 {{ margin: 0 0 8px; }}
    .sub {{ color: #555; margin-bottom: 16px; }}
    .row {{ display: flex; gap: 8px; align-items: center; }}
    input[type=text] {{ flex: 1; padding: 12px; border: 1px solid #ccc; border-radius: 10px; }}
    button {{ padding: 12px 16px; border: 0; border-radius: 10px; background: #0d6efd; color: #fff; cursor: pointer; }}
    button:disabled {{ opacity: .6; cursor: wait; }}
    .toggle {{ margin-left: 8px; user-select:none; }}
    .log {{ white-space: pre-wrap; background: #f7f7f9; border: 1px solid #eee; padding: 16px; border-radius: 12px; margin-top: 16px; }}
    .footer {{ color:#777; font-size: 12px; margin-top: 18px; }}
    .pill {{ display:inline-block; font-size:12px; background:#eef; color:#223; padding:3px 8px; border-radius:999px; margin-left:8px; }}
    label {{ display:flex; align-items:center; gap:6px; font-size: 14px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Tecnaria QA Bot <span class="pill">{MODEL} / temp {TEMPERATURE}</span></h1>
    <div class="sub">Domande libere. Spunta <b>Solo risposta LLM</b> per confrontare con ChatGPT standard.</div>

    <div class="row">
      <input id="q" type="text" placeholder="Es. Su lamiera grecata con trave in acciaio, che connettore uso?" autofocus />
      <button id="btn" onclick="ask()">Chiedi</button>
      <label class="toggle"><input type="checkbox" id="raw"/> Solo risposta LLM</label>
    </div>

    <div id="out" class="log" style="display:none"></div>
    <div class="footer">API: <code>POST /ask</code> con JSON <code>{{'{{'}}"question":"...", "mode":"raw|fused"{{'}}'}}</code></div>
  </div>

<script>
async function ask() {{
  const btn = document.getElementById('btn');
  const q = document.getElementById('q').value.trim();
  const out = document.getElementById('out');
  const raw = document.getElementById('raw').checked;
  if(!q) return;

  btn.disabled = true; out.style.display = 'block';
  out.textContent = "⏳ Sto pensando...";

  try {{
    const r = await fetch('/ask', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ question: q, mode: raw ? 'raw' : 'fused' }})
    }});
    const j = await r.json();
    if (j.error) {{
      out.textContent = "❌ " + j.error;
    }} else {{
      out.textContent = j.answer;
      // Se vuoi vedere anche il raw sotto, decommenta:
      // if (j.raw_llm_answer) out.textContent += "\\n\\n[RAW LLM]\\n" + j.raw_llm_answer;
    }}
  }} catch(e) {{
    out.textContent = "❌ Errore di rete: " + e.message;
  }} finally {{
    btn.disabled = false;
  }}
}}
document.getElementById('q').addEventListener('keydown', (ev) => {{
  if (ev.key === 'Enter') ask();
}});
</script>
</body>
</html>
"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=True)
