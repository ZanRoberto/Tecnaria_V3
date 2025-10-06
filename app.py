# app.py — Tecnaria QA Bot (WEB ➜ SINAPSI ➜ fallback) — no KB locale
# - POST /api/ask  → {"ok": true, "html": "<div class='card'>…</div>"}
# - GET  /health   → stato
# - POST /admin/reload (Bearer ADMIN_TOKEN) → ricarica static/data/sinapsi_rules.json senza redeploy
#
# Compatibile con:
# fastapi==0.115.0, uvicorn[standard]==0.30.6, gunicorn==21.2.0, requests==2.32.3, beautifulsoup4==4.12.3, jinja2==3.1.4

import os
import re
import json
import time
import html
import unicodedata
from pathlib import Path
from typing import List, Dict, Any, Tuple

import requests
from fastapi import FastAPI, Body, Header, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

APP_TITLE = "Tecnaria – Assistente Tecnico"
app = FastAPI(title=APP_TITLE)

# ============================== CONFIG ==============================
STATIC_DIR = os.environ.get("STATIC_DIR", "static")
SINAPSI_FILE = os.environ.get("SINAPSI_FILE", os.path.join(STATIC_DIR, "data", "sinapsi_rules.json"))

# WEB first
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")  # se vuota, web disabilitato
PREFERRED_DOMAINS = [d.strip() for d in os.environ.get(
    "PREFERRED_DOMAINS",
    "tecnaria.com,www.tecnaria.com,spit.eu,spitpaslode.com"
).split(",") if d.strip()]

WEB_RESULTS_COUNT_PREFERRED = int(os.environ.get("WEB_RESULTS_COUNT_PREFERRED", "5"))
WEB_RESULTS_COUNT_FALLBACK  = int(os.environ.get("WEB_RESULTS_COUNT_FALLBACK",  "3"))
WEB_FRESHNESS_DAYS          = os.environ.get("WEB_FRESHNESS_DAYS", "365d")  # 365d, month, etc.

# Admin
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")  # opzionale per /admin/reload

# ============================== STATO ==============================
SINAPSI: Dict[str, Any] = {"rules": [], "exclude_any_q": [r"\bprezz\w*", r"\bcost\w*", r"\bpreventiv\w*", r"\boffert\w*"]}
SINAPSI_COMPILED: List[Dict[str, Any]] = []

# ============================== UTILS ==============================
def _safe_read(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="ignore")

def _normalize_text(s: str) -> str:
    s = s or ""
    s = s.strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^\w\s/.\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _compile_sinapsi():
    """Compila regex e ordina per priorità discendente."""
    global SINAPSI_COMPILED
    SINAPSI_COMPILED = []
    rules = SINAPSI.get("rules", []) or []
    for r in rules:
        patt = str(r.get("pattern", "") or "").strip()
        if not patt:
            continue
        try:
            rx = re.compile(patt, re.I | re.S)
        except re.error:
            continue
        ans  = str(r.get("answer", "") or "").strip()
        if not ans:
            continue
        mode = str(r.get("mode", "augment")).lower().strip()
        prio = r.get("priority", 0)
        SINAPSI_COMPILED.append({
            "id": r.get("id"),
            "mode": mode,
            "answer": ans,
            "rx": rx,
            "priority": prio
        })
    SINAPSI_COMPILED.sort(key=lambda x: x["priority"], reverse=True)

def _load_sinapsi():
    """Carica/ricompila Sinapsi dal file configurato."""
    global SINAPSI
    raw = _safe_read(SINAPSI_FILE)
    if not raw.strip():
        _compile_sinapsi()
        return
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            SINAPSI = {
                "rules": data.get("rules", []) or [],
                "exclude_any_q": data.get("exclude_any_q", SINAPSI.get("exclude_any_q", []))
            }
        elif isinstance(data, list):
            SINAPSI = {"rules": data, "exclude_any_q": SINAPSI.get("exclude_any_q", [])}
        else:
            SINAPSI = {"rules": [], "exclude_any_q": SINAPSI.get("exclude_any_q", [])}
    except Ex
