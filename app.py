# app.py
import os
import re
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI

# ------------------- Config -------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata nelle Environment Variables di Render.")

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.1-mini")

# ------------------- Base dati di dominio (invarianti) -------------------
# Questa è la "stessa base dati" condivisa che vogliamo usare ora (niente RAG da file).
SYSTEM_KB = """
DOMINIO TECNARIA — REGOLE BASE (CTF + P560):

• Attrezzo: chiodatrice a cartuccia SPIT P560 con kit/adattatori Tecnaria per CTF.
• Fissaggio: ogni connettore CTF richiede 2 chiodi HSBR14.
• Trave in acciaio: spessore minimo ≥ 6 mm.
• Lamiera grecata ammessa: 1×1,5 mm oppure 2×1,0 mm se ben aderente alla trave (senza giochi).
• Posizione di posa: i connettori CTF si posano sopra la trave.
• Propulsori: cartucce a disco 6,3×16 mm; livello di potenza da tarare con prove in cantiere.
• Riferimenti: ETA-18/0447; Istruzioni di posa CTF; Manuale SPIT P560.
• Principio di risposta:
  - Se l’informazione è fuori da questo perimetro o mancano dati, rispondi in modo prudente e dichiaralo.
  - Non inventare valori tabellari specifici (PRd/P0 ecc.); rimanda alle tabelle ETA-18/0447 quando richieste.
"""

SYSTEM_PROMPT = f"""Sei il BOT Tecnaria ufficiale per connettori e accessori (CTF, CTL, CTCEM, VCEM, SPIT P560, ecc.).
Attieniti strettamente alle regole qui sotto e rispondi SOLO in italiano.

{SYSTEM_KB}

OUTPUT richiesto:
A) BOT Tecnaria (scheda) → 4–10 bullet normativi, brevi e pronti per capitolato/cantiere.
B) Spiegazione (ingegneristica) → 1–2 paragrafi con motivazioni operative, taratura propulsori e controlli minimi.
C) Fonti/Riferimenti → elenca i riferimenti fissi (ETA-18/0447, Istruzioni di posa CTF, Manuale SPIT P560).
Tono: tecnico, preciso, zero fronzoli. Se servono prove/controlli in cantiere, dillo esplicitamente.
"""

USER_WRAPPER = """Domanda utente:
{question}

ISTRUZIONI PER L'OUTPUT:
• A) BOT Tecnaria (scheda): bullet compatti, fattuali (attenzione a 2×HSBR14, ≥6 mm, lamiera ammessa, posa sopra trave).
• B) Spiegazione (ingegneristica): motivazioni, taratura propulsori (cartucce 6,3×16 mm), controlli in opera (test di trazione a campione se sensato).
• C) Fonti/Riferimenti: elenco sintetico dei documenti di riferimento (ETA-18/0447, Istruzioni di posa CTF, Manuale SPIT P560).
"""

# ------------------- FastAPI -------------------
app = FastAPI(title="Tecnaria Bot - Scheda + Spiegazione (NO RAG)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = OpenAI(api_key=OPENAI_API_KEY)

class AskIn(BaseModel):
    question: str
    mode: str | None = "both"  # "both" | "bot" | "explain"

def ask_openai_compound(question: str) -> dict:
    user_msg = USER_WRAPPER.format(question=question.strip())
    try:
        resp = client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            max_output_tokens=900,
        )
        text = resp.output_text
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore OpenAI Responses API: {e}")

    # Split robusto su sezioni A/B/C
    bot_section = ""
    explain_section = ""
    sources_section = ""

    parts = re.split(r"\n\s*A\)\s*BOT\s+Tecnaria.*?:|\n\s*B\)\s*Spiegazione.*?:|\n\s*C\)\s*(Fonti|Riferimenti).*?:", text, flags=re.I)
    headers = re.findall(r"\n\s*([ABC])\)\s*(BOT\s+Tecnaria|Spiegazione|Fonti|Riferimenti)[^\n]*:", text, flags=re.I)

    if headers and len(parts) >= 2:
        bodies = parts[1:]
        mapping = {}
        for i, h in enumerate(headers):
            key = h[0].upper()
            mapping[key] = bodies[i].strip()
        bot_section = mapping.get("A", "").strip()
        explain_section = mapping.get("B", "").strip()
        sources_section = mapping.get("C", "").strip()
    else:
        # fallback: tutto nella spiegazione
        explain_section = text.strip()

    return {
        "mode": "both",
        "bot": bot_section,
        "explain": explain_section,
        "sources": sources_section,
    }

@app.get("/", response_model=dict)
def root():
    return {"status": "ok", "service": "Tecnaria Bot - Scheda + Spiegazione (NO RAG)"}

@app.get("/health", response_model=dict)
def health():
    # Niente RAG: riportiamo solo lo stato ambiente/modello
    return {
        "status": "ok",
        "rag": "disabled",
        "model": OPENAI_MODEL,
        "kb_loaded": True,  # KB è incorporata
    }

@app.post("/ask", response_model=dict)
def ask(inp: AskIn):
    q = (inp.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Manca 'question'.")

    result = ask_openai_compound(q)
    mode = (inp.mode or "both").lower()
    if mode == "bot":
        return {"answer": result.get("bot", ""), "mode": "bot", "sources": result.get("sources", "")}
    elif mode == "explain":
        return {"answer": result.get("explain", ""), "mode": "explain", "sources": result.get("sources", "")}
    else:
        return result

# ---- Avvio su Render (esempio) ----
# gunicorn app:app --timeout 120 --workers=1 --threads=2 -b 0.0.0.0:$PORT
