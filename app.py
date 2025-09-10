# -*- coding: utf-8 -*-
from __future__ import annotations
import os
from flask import Flask, render_template, request, redirect, url_for, jsonify
from dotenv import load_dotenv

load_dotenv()

# Import motore document-first
try:
    from scraper_tecnaria import risposta_document_first, reload_index  # type: ignore
except Exception:
    # Fallback sicuro per sviluppo: non stampa prefissi, non mostra "undefined"
    def risposta_document_first(domanda: str) -> str:
        return ""
    def reload_index() -> None:
        pass

app = Flask(__name__, template_folder="templates", static_folder="static")

# ---- Branding opzionale via .env ----
BRAND_NAME = os.getenv("BRAND_NAME", "Tecnaria")
BRAND_LOGO_URL = os.getenv("BRAND_LOGO_URL", "/static/img/logo-placeholder.svg")

@app.context_processor
def inject_brand():
    return dict(BRAND_NAME=BRAND_NAME, BRAND_LOGO_URL=BRAND_LOGO_URL)

def _sanitize(text: str) -> str:
    """Rimuove eventuali prefissi rumorosi inseriti a monte (no 'documentazione locale', no 'Fonti', no 'undefined')."""
    if not text:
        return ""
    # rimuovi parole inutili/prefissi noti
    bad_prefixes = (
        "🧠", "Risposta basata su documentazione", "Fonti:", "Capitolo_", "undefined"
    )
    lines = []
    for ln in text.splitlines():
        ln_strip = ln.strip()
        if not ln_strip:
            lines.append(ln)  # mantieni righe vuote per spaziatura
            continue
        if any(ln_strip.startswith(p) for p in bad_prefixes):
            continue
        lines.append(ln)
    cleaned = "\n".join(lines).strip()
    return cleaned or ""

@app.route("/", methods=["GET", "POST"])
def index():
    risposta = ""
    domanda = ""
    if request.method == "POST":
        domanda = (request.form.get("domanda") or "").strip()
        if domanda:
            risposta = risposta_document_first(domanda) or ""
            risposta = _sanitize(risposta)
        if not risposta:
            risposta = "Non ho trovato riferimenti utili nei file locali. Prova a riformulare la domanda."
    return render_template("index.html", risposta=risposta, domanda=domanda)

@app.route("/reload", methods=["POST"])
def reload():
    try:
        reload_index()
    except Exception:
        pass
    # Torna alla home dopo il reload
    return redirect(url_for("index"))

@app.route("/health")
def health():
    return jsonify(status="ok")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
