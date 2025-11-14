import os
import json
import re
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from openai import OpenAI

# --------------------------------------------------
# Config generale
# --------------------------------------------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata nelle variabili d'ambiente.")

client = OpenAI()

MODEL_CHAT = os.getenv("TECNARIA_MODEL_CHAT", "gpt-4.1-mini")
DATA_DIR = os.getenv("TECNARIA_DATA_DIR", "static/data")

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

CONSOLIDATO_PATH = os.path.join(DATA_DIR, "patches", "tecnaria_gold_consolidato.json")

# --------------------------------------------------
# FastAPI
# --------------------------------------------------

app = FastAPI(title="TECNARIA-IMBUTO", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restringi in produzione
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------
# Modelli Pydantic
# --------------------------------------------------

class AskRequest(BaseModel):
    question: str = Field(..., description="Domanda grezza dell'utente")
    session_id: Optional[str] = Field(
        None, description="ID sessione chat lato frontend"
    )
    lang: Optional[str] = Field(
        "it", description="Lingua preferita per la risposta (it/en/fr/de/es)"
    )
    debug: bool = Field(False, description="Se true, restituisce anche info di match/imbuto")
    force_family: Optional[str] = Field(
        None, description="Forza una famiglia (es: CTF, CTL, VCEM, ecc.)"
    )
    force_stage: Optional[str] = Field(
        None,
        description=(
            "Forza lo stadio imbuto: top, middle, bottom, post. "
            "Se None, viene classificato in automatico."
        ),
    )


class AskResponse(BaseModel):
    answer: str
    family: Optional[str] = None
    stage: Optional[str] = None
    lang: str = "it"
    debug: Optional[Dict[str, Any]] = None


class ConfigResponse(BaseModel):
    model_chat: str
    gold_items: int
    imbuto_stages: List[str]
    data_dir: str
    family_files: List[str]
    consolidato_loaded: bool


# --------------------------------------------------
# Caricamento KB GOLD
# --------------------------------------------------

KB_GOLD: List[Dict[str, Any]] = []


def _load_json_items(path: str) -> List[Dict[str, Any]]:
    """
    Carica un file JSON e restituisce la lista di items, qualunque sia il formato:
    - {"items": [ ... ]}
    - [ ... ]

    In caso di JSON corrotto NON blocca l'app:
    logga l'errore e restituisce lista vuota.
    """
    if not os.path.exists(path):
        print(f"[IMBUTO] WARNING: file non trovato: {path}")
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[IMBUTO] ERRORE JSON in {path}: {e} — FILE IGNORATO.")
        return []

    if isinstance(data, dict) and "items" in data:
        items = data["items"]
    elif isinstance(data, list):
        items = data
    else:
        items = []

    return [it for it in items if isinstance(it, dict)]


def load_kb() -> List[Dict[str, Any]]:
    all_items: List[Dict[str, Any]] = []

    for fname in FAMILY_FILES:
        full = os.path.join(DATA_DIR, fname)
        items = _load_json_items(full)
        all_items.extend(items)

    if os.path.exists(CONSOLIDATO_PATH):
        items = _load_json_items(CONSOLIDATO_PATH)
        all_items.extend(items)

    # filtra doppioni per id
    seen_ids = set()
    unique_items: List[Dict[str, Any]] = []
    for it in all_items:
        iid = it.get("id")
        if iid and iid in seen_ids:
            continue
        if iid:
            seen_ids.add(iid)
        unique_items.append(it)

    print(f"[IMBUTO] Caricati {len(unique_items)} blocchi GOLD (famiglie + consolidato se presente).")
    return unique_items


@app.on_event("startup")
def _startup_event():
    global KB_GOLD
    KB_GOLD = load_kb()

# --------------------------------------------------
# Utilità LLM (classificazione imbuto)
# --------------------------------------------------

IMBUTO_STAGES = ["top", "middle", "bottom", "post"]

FAMILIES_ALLOWED = {
    "CTF", "CTL", "CTL_MAXI", "VCEM", "CTCEM", "P560", "DIAPASON", "GTS", "COMM", "ALTRO"
}


def call_chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 500,
) -> str:
    if model is None:
        model = MODEL_CHAT

    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


def classify_imbuto(question: str, lang: str = "it") -> Dict[str, Any]:
    system_prompt = f"""
Sei il modulo IMBUTO di un bot Tecnaria.
Devi classificare la domanda dell'utente nello stadio del funnel commerciale e tecnico.

Stadi imbuto (usa SEMPRE uno di questi, minuscolo):
- "top"    : curiosità generali, che prodotto usare, confronto famiglie, concetti base.
- "middle" : dettagli tecnici su una famiglia già abbastanza chiara (posa, limiti, verifiche).
- "bottom" : domande molto specifiche e operative (quantità, codici ordine, tempi consegna, casi limite).
- "post"   : assistenza post-vendita, problemi in cantiere, varianti in corso d'opera.

Famiglie disponibili (usa esattamente queste stringhe o "ALTRO"):
- CTF, CTL, CTL_MAXI, VCEM, CTCEM, P560, DIAPASON, GTS, COMM, ALTRO

Rispondi in JSON valido nel formato:
{{
  "stage": "top|middle|bottom|post",
  "family": "CTF|CTL|CTL_MAXI|VCEM|CTCEM|P560|DIAPASON|GTS|COMM|ALTRO",
  "short_context": "riassunto telegrafico del caso (max 25 parole, nella lingua dell'utente)"
}}
"""
    user_prompt = f"Domanda utente ({lang}): {question}"

    raw = call_chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        max_tokens=200,
    )

    try:
        data = json.loads(raw)
    except Exception:
        data = {}

    stage = data.get("stage", "middle")
    if stage not in IMBUTO_STAGES:
        stage = "middle"

    family = data.get("family", "ALTRO")
    if family not in FAMILIES_ALLOWED:
        family = "ALTRO"

    short_context = data.get("short_context") or question[:150]

    return {
        "stage": stage,
        "family": family,
        "short_context": short_context,
        "raw": raw,
    }

# --------------------------------------------------
# MATCH GOLD
# --------------------------------------------------

TOKEN_REGEX = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> List[str]:
    return [t.lower() for t in TOKEN_REGEX.findall(text or "")]


def score_item(
    item: Dict[str, Any],
    q_tokens: List[str],
    imbuto_family: Optional[str],
    imbuto_stage: Optional[str],
) -> float:
    """
    Scoring:
    - overlap tra tokens domanda e tokens di triggers/tags/id/family
    - booster se famiglia coincide
    - piccoli bonus in base allo stage
    """
    score = 0.0

    triggers = item.get("triggers", [])
    tags = item.get("tags", [])
    text_parts: List[str] = []

    if isinstance(triggers, list):
        text_parts.extend(triggers)
    if isinstance(tags, list):
        text_parts.extend(tags)

    text_parts.append(item.get("id", ""))
    text_parts.append(item.get("family", ""))

    item_tokens = tokenize(" ".join(text_parts))
    if item_tokens:
        overlap = len(set(q_tokens) & set(item_tokens))
        base = overlap / (len(set(item_tokens)) + 1e-6)
    else:
        base = 0.0

    score += base

    item_family = item.get("family")
    if imbuto_family and item_family:
        if imbuto_family == item_family:
            score += 0.3
        elif imbuto_family != "ALTRO":
            score -= 0.05

    tags_lower = [t.lower() for t in tags if isinstance(t, str)]
    if imbuto_stage == "bottom":
        if any(k in tags_lower for k in ["ordine", "codice", "cantiere"]):
            score += 0.1
    if imbuto_stage == "top":
        if any(k in tags_lower for k in ["panoramica", "confronto"]):
            score += 0.1

    return score


def match_item(
    question: str,
    imbuto_info: Dict[str, Any],
    kb: List[Dict[str, Any]],
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    q_tokens = tokenize(question)

    scored: List[Dict[str, Any]] = []
    for item in kb:
        s = score_item(
            item=item,
            q_tokens=q_tokens,
            imbuto_family=imbuto_info.get("family"),
            imbuto_stage=imbuto_info.get("stage"),
        )
        scored.append({"score": s, "item": item})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def pick_response(
    matches: List[Dict[str, Any]],
    lang: str = "it",
) -> Dict[str, Any]:
    """
    Restituisce:
    - blocco GOLD selezionato
    - testo risposta finale (da response_variants.gold.[lang])
    """
    if not matches:
        raise HTTPException(status_code=404, detail="Nessun blocco GOLD pertinente trovato.")

    best = matches[0]["item"]

    rv = best.get("response_variants", {}) or {}
    gold_block = rv.get("gold") or {}
    chosen = None

    if isinstance(gold_block, dict):
        chosen = gold_block.get(lang) or gold_block.get("it")
        if chosen is None and gold_block:
            chosen = next(iter(gold_block.values()), None)

    if not chosen:
        chosen = best.get("question") or best.get("summary") or "Nessuna risposta GOLD disponibile per questo blocco."

    return {
        "block": best,
        "answer": chosen,
    }

# --------------------------------------------------
# Endpoint API
# --------------------------------------------------

@app.get("/api/config", response_model=ConfigResponse)
def get_config():
    return ConfigResponse(
        model_chat=MODEL_CHAT,
        gold_items=len(KB_GOLD),
        imbuto_stages=IMBUTO_STAGES,
        data_dir=DATA_DIR,
        family_files=FAMILY_FILES,
        consolidato_loaded=os.path.exists(CONSOLIDATO_PATH),
    )


@app.post("/api/ask", response_model=AskResponse)
def api_ask(payload: AskRequest):
    if not payload.question or not payload.question.strip():
        raise HTTPException(status_code=400, detail="Domanda vuota.")

    q = payload.question.strip()

    # 1) IMBUTO
    if payload.force_stage or payload.force_family:
        imbuto = classify_imbuto(q, lang=payload.lang)
        if payload.force_stage:
            imbuto["stage"] = payload.force_stage
        if payload.force_family:
            imbuto["family"] = payload.force_family
    else:
        imbuto = classify_imbuto(q, lang=payload.lang)

    # 2) MATCH GOLD
    matches = match_item(q, imbuto_info=imbuto, kb=KB_GOLD, top_k=5)

    # 3) PICK risposta GOLD
    picked = pick_response(matches, lang=payload.lang)
    answer_text = picked["answer"]
    block = picked["block"]

    debug_data = None
    if payload.debug:
        debug_data = {
            "imbuto": imbuto,
            "matches": [
                {
                    "score": float(m["score"]),
                    "id": m["item"].get("id"),
                    "family": m["item"].get("family"),
                    "tags": m["item"].get("tags", []),
                }
                for m in matches
            ],
            "picked_block_id": block.get("id"),
        }

    return AskResponse(
        answer=answer_text,
        family=block.get("family"),
        stage=imbuto.get("stage"),
        lang=payload.lang,
        debug=debug_data,
    )


@app.post("/ask", response_model=AskResponse)
def api_ask_alias(payload: AskRequest):
    """Alias per compatibilità con UI che chiama ancora /ask."""
    return api_ask(payload)

# --------------------------------------------------
# UI HTML su "/"
# --------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def root():
    return """
<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8" />
  <title>TECNARIA Sinapsi – Imbuto GOLD</title>
  <style>
    body {
      margin: 0;
      padding: 40px 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(circle at top, #101826 0, #020617 55%, #000000 100%);
      color: #e5e7eb;
      display: flex;
      justify-content: center;
    }
    .shell {
      width: 100%;
      max-width: 1200px;
      padding: 0 24px;
    }
    .card {
      border-radius: 24px;
      background: #020617;
      box-shadow: 0 24px 60px rgba(0,0,0,0.6);
      border: 1px solid rgba(148,163,184,0.35);
      overflow: hidden;
    }
    .card-header {
      padding: 20px 28px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: radial-gradient(circle at top left, #fb923c 0, #ea580c 45%, #020617 100%);
      color: white;
    }
    .title-block {
      display: flex;
      align-items: center;
      gap: 16px;
    }
    .avatar {
      width: 40px;
      height: 40px;
      border-radius: 999px;
      background: #0f172a;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
      font-size: 20px;
      border: 2px solid rgba(248, 250, 252, 0.6);
    }
    .title-main {
      font-size: 20px;
      font-weight: 700;
    }
    .title-sub {
      font-size: 13px;
      opacity: 0.9;
    }
    .header-right {
      text-align: right;
      font-size: 12px;
    }
    .badge-green {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 10px;
      border-radius: 999px;
      background: #16a34a;
      font-size: 11px;
      font-weight: 600;
    }
    .badge-green span.dot {
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: #bbf7d0;
    }
    .tabs {
      padding: 10px 20px 4px 20px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      border-bottom: 1px solid rgba(148,163,184,0.35);
      background: #020617;
    }
    .tab-pill {
      font-size: 11px;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid rgba(148,163,184,0.5);
      background: rgba(15,23,42,0.8);
      color: #e5e7eb;
      cursor: default;
    }
    .tab-pill.primary {
      border-color: #f97316;
      background: linear-gradient(to right, rgba(248, 153, 72, 0.25), rgba(248, 113, 113, 0.25));
    }
    .card-body {
      display: grid;
      grid-template-columns: minmax(0, 2fr) minmax(0, 2fr);
      gap: 0;
      min-height: 360px;
    }
    @media (max-width: 900px) {
      .card-body {
        grid-template-columns: minmax(0, 1fr);
      }
    }
    .left-pane {
      padding: 20px 20px 18px 20px;
      border-right: 1px solid rgba(30,41,59,0.9);
    }
    .right-pane {
      padding: 20px 20px 18px 20px;
      background: radial-gradient(circle at top, rgba(15,23,42,0.9), #020617 55%, #000000 100%);
    }
    .system-box {
      border-radius: 16px;
      background: rgba(15,23,42,0.9);
      border: 1px solid rgba(148,163,184,0.5);
      font-size: 13px;
      padding: 12px 14px;
      margin-bottom: 12px;
      color: #e5e7eb;
    }
    .system-box strong {
      color: #f97316;
    }
    textarea#question {
      width: 100%;
      min-height: 160px;
      max-height: 260px;
      resize: vertical;
      border-radius: 14px;
      border: 1px solid rgba(55,65,81,0.9);
      background: #020617;
      color: #e5e7eb;
      padding: 10px 12px;
      font-size: 14px;
      font-family: inherit;
      outline: none;
    }
    textarea#question:focus {
      border-color: #f97316;
      box-shadow: 0 0 0 1px rgba(249,115,22,0.6);
    }
    .input-row {
      margin-top: 12px;
      display: flex;
      gap: 12px;
      align-items: center;
    }
    .input-label {
      flex: 1;
      font-size: 13px;
      color: #9ca3af;
    }
    .btn-main {
      padding: 9px 18px;
      border-radius: 999px;
      border: none;
      background: linear-gradient(to right, #f97316, #ec4899);
      color: white;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      box-shadow: 0 10px 25px rgba(248,113,22,0.4);
    }
    .btn-main:active {
      transform: translateY(1px);
      box-shadow: 0 4px 14px rgba(248,113,22,0.3);
    }
    .chips-row {
      margin-top: 10px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      font-size: 11px;
    }
    .chip {
      padding: 5px 10px;
      border-radius: 999px;
      background: rgba(15,23,42,0.9);
      border: 1px solid rgba(75,85,99,0.8);
      cursor: pointer;
      color: #e5e7eb;
    }
    .chip:hover {
      border-color: #f97316;
      color: #fde68a;
    }
    .footer-note {
      margin-top: 8px;
      font-size: 11px;
      color: #9ca3af;
    }
    .answer-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 8px;
      font-size: 13px;
      color: #9ca3af;
    }
    .answer-box {
      border-radius: 16px;
      background: rgba(15,23,42,0.9);
      border: 1px solid rgba(148,163,184,0.5);
      padding: 12px 14px;
      font-size: 14px;
      min-height: 160px;
      white-space: pre-wrap;
    }
    .debug-toggle {
      font-size: 11px;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      cursor: pointer;
      user-select: none;
    }
    .debug-toggle input {
      accent-color: #f97316;
    }
    .debug-box {
      margin-top: 10px;
      border-radius: 12px;
      background: rgba(15,23,42,0.9);
      border: 1px dashed rgba(148,163,184,0.6);
      padding: 10px;
      font-size: 11px;
      max-height: 180px;
      overflow: auto;
    }
  </style>
</head>
<body>
<div class="shell">
  <div class="card">
    <div class="card-header">
      <div class="title-block">
        <div class="avatar">T</div>
        <div>
          <div class="title-main">TECNARIA Sinapsi</div>
          <div class="title-sub">
            Assistente GOLD strutturale · connettori &amp; sistemi misti · Imbuto automatico
          </div>
        </div>
      </div>
      <div class="header-right">
        <div class="badge-green"><span class="dot"></span> GOLD attivo</div>
        <div style="margin-top:4px; opacity:0.8;">Dataset: tecnaria_gold + imbuto · /api/ask</div>
      </div>
    </div>

    <div class="tabs">
      <div class="tab-pill primary">Instradamento automatico · CTF · VCEM · CTCEM · CTL · CTL MAXI · DIAPASON · P560 · COMM</div>
      <div class="tab-pill">Mode: GOLD deterministico</div>
      <div class="tab-pill">Lingua: IT</div>
    </div>

    <div class="card-body">
      <div class="left-pane">
        <div class="system-box">
          <strong>Sistema · init · mode: GOLD</strong><br>
          Quando fai una domanda reale di cantiere (solai in legno, laterocemento, acciaio, P560, ecc.)
          l'imbuto sceglie la famiglia più pertinente (CTF, VCEM, CTCEM, CTL, CTL MAXI, DIAPASON, P560, COMM)
          e pesca una risposta GOLD completa e deterministica.
        </div>

        <textarea id="question" placeholder="Scrivi la tua domanda (es. 'Quando devo usare il CTL MAXI in un solaio misto legno-calcestruzzo?')"></textarea>

        <div class="input-row">
          <div class="input-label">
            GOLD = risposta completa strutturale. <br>
            Se scrivi <strong>CANONICO:</strong> prima della domanda, la risposta sarà più sintetica e tecnica.
          </div>
          <button class="btn-main" onclick="sendAsk()">
            <span>Chiedi</span> <span>➜</span>
          </button>
        </div>

        <div class="chips-row">
          <div class="chip" onclick="preset('Uso CTF per solai su lamiera grecata con travi in acciaio: quali limiti di spessore lamiera e che chiodatrice devo usare?')">Uso CTF</div>
          <div class="chip" onclick="preset('Quando è preferibile usare VCEM invece di CTCEM nel recupero di un solaio in laterocemento esistente?')">VCEM vs CTCEM</div>
          <div class="chip" onclick="preset('Quando devo scegliere CTL MAXI rispetto al CTL standard in un solaio misto legno-calcestruzzo?')">CTL vs CTL MAXI</div>
          <div class="chip" onclick="preset('Posso usare DIAPASON per il recupero di un solaio in laterocemento con travetti ammalorati?')">Uso DIAPASON</div>
          <div class="chip" onclick="preset('Per la posa dei connettori CTF su lamiera grecata quale chiodatrice e quali chiodi idonei Tecnaria devo usare?')">P560 &amp; CTF</div>
          <div class="chip" onclick="preset('Cosa devo mandare a Tecnaria per avere un preventivo completo per CTF, VCEM e DIAPASON sullo stesso cantiere?')">Supporto Tecnaria</div>
        </div>

        <div class="footer-note">
          Suggerimento: descrivi sempre tipo di solaio, travi, spessori, luce delle campate e problemi (fessurazioni, umidità, degrado).
        </div>
      </div>

      <div class="right-pane">
        <div class="answer-header">
          <div><strong>Risposta GOLD</strong></div>
          <label class="debug-toggle">
            <input type="checkbox" id="debugToggle" checked />
            debug imbuto &amp; match
          </label>
        </div>
        <div id="answer" class="answer-box">In attesa della domanda…</div>
        <div id="debugBox" class="debug-box" style="display:none;"></div>
      </div>
    </div>
  </div>
</div>

<script>
  function preset(text) {
    document.getElementById("question").value = text;
    document.getElementById("question").focus();
  }

  async function sendAsk() {
    const q = document.getElementById("question").value.trim();
    const debug = document.getElementById("debugToggle").checked;
    const answerEl = document.getElementById("answer");
    const debugEl = document.getElementById("debugBox");

    if (!q) {
      answerEl.textContent = "Scrivi prima una domanda.";
      return;
    }

    answerEl.textContent = "Invio richiesta a /api/ask…";
    debugEl.style.display = debug ? "block" : "none";
    debugEl.textContent = "";

    try {
      const resp = await fetch("/api/ask", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          question: q,
          lang: "it",
          debug: debug
        })
      });

      const data = await resp.json();

      if (!resp.ok) {
        answerEl.textContent = "Errore backend (" + resp.status + "): " + (data.detail || JSON.stringify(data));
        return;
      }

      answerEl.textContent = data.answer || "(nessuna risposta)";

      if (debug && data.debug) {
        const ib = data.debug.imbuto || {};
        const matches = data.debug.matches || [];

        let text = "";
        text += "IMBUTO\n";
        text += "- stage: " + (ib.stage || "?") + "\n";
        text += "- family: " + (ib.family || "?") + "\n";
        if (ib.short_context) {
          text += "- short_context: " + ib.short_context + "\n";
        }
        text += "\nMATCH TOP K\n";
        matches.forEach((m, idx) => {
          text += (idx + 1) + ") score=" + m.score.toFixed(3)
               + " · id=" + (m.id || "?")
               + " · family=" + (m.family || "?") + "\n";
          if (m.tags && m.tags.length) {
            text += "   tags: " + m.tags.join(", ") + "\n";
          }
        });

        debugEl.style.display = "block";
        text = text || "Nessun dettaglio di debug disponibile.";
        debugEl.textContent = text;
      } else {
        debugEl.style.display = "none";
      }
    } catch (e) {
      answerEl.textContent = "Errore di rete: " + e;
    }
  }
</script>
</body>
</html>
    """


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=True,
    )
