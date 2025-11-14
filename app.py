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

app = FastAPI(title="TECNARIA-IMBUTO", version="1.2.0")

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

    # famiglie principali
    for fname in FAMILY_FILES:
        full = os.path.join(DATA_DIR, fname)
        items = _load_json_items(full)
        all_items.extend(items)

    # consolidato aggiuntivo, se esiste
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
    """
    Classifica la domanda nello stadio dell'imbuto e nella famiglia principale.
    ATTENZIONE: niente f-string nella system_prompt per non rompere le graffe del JSON.
    """
    system_prompt = """
Sei il modulo IMBUTO di un bot Tecnaria.
Devi classificare la domanda dell'utente nello stadio del funnel commerciale e tecnico.

Stadi imbuto (usa SEMPRE uno di questi, minuscolo):
- "top"    : curiosità generali, che prodotto usare, confronto famiglie, concetti base.
- "middle" : dettagli tecnici su una famiglia già abbastanza chiara (posa, limiti, verifiche).
- "bottom" : domande molto specifiche e operative (quantità, codici ordine, tempi consegna, casi limite).
- "post"   : assistenza post-vendita, problemi in cantiere, varianti in corso d'opera.

Famiglie disponibili (usa esattamente queste stringhe o "ALTRO"):
CTF, CTL, CTL_MAXI, VCEM, CTCEM, P560, DIAPASON, GTS, COMM, ALTRO

Rispondi in JSON valido nel formato:

{
  "stage": "top|middle|bottom|post",
  "family": "CTF|CTL|CTL_MAXI|VCEM|CTCEM|P560|DIAPASON|GTS|COMM|ALTRO",
  "short_context": "riassunto telegrafico del caso (max 25 parole, nella lingua dell'utente)"
}
"""

    # Qui l'f-string è sicura, non contiene graffe JSON
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

    short_context = data.get("short_context") or question[:120]

    return {
        "stage": stage,
        "family": family,
        "short_context": short_context,
        "raw": raw,
    }


# --------------------------------------------------
# MATCH GOLD basato su triggers/tags (no embeddings)
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
    Scoring semplice e robusto:
    - overlap tra tokens della domanda e tokens di triggers/tags
    - booster se famiglia coincide
    - piccoli bonus in base allo stage.
    """
    score = 0.0

    triggers = item.get("triggers", [])
    tags = item.get("tags", [])
    text_parts = []

    if isinstance(triggers, list):
        text_parts.extend(triggers)
    if isinstance(tags, list):
        text_parts.extend(tags)

    text_parts.append(item.get("id", ""))
    text_parts.append(item.get("family", ""))

    item_tokens = tokenize(" ".join(text_parts))
    if not item_tokens:
        base = 0.0
    else:
        overlap = len(set(q_tokens) & set(item_tokens))
        base = overlap / (len(set(item_tokens)) + 1e-6)

    score += base

    # booster famiglia
    item_family = item.get("family")
    if imbuto_family and item_family:
        if imbuto_family == item_family:
            score += 0.3
        elif imbuto_family != "ALTRO":
            score -= 0.05

    # booster stage via tags
    tags_lower = [t.lower() for t in tags]
    if imbuto_stage == "bottom":
        if "ordine" in tags_lower or "codice" in tags_lower or "cantiere" in tags_lower:
            score += 0.1
    if imbuto_stage == "top":
        if "panoramica" in tags_lower or "confronto" in tags_lower:
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

    # preferiamo sempre GOLD
    gold_block = rv.get("gold") or {}
    chosen = None

    if isinstance(gold_block, dict):
        # gold_block es: {"it": "...", "en": "..."}
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


# --------------------------------------------------
# Root semplice
# --------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def root():
    return """
    <html>
      <head><title>TECNARIA-IMBUTO</title></head>
      <body>
        <h1>TECNARIA-IMBUTO</h1>
        <p>API attiva.</p>
        <ul>
          <li>Config: <code>/api/config</code></li>
          <li>Ask: <code>POST /api/ask</code> (JSON)</li>
        </ul>
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
