# app.py — Backend Flask per Bot Tecnaria (A/B/C)
# Avvio su Render/Heroku:
#   gunicorn app:app --timeout 120 --workers=1 --threads=2 --preload -b 0.0.0.0:$PORT

from __future__ import annotations
import os, re, logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from flask import Flask, request, jsonify, make_response
from flask_cors import CORS

# =============================================================================
# Config & App
# =============================================================================

APP_NAME = os.getenv("APP_NAME", "TecnariaBot")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # cambia se vuoi
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Logging pulito
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(APP_NAME)

# =============================================================================
# Template loader A/B/C
# =============================================================================

TEMPLATES_DIR = Path("templates")
TEMPLATE_FILES = {
    "breve": "TEMPLATE_A_BREVE.txt",
    "standard": "TEMPLATE_B_STANDARD.txt",
    "dettagliata": "TEMPLATE_C_DETTAGLIATA.txt",
}

def _load_templates() -> Dict[str, str]:
    """Carica i template A/B/C dalla cartella templates/"""
    templates: Dict[str, str] = {}
    for mode, filename in TEMPLATE_FILES.items():
        path = TEMPLATES_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"Template mancante: {path}")
        templates[mode] = path.read_text(encoding="utf-8")
    return templates

# Cache template (reload in DEBUG)
_TEMPLATES_CACHE: Dict[str, str] | None = None

def get_templates() -> Dict[str, str]:
    global _TEMPLATES_CACHE
    if DEBUG or _TEMPLATES_CACHE is None:
        _TEMPLATES_CACHE = _load_templates()
    return _TEMPLATES_CACHE

def build_prompt(mode: str, question: str, context: str | None = None) -> str:
    templates = get_templates()
    tpl = templates.get(mode, templates["dettagliata"])  # default: C tecnico
    return tpl.replace("{question}", question).replace("{context}", context or "")

# =============================================================================
# Guardrail per la modalità C (dettagliata/tecnica)
# =============================================================================

CRITICAL_KEYS = ("passo gola", "V_L,Ed", "cls", "direzione lamiera")

def missing_critical_inputs(text: str) -> List[str]:
    """Euristiche semplici per capire se nella domanda/contesto ci sono i parametri chiave."""
    found: List[str] = []
    if re.search(r"\b(gola|passo\s*gola|rib|pitch)\b", text, re.I):
        found.append("passo gola")
    if re.search(r"\bV\s*L\s*,?\s*Ed|kN/m\b", text, re.I):
        found.append("V_L,Ed")
    if re.search(r"\bC(\d{2}/\d{2})\b|\bcls\b", text, re.I):
        found.append("cls")
    if re.search(r"\btrasversal(e|i)|longitudinal(e|i)|direzione\s*lamiera\b", text, re.I):
        found.append("direzione lamiera")
    return [k for k in CRITICAL_KEYS if k not in found]

def prepare_input(mode: str, question: str, context: str | None = None) -> str:
    """Se modalità C e mancano i dati, chiedi i parametri in UNA riga e fermati."""
    if mode == "dettagliata":
        missing = missing_critical_inputs((question + " " + (context or "")).strip())
        if len(missing) == len(CRITICAL_KEYS):
            # Non proseguire verso l'LLM: chiedi i dati e basta
            return f"Per procedere servono: {', '.join(CRITICAL_KEYS)}. Indicali e riprova."
    return build_prompt(mode, question, context)

# =============================================================================
# LLM wrapper (OpenAI). Se manca OPENAI_API_KEY, ritorna il prompt (debug).
# =============================================================================

def llm_respond(prompt: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        # Fallback utile per test: restituisce il prompt generato
        return f"[NO_API_KEY] Prompt generato:\n\n{prompt}"

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Sei un assistente Tecnaria. "
                        "Segui rigorosamente lo stile del template fornito nel messaggio utente."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        return resp.choices[0].message.content
    except Exception as e:
        log.exception("Errore LLM")
        return f"[LLM_ERROR] {e}\n\nPrompt:\n{prompt}"

# =============================================================================
# Routes
# =============================================================================

@app.get("/")
def root():
    """Health + mini pagina per test rapido dal browser."""
    html = f"""<!doctype html>
<html lang="it">
<head><meta charset="utf-8"><title>{APP_NAME}</title></head>
<body>
<h1>{APP_NAME}</h1>
<p>Deploy ok — {datetime.utcnow().isoformat()}Z</p>
<form method="post" action="/api/answer" onsubmit="event.preventDefault(); send();">
  <label>Domanda:</label><br/>
  <textarea id="q" rows="4" cols="80">Quale altezza di connettore CTF devo usare?</textarea><br/><br/>
  <label>Modalità:</label>
  <select id="m">
    <option value="breve">breve</option>
    <option value="standard">standard</option>
    <option value="dettagliata" selected>dettagliata</option>
  </select><br/><br/>
  <label>Contesto (facoltativo):</label><br/>
  <input id="c" size="80" placeholder="es. lamiera H55, V_L,Ed=150 kN/m, cls C30/37, passo gola 150 mm, lamiera trasversale" />
  <br/><br/>
  <button type="submit">Invia</button>
</form>
<pre id="out" style="white-space:pre-wrap;border:1px solid #ddd;padding:8px;margin-top:16px;"></pre>
<script>
async function send() {{
  const question = document.getElementById('q').value;
  const mode = document.getElementById('m').value;
  const context = document.getElementById('c').value;
  const r = await fetch('/api/answer', {{
    method: 'POST',
    headers: {{'Content-Type':'application/json'}},
    body: JSON.stringify({{question, mode, context}})
  }});
  const j = await r.json();
  document.getElementById('out').textContent = JSON.stringify(j, null, 2);
}}
</script>
</body></html>"""
    return make_response(html, 200)

@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "app": APP_NAME, "model": MODEL_NAME})

@app.get("/api/modes")
def modes():
    return jsonify({"modes": ["breve", "standard", "dettagliata"], "default": "dettagliata"})

@app.post("/api/answer")
def answer():
    try:
        data: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
        question = (data.get("question") or "").strip()
        mode = (data.get("mode") or "dettagliata").strip().lower()
        context = (data.get("context") or "").strip()

        if not question:
            return jsonify({"error": "Missing 'question'"}), 400

        prompt = prepare_input(mode, question, context)

        # Se il guardrail ha prodotto la richiesta dati, non chiamare l'LLM
        if prompt.startswith("Per procedere servono:"):
            return jsonify({"mode": mode, "answer": prompt})

        answer_text = llm_respond(prompt)
        return jsonify(
            {
                "mode": mode,
                "model": MODEL_NAME,
                "answer": answer_text,
                "meta": {"template_used": mode if mode in TEMPLATE_FILES else "dettagliata"},
            }
        )

    except Exception as e:
        log.exception("Errore /api/answer")
        return jsonify({"error": str(e)}), 500

# =============================================================================
# Error Handlers
# =============================================================================

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(405)
def not_allowed(e):
    return jsonify({"error": "Method not allowed"}), 405

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Internal server error"}), 500

# Nota: niente if __name__ == "__main__": il run lo fa gunicorn
