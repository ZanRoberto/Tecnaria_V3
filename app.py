# app.py — Backend Flask per TecnariaBot (A/B/C) + index.html statico
# Avvio consigliato:
#   gunicorn app:app --timeout 120 --workers=1 --threads=2 --preload -b 0.0.0.0:$PORT

from __future__ import annotations
import os, re, logging
from pathlib import Path
from typing import Dict, Any, List

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# =============================================================================
# Config
# =============================================================================
APP_NAME = os.getenv("APP_NAME", "TecnariaBot")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # cambia se vuoi
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app, resources={r"/api/*": {"origins": "*"}})

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
    templates: Dict[str, str] = {}
    for mode, filename in TEMPLATE_FILES.items():
        path = TEMPLATES_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"Template mancante: {path}")
        templates[mode] = path.read_text(encoding="utf-8")
    return templates

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
# Guardrail modalità C (tecnica)
# =============================================================================
CRITICAL_KEYS = ("passo gola", "V_L,Ed", "cls", "direzione lamiera")

def missing_critical_inputs(text: str) -> List[str]:
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
    if mode == "dettagliata":
        missing = missing_critical_inputs((question + " " + (context or "")).strip())
        if len(missing) == len(CRITICAL_KEYS):
            return f"Per procedere servono: {', '.join(CRITICAL_KEYS)}. Indicali e riprova."
    return build_prompt(mode, question, context)

# =============================================================================
# LLM wrapper (OpenAI). Se manca OPENAI_API_KEY, ritorna il prompt (debug)
# =============================================================================
def llm_respond(prompt: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return f"[NO_API_KEY] Prompt generato:\n\n{prompt}"
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            temperature=0.2,
            messages=[
                {"role": "system",
                 "content": "Sei un assistente Tecnaria. Segui rigorosamente lo stile del template fornito nel messaggio utente."},
                {"role": "user", "content": prompt},
            ],
        )
        return resp.choices[0].message.content
    except Exception as e:
        log.exception("Errore LLM")
        return f"[LLM_ERROR] {e}\n\nPrompt:\n{prompt}"

# =============================================================================
# ROUTES
# =============================================================================

# 1) SERVE index.html (UI bella) — >>> QUESTA È LA DIFFERENZA CHIAVE <<<
@app.get("/")
def root():
    # serve il file index.html dalla root del progetto (non la vecchia pagina generata)
    return send_from_directory(".", "index.html")

# 2) API health
@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "app": APP_NAME, "model": MODEL_NAME})

# 3) API answer (A/B/C)
@app.post("/api/answer")
def answer():
    data: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
    question = (data.get("question") or "").strip()
    mode = (data.get("mode") or "dettagliata").strip().lower()
    context = (data.get("context") or "").strip()

    if not question:
        return jsonify({"error": "Missing 'question'"}), 400

    prompt = prepare_input(mode, question, context)
    if prompt.startswith("Per procedere servono:"):
        return jsonify({"mode": mode, "answer": prompt})

    answer_text = llm_respond(prompt)
    return jsonify({
        "mode": mode,
        "model": MODEL_NAME,
        "answer": answer_text,
        "meta": {"template_used": mode if mode in TEMPLATE_FILES else "dettagliata"},
    })

# 4) DEBUG: lista file static (per capire se Render li vede)
@app.get("/api/debug/list-static")
def list_static():
    root = Path("static")
    listing = []
    if root.exists():
        for p in root.rglob("*"):
            if p.is_file():
                listing.append(str(p).replace("\\", "/"))
    return jsonify({"static_files": listing})

# Error handlers (essenziali)
@app.errorhandler(404)
def _404(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def _500(e):
    return jsonify({"error": "Internal server error"}), 500

# Niente if __name__ == "__main__": usi gunicorn
