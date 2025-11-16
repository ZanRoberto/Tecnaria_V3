import os
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from openai import OpenAI

# ============================================================
#  CONFIGURAZIONE BASE
# ============================================================

client = OpenAI()  # usa OPENAI_API_KEY dall'ambiente

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "static", "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")

# File master principale: CTF + P560 (146 blocchi)
KB_PATH = os.path.join(DATA_DIR, "ctf_system_COMPLETE_GOLD_v3.json")

# Directory overlay: patch, migliorie, nuove famiglie
OVERLAY_DIR = os.path.join(DATA_DIR, "overlays")

FALLBACK_FAMILY = "COMM"
FALLBACK_ID = "COMM-FALLBACK-NOANSWER-0001"
FALLBACK_MESSAGE = (
    "Per questa domanda non trovo una risposta GOLD nei dati caricati. "
    "Meglio un confronto diretto con l’ufficio tecnico Tecnaria, indicando tipo di solaio, travi, spessori e vincoli."
)

# ============================================================
#  FASTAPI
# ============================================================

app = FastAPI(title="TECNARIA-IMBUTO GOLD CTF_SYSTEM+P560", version="6.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static UI
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_ui():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"ok": True, "message": "UI non trovata"}

# ============================================================
#  MODELLI I/O
# ============================================================

class AskRequest(BaseModel):
    question: str
    lang: str = "it"
    mode: str = "gold"


class AskResponse(BaseModel):
    ok: bool
    answer: str
    family: str
    id: str
    mode: str
    lang: str
    score: float


# ============================================================
#  NORMALIZZAZIONE TESTO
# ============================================================

def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def normalize(text: str) -> str:
    if not isinstance(text, str):
        return ""
    t = strip_accents(text)
    t = t.lower()
    t = re.sub(r"\s+", " ", t).strip()
    return t


def tokenize(text: str) -> List[str]:
    t = normalize(text)
    if not t:
        return []
    return t.split(" ")


# ============================================================
#  LOAD KB: MASTER + OVERLAY
# ============================================================

def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_kb() -> Dict[str, Any]:
    """
    Carica:
    - il file MASTER (ctf_system_COMPLETE_GOLD_v3.json)
    - tutti gli OVERLAY in static/data/overlays/*.json
    Fonde tutti i blocchi in un'unica knowledge base.
    """
    if not os.path.exists(KB_PATH):
        raise FileNotFoundError(f"KB master non trovato: {KB_PATH}")

    base = load_json(KB_PATH)
    total_blocks: List[Dict[str, Any]] = base.get("blocks", [])

    # Carica overlay solo se la dir esiste
    overlay_path = Path(OVERLAY_DIR)
    if overlay_path.exists():
        for overlay_file in overlay_path.glob("*.json"):
            try:
                overlay = load_json(str(overlay_file))
                overlay_blocks = overlay.get("blocks", [])
                before = len(total_blocks)
                total_blocks.extend(overlay_blocks)
                after = len(total_blocks)
                print(f"[OVERLAY] {overlay_file.name}: +{after - before} blocchi")
            except Exception as e:
                print(f"[OVERLAY ERROR] {overlay_file}: {e}")

    print(f"[KB] Totale blocchi caricati (MASTER+OVERLAY): {len(total_blocks)}")
    return {"blocks": total_blocks}


# ============================================================
#  INDICE IN MEMORIA
# ============================================================

class KBState:
    blocks: List[Dict[str, Any]] = []
    # lista di (idx_block, trigger_norm, token_set)
    trigger_index: List[Tuple[int, str, set]] = []


S = KBState()


def build_index():
    kb = load_kb()
    S.blocks = kb.get("blocks", [])
    S.trigger_index = []

    for idx, block in enumerate(S.blocks):
        triggers = block.get("triggers", []) or []
        for trig in triggers:
            t_norm = normalize(trig)
            if not t_norm:
                continue
            tokens = set(tokenize(trig))
            S.trigger_index.append((idx, t_norm, tokens))

    print(f"[INDEX] Blocchi: {len(S.blocks)} – Trigger indicizzati: {len(S.trigger_index)}")


# Caricamento all'avvio
build_index()


# ============================================================
#  MOTORE DI MATCH – IMBUTO
# ============================================================

def lexical_match(question: str) -> Tuple[Optional[Dict[str, Any]], float]:
    """
    Imbuto lessicale:
    1) match per substring del trigger nella domanda
    2) se punteggi bassi, match per overlap di token
    """
    q_norm = normalize(question)
    q_tokens = set(tokenize(question))

    best_block = None
    best_score = 0.0

    # 1) substring-based
    for idx, trig_norm, trig_tokens in S.trigger_index:
        if trig_norm in q_norm:
            score = len(trig_norm) / max(10, len(q_norm))
            if score > best_score:
                best_score = score
                best_block = S.blocks[idx]

    # 2) token-overlap se score troppo basso
    if best_score < 0.25 and q_tokens:
        for idx, trig_norm, trig_tokens in S.trigger_index:
            if not trig_tokens:
                continue
            inter = q_tokens.intersection(trig_tokens)
            if not inter:
                continue
            score = len(inter) / len(trig_tokens)
            if score > best_score:
                best_score = score
                best_block = S.blocks[idx]

    return best_block, best_score


def ai_rerank(question: str, candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Secondo cervello OpenAI: tra i candidati già filtrati lessicalmente,
    chiede al modello quale ID è il più pertinente.
    """
    if not candidates:
        return None

    blocks_desc = []
    for b in candidates:
        blocks_desc.append(
            f"- ID: {b.get('id','?')}\n  Family: {b.get('family','?')}\n  Triggers: {', '.join(b.get('triggers', []))}"
        )
    blocks_text = "\n".join(blocks_desc)

    prompt = f"""
Sei l'assistente tecnico del motore Tecnaria-IMBUTO.
Devi scegliere il blocco più pertinente alla domanda seguente.

Domanda utente:
\"\"\"{question}\"\"\"


Blocchi candidati:
{blocks_text}

Rispondi SOLO con l'ID del blocco migliore, senza altro testo.
"""

    try:
        completion = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Sei un selettore deterministico di ID blocco Tecnaria. Rispondi solo con un ID esattamente come ti viene fornito."
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=16,
            temperature=0.0,
        )
        content = completion.choices[0].message.content.strip()
        for b in candidates:
            if b.get("id") == content:
                return b
    except Exception as e:
        print(f"[AI_RERANK ERROR] {e}")

    return None


def find_best_block(question: str) -> Tuple[Optional[Dict[str, Any]], float]:
    """
    Combina imbuto lessicale e, se necessario, reranking AI.
    """
    block, score = lexical_match(question)

    if block is not None and score >= 0.40:
        return block, score

    q_norm = normalize(question)
    q_tokens = set(tokenize(question))
    scored: List[Tuple[float, Dict[str, Any]]] = []

    for idx, trig_norm, trig_tokens in S.trigger_index:
        base_score = 0.0
        if trig_norm in q_norm:
            base_score += len(trig_norm) / max(10, len(q_norm))
        if q_tokens and trig_tokens:
            inter = q_tokens.intersection(trig_tokens)
            if inter:
                base_score += len(inter) / len(trig_tokens)
        if base_score > 0:
            scored.append((base_score, S.blocks[idx]))

    if not scored:
        return block, score

    scored.sort(key=lambda x: x[0], reverse=True)
    top_candidates = [b for s, b in scored[:5]]

    ai_block = ai_rerank(question, top_candidates)
    if ai_block is not None:
        return ai_block, max(score, 0.5)

    return block, score


# ============================================================
#  ENDPOINTS
# ============================================================

@app.get("/health")
def health():
    return {
        "ok": True,
        "blocks_loaded": len(S.blocks),
        "triggers_indexed": len(S.trigger_index),
        "kb_path": KB_PATH,
        "overlay_dir": OVERLAY_DIR,
    }


@app.post("/api/ask", response_model=AskResponse)
def api_ask(req: AskRequest):
    if req.mode.lower() != "gold":
        raise HTTPException(status_code=400, detail="Modalità non supportata. Usa mode='gold'.")

    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Domanda vuota.")

    block, score = find_best_block(question)

    if block is None:
        return AskResponse(
            ok=False,
            answer=FALLBACK_MESSAGE,
            family=FALLBACK_FAMILY,
            id=FALLBACK_ID,
            mode="gold",
            lang=req.lang,
            score=0.0,
        )

    lang_key = f"answer_{req.lang}"
    answer = (
        block.get(lang_key)
        or block.get("answer_it")
        or block.get("answer")
        or FALLBACK_MESSAGE
    )

    family = block.get("family", "CTF_SYSTEM")
    mode = block.get("mode", "gold")
    block_id = block.get("id", "UNKNOWN-ID")

    return AskResponse(
        ok=True,
        answer=answer,
        family=family,
        id=block_id,
        mode=mode,
        lang=req.lang,
        score=float(score),
    )


@app.post("/api/reload")
def api_reload():
    """
    Permette di ricaricare KB master + overlay senza riavviare il servizio.
    Utile quando aggiungiamo nuove patch JSON.
    """
    build_index()
    return {
        "ok": True,
        "message": "KB ricaricato (MASTER + OVERLAY)",
        "blocks_loaded": len(S.blocks),
        "triggers_indexed": len(S.trigger_index),
    }
