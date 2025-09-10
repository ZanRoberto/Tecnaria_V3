# app.py — Tecnaria Bot (Document-Only, FastAPI + Uvicorn)

import os
from fastapi import FastAPI
from pydantic import BaseModel
from scraper_tecnaria import risposta_document_first, reload_index

BOT_OFFLINE_ONLY = os.getenv("BOT_OFFLINE_ONLY", "true").lower() == "true"
DOC_FOLDER = os.getenv("DOC_FOLDER", "./documenti_gTab")

app = FastAPI(title="Tecnaria Bot - Document Only")

class Query(BaseModel):
    question: str

@app.get("/healthz")
def healthz():
    return {"ok": True, "offline_only": BOT_OFFLINE_ONLY, "doc_folder": DOC_FOLDER}

@app.post("/ask")
def ask(q: Query):
    # Sempre e solo dai documenti locali
    return risposta_document_first(q.question)

@app.post("/reload")
def reload_docs():
    n = reload_index()
    return {"ok": True, "documents": n}

if __name__ == "__main__":
    # Avvio locale (Render usa lo Start Command uvicorn)
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")), log_level="info")
