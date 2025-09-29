# app.py — Tecnaria Bot (Flask) • Render-ready • ChatGPT+Critici • Debug /diag
import os, re, json, logging
from pathlib import Path
from typing import Optional, List

from flask import Flask, request, jsonify, make_response
from openai import OpenAI

# ====== LOGGING SEMPLICE ======
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tecnaria-bot")

# ====== ENV OBBLIGATORI / OPZIONALI ======
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata (Render → Environment).")

# Interruttore arricchimenti (default ON)
CRITICI_ENRICH = os.getenv("CRITICI_ENRICH", "1").lower() in ("1", "true", "yes")

# Nome modello: priorità a MODEL_NAME; compatibilità con OPENAI_MODEL; poi fallback automatici
MODEL_CANDIDATES = [
    os.getenv("MODEL_NAME") or os.getenv("OPENAI_MODEL") or "",
    "gpt-4o", "gpt-4.0",
    "gpt-4.1", "gpt-4.1-mini",
    "gpt-4o-mini",
]

# ====== FILE LOCALI (OPZIONALI) ======
CONTACTS_FILE = "static/data/contatti.json"       # recapiti base (opzionale)
CRITICI_DIR   = "static/data/critici"
F_CONTATTI    = Path(CRITICI_DIR) / "contatti.json"
F_BANCARI     = Path(CRITICI_DIR) / "bancari.json"
F_HSINC       = Path(CRITICI_DIR) / "hs_incoterms.json"
F_POSACTF     = Path(CRITICI_DIR) / "posa_ctf.json"
F_CERTS       = Path(CRITICI_DIR) / "certs.json"
F_POLRESI     = Path(CRITICI_DIR) / "policy_resi.json"

# ====== OPENAI CLIENT ======
client = OpenAI(api_key=OPENAI_API_KEY)
MODEL_IN_USE: Optional[str] = None

SYSTEM_PROMPT = """
Sei un assistente tecnico-commerciale per TECNARIA S.p.A.
- Rispondi in modo chiaro, sintetico, professionale, nella lingua dell'utente (IT di default).
- Se un dato è incerto (numeri, recapiti, HS code): non inventare; segnala l'incertezza.
- Non fornire contatti se non espressamente disponibili nei dati ufficiali dell'app.
- Per posa: attieniti a documentazione Tecnaria; se il dettaglio non è certo, dillo.
- Per export/Incoterms/HS code: EXW/FCA sono i più comuni, ma HS code richiede prodotto e Paese.
- Evita dettagli non verificabili e toni categorici; usa bullet dove utile.
"""

# ====== FLASK ======
app = Flask(__name__, static_folder="static", static_url_path="/static")

# ====== UTILS ======
def _pack(head: str, bullets: List[str], note: Optional[str] = None) -> str:
    lines = [head] if head else []
    lines += [f"- {b}" for b in bullets if b]
    if note:
        lines.append("")
        lines.append(f"_Nota:_ {note}")
    return "\n".join(lines).strip()

def _load_json(path: Path) -> Optional[dict]:
    try:
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
            return obj if isinstance(obj, dict) else None
    except Exception as e:
        log.warning(f"Impossibile leggere {path}: {e}")
        return None

def _contacts_primary_block() -> Optional[str]:
    p = Path(CONTACTS_FILE)
    if not p.exists():
        return None
    try:
        data = json.load(open(p, "r", encoding="utf-8"))
    except Exception:
        return None
    lines = []
    comp = data.get("company")
    if comp: lines.append(f"**{comp}**")
    addr = " ".join([data.get("address",""), data.get("city","")]).strip()
    if addr: lines.append(addr)
    if data.get("phone"):  lines.append(f"Tel: {data['phone']}")
    if data.get("email"):  lines.append(f"Email: {data['email']}")
    if data.get("website"):lines.append(f"Sito: {data['website']}")
    if not lines:
        return None
    return _pack("Dati ufficiali", lines, "Fonte: static/data/contatti.json")

def _contacts_critici_block() -> Optional[str]:
    data = _load_json(F_CONTATTI)
    if not data:
        return None
    lines = []
    comp = data.get("company")
    if comp: lines.append(f"**{comp}**")
    addr = " ".join([data.get("address",""), data.get("city","")]).strip()
    if addr: lines.append(addr)
    if data.get("phone"):   lines.append(f"Tel: {data['phone']}")
    if data.get("email"):   lines.append(f"Email: {data['email']}")
    if data.get("pec"):     lines.append(f"PEC: {data['pec']}")
    if data.get("website"): lines.append(f"Sito: {data['website']}")
    # opzionali legali accorpati in contatti.json
    for k,label in (("partita_iva","Partita IVA"),("codice_fiscale","Codice Fiscale"),("rea","REA"),("sdi","SDI")):
        if data.get(k): lines.append(f"{label}: {data[k]}")
    if not lines:
        return None
    return _pack("Dati ufficiali", lines, "Fonte: static/data/critici/contatti.json")

# Trigger “critici”
RX_CONTACTS  = re.compile(r"\b(contatt|telefono|tel\.?|telefon|mail|email|pec|sede|indirizzo|recapiti|ufficio)\b", re.I)
RX_CTF_POSA  = re.compile(r"\b(ctf)\b.*\b(posa|fiss|chiod|lamiera)\b|\b(posa|fiss|chiod|lamiera)\b.*\b(ctf)\b", re.I)
RX_EXPORT    = re.compile(r"\b(export|spedizion|incoterm|resa|hs\s*code|dogan)\b", re.I)
RX_BANK      = re.compile(r"\b(iban|bic|swift|coordinate\s*banc|bonifico)\b", re.I)
RX_CERTS     = re.compile(r"\b(eta|certificaz|marcatura\s*ce|do[pb]|rapporto\s*prova)\b", re.I)
RX_RESI      = re.compile(r"\b(resi?|reso|rma|garanzi[ae])\b", re.I)

def _block_hs_incoterms() -> Optional[str]:
    data = _load_json(F_HSINC)
    if not data:
        return _pack("Export & Incoterms – Nota",
                     ["Incoterms più frequenti: **EXW** / **FCA**.",
                      "Per **HS code** serve confermare **prodotto e Paese**."],
                     "Confermare condizioni definitive in offerta/ordine.")
    bullets = []
    if data.get("nota"): bullets.append(data["nota"])
    return _pack("Export & Incoterms – Nota", bullets, None) if bullets else None

def _block_posa_ctf() -> Optional[str]:
    data = _load_json(F_POSACTF)
    if not data:
        return _pack("Posa CTF – Nota",
                     ["Per ogni connettore CTF: **2 chiodi HSBR14** con **SPIT P560**.",
                      "Rispettare geometria lamiera e documentazione Tecnaria (ETA/tavole)."],
                     "Condizioni particolari: attenersi alle tavole di progetto.")
    bullets = []
    if data.get("nota"): bullets.append(data["nota"])
    return _pack("Posa CTF – Nota", bullets, None) if bullets else None

def _block_bancari() -> Optional[str]:
    data = _load_json(F_BANCARI)
    if not data: return None
    bullets = []
    if data.get("beneficiario"): bullets.append(f"Beneficiario: {data['beneficiario']}")
    if data.get("iban"):         bullets.append(f"IBAN: {data['iban']}")
    if data.get("bic"):          bullets.append(f"BIC/SWIFT: {data['bic']}")
    if data.get("banca"):        bullets.append(f"Banca: {data['banca']}")
    return _pack("Coordinate bancarie (ufficiali)", bullets, "Fonte: static/data/critici/bancari.json") if bullets else None

def _block_certs() -> Optional[str]:
    data = _load_json(F_CERTS)
    if not data: return None
    bullets = []
    eta = data.get("ETA")
    if isinstance(eta, list) and eta:
        bullets.append("ETA: " + ", ".join(eta))
    if data.get("marcatura_CE"):
        bullets.append(f"Marcatura CE: {data['marcatura_CE']}")
    if data.get("note"):
        bullets.append(f"Note: {data['note']}")
    return _pack("Certificazioni", bullets, "Fonte: static/data/critici/certs.json") if bullets else None

def _block_policy_resi() -> Optional[str]:
    data = _load_json(F_POLRESI)
    if not data: return None
    bullets = []
    if data.get("resi"):     bullets.append(f"Resi: {data['resi']}")
    if data.get("garanzia"): bullets.append(f"Garanzia: {data['garanzia']}")
    return _pack("Resi & Garanzia (policy)", bullets, "Fonte: static/data/critici/policy_resi.json") if bullets else None

def _contacts_any() -> Optional[str]:
    return _contacts_critici_block() or _contacts_primary_block()

def _pick_model() -> str:
    last_err = None
    for m in [x for x in MODEL_CANDIDATES if x]:
        try:
            client.responses.create(model=m, input=[{"role":"user","content":"ping"}], max_output_tokens=5)
            log.info(f"Modello selezionato: {m}")
            return m
        except Exception as e:
            last_err = e
            log.warning(f"Modello non disponibile: {m} → {e}")
            continue
    raise RuntimeError(f"Nessun modello utilizzabile. Ultimo errore: {last_err}")

def _answer_via_model(question: str, lang: str = "it") -> str:
    global MODEL_IN_USE
    if not question:
        return ""
    if not MODEL_IN_USE:
        MODEL_IN_USE = _pick_model()
    user_prompt = f"[Lingua: {lang}] Domanda: {question}"
    resp = client.responses.create(
        model=MODEL_IN_USE,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.3,
        max_output_tokens=800,
    )
    out = ""
    for item in resp.output:  # type: ignore
        if item.get("type") == "message":
            for c in item["content"]:
                if c.get("type") == "output_text":
                    out += c.get("text","")
    return (out or "").strip()

def _enrich_minimally(question: str, model_answer: str) -> str:
    if not CRITICI_ENRICH:
        return model_answer.strip()
    q = (question or "").lower()
    enriched = model_answer.strip()
    if RX_CONTACTS.search(q):
        b = _contacts_any()
        if b: enriched += "\n\n---\n" + b
    if RX_CTF_POSA.search(q):
        b = _block_posa_ctf()
        if b: enriched += "\n\n---\n" + b
    if RX_EXPORT.search(q):
        b = _block_hs_incoterms()
        if b: enriched += "\n\n---\n" + b
    if RX_BANK.search(q):
        b = _block_bancari()
        if b: enriched += "\n\n---\n" + b
    if RX_CERTS.search(q):
        b = _block_certs()
        if b: enriched += "\n\n---\n" + b
    if RX_RESI.search(q):
        b = _block_policy_resi()
        if b: enriched += "\n\n---\n" + b
    return enriched

# ====== ENDPOINTS ======
@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "Tecnaria Bot - Flask + Critici",
        "enrich": CRITICI_ENRICH,
        "model_in_use": MODEL_IN_USE
    })

@app.route("/diag")
def diag():
    # Diagnostica non sensibile (non mostra API key)
    found = []
    for p in [Path(CONTACTS_FILE), F_CONTATTI, F_BANCARI, F_HSINC, F_POSACTF, F_CERTS, F_POLRESI]:
        try:
            found.append({"file": str(p), "exists": p.exists(), "size": (p.stat().st_size if p.exists() else 0)})
        except Exception:
            found.append({"file": str(p), "exists": False, "size": 0})
    return jsonify({
        "service": "Tecnaria Bot - Flask + Critici",
        "CRITICI_ENRICH": CRITICI_ENRICH,
        "MODEL_CANDIDATES": [m for m in MODEL_CANDIDATES if m],
        "MODEL_IN_USE": MODEL_IN_USE,
        "files": found
    })

# Home di cortesia (se non usi una tua index.html)
@app.route("/")
def home():
    badge = "ON" if CRITICI_ENRICH else "OFF"
    html = f"""<!doctype html><html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tecnaria Bot</title>
<style>body{{font-family:system-ui;max-width:820px;margin:40px auto;padding:0 16px}}textarea{{width:100%;height:120px}}pre{{background:#f6f6f6;padding:12px;border-radius:8px;white-space:pre-wrap}}</style></head>
<body><h1>Tecnaria Bot</h1><p>Modello ➜ arricchimenti critici: <b>{badge}</b></p>
<textarea id="q">Mi parli della P560?</textarea><br/><button onclick="ask()">Chiedi</button><pre id="out"></pre>
<script>
async function ask(){{
  const q = document.getElementById('q').value;
  const res = await fetch('/ask', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{message:q}})}});
  const j = await res.json();
  document.getElementById('out').textContent = j.response || '(nessuna risposta)';
}}
</script></body></html>"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp

# ✅ Endpoint compatibile col tuo front-end (message / question → response)
@app.route("/ask", methods=["POST"])
def ask():
    try:
        data = request.get_json(force=True, silent=False) or {}
    except Exception:
        return jsonify({"response": "Payload non valido."}), 400

    text = (data.get("message") or data.get("question") or "").strip()
    lang = (data.get("lang") or "it").lower()
    if not text:
        return jsonify({"response": "Domanda vuota."}), 400

    try:
        model_answer = _answer_via_model(text, lang)
    except Exception as e:
        log.warning(f"OpenAI call failed: {e}")
        return jsonify({"response": "Non ho trovato una risposta. Riprova tra poco."}), 503

    if not model_answer:
        return jsonify({"response": "Non ho trovato una risposta. Riprova tra poco."}), 503

    final = _enrich_minimally(text, model_answer)
    return jsonify({"response": final})

# Stub audio: evita errori se la tua pagina lo chiama
@app.route("/audio", methods=["POST"])
def audio_stub():
    return ("", 204)
