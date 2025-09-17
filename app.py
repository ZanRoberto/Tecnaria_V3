import os, re, glob, logging
from flask import Flask, request, jsonify, Response, redirect
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
from openai import OpenAI

# ===== Logging =====
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ===== App & CORS =====
app = Flask(__name__)
CORS(app, resources={r"/ask": {"origins": "*"}})

# ===== ENV =====
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL   = os.environ.get("OPENAI_MODEL", "gpt-5")

def _parse_float(val, default=0.0):
    try:
        if val is None: return default
        v = str(val).strip().lower()
        if v in ("", "none", "null", "nil"): return default
        return float(v)
    except Exception:
        return default

# 0 => non passare temperature (alcuni modelli vogliono solo default=1)
TEMPERATURE = _parse_float(os.environ.get("OPENAI_TEMPERATURE"), 0.0)
NOTE_DIR    = os.environ.get("NOTE_DIR", "documenti_gTab")

# ===== OpenAI client =====
if not OPENAI_API_KEY:
    logging.warning("OPENAI_API_KEY non impostata. /ask restituirà errore.")
client = OpenAI(api_key=OPENAI_API_KEY)

# ===== Guard-rail =====
BANNED = [r"\bHBV\b", r"\bFVA\b", r"\bAvantravetto\b", r"\bT[\- ]?Connect\b", r"\bAlfa\b"]

SYSTEM_MSG = {
    "role": "system",
    "content": (
        "Sei un esperto dei prodotti Tecnaria S.p.A. di Bassano del Grappa. "
        "Rispondi SOLO su prodotti ufficiali Tecnaria: connettori CTF/CTL, CEM-E, MINI CEM-E, V-CEM-E, CTCEM, "
        "Diapason, Omega, Manicotto GTS; chiodatrice Spit P560 e accessori; certificazioni (ETA/DoP/CE); "
        "manuali di posa, capitolati, computi metrici, assistenza, posa in opera. "
        "Se la domanda esce da questo perimetro o cita prodotti non Tecnaria, rispondi: "
        "'Non posso rispondere: non è un prodotto Tecnaria ufficiale.' "
        "Stile: sintetico, preciso, puntato. Italiano."
    )
}

# ===== Stili =====
STYLE_HINTS = {
    "A": "Formato: 2–3 bullet essenziali, niente chiusura.",
    "B": "Formato: Titolo (<=80c) + 3–4 bullet tecnici + riga finale 'Se ti serve altro su Tecnaria, chiedi pure.'",
    "C": "Formato: Titolo (<=100c) + 5–8 punti tecnici + breve suggerimento operativo."
}
STYLE_TOKENS = {"A": 180, "B": 280, "C": 380}

def normalize_style(val):
    if not val: return "B"
    v = str(val).strip().upper()
    if v in ("A","SHORT"): return "A"
    if v in ("C","DETAILED","LONG"): return "C"
    return "B"

def banned(text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in BANNED)

# ===== NOTE TECNICHE LOCALI =====
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

# ===== ROUTES =====

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
        "temperature": TEMPERATURE
    }), 200

def _responses_call(system_msg, user_msg, style):
    """Chiama il Responses API e ritorna sempre testo (output_text)."""
    params = {
        "model": OPENAI_MODEL,
        "input": [
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": user_msg}
        ],
        "top_p": 1,
        "max_output_tokens": STYLE_TOKENS.get(style, 280)
    }
    if TEMPERATURE and TEMPERATURE > 0:
        params["temperature"] = TEMPERATURE

    resp = client.responses.create(**params)
    logging.info(f"RAW RESPONSES: {resp}")

    # Via preferita
    text = getattr(resp, "output_text", None)
    if text:
        return text.strip()

    # Fallback robusto: ricompone dai pezzi
    parts = []
    out = getattr(resp, "output", None) or []
    for item in out:
        if getattr(item, "type", "") == "message":
            for c in getattr(item, "content", []) or []:
                if getattr(c, "type", "") == "output_text":
                    t = getattr(c, "text", "") or ""
                    if t:
                        parts.append(t)
    return "".join(parts).strip()

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
        # 1) Prompt con stile
        user_prompt = f"Domanda utente: {q}\n\n{STYLE_HINTS.get(style,'')}"
        ans = _responses_call(SYSTEM_MSG["content"], user_prompt, style)

        # 2) Retry semplice se vuota la prima
        if not ans:
            logging.info("Prima risposta vuota. Retry senza hint stile.")
            ans = _responses_call(SYSTEM_MSG["content"], q, style)

        if not ans:
            ans = "Non ho ricevuto testo dal modello in questa richiesta."

        if banned(ans):
            ans = "Non posso rispondere: non è un prodotto Tecnaria ufficiale."

        ans = attach_local_note(ans, q)
        return jsonify({"answer": ans, "style_used": style, "source":"responses_api"}), 200

    except Exception as e:
        logging.exception("Errore OpenAI")
        return jsonify({"error": f"OpenAI error: {str(e)}"}), 500

# ===== UI =====
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
        <label><input type="radio" name="style" value="B" checked> B — Standard</label>
        <label><input type="radio" name="style" value="C"> C — Dettagliata</label>
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
          `Model: ${sj.model} • Temp: ${sj.temperature} • Note dir: ${sj.note_dir} (exists: ${sj.note_dir_exists})`;
      }catch(e){ /* ignore */ }
    }
  </script>
</body>
</html>"""

@app.get("/ui")
def ui():
    return Response(HTML_UI, mimetype="text/html")

# ===== Error handling =====
@app.errorhandler(HTTPException)
def _http(e: HTTPException):
    return jsonify({"error": e.description, "code": e.code}), e.code

@app.errorhandler(Exception)
def _any(e: Exception):
    logging.exception("Errore imprevisto")
    return jsonify({"error": str(e)}), 500

# ===== Local run =====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
