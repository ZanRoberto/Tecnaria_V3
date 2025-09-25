# app.py — Tecnaria Bot v3.2 (PHASE=A lock per risposte commerciali GOLD)
# - PHASE=A (default): SOLO risposte commerciali Gold, compatte, senza modello
# - PHASE=B: attiva modello Responses API (scheda + spiegazione) con fallback
# - Golden Q&A estese (preventivo/export, fasi ordine, CTF chiodatrice, CTCEM resine)
# - UI semplice su "/"
# - Start command (Render): gunicorn -k uvicorn.workers.UvicornWorker -w 1 --timeout 180 -b 0.0.0.0:$PORT app:app

import os, re, json, time
from typing import Optional, Tuple, List, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

# Attiva OpenAI SOLO in PHASE=B
PHASE = (os.getenv("PHASE") or "A").strip().upper()

if PHASE == "B":
    from openai import OpenAI
    from openai._exceptions import APIConnectionError, APIStatusError, RateLimitError, APITimeoutError
else:
    OpenAI = None
    APIConnectionError = APIStatusError = RateLimitError = APITimeoutError = Exception  # placeholders

# =========================
# Config comuni
# =========================
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
# Prompt (usati solo in PHASE=B). Nessuna triple-quoted string.
# =========================
SYSTEM_KB = (
    "DOMINIO TECNARIA — REGOLE BASE:\n"
    "• Fase A: solo commerciale (preventivo, export, documenti). No dettagli di posa.\n"
    "• Fase B: tecnica (posa, taratura, interassi): attenersi a istruzioni/ETA.\n"
    "• CTF: posa con chiodatrice idonea; P560 nel perimetro commerciale Tecnaria; 2 chiodi idonei; in gola.\n"
    "• CTCEM/VCEM: sistemi meccanici a secco; no resine; foratura+avvitamento a battuta piastra (CTCEM).\n"
    "• Documenti export: ETA, DoP, CE, packing list, famiglia HS code, fattura commerciale con Incoterms.\n"
)

SYSTEM_PROMPT_EXT = (
    "Sei il BOT Tecnaria ufficiale (IT). Rispondi in modo opponibile e sintetico.\n"
    f"{SYSTEM_KB}\n"
    "Formato esteso quando richiesto: A) Scheda (bullet), B) Spiegazione breve, C) Riferimenti.\n"
    "Evita numeri non documentati e dettagli di posa se la domanda è commerciale.\n"
)

USER_WRAPPER_EXT = (
    "Domanda utente:\n"
    "{question}\n\n"
    "Istruzioni: se è commerciale (preventivo/export/fasi), privilegia scheda concisa.\n"
    "Se è tecnica (PHASE=B), inserisci solo regole opponibili, niente numeri inventati.\n"
)

# =========================
# Golden Q&A — risposte bloccate (fase A)
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
    {   # Preventivo + export
        "lang": "it",
        "patterns": [
            r"\bpreventiv\w+\b.*\bexport\b",
            r"\bpreventiv\w+\b.*\bdocument\w+\b",
            r"\bofferta\b.*\bexport\b",
        ],
        "answer": GOLD_PREVENTIVO_EXPORT
    },
    {   # Fasi dall'offerta alla spedizione
        "lang": "it",
        "patterns": [
            r"\bfasi\b.*\bdall'?offerta\b.*\bspedizion\w+",
            r"\bdistributor\w*\b.*\bestero\b.*\bfasi\b.*\bspedizion\w+",
            r"\bordine\b.*\bexport\b.*\bfasi\b",
        ],
        "answer": GOLD_FASI_ORDINE
    },
    {   # CTF + chiodatrice “normale”
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
app = FastAPI(title="Tecnaria Bot v3.2 — Phase Lock")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

client = OpenAI(api_key=OPENAI_API_KEY) if (PHASE == "B" and OPENAI_API_KEY) else None

# =========================
# UI
# =========================
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
  <p>Fai una domanda commerciale (preventivo, export, fasi ordine) e premi “Chiedi”.</p>
  <textarea id="q" placeholder="Es: Cosa serve per il preventivo export?"></textarea>
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

# =========================
# Schemi I/O
# =========================
class AskIn(BaseModel):
    question: str
    mode: Optional[str] = "auto"
    lang: Optional[str] = DEFAULT_LANG

class AskOut(BaseModel):
    ok: bool
    answer: str
    model: Optional[str] = None
    mode: Optional[str] = None

# =========================
# Helpers (PHASE=B)
# =========================
def _is_model_not_found(e) -> bool:
    msg = (getattr(e, "message", "") or str(e)).lower()
    return ("model_not_found" in msg) or ("does not exist" in msg and "model" in msg)

def call_model_ext(question: str, lang: str) -> Tuple[str, str]:
    if PHASE != "B" or client is None:
        return ("", "disabled")
    system_content = SYSTEM_PROMPT_EXT
    user_content = USER_WRAPPER_EXT.format(question=question.strip())
    last_err: Optional[Exception] = None
    for model in MODEL_FALLBACKS:
        try:
            resp = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_content},
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
            time.sleep(1.2)
        except Exception as e:
            last_err = e
            break
    raise HTTPException(status_code=504, detail=f"OpenAI non disponibile. Ultimo errore: {type(last_err).__name__}: {str(last_err)}")

# =========================
# Sanitizer leggero
# =========================
MACHINE_RX = re.compile(r"\bspit\s*-?\s*p560\b", re.I)
SOFT_RX = re.compile(r"\b(semplificat\w*|indicativ\w*|orientativ\w*|di\s*massima|tipic\w+)\b", re.I)
SALD_RX = re.compile(r"\b(saldatur\w+|saldare|saldato)\b", re.I)

def sanitize(text: str, query: str) -> str:
    out = (text or "").strip()
    out = MACHINE_RX.sub("SPIT P560", out)
    if SOFT_RX.search(out):
        out = SOFT_RX.sub("operativo", out)
    out = SALD_RX.sub("saldatura (non prevista per questo sistema)", out)
    out = re.sub(r"\b(Tecnaria)\s+\1\b", r"\1", out)
    out = re.sub(r"\s+\.", ".", out)
    out = re.sub(r"\s+,", ",", out)
    if "ctcem" in (query or "").lower():
        out = re.sub(r"inseriment[oa]\s+a\s+pressione|interferenza\s+meccanica",
                     "avvitamento fino a battuta della piastra (sistema meccanico a secco)",
                     out, flags=re.I)
    return out.strip()

# =========================
# Endpoint
# =========================
@app.get("/health", response_model=dict)
def health():
    return {"status": "ok", "phase": PHASE, "model": PREFERRED_MODEL, "fallbacks": MODEL_FALLBACKS}

@app.post("/ask", response_model=AskOut)
def ask(inp: AskIn):
    q = (inp.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Manca 'question'.")
    lang = (inp.lang or DEFAULT_LANG).strip().lower()
    mode = (inp.mode or "auto").strip().lower()

    # PHASE=A → SOLO GOLD/COMMERCIALE, NIENTE MODELLO
    if PHASE == "A":
        golden = match_golden(q, lang)
        if golden:
            return JSONResponse({"ok": True, "answer": sanitize(golden, q), "model": "golden", "mode": "compact"})
        # fallback commerciale generico, sicuro
        return JSONResponse({"ok": True, "answer": sanitize(GOLD_DEFAULT_A, q), "model": "golden-default", "mode": "compact"})

    # PHASE=B → come v3.1 (modello + compact/estesa)
    # try golden prima comunque
    golden = match_golden(q, lang)
    if golden and (("ctf" in q.lower() and "chiodatrice" in q.lower()) or ("ctcem" in q.lower() and ("resin" in q.lower() or "resine" in q.lower()))):
        return JSONResponse({"ok": True, "answer": sanitize(golden, q), "model": "golden", "mode": "compact"})

    yesno_simple = any([
        ("ctf" in q.lower() and "chiodatrice" in q.lower()),
        ("ctcem" in q.lower() and ("resin" in q.lower() or "resine" in q.lower())),
    ]) and len(q) < 200

    if mode == "compact" or (mode == "auto" and yesno_simple):
        if golden:
            return JSONResponse({"ok": True, "answer": sanitize(golden, q), "model": "golden", "mode": "compact"})
        txt, used = call_model_ext(q, lang)
        m = re.search(r"A\)\s*BOT\s+Tecnaria.*?:\s*(.+?)(?:\n\s*[B]\)|\Z)", txt, flags=re.I|re.S)
        compact = m.group(1).strip() if m else txt.strip()
        return JSONResponse({"ok": True, "answer": sanitize(compact, q), "model": used, "mode": "compact"})

    txt, used = call_model_ext(q, lang)
    return JSONResponse({"ok": True, "answer": sanitize(txt, q), "model": used, "mode": "both"})

# ---- Start command (Render Runtime: Python) ----
# gunicorn -k uvicorn.workers.UvicornWorker -w 1 --timeout 180 -b 0.0.0.0:$PORT app:app
