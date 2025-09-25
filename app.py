# app.py — Tecnaria Bot API (standalone, no SINAPSI)
# - FastAPI + OpenAI Responses API
# - Dominio ristretto a Tecnaria (hard-guard)
# - Stile "telefono" forzato via system prompt
# - Shortcut: per domande ricorrenti note (CTF+chiodatrice, CTCEM+resine, MAXI+tavolato, "modalità di posa")
#   risponde con schede pre-formattate senza chiamare il modello (coerenza massima)
# - Endpoint: GET /health, POST /ask  (body: {"question":"...", "lang":"it"})

import os, re
from typing import List
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

APP_NAME = "Tecnaria Bot API"
MODEL_NAME = (os.getenv("MODEL_NAME") or "gpt-4.1").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or ""

if not OPENAI_API_KEY:
    # Non blocchiamo l'avvio: l'errore verrà gestito al primo /ask
    pass

client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI(title=APP_NAME)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)

# --------------------------- Hard-guard dominio Tecnaria ---------------------------
TEC_KEYWORDS = [
    "tecnaria","ctf","ctcem","ctl","maxi","lamiera","grecata","laterocemento",
    "connettore","connettori","p560","spit","eta","dop","marcatura ce",
    "posa","interasse","incoterms","packing list","hs code","soletta","tavolato","assito","trave"
]
_dom_rx = re.compile("|".join([re.escape(k) for k in TEC_KEYWORDS]), re.I)

def in_tecnaria_scope(q: str) -> bool:
    return bool(q and _dom_rx.search(q))

# --------------------------- Riconoscimento casi ricorrenti ------------------------
_RX = {
    "CTF_CHIODATRICE": re.compile(r"\bctf\b.*\b(chiodatrice|sparo)\b|\b(chiodatrice|sparo)\b.*\bctf\b", re.I),
    "CTCEM_RESINE":    re.compile(r"\bctcem\b.*\bresin\w+\b", re.I),
    "MAXI_TAVOLATO":   re.compile(r"\b(ctl\s*maxi|maxi)\b.*\b(tavolat|assito|legno)\b", re.I),
    "POSA_GENERIC":    re.compile(r"\b(modalit[aà]|modalita|modo|come)\b.*\b(posa|installaz|montagg)\w*\b", re.I),
    "POSA_CTF":        re.compile(r"\bctf\b.*\b(posa|installaz|montagg)\w*\b|\b(posa|installaz|montagg)\w*\b.*\bctf\b", re.I),
    "POSA_CTCEM":      re.compile(r"\bctcem\b.*\b(posa|installaz|montagg)\w*\b|\b(posa|installaz|montagg)\w*\b.*\bctcem\b", re.I),
    "POSA_MAXI":       re.compile(r"\b(ctl\s*maxi|maxi)\b.*\b(posa|installaz|montagg)\w*\b|\b(posa|installaz|montagg)\w*\b.*\b(ctl\s*maxi|maxi)\b", re.I),
}

def _bold(s: str) -> str:
    return f"**{s}**"

def _pack(head: str, bullets: List[str], note: str) -> str:
    bl = "\n".join([f"- {b}" for b in bullets])
    return f"{head}\n\n{bl}\n\n{note}"

# --------------------------- Schede pre-formattate (no LLM) -----------------------
def sheet_ctf_chiodatrice() -> str:
    head = "Sì, ma **non** con una chiodatrice qualsiasi."
    bullets = [
        f"usa {_bold('SPIT P560')} con {_bold('2 chiodi')} per connettore; kit/adattatori dedicati.",
        "posa **in gola**, utensile perpendicolare, piastra in appoggio pieno.",
        "esegui **taratura** su provino identico prima della produzione.",
        "varianti solo previa **approvazione Tecnaria** (qualifica in sito).",
    ]
    note = "Nota: vedi **Istruzioni di posa CTF**."
    return _pack(head, bullets, note)

def sheet_ctcem_resine() -> str:
    head = "No: **niente resine**."
    bullets = [
        "**fissaggio a secco** (meccanico).",
        "**foratura → pulizia foro → avvitamento a battuta piastra**.",
        "varianti solo previa **approvazione Tecnaria**.",
    ]
    note = "Nota: vedi **Istruzioni di posa CTCEM**."
    return _pack(head, bullets, note)

def sheet_maxi_tavolato() -> str:
    head = "Sì: **CTL MAXI** per posa su tavolato (modello/lunghezze da confermare su elaborati)."
    bullets = [
        "**viti** che **attraversano il tavolato e ancorano nella trave** (non solo nel tavolato).",
        "testa **sopra la rete** e **sotto** il filo superiore del getto.",
        "modello/altezza e lunghezza viti **si confermano su DWG/PDF**.",
    ]
    note = "Nota: vedi **Istruzioni CTL MAXI / particolari costruttivi**."
    return _pack(head, bullets, note)

def sheet_posa_ctf() -> str:
    head = "**CTF (acciaio + lamiera grecata)**"
    bullets = [
        "posa **in gola** della lamiera; utensile **perpendicolare**, piastra in appoggio pieno",
        f"chiodatrice {_bold('SPIT P560')} con {_bold('2 chiodi')} per connettore",
        "eseguire **taratura** su provino identico prima della produzione",
        "varianti solo previa **approvazione Tecnaria**",
    ]
    note = "**Nota**: Istruzioni di posa **CTF**"
    return _pack(head, bullets, note)

def sheet_posa_ctcem() -> str:
    head = "**CTCEM (laterocemento)**"
    bullets = [
        "**a secco**: **foratura → pulizia foro → avvitamento a battuta piastra**",
        "**no resine**; varianti solo se approvate da Tecnaria",
        "confermare interassi e quantità su **DWG/PDF**",
    ]
    note = "**Nota**: Istruzioni di posa **CTCEM**"
    return _pack(head, bullets, note)

def sheet_posa_maxi() -> str:
    head = "**CTL MAXI (legno / tavolato)**"
    bullets = [
        "**viti** che **attraversano il tavolato e ancorano nella trave** (non solo nel tavolato)",
        "testa **sopra la rete** e **sotto** il filo superiore del getto",
        "modello e lunghezza viti **si confermano su DWG/PDF**",
    ]
    note = "**Nota**: Istruzioni **CTL MAXI / particolari costruttivi**"
    return _pack(head, bullets, note)

def sheet_posa_panorama() -> str:
    return "\n".join([sheet_posa_ctf(), "", sheet_posa_ctcem(), "", sheet_posa_maxi()])

# --------------------------- System prompt (stile “telefono”) ----------------------
def build_system_prompt(lang: str = "it") -> str:
    lang = (lang or "it").strip().lower()
    return "\n".join([
        "Ruolo: assistente Tecnaria. Rispondi SOLO su Tecnaria (CTF, CTCEM, CTL MAXI, posa, documenti, export).",
        "Stile 'telefono':",
        " - Apri con verdetto secco **Sì/No + motivo breve** in UNA riga.",
        " - Poi 3–5 bullet telegrafici con parole chiave in **grassetto**.",
        " - Chiudi con **Nota** (Istruzioni Tecnaria o richiesta DWG/PDF).",
        "Limiti:",
        " - Non inventare **mm/Ø/modelli** non presenti nella domanda.",
        " - Se mancano dati, usa: 'si conferma su DWG/PDF'.",
        " - Evita disclaimer o riferimenti all'AI.",
        "Contenuti cardine:",
        " - CTF: **SPIT P560 + 2 chiodi**, **posa in gola**, **taratura** su provino; varianti solo con approvazione Tecnaria.",
        " - CTCEM: **no resine**; **foratura → pulizia foro → avvitamento a battuta piastra**.",
        " - CTL MAXI: **viti attraversano tavolato e ancorano nella trave**; testa **sopra rete/sotto filo getto**; modello/lunghezze su **DWG/PDF**.",
        f"Lingua: usa la lingua della domanda (default: {lang})."
    ])

# --------------------------- OpenAI Responses API wrapper -------------------------
def call_openai(system_prompt: str, user_text: str) -> str:
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY non configurata.")
    try:
        resp = client.responses.create(
            model=MODEL_NAME,
            input=[
                {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
                {"role": "user",   "content": [{"type": "text", "text": user_text}]}
            ]
        )
        # output_text nelle versioni recenti
        try:
            return (resp.output_text or "").strip()
        except Exception:
            # Fallback robusto
            if hasattr(resp, "output") and resp.output:
                first = resp.output[0]
                content = getattr(first, "content", None) or []
                if content and hasattr(content[0], "text"):
                    return (content[0].text or "").strip()
            return str(resp)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OpenAI error: {e}")

# ----------------------------------- Routes --------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{APP_NAME}</title></head>
<body style="font-family:system-ui,Arial,sans-serif;padding:24px;line-height:1.5">
<h1>{APP_NAME}</h1>
<p>Endpoint:</p>
<ul>
<li><code>GET /health</code></li>
<li><code>POST /ask</code> — body: <code>{{"question": "...", "lang": "it"}}</code></li>
</ul>
</body></html>"""

@app.get("/health")
def health():
    return {"ok": True, "model": MODEL_NAME}

@app.post("/ask")
async def ask(req: Request):
    try:
        data = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON invalido.")

    q = (data.get("question") or "").strip()
    lang = (data.get("lang") or "it").strip().lower()

    if not q:
        raise HTTPException(status_code=400, detail="Campo 'question' mancante o vuoto.")

    # Hard-guard dominio Tecnaria
    if not in_tecnaria_scope(q):
        msg = ("Rispondo solo su argomenti **Tecnaria** (CTF, CTCEM, CTL MAXI, posa, "
               "documentazione, export). Per favore riformula la domanda in questo perimetro.")
        return JSONResponse({"ok": True, "answer": msg, "model": "guard", "mode": "scope"})

    # Shortcut per i casi ricorrenti (risposte “a scheda”, zero varianza)
    if _RX["CTF_CHIODATRICE"].search(q):
        return JSONResponse({"ok": True, "answer": sheet_ctf_chiodatrice(), "model": "style", "mode": "sheet"})
    if _RX["CTCEM_RESINE"].search(q):
        return JSONResponse({"ok": True, "answer": sheet_ctcem_resine(), "model": "style", "mode": "sheet"})
    if _RX["MAXI_TAVOLATO"].search(q):
        return JSONResponse({"ok": True, "answer": sheet_maxi_tavolato(), "model": "style", "mode": "sheet"})
    if _RX["POSA_CTF"].search(q):
        return JSONResponse({"ok": True, "answer": sheet_posa_ctf(), "model": "style", "mode": "sheet"})
    if _RX["POSA_CTCEM"].search(q):
        return JSONResponse({"ok": True, "answer": sheet_posa_ctcem(), "model": "style", "mode": "sheet"})
    if _RX["POSA_MAXI"].search(q):
        return JSONResponse({"ok": True, "answer": sheet_posa_maxi(), "model": "style", "mode": "sheet"})
    if _RX["POSA_GENERIC"].search(q):
        return JSONResponse({"ok": True, "answer": sheet_posa_panorama(), "model": "style", "mode": "sheet"})

    # Tutto il resto passa al modello, con stile imposto dal system prompt
    system_prompt = build_system_prompt(lang=lang)
    answer = call_openai(system_prompt, q)
    return JSONResponse({"ok": True, "answer": answer, "model": MODEL_NAME, "mode": "responses"})
