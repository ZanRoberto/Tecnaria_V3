# app.py — FastAPI + intent router Tecnaria (versione compatta e robusta)
import time
import json
import re
import unicodedata
import os
from pathlib import Path
from typing import Dict, Any, Tuple

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ==========================
# Setup di base
# ==========================
APP_VERSION = "2025-10-21.patch1"
BASE_DIR = Path(__file__).parent

app = FastAPI(title="Tecnaria QA API", version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================
# Utility + caricamento JSON
# ==========================
def _norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()

def _load_json(name: str):
    """Carica un JSON se esiste, altrimenti ritorna {} (mai crashare)."""
    p = BASE_DIR / name
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if data is not None else {}
    except Exception:
        return {}

# Contenuti (opzionali: se mancano, abbiamo fallback)
KB_OVERVIEW: Dict[str, Any] = _load_json("tecnaria_overviews.json")       # es: {"cem-e": "...", "ctf": "...", ...}
KB_GTS: Dict[str, Any]      = _load_json("tecnaria_gts_qa500.json")       # opzionale

# Se più avanti avrai file dedicati, decommenta e usa:
# KB_P560: Dict[str, Any]     = _load_json("p560_qa.json")
# KB_CTL: Dict[str, Any]      = _load_json("ctl_qa.json")
# KB_CTF: Dict[str, Any]      = _load_json("ctf_qa.json")


# ==========================
# Blocchi di risposta forti
# ==========================
def _vcem_preforo_best_practice() -> str:
    return (
        "VCEM su essenze dure: sì, serve preforo.\n"
        "Indicazione pratica: diametro preforo ≈ 70–80% del diametro della vite (in funzione della densità del legno).\n"
        "Motivo: riduce il rischio di fessurazioni e rende più regolari coppie/tenuta.\n"
        "Riferimenti: schede VCEM / manuali di posa Tecnaria."
    )

def _kb_overview(key: str, fallback: str) -> str:
    """Pesca una sintesi dal JSON `tecnaria_overviews.json` se presente, altrimenti usa fallback."""
    if isinstance(KB_OVERVIEW, dict):
        for cand in (key, key.upper(), key.capitalize()):
            v = KB_OVERVIEW.get(cand)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return fallback

def _checklist(label: str) -> str:
    label = label.upper()
    if label == "CEM-E":
        return ("Checklist CEM-E (rapida):\n"
                "• Sistema a secco, senza resine, per laterocemento (famiglia CTCEM/VCEM).\n"
                "• Verifica supporto laterizio/calcestruzzo; geometrie e tolleranze.\n"
                "• Attrezzatura idonea; rispetto schede/ETA.\n"
                "• Controlli in corso d’opera; documentazione finale.")
    if label == "VCEM":
        return ("Checklist VCEM (rapida):\n"
                "• Viti su legno: su essenze dure eseguire preforo ≈ 70–80% Ø vite.\n"
                "• Profondità/angolo secondo schede; coppie controllate.\n"
                "• Pulizia fori; DPI; registrare serraggi.")
    if label == "CTCEM":
        return ("Checklist CTCEM (rapida):\n"
                "• Sistema CEM-E a secco, senza resine, per laterocemento.\n"
                "• Incisione per piastra dentata; preforo Ø indicato in scheda.\n"
                "• Alloggiamento corretto; controlli e registri di posa.")
    if label == "CTF":
        return ("Checklist CTF (rapida):\n"
                "• Posa con SPIT P560 + kit/adattatori Tecnaria.\n"
                "• Ogni connettore: 2 chiodi HSBR14; selezionare propulsori in base al supporto.\n"
                "• Prove preliminari; sicurezza e DPI.")
    if label == "P560":
        return ("Checklist P560 (rapida):\n"
                "• Chiodatrice SPIT P560 per connettori CTF.\n"
                "• Chiodi HSBR14; propulsori idonei; centraggio con adattatore Tecnaria.\n"
                "• Formazione operatore; prove di qualifica.")
    if label == "CTL":
        return ("Checklist CTL (rapida):\n"
                "• Collaborazione legno–calcestruzzo con soletta in c.a.\n"
                "• Verifica stato legno, umidità, ripristini locali.\n"
                "• Coppie controllate; schema di posizionamento da progetto.")
    if label == "GTS":
        return ("Checklist GTS (rapida):\n"
                "• Manicotto metallico filettato per giunzioni meccaniche a secco.\n"
                "• Prefori e tolleranze; uso di dadi/rondelle idonei.\n"
                "• Serraggi controllati; documentazione allegata.")
    return "Checklist non disponibile."

def _four_points_compare(a: str, b: str) -> str:
    oa = _kb_overview(a, a.upper())
    ob = _kb_overview(b, b.upper())
    return (
        f"Confronto {a.upper()} vs {b.upper()} (4 punti):\n"
        f"1) Ambito d’uso – {a.upper()}: {oa[:220]}...\n"
        f"   {b.upper()}: {ob[:220]}...\n"
        f"2) Posa – {a.upper()}: a secco/meccanica ove previsto; {b.upper()}: idem secondo schede.\n"
        f"3) Pro/Contro – dipende da accessibilità, ripetibilità, controllabilità e interferenze di cantiere.\n"
        f"4) Documentazione – consultare ETA/schede e manuali Tecnaria per il caso specifico."
    )


# ==========================
# Intent Router
# ==========================
def route_and_answer(q: str) -> Tuple[str, str]:
    """
    Ritorna (text, match_id) in base agli intenti riconosciuti.
    match_id è una stringa utile per i tuoi report (colori/score).
    """
    t = _norm(q)
    if not t:
        return ("Domanda vuota.", "EMPTY")

    # 1) Regola forte: VCEM + preforo/essenze dure/70-80
    if ("vcem" in t) and (("preforo" in t) or ("essenze dure" in t) or ("70" in t and "80" in t) or ("legno" in t and "vite" in t)):
        return (_vcem_preforo_best_practice(), "VCEM-Q-HARDWOOD-PREFORO")

    # 2) Checklist / lista
    if ("checklist" in t) or ("lista" in t):
        for key in ("vcem", "ctcem", "cem-e", "ctf", "p560", "ctl", "gts"):
            if key in t:
                return (_checklist("cem-e" if key == "cem-e" else key), f"CHECKLIST::{key.upper()}")

    # 3) Overview / “che cos’è” / introduzione / quadro
    if any(k in t for k in ("overview", "che cos", "introduzione", "quadro", "spiega", "in sintesi")):
        for k, name in (("cem-e", "cem-e"), ("ctcem", "ctcem"), ("vcem", "vcem"),
                        ("ctf", "ctf"), ("p560", "p560"), ("ctl", "ctl"), ("gts", "gts")):
            if k in t:
                fallback = f"{name.upper()}: sistema/soluzione Tecnaria; vedere schede/ETA."
                return (_kb_overview(name, fallback), f"OVERVIEW::{name.upper()}")

    # 4) Confronti (CF-…)
    pairs = [("ctf", "gts"), ("ctf", "p560"), ("ctl", "vcem"), ("ctl", "cem-e"),
             ("ctcem", "gts"), ("vcem", "p560"), ("ctf", "cem-e"), ("ctcem", "p560"),
             ("ctl", "ctcem"), ("cem-e", "gts"), ("cem-e", "p560")]
    for a, b in pairs:
        if a in t and b in t:
            return (_four_points_compare(a, b), f"COMPARE::{a.upper()}_VS_{b.upper()}")

    # 5) CEM-E: errori / cosa controllare (diversi casi “rossi/gialli”)
    if "cem-e" in t:
        if ("errori" in t) or ("evitare" in t):
            return (
                "Errori comuni CEM-E:\n"
                "• Prefori/tolleranze non conformi;\n"
                "• Documentazione carente (ETA/schede);\n"
                "• Controlli inadeguati su campioni e in corso d’opera.\n"
                "Buone pratiche: seguire schede/ETA, controlli costanti, registri di posa.",
                "CEME-ERRORI-BASE"
            )
        if "controllare" in t:
            return (
                "Cosa controllare con CEM-E:\n"
                "• Idoneità supporto laterizio/calcestruzzo e geometrie;\n"
                "• Attrezzature e accessori corretti;\n"
                "• Posa a secco (senza resine) secondo schede e indicazioni della DL.\n"
                "• Documentazione: ETA/schede Tecnaria, registri di posa.",
                "CEME-CONTROLLI-BASE"
            )

    # 6) Fallback finale (mai <VUOTO>)
    return (
        _kb_overview("cem-e", "Non trovo un blocco preciso: indica il sistema (CEM-E/CTCEM/VCEM/CTF/P560/CTL/GTS) o aggiungi parole chiave tecniche, così ti do risposta puntuale."),
        "FALLBACK::CEME_OVERVIEW"
    )


# ==========================
# API
# ==========================
class AskIn(BaseModel):
    q: str

@app.get("/health")
def health():
    return {"ok": True, "version": APP_VERSION}

@app.get("/version")
def version():
    return {"version": APP_VERSION}

@app.post("/api/ask")
def api_ask(payload: AskIn, request: Request):
    t0 = time.perf_counter()
    text, match_id = route_and_answer(payload.q)
    ms = int((time.perf_counter() - t0) * 1000)
    # Struttura compatibile con il tuo runner (match_id/ms/text)
    return {
        "ok": True,
        "match_id": match_id,
        "ms": ms,
        "text": text
    }

# Facoltativo: avvio diretto (utile su Windows)
if __name__ == "__main__":
    try:
        import uvicorn
        uvicorn.run("app:app", host="0.0.0.0", port=8010, reload=False)
    except Exception as e:
        print("Errore avvio uvicorn:", e)
