# app.py — Tecnaria Bot v4 (Reasoner + Guard-Rails)
# Modalità:
# - PHASE=A  → Solo GOLD commerciali (deterministiche)
# - PHASE=B  → Modello esteso (come v3.x) + GOLD
# - PHASE=C  → GOLD-first, poi Reasoner (modello) con Guard-Rails (DINAMICO ma SICURO)  ← CONSIGLIATO
#
# Novità v4:
# - Validatore anti-errori: rimuove numeri non giustificati (mm/Ø/modelli) e impone clausole critiche di sicurezza
# - Clausole automatiche: CTF↔P560/2 chiodi/in gola; CTCEM↔no resine + foratura/avvitamento; MAXI↔ancoraggio in trave
# - Risposte opponibili e concise, senza presence/frequency_penalty
#
# Start (Render):
# gunicorn -k uvicorn.workers.UvicornWorker -w 1 --timeout 180 -b 0.0.0.0:$PORT app:app

import os, re, time
from typing import Optional, Tuple, List, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

PHASE = (os.getenv("PHASE") or "C").strip().upper()  # C di default: dinamico ma con guard-rails

# OpenAI solo se serve (B o C)
if PHASE in ("B", "C"):
    from openai import OpenAI
    from openai._exceptions import APIConnectionError, APIStatusError, RateLimitError, APITimeoutError
else:
    OpenAI = None
    APIConnectionError = APIStatusError = RateLimitError = APITimeoutError = Exception

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
PREFERRED_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-4.1").strip()
MODEL_FALLBACKS: List[str] = []
for m in [PREFERRED_MODEL, "gpt-4o", "gpt-4.1", "gpt-4.1-mini"]:
    if m and m not in MODEL_FALLBACKS:
        MODEL_FALLBACKS.append(m)

DEFAULT_LANG = (os.getenv("DEFAULT_LANG") or "it").strip().lower()
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "900"))

def attrezzatura_clause() -> str:
    return (
        "Per garantire prestazioni ripetibili, tracciabilità e qualità, è ammessa la chiodatrice SPIT P560; "
        "si usano chiodi idonei secondo istruzioni Tecnaria. Alternative solo previa approvazione tecnica scritta "
        "di Tecnaria a seguito di prova di qualifica in sito."
    )

# =========================
# Prompt base (senza triple-quoted)
# =========================
SYSTEM_KB = (
    "BOT Tecnaria (IT) — Regole: "
    "Fase A: solo commerciale. Fase B: tecnica prudente secondo istruzioni/ETA. "
    "CTF: posa con chiodatrice idonea; P560 nel perimetro commerciale; 2 chiodi idonei; in gola. "
    "CTCEM/VCEM: sistemi meccanici a secco; no resine; foratura+avvitamento a battuta piastra (CTCEM). "
    "Export: ETA, DoP, CE, packing list, famiglia HS code, fattura con Incoterms."
)

SYSTEM_REASONER = (
    "Sei un assistente Tecnaria prudente. Rispondi in italiano, conciso, opponibile. "
    "Usa criteri e procedure, non inventare numeri (mm/Ø/modelli) che non siano citati dall'utente "
    "o già standardizzati nelle GOLD aziendali. Se servono numeri, scrivi che si definiscono su DWG/PDF."
)

USER_WRAPPER = (
    "Domanda utente:\n{question}\n\n"
    "Istruzioni: se la domanda è commerciale (preventivo/export/fasi), rispondi schematico. "
    "Se è tecnica, usa criteri prudenti e rimanda a elaborati per scelte definitive. "
)

# =========================
# GOLD — risposte bloccate (prima di tutto)
# =========================
GoldenRule = Dict[str, Any]

GOLD_PREVENTIVO_EXPORT = (
    "Per il preventivo inviaci: elaborati del solaio (PDF/DWG) e tipologia (lamiera grecata o laterocemento); "
    "quantità indicative e aree/lunghezze interessate; Paese di destinazione e resa Incoterms richiesta (es. EXW/FOB/CIF/DDP); "
    "eventuali esigenze di imballo/etichette e dati aziendali (ragione sociale, VAT/EORI se applicabile, contatto). \n"
    "In offerta ricevi: proposta prodotti e accessori (CTF/CTCEM), documenti di conformità disponibili (ETA, DoP, CE), "
    "packing list e famiglia HS code indicata (conferma in conferma d’ordine), condizioni commerciali e logistiche "
    "(Incoterms, luogo di resa, lead time indicativo, termini economici)."
)

GOLD_FASI_ORDINE = (
    "1) Richiesta & offerta: inviate elaborati/bozza, quantità, Paese e resa Incoterms; ricevete offerta con prodotti "
    "(CTF/CTCEM), condizioni e documenti disponibili (ETA, DoP, CE), nota su packing list e HS family. \n"
    "2) Accettazione & proforma: invio PO/conferma offerta; emissione proforma con valori, Incoterms, luogo di resa, "
    "lead time indicativo e dati fiscali. \n"
    "3) Pagamento & pianificazione: pagamento come da proforma; pianificazione preparazione merce e imballi; coordinamento "
    "con eventuale spedizioniere del cliente. \n"
    "4) Documenti & spedizione: conferma d’ordine aggiornata + packing list (pesi/colli) + famiglia HS code; alla spedizione "
    "fattura commerciale con Incoterms e tracking; ETA/DoP/CE allegati o linkati. \n"
    "5) Post-vendita: supporto pratiche export e documenti; dettagli tecnici (posa/verifiche) in fase B su elaborati aggiornati."
)

GOLD_CTF_CHIODATRICE = (
    "No: non con una “normale” chiodatrice a sparo. "
    + attrezzatura_clause()
    + " Ogni CTF va fissato con 2 chiodi idonei; utensile in asse, piastra in appoggio pieno, posa in gola; "
      "prima della produzione: taratura su provino e tracciabilità lotti. Deroghe solo con approvazione tecnica scritta "
      "di Tecnaria dopo qualifica in sito."
)

GOLD_CTCEM_RESINE = (
    "No: i CTCEM non si posano con resine. Fissaggio meccanico a secco: foratura del travetto, pulizia del foro e "
    "avvitamento della vite fino a battuta della piastra dentata, secondo istruzioni CTCEM. "
    "Eventuali varianti richiedono approvazione tecnica scritta di Tecnaria."
)

GOLD_MAXI_TAVOLATO = (
    "Famiglia corretta: CTL MAXI per posa su tavolato. "
    "Vincolo chiave: le viti devono attraversare il tavolato e ancorare nella trave; fissaggi solo nel tavolato non sono ammessi. "
    "Tracciamento sull’asse della trave.\n\n"
    "Altezza connettore: con soletta 5 cm si valutano CTL MAXI 30 o 40. La scelta dipende da quota rete e coperture "
    "(testa sopra la rete ma sotto il filo superiore del getto), interferenze con armature/accessori, tolleranze e quote reali.\n\n"
    "Fissaggio: viti idonee per CTL MAXI con lunghezza definita a disegno per passare il tavolato e ancorarsi nella trave, "
    "secondo istruzioni Tecnaria (preforo/coppia se previsti).\n\n"
    "Per confermare modello e viti: inviaci spaccato DWG/PDF con sezione trave, pacchetto (tavolato/interposti), quota rete nella soletta 5 cm, essenza/stato del legno.\n\n"
    "Esito operativo: CTL MAXI 30 o 40 coerenti; la conferma si fa su elaborato, garantendo ancoraggio in trave e corretta posizione della testa rispetto alla rete."
)

GOLD_LEAD_TIME = (
    "Il lead time è indicato in offerta e viene confermato in conferma d’ordine in base a quantità, mix prodotti, imballi "
    "e resa Incoterms. Per urgenze valutiamo insieme disponibilità materiali e slot spedizione."
)

GOLD_PAGAMENTI = (
    "Condizioni di pagamento come da offerta/proforma (es. bonifico anticipato o modalità concordate). "
    "Dati fiscali e bancari sono riportati in proforma e fattura."
)

GOLD_DOCUMENTI_EXPORT = (
    "Per l’export forniamo: ETA, DoP, Marcatura CE; packing list con pesi/colli; famiglia HS code; "
    "fattura commerciale con Incoterms e dati del destinatario; eventuali allegati richiesti dal Paese di destinazione."
)

GOLD_DEFAULT_A = (
    "Per la fase commerciale inviaci: elaborati del solaio (PDF/DWG), quantità indicative, Paese di destinazione e resa Incoterms. "
    "In offerta riceverai prodotti coerenti (CTF/CTCEM), documenti di conformità (ETA, DoP, CE), packing list e famiglia HS code, "
    "oltre a condizioni commerciali e logistiche. I dettagli tecnici di posa seguono in fase B."
)

GOLDEN_QA: List[GoldenRule] = [
    {   # MAXI su tavolato/assito/soletta
        "lang": "it",
        "patterns": [
            r"\bmaxi\b.*\btavolat\w+\b.*\b2\s*cm\b.*\bsolett\w+\b.*\b5\s*cm\b",
            r"\bctl\s*maxi\b.*\bmodello\b",
            r"\bconnettori\b.*\bmaxi\b.*\bmodello\b",
            r"\bmaxi\b.*\bsolett\w+\b.*\b5\s*cm\b",
            r"\bmaxi\b.*\b(assito|perlinat\w+|tavolat\w+)\b",
        ],
        "answer": GOLD_MAXI_TAVOLATO
    },
    {   # Preventivo + export
        "lang": "it",
        "patterns": [
            r"\bpreventiv\w+\b.*\bexport\b",
            r"\bpreventiv\w+\b.*\bdocument\w+\b",
            r"\bofferta\b.*\bexport\b",
        ],
        "answer": GOLD_PREVENTIVO_EXPORT
    },
    {   # Fasi ordine→spedizione
        "lang": "it",
        "patterns": [
            r"\bfasi\b.*\bdall'?offerta\b.*\bspedizion\w+",
            r"\bdistributor\w*\b.*\bestero\b.*\bfasi\b.*\bspedizion\w+",
            r"\bordine\b.*\bexport\b.*\bfasi\b",
        ],
        "answer": GOLD_FASI_ORDINE
    },
    {   # CTF + chiodatrice
        "lang": "it",
        "patterns": [
            r"\bctf\b.*\bchiodatrice\b.*\bnormal\w+",
            r"\bchiodatrice\b.*\bsparo\b.*\bctf\b",
            r"\bsi\s*possono\b.*\bctf\b.*\bchiodatrice\b",
        ],
        "answer": GOLD_CTF_CHIODATRICE
    },
    {   # CTCEM + resine
        "lang": "it",
        "patterns": [
            r"\bctcem\b.*\bresin\w+",
            r"\bresin\w+.*\bctcem\b",
            r"\bctcem\b.*\bpos[ao].*\bresin\w+",
            r"\bconnettori\b.*\bctcem\b.*\bresin\w+",
        ],
        "answer": GOLD_CTCEM_RESINE
    },
    {   # Lead time
        "lang": "it",
        "patterns": [
            r"\btempi\b.*\bconsegn\w+",
            r"\blead\s*time\b",
            r"\bquando\b.*\bspedizion\w+",
        ],
        "answer": GOLD_LEAD_TIME
    },
    {   # Pagamenti
        "lang": "it",
        "patterns": [
            r"\bpagament\w+\b",
            r"\bterms?\b.*\bpayment\b",
        ],
        "answer": GOLD_PAGAMENTI
    },
    {   # Documenti export
        "lang": "it",
        "patterns": [
            r"\bdocument\w+\b.*\bexport\b",
            r"\bquali\b.*\bdocument\w+\b",
        ],
        "answer": GOLD_DOCUMENTI_EXPORT
    },
]

def match_golden(question: str, lang: str) -> Optional[str]:
    q = (question or "").lower()
    for rule in GOLDEN_QA:
        rlang = (rule.get("lang") or "").lower()
        if rlang and lang and rlang != lang.lower():
            continue
        for pat in rule.get("patterns", []):
            try:
                if re.search(pat, q, flags=re.I):
                    return rule.get("answer", "").strip()
            except re.error:
                continue
    return None

# =========================
# FastAPI
# =========================
app = FastAPI(title="Tecnaria Bot v4 — Reasoner + Guard-Rails")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

client = OpenAI(api_key=OPENAI_API_KEY) if (PHASE in ("B", "C") and OPENAI_API_KEY) else None

HOME_HTML = """<!doctype html>
<meta charset="utf-8" />
<title>Tecnaria Bot</title>
<style>
  :root { --bd:#e5e7eb; --fg:#111; --muted:#666; }
  body{font:16px system-ui,Arial; color:var(--fg); margin:0}
  .wrap{max-width:880px; margin:40px auto; padding:0 20px}
  textarea{width:100%; height:140px; border:1px solid var(--bd); border-radius:12px; padding:12px}
  .row{display:flex; gap:10px; margin-top:10px}
  select,button{border:1px solid var(--bd); border-radius:10px; padding:10px}
  .out{margin-top:14px; border:1px solid var(--bd); border-radius:12px; padding:12px; white-space:pre-wrap}
  small{color:var(--muted)}
</style>
<div class="wrap">
  <h1>🚀 Tecnaria Bot — Fase {PHASE}</h1>
  <p>Fai una domanda e premi “Chiedi”. GOLD-first; se non coperta, Reasoner con Guard-Rails.</p>
  <textarea id="q" placeholder="Es: Vorrei usare MAXI su tavolato 2 cm e soletta 5 cm: che modello?"></textarea>
  <div class="row">
    <select id="mode">
      <option value="auto" selected>Auto</option>
      <option value="compact">Compatta</option>
      <option value="both">Scheda + Spiegazione</option>
    </select>
    <button onclick="ask()">Chiedi</button>
  </div>
  <div id="out" class="out"></div>
  <small>Endpoint: /ask • Phase: {PHASE} • Modello: {model}</small>
</div>
<script>
async function ask(){
  const out=document.getElementById('out');
  const q=document.getElementById('q').value.trim();
  const mode=document.getElementById('mode').value;
  if(!q){ out.textContent="Inserisci una domanda."; return; }
  out.textContent="⏳...";
  try{
    const r=await fetch("/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question:q, mode})});
    const data=await r.json();
    out.textContent = data.answer || ("Errore: "+(data.detail||JSON.stringify(data)));
  }catch(e){ out.textContent="Errore di rete: "+e.message; }
}
</script>
""".replace("{PHASE}", PHASE).replace("{model}", PREFERRED_MODEL)

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(HOME_HTML)

@app.get("/favicon.ico")
def favicon():
    return PlainTextResponse("", status_code=204)

class AskIn(BaseModel):
    question: str
    mode: Optional[str] = "auto"
    lang: Optional[str] = DEFAULT_LANG

class AskOut(BaseModel):
    ok: bool
    answer: str
    model: Optional[str] = None
    mode: Optional[str] = None

@app.get("/health", response_model=dict)
def health():
    return {"status": "ok", "phase": PHASE, "model": PREFERRED_MODEL, "fallbacks": MODEL_FALLBACKS}

# =========================
# Reasoner (modello) — usato in PHASE=B/C
# =========================
def _is_model_not_found(e) -> bool:
    msg = (getattr(e, "message", "") or str(e)).lower()
    return ("model_not_found" in msg) or ("does not exist" in msg and "model" in msg)

def call_model_reasoner(question: str, lang: str) -> Tuple[str, str]:
    if client is None:
        return ("", "disabled")
    system = f"{SYSTEM_KB}\n{SYSTEM_REASONER}"
    user = USER_WRAPPER.format(question=question.strip())
    last_err: Optional[Exception] = None
    for model in MODEL_FALLBACKS:
        try:
            resp = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )
            text = getattr(resp, "output_text", "").strip() or str(resp)
            return text, model
        except APIStatusError as e:  # type: ignore
            if getattr(e, "status_code", 400) == 400 and _is_model_not_found(e):
                continue
            raise HTTPException(status_code=502, detail=f"Errore OpenAI (status {getattr(e,'status_code',0)}) con modello '{model}': {getattr(e,'message',str(e))}") from e
        except (APIConnectionError, APITimeoutError, RateLimitError) as e:  # type: ignore
            last_err = e
            time.sleep(1.0)
        except Exception as e:
            last_err = e
            break
    raise HTTPException(status_code=504, detail=f"OpenAI non disponibile. Ultimo errore: {type(last_err).__name__}: {str(last_err)}")

# =========================
# Sanitizer + Guard-Rails
# =========================
MACHINE_RX = re.compile(r"\bspit\s*-?\s*p560\b", re.I)
SALD_RX = re.compile(r"\b(saldatur\w+|saldare|saldato)\b", re.I)
DIM_RX = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(mm|cm)\b", re.I)
DIA_RX = re.compile(r"[ØO]\s*\d+\b", re.I)
MODEL_RX = re.compile(r"\b\d{2}\s*/\s*\d{3}\b")  # es. 12/040

def sanitize_basic(text: str, query: str) -> str:
    out = (text or "").strip()
    out = MACHINE_RX.sub("SPIT P560", out)
    out = SALD_RX.sub("saldatura (non prevista per questo sistema)", out)
    out = re.sub(r"\b(Tecnaria)\s+\1\b", r"\1", out)
    out = re.sub(r"\s+\.", ".", out)
    out = re.sub(r"\s+,", ",", out)
    return out.strip()

def remove_unjustified_numbers(text: str, question: str) -> str:
    """Elimina o neutralizza mm/Ø/modelli NON menzionati nella domanda."""
    q = (question or "").lower()
    def keep_token(tok: str) -> bool:
        return tok.lower() in q
    out = text

    # mm/cm
    for m in list(DIM_RX.finditer(out)):
        tok = m.group(0)
        if not keep_token(tok):
            out = out.replace(tok, "valore da definire su elaborato")

    # Ø
    for m in list(DIA_RX.finditer(out)):
        tok = m.group(0)
        if not keep_token(tok):
            out = out.replace(tok, "Ø da definire su elaborato")

    # Modelli tipo 12/040
    for m in list(MODEL_RX.finditer(out)):
        tok = m.group(0)
        if not keep_token(tok):
            out = out.replace(tok, "modello da definire su elaborato")

    # Pulizia ripetizioni
    out = re.sub(r"(da definire su elaborato)(\s+\1)+", r"\1", out, flags=re.I)
    return out

def enforce_domain_clauses(question: str, answer: str) -> str:
    ql = (question or "").lower()
    out = answer

    # MAXI su legno/tavolato
    if ("maxi" in ql or "ctl maxi" in ql) and re.search(r"(tavolat|assito|perlinat)", ql):
        if "ancorar" not in out.lower() or "trave" not in out.lower():
            out += "\n\nVincolo chiave: le viti devono attraversare il tavolato e ancorare nella trave; fissaggi solo nel tavolato non sono ammessi."
        if "dwg" not in out.lower() and "elaborat" not in out.lower():
            out += "\nPer confermare modello e lunghezza viti: invia DWG/PDF con sezione trave, pacchetto e quota rete."
    # CTF + chiodatrice
    if ("ctf" in ql and ("chiodatrice" in ql or "sparo" in ql)):
        clause = ("Per garantire prestazioni ripetibili, è ammessa la SPIT P560 con 2 chiodi idonei per connettore, posa in gola; "
                  "alternative solo previa approvazione tecnica scritta di Tecnaria dopo prova di qualifica.")
        if "spit p560" not in out.lower():
            out += ("\n\n" + clause)
    # CTCEM + resine
    if ("ctcem" in ql and re.search(r"resin\w+", ql)):
        if "resine" not in out.lower() or "no" not in out.lower():
            out += "\n\nNota: i CTCEM non usano resine; fissaggio meccanico a secco con foratura e avvitamento a battuta piastra."

    return out.strip()

def guardrails(question: str, draft: str) -> str:
    out = sanitize_basic(draft, question)
    out = remove_unjustified_numbers(out, question)
    out = enforce_domain_clauses(question, out)
    # Compattezza
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out

# =========================
# Endpoint principale
# =========================
@app.post("/ask", response_model=AskOut)
def ask(inp: AskIn):
    q = (inp.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Manca 'question'.")
    lang = (inp.lang or DEFAULT_LANG).strip().lower()
    mode = (inp.mode or "auto").strip().lower()

    # 1) GOLD-first (tutte le fasi)
    golden = match_golden(q, lang)
    if golden:
        return JSONResponse({"ok": True, "answer": sanitize_basic(golden, q), "model": "golden", "mode": "compact"})

    # 2) PHASE=A → solo commerciale/gold default
    if PHASE == "A":
        return JSONResponse({"ok": True, "answer": sanitize_basic(GOLD_DEFAULT_A, q), "model": "golden-default", "mode": "compact"})

    # 3) PHASE=B/C → Reasoner (modello) + Guard-Rails
    if PHASE in ("B", "C"):
        draft, used = call_model_reasoner(q, lang)
        safe = guardrails(q, draft)
        return JSONResponse({"ok": True, "answer": safe, "model": used, "mode": "compact"})

    # Fallback estremo
    return JSONResponse({"ok": True, "answer": sanitize_basic(GOLD_DEFAULT_A, q), "model": "golden-default", "mode": "compact"})
