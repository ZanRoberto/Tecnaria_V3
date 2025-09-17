import os, re, glob, logging
from flask import Flask, request, jsonify, Response, redirect
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

# ===================================
# Logging
# ===================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ===================================
# Flask app
# ===================================
app = Flask(__name__)
CORS(app, resources={r"/ask": {"origins": "*"}})

# ===================================
# ENV
# ===================================
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL   = os.environ.get("OPENAI_MODEL", "gpt-4o")  # consigliato per stabilità
NOTE_DIR       = os.environ.get("NOTE_DIR", "documenti_gTab")

def _parse_float(val, default=0.0):
    try:
        if val is None: return default
        v = str(val).strip().lower()
        if v in ("", "none", "null", "nil"): return default
        return float(v)
    except Exception:
        return default

# 0 => NON passare temperature (molti modelli “nuovi” vogliono default=1)
TEMPERATURE = _parse_float(os.environ.get("OPENAI_TEMPERATURE"), 0.0)

# ===================================
# OpenAI client (compat nuovo/vecchio SDK)
# ===================================
NEW_SDK = True
openai = None
client = None
try:
    from openai import OpenAI  # SDK >= 1.x
    if not OPENAI_API_KEY:
        logging.warning("OPENAI_API_KEY non impostata. /ask restituirà errore.")
    client = OpenAI(api_key=OPENAI_API_KEY)
    logging.info("OpenAI SDK: NUOVO (>=1.x) — uso Responses API")
except Exception:
    import openai as _openai  # SDK <= 0.28.x
    openai = _openai
    NEW_SDK = False
    if not OPENAI_API_KEY:
        logging.warning("OPENAI_API_KEY non impostata. /ask restituirà errore.")
    openai.api_key = OPENAI_API_KEY
    logging.info("OpenAI SDK: LEGACY (<=0.28.x) — uso Chat Completions")

# ===================================
# Guard-rail + perimetro prodotti
# ===================================
BANNED = [r"\bHBV\b", r"\bFVA\b", r"\bAvantravetto\b", r"\bT[\- ]?Connect\b", r"\bAlfa\b"]

SYSTEM_TEXT = (
    "Sei un esperto dei prodotti Tecnaria S.p.A. di Bassano del Grappa. "
    "Rispondi in modo completo, strutturato e operativo: titolo breve + punti tecnici, "
    "con esempi pratici e indicazioni di posa. Includi, se utile, avvertenze e tolleranze. "
    "Non inventare dati: se servono parametri di progetto, spiega cosa chiedere al cliente. "
    "Resta nel perimetro Tecnaria (connettori CTF/CTL, CEM-E, MINI CEM-E, V-CEM-E, CTCEM, Diapason, Omega, GTS; "
    "Spit P560; certificazioni, manuali di posa, capitolati, computi). "
    "Se la domanda non è su prodotti Tecnaria, rispondi che non puoi. Italiano, tono tecnico ma chiaro."
)

def banned(text: str) -> bool:
    """Prima whitelist (prodotti Tecnaria), poi ban di termini non Tecnaria."""
    q = (text or "").lower()
    for keys in TOPIC_KEYS.values():
        if any(k in q for k in keys):
            return False
    return any(re.search(p, text, re.IGNORECASE) for p in BANNED)

# ===================================
# Stili A/B/C
# ===================================
STYLE_HINTS = {
    "A": "Formato: 2–3 bullet essenziali, niente chiusura.",
    "B": "Formato: Titolo (<=80c) + 3–4 bullet tecnici + riga finale 'Se ti serve altro su Tecnaria, chiedi pure.'",
    "C": "Formato: Titolo (<=100c) + 5–8 punti tecnici + breve suggerimento operativo."
}
STYLE_TOKENS = {"A": 250, "B": 450, "C": 700}

def normalize_style(val):
    if not val: return "B"
    v = str(val).strip().upper()
    if v in ("A","SHORT"): return "A"
    if v in ("C","DETAILED","LONG"): return "C"
    return "B"

# ===================================
# NOTE TECNICHE LOCALI (documenti_gTab/<TOPIC>/*.txt)
# ===================================
TOPIC_KEYS = {
    "CTF": ["ctf","cft","acciaio-calcestruzzo","lamiera","grecata"],
    "CTL": ["ctl","legno-calcestruzzo","legno","solaio in legno"],
    "CEM-E": ["cem-e","ripresa di getto","nuovo su esistente","cucitura"],
    "MINI CEM-E": ["mini cem-e","mini cem"],
    "V-CEM-E": ["v-cem-e","vcem","v cem"],
    "CTCEM": ["ctcem","ct cem"],
    "DIAPASON": ["diapason"],
    "OMEGA": ["omega"],
    "GTS": ["manicotto gts","gts"],
    "P560": ["p560","spit p560","chiodatrice"]
}

def guess_topic(question: str) -> str | None:
    q = (question or "").lower()
    for topic, keys in TOPIC_KEYS.items():
        if any(k in q for k in keys):
            return topic
    return None

def load_note_files(topic: str):
    folder = os.path.join(NOTE_DIR, topic)
    return sorted(glob.glob(os.path.join(folder, "*.txt")))

def best_local_note(question: str, topic: str) -> str | None:
    paths = load_note_files(topic)
    if not paths:
        return None
    ql = (question or "").lower()
    best_score, best_text = 0, None
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8") as f:
                txt = f.read()
        except Exception:
            continue
        base = os.path.basename(p).lower()
        tokens = [w for w in re.split(r"[^a-z0-9àèéìòóù]+", ql) if len(w) > 2]
        score = sum(base.count(w) + txt.lower().count(w) for w in tokens)
        if score > best_score:
            best_score, best_text = score, txt.strip()
    if not best_text:
        return None
    MAX_CHARS = 1200
    if len(best_text) > MAX_CHARS:
        best_text = best_text[:MAX_CHARS].rstrip() + " …"
    return best_text

def attach_local_note(answer: str, question: str) -> str:
    topic = guess_topic(question)
    if not topic:
        return answer
    note = best_local_note(question, topic)
    if not note:
        return answer
    lines = note.splitlines()
    if lines and len(lines[0]) <= 100:
        title = lines[0].strip()
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        block = f"---\n📎 Nota tecnica (locale) — {title}\n{body}" if body else f"---\n📎 Nota tecnica (locale)\n{title}"
    else:
        block = f"---\n📎 Nota tecnica (locale)\n{note}"
    return (answer or "").rstrip() + "\n\n" + block

# ===================================
# Helpers chiamate OpenAI (nuovo/legacy)
# ===================================
def ask_new_sdk(system_text: str, user_text: str, style_tokens: int, temperature: float) -> str:
    """Responses API (SDK >= 1.x)."""
    from openai import OpenAI  # type: ignore
    params = {
        "model": OPENAI_MODEL,
        "input": [
            {"role": "system", "content": system_text},
            {"role": "user",   "content": user_text}
        ],
        "top_p": 1,
        "max_output_tokens": style_tokens
    }
    if temperature and temperature > 0:
        params["temperature"] = temperature
    resp = client.responses.create(**params)  # type: ignore
    logging.info(f"RAW RESPONSES: {resp}")
    text = getattr(resp, "output_text", None)
    if text:
        return text.strip()
    # Fallback: ricomponi dai pezzi
    out = getattr(resp, "output", None) or []
    parts = []
    for item in out:
        if getattr(item, "type", "") == "message":
            for c in getattr(item, "content", []) or []:
                if getattr(c, "type", "") == "output_text":
                    t = getattr(c, "text", "") or ""
                    if t: parts.append(t)
    return "".join(parts).strip()

def ask_legacy_sdk(system_text: str, user_text: str, style_tokens: int, temperature: float) -> str:
    """Chat Completions (SDK <= 0.28.x)."""
    kwargs = dict(
        model=OPENAI_MODEL,
        messages=[{"role":"system","content": system_text},
                  {"role":"user","content": user_text}],
        top_p=1,
        max_tokens=style_tokens
    )
    if temperature and temperature > 0:
        kwargs["temperature"] = temperature
    resp = openai.ChatCompletion.create(**kwargs)  # type: ignore
    return (resp["choices"][0]["message"]["content"] or "").strip()

def call_model(question: str, style: str) -> str:
    style_tokens = STYLE_TOKENS.get(style, 450)
    user_prompt = f"Domanda utente: {question}\n\n{STYLE_HINTS.get(style,'')}"
    if NEW_SDK:
        text = ask_new_sdk(SYSTEM_TEXT, user_prompt, style_tokens, TEMPERATURE)
        if not text:
            logging.info("Prima risposta vuota. Retry senza hint stile (NEW_SDK).")
            text = ask_new_sdk(SYSTEM_TEXT, question, style_tokens, TEMPERATURE)
        return text
    else:
        text = ask_legacy_sdk(SYSTEM_TEXT, user_prompt, style_tokens, TEMPERATURE)
        if not text:
            logging.info("Prima risposta vuota. Retry senza hint stile (LEGACY).")
            text = ask_legacy_sdk(SYSTEM_TEXT, question, style_tokens, TEMPERATURE)
        return text

# ===================================
# Routes
# ===================================
@app.get("/")
def root_redirect():
    return redirect("/ui", code=302)

@app.get("/status")
def status():
    return jsonify({
        "status": "ok",
        "service": "Tecnaria QA",
        "note_dir_exists": os.path.isdir(NOTE_DIR),
        "note_dir": NOTE_DIR,
        "endpoints": {"ask": "POST /ask {question: str, style?: 'A'|'B'|'C'}", "ui": "GET /ui"},
        "model": OPENAI_MODEL,
        "temperature": TEMPERATURE,
        "sdk": "new" if NEW_SDK else "legacy"
    }), 200

@app.post("/ask")
def ask():
    if not OPENAI_API_KEY:
        return jsonify({"error":"OPENAI_API_KEY non configurata"}), 500

    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"error":"Body JSON non valido."}), 400

    q = (data.get("question") or "").strip()
    style = normalize_style(data.get("style"))

    if not q:
        return jsonify({"error":"Missing 'question'."}), 400
    if banned(q):
        return jsonify({"answer":"Non posso rispondere: non è un prodotto Tecnaria ufficiale.", "source":"guardrail"}), 200

    try:
        ans = call_model(q, style)
        if not ans:
            ans = "Non ho ricevuto testo dal modello in questa richiesta."
        if banned(ans):
            ans = "Non posso rispondere: non è un prodotto Tecnaria ufficiale."
        ans = attach_local_note(ans, q)
        return jsonify({"answer": ans, "style_used": style, "source": "openai_new" if NEW_SDK else "openai_legacy"}), 200
    except Exception as e:
        logging.exception("Errore OpenAI")
        return jsonify({"error": f"OpenAI error: {str(e)}"}), 500

# ===================================
# UI embedded
# ===================================
HTML_UI = """<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Tecnaria QA Bot</title>
  <style>
    :root { --bg:#0f172a; --card:#111827; --ink:#e5e7eb; --muted:#9ca3af; --accent:#22d3ee; }
    *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,Segoe UI,Roboto,Arial}
    .wrap{max-width:900px;margin:40px auto;padding:0 16px}
    .card{background:var(--card);border:1px solid #1f2937;border-radius:16px;padding:20px;box-shadow:0 6px 24px rgba(0,0,0,.35)}
    h1{margin:0 0 8px;font-size:22px} .sub{color:var(--muted);font-size:14px;margin-bottom:16px}
    textarea{width:100%;min-height:110px;border-radius:12px;border:1px solid #374151;background:#0b1220;color:var(--ink);padding:12px}
    .btn{background:var(--accent);color:#041014;border:0;border-radius:12px;padding:12px 16px;font-weight:700;cursor:pointer;margin-top:10px}
    .out{white-space:pre-wrap;background:#0b1220;border:1px solid #1f2937;border-radius:12px;padding:14px;margin-top:16px}
    label{display:inline-block;margin:8px 12px 0 0}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Tecnaria QA Bot</h1>
      <div class="sub">Domande libere su Tecnaria. Scegli A/B/C. Se esiste una nota locale, la vedi in fondo.</div>
      <textarea id="question" placeholder="Es. Mi spieghi il connettore CTF?"></textarea>
      <div>
        <label><input type="radio" name="style" value="A"> A — Breve</label>
        <label><input type="radio" name="style" value="B"> B — Standard</label>
        <label><input type="radio" name="style" value="C" checked> C — Dettagliata</label>
      </div>
      <button class="btn" onclick="ask()">Chiedi</button>
      <div id="output" class="out" style="display:none"></div>
      <div id="err" class="out" style="display:none; border-color:#7f1d1d; background:#450a0a; color:#fecaca"></div>
      <div class="sub" id="meta"></div>
    </div>
  </div>
  <script>
    async function ask(){
      const q = document.getElementById('question').value;
      const style = document.querySelector('input[name="style"]:checked').value;
      const out = document.getElementById('output');
      const err = document.getElementById('err');
      out.style.display='none'; err.style.display='none';
      try{
        const r = await fetch('/ask', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({question:q, style})
        });
        const j = await r.json();
        if(!r.ok || j.error){
          err.textContent = j.error || ('HTTP '+r.status);
          err.style.display = 'block';
        }else{
          out.textContent = j.answer || '(nessuna risposta)';
          out.style.display = 'block';
        }
      }catch(e){
        err.textContent = 'Errore di rete: ' + e.message;
        err.style.display = 'block';
      }
      try{
        const s = await fetch('/status', {cache:'no-store'});
        const sj = await s.json();
        document.getElementById('meta').textContent =
          `Model: ${sj.model} • Temp: ${sj.temperature} • SDK: ${sj.sdk} • Note dir: ${sj.note_dir} (exists: ${sj.note_dir_exists})`;
      }catch(e){ /* ignore */ }
    }
  </script>
</body>
</html>"""

@app.get("/ui")
def ui():
    return Response(HTML_UI, mimetype="text/html")

# ===================================
# Error handling
# ===================================
@app.errorhandler(HTTPException)
def _http(e: HTTPException):
    return jsonify({"error": e.description, "code": e.code}), e.code

@app.errorhandler(Exception)
def _any(e: Exception):
    logging.exception("Errore imprevisto")
    return jsonify({"error": str(e)}), 500

# ===================================
# Local run
# ===================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
