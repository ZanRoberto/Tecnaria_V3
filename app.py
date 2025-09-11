# --- HOTFIX: seed e listing file sul server ---
import os, glob
from flask import jsonify

@app.get("/files")
def files_on_disk():
    folder = os.path.abspath(os.environ.get("DOCS_FOLDER", "documenti_gTab"))
    files = sorted(glob.glob(os.path.join(folder, "*.txt")))
    return jsonify({"folder": folder, "count": len(files), "files": files})

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
    return jsonify({"status":"ok","created":[p560]})
