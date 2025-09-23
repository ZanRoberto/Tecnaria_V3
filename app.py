import os
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from openai import OpenAI

# ----------------------------
# Config base
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
# Allowlist macro-argomenti Tecnaria
TECNARIA_KEYWORDS = [
    "tecnaria", "ctf", "ctl", "cem", "cem-e", "diapason", "p560",
    "solaio collaborante", "solai collaboranti", "lamiera grecata",
    "acciaio-calcestruzzo", "acciaio legno", "acciaio-legno", "connettori tecnaria"
]

# Denylist marchi / prodotti non Tecnaria (si espande se necessario)
DENYLIST = [
    "hbv", "hi-bond", "hibond", "x-hbv", "xhbv",
    "fva", "arcelormittal", "metaldeck non tecnaria",
    "hilti", "fischer", "perno a piolo", "stud welded non tecnaria"
]

# Codici CTF ufficiali (per risposta deterministica alla domanda “codici CTF”)
CTF_CODES = ["CTF_020","CTF_025","CTF_030","CTF_040","CTF_060","CTF_070","CTF_080","CTF_090","CTF_105","CTF_125","CTF_135"]

# Contatti Tecnaria (blocco fisso deterministico)
TECNARIA_CONTACTS = (
    "Tecnaria S.p.A. – Via delle Industrie, 23, 36061 Bassano del Grappa (VI)\n"
    "Telefono: +39 0424 567 120 • Email: info@tecnaria.com\n"
    "Sito: www.tecnaria.com"
)

# Allegati/Note tecniche (mappa semplice: titolo -> (url, tipo))
# Metti i file nelle cartelle static/… come indicato
ATTACHMENT_MAP = {
    # P560
    "Foto P560": ("/static/img/p560_magazzino.jpg", "image"),
    # Esempi PDF
    "Scheda CTF (PDF)": ("/static/docs/ctf_scheda.pdf", "document"),
    "Manuale P560 (PDF)": ("/static/docs/p560_manual.pdf", "document"),
    # Esempio video
    # "Video posa CTF": ("/static/video/posa_ctf.mp4", "video"),
}

def collect_attachments(question_lower: str):
    """Restituisce allegati coerenti col tema (P560, CTF, ecc.)."""
    a = []
    if "p560" in question_lower:
        if "Foto P560" in ATTACHMENT_MAP:
            url, typ = ATTACHMENT_MAP["Foto P560"]
            a.append({"title": "Foto P560", "url": url, "type": typ})
        if "Manuale P560 (PDF)" in ATTACHMENT_MAP:
            url, typ = ATTACHMENT_MAP["Manuale P560 (PDF)"]
            a.append({"title": "Manuale P560 (PDF)", "url": url, "type": typ})

    if "ctf" in question_lower or "connettori" in question_lower:
        if "Scheda CTF (PDF)" in ATTACHMENT_MAP:
            url, typ = ATTACHMENT_MAP["Scheda CTF (PDF)"]
            a.append({"title": "Scheda CTF (PDF)", "url": url, "type": typ})
    return a


# ----------------------------
# Prompt di sistema (stile ChatGPT ma “Tecnaria only”)
# ----------------------------
SYSTEM_PROMPT = """Sei “TecnariaBot”, assistente tecnico aziendale.
Regole:
- Rispondi SOLO su prodotti/servizi Tecnaria S.p.A. (CTF/CTL/CEM-E/Diapason, posa, P560 chiodatrice, ecc.).
- Non parlare di marchi/prodotti non Tecnaria; se l’utente chiede altro, reindirizza gentilmente ai prodotti Tecnaria.
- Tieni uno stile naturale da ChatGPT. Se l’utente chiede “spiegami/proponi/confronta”, rispondi in modo completo.
- P560 è una CHIODATRICE a polvere, non è un connettore.
- Non inventare codici/modelli inesistenti.
- Se chiedono “codici CTF”, l’elenco atteso è: CTF_020, CTF_025, CTF_030, CTF_040, CTF_060, CTF_070, CTF_080, CTF_090, CTF_105, CTF_125, CTF_135.
- Se chiedono “contatti Tecnaria”, restituisci il blocco contatti aziendale in modo chiaro.
- Evita formule/calcoli: il modulo wizard è disattivato in questa versione.
Tono: tecnico ma chiaro; usa elenchi puntati dove utile; niente frasi generiche vuote.
"""

# ----------------------------
# Helpers per risposte deterministiche
# ----------------------------
def is_non_tecnaria(question_lower: str) -> bool:
    return any(bad in question_lower for bad in DENYLIST)

def looks_like_ctf_codes(question_lower: str) -> bool:
    keys = ["codici ctf", "tutti i codici ctf", "serie ctf", "codici dei connettori ctf"]
    return any(k in question_lower for k in keys)

def looks_like_contacts(question_lower: str) -> bool:
    keys = ["contatti", "telefono tecnaria", "email tecnaria", "sede tecnaria", "assistena", "supporto", "contatto"]
    return any(k in question_lower for k in keys)

def looks_like_p560(question_lower: str) -> bool:
    return "p560" in question_lower

def reply_ctf_codes():
    return (
        "Ecco i codici della **serie CTF Tecnaria** (altezze nominali):\n"
        "- " + ", ".join(CTF_CODES) + ".\n"
        "La scelta dell'altezza dipende dalla configurazione del solaio e dai requisiti di progetto."
    )

def reply_contacts():
    return f"**Contatti Tecnaria**\n{TECNARIA_CONTACTS}"

def reply_p560():
    return (
        "**P560 (chiodatrice a polvere)**\n"
        "Strumento per il fissaggio rapido e controllato dei connettori Tecnaria (es. CTF) su acciaio/lamiera.\n"
        "• Impiego: posa connettori su travi e lamiera grecata, con cartucce adeguate.\n"
        "• Sicurezza: DPI, regolazione potenza, controlli di cantiere (verifica visiva e bending test).\n"
        "• Riferimenti: manuale d’uso Tecnaria e linee guida di posa.\n"
        "_Nota: la P560 non è un connettore, ma l’attrezzo per installarli._"
    )

# ----------------------------
# Route UI
# ----------------------------
@app.route("/")
def index():
    return render_template("index.html")

# ----------------------------
# API principale
# ----------------------------
@app.route("/api/answer", methods=["POST"])
def api_answer():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    q_lower = question.lower()

    # Allegati coerenti (P560/CTF…)
    attachments = collect_attachments(q_lower)

    # Filtri/risposte deterministiche
    if not any(k in q_lower for k in TECNARIA_KEYWORDS) and not looks_like_contacts(q_lower):
        # Non Tecnaria → reindirizza
        answer = "Questo assistente risponde solo su prodotti e servizi **Tecnaria** (CTF/CTL/CEM-E/Diapason, posa, P560). Riformula la domanda in ambito Tecnaria."
        return jsonify({"answer": answer, "attachments": attachments})

    if is_non_tecnaria(q_lower):
        answer = "Resto focalizzato su **Tecnaria**: se ti interessa un equivalente nel catalogo Tecnaria, dimmelo e ti indirizzo al prodotto corretto."
        return jsonify({"answer": answer, "attachments": attachments})

    if looks_like_ctf_codes(q_lower):
        return jsonify({"answer": reply_ctf_codes(), "attachments": attachments})

    if looks_like_contacts(q_lower):
        return jsonify({"answer": reply_contacts(), "attachments": attachments})

    if looks_like_p560(q_lower):
        # P560 = chiodatrice, mai come connettore
        return jsonify({"answer": reply_p560(), "attachments": attachments})

    # Altrimenti: passa a LLM (ChatGPT-like), ma “Tecnaria only”
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
            # fallback se il modello non produce testo
            raise ValueError("Risposta vuota dal modello.")
    except Exception:
        # Fallback sul modello di riserva
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
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False)
