import os
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from openai import OpenAI

# ============================================================
# CONFIG
# ============================================================

APP_VERSION = "12.6.0-DIAGNOSTIC-LIMITI"

client = OpenAI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "static", "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")

MASTER_PATH = os.path.join(DATA_DIR, "ctf_system_COMPLETE_GOLD_master.json")
OVERLAY_DIR = os.path.join(DATA_DIR, "overlays")

FALLBACK_FAMILY = "COMM"
FALLBACK_ID = "COMM-FALLBACK-NOANSWER-0001"
FALLBACK_MESSAGE = (
    "Per questa domanda non trovo una risposta GOLD appropriata nei dati caricati. "
    "Meglio un confronto diretto con l’ufficio tecnico Tecnaria."
)

# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="TECNARIA GOLD – MATCHING v12.6.0 DIAGNOSTIC+LIMITI",
    version=APP_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(path):
        return FileResponse(path)
    return {"ok": True, "message": "UI non trovata"}


# ============================================================
# MODELLI
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
# NORMALIZZAZIONE
# ============================================================

def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )


def normalize(t: str) -> str:
    if not isinstance(t, str):
        return ""
    t = strip_accents(t)
    t = t.lower()
    t = re.sub(r"[^a-z0-9àèéìòùç\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def tokenize(text: str) -> List[str]:
    return normalize(text).split(" ")


# ============================================================
# LOAD KB
# ============================================================

def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_master_blocks() -> List[Dict[str, Any]]:
    data = load_json(MASTER_PATH)
    return data.get("blocks", [])


def load_overlay_blocks() -> List[Dict[str, Any]]:
    blocks = []
    p = Path(OVERLAY_DIR)
    if not p.exists():
        return blocks
    for f in p.glob("*.json"):
        try:
            d = load_json(str(f))
            blocks.extend(d.get("blocks", []))
        except Exception:
            pass
    return blocks


# ============================================================
# STATE
# ============================================================

class KBState:
    master_blocks: List[Dict[str, Any]] = []
    overlay_blocks: List[Dict[str, Any]] = []


S = KBState()


def reload_all():
    S.master_blocks = load_master_blocks()
    S.overlay_blocks = load_overlay_blocks()
    print(f"[KB LOADED] master={len(S.master_blocks)} overlay={len(S.overlay_blocks)}")


reload_all()


# ============================================================
# MATCHING ENGINE (LESSIC + AI RERANK) – v12.6.0
# ============================================================

def score_trigger(trigger: str, q_tokens: set, q_norm: str) -> float:
    """
    Punteggio di un singolo trigger.
    Patch v12.1: i trigger con UNA SOLA PAROLA (es. 'ctf', 'posare')
    vengono ignorati perché troppo generici e rumorosi.
    """
    trig_norm = normalize(trigger)
    if not trig_norm:
        return 0.0

    trig_tokens = set(trig_norm.split())

    # Trigger troppo generici (una sola parola) → li ignoriamo
    if len(trig_tokens) <= 1:
        return 0.0

    score = 0.0

    # 1) token match totale
    if trig_tokens.issubset(q_tokens):
        score += 3.0

    # 2) match parziale > metà token
    inter = trig_tokens.intersection(q_tokens)
    if len(inter) >= max(1, len(trig_tokens) // 2):
        score += len(inter) / len(trig_tokens)

    # 3) substring significativa
    if len(trig_norm) >= 10 and trig_norm in q_norm:
        score += 0.5

    return score


def score_block(question: str, block: Dict[str, Any]) -> float:
    """
    Punteggio complessivo di un blocco:
    - somma dei punteggi trigger
    - + similarità domanda_utente vs question_it del blocco
    """
    q_norm = normalize(question)
    q_tokens = set(tokenize(question))

    # trigger score
    trig_score = 0.0
    for trigger in block.get("triggers", []) or []:
        trig_score += score_trigger(trigger, q_tokens, q_norm)

    # question_it similarity
    q_it = block.get("question_it") or ""
    q_it_tokens = set(tokenize(q_it)) if q_it else set()

    sim_score = 0.0
    if q_it_tokens:
        inter = q_tokens.intersection(q_it_tokens)
        if inter:
            sim_score = len(inter) / len(q_it_tokens)
            sim_score *= 3.0  # peso forte

    total = trig_score + sim_score

    # penalizza overview se ci sono candidati più specifici
    if "OVERVIEW" in (block.get("id") or "").upper():
        total *= 0.5

    return total


def lexical_candidates(question: str, blocks: List[Dict[str, Any]], limit: int = 15):
    scored: List[Tuple[float, Dict[str, Any]]] = []

    for block in blocks:
        s = score_block(question, block)
        if s > 0:
            scored.append((s, block))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:limit]


def is_overview_question(q_norm: str) -> bool:
    patterns = [
        "mi parli della", "mi parli del", "mi parli di ",
        "parlami della", "parlami del", "parlami di ",
        "cos e ", "cosa e ", "che cos e", "che cosa e",
        "overview", "panoramica",
        "descrizione della", "descrizione del",
        "descrivimi la", "descrivimi il",
    ]
    return any(p in q_norm for p in patterns)


# ============================================================
# RERANK AI – v12.6 con DIAGNOSTIC SAFE + LIMITI
# ============================================================

def ai_rerank(question: str, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Usa l'AI SOLO per scegliere l'ID tra i candidati.

    Patch v12.2 STRADA A: geometria vs chiodi difettosi.
    Patch v12.3: negazioni → killer.
    Patch v12.4 STRUTTURALE:
      se la domanda riguarda spessori lamiera / lamiera doppia /
      propulsore forte / fuori ETA / prove Tecnaria →
      usare SOLO killer strutturali ed escludere killer ambientali.
    Patch v12.5 DIAGNOSTIC SAFE:
      se la domanda è di tipo 'come verifico / come controllo / come faccio a capire se'
      escludere blocchi killer/errore/fuori campo e preferire blocchi neutri di verifica.
    Patch v12.6 LIMITI:
      per domande 'in quali casi non posso usare...' preferire il blocco limiti di applicazione.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    q_norm = normalize(question)

    # -------------------------
    # Riconoscimento domande DIAGNOSTICHE (v12.5)
    # -------------------------
    diagnostic_terms = [
        "come verifico", "come faccio a verificare",
        "come controllo", "come faccio a controllare",
        "come faccio a capire se", "come posso capire se",
        "come posso verificare", "come si verifica",
        "come si controlla", "verificare se", "controllare se",
        "come faccio a sapere se", "come posso essere sicuro",
        "come posso essere certa", "come posso essere certo",
        "come faccio a essere sicuro", "come faccio a essere certo"
    ]
    question_is_diagnostic = any(t in q_norm for t in diagnostic_terms)

    # -------------------------
    # Riconoscimento domande sui LIMITI CTF (v12.6)
    # -------------------------
    limit_terms = [
        "in quali casi non posso usare i ctf",
        "in quali casi non posso usare i ctf su lamiera",
        "quando non posso usare i ctf",
        "quando non è possibile usare i ctf",
        "limiti di applicazione dei ctf",
        "casi in cui i ctf non sono ammessi",
        "quando i ctf sono fuori campo",
    ]
    question_is_limits = any(t in q_norm for t in limit_terms)

    # -------------------------
    # PATCH 12.4: riconoscimento "caso strutturale"
    # -------------------------

    structural_terms = [
        "spessa", "spessore", "spess", "1 2", "1 5", "2 0",
        "due lamiere", "doppia lamiera", "lamiera doppia", "sovrappost",
        "propulsore forte", "propulsore molto forte",
        "classe alta", "potenza alta",
        "fuori eta", "fuori campo", "non coperto", "coperto dalle prestazioni",
        "prestazioni dichiarate",
        "prove tecnaria", "non rappresentativo",
        "deformazione non rappresentativa",
    ]

    question_is_structural = any(t in q_norm for t in structural_terms)

    if question_is_structural:
        structural_killers = []
        others = []

        for b in candidates:
            text_block = (b.get("question_it") or "") + " " + " ".join(b.get("triggers") or [])
            tb_norm = normalize(text_block)

            # Killer strutturali
            is_structural = any(key in tb_norm for key in [
                "spesso", "spessore", "fuori campo", "fuori eta",
                "doppia lamiera", "due lamiere", "lamiera sovrapposta",
                "non rappresentativa", "prove tecnaria",
                "sovra infissione", "sovra-infissione", "propulsore",
                "rigidezza aumentata", "rigidita aumentata"
            ])

            # Killer ambientali da ESCLUDERE
            is_ambient = any(key in tb_norm for key in [
                "ghiaccio", "acqua", "condensa", "bagnata", "umidita",
                "vibrazione", "vibra", "puntale scivola",
                "sporco", "residui", "clack",
            ])

            if is_structural and not is_ambient:
                structural_killers.append(b)
            else:
                others.append(b)

        if structural_killers:
            candidates = structural_killers

    # -------------------------
    # PATCH 12.3: negazioni → killer
    # -------------------------
    neg_patterns = [
        "non posso", "in quali casi non", "quando non posso",
        "quando non si puo", "non e valido", "non e ammesso",
        "non si deve", "non coperto", "non rappresentativo",
    ]
    if any(p in q_norm for p in neg_patterns):
        killer_like = []
        others = []
        for b in candidates:
            tb_norm = normalize(
                (b.get("question_it") or "") + " " +
                " ".join(b.get("triggers") or [])
            )
            if any(k in tb_norm for k in [
                "errore", "fuori campo", "non valido", "non ammesso",
                "sovra infissione", "sovra-infissione", "deformazione anomala"
            ]):
                killer_like.append(b)
            else:
                others.append(b)

        if killer_like:
            candidates = killer_like

    # -------------------------
    # PATCH STRADA A (12.2): geometria vs chiodi difettosi
    # -------------------------

    geometry_terms = [
        "lamiera", "ondina", "onda", "ala", "imbarcata",
        "laminazione", "rigonfiamento", "bombatura",
        "rigidita", "rigidezza"
    ]
    defect_terms = [
        "chiodo", "chiodi", "punta", "danneggiata",
        "danneggiato", "difettoso", "difettosi"
    ]
    question_is_geometry = any(t in q_norm for t in geometry_terms)

    if question_is_geometry:
        geometric = []
        for b in candidates:
            tb_norm = normalize(
                (b.get("id") or "") + " " +
                (b.get("question_it") or "") + " " +
                " ".join(b.get("triggers") or [])
            )
            has_defect = any(t in tb_norm for t in defect_terms)
            has_geometry = any(t in tb_norm for t in geometry_terms)

            if has_defect and not has_geometry:
                continue
            geometric.append(b)
        if geometric:
            candidates = geometric

    # -------------------------
    # PATCH 12.5: domande DIAGNOSTICHE → escludi killer
    # -------------------------
    if question_is_diagnostic:
        safe_candidates = []
        for b in candidates:
            combined_text = " ".join(filter(None, [
                b.get("id") or "",
                b.get("question_it") or "",
                " ".join(b.get("triggers") or []),
                " ".join(b.get("tags") or []),
            ]))
            tb_norm = normalize(combined_text)

            is_killer_like = False

            # ID che contengono pattern di errore
            if any(tag in (b.get("id") or "").upper() for tag in ["ERR", "KILLER", "LIMITE", "LIMITI"]):
                is_killer_like = True

            # Testo che parla di errore / non valido / fuori campo / difetto
            killer_terms = [
                "errore", "errore di posa", "fuori campo",
                "non valido", "non ammesso", "da considerarsi non valido",
                "difetto", "difettoso", "anomalia", "anomala",
                "sovra infissione", "sovra-infissione",
                "deformazione anomala", "colpo non valido",
                "testa schiacciata", "propulsore eccessivo"
            ]
            if any(term in tb_norm for term in killer_terms):
                is_killer_like = True

            if not is_killer_like:
                safe_candidates.append(b)

        # Se abbiamo almeno un candidato “sicuro”, usiamo solo quelli
        if safe_candidates:
            candidates = safe_candidates

    # -------------------------
    # PATCH 12.6: domande sui LIMITI → preferisci blocco limiti applicazione
    # -------------------------
    if question_is_limits:
        preferred = []
        others = []
        for b in candidates:
            bid = (b.get("id") or "").upper()
            if "LIMITI-APPLICAZIONE-LAMIERA" in bid:
                preferred.append(b)
            else:
                others.append(b)
        if preferred:
            candidates = preferred + others

    # ====================================
    # AI RERANK
    # ====================================

    candidate_ids = [b.get("id") for b in candidates]
    if not candidates:
        return None

    try:
        desc = "\n".join(
            f"- ID:{b.get('id')} | Q:{b.get('question_it')}"
            for b in candidates
        )

        prompt = (
            "Sei un motore di routing per una knowledge base. "
            "Ti do una domanda utente e una lista di blocchi possibili.\n\n"
            f"DOMANDA:\n{question}\n\n"
            f"CANDIDATI:\n{desc}\n\n"
            "Devi restituire SOLO l'ID del blocco che risponde meglio.\n"
            "- Evita overview se ci sono blocchi specifici.\n"
            "- Evita chiodi difettosi se la domanda parla di geometria.\n"
            "- Se la domanda parla di spessore lamiera / fuori ETA, scegli blocchi relativi a fuori campo.\n"
            "- Se la domanda è su 'come verificare / controllare', scegli blocchi che descrivono la verifica e NON blocchi di errore/fuori campo.\n"
            "- Se la domanda chiede 'in quali casi non posso usare i CTF', scegli il blocco che elenca i limiti di applicazione.\n"
            "- Rispondi SOLO con un ID presente nella lista dei candidati.\n"
        )

        res = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20,
            temperature=0.0,
        )

        chosen = (res.choices[0].message.content or "").strip()

        if chosen in candidate_ids:
            for b in candidates:
                if b.get("id") == chosen:
                    return b

    except Exception as e:
        print("[AI RERANK ERROR]", e)

    return candidates[0]


# ============================================================
# BEST BLOCK
# ============================================================

def find_best_block(question: str) -> Tuple[Dict[str, Any], float]:
    q_norm = normalize(question)

    # 1. Overlay
    over_scored = lexical_candidates(question, S.overlay_blocks)
    if over_scored:
        over_blocks = [b for s, b in over_scored]
        best_o = ai_rerank(question, over_blocks)
        best_s = max(s for s, b in over_scored if b is best_o)
        return best_o, float(best_s)

    # 2. Overview
    if is_overview_question(q_norm):
        overview_blocks = [
            b for b in S.master_blocks if "OVERVIEW" in (b.get("id") or "").upper()
        ]
        scored = lexical_candidates(question, overview_blocks)
        if scored:
            blocks = [b for s, b in scored]
            best = ai_rerank(question, blocks)
            best_s = max(s for s, b in scored if b is best)
            return best, float(best_s)

    # 3. Master
    master_scored = lexical_candidates(question, S.master_blocks)
    if not master_scored:
        return None, 0.0

    master_blocks = [b for s, b in master_scored]
    best = ai_rerank(question, master_blocks)
    best_s = max(s for s, b in master_scored if b is best)
    return best, float(best_s)


# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/health")
def health():
    return {
        "ok": True,
        "version": APP_VERSION,
        "master_blocks": len(S.master_blocks),
        "overlay_blocks": len(S.overlay_blocks),
    }


@app.post("/api/reload")
def api_reload():
    reload_all()
    return {
        "ok": True,
        "version": APP_VERSION,
        "master_blocks": len(S.master_blocks),
        "overlay_blocks": len(S.overlay_blocks),
    }


@app.post("/api/ask", response_model=AskResponse)
def api_ask(req: AskRequest):

    if req.mode.lower() != "gold":
        raise HTTPException(400, "Modalità non supportata (solo gold).")

    question = (req.question or "").strip()
    if not question:
        raise HTTPException(400, "Domanda vuota.")

    block, score = find_best_block(question)

    if block is None:
        return AskResponse(
            ok=False,
            answer=FALLBACK_MESSAGE,
            family=FALLBACK_FAMILY,
            id=FALLBACK_ID,
            mode="gold",
            lang=req.lang,
            score=0.0
        )

    answer = (
        block.get(f"answer_{req.lang}")
        or block.get("answer_it")
        or FALLBACK_MESSAGE
    )

    return AskResponse(
        ok=True,
        answer=answer,
        family=block.get("family", "CTF_SYSTEM"),
        id=block.get("id", "UNKNOWN-ID"),
        mode=block.get("mode", "gold"),
        lang=req.lang,
        score=float(score)
    )
