# app.py — TecnariaBot (solo risposte stile ChatGPT, niente wizard/calcoli)
# - Scope: solo prodotti/servizi Tecnaria S.p.A. (Bassano del Grappa)
# - P560: CHIODATRICE (attrezzo), NON compare mai tra i connettori
# - Allegati/Note tecniche: mantenuti (immagini/pdf)
# - Risposte generate "come ChatGPT": inoltro diretto domanda+contesto al modello, temp=0.0

import os
from typing import Any, Dict, List, Optional
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_cors import CORS

# =============== OpenAI ===============
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

# =============== Contatti (facoltativo) ===============
CONTACTS = {
    "ragione_sociale": "TECNARIA S.p.A.",
    "indirizzo": "Viale Pecori Giraldi, 55 – 36061 Bassano del Grappa (VI)",
    "telefono": "+39 0424 502029",
    "fax": "+39 0424 502386",
    "email": "info@tecnaria.com",
    "sito": "https://tecnaria.com"
}

CONTACTS_KEYWORDS = [
    "contatti","contatto","telefono","numero","chiamare","email","mail","indirizzo",
    "sede","dove siete","orari","pec","ufficio","assistenza","referente","commerciale"
]

# =============== Scope Tecnaria & denylist ===============
# Ammessi: solo questi topic/brand Tecnaria
TECHNARIA_WHITELIST = {
    "ctf","ctl","cem","cem-e","diapason","p560","tecnaria",
    "connettore","connettori","lamiera","soletta","solaio",
    "acciaio-calcestruzzo","acciaio legno","acciaio-legno","legno-calcestruzzo"
}

# Escludi riferimenti ad altri marchi/sistemi non Tecnaria
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
    q = (question or "").lower()
    out: List[Dict[str, Any]] = []
    # P560 (foto)
    if "p560" in q or "chiodatrice" in q or "pistola" in q:
        out.append({"label": "Foto P560", "href": "/static/img/p560_magazzino.jpg", "preview": True})
    # CTF: scheda o posa
    if "ctf" in q or ("connettor" in q and "lamiera" in q):
        out.append({"label":"Scheda tecnica CTF (PDF)","href":"/static/docs/ctf_scheda.pdf","preview":True})
        out.append({"label":"Istruzioni di posa CTF (PDF)","href":"/static/docs/ctf_posa.pdf","preview":True})
    # CTL: scheda
    if "ctl" in q or "acciaio-legno" in q or "acciaio legno" in q:
        out.append({"label":"Scheda tecnica CTL (PDF)","href":"/static/docs/ctl_scheda.pdf","preview":True})
    # CEM-E
    if "cem-e" in q or "ceme" in q:
        out.append({"label":"Scheda tecnica CEM-E (PDF)","href":"/static/docs/cem-e_scheda.pdf","preview":True})
    # Diapason
    if "diapason" in q:
        out.append({"label":"Scheda tecnica Diapason (PDF)","href":"/static/docs/diapason_scheda.pdf","preview":True})
    return out

# =============== Prompt =================
SYSTEM_PROMPT = (
    "Sei TecnariaBot, assistente ufficiale di Tecnaria S.p.A. (Bassano del Grappa).\n"
    "- Rispondi SOLO su prodotti/servizi Tecnaria: CTF, CTL, CEM-E, Diapason e l’attrezzo P560.\n"
    "- La P560 NON è un connettore: è una chiodatrice a polvere per fissare i connettori.\n"
    "- Non citare o consigliare prodotti/brand non Tecnaria. Se l’utente insiste su altri marchi, ricorda il perimetro.\n"
    "- Stile chiaro, tecnico quando serve. Niente valori inventati: se un dato non è disponibile, dillo esplicitamente.\n"
    "- Se l’utente chiede contatti/assistenza, rispondi con i dati di contatto aziendali se presenti nel contesto tool."
)

def build_contacts_block() -> str:
    return (f"{CONTACTS['ragione_sociale']} — {CONTACTS['indirizzo']} — "
            f"Tel {CONTACTS['telefono']} — {CONTACTS['email']} — {CONTACTS['sito']}")

def llm_answer_like_chatgpt(question: str, context: str) -> str:
    """
    Inoltra la domanda quasi "grezza" al modello per ottenere una risposta
    nello stile di ChatGPT, con temperatura 0.0 per massima stabilità.
    """
    if not client:
        # Fallback locale se manca la chiave OpenAI
        return ("Assistente dedicato ai prodotti Tecnaria (CTF, CTL, CEM-E, Diapason, P560). "
                "Configura OPENAI_API_KEY per risposte complete in stile ChatGPT.")

    user_content = f"Domanda: {question.strip()}\nContesto: {context.strip() if context else '—'}"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_content}
    ]
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
        temperature=0.0,   # massima coerenza/determinismo
        top_p=1.0,
        max_tokens=1200
    )
    return resp.choices[0].message.content.strip()

# =============== Routes =================
@app.route("/")
def index():
    # Usa templates/index.html già presente nel progetto
    return render_template("index.html")

@app.route("/api/answer", methods=["POST"])
def api_answer():
    payload = request.get_json(force=True) or {}
    question = (payload.get("question") or "").strip()
    context  = (payload.get("context") or "").strip()

    # 1) Scope check: se non è Tecnaria, rifiuto gentile
    if not is_in_scope(question):
        return jsonify({
            "answer": "Assistente dedicato esclusivamente ai prodotti e servizi Tecnaria S.p.A. (CTF, CTL, CEM-E, Diapason, P560).",
            "attachments": [],
            "meta": {"in_scope": False}
        })

    # 2) Se chiede contatti, rispondi subito con il blocco aziendale (senza passare al modello)
    qlow = question.lower()
    if any(k in qlow for k in CONTACTS_KEYWORDS):
        txt = build_contacts_block()
        return jsonify({
            "answer": txt,
            "attachments": [{"label":"Sito ufficiale Tecnaria","href":CONTACTS["sito"],"preview":False}],
            "meta": {"in_scope": True}
        })

    # 3) Risposta in stile ChatGPT (inoltro domanda+contesto)
    answer_text = llm_answer_like_chatgpt(question, context)

    # 4) Allegati/Note tecniche automatiche
    attachments = tool_attachments(question)

    return jsonify({
        "answer": answer_text,
        "attachments": attachments,
        "meta": {"in_scope": True}
    })

@app.route("/static/<path:path>")
def static_proxy(path):
    return send_from_directory("static", path)

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    # Debug locale
    app.run(host="0.0.0.0", port=8000, debug=True)
