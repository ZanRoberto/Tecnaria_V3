# app.py — TecnariaBot (solo prodotti/servizi Tecnaria S.p.A. – Bassano del Grappa)
# - Guard dominio Tecnaria + denylist (CFT, FVA, HBV/HI-BOND, X-HBV)
# - Keywords ufficiali (CTF, CTL, CEM-E, Diapason, P560… + contesti)
# - Intent: CALCOLO vs POSA vs CONFRONTO (comparativa)
# - Allegati automatici
# - A/B/C via templates; append NOTE TECNICHE / ALLEGATI

from __future__ import annotations
import os, re, logging
from pathlib import Path
from typing import Dict, Any, List

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS

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

# ---------------- Templates ----------------
TEMPLATES_DIR = Path("templates")
TEMPLATE_FILES = {
    "breve": "TEMPLATE_A_BREVE.txt",
    "standard": "TEMPLATE_B_STANDARD.txt",
    "dettagliata": "TEMPLATE_C_DETTAGLIATA.txt",   # tecnico connettori/solai
    "attrezzi": "TEMPLATE_C_ATTREZZI.txt",         # tecnico P560 & co.
}
def _load_templates() -> Dict[str, str]:
    t: Dict[str, str] = {}
    for mode, filename in TEMPLATE_FILES.items():
        p = TEMPLATES_DIR / filename
        if not p.exists():
            t[mode] = f"[TEMPLATE MANCANTE: {filename}]\nDomanda: {{question}}\nContesto: {{context}}\n"
        else:
            t[mode] = p.read_text(encoding="utf-8")
    return t
_TPL: Dict[str, str] | None = None
def get_templates() -> Dict[str, str]:
    global _TPL
    if DEBUG or _TPL is None: _TPL = _load_templates()
    return _TPL
def render_template(mode_key: str, question: str, context: str | None) -> str:
    tpl = get_templates().get(mode_key, get_templates()["dettagliata"])
    return tpl.replace("{question}", question).replace("{context}", context or "")

# ---------------- Keywords ufficiali Tecnaria ----------------
TECNARIA_CONNECTORS = [
    "ctf", "connettore ctf", "piolo ctf",
    "ctl", "connettore ctl", "legno-calcestruzzo ctl",
    "cem-e", "cem e", "connettore cem-e",
    "diapason", "connettore diapason",
]
TECNARIA_CONTEXTS = [
    "acciaio-calcestruzzo", "solaio collaborante", "lamiera grecata", "soletta piena",
    "legno-calcestruzzo", "solaio legno", "travetto legno",
    "calcestruzzo esistente", "ripresa di getto", "rinforzo", "ripristino",
]
TECNARIA_TOOLS = [
    "p560", "p800", "p370", "p200", "spit", "chiodatrice", "sparachiodi"
]
TECNARIA_KEYWORDS = (
    ["tecnaria"] +
    TECNARIA_CONNECTORS +
    TECNARIA_CONTEXTS +
    TECNARIA_TOOLS
)
CONNECTOR_KEYWORDS = TECNARIA_CONNECTORS + TECNARIA_CONTEXTS

# Denylist non Tecnaria (case-insensitive)
NON_TECNARIA_TERMS = [
    "cft", "fva",
    "hbv", "hi-bond", "hibond",
    "x-hbv", "xhbv",
]

# ---------------- Intent lists ----------------
CRITICAL_KEYS = ("passo gola", "V_L,Ed", "cls", "direzione lamiera")
CALC_KEYWORDS = [
    "altezza", "dimension", "dimensionamento", "pr_d", "pr,d",
    "v_l,ed", "kn/m", "resistenza", "portata", "verifica", "capacit", "numero connettori",
    "quanto regge", "quanto portano", "quanti connettori", "quale altezza"
]
POSE_KEYWORDS = [
    "posa", "installazione", "montaggio", "consiglio", "consigli", "istruzioni",
    "distanza", "distanze", "sequenza", "tracciamento", "attrezzi", "attrezzatura",
    "chiodatrice", "spit", "p560", "dpi", "sicurezza", "manuale di posa", "come si posa",
    "come fissare", "fissaggio", "posizionamento"
]
# Intent di confronto/comparazione
COMPARISON_KEYWORDS = [
    "differenza", "differenze", "confronto", "confrontare", "vs", "contro",
    "meglio", "qual è la differenza", "che differenza c'è", "quale scegliere"
]

OFFTOPIC_BLOCK = ["sparare", "uccelli", "armi", "violenza", "caccia"]

CT_ALLOWED_TOKENS = [
    "lamiera", "h55", "h75", "soletta", "mm", "cls", "c25/30", "c30/37", "c35/45",
    "passo", "gola", "direzione", "trasversale", "longitudinale",
    "v_l,ed", "kn/m", "travi", "ipe", "hea", "heb", "s355", "interasse", "m"
]

# ---------------- Helpers: topic/intent ----------------
def is_tecnaria_topic(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in TECNARIA_KEYWORDS)
def has_non_tecnaria_terms(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in NON_TECNARIA_TERMS)
def is_non_tecnaria_only(text: str) -> bool:
    return has_non_tecnaria_terms(text) and not is_tecnaria_topic(text)

def is_connector_topic(text: str) -> bool:
    return any(kw in text.lower() for kw in CONNECTOR_KEYWORDS)
def is_tool_topic(text: str) -> bool:
    return any(kw in text.lower() for kw in TECNARIA_TOOLS)

def has_calc_intent(text: str) -> bool:
    return any(kw in text.lower() for kw in CALC_KEYWORDS)
def has_pose_intent(text: str) -> bool:
    return any(kw in text.lower() for kw in POSE_KEYWORDS)
def has_comparison_intent(text: str) -> bool:
    return any(kw in text.lower() for kw in COMPARISON_KEYWORDS)

def mentions_two_tecnaria_products(text: str) -> bool:
    t = text.lower()
    names = ["ctf", "ctl", "cem-e", "cem e", "diapason"]
    hits = [n for n in names if n in t]
    return len(set(hits)) >= 2

# ---------------- Helpers: sanitizzazione/parametri ----------------
def missing_critical_inputs(text: str) -> List[str]:
    found: List[str] = []
    if re.search(r"\b(gola|passo\s*gola|rib|pitch)\b", text, re.I): found.append("passo gola")
    if re.search(r"\bV\s*L\s*,?\s*Ed|kN/m\b", text, re.I):         found.append("V_L,Ed")
    if re.search(r"\bC(\d{2}/\d{2})\b|\bcls\b", text, re.I):        found.append("cls")
    if re.search(r"\btrasversal(e|i)|longitudinal(e|i)|direzione\s*lamiera\b", text, re.I):
        found.append("direzione lamiera")
    return [k for k in CRITICAL_KEYS if k not in found]

def sanitize_context(raw: str) -> str:
    ctx = (raw or "").strip()
    if not ctx: return ctx
    parts = re.split(r'([.!?])', ctx)
    cleaned = []
    for i in range(0, len(parts), 2):
        sentence = parts[i].strip()
        punct = parts[i+1] if i+1 < len(parts) else ""
        if any(b in sentence.lower() for b in OFFTOPIC_BLOCK): continue
        if sentence: cleaned.append(sentence + punct)
    ctx = " ".join(s.strip() for s in cleaned).strip()
    return ctx if len(ctx) <= 300 else ctx[:300].rstrip() + "..."

def whitelist_ctx_for_connectors(ctx: str) -> str:
    low = (ctx or "").lower()
    tokens = re.findall(r"[a-z0-9/._+-]+", low)
    kept: List[str] = []
    for t in tokens:
        if t in CT_ALLOWED_TOKENS or re.match(r"^\d+(mm|m|kn/m)$", t):
            kept.append(t)
    return " ".join(kept) if kept else ""

# ---------------- Allegati automatici ----------------
def tool_attachments(text: str) -> list[str]:
    t = text.lower()
    RULES = [
        { "when_any": ["p560"],
          "files": [
              ("static/img/p560_magazzino.jpg", "/static/img/p560_magazzino.jpg"),
              ("static/img/p560_scheda.pdf",    "/static/img/p560_scheda.pdf"),
          ]},
        { "when_all": ["ctf", "h55"],
          "files": [
              ("static/img/ctf_h55_tabella.png", "/static/img/ctf_h55_tabella.png"),
              ("static/img/ctf_eta.pdf",         "/static/img/ctf_eta.pdf"),
          ]},
        { "when_all": ["ctf", "h75"],
          "files": [
              ("static/img/ctf_h75_tabella.png", "/static/img/ctf_h75_tabella.png"),
              ("static/img/ctf_eta.pdf",         "/static/img/ctf_eta.pdf"),
          ]},
    ]
    out: list[str] = []
    for rule in RULES:
        if ("when_all" in rule and all(kw in t for kw in rule["when_all"])) or \
           ("when_any" in rule and any(kw in t for kw in rule["when_any"])):
            for fs_path, url_path in rule["files"]:
                if Path(fs_path).exists(): out.append(url_path)
    seen = set()
    return [u for u in out if not (u in seen or seen.add(u))]

# ---------------- Prompt routing (guard + intent) ----------------
def prepare_input(mode: str, question: str, context: str | None = None) -> tuple[str, dict]:
    meta = {"topic":"altro","calc":False,"pose":False,"needs_params":False,"guard":None}
    q, ctx = (question or ""), sanitize_context(context or "")
    all_low = (q + " " + ctx).lower()

    # Guard dominio Tecnaria
    if is_non_tecnaria_only(all_low):
        msg = ("Questo assistente risponde solo su prodotti e servizi Tecnaria. "
               "Hai citato codici/marchi non Tecnaria (es. CFT, FVA, HBV/HI-BOND, X-HBV).")
        meta["guard"] = "non_tecnaria_only"
        return msg, meta
    if not is_tecnaria_topic(all_low):
        msg = ("Questo assistente è dedicato esclusivamente a prodotti e servizi Tecnaria "
               "(CTF/CTL/CEM-E/Diapason, solai collaboranti, attrezzi P560/P800, ecc.).")
        meta["guard"] = "not_tecnaria"
        return msg, meta

    # Modalità C (dettagliata)
    if mode == "dettagliata":
        # 1) Attrezzi (priorità)
        if is_tool_topic(q.lower()):
            meta.update(topic="attrezzi")
            return render_template("attrezzi", q, ctx), meta

        # 2) Confronto tra prodotti Tecnaria -> descrittiva comparativa (mai parametri)
        if has_comparison_intent(all_low) and mentions_two_tecnaria_products(all_low):
            meta.update(topic="connettori", calc=False, pose=False)
            prompt = render_template("dettagliata", q, ctx)
            hint_cmp = (
                "\n\n[Modalità CONFRONTO: rispondi in modo comparativo e sintetico. "
                "Per ciascun prodotto evidenzia: AMBITO D'USO, MATERIALI, POSA/ATTREZZI, "
                "CERTIFICAZIONI/ETA, VANTAGGI/LIMITI, ESEMPI. "
                "NON chiedere parametri di calcolo. Concludi con GUIDA ALLA SCELTA.]"
            )
            return prompt + hint_cmp, meta

        # 3) Connettori (calcolo vs posa)
        if is_connector_topic(all_low):
            meta.update(topic="connettori", calc=has_calc_intent(all_low), pose=has_pose_intent(all_low))

            # Calcolo puro -> richiedi parametri se mancano
            if meta["calc"] and not meta["pose"]:
                filtered_ctx = whitelist_ctx_for_connectors(ctx)
                missing = missing_critical_inputs(q + " " + filtered_ctx)
                if len(missing) == len(CRITICAL_KEYS):
                    meta["needs_params"] = True
                    return f"Per procedere servono: {', '.join(CRITICAL_KEYS)}. Indicali e riprova.", meta
                return render_template("dettagliata", q, filtered_ctx), meta

            # Posa/Consigli (o misto) -> nessun guardrail
            prompt = render_template("dettagliata", q, ctx)
            hint = (
                "\n\n[Modalità POSA/CONSIGLI: fornisci istruzioni operative chiare e sequenziali, "
                "riferimenti al manuale di posa Tecnaria, attrezzi compatibili (es. Spit P560), "
                "distanze minime, attenzione a bordo/traversi, DPI e sicurezza. "
                "Non richiedere parametri di calcolo.]"
            )
            return prompt + hint, meta

        # 4) Fallback tecnico (comunque dominio Tecnaria)
        return render_template("dettagliata", q, ctx), meta

    # Modalità A/B
    return render_template(mode, q, ctx), meta

# ---------------- LLM wrapper ----------------
def llm_respond(prompt: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key: return f"[NO_API_KEY] Prompt generato:\n\n{prompt}"
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=MODEL_NAME, temperature=0.2,
            messages=[
                {"role":"system","content":"Sei un assistente Tecnaria. Rispondi solo su ambito Tecnaria e segui il template."},
                {"role":"user","content":prompt},
            ],
        )
        return resp.choices[0].message.content
    except Exception as e:
        log.exception("Errore LLM")
        return f"[LLM_ERROR] {e}\n\nPrompt:\n{prompt}"

# ---------------- ROUTES ----------------
@app.get("/")
def root():
    return send_from_directory(".", "index.html") if Path("index.html").exists() \
        else Response("<h1>TecnariaBot</h1>", mimetype="text/html")

@app.get("/api/health")
def health():
    return jsonify({"status":"ok","app":APP_NAME,"model":MODEL_NAME})

@app.post("/api/answer")
def answer():
    data: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
    q  = (data.get("question") or "").strip()
    mode = (data.get("mode") or "dettagliata").strip().lower()
    ctx = (data.get("context") or "").strip()
    if not q:
        return jsonify({"error":"Missing 'question'"}), 400

    prompt, meta = prepare_input(mode, q, ctx)

    # Guard: fuori dominio
    if meta.get("guard"):
        return jsonify({"mode":mode,"answer":prompt,"attachments":[],"meta":meta})

    # Parametri richiesti per calcolo
    if prompt.startswith("Per procedere servono:"):
        return jsonify({
            "mode": mode,
            "answer": prompt,
            "attachments": tool_attachments(q + " " + ctx),
            "meta": meta
        })

    # LLM + allegati
    answer_text = llm_respond(prompt)
    auto_attachments = tool_attachments(q + " " + ctx)
    if auto_attachments:
        answer_text += "\n\n6) NOTE TECNICHE / ALLEGATI:\n" + "\n".join(f"- {u}" for u in auto_attachments)

    return jsonify({
        "mode": mode,
        "model": MODEL_NAME,
        "answer": answer_text,
        "attachments": auto_attachments,
        "meta": meta
    })
