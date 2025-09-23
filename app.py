# app.py — TecnariaBot · ChatGPT puro (solo prodotti/servizi Tecnaria)
# Flask + OpenAI SDK v1.x
# NOTE: nessun wizard/calcoli; hard-rule per "codici CTF"; allegati auto.

import os
import re
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
# OpenAI client/config
# ----------------------------
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
OPENAI_TEMPERATURE = float(os.environ.get("OPENAI_TEMPERATURE", "0"))
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
client = OpenAI(api_key=OPENAI_API_KEY)

# ----------------------------
# Ambito Tecnaria: parole chiave
# ----------------------------
PRODUCT_KEYWORDS = {
    "ctf": ["ctf", "connettore ctf", "connettori ctf"],
    "ctl": ["ctl", "connettore ctl", "connettori ctl"],
    "cem-e": ["cem", "cem-e", "cem e", "connettore cem", "connettori cem"],
    "diapason": ["diapason", "sistema diapason"],
    "p560": ["p560", "spit p560", "chiodatrice p560", "pistola p560"],
}

# linee/marchi non Tecnaria da escludere
DENYLIST = [
    "hbv", "hi-bond", "hibond", "x-hbv", "xhbv", "fva",
    "hilti", "peikko", "lindapter", "sika", "rothoblaas",
]

# codici CTF ufficiali Tecnaria (ETA-18/0447)
CTF_CODES = [
    "CTF020", "CTF025", "CTF030", "CTF040",
    "CTF060", "CTF070", "CTF080", "CTF090",
    "CTF105", "CTF125", "CTF135",
]

# ----------------------------
# Prompt di sistema (vincoli)
# ----------------------------
SYSTEM_PROMPT = """
Sei un assistente tecnico. Rispondi come ChatGPT (stile chiaro, ordinato), con tre livelli possibili:
- A = breve (2-3 frasi, senza numeri)
- B = standard (discorsiva commerciale/progettuale, senza formule)
- C = dettagliata (tecnica/ingegneri: sezioni Cos’è/Componenti-varianti/Prestazioni/Uso-posa/Norme-Vantaggi-limiti; includi criteri e chiudi con una sintesi).

REGOLE FERREE:
1) Limita SEMPRE le risposte a prodotti e servizi di Tecnaria S.p.A. (Bassano del Grappa).
   Se la domanda non riguarda Tecnaria, rispondi: "Questo assistente tratta solo prodotti e servizi Tecnaria S.p.A. di Bassano del Grappa."
2) P560 = chiodatrice/sparachiodi (strumento), MAI "connettore".
3) Non citare altri marchi/linee non Tecnaria.
4) Accuratezza prima di tutto; non inventare codici, tabelle o normative.
5) Se l’utente non specifica A/B/C, usa modalità B (standard).
"""

# ----------------------------
# Helper: modalità A/B/C
# ----------------------------
def extract_mode_mark(question: str) -> str:
    q = question.lower()
    if re.search(r"\bmodalita.?a\b|^a\b| a breve\b", q):
        return "A"
    if re.search(r"\bmodalita.?c\b|^c\b| c dettagliata\b", q):
        return "C"
    if re.search(r"\bmodalita.?b\b|^b\b| b standard\b", q):
        return "B"
    return "B"

def mode_instruction(mode: str) -> str:
    if mode == "A":
        return "Modalità A (breve): rispondi in 2-3 frasi, senza numeri."
    if mode == "C":
        return ("Modalità C (dettagliata): sezioni Cos’è, Componenti/varianti, Prestazioni, Uso/posa, "
                "Norme/riferimenti, Vantaggi/limiti; includi criteri e una sintesi finale unica.")
    return "Modalità B (standard): rispondi discorsivo, chiaro, senza formule."

# ----------------------------
# Helper: scope Tecnaria only
# ----------------------------
def is_in_scope(question: str) -> bool:
    q = question.lower()
    if "tecnaria" in q:
        return True
    for fam, kws in PRODUCT_KEYWORDS.items():
        if any(k in q for k in kws):
            return True
    if any(k in q for k in ["contatti", "supporto", "assistenza", "sede", "telefono", "email", "pec"]):
        return True
    return False

def contains_denylist(question: str) -> bool:
    q = question.lower()
    return any(bad in q for bad in DENYLIST)

# ----------------------------
# Allegati / Note tecniche
# ----------------------------
STATIC_DIRS = [Path("static/docs"), Path("static/img"), Path("static/video")]

def discover_attachments(question: str) -> List[Dict]:
    q = question.lower()
    keys = []
    for fam, kws in PRODUCT_KEYWORDS.items():
        if any(k in q for k in kws):
            keys.append(fam)
    if not keys:
        if "connettore" in q:
            keys.append("ctf")
        if "chiodatrice" in q or "pistola" in q or "p560" in q:
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
    # dedup
    unique = []
    seen = set()
    for a in found:
        if a["title"] in seen:
            continue
        seen.add(a["title"])
        unique.append(a)
    return unique[:8]

# ----------------------------
# Regola hard: domanda "codici CTF"
# ----------------------------
CODICI_CTF_PATTERNS = [
    r"\bcodici\b.*\bctf\b",
    r"\bcodice\b.*\bctf\b",
    r"\belenco\b.*\bctf\b",
    r"\blista\b.*\bctf\b",
    r"\bquali\b.*\bctf\b.*\bcodic",
    r"\b(tutti|tutte)\b.*\bctf\b.*\bcodic",
]

def asks_ctf_codes(question: str) -> bool:
    q = question.lower()
    return any(re.search(p, q) for p in CODICI_CTF_PATTERNS)

def make_ctf_codes_answer(mode: str) -> str:
    base = ("I connettori **CTF** Tecnaria sono identificati dall’altezza del gambo (stud). "
            "I **codici ufficiali** sono:\n\n"
            "CTF020, CTF025, CTF030, CTF040, CTF060, CTF070, CTF080, CTF090, CTF105, CTF125, CTF135.\n")
    if mode == "A":
        return ("I codici CTF Tecnaria sono quelli ufficiali: "
                "CTF020, CTF025, CTF030, CTF040, CTF060, CTF070, CTF080, CTF090, CTF105, CTF125, CTF135.")
    if mode == "C":
        return (base + "\n**Note tecniche (modalità C):**\n"
                "- Il codice riflette l’**altezza** (es. CTF080 ≈ 80 mm).\n"
                "- La scelta dipende da soletta, lamiera/posa e verifiche da ETA/EC4.\n"
                "- Posa con **P560** (chiodatrice), controlli di cantiere secondo manuale Tecnaria.\n")
    return base

# ----------------------------
# Routes
# ----------------------------
@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/api/answer", methods=["POST"])
def api_answer():
    data = request.get_json(force=True) or {}
    question = (data.get("question") or "").strip()

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

    # Modalità A/B/C
    mode = extract_mode_mark(question)
    mode_hint = mode_instruction(mode)

    # Hard rule: domanda codici CTF → risposta deterministica
    if asks_ctf_codes(question):
        answer_text = make_ctf_codes_answer(mode)
        # allegati (opzionali) relativi a CTF
        attachments = discover_attachments("ctf")
        if attachments:
            lines = ["\nAllegati / note collegate:"]
            for a in attachments:
                icon = "📄" if a["type"] == "document" else "🖼️" if a["type"] == "image" else "🎞️" if a["type"] == "video" else "📎"
                lines.append(f"- {icon} {a['title']}: {a['url']}")
            answer_text += "\n" + "\n".join(lines)
        return jsonify({"answer": answer_text, "attachments": attachments})

    # P560 guard add-on
    p560_guard = ""
    if any(k in question.lower() for k in PRODUCT_KEYWORDS["p560"]):
        p560_guard = "Ricorda: P560 è una chiodatrice/sparachiodi (strumento), NON un connettore."

    # ChatGPT puro, ma Tecnaria-only
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n" + mode_hint + ("\n" + p560_guard if p560_guard else "")},
        {"role": "user", "content": question},
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
    if attachments:
        lines = ["\n\nAllegati / note collegate:"]
        for a in attachments:
            icon = "📄" if a["type"] == "document" else "🖼️" if a["type"] == "image" else "🎞️" if a["type"] == "video" else "📎"
            lines.append(f"- {icon} {a['title']}: {a['url']}")
        answer_text += "\n" + "\n".join(lines)

    return jsonify({"answer": answer_text, "attachments": attachments})

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "model": OPENAI_MODEL})

# ----------------------------
# Avvio locale
# ----------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
