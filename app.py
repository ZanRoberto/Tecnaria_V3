import os
import json
import re
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from openai import OpenAI

# ============================================================
# CONFIG
# ============================================================

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(STATIC_DIR, "data")

MASTER_PATH = os.path.join(DATA_DIR, "ctf_system_COMPLETE_GOLD_master.json")
OVERLAY_PATH = os.path.join(DATA_DIR, "overlay_dynamic.json")

# ============================================================
# LOAD KB
# ============================================================

def load_json(path):
    if not os.path.exists(path):
        return {"blocks": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

master_kb = load_json(MASTER_PATH)
overlay_kb = load_json(OVERLAY_PATH)


# ============================================================
# FAST FILTERING FOR MATCHING
# ============================================================

def normalize_text(t: str) -> str:
    t = t.lower()
    t = re.sub(r"[^a-z0-9àèéìòùç\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def match_blocks(user_q: str, blocks):
    nq = normalize_text(user_q)
    best = []

    for b in blocks:
        score = 0

        # Trigger match
        for trig in b.get("triggers", []):
            if normalize_text(trig) in nq:
                score += 4

        # Keyword match
        for w in nq.split():
            if w in normalize_text(b.get("question_it", "")):
                score += 1

        if score > 0:
            best.append((score, b))

    best.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in best[:3]]


# ============================================================
# AI CALL
# ============================================================

def ask_chatgpt(question: str) -> str:
    try:
        r = client.chat.completions.create(
            model="gpt-5.1",
            messages=[
                {"role": "system", "content": (
                    "Sei l'ingegnere capo Tecnaria Spa (Bassano). "
                    "Rispondi solo su CTF, P560, lamiera grecata, CTL, VCEM, CTCEM, Diapason. "
                    "Tono GOLD tecnico aziendale. "
                    "Vietato inventare prodotti non Tecnaria. "
                    "Rispondi in italiano."
                )},
                {"role": "user", "content": question}
            ],
            max_tokens=800,
            temperature=0.2
        )
        return r.choices[0].message.content.strip()
    except:
        return None


# ============================================================
# JUDGE INTERNO
# ============================================================

def judge_answer(question, ai_answer, kb_answers):
    """
    Decide la migliore risposta:
    - se AI è ottima → vince AI
    - se JSON è più aderente → vince JSON
    - se AI è fuori contesto → JSON
    """

    try:
        judge = client.chat.completions.create(
            model="gpt-5.1",
            messages=[
                {"role": "system", "content": (
                    "Devi giudicare quale risposta è tecnicamente più corretta "
                    "e 100% conforme alla documentazione Tecnaria Spa. "
                    "Rispondi SOLO con: AI / KB."
                )},
                {"role": "user", "content": f"Domanda: {question}"},
                {"role": "assistant", "content": f"Risposta AI: {ai_answer}"},
                {"role": "assistant", "content": f"Risposte KB: {json.dumps(kb_answers, ensure_ascii=False)}"},
            ],
            max_tokens=5,
            temperature=0
        )

        result = judge.choices[0].message.content.strip().upper()
        if "AI" in result:
            return "AI"
        return "KB"

    except:
        return "KB"


# ============================================================
# SAVE IMPROVED ANSWERS TO OVERLAY
# ============================================================

def save_overlay(question, answer):
    overlay_kb["blocks"].append({
        "id": f"OVER-{len(overlay_kb['blocks'])+1}",
        "question_it": question,
        "answer_it": answer,
        "mode": "gold",
        "family": "AUTO",
        "triggers": [question.lower()],
        "lang": "it"
    })

    with open(OVERLAY_PATH, "w", encoding="utf-8") as f:
        json.dump(overlay_kb, f, ensure_ascii=False, indent=2)


# ============================================================
# MODELLI FASTAPI
# ============================================================

class AskModel(BaseModel):
    question: str


# ============================================================
# FASTAPI SETUP
# ============================================================

app = FastAPI()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ============================================================
# ENGINE
# ============================================================

@app.post("/api/ask")
def api_ask(data: AskModel):

    q = data.question.strip()

    # 1) Prova AI
    ai_answer = ask_chatgpt(q)

    # 2) Cerca nel KB
    kb_candidates = match_blocks(q, master_kb["blocks"] + overlay_kb["blocks"])
    kb_best = kb_candidates[0]["answer_it"] if kb_candidates else None

    # 3) Fail-safe
    if ai_answer is None and kb_best:
        return {"answer": kb_best, "source": "KB_FAILSAFE"}

    if ai_answer is None and kb_best is None:
        return {"answer": "Non trovo nulla né in AI né in KB. Serve chiarimento.", "source": "FAILSAFE_EMPTY"}

    # 4) Judge interno
    if kb_best:
        chosen = judge_answer(q, ai_answer, kb_candidates)
        if chosen == "AI":
            save_overlay(q, ai_answer)
            return {"answer": ai_answer, "source": "AI"}
        else:
            return {"answer": kb_best, "source": "KB"}
    else:
        save_overlay(q, ai_answer)
        return {"answer": ai_answer, "source": "AI_NO_KB"}


@app.get("/health")
def health():
    return {
        "status": "Tecnaria Bot v14.3 MISTO FAIL-SAFE attivo",
        "master_blocks": len(master_kb["blocks"]),
        "overlay_blocks": len(overlay_kb["blocks"])
    }
