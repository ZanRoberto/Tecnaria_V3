import os
import json
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from pydantic import BaseModel

from openai import OpenAI

# ============================================================
#  PATH / FILE
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(STATIC_DIR, "data")

KB_PATH = os.path.join(DATA_DIR, "ctf_system_COMPLETE_GOLD_master.json")

# Cartella per i candidati GOLD nuovi (salvati da ChatGPT esterno)
NEW_GOLD_DIR = os.path.join(DATA_DIR, "new_gold_candidates")
os.makedirs(NEW_GOLD_DIR, exist_ok=True)

NEW_GOLD_LOG = os.path.join(NEW_GOLD_DIR, "new_gold_index.json")

FALLBACK_FAMILY = "COMM"
FALLBACK_ID = "COMM-FALLBACK-NOANSWER-0001"
FALLBACK_MESSAGE = (
    "Per questa domanda non trovo una risposta GOLD nei dati caricati. "
    "Meglio un confronto diretto con l’ufficio tecnico Tecnaria, indicando tipo di solaio, "
    "famiglia di connettori (CTF, P560, CTL, CTL MAXI, VCEM, CTCEM, DIAPASON) e quadro "
    "statico. In questo modo il supporto sarà rapido e mirato."
)

# ============================================================
#  OPENAI CLIENT (per ChatGPT esterno)
# ============================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client: Optional[OpenAI] = None
if OPENAI_API_KEY:
    client = OpenAI()

# ============================================================
#  FASTAPI APP
# ============================================================

app = FastAPI(title="Tecnaria GOLD Bot v14.6")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve /static (immagini, CSS, index.html, ecc.)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ============================================================
#  MODELLI
# ============================================================

class AskRequest(BaseModel):
    question: str
    lang: str = "it"


# ============================================================
#  UTILITY NORMALIZZAZIONE TESTO
# ============================================================

def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9àèéìòóùçãõüäöß\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ============================================================
#  CARICAMENTO KB
# ============================================================

def load_kb(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"KB file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    blocks = data.get("blocks", [])
    return blocks


MASTER_BLOCKS: List[Dict[str, Any]] = []
try:
    MASTER_BLOCKS = load_kb(KB_PATH)
    print(f"[KB LOADED] master={len(MASTER_BLOCKS)} overlay=0")
except Exception as e:
    print(f"[KB ERROR] {e}")


# ============================================================
#  MATCHING SEMPLICE SUI BLOCKS
# ============================================================

def score_block(block: Dict[str, Any], q_norm: str) -> float:
    """
    Matching lessicale grezzo: serve per avere un "best_json_answer"
    anche quando c'è ChatGPT esterno.
    """
    score = 0.0
    triggers = block.get("triggers", [])
    question_it = block.get("question_it") or ""
    answer_it = block.get("answer_it") or ""

    haystack = " ".join(triggers + [question_it, answer_it])
    haystack_norm = normalize_text(haystack)

    if not haystack_norm:
        return 0.0

    for term in q_norm.split():
        if term in haystack_norm:
            score += 1.0

    tags = block.get("tags", [])
    tags_norm = " ".join(tags).lower()
    for term in ["ctf", "p560", "lamiera", "card", "chiodo", "tecnaria"]:
        if term in q_norm and term in tags_norm:
            score += 0.5

    if block.get("mode", "").lower() == "gold":
        score += 0.3

    return score


def find_best_json_answer(question: str, lang: str = "it") -> Optional[Dict[str, Any]]:
    q_norm = normalize_text(question)
    best_block = None
    best_score = 0.0

    for block in MASTER_BLOCKS:
        if block.get("lang", "it") != lang:
            continue
        s = score_block(block, q_norm)
        if s > best_score:
            best_score = s
            best_block = block

    if not best_block or best_score <= 0.0:
        return None

    answer_key = f"answer_{lang}"
    question_key = f"question_{lang}"

    return {
        "id": best_block.get("id"),
        "family": best_block.get("family"),
        "question": best_block.get(question_key) or "",
        "answer": best_block.get(answer_key) or "",
        "score": best_score,
        "mode": best_block.get("mode", ""),
    }


# ============================================================
#  CHATGPT ESTERNO (LLM)
# ============================================================

async def ask_external_gpt(question: str, lang: str = "it") -> Optional[str]:
    """
    Chiama ChatGPT esterno (OpenAI Responses API) con istruzioni chiare:
    - parlare SOLO di Tecnaria S.p.A. (Bassano del Grappa)
    - se non è ambito Tecnaria, dire che è fuori ambito
    """
    if client is None:
        return None

    system_prompt = (
        "Sei l'assistente tecnico-commerciale ufficiale di Tecnaria S.p.A. "
        "(Bassano del Grappa). Devi rispondere SOLO su prodotti, sistemi e "
        "applicazioni Tecnaria (CTF, P560, CTL, CTL MAXI, VCEM, CTCEM, DIAPASON, ecc.). "
        "Se la domanda non riguarda Tecnaria, rispondi chiaramente che è fuori ambito. "
        "Stile: tecnico, preciso, modalità GOLD."
    )

    try:
        resp = client.responses.create(
            model="gpt-4.1-mini",
            reasoning={"effort": "medium"},
            input=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": f"[Lingua: {lang}]\nDomanda: {question}",
                },
            ],
            max_output_tokens=900,
        )
        content = resp.output[0].content[0].text
        return content.strip()
    except Exception as e:
        print(f"[EXTERNAL GPT ERROR] {e}")
        return None


# ============================================================
#  SALVATAGGIO CANDIDATI GOLD (NUOVE RISPOSTE)
# ============================================================

def save_new_candidate(question: str, answer: str, source: str) -> None:
    """
    Salva una nuova Q/A proposta da ChatGPT esterno per futura integrazione nel master JSON.
    """
    os.makedirs(NEW_GOLD_DIR, exist_ok=True)

    # Log indice
    if os.path.exists(NEW_GOLD_LOG):
        try:
            with open(NEW_GOLD_LOG, "r", encoding="utf-8") as f:
                log_data = json.load(f)
        except Exception:
            log_data = {"items": []}
    else:
        log_data = {"items": []}

    item_id = f"CAND-{len(log_data['items'])+1:05d}"

    filename = os.path.join(NEW_GOLD_DIR, f"{item_id}.json")
    payload = {
        "id": item_id,
        "question": question,
        "answer": answer,
        "source": source,
    }
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    log_data["items"].append(
        {
            "id": item_id,
            "file": os.path.basename(filename),
            "source": source,
        }
    )
    with open(NEW_GOLD_LOG, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)

    print(f"[NEW GOLD CANDIDATE] {item_id} saved from {source}")


# ============================================================
#  GIUDICE: CONFRONTO EXTERNAL vs JSON
# ============================================================

async def ask_judge(
    question: str, external_answer: Optional[str], json_answer: Optional[str]
) -> str:
    """
    Usa ChatGPT come GIUDICE:
    - gli passo la domanda + risposta esterna + risposta JSON
    - deve dirmi quale usare: "chatgpt", "json" oppure "mix"
    """
    if client is None:
        # Se non c'è il client, per sicurezza preferisco JSON
        return "json" if json_answer else "none"

    if not external_answer and not json_answer:
        return "none"
    if external_answer and not json_answer:
        return "chatgpt"
    if json_answer and not external_answer:
        return "json"

    judge_prompt = (
        "Sei il GIUDICE TECNICO del bot Tecnaria.\n"
        "Ricevi:\n"
        "- la domanda dell'utente\n"
        "- una risposta generata da ChatGPT esterno (basata sul web e sulle tue conoscenze)\n"
        "- una risposta proveniente dal JSON interno (kb Tecnaria)\n\n"
        "Devi decidere quale risposta è MIGLIORE per un contesto tecnico-strutturale reale.\n"
        "Criteri:\n"
        "1) correttezza tecnica rispetto ai prodotti Tecnaria\n"
        "2) aderenza al perimetro Tecnaria S.p.A.\n"
        "3) chiarezza e utilità pratica per progettista/cantiere.\n\n"
        "Rispondi SOLO con una di queste parole:\n"
        "- 'chatgpt' se è meglio la risposta esterna\n"
        "- 'json' se è meglio la risposta JSON\n"
        "- 'mix' se sono entrambe utili e ha senso combinarle\n"
        "- 'none' se nessuna delle due è utilizzabile."
    )

    try:
        resp = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {"role": "system", "content": judge_prompt},
                {
                    "role": "user",
                    "content": (
                        f"DOMANDA:\n{question}\n\n"
                        f"RISPOSTA_CHATGPT:\n{external_answer}\n\n"
                        f"RISPOSTA_JSON:\n{json_answer}\n\n"
                        "Decidi quale usare (chatgpt/json/mix/none)."
                    ),
                },
            ],
            max_output_tokens=50,
        )
        verdict = resp.output[0].content[0].text.strip().lower()
        verdict = verdict.split()[0]
        if verdict not in {"chatgpt", "json", "mix", "none"}:
            return "json" if json_answer else "chatgpt"
        return verdict
    except Exception as e:
        print(f"[JUDGE ERROR] {e}")
        # Se il giudice fallisce, preferisco il JSON (più controllato)
        return "json" if json_answer else "chatgpt"


# ============================================================
#  ENDPOINTS
# ============================================================

@app.get("/", response_class=FileResponse)
async def root():
    """Serve la UI principale Tecnaria (index.html)."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    return FileResponse(index_path)


@app.get("/health")
def health():
    return {
        "status": "Tecnaria Bot v14.6 attivo",
        "master_blocks": len(MASTER_BLOCKS),
    }


@app.post("/api/ask")
async def ask(req: AskRequest):
    if not req.question or not req.question.strip():
        raise HTTPException(status_code=400, detail="Domanda vuota.")

    question = req.question.strip()
    lang = req.lang or "it"

    # 1) Tentativo ChatGPT esterno (sempre per primo, come richiesto)
    external_answer = await ask_external_gpt(question, lang=lang)

    # 2) Tentativo JSON interno (sempre calcolato per avere un confronto)
    best_json = find_best_json_answer(question, lang=lang)
    json_answer = best_json.get("answer") if best_json else None

    # 3) Se non c'è proprio nulla, fallback duro
    if not external_answer and not json_answer:
        return {
            "answer": FALLBACK_MESSAGE,
            "source": "fallback",
        }

    # 4) Se c'è solo una delle due, usiamo quella
    if external_answer and not json_answer:
        # Salvo come candidato GOLD
        save_new_candidate(question, external_answer, source="chatgpt_only")
        return {
            "answer": external_answer,
            "source": "chatgpt",
        }

    if json_answer and not external_answer:
        return {
            "answer": json_answer,
            "source": "json",
        }

    # 5) Abbiamo entrambe → chiedo al GIUDICE
    verdict = await ask_judge(question, external_answer, json_answer)

    if verdict == "chatgpt":
        save_new_candidate(question, external_answer, source="chatgpt_wins")
        final = external_answer
    elif verdict == "json":
        final = json_answer
    elif verdict == "mix":
        combined = (
            "Risposta da ChatGPT esterno (validata):\n"
            f"{external_answer}\n\n"
            "Integrazione dalla knowledge base Tecnaria (JSON):\n"
            f"{json_answer}"
        )
        save_new_candidate(question, combined, source="mix")
        final = combined
    else:
        # 'none' → come massima prudenza torno al fallback
        final = FALLBACK_MESSAGE

    return {
        "answer": final,
        "source": verdict.lower(),
    }
