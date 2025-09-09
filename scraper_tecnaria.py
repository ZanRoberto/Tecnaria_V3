# -*- coding: utf-8 -*-
"""
Ricerca "document-first" nei contenuti locali.
- Legge ricorsivamente la cartella KNOWLEDGE_DIR (da .env)
- Indicizza txt/md/html/pdf (solo testo; per PDF scanner serve OCR esterno)
- Ranking con RapidFuzz
- Ritorna uno snippet per ciascun match trovato

Opzionale: funzione fallback LLM (commentata) se vuoi chiedere a OpenAI
quando i documenti non bastano.
"""

from __future__ import annotations
import os
import re
from typing import List, Dict, Optional
from dotenv import load_dotenv
from rapidfuzz import fuzz, process
from knowledge_loader import load_with_cache

# (Opzionale) LLM fallback
# from openai import OpenAI

load_dotenv()

KNOWLEDGE_DIR = os.getenv("KNOWLEDGE_DIR", "./documenti_gTab")
DEFAULT_THRESHOLD = int(os.getenv("SIMILARITY_THRESHOLD", "65"))
DEFAULT_MAX = int(os.getenv("MAX_MATCHES", "6"))

# MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
# OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def _normalize_query(q: str) -> str:
    q = (q or "").strip()
    q = re.sub(r"\s+", " ", q)
    return q


def cerca_nei_documenti(
    domanda: str,
    threshold: Optional[int] = None,
    max_matches: Optional[int] = None
) -> List[Dict]:
    """
    Ritorna lista di hit: [{"file": relpath, "score": int, "snippet": str}, ...]
    """
    domanda = _normalize_query(domanda)
    if not domanda:
        return []

    th = int(threshold or DEFAULT_THRESHOLD)
    k = int(max_matches or DEFAULT_MAX)

    docs = load_with_cache(KNOWLEDGE_DIR)
    if not docs:
        return []

    corpus = {d["relpath"]: d["text"] for d in docs if d.get("text")}
    if not corpus:
        return []

    results = process.extract(
        domanda,
        corpus,
        scorer=fuzz.token_set_ratio,
        limit=max(10, k)  # prendi un po' di più e poi filtra
    )

    hits: List[Dict] = []
    for relpath, score, text in results:
        if score < th:
            continue
        # snippet: cerca di mostrare i primi ~700 caratteri puliti
        snippet = text[:700]
        if len(text) > 700:
            snippet += "…"
        hits.append({"file": relpath, "score": int(score), "snippet": snippet})

        if len(hits) >= k:
            break

    return hits


def risposta_document_first(domanda: str) -> Optional[str]:
    """
    Se trova nei documenti, costruisce una risposta formattata.
    Altrimenti ritorna None (così l'app può fare un fallback).
    """
    hits = cerca_nei_documenti(domanda)
    if not hits:
        return None

    blocchi = []
    for h in hits:
        blocchi.append(
            f"📄 **{h['file']}** (score: {h['score']})\n\n{h['snippet']}"
        )
    return "\n\n---\n\n".join(blocchi)


# ==============================
# FACOLTATIVO: FALLBACK CON LLM
# ==============================
# def risposta_llm(domanda: str) -> str:
#     """
#     Chiede al modello una risposta sintetica quando i documenti locali
#     non hanno dato match sopra soglia. Richiede OPENAI_API_KEY nel .env.
#     """
#     if not client:
#         return "Non ho trovato riferimenti nei documenti. Prova a riformulare la domanda o ad abbassare la soglia di similarità."
#
#     prompt = (
#         "Rispondi in modo chiaro e sintetico (max 120 parole). "
#         "Se non sei sicuro, chiedi all'utente maggiori dettagli.\n\n"
#         f"Domanda: {domanda}"
#     )
#     try:
#         resp = client.chat.completions.create(
#             model=MODEL_NAME,
#             messages=[{"role": "user", "content": prompt}],
#             temperature=0.2,
#         )
#         return resp.choices[0].message.content.strip()
#     except Exception as e:
#         return f"Errore fallback LLM: {e}"
