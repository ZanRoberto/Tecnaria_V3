# app.py — Tecnaria Bot API (UI integrata, “soft gold” incluse)
# Endpoint: GET /(UI), GET /health, POST /ask
# Start cmd (Render):
#   gunicorn app:app -k uvicorn.workers.UvicornWorker --timeout 180 --workers=1 --preload -b 0.0.0.0:$PORT

import os, re
from typing import List
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

APP_NAME = "Tecnaria Bot API"
MODEL_NAME = (os.getenv("MODEL_NAME") or "gpt-4.1").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or ""

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

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
    "posa","interasse","incoterms","packing list","hs code","soletta",
    "tavolato","assito","trave","cresta","gola","export","spedizione","dogana",
    "pagamento","preventivo","offerta","reclami","assistenza","reso","res i"
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
    "CTF_CRESTA":      re.compile(r"\bctf\b.*\bcresta\b|\bcresta\b.*\bctf\b", re.I),
    # Commerciale
    "EXPORT_DOCS":     re.compile(r"\b(export|incoterm|hs\s*code|packing|spedizion|dogan)\w*\b", re.I),
    "LEAD_TIMES":      re.compile(r"\b(tempi|consegn|lead\s*time|disponibilit[aà]|a\s*stock|pront[oi])\b", re.I),
    "PAYMENTS":        re.compile(r"\b(pagament[io]|termini\s*di\s*pagamento|saldo|acconto|bonific[io])\b", re.I),
    "SUPPORT_RET":     re.compile(r"\b(reclami?|assistenza|reso|resi|garanzi\w+|supporto)\b", re.I),
    "QUOTE_MIN":       re.compile(r"(preventiv|offert)\w+|\b(cosa\s+vi\s+serve|dati\s+minimi)\b", re.I),
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
        f"usa {_bold('SPIT P560')} con {_bold('2 chiodi')} per connettore",
        "posa **in gola**, utensile **perpendicolare**, piastra **in battuta**",
        "esegui **taratura** su provino identico prima della produzione",
        "varianti solo previa **approvazione Tecnaria** (qualifica in sito)",
    ]
    note = "Nota: vedi **Istruzioni di posa CTF**; interassi/quantità si **confermano su DWG/PDF**."
    return _pack(head, bullets, note)

def sheet_ctcem_resine() -> str:
    head = "No: **niente resine**."
    bullets = [
        "**fissaggio a secco** (meccanico)",
        "**foratura → pulizia foro → avvitamento a battuta piastra**",
        "varianti solo previa **approvazione Tecnaria**",
    ]
    note = "Nota: vedi **Istruzioni di posa CTCEM**; interassi/quantità si **confermano su DWG/PDF**."
    return _pack(head, bullets, note)

def sheet_maxi_tavolato() -> str:
    head = "Sì: **CTL MAXI** per posa su tavolato (modello/lunghezze da confermare su elaborati)."
    bullets = [
        "**viti** che **attraversano il tavolato e ancorano nella trave** (non solo nel tavolato)",
        "testa **sopra la rete** e **sotto** il filo del getto",
        "modello/altezza e **lunghezza viti** si **confermano su DWG/PDF**",
    ]
    note = "Nota: vedi **Istruzioni CTL MAXI / particolari costruttivi**."
    return _pack(head, bullets, note)

def sheet_posa_ctf() -> str:
    head = "**CTF (acciaio + lamiera grecata)**"
    bullets = [
        "posa **in gola** della lamiera; utensile **perpendicolare**, piastra **in battuta**",
        f"chiodatrice {_bold('SPIT P560')} con {_bold('2 chiodi')} per connettore",
        "eseguire **taratura** su provino identico prima della produzione",
        "varianti solo previa **approvazione Tecnaria**",
    ]
    note = "**Nota**: Istruzioni di posa **CTF**; interassi/quantità su **DWG/PDF**."
    return _pack(head, bullets, note)

def sheet_posa_ctcem() -> str:
    head = "**CTCEM (laterocemento)**"
    bullets = [
        "**a secco**: **foratura → pulizia foro → avvitamento a battuta piastra**",
        "**no resine**; varianti solo se approvate da Tecnaria",
        "confermare interassi e quantità su **DWG/PDF**",
    ]
    note = "**Nota**: Istruzioni di posa **CTCEM**."
    return _pack(head, bullets, note)

def sheet_posa_maxi() -> str:
    head = "**CTL MAXI (legno / tavolato)**"
    bullets = [
        "**viti** che **attraversano il tavolato e ancorano nella trave** (non solo nel tavolato)",
        "testa **sopra la rete** e **sotto** il filo del getto",
        "modello e lunghezza viti **si confermano su DWG/PDF**",
    ]
    note = "**Nota**: Istruzioni **CTL MAXI / particolari costruttivi**."
    return _pack(head, bullets, note)

def sheet_posa_panorama() -> str:
    return "\n\n".join([sheet_posa_ctf(), sheet_posa_ctcem(), sheet_posa_maxi()])

def sheet_ctf_cresta() -> str:
    head = "No: la posa dei CTF su lamiera si fa **in gola**, non in cresta."
    bullets = [
        "la **cresta** non garantisce **appoggio/foratura corretti** né rispetta le istruzioni",
        "fissaggio: **SPIT P560 + 2 chiodi** per connettore; utensile **perpendicolare**, piastra **in battuta**",
        "**taratura** su provino identico prima della produzione",
        "varianti solo con **approvazione Tecnaria** (qualifica in sito)"
    ]
    note = "Nota: vedi **Istruzioni di posa CTF**; interassi/quantità si **confermano su DWG/PDF**."
    return _pack(head, bullets, note)

# --------- Export (SOFT GOLD) — versione più morbida -----------
def sheet_export_docs() -> str:
    head = ("Sì, per le forniture all’estero possiamo fornire la documentazione export e gestire "
            "Incoterms/HS code in base al Paese di destinazione.")
    bullets = [
        "**Documenti**: packing list, fattura (proforma/commerciale), dichiarazione di origine se richiesta, "
        "ETA/DoP/CE e istruzioni di posa",
        "**Incoterms**: di solito EXW o FCA; altri (FOB, CIF, DDP) su accordo in offerta",
        "**HS code**: indichiamo la **famiglia**; il dettaglio preciso si **conferma in ordine**",
        "**Imballi/etichette**: standard; **personalizzazioni** disponibili su richiesta"
    ]
    note = ("**Nota**: inviaci **DWG/PDF**, **quantità**, **Paese di destinazione** e la **resa Incoterms** desiderata "
            "per preparare un’offerta completa.")
    return _pack(head, bullets, note)

# --------- Lead times / disponibilità (SOFT GOLD) -----------
def sheet_lead_times() -> str:
    head = ("Possiamo indicare disponibilità e tempi in base al prodotto e alle quantità; "
            "la conferma avviene in offerta/ordine.")
    bullets = [
        "**A stock / produzione**: alcuni articoli pronti, altri su **lead time**",
        "**Indicazione tipica**: disponibilità o **settimane** di produzione; imballo standard",
        "**Parziali**: possibili **spedizioni parziali** su richiesta",
        "**Ritiro/resa**: **EXW/FCA** di norma; altre rese su accordo",
        "**Conferma**: tempi e quantità **si confermano in offerta** (poi in ordine)",
    ]
    note = ("**Nota**: inviaci **PDF/DWG**, **quantità** e **Paese** per stimare correttamente tempi e spedizione.")
    return _pack(head, bullets, note)

# --------- Condizioni di pagamento (SOFT GOLD) -----------
def sheet_payments() -> str:
    head = "Definiamo il pagamento insieme in offerta in base a destinazione e Incoterms."
    bullets = [
        "**Modalità**: **bonifico bancario** (anticipo/saldo)",
        "**Export extra-UE**: condizioni **senza IVA** con prova di export",
        "**Incassi**: saldo **prima della spedizione** se non diversamente concordato",
        "**Valuta**: normalmente **EUR** (altre valute su accordo)",
        "**Documenti**: **proforma** per anticipo, **commercial invoice** a saldo",
    ]
    note = ("**Nota**: inserisci in richiesta **Paese**, **resa Incoterms** e l’eventuale **scadenza** desiderata.")
    return _pack(head, bullets, note)

# --------- Resi / Reclami / Assistenza (SOFT GOLD) -----------
def sheet_support_returns() -> str:
    head = "Siamo a disposizione per assistenza tecnica e gestione resi/reclami."
    bullets = [
        "**Assistenza**: supporto su posa/prodotto; invia foto/elaborati per analisi rapida",
        "**Resi**: concordati caso per caso; imballo integro e autorizzazione preventiva",
        "**Reclami**: apri ticket con **ordine/lotto**, descrizione e immagini",
        "**Tempi**: riscontro **entro 2 giorni lavorativi**; soluzioni condivise",
        "**Documenti**: verbale di non conformità se necessario",
    ]
    note = "**Nota**: scrivici i riferimenti d’ordine e il caso; ti guidiamo passo passo."
    return _pack(head, bullets, note)

# --------- Preventivo: dati minimi da inviare (SOFT GOLD) -----------
def sheet_quote_min_data() -> str:
    head = "Per preparare un preventivo accurato ci servono pochi dati chiave."
    bullets = [
        "**Elaborati**: PDF/DWG del solaio (tipologia: lamiera grecata o laterocemento)",
        "**Quantità**: indicazioni per aree/lunghezze e connettori (se già stimati)",
        "**Destinazione**: Paese e **Incoterms** desiderato (es. EXW/FCA/FOB/CIF/DDP)",
        "**Tempistiche**: eventuale data obiettivo o vincoli di cantiere",
        "**Contatti**: ragione sociale e recapito",
    ]
    note = "In base ai dati confermiamo prodotti/accessori (CTF/CTCEM/CTL MAXI) e tempistiche."
    return _pack(head, bullets, note)

# --------------------------- System prompt (stile “telefono”) ----------------------
def build_system_prompt(lang: str = "it") -> str:
    lang = (lang or "it").strip().lower()
    return "\n".join([
        "Ruolo: assistente Tecnaria. Rispondi SOLO su Tecnaria (CTF, CTCEM, CTL MAXI, posa, documenti, export).",
        "Stile 'telefono':",
        " - Apri con verdetto secco **Sì/No + motivo breve** in UNA riga.",
        " - Poi 3–5 bullet telegrafici con parole chiave in **grassetto**.",
        " - Chiudi con **Nota** (Istruzioni Tecnaria o richiesta DWG/PDF).",
        "Tono: chiaro e **morbido** (consiglia, evita termini perentori salvo necessità).",
        "Limiti:",
        " - Non inventare **mm/Ø/modelli** non presenti nella domanda.",
        " - Se mancano dati, usa: 'si conferma su DWG/PDF'.",
        " - Evita disclaimer o riferimenti all'AI.",
        "Contenuti cardine:",
        " - CTF: **SPIT P560 + 2 chiodi**, **posa in gola**, **taratura** su provino; varianti con approvazione Tecnaria.",
        " - CTCEM: **no resine**; **foratura → pulizia foro → avvitamento a battuta piastra**.",
        " - CTL MAXI: **viti attraversano tavolato e ancorano nella trave**; testa **sopra rete/sotto filo getto**; modello/lunghezze su **DWG/PDF**.",
        f"Lingua: usa la lingua della domanda (default: {lang})."
    ])

# --------------------------- OpenAI Responses API wrapper -------------------------
def call_openai(system_prompt: str, user_text: str) -> str:
    if not client:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY non configurata.")
    try:
        resp = client.responses.create(
            model=MODEL_NAME,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user",   "content": [{"type": "input_text", "text": user_text}]}
            ]
        )
        try:
            return (resp.output_text or "").strip()
        except Exception:
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
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>" + APP_NAME + "</title>"
        "<style>"
        "body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:0;background:#f7f7fb;color:#111}"
        ".wrap{max-width:900px;margin:32px auto;padding:16px}"
        ".card{background:#fff;border:1px solid #e5e7eb;border-radius:14px;box-shadow:0 6px 20px rgba(0,0,0,.06);padding:18px}"
        "textarea{width:100%;border:1px solid #d1d5db;border-radius:10px;padding:10px;min-height:90px}"
        "button{background:#0b5cff;color:#fff;border:0;border-radius:10px;padding:10px 14px;cursor:pointer}"
        "button:disabled{opacity:.6;cursor:not-allowed}"
        "select,input{border:1px solid #d1d5db;border-radius:8px;padding:8px}"
        "pre{white-space:pre-wrap;background:#0a0a0a;color:#f7f7f7;padding:14px;border-radius:10px;max-height:50vh;overflow:auto}"
        ".row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px}"
        ".muted{color:#6b7280}"
        "a{color:#0b5cff;text-decoration:none}"
        "</style></head><body>"
        "<div class='wrap'>"
        "<h1 style='margin:0 0 10px'>" + APP_NAME + "</h1>"
        "<div class='muted' style='margin-bottom:16px'>Fai una domanda Tecnaria e premi “Chiedi”.</div>"
        "<div class='card'>"
        "<label for='q' style='font-weight:600'>Domanda</label>"
        "<textarea id='q' placeholder='Es.: CTF in gola? CTCEM con resine? MAXI su tavolato? Export/tempi/pagamenti?'></textarea>"
        "<div class='row'>"
        "<div>Lingua: <select id='lang'><option value='it'>Italiano</option><option value='en'>English</option></select></div>"
        "<button id='go'>Chiedi</button>"
        "<span id='status' class='muted'></span>"
        "</div>"
        "<pre id='out' style='margin-top:12px'></pre>"
        "<div class='muted' style='font-size:13px;margin-top:6px'>"
        "Endpoint: <a href='/health'>GET /health</a> • POST /ask — body: {\"question\":\"...\",\"lang\":\"it\"}"
        "</div>"
        "</div></div>"
        "<script>"
        "const $ = id => document.getElementById(id);"
        "const q=$('q'), lang=$('lang'), go=$('go'), out=$('out'), statusEl=$('status');"
        "go.onclick = async () => {"
        "  const question = (q.value||'').trim();"
        "  if(!question){ out.textContent='Scrivi una domanda.'; return; }"
        "  go.disabled=true; statusEl.textContent='...'; out.textContent='';"
        "  try{"
        "    const r = await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question,lang:lang.value})});"
        "    const j = await r.json();"
        "    out.textContent = j.answer || JSON.stringify(j,null,2);"
        "  }catch(e){ out.textContent = 'Errore: '+e; }"
        "  finally{ go.disabled=false; statusEl.textContent=''; }"
        "};"
        "</script>"
        "</body></html>"
    )
    return HTMLResponse(content=html)

@app.get("/health")
def health():
    return {"ok": True, "model": MODEL_NAME, "api_key_set": bool(OPENAI_API_KEY)}

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

    # Scorciatoie “a scheda” (risposte immediate, consistenti)
    if _RX["EXPORT_DOCS"].search(q):
        return JSONResponse({"ok": True, "answer": sheet_export_docs(), "model": "style", "mode": "sheet"})
    if _RX["LEAD_TIMES"].search(q):
        return JSONResponse({"ok": True, "answer": sheet_lead_times(), "model": "style", "mode": "sheet"})
    if _RX["PAYMENTS"].search(q):
        return JSONResponse({"ok": True, "answer": sheet_payments(), "model": "style", "mode": "sheet"})
    if _RX["SUPPORT_RET"].search(q):
        return JSONResponse({"ok": True, "answer": sheet_support_returns(), "model": "style", "mode": "sheet"})
    if _RX["QUOTE_MIN"].search(q):
        return JSONResponse({"ok": True, "answer": sheet_quote_min_data(), "model": "style", "mode": "sheet"})

    # Tecniche
    if _RX["CTF_CRESTA"].search(q):
        return JSONResponse({"ok": True, "answer": sheet_ctf_cresta(), "model": "style", "mode": "sheet"})
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

    # Fallback: modello con stile “telefono” forzato
    answer = call_openai(build_system_prompt(lang=lang), q)
    return JSONResponse({"ok": True, "answer": answer, "model": MODEL_NAME, "mode": "responses"})
