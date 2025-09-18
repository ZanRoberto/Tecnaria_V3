# app.py — Backend Flask per TecnariaBot (A/B/C) + UI statica + fallback anti-502
# Avvio consigliato su Render/Heroku:
#   gunicorn app:app --timeout 120 --workers=1 --threads=2 --preload -b 0.0.0.0:$PORT

from __future__ import annotations
import os, re, logging
from pathlib import Path
from typing import Dict, Any, List

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS

# =============================================================================
# Config
# =============================================================================
APP_NAME = os.getenv("APP_NAME", "TecnariaBot")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")   # cambia se vuoi
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app, resources={r"/api/*": {"origins": "*"}})

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(APP_NAME)

# =============================================================================
# Template loader A/B/C (con fallback se mancano file)
# =============================================================================
TEMPLATES_DIR = Path("templates")
TEMPLATE_FILES = {
    "breve": "TEMPLATE_A_BREVE.txt",
    "standard": "TEMPLATE_B_STANDARD.txt",
    "dettagliata": "TEMPLATE_C_DETTAGLIATA.txt",
}

def _load_templates() -> Dict[str, str]:
    """Carica i template; se un file manca NON crasha: usa un mini-fallback."""
    templates: Dict[str, str] = {}
    for mode, filename in TEMPLATE_FILES.items():
        path = TEMPLATES_DIR / filename
        if not path.exists():
            # Fallback minimale, così il servizio resta su anche senza file
            templates[mode] = (
                f"[TEMPLATE MANCANTE: {filename}]\n"
                "Domanda: {question}\nContesto: {context}\n"
                "(Aggiungi i template reali in /templates per ottenere lo stile definitivo.)"
            )
        else:
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
#   - Si applica SOLO a domande su connettori/solai (CTF/CTL/CEM-E, lamiera, soletta, ecc.)
#   - Si BYPASSA per attrezzi/strumenti (es. P560), chiodatrici, manuali, ecc.
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

CONNECTOR_KEYWORDS = [
    "ctf", "ctl", "cem", "cem-e", "diapason",
    "connettore", "connettori",
    "lamiera", "soletta", "collaborante", "solaio", "acciaio-calcestruzzo",
    "hbv", "hi-bond", "rib", "gola", "passo gola",
]

def is_connector_topic(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in CONNECTOR_KEYWORDS)

def prepare_input(mode: str, question: str, context: str | None = None) -> str:
    if mode == "dettagliata":
        full_text = (question + " " + (context or "")).strip()
        # Applica guardrail SOLO se l'argomento è connettori/solai
        if is_connector_topic(full_text):
            missing = missing_critical_inputs(full_text)
            if len(missing) == len(CRITICAL_KEYS):
                return f"Per procedere servono: {', '.join(CRITICAL_KEYS)}. Indicali e riprova."
        # Altrimenti (es. P560, strumenti, manuali) bypass e vai al modello
    return build_prompt(mode, question, context)

# =============================================================================
# LLM wrapper (OpenAI). Se manca OPENAI_API_KEY, ritorna il prompt (debug)
# =============================================================================
def llm_respond(prompt: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        # Non crashare: utile in test/PR
        return f"[NO_API_KEY] Prompt generato:\n\n{prompt}"
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)  # OK con openai>=1.x
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

# 1) SERVE index.html (UI bella) — con fallback se manca
@app.get("/")
def root():
    index_path = Path("index.html")
    if index_path.exists():
        return send_from_directory(".", "index.html")
    # Fallback HTML se manca l'index (così non va 500/502)
    return Response(
        "<h1>TecnariaBot</h1><p>index.html non trovato nel root del progetto.</p>",
        mimetype="text/html",
    )

# 2) API health
@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "app": APP_NAME, "model": MODEL_NAME})

# 3) API modes (utile per UI)
@app.get("/api/modes")
def modes():
    return jsonify({"modes": ["breve", "standard", "dettagliata"], "default": "dettagliata"})

# 4) API answer (A/B/C)
@app.post("/api/answer")
def answer():
    data: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
    question = (data.get("question") or "").strip()
    mode = (data.get("mode") or "dettagliata").strip().lower()
    context = (data.get("context") or "").strip()

    if not question:
        return jsonify({"error": "Missing 'question'"}), 400

    prompt = prepare_input(mode, question, context)
    # Se il guardrail ha chiesto dati, non chiamare il modello
    if prompt.startswith("Per procedere servono:"):
        return jsonify({"mode": mode, "answer": prompt})

    answer_text = llm_respond(prompt)
    return jsonify({
        "mode": mode,
        "model": MODEL_NAME,
        "answer": answer_text,
        "meta": {"template_used": mode if mode in TEMPLATE_FILES else "dettagliata"},
    })

# 5) DEBUG: lista file static
@app.get("/api/debug/list-static")
def list_static():
    root = Path("static")
    listing = []
    if root.exists():
        for p in root.rglob("*"):
            if p.is_file():
                listing.append(str(p).replace("\\", "/"))
    return jsonify({"static_files": listing})

# 6) DEBUG: lista template con stato (presente/mancante)
@app.get("/api/debug/list-templates")
def list_templates():
    out = []
    for mode, filename in TEMPLATE_FILES.items():
        p = TEMPLATES_DIR / filename
        out.append({
            "mode": mode,
            "file": str(p),
            "exists": p.exists(),
            "size": (p.stat().st_size if p.exists() else 0)
        })
    return jsonify({"templates": out})

# Error handlers essenziali
@app.errorhandler(404)
def _404(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def _500(e):
    return jsonify({"error": "Internal server error"}), 500

# (nessun if __name__ == "__main__": usi gunicorn)
