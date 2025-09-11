import os, glob, requests
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
import scraper_tecnaria as st

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# ============ CONFIG DI BASE ============
def _docs_folder() -> str:
    return os.path.abspath(os.environ.get("DOCS_FOLDER", "documenti_gTab"))

# ============ AUTOSYNC GITHUB ============
def github_autosync():
    """
    Se la cartella DOCS_FOLDER è vuota, scarica tutti i .txt dalla repo GitHub.
    Usa API pubblica dei contenuti (branch main per default).
    """
    folder = _docs_folder()
    os.makedirs(folder, exist_ok=True)
    # se ci sono già .txt, non fare nulla
    if glob.glob(os.path.join(folder, "*.txt")):
        return

    owner  = os.environ.get("GITHUB_OWNER",  "ZanRoberto")
    repo   = os.environ.get("GITHUB_REPO",   "Tecnaria_V3")
    subdir = os.environ.get("GITHUB_DIR",    "documenti_gTab")
    branch = os.environ.get("GITHUB_BRANCH", "main")
    token  = os.environ.get("GITHUB_TOKEN")  # solo se repo privata

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{subdir}?ref={branch}"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        items = r.json()
        n = 0
        for it in items:
            if it.get("type") == "file" and it.get("name", "").lower().endswith(".txt"):
                dl = it.get("download_url")
                if not dl:
                    continue
                txt = requests.get(dl, timeout=20).text
                out = os.path.join(folder, it["name"])
                with open(out, "w", encoding="utf-8") as f:
                    f.write(txt)
                n += 1
        print(f"[autosync] Scaricati {n} file .txt da GitHub in {folder}")
    except Exception as e:
        print(f"[autosync][WARN] Fallito download contenuti da GitHub: {e}")

# ============ SEED MANUALE (OPZIONALE) ============
@app.route("/seed", methods=["GET", "POST"])
def seed_files():
    """
    Crea due file minimi (P560 + Contatti) nella DOCS_FOLDER.
    Utile solo come emergenza.
    """
    folder = _docs_folder()
    os.makedirs(folder, exist_ok=True)

    p1 = os.path.join(folder, "P560.txt")
    with open(p1, "w", encoding="utf-8") as f:
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

    p2 = os.path.join(folder, "ChiSiamo_ContattiOrari.txt")
    with open(p2, "w", encoding="utf-8") as f:
        f.write(
            "[TAGS: contatti, orari, sede]\n\n"
            "D: Quali sono gli orari e i contatti?\n"
            "R: Sede: Viale Pecori Giraldi 55, 36061 Bassano del Grappa (VI). "
            "Telefono: +39 0424 502029. Email: info@tecnaria.com. Orari: lun–ven 8:30–12:30, 14:00–18:00.\n"
        )

    return jsonify({"status": "ok", "created": [p1, p2]})

# ============ COSTRUZIONE INDICE ALL'AVVIO ============
REINDEX_ON_STARTUP = os.environ.get("REINDEX_ON_STARTUP", "1") == "1"

# 1) prova a scaricare i .txt dalla repo se la cartella è vuota
github_autosync()

# 2) costruisci l'indice
if REINDEX_ON_STARTUP:
    st.build_index()

# ============ DIAGNOSTICA ============
@app.get("/health")
def health():
    info = st.list_index()
    return jsonify({
        "status": "ok",
        "docs": info.get("count", 0),
        "lines": info.get("lines", 0),
        "blocks": info.get("blocks", 0),
        "ts": info.get("ts", 0)
    })

@app.get("/ls")
def ls():
    info = st.list_index()
    return jsonify({"status": "ok", **info})

@app.get("/files")
def files_on_disk():
    folder = _docs_folder()
    files = sorted(glob.glob(os.path.join(folder, "*.txt")))
    return jsonify({"folder": folder, "count": len(files), "files": files})

# ============ RICARICA INDICE / Q&A ============
@app.post("/reload")
def reload_index():
    info = st.reload_index()
    # la UI legge 'docs', quindi mappo count -> docs
    return jsonify({
        "status": "reloaded",
        "docs": info.get("count", 0),
        "lines": info.get("lines", 0),
        "blocks": info.get("blocks", 0)
    })

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

# ============ UI ============
@app.get("/")
def home():
    return render_template("index.html")

@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
