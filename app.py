# -*- coding: utf-8 -*-
from __future__ import annotations
import os
from flask import Flask, render_template, request
from dotenv import load_dotenv

from scraper_tecnaria import risposta_document_first, reload_index
# Fallback LLM opzionale:
# from scraper_tecnaria import risposta_llm

load_dotenv()

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    risposta = ""
    if request.method == "POST":
        domanda = (request.form.get("domanda") or "").strip()

        # 1) Prova document-first
        risposta = risposta_document_first(domanda) or ""

        # 2) (Opzionale) Fallback LLM se non trovato nulla nei documenti
        # if not risposta:
        #     risposta = risposta_llm(domanda)

        if not risposta:
            risposta = (
                "Non ho trovato riferimenti nei documenti locali. "
                "Prova a riformulare la domanda oppure abbassa la soglia di similarità "
                f"(attuale: {os.getenv('SIMILARITY_THRESHOLD','65')})."
            )
    return render_template("index.html", risposta=risposta)

@app.route("/health", methods=["GET"])
def health():
    return "ok", 200

@app.route("/reload", methods=["POST", "GET"])
def reload_route():
    n = reload_index()
    return f"Indice ricaricato. Documenti indicizzati: {n}", 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
