import os
import re
import json
import time
import threading
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# =========================
# CONFIGURAZIONE DI BASE
# =========================
DATA_PATH = "static/data/SINAPSI_GLOBAL_TECNARIA_EXT.json"
I18N_DIR = "static/i18n"
I18N_CACHE_DIR = os.getenv("I18N_CACHE_DIR", "static/i18n-cache")  # su Render: /tmp/i18n-cache

ALLOWED_LANGS = {"it", "en", "fr", "de", "es"}  # puoi aggiungerne altre
DO_NOT_TRANSLATE = [
    "Tecnaria", "CTF", "CTL", "Diapason", "GTS",
    "SPIT P560", "HSBR14", "ETA 18/0447", "ETA 13/0786",
    "mm", "µm"
]

_lock = threading.Lock()

# =========================
# UTILS: FILESYSTEM & JSON
# =========================
def ensure_dirs():
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)

    # i18n (se per sbaglio fosse un file, rimuovilo e ricrea come dir)
    if os.path.isfile(I18N_DIR):
        os.remove(I18N_DIR)
    os.makedirs(I18N_DIR, exist_ok=True)

    # i18n-cache (gestisce il caso "file al posto di cartella")
    if os.path.isfile(I18N_CACHE_DIR):
        os.remove(I18N_CACHE_DIR)
    os.makedirs(I18N_CACHE_DIR, exist_ok=True)

    # seed file lingue (possono restare vuoti)
    for lang in ALLOWED_LANGS - {"it"}:
        p = os.path.join(I18N_DIR, f"{lang}.json")
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                f.write("{}")

def _strip_json_comments_and_trailing_commas(text: str) -> str:
    # Rimuovi /* ... */ e // ...
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(?m)//.*?$", "", text)
    # Rimuovi virgole finali superflue
    text = re.sub(r",\s*([}\]])", r"\1", text)
    # BOM
    text = text.lstrip("\ufeff")
    return text

def load_json_lenient(path: str):
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        cleaned = _strip_json_comments_and_trailing_commas(raw)
        return json.loads(cleaned)

def safe_load_json(path: str) -> Dict:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

# =========================
# CARICAMENTO KB & I18N
# =========================
ensure_dirs()
try:
    KB: List[Dict] = load_json_lenient(DATA_PATH)   # lista di {id, category, q, a}
except Exception:
    KB = []
KB_BY_ID: Dict[str, Dict] = {r["id"]: r for r in KB if isinstance(r, dict) and "id" in r}

I18N: Dict[str, Dict[str, str]] = {
    lang: safe_load_json(os.path.join(I18N_DIR, f"{lang}.json"))
    for lang in ALLOWED_LANGS if lang != "it"
}
I18N_CACHE: Dict[str, Dict[str, str]] = {
    lang: safe_load_json(os.path.join(I18N_CACHE_DIR, f"{lang}.json"))
    for lang in ALLOWED_LANGS if lang != "it"
}

def persist_cache(lang: str):
    p = os.path.join(I18N_CACHE_DIR, f"{lang}.json")
    with _lock, open(p, "w", encoding="utf-8") as f:
        json.dump(I18N_CACHE[lang], f, ensure_ascii=False, indent=2)

# =========================
# RETRIEVAL SEMPLICE
# =========================
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def _token_set(q: str) -> set:
    return set(re.findall(r"[a-z0-9\-\_/\.]+", _norm(q)))

def _sim_ratio(a: str, b: str) -> float:
    ta, tb = _token_set(a), _token_set(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    jacc = inter / union
    sub_bonus = 0.15 if _norm(a) in _norm(b) and len(_norm(a)) >= 8 else 0.0
    return min(1.0, jacc + sub_bonus)

def retrieve_best_entry(query: str) -> Optional[Dict]:
    if not KB_BY_ID:
        return None
    qn = _norm(query)
    fam = None
    if "ctf" in qn:
        fam = "ctf"
    elif "ctl" in qn:
        fam = "ctl"
    elif "diapason" in qn:
        fam = "diapason"
    elif "gts" in qn or "manicott" in qn or "giunti" in qn:
        fam = "gts"

    scored: List[Tuple[float, Dict]] = []
    for r in KB:
        text = f"{r.get('q','')} || {r.get('a','')} || {r.get('id','')} || {r.get('category','')}"
        score = _sim_ratio(query, text)

        rid = (r.get("id") or "").lower()
        if fam and rid.startswith(fam):
            score += 0.15
        if ("codici" in qn or "codes" in qn) and r.get("category") == "codici_prodotti":
            score += 0.15

        scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]
    return best if best_score >= 0.18 else None

def search_entries(q: str, top_k: int = 10) -> List[Dict]:
    """Ricerca multi-campo semplice, ordinata per punteggio."""
    qn = _norm(q)
    results: List[Tuple[float, Dict]] = []
    for r in KB:
        text = f"{r.get('q','')} || {r.get('a','')} || {r.get('id','')} || {r.get('category','')}"
        score = _sim_ratio(qn, text)
        if score > 0:
            results.append((score, r))
    results.sort(key=lambda x: x[0], reverse=True)
    return [dict(item[1], _score=round(item[0], 3)) for item in results[:top_k]]

# =========================
# MULTILINGUA
# =========================
def get_lang_from_request(req: Request) -> str:
    lang = (req.query_params.get("lang") or "").lower()
    if not lang:
        accept = (req.headers.get("Accept-Language") or "").lower()
        for cand in ALLOWED_LANGS:
            if cand in accept:
                lang = cand
                break
    if not lang:
        lang = "it"
    if lang not in ALLOWED_LANGS:
        lang = "en"
    return lang

def translate_with_llm(text_it: str, target_lang: str) -> str:
    """
    Integra qui la tua traduzione (OpenAI o altro).
    Se non configurata, restituisce il testo IT (funziona comunque).
    """
    try:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if api_key:
            import openai  # type: ignore
            openai.api_key = api_key
            system = (
                "You are a technical translator for structural engineering content. "
                "Preserve product codes, brands, and units exactly as-is. "
                f"Do NOT translate these terms: {', '.join(DO_NOT_TRANSLATE)}."
            )
            user = f"Translate to {target_lang}. Text:\n{text_it}"
            resp = openai.ChatCompletion.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=0
            )
            out = resp["choices"][0]["message"]["content"].strip()
            return out or text_it
    except Exception:
        pass
    return text_it

def translate_cached(answer_it: str, id_key: str, lang: str) -> str:
    # evita cache su id mancante; IT esce diretto
    if lang == "it" or not id_key:
        return answer_it

    # 1) dizionario ufficiale (manutenzione manuale)
    txt = I18N.get(lang, {}).get(id_key)
    if txt:
        return txt

    # 2) cache runtime
    txt = I18N_CACHE.get(lang, {}).get(id_key)
    if txt:
        return txt

    # 3) traduzione on-the-fly + salva cache
    txt = translate_with_llm(answer_it, lang)
    I18N_CACHE.setdefault(lang, {})[id_key] = txt
    persist_cache(lang)
    return txt

# =========================
# RENDER HTML
# =========================
def render_card(answer_text: str, ms: int) -> str:
    return (
        '<div class="card" style="border:1px solid #e5e7eb;border-radius:12px;padding:16px;'
        'font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">'
        '<h2 style="margin:0 0 8px 0;font-size:18px;color:#111827;">Risposta Tecnaria</h2>'
        f'<p style="margin:0 0 8px 0;line-height:1.5;color:#111827;">{answer_text}</p>'
        f'<p style="margin:8px 0 0 0;color:#6b7280;font-size:12px;">⏱ {ms} ms</p>'
        '</div>'
    )

# =========================
# FASTAPI APP
# =========================
app = FastAPI(title="Tecnaria BOT", version="3.3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"ok": True, "kb_items": len(KB_BY_ID), "langs": sorted(list(ALLOWED_LANGS))}

@app.get("/debug-paths")
def debug_paths():
    def typ(p): return "dir" if os.path.isdir(p) else ("file" if os.path.isfile(p) else "missing")
    return {
        "DATA_PATH": DATA_PATH, "DATA_PATH_type": typ(DATA_PATH),
        "I18N_DIR": I18N_DIR, "I18N_DIR_type": typ(I18N_DIR),
        "I18N_CACHE_DIR": I18N_CACHE_DIR, "I18N_CACHE_DIR_type": typ(I18N_CACHE_DIR),
        "ALLOWED_LANGS": sorted(list(ALLOWED_LANGS))
    }

@app.post("/reload-kb")
def reload_kb():
    global KB, KB_BY_ID
    try:
        new_kb = load_json_lenient(DATA_PATH)
        KB = new_kb
        KB_BY_ID = {r["id"]: r for r in KB if isinstance(r, dict) and "id" in r}
        return {"ok": True, "kb_items": len(KB_BY_ID)}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@app.get("/kb/ids")
def kb_ids():
    """Elenco sintetico delle voci del KB (id e categoria)."""
    out = [{"id": r.get("id", ""), "category": r.get("category", "")} for r in KB]
    return {"count": len(out), "items": out[:50]}  # prime 50 per non esagerare

@app.get("/kb/search")
def kb_search(q: str = Query("", description="Testo da cercare nel KB"), k: int = 10):
    """Ricerca veloce nel KB; ritorna i migliori k match con punteggio."""
    if not q.strip():
        return {"ok": True, "count": 0, "items": []}
    k = max(1, min(50, k))
    items = search_entries(q, top_k=k)
    # riduci i campi dell'answer per non inviare testi lunghi in debug
    slim = []
    for r in items:
        slim.append({
            "id": r.get("id", ""),
            "category": r.get("category", ""),
            "_score": r.get("_score", 0.0),
            "q": r.get("q", "")[:200],
            "a": r.get("a", "")[:200] + ("…" if len(r.get("a","")) > 200 else "")
        })
    return {"ok": True, "count": len(slim), "items": slim}

@app.post("/api/ask")
async def api_ask(request: Request):
    t0 = time.time()
    try:
        data = await request.json()
    except Exception:
        data = {}
    q = (data.get("q") or "").strip()
    if not q:
        html = render_card("Domanda vuota.", int((time.time()-t0)*1000))
        return JSONResponse({"ok": True, "html": html})

    entry = retrieve_best_entry(q)
    if not entry:
        html = render_card("Non ho trovato elementi sufficienti su domini autorizzati o nelle regole. Raffina la domanda o aggiorna le regole.", int((time.time()-t0)*1000))
        return JSONResponse({"ok": True, "html": html})

    lang = get_lang_from_request(request)
    id_key = entry.get("id", "")
    answer_it = entry.get("a", "")

    answer_out = translate_cached(answer_it, id_key, lang)

    ms = int((time.time() - t0) * 1000)
    html = render_card(answer_out, ms)
    return JSONResponse({"ok": True, "html": html})

# =========================
# AVVIO LOCALE
# =========================
if __name__ == "__main__":
    # Avvio rapido:  uvicorn app:app --reload --host 0.0.0.0 --port 8000
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
