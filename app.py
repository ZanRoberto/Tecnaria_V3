# app.py — TecnariaBot (ChatGPT puro • Tecnaria-only)
import os
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from openai import OpenAI

# ----------------------------
# App / Config
# ----------------------------
app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata nell'ambiente.")

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_MODEL_FALLBACK = os.getenv("OPENAI_MODEL_FALLBACK", "gpt-4o-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0"))

client = OpenAI(api_key=OPENAI_API_KEY)

# ----------------------------
# Regole “Tecnaria only”
# ----------------------------
# Macro-argomenti ammessi (filtra domande fuori contesto)
TECNARIA_KEYWORDS = [
    "tecnaria", "ctf", "ctl", "cem", "cem-e", "diapason", "p560",
    "solaio collaborante", "solai collaboranti", "lamiera grecata",
    "acciaio-calcestruzzo", "acciaio legno", "acciaio-legno",
    "connettori tecnaria", "laterocemento", "posa tecnaria"
]

# Marchi / prodotti NON Tecnaria (se presenti → reindirizzo)
DENYLIST = [
    "hbv", "hi-bond", "hibond", "x-hbv", "xhbv",
    "fva", "arcelormittal",
    "hilti", "fischer", "stud welded", "perno a piolo"
]

# Codici CTF ufficiali (risposta deterministica)
CTF_CODES = [
    "CTF_020","CTF_025","CTF_030","CTF_040",
    "CTF_060","CTF_070","CTF_080","CTF_090",
    "CTF_105","CTF_125","CTF_135"
]

# Blocco contatti (deterministico)
TECNARIA_CONTACTS = (
    "Tecnaria S.p.A.\n"
    "Via delle Industrie, 23 — 36061 Bassano del Grappa (VI)\n"
    "Tel: +39 0424 567 120 • Email: info@tecnaria.com\n"
    "Sito: www.tecnaria.com"
)

# Allegati/Note tecniche disponibili (titolo -> (url, tipo))
ATTACHMENT_MAP = {
    # P560
    "Foto P560": ("/static/img/p560_magazzino.jpg", "image"),
    "Manuale P560 (PDF)": ("/static/docs/p560_manual.pdf", "document"),
    # CTF
    "Scheda CTF (PDF)": ("/static/docs/ctf_scheda.pdf", "document"),
    # Esempio: aggiungi qui altri PDF/foto/video
    # "Istruzioni posa CTF (PDF)": ("/static/docs/istruzioni_posa_ctf.pdf", "document"),
}

def collect_attachments(question_lower: str):
    """Raccoglie allegati coerenti con la domanda."""
    out = []
    if "p560" in question_lower:
        for k in ["Foto P560", "Manuale P560 (PDF)"]:
            if k in ATTACHMENT_MAP:
                url, typ = ATTACHMENT_MAP[k]
                out.append({"title": k, "url": url, "type": typ})
    if "ctf" in question_lower or "connettori" in question_lower:
        if "Scheda CTF (PDF)" in ATTACHMENT_MAP:
            url, typ = ATTACHMENT_MAP["Scheda CTF (PDF)"]
            out.append({"title": "Scheda CTF (PDF)", "url": url, "type": typ})
    return out

# ----------------------------
# SYSTEM PROMPT (stile ChatGPT, ma vincolato a Tecnaria)
# ----------------------------
SYSTEM_PROMPT = """
Sei “TecnariaBot”, assistente tecnico ufficiale di Tecnaria S.p.A.
OBIETTIVO: rispondi nello stile naturale e completo di ChatGPT, ma SOLO su prodotti/servizi Tecnaria.

Regole:
- Ambito consentito: CTF/CTL/CEM/VCEM/CEM-E, Diapason, posa/istruzioni, solai collaboranti (acciaio-calcestruzzo, legno-calcestruzzo, laterocemento) e attrezzi come P560.
- La P560 è una CHIODATRICE a polvere per posa connettori. NON è un connettore.
- NON parlare di marchi/prodotti non Tecnaria. Se compaiono, reindirizza con tatto ai prodotti equivalenti Tecnaria.
- NON inventare codici/modelli inesistenti.
- Se chiedono “codici CTF”, restituisci esattamente: CTF_020, CTF_025, CTF_030, CTF_040, CTF_060, CTF_070, CTF_080, CTF_090, CTF_105, CTF_125, CTF_135.
- Se chiedono “contatti Tecnaria”, restituisci il blocco contatti fisso.
- Tono: tecnico, chiaro, senza frasi vuote. Usa elenchi puntati dove utile.
- Questa versione NON fa calcoli: niente formule. Spiega, confronta, guida l’utente in modo pratico.

Stile:
- Breve = 2–3 frasi efficaci.
- Standard = spiegazione completa ma scorrevole (5–10 frasi).
- Dettagliata = guida molto strutturata (sezioni, punti elenco, consigli pratici), restando nell’ambito Tecnaria.
"""

# ----------------------------
# Heuristics per risposte “fisse”
# ----------------------------
def is_non_tecnaria(question_lower: str) -> bool:
    return any(bad in question_lower for bad in DENYLIST)

def looks_like_ctf_codes(question_lower: str) -> bool:
    keys = [
        "codici ctf", "tutti i codici ctf", "serie ctf",
        "codici dei connettori ctf", "elenco ctf", "lista ctf"
    ]
    return any(k in question_lower for k in keys)

def looks_like_contacts(question_lower: str) -> bool:
    keys = ["contatti", "telefono tecnaria", "email tecnaria", "sede tecnaria", "supporto", "assistenza"]
    return any(k in question_lower for k in keys)

def looks_like_p560(question_lower: str) -> bool:
    return "p560" in question_lower

def reply_ctf_codes():
    return (
        "Ecco i **codici ufficiali della serie CTF Tecnaria** (altezze nominali):\n"
        "- " + ", ".join(CTF_CODES) + ".\n"
        "La scelta dell’altezza dipende dalla configurazione del solaio e dai requisiti di progetto."
    )

def reply_contacts():
    return f"**Contatti Tecnaria**\n{TECNARIA_CONTACTS}"

def reply_p560():
    return (
        "**P560 — chiodatrice a polvere (non un connettore)**\n"
        "- Impiego: fissaggio connettori Tecnaria (es. CTF) su travi acciaio e lamiera grecata.\n"
        "- Uso: regolazione potenza, due chiodi per connettore, verifiche in cantiere (visiva + bending test).\n"
        "- Sicurezza: DPI e rispetto del manuale Tecnaria.\n"
        "Per i dettagli operativi vedi il **Manuale P560 (PDF)** negli allegati."
    )

# ----------------------------
# Routes
# ----------------------------
@app.route("/")
def index():
    # L’HTML sta in templates/index.html
    return render_template("index.html")

@app.route("/api/answer", methods=["POST"])
def api_answer():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    q_lower = question.lower()

    # Allegati coerenti col tema
    attachments = collect_attachments(q_lower)

    # Filtro “Tecnaria only”
    if not any(k in q_lower for k in TECNARIA_KEYWORDS) and not looks_like_contacts(q_lower):
        answer = (
            "Questo assistente risponde **solo** su prodotti e servizi Tecnaria "
            "(CTF/CTL/CEM-E/Diapason, posa, P560). Riformula la domanda in ambito Tecnaria."
        )
        return jsonify({"answer": answer, "attachments": attachments})

    if is_non_tecnaria(q_lower):
        answer = (
            "Resto focalizzato su **Tecnaria**. Se cerchi un equivalente nel catalogo Tecnaria, "
            "dimmi pure cosa vuoi ottenere e ti indirizzo al prodotto corretto."
        )
        return jsonify({"answer": answer, "attachments": attachments})

    # Risposte deterministiche
    if looks_like_ctf_codes(q_lower):
        return jsonify({"answer": reply_ctf_codes(), "attachments": attachments})

    if looks_like_contacts(q_lower):
        return jsonify({"answer": reply_contacts(), "attachments": attachments})

    if looks_like_p560(q_lower):
        # P560 è una CHIODATRICE
        return jsonify({"answer": reply_p560(), "attachments": attachments})

    # ChatGPT-like, ma vincolato a Tecnaria
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=OPENAI_TEMPERATURE,
            messages=messages,
        )
        answer = resp.choices[0].message.content.strip()
        if not answer:
            raise ValueError("Risposta vuota dal modello.")
    except Exception:
        # Fallback
        try:
            resp = client.chat.completions.create(
                model=OPENAI_MODEL_FALLBACK,
                temperature=OPENAI_TEMPERATURE,
                messages=messages,
            )
            answer = resp.choices[0].message.content.strip()
        except Exception as e2:
            answer = f"Errore nel modello: {e2}"

    return jsonify({"answer": answer, "attachments": attachments})


if __name__ == "__main__":
    # NIENTE HTML qui dentro; solo server Python.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False)
