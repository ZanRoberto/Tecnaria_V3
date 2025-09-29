# app.py — Render-ready (message/question), Slim + Critici + Switch + Model picker
import os, re, json
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from openai import OpenAI

# ================== Config (da Environment su Render) ==================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata (Render ➜ Environment).")

# Interruttore arricchimenti critici: "1"/"true"/"yes" = ON, altro = OFF
CRITICI_ENRICH = os.getenv("CRITICI_ENRICH", "1").lower() in ("1", "true", "yes")

# Lista modelli: priorità a MODEL_NAME se definito su Render, poi fallback
MODEL_CANDIDATES = [
    os.getenv("MODEL_NAME") or "",  # es. "gpt-4o" o "gpt-4.1"
    "gpt-4o", "gpt-4.0",
    "gpt-4.1", "gpt-4.1-mini",
    "gpt-4o-mini",
]

# File opzionali (arricchimenti minimi)
CONTACTS_FILE = "static/data/contatti.json"             # recapiti ufficiali (semplice)
CRITICI_DIR   = "static/data/critici"                   # cartella JSON critici
F_CONTATTI    = Path(CRITICI_DIR) / "contatti.json"     # (qui puoi avere anche PEC e dati legali)
F_BANCARI     = Path(CRITICI_DIR) / "bancari.json"
F_HSINC       = Path(CRITICI_DIR) / "hs_incoterms.json"
F_POSACTF     = Path(CRITICI_DIR) / "posa_ctf.json"
F_CERTS       = Path(CRITICI_DIR) / "certs.json"
F_POLRESI     = Path(CRITICI_DIR) / "policy_resi.json"

# ================== OpenAI (Responses API) ==================
client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
Sei un assistente tecnico-commerciale per TECNARIA S.p.A.
- Rispondi in modo chiaro, sintetico, professionale, nella lingua dell'utente (IT di default).
- Se un dato è incerto (numeri, recapiti, HS code): non inventare; segnala l'incertezza.
- Non fornire contatti se non espressamente disponibili nei dati ufficiali dell'app.
- Per posa: attieniti a documentazione Tecnaria; se il dettaglio non è certo, dillo.
- Per export/Incoterms/HS code: EXW/FCA sono i più comuni, ma HS code richiede prodotto e Paese.
- Evita dettagli non verificabili e toni categorici; usa bullet dove utile.
"""

def _pick_model() -> str:
    last_err = None
    for m in [x for x in MODEL_CANDIDATES if x]:
        try:
            _ = client.responses.create(model=m, input=[{"role":"user","content":"ping"}], max_output_tokens=5)
            return m
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"Nessun modello utilizzabile. Ultimo errore: {last_err}")

MODEL_IN_USE = None  # sarà valorizzato alla prima domanda

# ================== App FastAPI ==================
app = FastAPI(title="Tecnaria Bot - Slim+Critici (Render Ready)")
if Path("static").exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")

# ================== Utils ==================
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
    except Exception:
        return None

def _load_contacts_block_primary() -> Optional[str]:
    try:
        p = Path(CONTACTS_FILE)
        if not p.exists():
            return None
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

def _load_contacts_block_critici() -> Optional[str]:
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
    # opzionali legali nel contatti.json
    if data.get("partita_iva"):    lines.append(f"Partita IVA: {data['partita_iva']}")
    if data.get("codice_fiscale"): lines.append(f"Codice Fiscale: {data['codice_fiscale']}")
    if data.get("rea"):            lines.append(f"REA: {data['rea']}")
    if data.get("sdi"):            lines.append(f"SDI: {data['sdi']}")
    if not lines:
        return None
    return _pack("Dati ufficiali", lines, "Fonte: static/data/critici/contatti.json")

# ================== Trigger minimi ==================
RX_CONTACTS  = re.compile(r"\b(contatt|telefono|tel\.?|telefon|mail|email|pec|sede|indirizzo|recapiti|ufficio)\b", re.I)
RX_CTF_POSA  = re.compile(r"\b(ctf)\b.*\b(posa|fiss|chiod|lamiera)\b|\b(posa|fiss|chiod|lamiera)\b.*\b(ctf)\b", re.I)
RX_EXPORT    = re.compile(r"\b(export|spedizion|incoterm|resa|hs\s*code|dogan)\b", re.I)
RX_BANK      = re.compile(r"\b(iban|bic|swift|coordinate\s*banc|bonifico)\b", re.I)
RX_CERTS     = re.compile(r"\b(eta|certificaz|marcatura\s*ce|do[pb]|rapporto\s*prova)\b", re.I)
RX_RESI      = re.compile(r"\b(resi?|reso|rma|garanzi[ae])\b", re.I)

# ================== Blocchi critici ==================
def block_hs_incoterms() -> Optional[str]:
    data = _load_json(F_HSINC)
    if not data:
        return _pack("Export & Incoterms – Nota",
                     ["Incoterms più frequenti: **EXW** / **FCA**.",
                      "Per **HS code** serve confermare **prodotto e Paese**."],
                     "Confermare condizioni definitive in offerta/ordine.")
    bullets = []
    if data.get("nota"): bullets.append(data["nota"])
    return _pack("Export & Incoterms – Nota", bullets, None) if bullets else None

def block_posa_ctf() -> Optional[str]:
    data = _load_json(F_POSACTF)
    if not data:
        return _pack("Posa CTF – Nota",
                     ["Per ogni connettore CTF: **2 chiodi HSBR14** con **SPIT P560**.",
                      "Rispettare geometria lamiera e documentazione Tecnaria (ETA/tavole)."],
                     "Condizioni particolari: attenersi alle tavole di progetto.")
    bullets = []
    if data.get("nota"): bullets.append(data["nota"])
    return _pack("Posa CTF – Nota", bullets, None) if bullets else None

def block_bancari() -> Optional[str]:
    data = _load_json(F_BANCARI)
    if not data: return None
    bullets = []
    if data.get("beneficiario"): bullets.append(f"Beneficiario: {data['beneficiario']}")
    if data.get("iban"):         bullets.append(f"IBAN: {data['iban']}")
    if data.get("bic"):          bullets.append(f"BIC/SWIFT: {data['bic']}")
    if data.get("banca"):        bullets.append(f"Banca: {data['banca']}")
    return _pack("Coordinate bancarie (ufficiali)", bullets, "Fonte: static/data/critici/bancari.json") if bullets else None

def block_certs() -> Optional[str]:
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

def block_policy_resi() -> Optional[str]:
    data = _load_json(F_POLRESI)
    if not data: return None
    bullets = []
    if data.get("resi"):     bullets.append(f"Resi: {data['resi']}")
    if data.get("garanzia"): bullets.append(f"Garanzia: {data['garanzia']}")
    return _pack("Resi & Garanzia (policy)", bullets, "Fonte: static/data/critici/policy_resi.json") if bullets else None

def block_contacts_any() -> Optional[str]:
    adv = _load_contacts_block_critici()
    if adv: return adv
    return _load_contacts_block_primary()

# ================== Modello per primo ==================
def answer_via_model(question: str, lang: str = "it") -> str:
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

def enrich_minimally(question: str, model_answer: str) -> str:
    """Aggiunge SOLO i blocchi minimi quando la domanda tocca un dato critico (se CRITICI_ENRICH=True)."""
    if not CRITICI_ENRICH:
        return model_answer.strip()

    q = (question or "").lower()
    enriched = model_answer.strip()

    if RX_CONTACTS.search(q):
        block = block_contacts_any()
        if block: enriched += "\n\n---\n" + block
    if RX_CTF_POSA.search(q):
        block = block_posa_ctf()
        if block: enriched += "\n\n---\n" + block
    if RX_EXPORT.search(q):
        block = block_hs_incoterms()
        if block: enriched += "\n\n---\n" + block
    if RX_BANK.search(q):
        block = block_bancari()
        if block: enriched += "\n\n---\n" + block
    if RX_CERTS.search(q):
        block = block_certs()
        if block: enriched += "\n\n---\n" + block
    if RX_RESI.search(q):
        block = block_policy_resi()
        if block: enriched += "\n\n---\n" + block

    return enriched

# ================== Endpoints ==================
@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "Tecnaria Bot - Slim+Critici",
        "enrich": CRITICI_ENRICH,
        "model_in_use": MODEL_IN_USE
    }

# (Homepage minimale di cortesia, se non usi la tua index)
@app.get("/", response_class=HTMLResponse)
def home():
    badge = "ON" if CRITICI_ENRICH else "OFF"
    return f"""
<!DOCTYPE html>
<html lang="it">
<head><meta charset="utf-8" /><meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Tecnaria Bot</title>
<style>body{{font-family:system-ui;max-width:840px;margin:40px auto;padding:0 16px}}
textarea{{width:100%;height:120px}}button{{padding:8px 12px}}pre{{background:#f6f6f6;padding:12px;border-radius:8px;white-space:pre-wrap}}</style>
</head>
<body>
<h1>Tecnaria Bot</h1>
<p>Modello OpenAI ➜ arricchimenti critici: <b>{badge}</b></p>
<textarea id="q" placeholder="Scrivi qui (usa /ask con 'message')">Mi parli della P560?</textarea><br/>
<button onclick="ask()">Chiedi</button>
<pre id="out"></pre>
<script>
async function ask(){{
  const q = document.getElementById('q').value;
  const res = await fetch('/ask', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{message:q}})}});
  const j = await res.json();
  document.getElementById('out').textContent = j.response || '(nessuna risposta)';
}}
</script>
</body></html>
"""

# ✅ Endpoint compatibile con il tuo front-end:
# - accetta { "message": "..." } (e anche "question" come fallback)
# - risponde sempre { "response": "..." }
@app.post("/ask")
async def ask(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"response": "Payload non valido."}, status_code=400)

    text = (data.get("message") or data.get("question") or "").strip()
    lang = (data.get("lang") or "it").lower()
    if not text:
        return JSONResponse({"response": "Domanda vuota."}, status_code=400)

    # 1) Modello per primo (stile ChatGPT)
    model_answer = ""
    try:
        model_answer = answer_via_model(text, lang)
    except Exception as e:
        print(f"[warn] model failed: {e}")

    # 2) Arricchimento opzionale (switch CRITICI_ENRICH)
    if model_answer:
        final = enrich_minimally(text, model_answer)
        return JSONResponse({"response": final, "mode": f"gen+critici({'ON' if CRITICI_ENRICH else 'OFF'})"})

    # 3) Ultimo fallback
    return JSONResponse({"response": "Non ho trovato una risposta. Riprova tra poco."}, status_code=503)
