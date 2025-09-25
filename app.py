# app.py — Tecnaria Bot v3.1 (fix stringhe + GOLD + compatta/estesa)
# - UI su "/"
# - /ask: auto | compact | both
# - Golden Q&A bloccate (100% identiche)
# - P560-first opponibile; niente presence/frequency_penalty
# - Responses API con fallback modelli

import os, re, json, time
from typing import Optional, Tuple, List, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from openai import OpenAI
from openai._exceptions import APIConnectionError, APIStatusError, RateLimitError, APITimeoutError


# =========================
# Config
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata nelle Environment Variables.")

PREFERRED_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-4.1").strip()
MODEL_FALLBACKS: List[str] = []
for m in [PREFERRED_MODEL, "gpt-4o", "gpt-4.1", "gpt-4.1-mini"]:
    if m and m not in MODEL_FALLBACKS:
        MODEL_FALLBACKS.append(m)

DEFAULT_LANG = (os.getenv("DEFAULT_LANG") or "it").strip().lower()
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "1000"))

def attrezzatura_clause() -> str:
    return (
        "Per garantire prestazioni ripetibili, tracciabilità e qualità, è ammessa la chiodatrice SPIT P560; "
        "si usano chiodi idonei secondo istruzioni Tecnaria. Alternative solo previa approvazione tecnica scritta "
        "di Tecnaria a seguito di prova di qualifica in sito."
    )

# =========================
# Prompt esteso (niente triple-quoted: solo stringhe concatenate)
# =========================
SYSTEM_KB = (
    "DOMINIO TECNARIA — REGOLE BASE (CTF e posa su lamiera):\n"
    "• Attrezzatura: chiodatrice strutturale idonea (linea SPIT P560 nel perimetro Tecnaria).\n"
    "• Fissaggio: ogni CTF si ancora con 2 chiodi idonei secondo istruzioni Tecnaria.\n"
    "• Posizione di posa: connettore in gola lamiera, utensile perpendicolare, piastra in appoggio pieno.\n"
    "• Taratura: prove su provino/lamiera identica prima della produzione; registrazione lotti ed esiti.\n"
    "• Passo: da progetto (V_Ed) e capacità da documentazione ufficiale; mai numeri 'tipici' senza calcolo.\n"
    "• Lessico: NON parlare di 'saldatura' per CTF; è ancoraggio/chiodatura meccanica.\n"
    "• Varianti: solo con approvazione tecnica scritta di Tecnaria dopo qualifica in sito.\n"
    "• Per CTCEM/VCEM (laterocemento): sistemi meccanici a secco; no resine; foratura+avvitamento a battuta piastra (CTCEM).\n"
)

SYSTEM_PROMPT_EXT = (
    "Sei il BOT Tecnaria ufficiale (lingua: IT). Rispondi in modo opponibile, operativo e senza fronzoli.\n"
    "Segui strettamente le regole di dominio e NON inventare valori tabellari.\n\n"
    f"{SYSTEM_KB}\n"
    "FORMATTA così:\n"
    "A) BOT Tecnaria (scheda) → 4–10 bullet normativi, brevi e pronti per cantiere.\n"
    "B) Spiegazione (ingegneristica) → 1–2 paragrafi (motivazioni operative, taratura, controlli).\n"
    "C) Riferimenti → elenco sintetico (es. ETA/istruzioni Tecnaria).\n\n"
    "Tono: assertivo, preciso, senza disclaimer 'AI può sbagliare'.\n"
)

USER_WRAPPER_EXT = (
    "Domanda utente:\n"
    "{question}\n\n"
    "ISTRUZIONI DI OUTPUT:\n"
    "• Se il tema è posa/passo/QA → includi voci su attrezzatura (P560), 2 chiodi idonei, taratura, gola, interassi da progetto.\n"
    "• Evita parole vaghe ('tipico', 'indicativo', 'di massima'). Usa formulazioni opponibili.\n"
)

# =========================
# Golden Q&A (risposte bloccate 100% identiche)
# =========================
GoldenRule = Dict[str, Any]
GOLDEN_QA: List[GoldenRule] = [
    {
        "lang": "it",
        "patterns": [
            r"\bctf\b.*\bchiodatrice\b.*\bnormal\w+",
            r"\bchiodatrice\b.*\bsparo\b.*\bctf\b",
            r"\bsi\s*possono\b.*\bctf\b.*\bchiodatrice\b",
        ],
        "answer": (
            "No: non con una “normale” chiodatrice a sparo.\n"
            f"{attrezzatura_clause()}\n"
            "Ogni CTF va fissato con 2 chiodi idonei, utensile in asse, piastra in appoggio pieno, posa al centro della gola; "
            "prima della produzione: taratura su provino con registrazione lotti ed esiti. Deroghe: solo con approvazione tecnica scritta "
            "di Tecnaria dopo qualifica in sito."
        )
    },
    {
        "lang": "it",
        "patterns": [
            r"\bctcem\b.*\bresin\w+",
            r"\bresin\w+.*\bctcem\b",
            r"\bctcem\b.*\bpos[ao].*\bresin\w+",
            r"\bconnettori\b.*\bctcem\b.*\bresin\w+",
        ],
        "answer": (
            "No: i CTCEM non si posano con resine. Il fissaggio è meccanico a secco: si fora il travetto, si pulisce il foro "
            "e si avvita la vite fino a battuta della piastra dentata, secondo istruzioni CTCEM. Niente resine/malte/schiume; "
            "eventuali varianti richiedono approvazione tecnica scritta di Tecnaria."
        )
    }
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
app = FastAPI(title="Tecnaria Bot v3.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)
client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# UI minima su "/"
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
  <h1>🚀 Tecnaria Bot</h1>
  <p>Fai una domanda tecnica o commerciale e premi “Chiedi”.</p>
  <textarea id="q" placeholder="Es: Si possono posare i CTF con una 'normale' chiodatrice a sparo?"></textarea>
  <div class="row">
    <select id="mode">
      <option value="auto" selected>Auto</option>
      <option value="compact">Compatta</option>
      <option value="both">Scheda + Spiegazione</option>
    </select>
    <button onclick="ask()">Chiedi</button>
  </div>
  <div id="out" class="out"></div>
  <small>Endpoint: /ask • Modello: ENV OPENAI_MODEL con fallback • Max tokens: ENV MAX_OUTPUT_TOKENS</small>
</div>
<script>
async function ask(){
  const out=document.getElementById('out');
  const q=document.getElementById('q').value.trim();
  const mode=document.getElementById('mode').value;
  if(!q){ out.textContent="Inserisci una domanda."; return; }
  out.textContent="⏳...";
  try{
    const r=await fetch("/ask",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({question:q, mode})
    });
    const data=await r.json();
    if(data.ok){ out.textContent=data.answer; }
    else if(data.answer){ out.textContent=data.answer; }
    else { out.textContent="Errore: "+(data.detail||JSON.stringify(data)); }
  }catch(e){ out.textContent="Errore di rete: "+e.message; }
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
# Schemi I/O
# =========================
class AskIn(BaseModel):
    question: str
    mode: Optional[str] = "auto"   # auto | compact | both
    lang: Optional[str] = DEFAULT_LANG

class AskOut(BaseModel):
    ok: bool
    answer: str
    model: Optional[str] = None
    mode: Optional[str] = None

# =========================
# Helpers OpenAI
# =========================
def _is_model_not_found(e: APIStatusError) -> bool:
    msg = (getattr(e, "message", "") or str(e)).lower()
    return ("model_not_found" in msg) or ("does not exist" in msg and "model" in msg)

def call_model_ext(question: str, lang: str) -> Tuple[str, str]:
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
        except APIStatusError as e:
            if e.status_code == 400 and _is_model_not_found(e):
                continue
            raise HTTPException(
                status_code=502,
                detail=f"Errore OpenAI (status {e.status_code}) con modello '{model}': {getattr(e,'message',str(e))}"
            ) from e
        except (APIConnectionError, APITimeoutError, RateLimitError) as e:
            last_err = e
            time.sleep(1.2)
        except Exception as e:
            last_err = e
            break
    raise HTTPException(status_code=504, detail=f"OpenAI non disponibile. Ultimo errore: {type(last_err).__name__}: {str(last_err)}")

# =========================
# Post-processing minimo (anti-ripetizioni / terminologia)
# =========================
MACHINE_RX = re.compile(r"\bspit\s*-?\s*p560\b", re.I)
SOFT_RX = re.compile(r"\b(semplificat\w*|indicativ\w*|orientativ\w*|di\s*massima|tipic\w+)\b", re.I)
SALD_RX = re.compile(r"\b(saldatur\w+|saldare|saldato)\b", re.I)

def sanitize(text: str, query: str) -> str:
    out = text.strip()
    out = MACHINE_RX.sub("SPIT P560", out)
    if SOFT_RX.search(out):
        out = SOFT_RX.sub("operativo", out)
    out = SALD_RX.sub("saldatura (non prevista per questo sistema)", out)
    out = re.sub(r"\b(Tecnaria)\s+\1\b", r"\1", out)
    out = re.sub(r"\s+\.", ".", out)
    out = re.sub(r"\s+,", ",", out)
    if "ctcem" in query.lower():
        out = re.sub(r"inseriment[oa]\s+a\s+pressione|interferenza\s+meccanica",
                     "avvitamento fino a battuta della piastra (sistema meccanico a secco)",
                     out, flags=re.I)
    return out.strip()

# =========================
# Endpoint
# =========================
@app.get("/health", response_model=dict)
def health():
    return {"status": "ok", "model": PREFERRED_MODEL, "fallbacks": MODEL_FALLBACKS, "kb": True}

@app.post("/ask", response_model=AskOut)
def ask(inp: AskIn):
    q = (inp.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Manca 'question'.")
    lang = (inp.lang or DEFAULT_LANG).strip().lower()
    mode = (inp.mode or "auto").strip().lower()

    # 1) GOLDEN Q&A
    golden = match_golden(q, lang)
    if golden:
        return JSONResponse({"ok": True, "answer": golden, "model": "golden", "mode": "compact"})

    # 2) Scelta modalità
    yesno_simple = any([
        ("ctf" in q.lower() and "chiodatrice" in q.lower()),
        ("ctcem" in q.lower() and ("resin" in q.lower() or "resine" in q.lower())),
    ]) and len(q) < 200

    if mode == "compact" or (mode == "auto" and yesno_simple):
        if "ctf" in q.lower() and "chiodatrice" in q.lower():
            ans = (
                "No: non con una “normale” chiodatrice a sparo.\n"
                f"{attrezzatura_clause()}\n"
                "Ogni CTF va fissato con 2 chiodi idonei; utensile in asse, piastra in appoggio pieno, posa in gola; "
                "prima della produzione: taratura su provino e tracciabilità lotti."
            )
            return JSONResponse({"ok": True, "answer": sanitize(ans, q), "model": "compact", "mode": "compact"})
        if "ctcem" in q.lower() and ("resin" in q.lower() or "resine" in q.lower()):
            ans = (
                "No: i CTCEM non usano resine. Fissaggio meccanico a secco: foratura, pulizia foro e avvitamento della vite "
                "fino a battuta della piastra dentata secondo istruzioni CTCEM."
            )
            return JSONResponse({"ok": True, "answer": sanitize(ans, q), "model": "compact", "mode": "compact"})
        txt, used = call_model_ext(q, lang)
        m = re.search(r"A\)\s*BOT\s+Tecnaria.*?:\s*(.+?)(?:\n\s*[B]\)|\Z)", txt, flags=re.I|re.S)
        compact = m.group(1).strip() if m else txt.strip()
        return JSONResponse({"ok": True, "answer": sanitize(compact, q), "model": used, "mode": "compact"})

    # 3) Estesa (scheda + spiegazione + riferimenti)
    txt, used = call_model_ext(q, lang)
    return JSONResponse({"ok": True, "answer": sanitize(txt, q), "model": used, "mode": "both"})

# ---- Start command consigliato (Render Runtime: Python) ----
# gunicorn -k uvicorn.workers.UvicornWorker -w 1 --timeout 180 -b 0.0.0.0:$PORT app:app
