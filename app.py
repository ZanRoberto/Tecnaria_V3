# app.py
import os, glob
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
import scraper_tecnaria as st

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# === Reload indice all'avvio ===
REINDEX_ON_STARTUP = os.environ.get("REINDEX_ON_STARTUP", "1") == "1"
if REINDEX_ON_STARTUP:
    st.build_index()

# ========== ROUTE DIAGNOSTICA ==========
@app.get("/health")
def health():
    info = st.list_index()
    return jsonify({"status": "ok",
                    "docs": info.get("count", 0),
                    "lines": info.get("lines", 0),
                    "blocks": info.get("blocks", 0),
                    "ts": info.get("ts", 0)})

@app.get("/ls")
def ls():
    info = st.list_index()
    return jsonify({"status": "ok", **info})

@app.get("/files")
def files_on_disk():
    folder = os.path.abspath(os.environ.get("DOCS_FOLDER", "documenti_gTab"))
    files = sorted(glob.glob(os.path.join(folder, "*.txt")))
    return jsonify({"folder": folder, "count": len(files), "files": files})

# ========== HOTFIX: CREA FILE DI EMERGENZA ==========
@app.route("/seed", methods=["GET", "POST"])
def seed_files():
    folder = os.path.abspath(os.environ.get("DOCS_FOLDER", "documenti_gTab"))
    os.makedirs(folder, exist_ok=True)

    p560 = os.path.join(folder, "P560.txt")
    with open(p560, "w", encoding="utf-8") as f:
        f.write(
            "[TAGS: P560, pistola, sparachiodi, posa CTF, posa Diapason, fissaggio connettori, accessori P560, noleggio]\n\n"
            "D: Che cos’è la P560?\n"
            "R: La P560 è la pistola sparachiodi a cartuccia propulsiva usata per fissare i connettori CTF e Diapason senza saldatura. "
            "Riduce i tempi di posa e garantisce fissaggi ripetibili; disponibili noleggio e accessori dedicati (guidapunte, pistone, anello ammortizzatore, chiodi SBR14).\n\n"
            "D: Quali sono i vantaggi della P560?\n"
            "R: Rapidità di posa, ripetibilità del fissaggio, niente saldatura, maggiore sicurezza su lamiera grecata e travi d’acciaio.\n\n"
            "D: È possibile noleggiare la P560?\n"
            "R: Sì, oltre all’acquisto è previsto il noleggio a breve termine.\n"
        )

    contatti = os.path.join(folder, "ChiSiamo_ContattiOrari.txt")
    with open(contatti, "w", encoding="utf-8") as f:
        f.write(
            "[TAGS: contatti, orari, sede]\n\n"
            "D: Quali sono gli orari e i contatti?\n"
            "R: Sede: Viale Pecori Giraldi 55, 36061 Bassano del Grappa (VI). "
            "Telefono: +39 0424 502029. Email: info@tecnaria.com. Orari: lun–ven 8:30–12:30, 14:00–18:00.\n"
        )

    return jsonify({"status":"ok","created":[p560, contatti]})

# ========== API RICARICA / Q&A ==========
@app.post("/reload")
def reload_index():
    info = st.reload_index()
    return jsonify({"status": "reloaded", **info})

@app.post("/ask")
def ask():
    data = request.get_json(silent=True) or {}
    q = (data.get("q") or "").strip()
    if not q:
        return jsonify({"ok": False, "answer": "Inserisci una domanda.", "debug": {}}), 400
    res = st.search_best_answer(q)
    if not res["found"]:
        return jsonify({"ok": False, "answer": "Non ho trovato una risposta precisa. Prova con una formulazione leggermente diversa.", "debug": res})
    return jsonify({"ok": True, "answer": res["answer"], "debug": res})

# ========== UI ==========
@app.get("/")
def home():
    return render_template("index.html")

@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
