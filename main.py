# -*- coding: utf-8 -*-
"""
main.py — shim di avvio per Render.
Richiama l'app Flask definita in app.py e permette di usare Gunicorn (main:application).
Se vuoi aggiungere regole/route speciali (HTML con immagini/link), puoi importarle qui.
"""

import os
from app import app as application  # alias per gunicorn: "gunicorn main:application"
from app import app

# ====== (Opzionale) Route speciali ======
# Se vuoi mantenere risposte HTML dedicate (es. connettori CTL Omega),
# crea un file rules_html.py e registra qui le sue route:
#
# from rules_html import register_rules_routes
# register_rules_routes(app)

# ====== Healthcheck ======
@app.route("/health", methods=["GET"])
def health():
    return "ok", 200

# ====== Avvio locale ======
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
