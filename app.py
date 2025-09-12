# app.py — Tecnaria · Assistente documentale (boot sincrono, zero timeout)
import os
import threading
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# ---- Import dal tuo scraper ----
# Richiede nel file scraper_tecnaria.py:
#   build_index(docs_dir) -> dict con chiavi: blocks, files, lines, data
#   search_best_answer(index, question) -> (answer, found, score, path, line)
from scraper_tecnaria import build_index, search_best_answer

app = Flask(__name__)
CORS(app)

# ---- Stato indice globale ----
INDEX = None
INDEX_READY = False
INDEX_ERR = None
INDEX_LOCK = threading.Lock()

def _docs_dir() -> str:
    # Compatibile con tutte le env che hai usato
    return (
        os.environ.get("DOC_DIR")
        or os.environ.get("DOCS_FOLDER")
        or os.environ.get("KNOWLEDGE_DIR")
        or "documenti_gTab"
    )

def _build_index_sync():
    """Costruisce sincrono (bloccante). Se fallisce, salva l’errore."""
    global INDEX, INDEX_READY, INDEX_ERR
    with INDEX_LOCK:
        try:
            docs_dir = _docs_dir()
            print(f"[app] Indicizzazione SINCRONA da: {docs_dir}")
            idx = build_index(docs_dir)
            INDEX = idx
            INDEX_READY = True
            INDEX_ERR = None
            print(f"[app] Indice pronto: blocks={idx.get('blocks',0)} files={idx.get('files',0)} lines={idx.get('lines',0)}")
        except Exception as e:
            INDEX = None
            INDEX_READY = False
            INDEX_ERR = str(e)
            print(f"[app][ERRORE] Indicizzazione fallita: {e}")

def _ensure_index_ready():
    """Se non pronto, ricostruisci sincrono (usato da /ask come rete di salvataggio)."""
    global INDEX, INDEX_READY
    if INDEX_READY and INDEX is not None:
        return
    _build_index_sync()

# ---- Indicizzazione all’avvio (sincrona) se richiesto ----
if os.environ.get("REINDEX_ON_STARTUP", "1") == "1":
    # SINCRONA: il servizio parte solo a indice pronto
    _build_index_sync()
else:
    # Fallback: lasciare comunque un indice costruito prima della prima domanda
    INDEX_READY = False
    INDEX = None
    INDEX_ERR = None

@app.get("/")
def home():
    return render_template("index.html")

@app.get("/health")
def health():
    info = {
        "status": "ok",
        "ready": INDEX_READY,
        "error": INDEX_ERR,
        "docs_dir": _docs_dir(),
    }
    if INDEX and isinstance(INDEX, dict):
        info.update({
            "blocks": INDEX.get("blocks", 0),
            "files": INDEX.get("files", 0),
            "lines": INDEX.get("lines", 0),
        })
    return jsonify(info), 200

@app.post("/reload")
def reload_index():
    """Ricarica indice manualmente (se vuoi tenerlo nascosto in UI va bene lo stesso)."""
    global INDEX, INDEX_READY, INDEX_ERR
    _build_index_sync()
    return jsonify({
        "ok": INDEX_READY,
        "blocks": INDEX.get("blocks", 0) if INDEX else 0,
        "files": INDEX.get("files", 0) if INDEX else 0,
        "lines": INDEX.get("lines", 0) if INDEX else 0,
        "error": INDEX_ERR
    }), 200 if INDEX_READY else 500

@app.post("/ask")
def ask():
    data = request.get_json(silent=True) or {}
    q = (data.get("question") or "").strip()
    if not q:
        return jsonify({"answer":"", "found":False, "score":0}), 200

    # Garanzia: se per qualunque motivo non è pronto, ricostruisci ora (sincrono)
    _ensure_index_ready()
    if not INDEX_READY or INDEX is None:
        msg = "Indice non disponibile. Carica i .txt in documenti_gTab/ e premi Ricarica indice."
        if INDEX_ERR: msg += f" (Dettaglio: {INDEX_ERR})"
        return jsonify({"answer": msg, "found": False, "score": 0}), 200

    try:
        answer, found, score, path, line = search_best_answer(INDEX, q)
    except Exception as e:
        print(f"[app][ERRORE] search_best_answer: {e}")
        return jsonify({
            "answer": "Errore in ricerca. Riprova o ricarica l'indice.",
            "found": False, "score": 0
        }), 200

    return jsonify({
        "answer": answer or "",
        "found": bool(found),
        "score": float(score or 0),
        "path": path,
        "line": line
    }), 200

if __name__ == "__main__":
    # Per sviluppo locale
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
