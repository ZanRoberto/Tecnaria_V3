import os
import time
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel

# ---------------- OpenAI (Responses API) ----------------
# pip install --upgrade openai
from openai import OpenAI
from openai._exceptions import APIConnectionError, APIStatusError, RateLimitError, APITimeoutError

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata nelle Environment Variables.")

client = OpenAI(api_key=OPENAI_API_KEY)

# --------------- FastAPI ----------------
app = FastAPI(title="Tecnaria Bot - API minimale (Responses API)")

# CORS permissivi (se serve restringi i domini)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # metti il tuo dominio se vuoi
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- Prompt base ----------------
PROMPT_BASE = """Sei un tecnico/commerciale esperto di TECNARIA S.p.A. (Bassano del Grappa).
Rispondi in modo chiaro e pratico. Se servono assunzioni, dichiarale.
NON inventare dati di certificazioni; sii prudente. Una sola risposta completa."""

# ---------------- Schemi ----------------
class AskPayload(BaseModel):
    question: str
    lang: Optional[str] = "it"   # puoi forzarla a "it" se vuoi

# ---------------- Utils ----------------
def _call_openai_responses(prompt: str, max_retries: int = 3, base_delay: float = 1.2) -> str:
    """
    Chiama la Responses API con retry/backoff e timeout più rilassati.
    """
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            # NB: usa il modello che stai effettivamente abilitando sul tuo account
            # Esempi comuni nel 2025: "gpt-5.0" per testo non vision, oppure il tuo custom.
            resp = client.responses.create(
                model="gpt-5.0",
                input=[
                    {
                        "role": "system",
                        "content": PROMPT_BASE,
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                # timeout lato server OpenAI (secondi). Alcuni SDK lo espongono come client-level; qui è safe.
                # Se il tuo SDK non supporta timeout qui, gestisci lato HTTP client o a livello di Reverse Proxy.
                # n.b. l'SDK recente gestisce timeouts interni; in caso opposto cattureremo APITimeoutError.
            )

            # Responses API: testo in resp.output_text (SDK recente) o va estratto dai "items"
            # Proviamo prima la scorciatoia:
            if hasattr(resp, "output_text") and resp.output_text:
                return resp.output_text.strip()

            # Fallback generico
            try:
                # Alcune versioni ritornano "output" con "content" -> "text"
                items = getattr(resp, "output", None) or []
                chunks = []
                for it in items:
                    # ogni item può avere "content" con "text"
                    content = getattr(it, "content", None) or []
                    for c in content:
                        if getattr(c, "type", "") == "output_text":
                            chunks.append(getattr(c, "text", ""))
                txt = "\n".join([c for c in chunks if c]).strip()
                if txt:
                    return txt
            except Exception:
                pass

            # Ultimo fallback: serializza resp e prova a cavarne il testo
            return str(resp)

        except (APIConnectionError, APITimeoutError) as e:
            last_err = e
            # backoff
            time.sleep(base_delay * attempt)
        except RateLimitError as e:
            last_err = e
            # attesa un po' più lunga su rate limit
            time.sleep(base_delay * attempt + 1.5)
        except APIStatusError as e:
            # Errori 4xx/5xx dal server OpenAI
            # Se è 401/403 → quasi sempre API key o permessi modello
            raise HTTPException(
                status_code=502,
                detail=f"Errore OpenAI (status {e.status_code}): {getattr(e, 'message', str(e))}"
            ) from e
        except Exception as e:
            last_err = e
            break

    # se siamo qui, tutti i tentativi sono falliti
    raise HTTPException(
        status_code=504,
        detail=f"Impossibile contattare OpenAI: {type(last_err).__name__}: {str(last_err)}"
    )

# ---------------- Routes ----------------
@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
      <head><meta charset="utf-8"><title>Tecnaria Bot</title></head>
      <body>
        <h3>Tecnaria Bot - API minimale</h3>
        <p>Endpoint:</p>
        <ul>
          <li>GET <code>/health</code></li>
          <li>POST <code>/ask</code> — body: {"question": "...", "lang": "it"}</li>
        </ul>
      </body>
    </html>
    """

@app.get("/health")
def health():
    return {"status": "ok", "service": "Tecnaria Bot - Responses API"}

@app.post("/ask")
def ask(payload: AskPayload):
    q = (payload.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="question mancante.")
    # Costruzione prompt finale
    final_prompt = q if payload.lang == "it" else f"[Rispondi in {payload.lang}] {q}"
    try:
        answer = _call_openai_responses(final_prompt)
        return JSONResponse({"ok": True, "answer": answer})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore interno: {str(e)}")
