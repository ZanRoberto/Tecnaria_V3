import os
import json
import math
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
MODEL_EMBED = os.getenv("TECNARIA_MODEL_EMBED", "text-embedding-3-large")

# Path knowledge base (adattali alla tua repo)
PATH_GOLD = os.getenv("TECNARIA_GOLD_PATH", "static/data/tecnaria_gold.json")

# --------------------------------------------------
# FastAPI
# --------------------------------------------------

app = FastAPI(title="TECNARIA-IMBUTO", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # in prod: restringi
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
    # Override imbuto (facoltativi)
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
    model_embed: str
    gold_items: int
    imbuto_stages: List[str]


# --------------------------------------------------
# Caricamento KB GOLD
# --------------------------------------------------

KB_GOLD: List[Dict[str, Any]] = []


def load_gold(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        raise RuntimeError(f"File GOLD non trovato: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise RuntimeError("Il file GOLD deve contenere una lista di blocchi.")
    return data


@app.on_event("startup")
def _startup_event():
    global KB_GOLD
    KB_GOLD = load_gold(PATH_GOLD)
    print(f"[IMBUTO] Caricati {len(KB_GOLD)} blocchi GOLD da {PATH_GOLD}")


# --------------------------------------------------
# Utilità LLM
# --------------------------------------------------

def call_chat(
    messages: List[Dict[str, str]],
    model: str = None,
    temperature: float = 0.2,
    max_tokens: int = 800,
) -> str:
    """Wrapper semplice per chiamata chat OpenAI."""
    if model is None:
        model = MODEL_CHAT

    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


def get_embedding(text: str) -> List[float]:
    """Calcola embedding con modello OpenAI."""
    text = text.replace("\n", " ")
    emb = client.embeddings.create(
        model=MODEL_EMBED,
        input=[text],
    )
    return emb.data[0].embedding


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# --------------------------------------------------
# IMBUTO: classificazione stadio + famiglia
# --------------------------------------------------

IMBUTO_STAGES = ["top", "middle", "bottom", "post"]


def classify_imbuto(question: str, lang: str = "it") -> Dict[str, Any]:
    """
    Usa il modello per capire:
    - stadio imbuto: top / middle / bottom / post
    - famiglia principale: CTF, CTL, CTL_MAXI, VCEM, CTCEM, P560, DIAPASON, GTS, COMM, ALTRO
    - tipo di solaio / contesto sintetico
    Torna sempre un dizionario robusto.
    """
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

Rispondi in JSON **valido**, senza testo aggiuntivo, nel formato:
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

    # fallback robusto
    try:
        data = json.loads(raw)
    except Exception:
        data = {}

    stage = data.get("stage", "middle")
    if stage not in IMBUTO_STAGES:
        stage = "middle"

    family = data.get("family", "ALTRO")
    allowed_fam = {"CTF", "CTL", "CTL_MAXI", "VCEM", "CTCEM", "P560", "DIAPASON", "GTS", "COMM", "ALTRO"}
    if family not in allowed_fam:
        family = "ALTRO"

    short_context = data.get("short_context") or question[:150]

    return {
        "stage": stage,
        "family": family,
        "short_context": short_context,
        "raw": raw,
    }


# --------------------------------------------------
# MATCH GOLD: selezione blocchi e varianti
# --------------------------------------------------

def score_item(
    item: Dict[str, Any],
    q_embedding: List[float],
    imbuto_family: Optional[str],
    imbuto_stage: Optional[str],
) -> float:
    """
    Semplice scoring:
    - similitudine embedding sulla domanda
    - booster se famiglia combacia
    - leggero booster se tag contiene 'bottom'/'middle' ecc coerente con lo stage
    """
    base_score = 0.0

    # embedding domanda vs testo item (question + tags)
    text = item.get("question", "") + " " + " ".join(item.get("tags", []))
    try:
        item_emb = item.get("_embedding")
        if item_emb is None:
            # se non presente, lo calcoliamo lazy (puoi anche pre-calcolarlo offline)
            item_emb = get_embedding(text)
            item["_embedding"] = item_emb  # cache in memoria
        sim = cosine_similarity(q_embedding, item_emb)
    except Exception:
        sim = 0.0

    base_score += sim * 1.0

    # booster famiglia
    item_family = item.get("family")
    if imbuto_family and item_family:
        if imbuto_family == item_family:
            base_score += 0.3
        elif imbuto_family != "ALTRO":
            # penalità leggera se mismatch grosso
            base_score -= 0.05

    # booster stage-logic sui tag
    tags = [t.lower() for t in item.get("tags", [])]
    if imbuto_stage == "bottom" and "ordine" in tags:
        base_score += 0.1
    if imbuto_stage == "top" and "panoramica" in tags:
        base_score += 0.1

    return base_score


def match_item(
    question: str,
    imbuto_info: Dict[str, Any],
    kb: List[Dict[str, Any]],
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Seleziona i best match dal GOLD usando embedding + segnali imbuto."""
    q_emb = get_embedding(question)

    scored: List[Dict[str, Any]] = []
    for item in kb:
        s = score_item(
            item=item,
            q_embedding=q_emb,
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
    - testo risposta finale (scegliendo variante GOLD se presente)
    """
    if not matches:
        raise HTTPException(status_code=404, detail="Nessun blocco GOLD pertinente trovato.")

    best = matches[0]["item"]

    # Struttura attesa (adatta se il tuo JSON è diverso):
    # {
    #   "id": "...",
    #   "family": "CTF",
    #   "question": "....",
    #   "answers": {
    #       "it": {
    #           "gold": [ "testo...", "variante..." ],
    #           "canonical": [ "..." ],
    #           "dynamic": [ "..." ]
    #       },
    #       "en": {...}
    #   },
    #   "tags": [...],
    #   ...
    # }

    answers = best.get("answers", {})
    lang_block = answers.get(lang) or answers.get("it") or {}

    # 1) preferisci GOLD
    gold_list = lang_block.get("gold") or []
    if gold_list:
        chosen = gold_list[0]
    else:
        # 2) fallback canonical
        canon_list = lang_block.get("canonical") or []
        if canon_list:
            chosen = canon_list[0]
        else:
            # 3) fallback dynamic / testo question se proprio disperati
            dyn_list = lang_block.get("dynamic") or []
            if dyn_list:
                chosen = dyn_list[0]
            else:
                chosen = best.get("question", "Nessuna risposta disponibile nel GOLD.")

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
        model_embed=MODEL_EMBED,
        gold_items=len(KB_GOLD),
        imbuto_stages=IMBUTO_STAGES,
    )


@app.post("/api/ask", response_model=AskResponse)
def api_ask(payload: AskRequest):
    if not payload.question or not payload.question.strip():
        raise HTTPException(status_code=400, detail="Domanda vuota.")

    q = payload.question.strip()

    # 1) IMBUTO: classificazione stadio + famiglia
    if payload.force_stage or payload.force_family:
        # se l'utente forza qualcosa, usiamo imbuto solo come "contesto soft"
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

    # 4) Impacchetta debug se richiesto
    debug_data = None
    if payload.debug:
        debug_data = {
            "imbuto": imbuto,
            "matches": [
                {
                    "score": float(m["score"]),
                    "id": m["item"].get("id"),
                    "family": m["item"].get("family"),
                    "question": m["item"].get("question"),
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
# Root semplice (opzionale)
# --------------------------------------------------

@app.get("/")
def root():
    return {
        "app": "TECNARIA-IMBUTO",
        "message": "API attiva. Usa /api/ask per le domande e /api/config per la configurazione.",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=True,
    )
