import os
import json
import re
import unicodedata
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# OpenAI: secondo cervello per la scelta semantica
from openai import OpenAI

client = OpenAI()

# ============================================================
#  PATH / FILE
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "static", "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")

KB_PATH = os.path.join(DATA_DIR, "ctf_system_COMPLETE_GOLD_v3.json")

FALLBACK_FAMILY = "COMM"
FALLBACK_ID = "COMM-FALLBACK-NOANSWER-0001"
FALLBACK_MESSAGE = (
    "Per questa domanda non trovo una risposta GOLD nei dati caricati. "
    "Meglio un confronto diretto con l’ufficio tecnico Tecnaria, indicando tipo di solaio, travi, spessori e vincoli."
)

# ============================================================
#  FASTAPI
# ============================================================

app = FastAPI(title="TECNARIA-IMBUTO GOLD CTF_SYSTEM+P560", version="5.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
#  STATIC UI
# ============================================================

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def serve_ui():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"ok": True, "message": "UI non trovata"}

# ============================================================
#  MODELLI RISPOSTA
# ============================================================

class AskResponse(BaseModel):
    ok: bool
    answer: str
    family: str
    id: str
    mode: str
    lang: str

# ============================================================
#  NORMALIZZAZIONE
# ============================================================

def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )

def normalize_text(s: str) -> str:
    if s is None:
        return ""
    s = strip_accents(s)
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s

def tokenize_norm(s: str) -> List[str]:
    return normalize_text(s).split()

# ============================================================
#  STATO
# ============================================================

class BrainState:
    blocks: List[Dict[str, Any]] = []
    tokens_by_id: Dict[str, Set[str]] = {}

S = BrainState()

# ============================================================
#  LOAD KB (LEGGE SIA SCHEMA GOLD CHE LEGACY)
# ============================================================

def load_kb() -> None:
    if not os.path.exists(KB_PATH):
        raise RuntimeError(f"File KB non trovato: {KB_PATH}")

    with open(KB_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    blocks = data.get("blocks")
    if not isinstance(blocks, list):
        raise RuntimeError("Struttura KB non valida: manca 'blocks'")

    S.blocks = []
    S.tokens_by_id = {}

    for blk in blocks:
        if not isinstance(blk, dict):
            continue

        blk_id = blk.get("id")
        if not isinstance(blk_id, str):
            continue

        S.blocks.append(blk)
        tokens: Set[str] = set()

        def add_tokens(val: Any):
            if isinstance(val, str):
                for t in tokenize_norm(val):
                    if t:
                        tokens.add(t)
            elif isinstance(val, list):
                for v in val:
                    if isinstance(v, str):
                        for t in tokenize_norm(v):
                            if t:
                                tokens.add(t)

        # Schema GOLD + legacy
        add_tokens(blk.get("question_it"))
        add_tokens(blk.get("question"))
        add_tokens(blk.get("triggers", []))
        add_tokens(blk.get("tags", []))

        fam = str(blk.get("family", "")).upper()
        if "P560" in fam:
            tokens.add("p560")
        if "CTF" in fam:
            tokens.add("ctf")

        S.tokens_by_id[blk_id] = tokens

    print(f"[BOOT] Caricati {len(S.blocks)} blocchi GOLD da {KB_PATH}")

# ============================================================
#  SCORING (PRIMO CERVELLO: IMBUTO)
# ============================================================

def score_block(q_norm: str, q_tokens: Set[str], blk: Dict[str, Any]) -> float:
    blk_id = blk.get("id", "")
    family = str(blk.get("family", "")).upper()
    intent = str(blk.get("intent", "")).lower()

    score = 0.0

    # Trigger e tag
    for field, w in [
        ("triggers", 10.0),
        ("tags", 3.0),
    ]:
        vals = blk.get(field, [])
        if isinstance(vals, list):
            for v in vals:
                tv = normalize_text(v)
                if tv and tv in q_norm:
                    score += w

    ref = S.tokens_by_id.get(blk_id, set())
    common = q_tokens & ref
    score += len(common)

    # Bonus famiglie
    if "P560" in family:
        if any(tok in q_tokens for tok in ["p560", "pistola", "sparachiodi", "chiodatrice"]):
            score += 8

    if "CTF" in family:
        if any(tok in q_tokens for tok in ["ctf", "lamiera", "ondina", "card", "grecata"]):
            score += 5

    # Mini bias sugli intent, per cominciare a differenziare
    # (overview / errore / controllo posa)
    if any(kw in q_norm for kw in ["mi parli", "cos e", "cos'è", "che cos e", "che cos'è", "a cosa serve"]):
        if intent == "overview":
            score += 5
        if "err" in blk_id.lower():
            score -= 3

    if any(kw in q_norm for kw in ["come verifico", "come controllo", "come faccio a sapere", "come faccio a capire"]):
        if intent in ["controllo_posa", "controllo", "qualita_posa"]:
            score += 5

    if any(kw in q_norm for kw in ["errore", "difetto", "non valido", "fuori campo", "vietato"]):
        if intent in ["errore_uso", "anomalia_strutturale"]:
            score += 5

    return score

def find_top_blocks(q: str, top_k: int = 5) -> List[Tuple[float, Dict[str, Any]]]:
    """Ritorna i migliori top_k blocchi (score, blk)."""
    q_norm = normalize_text(q)
    if not q_norm:
        return []

    q_tokens = set(q_norm.split())
    scored: List[Tuple[float, Dict[str, Any]]] = []

    for blk in S.blocks:
        s = score_block(q_norm, q_tokens, blk)
        if s > 0:
            scored.append((s, blk))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_k]

# ============================================================
#  PICK ANSWER (GOLD + LEGACY)
# ============================================================

def pick_answer_it(blk: Dict[str, Any]) -> Optional[str]:
    # 1) Schema GOLD
    ans = blk.get("answer_it")
    if isinstance(ans, str) and ans.strip():
        return ans.strip()

    ga = blk.get("gold_answer_it")
    if isinstance(ga, str) and ga.strip():
        return ga.strip()

    rv = blk.get("response_variants")
    if isinstance(rv, dict):
        gold = rv.get("gold") or rv.get("GOLD") or {}
        if isinstance(gold, dict):
            txt = gold.get("it") or gold.get("IT")
            if isinstance(txt, str) and txt.strip():
                return txt.strip()

    # 2) Schema legacy
    ans2 = blk.get("answer")
    if isinstance(ans2, str) and ans2.strip():
        return ans2.strip()

    ga2 = blk.get("gold_answer")
    if isinstance(ga2, str) and ga2.strip():
        return ga2.strip()

    if isinstance(rv, dict):
        gold2 = rv.get("gold") or rv.get("GOLD") or {}
        if isinstance(gold2, dict):
            txt2 = gold2.get("answer")
            if isinstance(txt2, str) and txt2.strip():
                return txt2.strip()

    return None

# ============================================================
#  TERMINOLOGIA
# ============================================================

def enforce_terminologia(family: str, txt: str) -> str:
    if not isinstance(txt, str):
        return txt
    return re.sub(r"\bperni\b", "chiodi idonei Tecnaria", txt, flags=re.IGNORECASE)

# ============================================================
#  SECONDO CERVELLO: OPENAI PER SCELTA SEMANTICA
# ============================================================

def choose_block_with_openai(question: str, candidates: List[Tuple[float, Dict[str, Any]]]) -> Dict[str, Any]:
    """
    Usa OpenAI per scegliere, tra i top-k blocchi proposti dall'imbuto,
    quello semanticamente più coerente con la domanda.
    Se qualcosa va storto, ritorna comunque il migliore per score.
    """
    # Fallback di default: il migliore per score
    if not candidates:
        return {}

    if len(candidates) == 1:
        return candidates[0][1]

    # Costruisco il contesto per OpenAI
    model_name = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    # Preparo una lista sintetica di candidati
    blocchi_descr = []
    for score, blk in candidates:
        bid = blk.get("id", "SENZA-ID")
        fam = blk.get("family", "")
        intent = blk.get("intent", "")
        q_it = blk.get("question_it") or blk.get("question") or ""
        ans = pick_answer_it(blk) or ""
        # Accorcio un po' la risposta per sicurezza
        if len(ans) > 600:
            ans = ans[:600] + " [...]"
        blocchi_descr.append(
            f"- ID: {bid}\n  family: {fam}\n  intent: {intent}\n  domanda_blocco: {q_it}\n  risposta_blocco: {ans}"
        )

    blocchi_text = "\n\n".join(blocchi_descr)

    system_prompt = (
        "Sei un motore di reranking per una knowledge base tecnica di connettori Tecnaria. "
        "Ricevi la domanda di un utente (in italiano) e alcuni blocchi candidati, "
        "ognuno con ID, family, intent, domanda_blocco e risposta_blocco. "
        "Devi scegliere UN SOLO blocco, quello la cui risposta risponde meglio alla domanda dell’utente. "
        "Se nessun blocco è davvero adatto, rispondi esattamente con 'NONE'. "
        "Rispondi SEMPRE SOLO con l'ID del blocco (es. 'P560-ERR-0003-SOVRA-INFISSIONE') oppure 'NONE'."
    )

    user_prompt = (
        f"Domanda utente:\n{question}\n\n"
        f"Blocchi candidati:\n{blocchi_text}\n\n"
        "Indica SOLO l'ID del blocco migliore oppure 'NONE' se nessuno è adeguato."
    )

    try:
        completion = client.chat.completions.create(
            model=model_name,
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = completion.choices[0].message.content.strip()
        if not content:
            # Nessuna risposta chiara → fallback
            return candidates[0][1]

        if content.upper().startswith("NONE"):
            # Nessun blocco ritenuto adatto → fallback al migliore per score
            return candidates[0][1]

        chosen_id = content.strip().split()[0]

        for _, blk in candidates:
            if str(blk.get("id", "")).strip() == chosen_id:
                return blk

        # Se non trova ID esatto, fallback
        return candidates[0][1]

    except Exception as e:
        # In caso di errore API, loggare e fallback
        print(f"[WARN] Errore OpenAI reranking: {e}")
        return candidates[0][1]

# ============================================================
#  STARTUP
# ============================================================

@app.on_event("startup")
def on_startup():
    load_kb()

# ============================================================
#  HEALTH
# ============================================================

@app.get("/health")
def health():
    return {
        "ok": True,
        "kb_path": KB_PATH,
        "blocks_loaded": len(S.blocks),
    }

# ============================================================
#  ASK
# ============================================================

@app.post("/api/ask", response_model=AskResponse)
async def api_ask(request: Request) -> AskResponse:

    try:
        body = await request.json()
    except Exception:
        body = {}

    if not isinstance(body, dict):
        body = {}

    q = (
        body.get("q")
        or body.get("question")
        or body.get("message")
        or body.get("text")
        or ""
    )

    if not isinstance(q, str):
        q = str(q)

    q = q.strip()

    mode = str(body.get("mode") or "gold").lower()
    lang = "it"

    if mode != "gold":
        raise HTTPException(status_code=400, detail="Usa mode=gold")

    if not q:
        return AskResponse(
            ok=False,
            answer="Domanda vuota.",
            family=FALLBACK_FAMILY,
            id=FALLBACK_ID,
            mode="gold",
            lang=lang,
        )

    # 1) Primo cervello: imbuto → top-k blocchi
    top = find_top_blocks(q, top_k=5)
    if not top:
        return AskResponse(
            ok=False,
            answer=FALLBACK_MESSAGE,
            family=FALLBACK_FAMILY,
            id=FALLBACK_ID,
            mode="gold",
            lang=lang,
        )

    # 2) Secondo cervello: OpenAI sceglie il migliore tra i candidati
    blk = choose_block_with_openai(q, top)
    if not blk:
        return AskResponse(
            ok=False,
            answer=FALLBACK_MESSAGE,
            family=FALLBACK_FAMILY,
            id=FALLBACK_ID,
            mode="gold",
            lang=lang,
        )

    txt = pick_answer_it(blk)
    if not txt:
        return AskResponse(
            ok=False,
            answer="Risposta GOLD non trovata per il blocco selezionato.",
            family=str(blk.get("family", FALLBACK_FAMILY)),
            id=str(blk.get("id", FALLBACK_ID)),
            mode="gold",
            lang=lang,
        )

    family = str(blk.get("family", FALLBACK_FAMILY))
    txt = enforce_terminologia(family, txt)

    return AskResponse(
        ok=True,
        answer=txt,
        family=family,
        id=str(blk.get("id", FALLBACK_ID)),
        mode="gold",
        lang=lang,
    )

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
