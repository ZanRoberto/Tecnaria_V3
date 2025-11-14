import os
import json
import difflib
from pathlib import Path
from typing import List, Dict, Any, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel


# ------------------------------------------------------------------------------
# Config di base
# ------------------------------------------------------------------------------

APP_NAME = "TECNARIA-IMBUTO"
DATA_DIR = Path("static/data")
FAMILY_FILES = [
    "CTF.json",
    "VCEM.json",
    "CTCEM.json",
    "CTL.json",
    "CTL_MAXI.json",
    "DIAPASON.json",
    "P560.json",
    "COMM.json",
]

IMBUTO_STAGES = ["top", "middle", "bottom", "post"]

app = FastAPI(title=APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------------------
# Modelli Pydantic
# ------------------------------------------------------------------------------

class AskRequest(BaseModel):
    question: str
    lang: Optional[str] = "it"
    debug: Optional[bool] = False


class AskResponse(BaseModel):
    answer: str
    family: Optional[str] = None
    stage: Optional[str] = None
    match: Optional[Dict[str, Any]] = None
    debug: Optional[str] = ""


# ------------------------------------------------------------------------------
# Caricamento KB GOLD
# ------------------------------------------------------------------------------

KB_GOLD: List[Dict[str, Any]] = []


def _load_json_items(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # può essere lista pura o dict con chiave "items"
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and "items" in data:
        items = data["items"]
    else:
        items = []

    # normalizza un minimo
    norm_items: List[Dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        # family obbligatoria per debug / instradamento
        if "family" not in item:
            # prova a dedurre dal file se presente
            item["family"] = item.get("famiglia") or path.stem.upper()
        # id di backup
        if "id" not in item:
            item["id"] = f"{item['family']}-AUTO"
        norm_items.append(item)
    return norm_items


def load_kb() -> List[Dict[str, Any]]:
    all_items: List[Dict[str, Any]] = []
    for fname in FAMILY_FILES:
        full = DATA_DIR / fname
        items = _load_json_items(full)
        all_items.extend(items)
    return all_items


def extract_answer(item: Dict[str, Any]) -> str:
    # Prova varie chiavi possibili per la risposta GOLD
    for key in (
        "answer",
        "gold_answer",
        "gold",
        "risposta",
        "risposta_gold",
        "body",
        "it",
    ):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # fallback di emergenza
    return item.get("answer", "") or ""


def score_item(question: str, item: Dict[str, Any]) -> float:
    q = question.lower()
    parts: List[str] = []
    q_item = item.get("question") or item.get("domanda") or ""
    parts.append(str(q_item))
    triggers = item.get("triggers") or item.get("keywords") or []
    if isinstance(triggers, list):
        parts.extend([str(t) for t in triggers])
    tags = item.get("tags") or []
    if isinstance(tags, list):
        parts.extend([str(t) for t in tags])

    text = " ".join(parts).lower()
    if not text:
        return 0.0
    return difflib.SequenceMatcher(None, q, text).ratio()


def match_item(
    question: str,
    kb: List[Dict[str, Any]],
    family: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    candidates = (
        [item for item in kb if item.get("family") == family]
        if family
        else kb
    )
    if not candidates:
        candidates = kb

    best_item = None
    best_score = -1.0
    for item in candidates:
        s = score_item(question, item)
        if s > best_score:
            best_score = s
            best_item = item

    if not best_item:
        return None

    best_item = dict(best_item)
    best_item["_score"] = round(best_score, 3)
    return best_item


def classify_imbuto(question: str) -> Dict[str, Optional[str]]:
    """
    Instradamento molto sobrio:
    - stage sempre 'top' per ora
    - family stimata solo con parole chiave grosse
    Tutto il resto resta interno, non esposto in UI.
    """
    q = question.lower()

    # famiglie per parole chiave forti
    if "p560" in q or "p 560" in q or "p-560" in q or "pistola a sparo" in q:
        family = "P560"
    elif "ctf" in q or "lamiera grecata" in q or "trave in acciaio" in q:
        family = "CTF"
    elif "ctl maxi" in q or "ctlmaxi" in q:
        family = "CTL_MAXI"
    elif "ctl" in q:
        family = "CTL"
    elif "v  c e m" in q or "vcem" in q:
        family = "VCEM"
    elif "ctcem" in q:
        family = "CTCEM"
    elif "diapason" in q:
        family = "DIAPASON"
    elif "ordine" in q or "preventivo" in q or "commerciale" in q:
        family = "COMM"
    else:
        family = None  # ricerca globale

    return {
        "stage": "top",
        "family": family,
    }


def answer_question(req: AskRequest) -> AskResponse:
    question = (req.question or "").strip()
    if not question:
        return AskResponse(
            answer="Inserisci una domanda di cantiere o di progetto.",
            family=None,
            stage=None,
            match=None,
            debug="",
        )

    imbuto = classify_imbuto(question)
    match = match_item(question, KB_GOLD, family=imbuto.get("family"))
    if not match:
        return AskResponse(
            answer="Non ho trovato una risposta adeguata nei dati GOLD. "
                   "Prova a riformulare la domanda con più dettagli sul solaio e sui connettori.",
            family=None,
            stage=imbuto.get("stage"),
            match=None,
            debug="",
        )

    answer = extract_answer(match)
    family = match.get("family")
    stage = imbuto.get("stage")

    debug_text = ""
    if req.debug:
        debug_text = (
            f"IMBUTO interno: stage={stage} · family={imbuto.get('family') or 'auto'}\n"
            f"MATCH: id={match.get('id')} · family={family} · "
            f"score={match.get('_score')} · question='{match.get('question') or match.get('domanda')}'"
        )

    match_public = {
        "id": match.get("id"),
        "family": family,
        "score": match.get("_score"),
        "question": match.get("question") or match.get("domanda"),
    }

    return AskResponse(
        answer=answer,
        family=family,
        stage=stage,
        match=match_public,
        debug=debug_text,
    )


# ------------------------------------------------------------------------------
# Startup: carica KB
# ------------------------------------------------------------------------------

@app.on_event("startup")
def _startup_event() -> None:
    global KB_GOLD
    KB_GOLD = load_kb()
    print(f"[IMBUTO] Caricati {len(KB_GOLD)} blocchi GOLD (famiglie + consolidato se presente).")


# ------------------------------------------------------------------------------
# API REST
# ------------------------------------------------------------------------------

@app.get("/api/config")
def api_config():
    return {
        "app": APP_NAME,
        "gold_items": len(KB_GOLD),
        "families": sorted({item.get("family") for item in KB_GOLD}),
        "imbuto_stages": IMBUTO_STAGES,
    }


@app.post("/api/ask", response_model=AskResponse)
def api_ask(req: AskRequest):
    resp = answer_question(req)
    return JSONResponse(content=resp.model_dump())


# ------------------------------------------------------------------------------
# UI HTML (dark, aziendale, nessun segreto esposto)
# ------------------------------------------------------------------------------

HTML_PAGE = """
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="utf-8" />
    <title>TECNARIA Sinapsi — Q/A</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <style>
        :root {
            color-scheme: dark;
            --bg: #050814;
            --bg-card: #121827;
            --bg-input: #0b1020;
            --accent: #ff7a3c;
            --accent-soft: rgba(255,122,60,0.12);
            --text: #f9fafb;
            --text-soft: #9ca3af;
            --border-subtle: #1f2937;
            --pill-bg: #111827;
            --pill-soft: #1f2937;
            --pill-text: #e5e7eb;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: radial-gradient(circle at top, #1f2937 0, #050814 55%);
            color: var(--text);
        }
        .page {
            max-width: 1240px;
            margin: 24px auto;
            padding: 8px 16px 32px;
        }
        .header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 16px 20px;
            background: linear-gradient(90deg, #fb923c, #f97316);
            border-radius: 18px;
            box-shadow: 0 18px 40px rgba(0,0,0,0.45);
        }
        .header-left {
            display: flex;
            align-items: center;
            gap: 14px;
        }
        .avatar {
            width: 40px;
            height: 40px;
            border-radius: 999px;
            background: rgba(15,23,42,0.18);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 20px;
            color: #111827;
        }
        .header-title {
            font-weight: 700;
            font-size: 20px;
            letter-spacing: 0.01em;
        }
        .header-sub {
            font-size: 13px;
            color: rgba(17,24,39,0.9);
        }
        .badge {
            padding: 6px 12px;
            border-radius: 999px;
            font-size: 12px;
            background: rgba(15,23,42,0.14);
            color: #f9fafb;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }
        .dot {
            width: 8px;
            height: 8px;
            border-radius: 999px;
            background: #22c55e;
        }
        .badge-secondary {
            margin-left: 8px;
            background: rgba(15,23,42,0.26);
        }
        .main {
            margin-top: 18px;
            background: rgba(15,23,42,0.9);
            border-radius: 18px;
            border: 1px solid rgba(31,41,55,0.9);
            padding: 16px 18px 18px;
            display: grid;
            grid-template-columns: minmax(0, 1.05fr) minmax(0, 1.1fr);
            gap: 14px;
        }
        @media (max-width: 900px) {
            .main {
                grid-template-columns: minmax(0, 1fr);
            }
        }
        .panel {
            background: radial-gradient(circle at top left, #111827 0, #020617 60%);
            border-radius: 14px;
            padding: 14px 14px 16px;
            border: 1px solid var(--border-subtle);
        }
        .panel-header {
            font-size: 13px;
            font-weight: 600;
            color: var(--text-soft);
            margin-bottom: 6px;
        }
        .pill-row {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: 10px;
            margin-bottom: 3px;
        }
        .pill {
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 11px;
            background: var(--pill-bg);
            border: 1px solid #1f2937;
            color: var(--pill-text);
            opacity: 0.92;
        }
        .pill-soft {
            background: var(--accent-soft);
            border-color: rgba(248,113,113,0.4);
            color: #fed7aa;
        }
        .system-box {
            font-size: 13px;
            line-height: 1.5;
            color: var(--text-soft);
            padding: 10px 11px;
            border-radius: 10px;
            background: rgba(15,23,42,0.95);
            border: 1px dashed #1f2937;
        }
        .textarea-wrapper {
            margin-top: 12px;
        }
        textarea {
            width: 100%;
            min-height: 170px;
            resize: vertical;
            padding: 10px 11px;
            border-radius: 10px;
            border: 1px solid var(--border-subtle);
            outline: none;
            background: var(--bg-input);
            color: var(--text);
            font-size: 14px;
            line-height: 1.5;
        }
        textarea::placeholder {
            color: #6b7280;
        }
        .textarea-footer {
            margin-top: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 11px;
            color: var(--text-soft);
        }
        .cta-row {
            display: flex;
            gap: 6px;
            align-items: center;
        }
        .chip {
            padding: 5px 10px;
            border-radius: 999px;
            background: #020617;
            border: 1px solid #1f2937;
            font-size: 11px;
            cursor: pointer;
            color: #e5e7eb;
        }
        .chip:hover {
            border-color: #4b5563;
        }
        .btn-primary {
            border-radius: 999px;
            padding: 8px 20px;
            font-size: 14px;
            font-weight: 600;
            border: none;
            cursor: pointer;
            background: linear-gradient(135deg, #fb923c, #f97316);
            color: #111827;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            box-shadow: 0 12px 25px rgba(0,0,0,0.55);
        }
        .btn-primary span.icon {
            display: inline-block;
            transform: translateY(1px);
        }
        .btn-primary:active {
            transform: translateY(1px);
            box-shadow: 0 6px 16px rgba(0,0,0,0.7);
        }
        .resp-box {
            width: 100%;
            min-height: 220px;
            max-height: 420px;
            padding: 10px 11px;
            border-radius: 10px;
            border: 1px solid var(--border-subtle);
            background: radial-gradient(circle at top left, #020617 0, #020617 60%);
            font-size: 14px;
            line-height: 1.6;
            overflow-y: auto;
            white-space: pre-wrap;
        }
        .resp-header-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
            font-size: 12px;
            color: var(--text-soft);
        }
        .debug-toggle {
            font-size: 11px;
            display: inline-flex;
            align-items: center;
            gap: 4px;
            cursor: pointer;
        }
        .debug-toggle input {
            accent-color: var(--accent);
        }
        .debug-box {
            margin-top: 6px;
            padding: 7px 8px;
            border-radius: 8px;
            background: #020617;
            border: 1px dashed #374151;
            font-size: 11px;
            color: #9ca3af;
            max-height: 150px;
            overflow-y: auto;
            display: none;
            white-space: pre-wrap;
        }
        .footer-note {
            margin-top: 10px;
            font-size: 11px;
            color: #6b7280;
        }
    </style>
</head>
<body>
<div class="page">
    <header class="header">
        <div class="header-left">
            <div class="avatar">T</div>
            <div>
                <div class="header-title">TECNARIA Sinapsi</div>
                <div class="header-sub">
                    Assistente GOLD strutturale · connettori &amp; sistemi misti
                </div>
            </div>
        </div>
        <div>
            <span class="badge">
                <span class="dot"></span>
                GOLD attivo
                <span id="datasetLabel" class="badge badge-secondary" style="margin-left:8px;">dataset: caricamento...</span>
            </span>
        </div>
    </header>

    <main class="main">
        <!-- PANNELLO SINISTRO: DOMANDA -->
        <section class="panel">
            <div class="panel-header">Sistema · init · mode: GOLD</div>
            <div class="system-box">
                Benvenuto in Tecnaria Sinapsi. Modalità GOLD attiva: scrivi una domanda reale di cantiere
                su connettori e sistemi misti (CTF, VCEM, CTCEM, CTL, CTL MAXI, DIAPASON, P560, ecc.).
            </div>

            <div class="pill-row" style="margin-top:12px;">
                <div class="pill pill-soft">Instradamento automatico · famiglie connettori</div>
                <div class="pill">CTF</div>
                <div class="pill">VCEM</div>
                <div class="pill">CTCEM</div>
                <div class="pill">CTL</div>
                <div class="pill">CTL MAXI</div>
                <div class="pill">DIAPASON</div>
                <div class="pill">P560</div>
                <div class="pill">COMM</div>
            </div>

            <div class="textarea-wrapper">
                <textarea id="questionInput"
                          placeholder="Es. &quot;Quando devo usare il CTL MAXI su un solaio misto legno-calcestruzzo?&quot;"></textarea>
            </div>

            <div class="textarea-footer">
                <div>
                    GOLD = risposta completa strutturale. Scrivi CANONICO: all'inizio se vuoi una risposta più sintetica.
                </div>
                <div class="cta-row">
                    <button class="chip" onclick="fillHint('Uso CTF')">Uso CTF</button>
                    <button class="chip" onclick="fillHint('Uso P560')">P560 &amp; fissaggi a sparo</button>
                    <button class="chip" onclick="fillHint('Confronto CTL vs CTL MAXI')">CTL vs CTL MAXI</button>
                    <button class="btn-primary" onclick="sendQuestion()">
                        <span>Chiedi</span>
                        <span class="icon">➜</span>
                    </button>
                </div>
            </div>

            <div class="footer-note">
                Suggerimento: descrivi sempre tipo di solaio, travi, spessori, luce delle campate e problemi
                (fessurazioni, umidità, degrado).
            </div>
        </section>

        <!-- PANNELLO DESTRO: RISPOSTA -->
        <section class="panel">
            <div class="resp-header-row">
                <div>Risposta GOLD</div>
                <label class="debug-toggle">
                    <input id="debugCheckbox" type="checkbox" />
                    <span>Debug interno (solo per te)</span>
                </label>
            </div>
            <div id="answerBox" class="resp-box">
                In attesa della domanda...
            </div>
            <div id="debugBox" class="debug-box"></div>
        </section>
    </main>
</div>

<script>
async function loadConfig() {
    try {
        const res = await fetch("/api/config");
        if (!res.ok) return;
        const data = await res.json();
        const label = document.getElementById("datasetLabel");
        if (Array.isArray(data.families)) {
            label.textContent = "dataset: GOLD · famiglie " + data.families.join(", ");
        } else {
            label.textContent = "dataset: GOLD";
        }
    } catch (e) {
        console.warn("Config error", e);
    }
}

function fillHint(text) {
    const el = document.getElementById("questionInput");
    if (!el) return;
    if (!el.value.trim()) {
        el.value = text;
    } else {
        el.value = el.value.trim() + " " + text;
    }
    el.focus();
}

async function sendQuestion() {
    const qEl = document.getElementById("questionInput");
    const aEl = document.getElementById("answerBox");
    const dEl = document.getElementById("debugBox");
    const debugOn = document.getElementById("debugCheckbox").checked;

    const question = (qEl.value || "").trim();
    if (!question) {
        aEl.textContent = "Inserisci una domanda di cantiere o di progetto.";
        dEl.style.display = "none";
        dEl.textContent = "";
        return;
    }

    aEl.textContent = "Sto elaborando la risposta GOLD...";
    dEl.style.display = "none";
    dEl.textContent = "";

    try {
        const res = await fetch("/api/ask", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ question: question, lang: "it", debug: debugOn })
        });
        if (!res.ok) {
            aEl.textContent = "Errore backend (" + res.status + ").";
            return;
        }
        const data = await res.json();
        aEl.textContent = data.answer || "Nessuna risposta disponibile.";

        if (debugOn && data.debug) {
            dEl.textContent = data.debug;
            dEl.style.display = "block";
        } else {
            dEl.style.display = "none";
            dEl.textContent = "";
        }
    } catch (e) {
        console.error(e);
        aEl.textContent = "Errore di comunicazione con il server.";
        dEl.style.display = "none";
        dEl.textContent = "";
    }
}

window.addEventListener("DOMContentLoaded", loadConfig);
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(content=HTML_PAGE)


# ------------------------------------------------------------------------------
# Fallback root info (per chiamate HEAD / GET raw)
# ------------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"app": APP_NAME, "status": "ok", "items": len(KB_GOLD)}
