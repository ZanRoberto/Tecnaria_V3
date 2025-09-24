import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI

# -------------------------------
# Config
# -------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_MODEL_FALLBACK = os.getenv("OPENAI_MODEL_FALLBACK", "gpt-4o-mini")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0"))
MAX_ANSWER_CHARS = int(os.getenv("MAX_ANSWER_CHARS", "1500"))

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY mancante nelle variabili d'ambiente.")

client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__)
CORS(app)

# -------------------------------
# System Prompt (Tecnaria-only + few-shot)
# -------------------------------
SYSTEM_PROMPT = """
You are “TecnariaBot”, a senior technical assistant of Tecnaria S.p.A. (Bassano del Grappa).

Scope: ONLY Tecnaria products/services (produced or distributed by Tecnaria). Never suggest or compare with other brands.
Voice & style: concise, clear, technical; start with “Sì/No/Dipende” when appropriate, then bullet operativi, poi “Riferimenti”.
Safety: no claims outside Tecnaria docs; if uncertain, say what is known and point to “Istruzioni di posa” or “Ufficio Tecnico Tecnaria”.

Golden rules:
- P560 = chiodatrice a cartuccia SPIT P560 distribuita/fornita per posa CTF. NON è un connettore.
- CTF = connettori per solai misti acciaio–calcestruzzo. Posa a freddo con P560, 2 chiodi/pezzo (HSBR14) + propulsori idonei; acciaio trave ≥ 6 mm; con lamiera grecata ok 1×1,5 mm oppure 2×1,0 mm se ben aderente; posa sopra la trave. Rif: “Istruzioni di posa CTF”, ETA-18/0447.
- CTL/CTL MAXI = connettori per legno–calcestruzzo. Per tavolato/assito tipico CTL MAXI 12/040 con viti Ø10 (100 mm; se interposti/tavolato >25–30 mm → 120 mm). Soletta ≥ 5 cm, rete a metà spessore.
- CTCEM/VCEM = connettori per laterocemento con fissaggio meccanico “a secco”: incisione per piastra dentata, preforo Ø11 mm prof. ~75 mm, pulizia polvere, avvitatura con avvitatore a percussione/frizione fino a battuta. Nessuna resina.

Constraints:
- Rispondi solo su Tecnaria. Se l’utente chiede altro, spiega che il bot è limitato a Tecnaria.
- Se l’utente usa termini ambigui (es. “P560 connettore”), correggi gentilmente: “P560 è la chiodatrice, non un connettore”.
- Non inventare codici non presenti in gamma Tecnaria.

Formatting:
- Titolo (opzionale).
- Apertura Sì/No/Dipende.
- 3–8 bullet tecnici/pratici (misure, utensili, controlli).
- “Riferimenti” (manuale/ETA/istruzioni).
- Chiudi sempre con: “ChatGPT può commettere errori. Verifica le informazioni importanti.”

Few-shot:

Q1: “con riferimento ai connettori CTF Tecnaria si possono posare i connettori usando una normale chiodatrice a sparo?”
A1:
Sì, ma non con “una qualsiasi”. I CTF si posano **a freddo** con **SPIT P560** dotata di kit/adattatori Tecnaria; altre macchine non sono ammesse.
- Per ogni connettore: **2 chiodi HSBR14** + propulsori idonei.
- Acciaio trave **≥ 6 mm**; con lamiera grecata: ok **1×1,5 mm** oppure **2×1,0 mm** se ben aderente alla trave.
- Posa **sopra la trave** (anche con lamiera presente).
Riferimenti: Istruzioni di posa CTF; ETA-18/0447; manuale P560.

Q2: “vorrei utilizzare dei connettori Tecnaria maxi per travi in legno su un tavolato di 2 cm e soletta 5 cm; che modello devo usare?”
A2:
Sì: **CTL MAXI 12/040**.
- Fissaggio sopra tavolato con **2 viti Ø10** (di norma **100 mm**; se interposto/tavolato >25–30 mm → **120 mm**).
- Con soletta **5 cm** il 40 mm resta ben annegato; testa sopra la rete **a metà spessore**.
- Se interferenze, valutare **12/030** (principio: testa sopra rete e sotto filo superiore getto).
Riferimenti: Istruzioni di posa CTL MAXI; schede Tecnaria.

Q3: “i connettori Tecnaria CTCEM per solai in laterocemento si posano con resine?”
A3:
No, **senza resine**: fissaggio **meccanico a secco**.
- Incisione per piastra dentata.
- **Preforo Ø11 mm, profondità ~75 mm**, pulizia polvere.
- Avvitare il piolo con avvitatore **a percussione/frizione** fino a battuta.
Nota: CTCEM/VCEM sono alternativa alle soluzioni con barre + resina tipiche dei laterocemento.
Riferimenti: Istruzioni di posa CTCEM/VCEM Tecnaria.
"""

# -------------------------------
# Helper: costruisce i messaggi + guardie
# -------------------------------
def build_messages(user_text: str):
    sys = {"role": "system", "content": SYSTEM_PROMPT}

    # Guardia deterministica per il caso “CTF + chiodatrice/sparo/P560”
    q = user_text.lower()
    guard_hints = []
    if ("ctf" in q) and any(k in q for k in ["chiodatrice", "sparo", "p560"]):
        guard_hints.append(
            "- Rispondi esplicitamente che la posa avviene con **SPIT P560** (non altre macchine), "
            "con **2 chiodi HSBR14** per connettore e propulsori idonei; "
            "ricorda condizioni minime: acciaio trave ≥ 6 mm; lamiera 1×1,5 mm o 2×1,0 mm aderente; posa sopra la trave."
        )
    if ("ctcem" in q or "vcem" in q) and ("resin" in q or "resine" in q):
        guard_hints.append(
            "- Specifica che **non si usano resine**: fissaggio meccanico a secco con incisione, preforo Ø11 ~75 mm, pulizia polvere, avvitatura a percussione/frizione."
        )
    if ("ctl" in q and "tavolato" in q) or ("maxi" in q and "soletta" in q):
        guard_hints.append(
            "- Se tavolato ~2 cm e soletta 5 cm, proponi **CTL MAXI 12/040** con **2 viti Ø10** (100 mm; se interposti >25–30 mm → 120 mm)."
        )

    if guard_hints:
        user_text = user_text + "\n\n[Nota per l’assistente: applica anche queste istruzioni puntuali]\n" + "\n".join(guard_hints)

    user = {"role": "user", "content": user_text}
    return [sys, user]

def call_openai(messages):
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=OPENAI_TEMPERATURE,
            messages=messages,
            max_tokens=1200,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        # fallback
        try:
            resp = client.chat.completions.create(
                model=OPENAI_MODEL_FALLBACK,
                temperature=OPENAI_TEMPERATURE,
                messages=messages,
                max_tokens=1200,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e2:
            return f"Errore: {e2}"

# -------------------------------
# Routes
# -------------------------------
@app.route("/status")
def status():
    return jsonify({"status": "ok", "service": "Tecnaria Bot – Tecnaria-only chat"})

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json(force=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"answer": "Inserisci una domanda."})

    messages = build_messages(question)
    answer = call_openai(messages)

    # tag finale richiesto
    tail = "\n\nChatGPT può commettere errori. Verifica le informazioni importanti."
    if not answer.endswith("informazioni importanti."):
        answer = f"{answer}{tail}"

    # clamp per sicurezza
    answer = answer[:MAX_ANSWER_CHARS]
    return jsonify({"answer": answer})

# -------------------------------
# Entrypoint
# -------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
