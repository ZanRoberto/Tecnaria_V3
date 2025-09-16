import os
import re
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)

# === Config ===
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5")
TEMPERATURE = float(os.environ.get("OPENAI_TEMPERATURE", "0"))
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Elenco prodotti/aree CONSENTITI (Tecnaria ufficiale)
ALLOWED_TOPICS = [
    "CTF", "CTL", "CTLB", "CTLU", "CEM-E", "MINI CEM-E", "V-CEM-E", "CTCEM",
    "Diapason", "Omega", "Manicotto GTS", "Spit P560", "chiodi", "cartucce",
    "manuali di posa", "capitolati", "computi metrici", "certificazioni", "ETA", "DoP", "CE",
    "posa in opera", "assistenza in cantiere", "solai collaboranti", "acciaio-calcestruzzo", "legno-calcestruzzo"
]

# Termini vietati / non Tecnaria (blocco duro)
BANNED = [
    r"\bHBV\b", r"\bFVA\b", r"\bAvantravetto\b", r"\bT[\- ]?Connect\b", r"\bAlfa\b"
]

# Messaggio di sistema con regole dure
SYSTEM_MSG = {
    "role": "system",
    "content": (
        "Sei un esperto dei prodotti Tecnaria S.p.A. di Bassano del Grappa. "
        "Rispondi SOLTANTO su prodotti ufficiali Tecnaria (connettori CTF/CTL, CEM-E, MINI CEM-E, V-CEM-E, CTCEM, "
        "Diapason, Omega, Manicotto GTS), Spit P560 e relativi accessori, certificazioni (ETA/DoP/CE), "
        "manuali di posa, capitolati, computi metrici, assistenza, posa in opera. "
        "Se la domanda non rientra in questo perimetro o cita marchi/prodotti non Tecnaria, "
        "rispondi: 'Non posso rispondere: non è un prodotto Tecnaria ufficiale.' "
        "Stile: sintetico, preciso, puntato. Niente divagazioni. Italiano."
    )
}

# Prompt di formato/qualità (evita fuffa)
FORMAT_HINT = (
    "Formato della risposta:\n"
    "- Titolo in una riga (max 100 caratteri)\n"
    "- 3–6 punti elenco tecnici (uso, compatibilità, valori rilevanti, norme/certificazioni se pertinenti)\n"
    "- Una riga finale 'Se ti serve altro su Tecnaria, chiedi pure.'"
)

def mentions_banned(text: str) -> bool:
    for pat in BANNED:
        if re.search(pat, text, flags=re.IGNORECASE):
            return True
    return False

@app.route("/", methods=["GET"])
def home():
    return "Tecnaria QA è online. POST /ask {question: \"...\"}", 200

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()

    if not question:
        return jsonify({"error": "Missing 'question' in JSON body."}), 400

    # Guard-rail: blocco duro se rilevo termini non Tecnaria
    if mentions_banned(question):
        return jsonify({"answer": "Non posso rispondere: non è un prodotto Tecnaria ufficiale."}), 200

    # Messaggi per il modello
    messages = [
        SYSTEM_MSG,
        {"role": "user", "content": f"Domanda utente: {question}\n\n{FORMAT_HINT}"}
    ]

    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=TEMPERATURE,
            top_p=1,
            max_tokens=600
        )
        answer = resp.choices[0].message["content"].strip()
        # Filtro di sicurezza: se il modello ha ignorato le regole
        if any(re.search(p, answer, re.IGNORECASE) for p in BANNED):
            answer = "Non posso rispondere: non è un prodotto Tecnaria ufficiale."
        return jsonify({"answer": answer}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
