# app.py — Tecnaria Bot (solo /ask, no /health, no /docs)

import os
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# OpenAI Responses API (SDK >= 1.40)
from openai import OpenAI
from openai._exceptions import (
    APIConnectionError,
    APIStatusError,
    RateLimitError,
    APITimeoutError,
)

# ------------------ Config ------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata nelle Environment Variables.")

MODEL_NAME = "gpt-5.0"  # Usa un modello abilitato sul tuo account

PROMPT_BASE = (
    "Sei un tecnico/commerciale esperto di TECNARIA S.p.A. (Bassano del Grappa). "
    "Rispondi in modo chiaro e pratico. Se servono assunzioni, dichiarale. "
    "NON inventare dati di certificazioni; sii prudente. Una sola risposta completa."
)

client = OpenAI(api_key=OPENAI_API_KEY)

# Disabilito docs/redoc/openapi per non esporre nulla
app = FastAPI(
    title="Tecnaria Bot",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# CORS (restringi allow_origins se hai un dominio preciso)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],            # es. ["https://tuo-dominio.it"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------ Schemi ------------------
class AskPayload(BaseModel):
    question: str
    lang: Optional[str] = "it"

# ------------------ OpenAI helper ------------------
def _call_openai_responses(prompt: str, max_retries: int = 3, base_delay: float = 1.2) -> str:
    """
    Chiama la Responses API con retry/backoff.
    Ritorna sempre testo (stringa). Lancia HTTPException se fallisce.
    """
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = client.responses.create(
                model=MODEL_NAME,
                input=[
                    {"role": "system", "content": PROMPT_BASE},
                    {"role": "user", "content": prompt},
                ],
            )

            # SDK moderno espone output_text
            if getattr(resp, "output_text", None):
                return resp.output_text.strip()

            # Fallback generico: estrazione dai chunks
            items = getattr(resp, "output", None) or []
            chunks = []
            for it in items:
                content = getattr(it, "content", None) or []
                for c in content:
                    if getattr(c, "type", "") == "output_text":
                        chunks.append(getattr(c, "text", ""))
            text = "\n".join([c for c in chunks if c]).strip()
            return text or str(resp)

        except (APIConnectionError, APITimeoutError, RateLimitError) as e:
            # Ritenti con backoff
            last_err = e
            time.sleep(base_delay * attempt)
        except APIStatusError as e:
            # 4xx/5xx da OpenAI → propaghiamo 502 per il frontend
            raise HTTPException(
                status_code=502,
                detail=f"Errore OpenAI (status {e.status_code}): {getattr(e, 'message', str(e))}",
            ) from e
        except Exception as e:
            last_err = e
            break

    # se qui → falliti tutti i retry
    raise HTTPException(
        status_code=504,
        detail=f"Impossibile contattare OpenAI: {type(last_err).__name__}: {str(last_err)}",
    )

# ------------------ Probe "muto" per Render ------------------
@app.head("/")
def _probe_head():
    return Response(status_code=204)

@app.get("/")
def _probe_get():
    return Response(status_code=204)

# ------------------ Endpoint principale ------------------
@app.post("/ask")
def ask(payload: AskPayload):
    q = (payload.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="question mancante.")
    # lingua
    lang = (payload.lang or "it").strip().lower()
    final_prompt = q if lang == "it" else f"[Rispondi in {lang}] {q}"

    try:
        answer = _call_openai_responses(final_prompt)
        return JSONResponse({"ok": True, "answer": answer})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore interno: {str(e)}")
