# -*- coding: utf-8 -*-
import os, sys, json, glob
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# === Import dallo scraper ===
# DEVONO esistere: build_index(dir), search_best_answer(q, threshold, topk),
# is_ready() e INDEX nello scraper_tecnaria.py
from scraper_tecnaria import (
    build_index,
    search_best_answer,
    is_ready,
    INDEX,
)

# === Config ===
DOC_DIR = os.environ.get("DOC_DIR", "documenti_gTab")
SIM_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.35"))
TOPK = int(os.environ.get("TOPK_SEMANTIC", "20"))

# === Flask ===
app = Flask(__name__)
CORS(app)

def _list_txt(dirpath):
    try:
        return sorted(glob.glob(os.path.join(dirpath, "*.txt"))) + \
               sorted(glob.glob(os.path.join(dirpath, "*.TXT")))
    except Exception:
        return []

def _force_build_index():
    """
    Costruzione SINCRONA all’avvio (niente 'indice non pronto').
    Se fallisce, stampa un log diagnostico con elenco file visti.
    """
    app.logger.warning("[startup] Build indice sincrona da: %s", os.path.abspath(DOC_DIR))
    files = _list_txt(DOC_DIR)
    app.logger.warning("[startup] Trovati %d file .txt/.TXT", len(files))
    if not files:
        app.logger.error("[startup] NESSUN file .txt trovato in %s", os.path.abspath(DOC_DIR))
    for f in files[:50]:
        app.logger.warning("[startup]  - %s", f)

    try:
        build_index(DOC_DIR)   # <-- BLOCCANTE
    except Exception as e:
        app.logger.exception("[startup] ERRORE build_index: %s", e)

    try:
        docs = len(INDEX) if INDEX is not None else 0
    except Exception:
        docs = 0

    app.logger.warning("[startup] Indice pronto: %s | docs=%d", is_ready(), docs)
    return docs > 0

# === COSTRUISCI L’INDICE PRIMA DI ESPORRE LE ROUTE ===
_force_build_index()

# === Routes ===
@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/ask", methods=["POST"])
def ask():
    q = (request.form.get("domanda") or "").strip()
    if not q:
        return jsonify({"answer": "Nessuna domanda ricevuta.", "found": False, "from": None})

    # Se per qualunque motivo l’indice non fosse pronto, RICOSTRUISCI subito e rispondi
    if not is_ready():
        app.logger.warning("[ask] Indice non pronto: ricostruisco SINCRONO")
        ok = _force_build_index()
        if not ok:
            return jsonify({
                "answer": "Indice non pronto. Controlla i file .txt in documenti_gTab.",
                "found": False,
                "from": None
            })

    try:
        result = search_best_answer(q, threshold=SIM_THRESHOLD, topk=TOPK)
        # (opzionale) post-processing: togli eventuali “D:/R:” se nel testo sorgente
        ans = (result.get("answer") or "")
        if ans.startswith("D:") or ans.startswith("R:"):
            # rimuovi etichette riga-iniziali per presentazione più pulita
            cleaned = []
            for line in ans.splitlines():
                if line.startswith("D: ") or line.startswith("R: "):
                    cleaned.append(line[3:])
                else:
                    cleaned.append(line)
            result["answer"] = "\n".join(cleaned)

        return jsonify(result)
    except Exception as e:
        app.logger.exception("[ask] Errore in search_best_answer: %s", e)
        return jsonify({
            "answer": "Errore interno durante la ricerca.",
            "found": False,
            "from": None
        }), 500

@app.route("/reload", methods=["POST"])
def reload():
    app.logger.warning("[reload] Ricostruzione SINCRONA richiesta")
    ok = _force_build_index()
    docs = len(INDEX) if INDEX is not None else 0
    if ok:
        return jsonify({"status": "ok", "docs": docs})
    else:
        return jsonify({"status": "error", "docs": docs}), 500

@app.route("/health", methods=["GET"])
def health():
    try:
        docs = len(INDEX) if INDEX is not None else 0
    except Exception:
        docs = 0
    return jsonify({
        "status": "ok" if docs > 0 else "empty",
        "docs": docs,
        "threshold": SIM_THRESHOLD,
        "topk": TOPK,
        "doc_dir": os.path.abspath(DOC_DIR),
        "ready": is_ready(),
    })

# DIAGNOSTICA EXTRA: elenca i file che l’app vede davvero
@app.route("/diag", methods=["GET"])
def diag():
    files = _list_txt(DOC_DIR)
    payload = {
        "cwd": os.getcwd(),
        "doc_dir": os.path.abspath(DOC_DIR),
        "exists_doc_dir": os.path.isdir(DOC_DIR),
        "txt_count": len(files),
        "first_files": files[:50],
        "ready": is_ready(),
        "env": {k: os.environ.get(k) for k in ["DOC_DIR", "SIMILARITY_THRESHOLD", "TOPK_SEMANTIC", "SINAPSI_JSON"]}
    }
    return jsonify(payload)

# === Avvio locale ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
