import os, re, logging, glob
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = Flask(__name__)
CORS(app, resources={r"/ask": {"origins": "*"}})

# === Config ENV ===
OPENAI_API_KEY   = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL     = os.environ.get("OPENAI_MODEL", "gpt-5")
TEMPERATURE      = float(os.environ.get("OPENAI_TEMPERATURE", "0"))
USE_LLM          = os.environ.get("USE_LLM", "1") == "1"
NOTE_DIR         = os.environ.get("NOTE_DIR", "documenti_gTab")  # <--- tua cartella locale

client = OpenAI(api_key=OPENAI_API_KEY)

# Termini da escludere (non Tecnaria)
BANNED = [r"\bHBV\b", r"\bFVA\b", r"\bAvantravetto\b", r"\bT[\- ]?Connect\b", r"\bAlfa\b"]

SYSTEM_MSG = {
    "role": "system",
    "content": (
        "Sei un esperto dei prodotti Tecnaria S.p.A. di Bassano del Grappa. "
        "Rispondi SOLO su prodotti ufficiali Tecnaria: connettori CTF/CTL, CEM-E, MINI CEM-E, V-CEM-E, CTCEM, "
        "Diapason, Omega, Manicotto GTS; chiodatrice Spit P560 e accessori; certificazioni (ETA/DoP/CE); "
        "manuali di posa, capitolati, computi metrici, assistenza, posa in opera. "
        "Se la domanda esce da questo perimetro o cita prodotti non Tecnaria, rispondi: "
        "'Non posso rispondere: non è un prodotto Tecnaria ufficiale.' "
        "Stile: sintetico, preciso, puntato. Italiano."
    )
}

# Stili A/B/C (puoi tenere B di default nel tuo frontend)
STYLE_HINTS = {
    "A": "Formato: 2–3 bullet essenziali, niente chiusura.",
    "B": "Formato: Titolo (<=80c) + 3–4 bullet tecnici + riga finale 'Se ti serve altro su Tecnaria, chiedi pure.'",
    "C": "Formato: Titolo (<=100c) + 5–8 bullet tecnici + breve suggerimento operativo."
}
STYLE_TOKENS = {"A":180, "B":280, "C":380}

def normalize_style(val):
    if not val: return "B"
    v = str(val).strip().upper()
    if v in ("A","SHORT"): return "A"
    if v in ("C","DETAILED","LONG"): return "C"
    return "B"

def banned(text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in BANNED)

# ========= MOTORE DETERMINISTICO (facoltativo) =========
def answer_deterministico(q: str):
    ql = q.lower()
    # Esempio semplice: altezze CTF
    if "ctf" in ql and ("altezza" in ql or "altezze" in ql or "consiglio" in ql):
        return (
            "Scelta indicativa altezze CTF:\n"
            "- Lamiera grecata comune: CTF090–CTF105 (in base a profilo e spessori)\n"
            "- Soletta piena ridotta: CTF070–CTF090\n"
            "- Ricorda copriferro e dettagli di posa; specificando lamiera/soletta posso affinare."
        )
    return None

# ========= NOTE TECNICHE LOCALI =========
# Convenzione semplice:
#  - cartelle: documenti_gTab/CTF, documenti_gTab/CTL, documenti_gTab/CEM-E, documenti_gTab/DIAPASON, documenti_gTab/P560, ...
#  - file .txt con testo libero (prima riga = titolo facoltativo)
KEYMAP = {
    "CTF": ["ctf","acciaio-calcestruzzo","lamiera"],
    "CTL": ["ctl","legno-calcestruzzo","legno"],
    "CEM-E": ["cem-e","ripresa di getto","ripresa","vecchio-nuovo"],
    "MINI CEM-E": ["mini cem-e","mini cem"],
    "V-CEM-E": ["v-cem-e","vcem","v cem"],
    "CTCEM": ["ctcem","ct cem"],
    "DIAPASON": ["diapason"],
    "OMEGA": ["omega"],
    "GTS": ["manicotto gts","gts"],
    "P560": ["p560","spit p560","chiodatrice"],
}

def guess_topic(question: str) -> str | None:
    q = question.lower()
    # match più "forte" su token del dizionario
    for topic, keys in KEYMAP.items():
        if any(k in q for k in keys):
            return topic
    # fallback: se contiene "connettore" ma non è specifico
    if "connettore" in q:
        return None
    return None

def load_note_files(topic: str):
    # Cerca .txt nella sottocartella del topic
    folder = os.path.join(NOTE_DIR, topic)
    paths = sorted(glob.glob(os.path.join(folder, "*.txt")))
    return paths

def best_local_note(question: str, topic: str) -> str | None:
    """
    Se esistono file .txt per il topic, sceglie il 'miglior' file per parole chiave semplici.
    Logica leggera (no dipendenze): conteggio match nel filename e nel contenuto.
    """
    paths = load_note_files(topic)
    if not paths: 
        return None

    q = question.lower()
    best_score, best_text = 0, None
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8") as f:
                txt = f.read()
        except Exception:
            continue
        base = os.path.basename(p).lower()
        # score grezzo: occorrenze di parole di domanda nel filename + nel testo
        tokens = [w for w in re.split(r"[^a-z0-9àèéìòóù]+", q) if w]
        score = 0
        for t in tokens:
            if len(t) <= 2: 
                continue
            score += base.count(t) + txt.lower().count(t)
        if score > best_score:
            best_score, best_text = score, txt.strip()

    if not best_text:
        return None

    # Limita la lunghezza della nota per non dilagare
    MAX_CHARS = 1200
    if len(best_text) > MAX_CHARS:
        best_text = best_text[:MAX_CHARS].rstrip() + " …"
    return best_text

def attach_local_note(answer: str, question: str) -> str:
    topic = guess_topic(question)
    if not topic:
        return answer
    note = best_local_note(question, topic)
    if not note:
        return answer
    # Se la nota ha una prima riga titolo, la evidenziamo
    lines = note.splitlines()
    if lines and len(lines[0]) <= 100:
        title = lines[0].strip()
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        block = f"---\n📎 Nota tecnica (locale) — {title}\n{body}" if body else f"---\n📎 Nota tecnica (locale)\n{title}"
    else:
        block = f"---\n📎 Nota tecnica (locale)\n{note}"
    return f"{answer}\n\n{block}"

# ========= API =========
@app.get("/")
def home():
    return jsonify({
        "status": "ok",
        "service": "Tecnaria QA",
        "note_dir_exists": os.path.isdir(NOTE_DIR),
        "note_dir": NOTE_DIR,
        "endpoints": {"ask": "POST /ask {question: str, style?: 'A'|'B'|'C'}"},
        "model": OPENAI_MODEL,
        "temperature": TEMPERATURE,
        "use_llm": USE_LLM
    }), 200

@app.post("/ask")
def ask():
    if not OPENAI_API_KEY:
        return jsonify({"error":"OPENAI_API_KEY non configurata"}), 500

    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"error":"Body JSON non valido."}), 400

    q = (data.get("question") or "").strip()
    style = normalize_style(data.get("style"))

    if not q:
        return jsonify({"error":"Missing 'question'."}), 400
    if banned(q):
        return jsonify({"answer":"Non posso rispondere: non è un prodotto Tecnaria ufficiale.", "source":"guardrail"}), 200

    # 1) Prova motore deterministico
    det = answer_deterministico(q)
    if det is not None and not USE_LLM:
        # Solo deterministico
        det = attach_local_note(det, q)
        return jsonify({"answer": det, "source":"deterministico"}), 200

    if det is not None and USE_LLM is False:
        det = attach_local_note(det, q)
        return jsonify({"answer": det, "source":"deterministico"}), 200

    # 2) Se hai risposta deterministica e vuoi comunque passare dall'LLM, puoi combinare.
    # Qui teniamo: se c'è det → priorità a det, altrimenti LLM.
    if det is not None and USE_LLM is False:
        det = attach_local_note(det, q)
        return jsonify({"answer": det, "source":"deterministico"}), 200

    if det is not None and USE_LLM is True:
        # restituisci det (snello) + eventuale nota locale
        det = attach_local_note(det, q)
        return jsonify({"answer": det, "source":"deterministico"}), 200

    # 3) LLM (se serve)
    if not USE_LLM:
        # niente LLM e nessuna regola → messaggio neutro + nota locale (se c'è)
        base = "Per questa domanda non ho una regola deterministica. Specifica meglio o chiedimi un prodotto Tecnaria."
        base = attach_local_note(base, q)
        return jsonify({"answer": base, "source":"deterministico"}), 200

    # chiamata modello
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                SYSTEM_MSG,
                {"role":"user","content": f"Domanda utente: {q}\n\n{STYLE_HINTS.get(style,'')}"}
            ],
            temperature=TEMPERATURE, top_p=1, max_tokens=STYLE_TOKENS.get(style, 280)
        )
        ans = (resp.choices[0].message["content"] or "").strip()
        if banned(ans):
            ans = "Non posso rispondere: non è un prodotto Tecnaria ufficiale."
        # Aggancia nota tecnica locale (se esiste per il topic)
        ans = attach_local_note(ans, q)
        return jsonify({"answer": ans, "source":"llm", "style_used": style}), 200

    except Exception as e:
        logging.exception("Errore OpenAI")
        return jsonify({"error": f"OpenAI error: {str(e)}"}), 500

# Errori sempre JSON
@app.errorhandler(HTTPException)
def _http(e: HTTPException):
    return jsonify({"error": e.description, "code": e.code}), e.code

@app.errorhandler(Exception)
def _any(e: Exception):
    logging.exception("Errore imprevisto")
    return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
