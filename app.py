# app.py — TecnariaBot (A/B/C + attrezzi) con sanitizzazione contesto
# Include: tool_attachments() -> attachments in /api/answer
# Avvio:
#   gunicorn app:app --timeout 120 --workers=1 --threads=2 --preload -b 0.0.0.0:$PORT

from __future__ import annotations
import os, re, logging
from pathlib import Path
from typing import Dict, Any, List, Tuple

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS

# =============================================================================
# Config
# =============================================================================
APP_NAME = os.getenv("APP_NAME", "TecnariaBot")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app, resources={r"/api/*": {"origins": "*"}})

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(APP_NAME)

# =============================================================================
# Template loader A/B/C (+ ATTREZZI) con fallback
# =============================================================================
TEMPLATES_DIR = Path("templates")
TEMPLATE_FILES = {
    "breve": "TEMPLATE_A_BREVE.txt",
    "standard": "TEMPLATE_B_STANDARD.txt",
    "dettagliata": "TEMPLATE_C_DETTAGLIATA.txt",  # tecnico connettori/solai
    "attrezzi": "TEMPLATE_C_ATTREZZI.txt",        # tecnico P560 & simili
}

def _load_templates() -> Dict[str, str]:
    t: Dict[str, str] = {}
    for mode, filename in TEMPLATE_FILES.items():
        p = TEMPLATES_DIR / filename
        if not p.exists():
            t[mode] = (
                f"[TEMPLATE MANCANTE: {filename}]\n"
                "Domanda: {question}\nContesto: {context}\n"
                "(Aggiungi i template reali in /templates per lo stile definitivo.)"
            )
        else:
            t[mode] = p.read_text(encoding="utf-8")
    return t

_TEMPLATES_CACHE: Dict[str, str] | None = None
def get_templates() -> Dict[str, str]:
    global _TEMPLATES_CACHE
    if DEBUG or _TEMPLATES_CACHE is None:
        _TEMPLATES_CACHE = _load_templates()
    return _TEMPLATES_CACHE

def render_template(mode_key: str, question: str, context: str | None) -> str:
    tpl = get_templates().get(mode_key, get_templates()["dettagliata"])
    return tpl.replace("{question}", question).replace("{context}", context or "")

# =============================================================================
# Keywords + Guardrail + Sanitizzazione
# =============================================================================
CRITICAL_KEYS = ("passo gola", "V_L,Ed", "cls", "direzione lamiera")

CONNECTOR_KEYWORDS = [
    "ctf", "ctl", "cem", "cem-e", "diapason",
    "connettore", "connettori",
    "lamiera", "soletta", "collaborante", "solaio", "acciaio-calcestruzzo",
    "hbv", "hi-bond", "rib", "gola", "passo gola",
]
TOOL_KEYWORDS = [
    "p560", "p800", "p370", "p200",
    "chiodatrice", "sparachiodi", "spit",
    "cartucce", "magazzino chiodi", "pistola a polvere"
]

OFFTOPIC_BLOCK = ["sparare", "uccelli", "armi", "violenza", "caccia"]

CT_ALLOWED_TOKENS = [
    "lamiera", "h55", "h75", "soletta", "mm", "cls", "c25/30", "c30/37", "c35/45",
    "passo", "gola", "direzione", "trasversale", "longitudinale",
    "v_l,ed", "kn/m", "travi", "ipe", "hea", "heb", "s355", "interasse", "m"
]

def is_connector_topic(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in CONNECTOR_KEYWORDS)

def is_tool_topic(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in TOOL_KEYWORDS)

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

def sanitize_context(raw: str) -> str:
    """Rimuove off-topic grossolani e limita la lunghezza (≈300 char)."""
    ctx = (raw or "").strip()
    if not ctx:
        return ctx
    parts = re.split(r'([.!?])', ctx)  # mantieni i separatori
    cleaned = []
    for i in range(0, len(parts), 2):
        sentence = parts[i].strip()
        punct = parts[i+1] if i+1 < len(parts) else ""
        low = sentence.lower()
        if any(bad in low for bad in OFFTOPIC_BLOCK):
            continue
        if sentence:
            cleaned.append(sentence + punct)
    ctx = " ".join(s.strip() for s in cleaned).strip()
    if len(ctx) > 300:
        ctx = ctx[:300].rstrip() + "..."
    return ctx

def whitelist_ctx_for_connectors(ctx: str) -> str:
    """Trattiene solo termini utili al calcolo per i connettori."""
    low = (ctx or "").lower()
    tokens = re.findall(r"[a-z0-9/._+-]+", low)
    kept: List[str] = []
    for t in tokens:
        if t in CT_ALLOWED_TOKENS or re.match(r"^\d+(mm|m|kn/m)$", t):
            kept.append(t)
    if not kept:
        return ""
    return " ".join(kept)

# =============================================================================
# Allegati automatici (backend) in base al tema
# =============================================================================
def tool_attachments(text: str) -> List[str]:
    """
    Ritorna URL di allegati "noti" in base alla domanda/contesto,
    ma SOLO se i file esistono davvero su disco.
    """
    t = text.lower()
    candidates: List[Tuple[str, str]] = []

    # Esempio: domanda su P560 -> immagine magazzino
    if "p560" in t or ("spit" in t and "560" in t):
        candidates.append(("static/img/p560_magazzino.jpg", "/static/img/p560_magazzino.jpg"))

    # Qui puoi aggiungere altre regole:
    # if "ctf h55" in t: candidates.append(("static/img/ctf_tabella.png", "/static/img/ctf_tabella.png"))

    out: List[str] = []
    for fs_path, url_path in candidates:
        if Path(fs_path).exists():
            out.append(url_path)
    return out

# =============================================================================
# Routing principale: prepare_input
# =============================================================================
def prepare_input(mode: str, question: str, context: str | None = None) -> str:
    """Decide template e se chiedere parametri. Priorità: attrezzi > connettori."""
    if mode == "dettagliata":
        q_low = question.lower()
        clean_ctx = sanitize_context(context or "")
        all_low = (question + " " + clean_ctx).lower()

        # 1) DOMANDA su attrezzi => sempre template attrezzi
        if is_tool_topic(q_low):
            return render_template("attrezzi", question, clean_ctx)

        # 2) Tema connettori/solai => guardrail + allowlist del contesto
        if is_connector_topic(all_low):
            filtered_ctx = whitelist_ctx_for_connectors(clean_ctx)
            all_low_filtered = (question + " " + filtered_ctx).lower()
            missing = missing_critical_inputs(all_low_filtered)
            if len(missing) == len(CRITICAL_KEYS):
                return f"Per procedere servono: {', '.join(CRITICAL_KEYS)}. Indicali e riprova."
            return render_template("dettagliata", question, filtered_ctx)

        # 3) fallback tecnico standard
        return render_template("dettagliata", question, clean_ctx)

    # breve/standard: passa contesto pulito
    return render_template(mode, question, sanitize_context(context or ""))

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
@app.get("/")
def root():
    index_path = Path("index.html")
    if index_path.exists():
        return send_from_directory(".", "index.html")
    return Response("<h1>TecnariaBot</h1><p>index.html non trovato.</p>", mimetype="text/html")

@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "app": APP_NAME, "model": MODEL_NAME})

@app.get("/api/modes")
def modes():
    return jsonify({"modes": ["breve", "standard", "dettagliata"], "default": "dettagliata"})

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
        # Anche in questo caso possiamo già allegare eventuali file utili
        auto_attachments = tool_attachments(question + " " + context)
        return jsonify({"mode": mode, "answer": prompt, "attachments": auto_attachments})

    answer_text = llm_respond(prompt)

    # Allegati dedotti dalla domanda+contesto (es. P560 -> foto magazzino)
    auto_attachments = tool_attachments(question + " " + context)

    return jsonify({
        "mode": mode,
        "model": MODEL_NAME,
        "answer": answer_text,
        "attachments": auto_attachments,   # <<--- NUOVO campo
        "meta": {
            "template_used": (
                "attrezzi" if is_tool_topic(question.lower()) else
                ("dettagliata" if mode == "dettagliata" else mode)
            )
        },
    })

# Debug utili
@app.get("/api/debug/list-static")
def list_static():
    root = Path("static")
    listing = []
    if root.exists():
        for p in root.rglob("*"):
            if p.is_file():
                listing.append(str(p).replace("\\", "/"))
    return jsonify({"static_files": listing})

@app.get("/api/debug/list-templates")
def list_templates():
    out = []
    for mode, filename in TEMPLATE_FILES.items():
        p = TEMPLATES_DIR / filename
        out.append({"mode": mode, "file": str(p), "exists": p.exists(), "size": (p.stat().st_size if p.exists() else 0)})
    return jsonify({"templates": out})

@app.errorhandler(404)
def _404(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def _500(e):
    return jsonify({"error": "Internal server error"}), 500
