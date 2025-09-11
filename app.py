# app.py — Tecnaria · Assistente documentale
import os
import threading
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# ---- Import dal tuo scraper ----
# Devono esistere queste due funzioni in scraper_tecnaria.py:
#   build_index(docs_dir) -> dict con chiavi: blocks, files, lines, data (o quello che usi tu)
#   search_answer(index, question) -> (answer, found, score, path, line)
from scraper_tecnaria import build_index, search_answer

app = Flask(__name__)
CORS(app)

# ---- Stato indice globale ----
INDEX = None
INDEX_READY = False
INDEX_ERR = None
INDEX_LOCK = threading.Lock()

def _docs_dir() -> str:
    # Tieni compatibilità con tutte le variabili che hai usato
    return (
        os.environ.get("DOC_DIR")
        or os.environ.get("DOCS_FOLDER")
        or os.environ.get("KNOWLEDGE_DIR")
        or "documenti_gTab"
    )

def _ensure_index() -> None:
    """Costruisce l'indice se non pronto. Thread-safe."""
    global INDEX, INDEX_READY, INDEX_ERR
    if INDEX_READY and INDEX is not None:
        return
    with INDEX_LOCK:
        # doppio check in caso di corse
        if INDEX_READY and INDEX is not None:
            return
        try:
            docs_dir = _docs_dir()
            print(f"[app] Indicizzazione on-demand da: {docs_dir}")
            idx = build_index(docs_dir)
            # ci aspettiamo che build_index ritorni un dict con almeno .get('blocks','files','lines')
            INDEX = idx
            INDEX_READY = True
            INDEX_ERR = None
            print(f"[app] Indice pronto: blocks={idx.get('blocks',0)} files={idx.get('files',0)} lines={idx.get('lines',0)}")
        except Exception as e:
            INDEX = None
            INDEX_READY = False
            INDEX_ERR = str(e)
            print(f"[app][ERRORE] Indicizzazione fallita: {e}")

def _ensure_index_async():
    """Avvia indicizzazione in background (per REINDEX_ON_STARTUP=1)."""
    t = threading.Thread(target=_ensure_index, daemon=True)
    t.start()

# ---- Avvio indicizzazione all'avvio se richiesto ----
if os.environ.get("REINDEX_ON_STARTUP", "0") == "1":
    _ensure_index_async()

@app.get("/")
def home():
    return render_template("index.html")

@app.get("/health")
def health():
    # Non blocca mai; utile a Render per capire se il processo è vivo
    info = {
        "status": "ok",
        "ready": INDEX_READY,
        "error": INDEX_ERR,
    }
    if INDEX and isinstance(INDEX, dict):
        info.update({
            "blocks": INDEX.get("blocks", 0),
            "files": INDEX.get("files", 0),
            "lines": INDEX.get("lines", 0),
            "docs_dir": _docs_dir(),
        })
    return jsonify(info), 200

@app.post("/reload")  # opzionale: se vuoi un endpoint manuale (anche se UI nuova non lo mostra)
def reload_index():
    global INDEX, INDEX_READY, INDEX_ERR
    try:
        # forza ricostruzione
        with INDEX_LOCK:
            INDEX = None
            INDEX_READY = False
            INDEX_ERR = None
        _ensure_index()  # sincrona
        return jsonify({
            "ok": True,
            "blocks": INDEX.get("blocks", 0) if INDEX else 0,
            "files": INDEX.get("files", 0) if INDEX else 0,
            "lines": INDEX.get("lines", 0) if INDEX else 0,
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.post("/ask")
def ask():
    # 1) leggi domanda
    data = request.get_json(silent=True) or {}
    q = (data.get("question") or "").strip()
    if not q:
        return jsonify({"answer": "", "found": False, "score": 0}), 200

    # 2) assicurati che l'indice esista (on-demand)
    if not INDEX_READY or INDEX is None:
        _ensure_index()
        if not INDEX_READY or INDEX is None:
            # se comunque fallisce, rispondi chiaro ma non bloccare la UI
            msg = "Indice non disponibile. Carica i file .txt in documenti_gTab/ e riprova."
            if INDEX_ERR:
                msg += f" (Dettaglio: {INDEX_ERR})"
            return jsonify({"answer": msg, "found": False, "score": 0}), 200

    # 3) cerca risposta
    try:
        answer, found, score, path, line = search_answer(INDEX, q)
    except Exception as e:
        # Qualsiasi eccezione lato ricerca non deve bloccare la chat
        print(f"[app][ERRORE] search_answer: {e}")
        return jsonify({
            "answer": "Errore in ricerca. Riprova tra poco o ricarica l'indice.",
            "found": False, "score": 0
        }), 200

    # 4) ritorna JSON pulito all'UI
    return jsonify({
        "answer": answer or "",
        "found": bool(found),
        "score": float(score or 0),
        "path": path,
        "line": line
    }), 200

if __name__ == "__main__":
    # Avvio sviluppo locale
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
