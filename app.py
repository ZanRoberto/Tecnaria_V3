# app.py  —  TecnariaBot · ChatGPT puro (solo prodotti/servizi Tecnaria)
# Flask + OpenAI SDK v1.x
# NOTE: wizard/calcoli ETA sono lasciati in coda come blocco commentato.

import os
import re
import json
from pathlib import Path
from typing import List, Dict

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from openai import OpenAI

# ----------------------------
# Flask
# ----------------------------
app = Flask(__name__)
CORS(app)

# ----------------------------
# OpenAI client
# ----------------------------
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
OPENAI_TEMPERATURE = float(os.environ.get("OPENAI_TEMPERATURE", "0"))
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# ----------------------------
# Ambito Tecnaria: parole chiave
# ----------------------------
PRODUCT_KEYWORDS = {
    "ctf": ["ctf", "connettore ctf", "connettori ctf"],
    "ctl": ["ctl", "connettore ctl", "connettori ctl"],
    "cem-e": ["cem", "cem-e", "cem e", "connettore cem", "connettori cem"],
    "diapason": ["diapason"],
    "p560": ["p560", "spit p560", "chiodatrice p560", "pistola p560"],
}

# parole che NON vogliamo (marche/linee non Tecnaria)
DENYLIST = [
    "hbv", "hi-bond", "hibond", "x-hbv", "xhbv", "fva", "hilti", "peikko", "lindapter",
]

# ----------------------------
# Prompt di sistema (vincoli)
# ----------------------------
SYSTEM_PROMPT = """
Sei un assistente tecnico. Rispondi come ChatGPT (stile chiaro, ordinato), con tre livelli possibili:
- A = breve (2-3 frasi, senza numeri)
- B = standard (discorsiva commerciale/progettuale, senza formule)
- C = dettagliata (tecnica/ingegneri, parametri, riferimenti, procedura)

REGOLE FERREE (importanti):
1) Limita SEMPRE le risposte a prodotti e servizi di Tecnaria S.p.A. (Bassano del Grappa).
   Non citare prodotti/marchi di altre aziende; se la domanda NON riguarda Tecnaria, di': 
   "Questo assistente tratta solo prodotti e servizi Tecnaria S.p.A. di Bassano del Grappa."
2) P560 = chiodatrice/sparachiodi (strumento). Non chiamarla mai "connettore".
3) Se l’utente chiede contatti/sede/assistenza, rispondi con un testo chiaro e professionale.
4) Non inventare codici/ETA se non richiesti: mantieni accuratezza.
5) Tono tecnico, educato, senza marchette.

Se l’utente NON specifica A/B/C, rispondi in modalità B (standard).
"""

# ----------------------------
# Helper: formato modalità A/B/C
# ----------------------------
def extract_mode(user_text: str) -> str:
    t = user_text.lower()
    if re.search(r"\b(a\s*breve|modalita.?a\b|^a\b)", t):
        return "A"
    if re.search(r"\b(b\s*standard|modalita.?b\b|^b\b)", t):
        return "B"
    if re.search(r"\b(c\s*dettagliata|modalita.?c\b|^c\b)", t):
        return "C"
    return "B"

def inject_mode_instructions(mode: str) -> str:
    if mode == "A":
        return "Modalità A (breve): rispondi in 2-3 frasi, senza numeri."
    if mode == "C":
        return ("Modalità C (dettagliata): struttura in sezioni (Cos’è, Componenti/varianti, Prestazioni, "
                "Uso/posa, Norme/riferimenti, Vantaggi/limiti); includi parametri, criteri e una sintesi conclusiva unica.")
    return "Modalità B (standard): rispondi discorsivo, chiaro, senza formule."

# ----------------------------
# Helper: scope Tecnaria only
# ----------------------------
def is_in_scope(question: str) -> bool:
    q = question.lower()
    # accetta se contiene tecnaria o prodotti tecnaria o p560, ctf, ctl, ecc.
    if "tecnaria" in q:
        return True
    for fam, kws in PRODUCT_KEYWORDS.items():
        if any(k in q for k in kws):
            return True
    # se chiede genericamente "contatti", "assistenza", ecc. lo consideriamo in scope
    if any(k in q for k in ["contatti", "supporto", "assistenza", "sede", "telefono", "email", "pec"]):
        return True
    return False

def contains_denylist(question: str) -> bool:
    q = question.lower()
    return any(bad in q for bad in DENYLIST)

# ----------------------------
# Allegati / Note tecniche
# ----------------------------
STATIC_DIRS = [
    Path("static/docs"),
    Path("static/img"),
    Path("static/video"),
]

def discover_attachments(question: str) -> List[Dict]:
    """
    Cerca file collegati al prodotto menzionato nella domanda.
    Regole semplici: se nomini 'p560' → prova a trovare file con 'p560' nel nome, ecc.
    """
    q = question.lower()
    keys = []
    for fam, kws in PRODUCT_KEYWORDS.items():
        if any(k in q for k in kws):
            keys.append(fam)
    # fallback: prova parole generiche
    if not keys:
        if "connettore" in q:
            keys.append("ctf")
        if "chiodatrice" in q or "pistola" in q:
            keys.append("p560")

    found: List[Dict] = []
    for base in STATIC_DIRS:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            name = p.name.lower()
            if any(k in name for k in keys):
                ext = p.suffix.lower()
                if ext in [".pdf", ".doc", ".docx", ".txt"]:
                    kind = "document"
                elif ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]:
                    kind = "image"
                elif ext in [".mp4", ".mov", ".avi", ".mkv"]:
                    kind = "video"
                else:
                    kind = "file"
                found.append({
                    "title": p.name,
                    "url": f"/{p.as_posix()}",
                    "type": kind,
                })
    # de-dup per titolo
    seen = set()
    deduped = []
    for a in found:
        if a["title"] in seen:
            continue
        seen.add(a["title"])
        deduped.append(a)
    return deduped[:8]  # massimo 8 allegati

# ----------------------------
# Home
# ----------------------------
@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

# ----------------------------
# API: answer (ChatGPT puro + filtro Tecnaria)
# ----------------------------
@app.route("/api/answer", methods=["POST"])
def api_answer():
    payload = request.get_json(force=True) or {}
    question = (payload.get("question") or "").strip()
    mode = extract_mode(question)
    mode_hint = inject_mode_instructions(mode)

    # Guard-rail dominio
    if contains_denylist(question):
        return jsonify({
            "answer": "Questo assistente tratta solo prodotti e servizi Tecnaria S.p.A. di Bassano del Grappa.",
            "attachments": []
        })

    if not is_in_scope(question):
        return jsonify({
            "answer": "Questo assistente è dedicato esclusivamente a prodotti e servizi Tecnaria S.p.A. di Bassano del Grappa.",
            "attachments": []
        })

    # Hard guard P560: se la domanda contiene P560, inserisco una riga tecnica nel system per evitare “connettore”.
    p560_guard = ""
    if any(k in question.lower() for k in PRODUCT_KEYWORDS["p560"]):
        p560_guard = "Ricorda: P560 è una chiodatrice/sparachiodi (strumento), NON un connettore."

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n" + mode_hint + ("\n" + p560_guard if p560_guard else "")},
        {"role": "user", "content": question}
    ]

    try:
        completion = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=OPENAI_TEMPERATURE,
            messages=messages,
            max_tokens=1200,
        )
        answer_text = completion.choices[0].message.content
    except Exception as e:
        answer_text = f"Errore nel modello: {e}"

    # Allegati
    attachments = discover_attachments(question)

    # Extra: append elenco allegati in coda alla risposta (in modo elegante)
    if attachments:
        lines = ["\n\nAllegati / note collegate:"]
        for a in attachments:
            icon = "📄" if a["type"] == "document" else "🖼️" if a["type"] == "image" else "🎞️" if a["type"] == "video" else "📎"
            lines.append(f"- {icon} {a['title']}: {a['url']}")
        answer_text += "\n" + "\n".join(lines)

    return jsonify({
        "answer": answer_text,
        "attachments": attachments
    })

# ----------------------------
# Healthcheck
# ----------------------------
@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "model": OPENAI_MODEL})

# ----------------------------
# Avvio locale
# ----------------------------
if __name__ == "__main__":
    # Per test locale: flask run oppure python app.py
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)


# ======================================================================
# BLOCCO DISATTIVATO (wizard e calcoli) — lasciato qui per futura riattivazione
# ======================================================================
"""
# ESEMPIO: parse_mini_wizard(context_text: str) -> dict
# ESEMPIO: selezione CTF via PRd/ETA + k_t, k_l, copriferro, ecc.
# (RIMOSSO DALLA LOGICA ATTUALE SU RICHIESTA: si risponde come ChatGPT puro,
#  mantenendo però gli allegati auto-collegati ai prodotti Tecnaria.)
"""
