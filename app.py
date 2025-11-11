# ==========================================================
# TECNARIA Sinapsi — Backend GOLD
# FastAPI + JSON families (CTF, VCEM, CTCEM, CTL, CTL_MAXI, DIAPASON, P560, COMM)
# ==========================================================
import os, json
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime

# ==========================================================
# INIT APP
# ==========================================================
app = FastAPI(title="TECNARIA Sinapsi – Backend", version="3.0-GOLD")

# ----------------------------------------------------------
# STATIC FILES
# ----------------------------------------------------------
app.mount("/static", StaticFiles(directory="static"), name="static")

# ----------------------------------------------------------
# CORS
# ----------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================================
# GLOBAL CONFIG
# ==========================================================
CONFIG_PATH = "static/data/config.runtime.json"

if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        runtime_config = json.load(f)
else:
    runtime_config = {
        "ok": True,
        "message": "TECNARIA Sinapsi backend attivo",
        "mode": "gold",
        "families": [
            "COMM", "CTCEM", "CTF", "CTL", "CTL_MAXI",
            "DIAPASON", "P560", "TECNARIA_GOLD", "VCEM"
        ]
    }

# ==========================================================
# HEALTH CHECK
# ==========================================================
@app.get("/api/health")
def health_check():
    return {"status": "online", "mode": runtime_config.get("mode", "gold"), "timestamp": datetime.now().isoformat()}

# ==========================================================
# CONFIG ENDPOINT
# ==========================================================
@app.get("/api/config")
def get_config():
    return runtime_config

@app.post("/api/config")
async def update_config(request: Request):
    data = await request.json()
    runtime_config.update(data)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(runtime_config, f, indent=2, ensure_ascii=False)
    return {"ok": True, "updated": data}

# ==========================================================
# ASK ENDPOINT (main engine)
# ==========================================================
@app.post("/api/ask")
async def ask_question(request: Request):
    data = await request.json()
    question = data.get("question", "").strip()
    lang = data.get("lang", "it").lower()
    mode = runtime_config.get("mode", "gold").lower()

    if not question:
        return {"error": "Domanda vuota", "mode": mode}

    # Cerca nei JSON caricati
    base_dir = "static/data"
    best_match = None
    best_score = 0.0

    for file in os.listdir(base_dir):
        if not file.endswith(".json"):
            continue
        path = os.path.join(base_dir, file)
        try:
            with open(path, "r", encoding="utf-8") as f:
                j = json.load(f)
        except:
            continue

        family = j.get("family", file.replace(".json", ""))
        for item in j.get("items", []):
            for q in item.get("questions", []):
                # Matching base (lowercase inclusion)
                if question.lower() in q.lower() or q.lower() in question.lower():
                    score = len(set(question.lower().split()) & set(q.lower().split()))
                    if score > best_score:
                        best_score = score
                        best_match = {
                            "question": q,
                            "answer": item.get("canonical", ""),
                            "variants": item.get("response_variants", []),
                            "family": family,
                            "id": item.get("id", ""),
                            "lang": lang,
                            "mode": mode,
                            "score": round(float(score), 2)
                        }

    if not best_match:
        return {
            "question": question,
            "response": "Per questa domanda non è ancora presente una risposta GOLD nei file Tecnaria.",
            "mode": mode
        }

    # Se esistono varianti GOLD, scegli una casuale
    import random
    variants = best_match.get("variants", [])
    answer = best_match.get("answer", "")
    if mode == "gold" and variants:
        answer = random.choice(variants)

    return {
        "question": question,
        "response": answer,
        "family": best_match.get("family"),
        "id": best_match.get("id"),
        "score": best_match.get("score"),
        "lang": lang,
        "mode": mode
    }

# ==========================================================
# FRONTEND ROOT — redirect automatico a interfaccia Q/A
# ==========================================================
@app.get("/", response_class=FileResponse)
def root():
    index_path = os.path.join("static", "interface", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse(
        content=f"""
        <html><body style='font-family:Arial;text-align:center;margin-top:40px;'>
        <h2>TECNARIA Sinapsi - Backend</h2>
        <p>Modalità: <b style='color:orange;'>GOLD</b></p>
        <hr>
        <p>Use <code>/api/ask</code> to query, <code>/api/config</code> to view/update runtime config.</p>
        </body></html>
        """,
        status_code=200
    )

# ==========================================================
# FAVICON
# ==========================================================
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    icon_path = os.path.join("static", "interface", "favicon.ico")
    if os.path.exists(icon_path):
        return FileResponse(icon_path)
    return HTMLResponse(status_code=404, content="No favicon found")
