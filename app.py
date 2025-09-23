# app.py — TecnariaBot (modalità ChatGPT “puro Tecnaria”)
# Flask app con risposte stile ChatGPT, solo dominio Tecnaria, con alcuni casi deterministici.
# Wizard & calcoli lasciati in REM per poterli riattivare in futuro.

import os
import re
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS

# --- OpenAI SDK v1 ---
from openai import OpenAI
client = OpenAI()

# -----------------------------------
# Flask
# -----------------------------------
app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# -----------------------------------
# Config da ENV
# -----------------------------------
OPENAI_MODEL         = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_MODEL_FALLBACK= os.getenv("OPENAI_MODEL_FALLBACK", "gpt-4o-mini")
OPENAI_TEMPERATURE   = float(os.getenv("OPENAI_TEMPERATURE", "0"))
MAX_ANSWER_CHARS     = int(os.getenv("MAX_ANSWER_CHARS", "1500"))

# -----------------------------------
# Allegati/Note: mappa parole chiave → file statici
# (Aggiungi liberamente qui nuove voci: immagini, PDF, ecc.)
# -----------------------------------
ATTACHMENTS_MAP = {
    # Attrezzature
    "p560": [
        {"label": "Foto P560", "href": "/static/img/p560_magazzino.jpg", "type": "image"},
        # Esempio PDF se lo aggiungi: {"label":"Manuale P560 (PDF)", "href":"/static/docs/p560_manual.pdf","type":"pdf"}
    ],
    # Connettori CTF
    "ctf": [
        {"label": "Istruzioni di posa CTF (PDF)", "href": "/static/docs/istruzioni_posa_ctf.pdf", "type": "pdf"},
    ],
    # Connettori CTL / MAXI
    "ctl": [
        {"label": "Scheda CTL/CTL MAXI (PDF)", "href": "/static/docs/scheda_ctl_maxi.pdf", "type": "pdf"},
    ],
    # CEM / VCEM (laterocemento)
    "cem": [
        {"label": "Istruzioni CEM/VCEM (PDF)", "href": "/static/docs/istruzioni_cem_vcem.pdf", "type":"pdf"},
    ],
    "diapason": [
        {"label": "Scheda DIAPASON (PDF)", "href": "/static/docs/scheda_diapason.pdf", "type":"pdf"},
    ],
}

def get_attachments_for(text: str):
    """Ritorna lista di allegati in base a keyword trovate nel testo domanda/risposta."""
    hits = []
    t = text.lower()
    for key, files in ATTACHMENTS_MAP.items():
        if key in t:
            hits.extend(files)
    # De-duplicate by href
    seen = set()
    filtered = []
    for f in hits:
        if f["href"] not in seen:
            filtered.append(f)
            seen.add(f["href"])
    return filtered

# -----------------------------------
# System prompt (stile ChatGPT, dominio Tecnaria ONLY)
# -----------------------------------
def build_system_prompt():
    return (
        "Sei un assistente tecnico di Tecnaria S.p.A. (Bassano del Grappa). "
        "COMPITI: rispondi come ChatGPT, ma SOLO su prodotti/servizi Tecnaria: "
        "CTF (acciaio–calcestruzzo), CTL/CTL MAXI (legno–calcestruzzo), CEM/VCEM (laterocemento), DIAPASON (rinforzi solai legno), "
        "attrezzature P560 (chiodatrice) e accessori correlati. "
        "ESCLUSIONI: non parlare di prodotti non Tecnaria, non citare concorrenti, non divagare. "
        "CLASSIFICAZIONE: se la domanda riguarda legno/tavolato/assito → CTL/CTL MAXI; "
        "acciaio-lamiera grecata/soletta piena → CTF; "
        "laterocemento → CEM/VCEM; "
        "rinforzo solai legno → DIAPASON; "
        "P560 è SEMPRE una chiodatrice a polvere, mai un connettore. "
        "STILE: chiaro, tecnico ma leggibile; struttura in punti dove utile; nessun markup HTML all'inizio della risposta; "
        "se l’utente chiede contatti, fornisci recapiti ufficiali. "
        "Se l’utente chiede cose non Tecnaria, rifiuta gentilmente e reindirizza al perimetro Tecnaria. "
    )

# -----------------------------------
# Regole deterministiche per casi critici
# -----------------------------------
def deterministic_answer(user_q: str) -> dict | None:
    """
    Riconosce pattern e restituisce una risposta pronta (uguale allo stile che vuoi),
    altrimenti None per andare al modello.
    """
    q = re.sub(r"\s+", " ", user_q.strip().lower())

    # 1) Domanda: “posso usare una chiodatrice qualsiasi per CTF?”
    if any(k in q for k in ["normale chiodatrice", "chiodatrice qualsiasi", "qualsiasi chiodatrice"]) and "ctf" in q:
        text = (
            "Sì, ma NON con “una chiodatrice qualsiasi”. Per i connettori **CTF** si utilizza "
            "esclusivamente la **SPIT P560** con kit/adattatori dedicati Tecnaria. Altre macchine non sono ammesse. "
            "Ogni connettore si fissa con **2 chiodi** (HSBR14) e propulsori idonei.\n\n"
            "Indicazioni essenziali:\n"
            "• usare solo **SPIT P560** (nolo/vendita disponibili); non serve patentino specifico; seguire le istruzioni in valigetta.\n"
            "• acciaio trave ≥ 6 mm; con lamiera grecata è ammesso 1×1,5 mm oppure 2×1,0 mm ben aderenti alla trave.\n"
            "• posa sopra la trave (anche con lamiera presente) con due chiodi per connettore.\n\n"
            "Per taratura potenza, verifiche e sicurezza: vedi **Istruzioni di posa CTF**."
        )
        return {"answer": text, "attachments": get_attachments_for("ctf p560")}

    # 2) Domanda: CTL MAXI su travi in legno + tavolato 2 cm + soletta 5 cm
    if ("maxi" in q or "ctl maxi" in q) and ("legno" in q or "travi in legno" in q) and ("tavolato" in q and ("2 cm" in q or "2cm" in q)) and ("soletta" in q and ("5 cm" in q or "5cm" in q)):
        text = (
            "Usa **CTL MAXI 12/040** (altezza gambo 40 mm), fissato **sopra il tavolato** con **2 viti Ø10**:\n"
            "• di norma **Ø10×100 mm**; se l’interposto/tavolato supera 25–30 mm, passa a **Ø10×120 mm**.\n\n"
            "Motivi:\n"
            "• Il MAXI è pensato proprio per posa su assito; con soletta 5 cm il 40 mm resta annegato correttamente e la testa supera la rete **a metà spessore**, come richiesto.\n"
            "• Altezze/viti disponibili: Ø10 × 100/120/140 mm della linea **CTL MAXI**.\n\n"
            "Note rapide:\n"
            "• Soletta **min 5 cm** (C25/30 o leggero strutturale), rete a metà spessore.\n"
            "• Se interferisce con staffe/armatura superiori puoi valutare **12/030**; in generale scegli l’altezza in modo che la testa sia sopra la rete ma sotto il filo superiore del getto."
        )
        return {"answer": text, "attachments": get_attachments_for("ctl maxi")}

    # 3) Domanda: CTCEM usa resine?
    if ("ctcem" in q or "vcem" in q or "cem" in q) and ("resine" in q or "resina" in q):
        text = (
            "No: **CTCEM/VCEM non usano resine**. Il fissaggio è completamente **meccanico** (“a secco”):\n"
            "1) incisione per alloggiare la piastra dentata;\n"
            "2) **preforo Ø11 mm** prof. ~75 mm;\n"
            "3) pulizia della polvere;\n"
            "4) avvitatura del piolo con avvitatore (percussione/frizione) fino a battuta.\n\n"
            "Questi connettori sono progettati proprio come alternativa alle soluzioni con barre piegate + resina tipiche dei solai in laterocemento."
        )
        return {"answer": text, "attachments": get_attachments_for("cem")}

    # 4) Domande contatti
    if any(k in q for k in ["contatti", "telefono", "email", "sede", "indirizzo", "orari", "come contattarvi"]):
        text = (
            "**Contatti Tecnaria S.p.A.**\n"
            "• Sede: Via G. Ferraris 32, 36061 Bassano del Grappa (VI)\n"
            "• Tel: +39 0424 330913\n"
            "• Email: info@tecnaria.com\n"
            "• Sito: www.tecnaria.com\n"
            "Assistenza tecnica: supporto su CTF / CTL / CEM-VCEM / DIAPASON e P560."
        )
        return {"answer": text, "attachments": []}

    # 5) P560: sempre chiodatrice (se chiede “cos’è P560” o simili)
    if "p560" in q:
        text = (
            "La **P560** è una **chiodatrice a polvere** (SPIT P560) utilizzata con kit Tecnaria per la posa dei connettori, "
            "soprattutto **CTF** su travi d’acciaio/lamiera grecata. Non è un connettore. "
            "Uso: corretta taratura propulsori, due chiodi per connettore, DPI e controlli di cantiere secondo manuale Tecnaria."
        )
        return {"answer": text, "attachments": get_attachments_for("p560")}

    return None  # Nessuna regola: vai al modello


# -----------------------------------
# LLM call (fallback stile ChatGPT)
# -----------------------------------
def llm_answer(user_q: str) -> str:
    system_prompt = build_system_prompt()

    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=OPENAI_TEMPERATURE,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_q},
            ],
            max_tokens=1000
        )
        text = resp.choices[0].message.content.strip()
        if len(text) > MAX_ANSWER_CHARS:
            text = text[:MAX_ANSWER_CHARS] + "…"
        return text
    except Exception as e:
        # Fallback su modello alternativo se disponibile
        try:
            resp = client.chat.completions.create(
                model=OPENAI_MODEL_FALLBACK,
                temperature=OPENAI_TEMPERATURE,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_q},
                ],
                max_tokens=1000
            )
            text = resp.choices[0].message.content.strip()
            if len(text) > MAX_ANSWER_CHARS:
                text = text[:MAX_ANSWER_CHARS] + "…"
            return text
        except Exception as e2:
            return "Servizio momentaneamente non disponibile. Riprova tra poco."

# -----------------------------------
# ROUTES
# -----------------------------------
@app.route("/", methods=["GET"])
def index():
    # L’HTML sta in templates/index.html (niente HTML qui dentro!)
    return render_template("index.html")

@app.route("/api/answer", methods=["POST"])
def api_answer():
    data = request.get_json(silent=True) or {}
    user_q = (data.get("question") or "").strip()

    # Filtro “Tecnaria only”: se la domanda è totalmente fuori tema, rispondi educatamente.
    scope_keywords = ["tecnaria", "ctf", "ctl", "cem", "vcem", "diapason", "p560", "chiodatrice", "solaio", "lamiera", "trave", "soletta", "connettore"]
    if not any(k in user_q.lower() for k in scope_keywords):
        return jsonify({
            "answer": "Assistente dedicato ai prodotti e servizi **Tecnaria**. Indica il prodotto/tema Tecnaria (CTF/CTL/CEM/DIAPASON/P560) e ti rispondo subito.",
            "attachments": []
        })

    # 1) prova regole deterministiche (per replicare esattamente lo stile richiesto)
    det = deterministic_answer(user_q)
    if det:
        # Allegati: se non ci sono, prova comunque a inferirli dal testo risposta
        atts = det.get("attachments") or get_attachments_for(det["answer"])
        return jsonify({"answer": det["answer"], "attachments": atts})

    # 2) altrimenti chiama il modello (stile ChatGPT)
    text = llm_answer(user_q)
    atts = get_attachments_for(user_q + " " + text)
    return jsonify({"answer": text, "attachments": atts})

# ---- STATIC (per sicurezza: servire docs se non gestiti dal server web a monte) ----
@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)

# -----------------------------------
# (REMMATO) — Vecchio Wizard/Calcoli — NON usato ora, tenuto per futura riattivazione
# -----------------------------------
"""
# ESEMPIO: parse_input, calcoli, ecc. (non attivi)
def parse_ctf_inputs(ctx: str) -> dict: ...
def compute_ctf_height(params: dict) -> dict: ...
"""

# -----------------------------------
# MAIN (debug locale)
# -----------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False)
