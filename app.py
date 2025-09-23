# app.py — TecnariaBot (ChatGPT “puro” solo Tecnaria) + hardening avvio
import os, re, json, traceback
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS

# OpenAI SDK v1
from openai import OpenAI
client = OpenAI()

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# -------- Config --------
OPENAI_MODEL           = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_MODEL_FALLBACK  = os.getenv("OPENAI_MODEL_FALLBACK", "gpt-4o-mini")
OPENAI_TEMPERATURE     = float(os.getenv("OPENAI_TEMPERATURE", "0"))
MAX_ANSWER_CHARS       = int(os.getenv("MAX_ANSWER_CHARS", "1500"))

# -------- Allegati / Note --------
ATTACHMENTS_MAP = {
    "p560": [
        {"label": "Foto P560", "href": "/static/img/p560_magazzino.jpg", "type": "image"},
        # {"label": "Manuale P560 (PDF)", "href": "/static/docs/p560_manual.pdf", "type":"pdf"},
    ],
    "ctf": [
        {"label": "Istruzioni di posa CTF (PDF)", "href": "/static/docs/istruzioni_posa_ctf.pdf", "type":"pdf"},
    ],
    "ctl": [
        {"label": "Scheda CTL/CTL MAXI (PDF)", "href": "/static/docs/scheda_ctl_maxi.pdf", "type":"pdf"},
    ],
    "cem": [
        {"label": "Istruzioni CEM/VCEM (PDF)", "href": "/static/docs/istruzioni_cem_vcem.pdf", "type":"pdf"},
    ],
    "diapason": [
        {"label": "Scheda DIAPASON (PDF)", "href": "/static/docs/scheda_diapason.pdf", "type":"pdf"},
    ],
}

def get_attachments_for(text: str):
    t = (text or "").lower()
    hits = []
    for k, files in ATTACHMENTS_MAP.items():
        if k in t:
            hits += files
    # dedup
    used = set(); out = []
    for f in hits:
        if f["href"] in used: continue
        out.append(f); used.add(f["href"])
    return out

# -------- System prompt --------
def build_system_prompt():
    return (
        "Sei un assistente tecnico di Tecnaria S.p.A. (Bassano del Grappa). "
        "Rispondi come ChatGPT, ma SOLO su prodotti/servizi Tecnaria: "
        "CTF (acciaio–calcestruzzo), CTL/CTL MAXI (legno–calcestruzzo), CEM/VCEM (laterocemento), DIAPASON (rinforzi solai legno), "
        "attrezzature P560 (chiodatrice) e accessori correlati. "
        "Non citare concorrenti, non divagare. "
        "Classifica mentalmente: legno/assito→CTL/CTL MAXI; acciaio/lamiera o soletta piena→CTF; laterocemento→CEM/VCEM; ”
        "rinforzo solai legno→DIAPASON; P560 è SEMPRE chiodatrice a polvere (non un connettore). "
        "Stile chiaro e tecnico; nessun HTML in testa alla risposta. "
        "Se chiedono contatti, fornisci recapiti ufficiali Tecnaria."
    )

# -------- Regole deterministiche (replica risposte chiave) --------
def deterministic_answer(user_q: str):
    q = re.sub(r"\s+", " ", (user_q or "").strip().lower())

    # Chiodatrice “qualsiasi” per CTF → P560
    if ("chiodatrice" in q and "ctf" in q) and any(k in q for k in ["qualsiasi", "normale"]):
        a = (
            "Sì, ma NON con “una chiodatrice qualsiasi”. Per i connettori **CTF** si usa "
            "esclusivamente la **SPIT P560** con kit/adattatori Tecnaria. Altre macchine non sono ammesse. "
            "Ogni connettore si posa con **2 chiodi** (HSBR14) e propulsori idonei.\n\n"
            "Indicazioni essenziali:\n"
            "• usare solo **SPIT P560**; seguire istruzioni in valigetta.\n"
            "• acciaio trave ≥ 6 mm; con lamiera: 1×1,5 mm oppure 2×1,0 mm ben aderenti.\n"
            "• posa sopra la trave (anche con lamiera) con due chiodi per connettore.\n\n"
            "Per taratura, verifiche e sicurezza: vedi **Istruzioni di posa CTF**."
        )
        return {"answer": a, "attachments": get_attachments_for("ctf p560")}

    # CTL MAXI su legno + tavolato 2 cm + soletta 5 cm
    if ("maxi" in q or "ctl maxi" in q) and "legno" in q and "tavolato" in q and "2 cm" in q and "soletta" in q and "5 cm" in q:
        a = (
            "Usa **CTL MAXI 12/040** (altezza gambo 40 mm), fissato **sopra il tavolato** con **2 viti Ø10**:\n"
            "• di norma **Ø10×100 mm**; se interposto/tavolato > 25–30 mm passa a **Ø10×120 mm**.\n\n"
            "Motivi:\n"
            "• Il MAXI è pensato per posa su assito; con soletta 5 cm il 40 mm resta annegato e la testa supera la rete **a metà spessore**.\n"
            "• Altezze/viti disponibili: Ø10 × 100/120/140 mm della linea **CTL MAXI**.\n\n"
            "Note rapide:\n"
            "• Soletta **min 5 cm** (C25/30 o leggero), rete a metà spessore.\n"
            "• Se interferisce con staffe/armatura superiori valuta **12/030**."
        )
        return {"answer": a, "attachments": get_attachments_for("ctl maxi")}

    # CTCEM resine?
    if any(k in q for k in ["ctcem", "vcem", "cem"]) and "resin" in q:
        a = (
            "No: **CTCEM/VCEM non usano resine**. Fissaggio **meccanico** (“a secco”):\n"
            "1) incisione per alloggiare la piastra dentata;\n"
            "2) **preforo Ø11 mm** prof. ~75 mm;\n"
            "3) pulizia della polvere;\n"
            "4) avvitatura del piolo con avvitatore fino a battuta."
        )
        return {"answer": a, "attachments": get_attachments_for("cem")}

    # Contatti
    if any(k in q for k in ["contatti", "telefono", "email", "sede", "indirizzo", "orari"]):
        a = (
            "**Contatti Tecnaria S.p.A.**\n"
            "• Sede: Via G. Ferraris 32, 36061 Bassano del Grappa (VI)\n"
            "• Tel: +39 0424 330913\n"
            "• Email: info@tecnaria.com\n"
            "• Sito: www.tecnaria.com"
        )
        return {"answer": a, "attachments": []}

    # P560: sempre chiodatrice
    if "p560" in q:
        a = (
            "La **P560** è una **chiodatrice a polvere** (SPIT P560) per posa connettori Tecnaria, "
            "soprattutto **CTF** su travi d’acciaio/lamiera grecata. Non è un connettore."
        )
        return {"answer": a, "attachments": get_attachments_for("p560")}

    return None

# -------- LLM fallback --------
def llm_answer(user_q: str) -> str:
    sys = build_system_prompt()
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=OPENAI_TEMPERATURE,
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": user_q},
            ],
            max_tokens=1000
        )
        text = (resp.choices[0].message.content or "").strip()
        if len(text) > MAX_ANSWER_CHARS:
            text = text[:MAX_ANSWER_CHARS] + "…"
        return text
    except Exception as e:
        # log e fallback
        print("LLM primary error:", e, flush=True)
        try:
            resp = client.chat.completions.create(
                model=OPENAI_MODEL_FALLBACK,
                temperature=OPENAI_TEMPERATURE,
                messages=[
                    {"role": "system", "content": sys},
                    {"role": "user", "content": user_q},
                ],
                max_tokens=1000
            )
            text = (resp.choices[0].message.content or "").strip()
            if len(text) > MAX_ANSWER_CHARS:
                text = text[:MAX_ANSWER_CHARS] + "…"
            return text
        except Exception as e2:
            print("LLM fallback error:", e2, flush=True)
            return "Servizio momentaneamente non disponibile. Riprova tra poco."

# -------- Routes --------
@app.route("/healthz")
def healthz():
    return jsonify(ok=True)

@app.route("/", methods=["GET"])
def index():
    try:
        return render_template("index.html")
    except Exception as e:
        # se manca il template, non rompere l'avvio
        return f"Template non trovato: {e}", 500

@app.route("/api/answer", methods=["POST"])
def api_answer():
    try:
        data = request.get_json(silent=True) or {}
        user_q = (data.get("question") or "").strip()

        scope_keywords = ["tecnaria", "ctf", "ctl", "cem", "vcem", "diapason", "p560", "chiodatrice", "solaio", "lamiera", "trave", "soletta", "connettore"]
        if not any(k in user_q.lower() for k in scope_keywords):
            return jsonify({
                "answer": "Assistente dedicato ai prodotti/servizi **Tecnaria**. Indica il prodotto/tema Tecnaria (CTF/CTL/CEM/DIAPASON/P560).",
                "attachments": []
            })

        det = deterministic_answer(user_q)
        if det:
            atts = det.get("attachments") or get_attachments_for(det["answer"])
            return jsonify({"answer": det["answer"], "attachments": atts})

        text = llm_answer(user_q)
        atts = get_attachments_for(user_q + " " + text)
        return jsonify({"answer": text, "attachments": atts})
    except Exception as e:
        print("api_answer error:", traceback.format_exc(), flush=True)
        return jsonify({"answer": "Errore interno. Riprova tra poco.", "attachments": []}), 500

@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False)
