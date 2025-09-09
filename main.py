# -*- coding: utf-8 -*-
from __future__ import annotations
import os

# Importa l'app Flask definita in app.py
from app import app as application  # per gunicorn: main:application
from app import app                 # per avvio con "python main.py"

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
