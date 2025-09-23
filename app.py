# app.py — TecnariaBot (Modalità "ChatGPT puro" 1:1)
# ---------------------------------------------------
# ✔ Risposte identiche a ChatGPT: inoltro domanda pari-pari al modello (temp=0.0)
# ✔ Scope: SOLO prodotti/servizi Tecnaria (CTF, CTL, CEM-E, Diapason, P560)
# ✔ P560 NON è un connettore: è una chiodatrice (attrezzo)
# ✔ Allegati/Note tecniche (immagini/PDF/video) in coda alla risposta
# ✘ Nessun mini-wizard / ✘ Nessun calcolo PRd (SEZIONI REMMATE in fondo per uso futuro)
#
# Static/attachments attesi (creali se non esistono):
#   static/img/p560_magazzino.jpg
#   static/docs/ctf_scheda.pdf
#   static/docs/ctf_posa.pdf
#   static/docs/ctl_scheda.pdf
#   static/docs/cem-e_scheda.pdf
#   static/docs/diapason_scheda.pdf
#   (eventuali .mp4/.png/.pdf aggiuntivi: verranno linkati se mappati in tool_attachments)

import os
from typing import Any, Dict, List
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_cors import CORS

# =============== OpenAI client ===============
try:
    from openai import OpenAI
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
    OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception:
    client, OPENAI_MODEL = None, "gpt-4o-mini"

# =============== Flask ===============
app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# =============== Contatti ===============
CONTACTS = {
    "ragione_sociale": "TECNARIA S.p.A.",
    "indirizzo": "Viale Pecori Giraldi, 55 — 36061 Bassano del Grappa (VI)",
    "telefono": "+39 0424 502029",
    "email": "info@tecnaria.com",
    "sito": "https://tecnaria.com"
}
CONTACTS_KEYWORDS = [
    "contatti","contatto","telefono","numero","chiamare","email","mail","indirizzo",
    "sede","dove siete","orari","pec","ufficio","assistenza","referente","commerciale"
]

def build_contacts_block() -> str:
    return (f"{CONTACTS['ragione_sociale']} — {CONTACTS['indirizzo']} — "
            f"Tel {CONTACTS['telefono']} — {CONTACTS['email']} — {CONTACTS['sito']}")

# =============== Scope Tecnaria & denylist ===============
TECHNARIA_WHITELIST = {
    "tecnaria", "ctf", "ctl", "cem", "cem-e", "diapason", "p560",
    "connettore", "connettori", "lamiera", "soletta", "solaio",
    "acciaio-calcestruzzo", "acciaio legno", "acciaio-legno", "legno-calcestruzzo"
}
DENYLIST = {
    "hbv","xhbv","x-hbv","hi-bond","hibond","ribdeck","comflor","metsec","holorib",
    "fva","p800","hilti shear","x hbv","hi bond","x-hbond","hbond"
}

def is_in_scope(question: str) -> bool:
    q = (question or "").lower()
    if any(d in q for d in DENYLIST):
        return False
    return any(k in q for k in TECHNARIA_WHITELIST) or any(k in q for k in CONTACTS_KEYWORDS)

# =============== Allegati / Note tecniche ===============
def tool_attachments(question: str) -> List[Dict[str, Any]]:
    """Mappa semplice keyword → allegati. Aggiungi qui nuovi file."""
    q = (question or "").lower()
    out: List[Dict[str, Any]] = []

    # P560: foto, manuale (se disponibile)
    if "p560" in q or "chiodatrice" in q or "pistola" in q:
        out.append({"label": "Foto P560", "href": "/static/img/p560_magazzino.jpg", "preview": True})
        # out.append({"label": "Manuale P560 (PDF)", "href": "/static/docs/p560_manual.pdf", "preview": True})

    # CTF
    if "ctf" in q:
        out.append({"label":"Scheda tecnica CTF (PDF)","href":"/static/docs/ctf_scheda.pdf","preview":True})
        out.append({"label":"Istruzioni di posa CTF (PDF)","href":"/static/docs/ctf_posa.pdf","preview":True})

    # CTL (acciaio-legno)
    if "ctl" in q or "acciaio-legno" in q or "acciaio legno" in q:
        out.append({"label":"Scheda tecnica CTL (PDF)","href":"/static/docs/ctl_scheda.pdf","preview":True})

    # CEM-E
    if "cem-e" in q or "ceme" in q:
        out.append({"label":"Scheda tecnica CEM-E (PDF)","href":"/static/docs/cem-e_scheda.pdf","preview":True})

    # Diapason
    if "diapason" in q:
        out.append({"label":"Scheda tecnica Diapason (PDF)","href":"/static/docs/diapason_scheda.pdf","preview":True})

    # Esempio video (se vuoi linkare un mp4 locale)
    # if "posa" in q and "ctf" in q:
    #     out.append({"label":"Video posa CTF (MP4)","href":"/static/docs/ctf_posa.mp4","preview":False})

    return out

# =============== Prompt minimale (ChatGPT-like) ===============
SYSTEM_PROMPT = (
    "Sei TecnariaBot, assistente ufficiale di Tecnaria S.p.A. (Bassano del Grappa). "
    "Rispondi ESCLUSIVAMENTE su prodotti/servizi Tecnaria: CTF, CTL, CEM-E, Diapason e la P560. "
    "La P560 NON è un connettore: è una chiodatrice a polvere per fissare i connettori. "
    "Tono naturale come ChatGPT. Se un dato non è noto, dillo. Non citare marchi terzi."
)

def llm_passthrough(question: str, context: str) -> str:
    """Inoltra domanda+contesto pari-pari al modello, come ChatGPT, temp=0.0."""
    if not client:
        return ("(Modalità demo) Configura OPENAI_API_KEY per risposte ChatGPT-like.\n"
                f"Domanda: {question}\nContesto: {context or '—'}")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": f"Domanda: {question.strip()}\nContesto: {context.strip() or '—'}"}
    ]
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
        temperature=0.0,
        top_p=1.0,
        max_tokens=1200,
    )
    return resp.choices[0].message.content.strip()

# =============== Routes ===============
@app.route("/")
def index():
    # L'HTML deve stare in templates/index.html (nessun markup qui dentro!)
    return render_template("index.html")

@app.route("/api/answer", methods=["POST"])
def api_answer():
    payload = request.get_json(force=True) or {}
    question = (payload.get("question") or "").strip()
    context  = (payload.get("context") or "").strip()

    # Scope Tecnaria
    if not is_in_scope(question):
        return jsonify({
            "answer": "Assistente dedicato esclusivamente a prodotti e servizi Tecnaria (CTF, CTL, CEM-E, Diapason, P560).",
            "attachments": [],
            "meta": {"in_scope": False}
        })

    # Shortcut contatti
    qlow = question.lower()
    if any(k in qlow for k in CONTACTS_KEYWORDS):
        return jsonify({
            "answer": build_contacts_block(),
            "attachments": [{"label": "Sito ufficiale Tecnaria", "href": CONTACTS["sito"], "preview": False}],
            "meta": {"in_scope": True, "type": "contacts"}
        })

    # ChatGPT-like
    answer_text = llm_passthrough(question, context)

    # Allegati/Note tecniche
    attachments = tool_attachments(question)

    return jsonify({
        "answer": answer_text,
        "attachments": attachments,
        "meta": {"in_scope": True, "type": "chatgpt"}
    })

@app.route("/static/<path:path>")
def static_proxy(path):
    return send_from_directory("static", path)

@app.route("/health")
def health():
    return "ok", 200


# ==============================
#  SEZIONI REMMATE (per il futuro)
# ==============================

# --- [DISABLED] mini-wizard/calcoli CTF ---
# def parse_ctf_inputs(context: str) -> dict:
#     """Estrattore di parametri (lamiera, soletta, V_L,Ed, cls, passo gola, direzione, ecc.)."""
#     return {}

# def ctf_select_height(params: dict) -> dict:
#     """Motore di selezione PRd/altezza da tabelle JSON."""
#     return {}

# @app.route("/api/ctf/calc", methods=["POST"])
# def api_ctf_calc():
#     """Endpoint futuro per calcolo tecnico: disattivato in questa versione."""
#     return jsonify({"error": "CTF engine disabled in ChatGPT-pure mode"}), 501


if __name__ == "__main__":
    # Avvio locale
    app.run(host="0.0.0.0", port=8000, debug=True)
