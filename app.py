# app.py — Tecnaria Bot (senza RAG) • versione “allineata 97–98%”
# - UI su "/"
# - Endpoint /ask (OpenAI Responses API) con fallback modelli
# - Determinismo (temperature=0.1)
# - POLICY_MODE: P560_ONLY (rigida) | EQUIV_ALLOWED (porta di servizio)
# - Guard-rails: no codici SPIT, no HS numerici, no “saldatura”, no “passi tipici”,
#                no disclaimer "AI/posso sbagliare", no parole morbide
# - Fraseario canonico per domande ricorrenti (es. “normale chiodatrice a sparo”)
# - Template/heading auto quando la domanda chiede passo/posa/QA

import os, time, re
from typing import Optional, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from openai import OpenAI
from openai._exceptions import APIConnectionError, APIStatusError, RateLimitError, APITimeoutError

# =========================
# Config base
# =========================
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata nelle Environment Variables.")

PREFERRED_MODEL = (os.environ.get("MODEL_NAME") or "gpt-4.1").strip()
MODEL_FALLBACKS = []
for m in [PREFERRED_MODEL, "gpt-4o", "gpt-4.1", "gpt-4.1-mini"]:
    if m and m not in MODEL_FALLBACKS:
        MODEL_FALLBACKS.append(m)

DEFAULT_LANG = (os.environ.get("DEFAULT_LANG") or "it").strip().lower()
MAX_OUTPUT_TOKENS = int(os.environ.get("MAX_OUTPUT_TOKENS", "1200"))

# Linea commerciale/tecnica: rigida P560-only o equivalente approvato
POLICY_MODE = (os.environ.get("POLICY_MODE") or "P560_ONLY").strip().upper()
# valori ammessi: "P560_ONLY" oppure "EQUIV_ALLOWED"

def _attrezzatura_line() -> str:
    if POLICY_MODE == "P560_ONLY":
        return ("Per garantire prestazioni ripetibili, tracciabilità e qualità, è ammessa esclusivamente la "
                "chiodatrice SPIT P560 con chiodi idonei secondo istruzioni Tecnaria. Alternative solo previa "
                "approvazione tecnica scritta di Tecnaria a seguito di prova di qualifica in sito.")
    # EQUIV_ALLOWED
    return ("Chiodatrice strutturale idonea (es. SPIT P560) oppure sistema equivalente approvato da Tecnaria; "
            "sempre con chiodi idonei secondo istruzioni Tecnaria e 2 chiodi per connettore; alternative previa "
            "approvazione tecnica scritta di Tecnaria dopo prova di qualifica in sito.")

# =========================
# Prompt TECNARIA + Template
# =========================
PROMPT_BASE = (
    "SEI: tecnico/commerciale TECNARIA S.p.A. (Bassano del Grappa).\n"
    "OBIETTIVO: risposta pronta per cliente, chiara, prudente, operativa.\n"
    "TONO: assertivo ma verificabile; nessuna frase di auto-scarico (es. 'come AI posso sbagliare').\n"
    "POLICY:\n"
    "• Connettori CTF su lamiera: fissaggio = ancoraggio/chiodatura (NON saldatura). 2 chiodi idonei P560 secondo istruzioni Tecnaria.\n"
    "• HS code: indica solo famiglia 73 (strutture in ferro/acciaio); codice numerico da validare con spedizioniere/dogana.\n"
    "• Passo/interassi: derivano da V_Ed e PRd tabellata (profilo lamiera, cls, direzione). Vietati numeri 'tipici' senza calcolo.\n"
    "• Evita termini vaghi: 'semplificato', 'indicativo', 'di massima', 'orientativo', 'tipicamente'.\n"
    "• Niente link generici e niente codici chiodi/marche se non esplicitamente richiesti dalle istruzioni Tecnaria.\n"
    "• Se mancano dati, dichiara ASSUNZIONI brevi senza fare domande al cliente.\n"
    "• Linea attrezzatura: {linea_attrezzatura}\n"
)

STRICT_TEMPLATE = (
    "FORMATTA COSÌ (mantieni questi titoli quando si parla di posa/passo/QA):\n"
    "1) Scenario e assunzioni (brevi)\n"
    "2) Verifiche geometriche (copriferro, immersione testa, compatibilità lamiera)\n"
    "3) Verifiche prestazionali (come leggere PRd: profilo, passo gola, cls, direzione; criterio ‘più bassa che verifica’ per 075/090)\n"
    "4) Posa e fissaggio (P560 + 2 chiodi idonei; taratura su provino; allineamento in gola)\n"
    "5) Passo connettori (criterio ΣPRd ≥ V_Ed + interassi min/max)\n"
    "6) QA di cantiere (checklist breve)\n"
    "7) Errori da evitare\n"
    "8) Sintesi decisionale (max 5 bullet)\n"
)

# Fraseario canonico per domande ricorrenti
CANONICAL = {
  "no_chiodatrice_normale": (
    "No: non con una 'normale' chiodatrice a sparo. "
    "Per la posa dei connettori CTF Tecnaria è ammessa {clausola_attrezzatura}. "
    "Ogni CTF richiede 2 chiodi idonei; utensile perpendicolare, piastra in appoggio pieno, posa al centro della gola; "
    "prima della produzione: taratura su provino con registrazione lotti ed esiti."
  )
}

client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# FastAPI
# =========================
app = FastAPI(title="Tecnaria Bot", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# =========================
# UI su "/"
# =========================
HOME_HTML = """<!doctype html>
<meta charset="utf-8" />
<title>Tecnaria Bot</title>
<style>
  :root { --fg:#111; --muted:#666; --bd:#e5e7eb; --bg:#fff; }
  html,body{background:var(--bg); color:var(--fg); font:16px system-ui, Arial; margin:0; padding:0}
  .wrap{max-width:920px; margin:40px auto; padding:0 20px}
  h1{font-size:22px; margin:0 0 6px}
  p.m{color:var(--muted); margin:0 0 16px}
  textarea{width:100%; height:150px; padding:12px; border:1px solid var(--bd); border-radius:12px; box-sizing:border-box; font:16px/1.3 system-ui, Arial}
  .row{display:flex; gap:12px; align-items:center; margin:10px 0 0}
  select,button{padding:10px; border:1px solid var(--bd); border-radius:10px}
  button{background:#f8f9fb; cursor:pointer}
  button:active{transform:translateY(1px)}
  .out{white-space:pre-wrap; border:1px solid var(--bd); border-radius:12px; padding:12px; margin-top:14px; min-height:80px}
  .small{font-size:12px; color:var(--muted); margin-top:8px}
</style>
<div class="wrap">
  <h1>🚀 Tecnaria Bot</h1>
  <p class="m">Fai una domanda tecnica o commerciale e premi “Chiedi”.</p>

  <label style="font-weight:600">Domanda</label>
  <textarea id="q" placeholder="Es: CTF e chiodatrice: si può usare una 'normale' a sparo?"></textarea>

  <div class="row">
    <select id="lang">
      <option value="it" selected>Italiano</option>
      <option value="en">English</option>
      <option value="fr">Français</option>
      <option value="de">Deutsch</option>
      <option value="es">Español</option>
    </select>
    <button onclick="ask()">Chiedi</button>
  </div>

  <div id="out" class="out"></div>
  <div class="small">Endpoint: <code>/ask</code> • Modello: <code>ENV MODEL_NAME / fallback</code> • Policy: <code>P560_ONLY o EQUIV_ALLOWED</code></div>
</div>
<script>
async function ask(){
  const out = document.getElementById('out');
  const q   = document.getElementById('q').value.trim();
  const lang= document.getElementById('lang').value;
  if(!q){ out.textContent = "Inserisci una domanda."; return; }
  out.textContent = "⏳ Invio...";
  try{
    const res = await fetch("/ask", {
      method:"POST",
      headers:{ "Content-Type":"application/json" },
      body: JSON.stringify({ question: q, lang: lang })
    });
    const data = await res.json();
    if(data.ok){ out.textContent = data.answer; }
    else{ out.textContent = "Errore: " + (data.detail || JSON.stringify(data)); }
  }catch(e){
    out.textContent = "Errore di rete: " + e.message;
  }
}
</script>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(HOME_HTML)

@app.get("/favicon.ico")
def favicon():
    return PlainTextResponse("", status_code=204)

# =========================
# Schemi
# =========================
class AskPayload(BaseModel):
    question: str
    lang: Optional[str] = DEFAULT_LANG

class AskResponse(BaseModel):
    ok: bool
    answer: str
    model: Optional[str] = None

# =========================
# OpenAI helpers
# =========================
def _is_model_not_found(e: APIStatusError) -> bool:
    msg = (getattr(e, "message", "") or str(e)).lower()
    return ("model_not_found" in msg) or ("does not exist" in msg and "model" in msg)

def _call_with_model(model: str, full_input):
    resp = client.responses.create(
        model=model,
        input=full_input,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.1,   # quasi deterministico
        top_p=1,
        presence_penalty=0,
        frequency_penalty=0
    )
    if getattr(resp, "output_text", None):
        return resp.output_text.strip()
    items = getattr(resp, "output", None) or []
    chunks = []
    for it in items:
        content = getattr(it, "content", None) or []
        for c in content:
            if getattr(c, "type", "") == "output_text":
                chunks.append(getattr(c, "text", ""))
    return ("\n".join([c for c in chunks if c]).strip()) or str(resp)

def _call_responses(prompt: str, lang: str) -> Tuple[str, str]:
    """
    Tenta i modelli in fallback. Ritorna (answer, model_used).
    """
    user_content = (prompt if lang == "it" else f"[Rispondi in {lang}] {prompt}")
    system_content = PROMPT_BASE.format(linea_attrezzatura=_attrezzatura_line())
    full_input = [
        {"role": "system", "content": system_content + "\n" + STRICT_TEMPLATE},
        {"role": "user",   "content": user_content},
    ]
    last_err = None
    for model in MODEL_FALLBACKS:
        if not model:
            continue
        for attempt in range(1, 3+1):
            try:
                ans = _call_with_model(model, full_input)
                return ans, model
            except APIStatusError as e:
                if e.status_code == 400 and _is_model_not_found(e):
                    break  # prova prossimo modello
                raise HTTPException(
                    status_code=502,
                    detail=f"Errore OpenAI (status {e.status_code}) con modello '{model}': {getattr(e, 'message', str(e))}"
                ) from e
            except (APIConnectionError, APITimeoutError, RateLimitError) as e:
                last_err = e
                time.sleep(1.2 * attempt)
            except Exception as e:
                last_err = e
                break
    raise HTTPException(
        status_code=504,
        detail=f"Impossibile contattare OpenAI o modelli non disponibili. Ultimo errore: {type(last_err).__name__}: {str(last_err)}"
    )

# =========================
# Prefissi/struttura
# =========================
def _maybe_preface(answer: str, question: str) -> str:
    ql = question.lower()
    if ("chiodatrice" in ql and "spar" in ql and "ctf" in ql):
        clausola = _attrezzatura_line()
        pref = CANONICAL["no_chiodatrice_normale"].format(clausola_attrezzatura=clausola)
        if pref not in answer:
            return pref + "\n\n" + answer
    return answer

def _ensure_headings(ans: str, question: str) -> str:
    ql = question.lower()
    need = ["Scenario e assunzioni", "Verifiche geometriche", "Verifiche prestazionali",
            "Posa e fissaggio", "Passo connettori", "QA di cantiere", "Errori da evitare", "Sintesi decisionale"]
    trigger = any(k in ql for k in ["passo", "interasse", "posa", "fissaggio", "qa", "qualità", "controlli", "verifica"])
    if trigger:
        missing = [h for h in need if h.lower() not in ans.lower()]
        if missing:
            ans += "\n\n" + "\n".join(f"**{h}**:" for h in missing)
    return ans

# =========================
# Post-processing (guard-rails)
# =========================
_SPIT_CODE_RX = re.compile(r"\b(spit\s*[-_]?\s*[a-z]*\d+|enk\d+|hsbr\d+)\b", re.I)
_HS_EXACT_RX  = re.compile(r"\bHS\s*code\s*[:\-]?\s*\d{4,10}\b", re.I)
_HS_PURE_RX   = re.compile(r"\b\d{6,10}\b")
_SALD_RX      = re.compile(r"\bsald\w+\b", re.I)   # saldatura/saldare…
_TIPICO_RX    = re.compile(r"\b(tipic[oi]|di\s*norma|solitamente|in\sgenerale)\b", re.I)
_PULLOUT_RX   = re.compile(r"\bpull[-\s]?out\b", re.I)
_DISCLAIMER_RX= re.compile(r"(chatgpt|as a language model|posso sbagliare|potrei sbagliare|\bla\s*ia\b|\bai\b)", re.I)
_BANNED_SOFT_RX = re.compile(r"\b(semplificat\w*|indicativ\w*|orientativ\w*|di\s*massima)\b", re.I)

def _sanitize_answer(text: str, query: str) -> str:
    out = text

    # 1) Vietati codici chiodi → dicitura ufficiale
    if _SPIT_CODE_RX.search(out):
        out = _SPIT_CODE_RX.sub("chiodi idonei P560 secondo istruzioni Tecnaria", out)

    # 2) HS code: no numeri → famiglia 73 + validazione
    if ("hs" in query.lower() or "incoterm" in query.lower() or "export" in query.lower()
        or _HS_EXACT_RX.search(out) or "HS code" in out):
        out = _HS_EXACT_RX.sub(
            "HS code: famiglia 73 (strutture in ferro/acciaio) — validare con spedizioniere/dogana", out
        )
        out = _HS_PURE_RX.sub(lambda m: "XXXX", out)

    # 3) Terminologia “saldatura” → correggi
    if _SALD_RX.search(out):
        out = _SALD_RX.sub("ancoraggio/chiodatura", out)

    # 4) “passi tipici” → vincola a V_Ed/PRd
    if _TIPICO_RX.search(out) and ("V_Ed" not in out and "PRd" not in out):
        out += ("\n\nNota: il passo/interasse NON è tipico; si determina da V_Ed e PRd tabellata "
                "(profilo lamiera, cls, direzione) rispettando gli interassi min/max.")

    # 5) “pull-out” → rimanda alla taratura su provino, salvo capitolato
    if _PULLOUT_RX.search(out) and "capitolato" not in query.lower():
        out = _PULLOUT_RX.sub(
            "taratura su provino a inizio turno (prove aggiuntive solo se richieste da capitolato)", out
        )

    # 6) Disclaimers stile "AI/posso sbagliare" -> rimuovi
    if _DISCLAIMER_RX.search(out):
        out = _DISCLAIMER_RX.sub("", out)

    # 7) Parole morbide -> sostituisci
    if _BANNED_SOFT_RX.search(out):
        out = _BANNED_SOFT_RX.sub("operativo", out)

    return out.strip()

# =========================
# Endpoint /ask
# =========================
@app.post("/ask", response_model=AskResponse)
def ask(payload: AskPayload):
    q = (payload.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="question mancante.")
    lang = (payload.lang or DEFAULT_LANG).strip().lower()

    answer, model_used = _call_responses(q, lang)
    answer = _maybe_preface(answer, q)
    answer = _ensure_headings(answer, q)
    answer = _sanitize_answer(answer, q)

    return JSONResponse({"ok": True, "answer": answer, "model": model_used})
