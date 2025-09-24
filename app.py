import os
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
from openai import OpenAI

# =======================
# Config
# =======================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_MODEL_FALLBACK = os.getenv("OPENAI_MODEL_FALLBACK", "gpt-4o-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0"))
MAX_ANSWER_CHARS = int(os.getenv("MAX_ANSWER_CHARS", "1800"))

if not OPENAI_API_KEY:
    raise RuntimeError("Variabile d'ambiente OPENAI_API_KEY mancante")

client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# =======================
# System prompt Tecnaria-only (tecnico + commerciale) + few-shot
# =======================
SYSTEM_PROMPT = """
You are “TecnariaBot”, an expert assistant for Tecnaria S.p.A. (Bassano del Grappa). Answer ONLY about Tecnaria products/services
(produced or distributed by Tecnaria). If the user asks about other brands, say you can only answer for Tecnaria.

Voice & style: technical, clear, structured. Start with “Sì/No/Dipende” when applicable, then bullet operativi (3–8 punti) e “Riferimenti”.
If the user asks commercial/admin info (contatti, orari, ordini, listini) answer like the best Tecnaria office staff.

Golden rules:
- P560 = chiodatrice a cartuccia SPIT P560 fornita/distribuita per posa CTF. NON è un connettore.
- CTF = connettori per solai misti acciaio–calcestruzzo. Posa a freddo con P560: 2 chiodi HSBR14/pezzo + propulsori idonei;
  acciaio trave ≥ 6 mm; con lamiera: ok 1×1,5 mm oppure 2×1,0 mm se ben aderente; posa sopra la trave.
- CTL/CTL MAXI = legno–calcestruzzo. Tipico su tavolato 2 cm + soletta 5 cm → CTL MAXI 12/040 con 2 viti Ø10 (100 mm;
  se interposti/tavolato >25–30 mm → 120 mm). Testa sopra rete a metà spessore.
- CTCEM/VCEM = laterocemento. Fissaggio meccanico “a secco”: piccola incisione per piastra dentata, preforo Ø11 mm prof. ~75 mm,
  pulizia polvere, avvitatura a percussione/frizione fino a battuta. Niente resine.
- Se l’utente chiama “P560” un connettore, correggi gentilmente.

Formatting:
- Titolo (opzionale)
- Apertura Sì/No/Dipende
- Bullet tecnici/pratici
- Se utile: “Riferimenti: Istruzioni di posa, ETA-18/0447, manuale Tecnaria”
- Chiudi sempre con: “ChatGPT può commettere errori. Verifica le informazioni importanti.”

Few-shot:

Q: “con riferimento ai connettori CTF Tecnaria si possono posare i connettori usando una normale chiodatrice a sparo?”
A:
Sì, ma non con “una qualsiasi”. I CTF si posano a freddo con **SPIT P560** dotata di kit/adattatori Tecnaria; altre macchine non sono ammesse.
- Per ogni connettore: **2 chiodi HSBR14** + propulsori idonei.
- Acciaio trave **≥ 6 mm**; con lamiera grecata: ok **1×1,5 mm** oppure **2×1,0 mm** se ben aderente alla trave.
- Posa **sopra la trave** (anche con lamiera presente).
Riferimenti: Istruzioni di posa CTF; ETA-18/0447; manuale P560.

Q: “vorrei utilizzare dei connettori Tecnaria maxi per travi in legno su un tavolato di 2 cm e soletta da 5 cm; che modello devo usare?”
A:
Sì: **CTL MAXI 12/040**.
- Fissaggio sopra tavolato con **2 viti Ø10** (di norma **100 mm**; se interposti/tavolato >25–30 mm → **120 mm**).
- Con soletta **5 cm** il 40 mm resta ben annegato; testa **sopra la rete a metà spessore**.
- Se interferenze, valutare **12/030** (principio: testa sopra rete e sotto filo superiore getto).
Riferimenti: Istruzioni di posa CTL MAXI; schede Tecnaria.

Q: “i connettori Tecnaria CTCEM per solai in laterocemento si posano con resine?”
A:
No, **senza resine**: fissaggio **meccanico a secco**.
- Incisione per piastra dentata.
- **Preforo Ø11 mm, profondità ~75 mm**, pulizia polvere.
- Avvitare il piolo con avvitatore **a percussione/frizione** fino a battuta.
Nota: CTCEM/VCEM sono alternativa alle soluzioni con barre + resina tipiche dei laterocemento.
Riferimenti: Istruzioni di posa CTCEM/VCEM Tecnaria.
"""

def build_messages(user_text: str):
    q = user_text.lower()
    guard = []

    if ("ctf" in q) and any(k in q for k in ["chiodatrice", "sparo", "p560"]):
        guard.append(
            "- Indica esplicitamente SPIT P560, 2 chiodi HSBR14/pezzo, acciaio ≥ 6 mm, lamiera 1×1,5 o 2×1,0 aderente; posa sopra la trave."
        )
    if ("ctcem" in q or "vcem" in q) and ("resin" in q or "resine" in q):
        guard.append(
            "- Specifica che non si usano resine: incisione, preforo Ø11 ~75 mm, pulizia, avvitatura a percussione/frizione."
        )
    if (("ctl" in q and "tavolato" in q) or ("maxi" in q and "soletta" in q)):
        guard.append(
            "- Su tavolato ~2 cm + soletta 5 cm, consiglia CTL MAXI 12/040 con 2 viti Ø10 (100 mm; se interposti >25–30 mm → 120 mm)."
        )

    if guard:
        user_text = user_text + "\n\n[Nota per l’assistente: applica anche queste istruzioni]\n" + "\n".join(guard)

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text.strip()},
    ]

def call_openai(messages):
    try:
        r = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=OPENAI_TEMPERATURE,
            messages=messages,
            max_tokens=1200,
        )
        return r.choices[0].message.content.strip()
    except Exception:
        r = client.chat.completions.create(
            model=OPENAI_MODEL_FALLBACK,
            temperature=OPENAI_TEMPERATURE,
            messages=messages,
            max_tokens=1200,
        )
        return r.choices[0].message.content.strip()

# =======================
# Routes
# =======================
@app.route("/")
def home():
    # pagina HTML (templates/index.html)
    return render_template("index.html")

@app.route("/status")
def status():
    return jsonify({"status": "ok", "service": "Tecnaria Bot"})

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json(force=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"answer": "Inserisci una domanda."})

    messages = build_messages(question)
    answer = call_openai(messages)

    tail = "\n\nChatGPT può commettere errori. Verifica le informazioni importanti."
    if not answer.endswith("informazioni importanti."):
        answer += tail

    answer = answer[:MAX_ANSWER_CHARS]
    return jsonify({"answer": answer})

# opzionale: servire file allegati (pdf, immagini) da /static/docs
@app.route("/docs/<path:fname>")
def docs(fname):
    return send_from_directory(os.path.join(app.static_folder, "docs"), fname)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
