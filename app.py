# app.py
from flask import Flask, request, jsonify, render_template_string
from scraper_tecnaria import kb

app = Flask(__name__)

# --- ROUTES DIAGNOSTICA ---
@app.get("/health")
def health():
    return jsonify({"ok": True, "files": len(kb.files_loaded), "entries": len(kb.entries)})

@app.get("/ls")
def ls():
    return jsonify({
        "doc_dir": str(kb.doc_dir),
        "files_loaded": kb.files_loaded,
        "entries": len(kb.entries)
    })

@app.post("/reload")
def reload_index():
    info = kb.reload()
    return jsonify({"reloaded": True, **info})

@app.get("/debug")
def debug():
    q = request.args.get("q", "orari sede")
    top = kb.debug_candidates(q, top=5)
    return jsonify({"q": q, "top": [{"file": f, "text": t, "score": s} for f, t, s in top]})

# --- API BOT ---
@app.post("/ask")
def ask():
    data = request.get_json(silent=True) or {}
    q = (data.get("q") or "").strip()
    if not q:
        return jsonify({"answer": "Scrivi una domanda."})
    answer = kb.answer(q)
    return jsonify({"answer": answer})

# --- HOME (opzionale minimal) ---
WELCOME = (
    "Benvenuto nel mondo dell’Intelligenza Artificiale di Tecnaria: "
    "qui ogni tua domanda trova risposta, tra esperienza ingegneristica e innovazione digitale."
)

@app.get("/")
def home():
    # piccola pagina di prova; sostituiscila con il tuo template se vuoi
    html = f"""
    <!doctype html><html><head><meta charset="utf-8"><title>Tecnaria · Assistente</title></head>
    <body style="font-family: system-ui, sans-serif; max-width: 900px; margin: 40px auto;">
      <img src="{{{{ url_for('static', filename='img/logo_tecnaria.png') }}}}" alt="Logo Tecnaria" style="max-width: 180px; height: auto;">
      <h1>Tecnaria · Assistente documentale</h1>
      <div id="risposta" style="padding:12px;border:1px solid #ddd;border-radius:8px;min-height:80px">{WELCOME}</div>
      <form onsubmit="sendQ(event)" style="margin-top:12px">
        <input id="q" placeholder="Scrivi una domanda..." style="width:70%;padding:8px">
        <button>Chiedi</button>
      </form>
      <script>
        async function sendQ(ev){{
          ev.preventDefault();
          const q = document.getElementById('q').value;
          const r = await fetch('/ask', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{q}})}});
          const j = await r.json();
          document.getElementById('risposta').textContent = j.answer || '—';
        }}
      </script>
    </body></html>
    """
    return render_template_string(html)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
