from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse

# =========================
# CONFIG
# =========================

USE_OPENAI = True  # se non hai la key, userà solo i testi dei JSON

try:
    from openai import OpenAI

    if USE_OPENAI and os.getenv("OPENAI_API_KEY"):
        openai_client = OpenAI()
    else:
        openai_client = None
except Exception:
    openai_client = None

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = STATIC_DIR / "data"
INDEX_HTML = STATIC_DIR / "index.html"

RUNTIME_CONFIG_PATH = DATA_DIR / "config.runtime.json"


def safe_read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# Eventuale config runtime, NON cambia la logica delle risposte
RUNTIME_CONFIG: Dict[str, Any] = {}
if RUNTIME_CONFIG_PATH.exists():
    try:
        RUNTIME_CONFIG = safe_read_json(RUNTIME_CONFIG_PATH)
    except Exception:
        RUNTIME_CONFIG = {}

app = FastAPI(title="Tecnaria Sinapsi — Q/A")

# =========================
# MODALITÀ GLOBALE PERSISTENTE
# =========================
# Parte in GOLD e resta GOLD finché non riceve CANONICO:
# Passa in CANONICO e resta CANONICO finché non riceve GOLD:
CURRENT_MODE: str = "gold"

# Cache famiglie
_family_cache: Dict[str, List[Dict[str, Any]]] = {}

FAMILY_KEYWORDS = {
    "ctf": "CTF",
    "ctl maxi": "CTL_MAXI",
    "ctl_maxi": "CTL_MAXI",
    "ctl": "CTL",
    "vcem": "VCEM",
    "ctcem": "CTCEM",
    "p560": "P560",
    "diapason": "DIAPASON",
    "comm": "COMM",
}

# =========================
# LETTURA JSON / FAMIGLIE
# =========================

def extract_blocks(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        blocks = [b for b in data if isinstance(b, dict)]
    elif isinstance(data, dict):
        blocks = []
        for key in ("items", "blocks", "data"):
            v = data.get(key)
            if isinstance(v, list):
                blocks = [b for b in v if isinstance(b, dict)]
                break
    else:
        blocks = []

    for i, b in enumerate(blocks):
        if "id" not in b:
            b["id"] = f"AUTO-{i:04d}"
    return blocks


def load_family(family: str) -> List[Dict[str, Any]]:
    fam = family.upper()
    if fam in _family_cache:
        return _family_cache[fam]

    candidates = [
        DATA_DIR / f"{fam}.json",
        DATA_DIR / f"{fam}.gold.json",
        DATA_DIR / f"{fam}.golden.json",
    ]

    path: Optional[Path] = next((p for p in candidates if p.exists()), None)

    if path is None and DATA_DIR.exists():
        for f in DATA_DIR.glob(f"{fam}*.json"):
            name_up = f.name.upper()
            if "CONFIG.RUNTIME" in name_up:
                continue
            path = f
            break

    if path is None:
        raise HTTPException(
            status_code=404,
            detail=f"File JSON per famiglia '{family}' non trovato.",
        )

    data = safe_read_json(path)
    blocks = extract_blocks(data)
    _family_cache[fam] = blocks
    return blocks


def list_all_families() -> List[str]:
    fams: List[str] = []
    if not DATA_DIR.exists():
        return fams

    for f in DATA_DIR.glob("*.json"):
        name = f.stem.upper()
        if "CONFIG.RUNTIME".upper() in name:
            continue
        if name.endswith(".GOLD"):
            name = name[:-5]
        fams.append(name)

    return sorted(set(fams))

# =========================
# UTILITY
# =========================

def norm(s: str) -> str:
    return " ".join(s.lower().strip().split())


def extract_queries(block: Dict[str, Any]) -> List[str]:
    """
    Testi per il matching del blocco.
    canonical qui è SOLO per agganciare, NON per rispondere in GOLD.
    """
    out: List[str] = []

    for key in ("q", "question", "domanda", "title", "label"):
        v = block.get(key)
        if isinstance(v, str):
            t = v.strip()
            if t:
                out.append(t)

    for key in ("questions", "paraphrases", "variants", "triggers"):
        v = block.get(key)
        if isinstance(v, list):
            for e in v:
                if isinstance(e, str):
                    t = e.strip()
                    if t:
                        out.append(t)

    tags = block.get("tags")
    if isinstance(tags, list):
        for t in tags:
            if isinstance(t, str):
                tt = t.strip()
                if tt:
                    out.append(tt)

    canon = block.get("canonical")
    if isinstance(canon, str):
        c = canon.strip()
        if c:
            out.append(c[:220])

    return out


def base_similarity(query: str, block: Dict[str, Any]) -> float:
    q = norm(query)
    if not q:
        return 0.0

    queries = extract_queries(block)
    if not queries:
        return 0.0

    sq = set(q.split())
    if not sq:
        return 0.0

    best = 0.0

    for cand in queries:
        c = norm(cand)
        if not c:
            continue

        if q == c:
            return 1.0

        if q in c or c in q:
            best = max(best, 0.9)
            continue

        sc = set(c.split())
        if not sc:
            continue

        inter = len(sq & sc)
        if inter == 0:
            continue

        j = inter / float(len(sq | sc))
        if j > best:
            best = j

    return best

# =========================
# RILEVAMENTO LINGUA
# =========================

def detect_lang(query: str) -> str:
    """
    Regole:
    - Se la frase INIZIA chiaramente in una lingua straniera (FR/EN/ES/DE) => quella lingua.
    - Altrimenti controlla marker tecnici / parole chiave.
    - Default assoluto: IT (il tuo dominio è italiano-centrico).
    """
    q = query.strip()
    if not q:
        return "it"

    q_low = q.lower()

    # Prima parola / token iniziale per riconoscere lingua
    # (ignoro eventuali ? ! iniziali tipo spagnolo)
    stripped = q_low.lstrip("¿¡?!.,;: ")
    first_token = stripped.split()[0] if stripped.split() else ""

    # ---- FRANCESE (inizio frase) ----
    fr_starters = {
        "bonjour", "bonsoir", "salut",
        "puis-je", "est-ce", "est-ce-que",
        "comment", "pourquoi", "quel", "quelle", "quels", "quelles",
        "je", "nous", "vous", "ils", "elles",
        "avec", "sans", "sur", "sous", "entre", "pour", "chez",
    }
    if first_token in fr_starters or stripped.startswith("puis-je") or "est-ce que" in stripped:
        return "fr"

    # ---- SPAGNOLO (inizio frase) ----
    if q_low[0] in ("¿", "¡"):
        return "es"
    es_starters = {
        "hola", "buenos", "buenas",
        "puedo", "podemos", "quisiera", "necesito", "tengo",
        "quiero", "donde", "cómo", "porqué", "porque",
    }
    if first_token in es_starters:
        return "es"

    # ---- TEDESCO (inizio frase) ----
    de_starters = {
        "hallo", "guten",
        "kann", "kannich", "kann-ich",
        "könnte", "kannst", "können",
        "ich", "wir", "sie", "gibt", "brauche", "möchte",
    }
    if first_token in de_starters or stripped.startswith("kann ich"):
        return "de"

    # ---- INGLESE (inizio frase) ----
    en_starters = {
        "hi", "hello", "dear",
        "can", "could", "should", "would",
        "is", "are", "do", "does", "how", "what", "where", "why", "when",
        "please",
    }
    if first_token in en_starters or stripped.startswith("can i "):
        return "en"

    # ---- MARKER DI CONTENUTO (come versione precedente) ----

    # Italiani (tecnici + lingua naturale)
    it_markers = [
        " connettore", " connettori", " soletta", " trave", " travetto",
        " calcestruzzo", " laterocemento", " lamiera", " chiodatrice",
        " pistola", " cartucce", " posa", " cemento armato",
        " posso ", " devo ", " voglio ", " quale ", " dove ",
        " usare ", " sparare ", " si può", " si possono", " è possibile ",
    ]
    if any(m in q_low for m in it_markers):
        return "it"

    fr_markers = ["béton", "connecteur", "plancher", "poutre", "acier"]
    if any(m in q_low for m in fr_markers):
        return "fr"

    es_markers = ["hormigón", "forjado", "viga de madera", "conectores", "losa"]
    if any(m in q_low for m in es_markers):
        return "es"

    de_markers = ["verbinder", "beton", "holz", "decken", "stahlträger", "verbund"]
    if any(m in q_low for m in de_markers):
        return "de"

    en_markers = [
        " beam", " beams", " steel", " timber", " composite", " deck",
        " slab", " connector", " connectors", " design", " load", " capacity"
    ]
    if any(m in q_low for m in en_markers):
        return "en"

    # Caratteri non ASCII generici → probabile EU, ma siccome hai tanta Italia:
    if not all(ord(c) < 128 for c in q):
        return "it"

    # Default assoluto: italiano
    return "it"

# =========================
# FAMIGLIE & ROUTING
# =========================

def detect_explicit_families(query: str) -> List[str]:
    q = query.lower()
    hits: List[str] = []

    if "ctl maxi" in q or "ctl_maxi" in q:
        hits.append("CTL_MAXI")

    for key, fam in FAMILY_KEYWORDS.items():
        if key in ("ctl maxi", "ctl_maxi"):
            continue
        if re.search(r"\b" + re.escape(key) + r"\b", q):
            if fam not in hits:
                hits.append(fam)

    return hits


def score_block_routed(
    query: str,
    block: Dict[str, Any],
    fam: str,
    explicit_fams: List[str],
) -> float:
    base = base_similarity(query, block)
    if base <= 0:
        return 0.0

    fam_u = fam.upper()
    q_low = query.lower()

    if explicit_fams:
        if fam_u in explicit_fams:
            base *= 8.0
        else:
            base *= 0.05
        return base

    if any(k in q_low for k in ["p560", "pistola", "chiodatrice", "sparo", "cartuccia", "cartucce"]):
        if fam_u == "P560":
            base *= 5.0
        else:
            base *= 0.4

    if "legno" in q_low or "trave in legno" in q_low:
        if fam_u in ["CTL", "CTL_MAXI"]:
            base *= 3.0
        elif fam_u in ["CTF", "VCEM", "CTCEM", "P560", "DIAPASON"]:
            base *= 0.4

    if any(k in q_low for k in ["laterocemento", "travetto", "travetti"]):
        if fam_u in ["VCEM", "CTCEM", "DIAPASON"]:
            base *= 3.0
        elif fam_u in ["CTF", "CTL", "CTL_MAXI", "P560"]:
            base *= 0.4

    return base

# =========================
# TOGGLE PERSISTENTE GOLD / CANONICO
# =========================

def update_mode_and_clean_query(raw_q: str, provided_mode: Optional[str]) -> (str, str):
    """
    Logica:
    - CURRENT_MODE parte "gold".
    - "GOLD:" => CURRENT_MODE = "gold" (persistente).
    - "CANONICO:" / "CANONICAL:" => CURRENT_MODE = "canonical" (persistente).
    - mode=gold/canonical nei parametri => aggiorna CURRENT_MODE (persistente).
    - Se nessun comando => usa CURRENT_MODE.
    """
    global CURRENT_MODE

    q = raw_q.strip()
    lower = q.lower()

    # Prefissi testuali
    if lower.startswith("gold:"):
        CURRENT_MODE = "gold"
        return q[5:].strip(), CURRENT_MODE

    if lower.startswith("canonico:") or lower.startswith("canonical:"):
        if lower.startswith("canonico:"):
            cleaned = q[len("canonico:"):].strip()
        else:
            cleaned = q[len("canonical:"):].strip()
        CURRENT_MODE = "canonical"
        return cleaned, CURRENT_MODE

    # Parametro esplicito
    if provided_mode:
        m = provided_mode.strip().lower()
        if m in ("gold", "gold-only", "dynamic"):
            CURRENT_MODE = "gold"
        elif m in ("canonical", "canonico", "base"):
            CURRENT_MODE = "canonical"

    if CURRENT_MODE not in ("gold", "canonical"):
        CURRENT_MODE = "gold"

    return q, CURRENT_MODE

# =========================
# EXTRACT ANSWER
# =========================

def extract_answer(block: Dict[str, Any], lang: str, mode: str) -> Optional[str]:
    """
    GOLD:
      - usa response_variants (priorità),
      - poi answers / answer_it,
      - MAI canonical.
    CANONICO:
      - usa canonical,
      - se manca, answers / answer_it / legacy.
    """
    if mode == "gold":
        primary: List[str] = []
        gold_candidates: List[str] = []

        # answers multilingua
        answers = block.get("answers")
        if isinstance(answers, dict):
            for key in (lang, lang.lower(), lang.upper()):
                v = answers.get(key)
                if isinstance(v, str) and v.strip():
                    primary.append(v.strip())
                    break
            if not primary:
                for v in answers.values():
                    if isinstance(v, str) and v.strip():
                        primary.append(v.strip())
                        break

        # answer_it
        answer_it = block.get("answer_it")
        if isinstance(answer_it, str) and answer_it.strip():
            primary.append(answer_it.strip())

        # GOLD variants
        variants_raw = block.get("response_variants")
        if isinstance(variants_raw, list):
            gold_candidates.extend(
                [v.strip() for v in variants_raw if isinstance(v, str) and v.strip()]
            )
        elif isinstance(variants_raw, dict):
            for v in variants_raw.values():
                if isinstance(v, list):
                    for e in v:
                        if isinstance(e, str) and e.strip():
                            gold_candidates.append(e.strip())
                elif isinstance(v, str) and v.strip():
                    gold_candidates.append(v.strip())

        if gold_candidates:
            rich = [t for t in gold_candidates if len(t) >= 160]
            source = rich if rich else gold_candidates
            return max(source, key=len).strip()

        if primary:
            rich = [t for t in primary if len(t) >= 80]
            source = rich if rich else primary
            return max(source, key=len).strip()

        return None

    # CANONICO
    canonical = block.get("canonical")
    if isinstance(canonical, str) and canonical.strip():
        return canonical.strip()

    answers = block.get("answers")
    if isinstance(answers, dict):
        for v in answers.values():
            if isinstance(v, str) and v.strip():
                return v.strip()

    answer_it = block.get("answer_it")
    if isinstance(answer_it, str) and answer_it.strip():
        return answer_it.strip()

    for key in ("answer", "risposta", "text", "content"):
        v = block.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()

    return None

# =========================
# GOLD REFINE (solo in GOLD)
# =========================

def generate_gold_answer(
    question: str,
    base: str,
    block: Dict[str, Any],
    family: str,
    lang: str,
    mode: str,
) -> str:
    if mode != "gold" or not USE_OPENAI or openai_client is None:
        return base

    if lang == "en":
        target_lang = "inglese"
    elif lang == "fr":
        target_lang = "francese"
    elif lang == "es":
        target_lang = "spagnolo"
    elif lang == "de":
        target_lang = "tedesco"
    else:
        target_lang = "italiano"
        lang = "it"

    try:
        resp = openai_client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            temperature=0.35,
            max_tokens=2000,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Sei Sinapsi, assistente tecnico-commerciale di Tecnaria. "
                        f"Rispondi SEMPRE in {target_lang}. "
                        "Stile GOLD: completo, tecnico, chiaro, narrativo ma professionale. "
                        "Non cambiare famiglie o campi di impiego. "
                        "Rispetta l'uso esclusivo della P560 dove previsto."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"DOMANDA: {question}\n"
                        f"FAMIGLIA: {family}\n"
                        "BLOCCO JSON UFFICIALE:\n"
                        f"{json.dumps(block, ensure_ascii=False)}\n\n"
                        "TESTO BASE SELEZIONATO:\n"
                        f"{base}\n\n"
                        "Riscrivi in stile GOLD nella lingua richiesta, "
                        "senza accorciare in modo drastico e senza alterare i dati tecnici."
                    ),
                },
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        if len(text) < len(base) * 0.7:
            return base
        return text
    except Exception as e:
        print(f"[generate_gold_answer] Errore NLM GOLD: {e}")
        return base

# =========================
# SELEZIONE BLOCCO
# =========================

def find_best_block(
    query: str, families: Optional[List[str]], lang: str, mode: str
) -> Optional[Dict[str, Any]]:
    explicit_fams = detect_explicit_families(query)
    forced_fams = [f.upper() for f in families] if families else None

    if explicit_fams:
        if forced_fams:
            fams = [f for f in forced_fams if f in explicit_fams] or explicit_fams
        else:
            fams = explicit_fams
    else:
        fams = forced_fams or list_all_families()

    best_block: Optional[Dict[str, Any]] = None
    best_family: Optional[str] = None
    best_score: float = 0.0

    for fam in fams:
        try:
            blocks = load_family(fam)
        except HTTPException:
            continue

        for b in blocks:
            ans = extract_answer(b, lang, mode)
            if not ans:
                continue

            s = score_block_routed(query, b, fam, explicit_fams)
            if s > best_score:
                best_score = s
                best_block = b
                best_family = fam

    min_score = 0.05 if explicit_fams else 0.25
    if not best_block or best_score < min_score:
        return None

    bb = dict(best_block)
    bb["_family"] = best_family
    bb["_score"] = best_score
    return bb

# =========================
# ENDPOINTS
# =========================

@app.get("/api/config")
def api_config():
    return JSONResponse({
        "app": "Tecnaria Sinapsi — Q/A",
        "status": "OK",
        "families_dir": str(DATA_DIR),
        "families": list_all_families(),
        "nlm": bool(openai_client is not None and USE_OPENAI),
        "current_mode": CURRENT_MODE,
        "note": (
            "Modalità persistente: GOLD o CANONICO.\n"
            "Scrivi 'GOLD:' per passare definitivamente a GOLD.\n"
            "Scrivi 'CANONICO:' per passare definitivamente a CANONICO.\n"
            "Lingua rilevata automaticamente per ogni domanda."
        ),
    })


@app.post("/api/ask")
async def api_ask_post(request: Request):
    raw = await request.body()
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Body JSON non valido. Atteso: {\"q\":..., \"family\":..., \"mode\":...}",
        )

    raw_q = str(data.get("q", "")).strip()
    if not raw_q:
        raise HTTPException(status_code=400, detail="Campo 'q' mancante o vuoto.")

    provided_mode = str(data.get("mode", "")).strip().lower() if data.get("mode") else None
    q, mode = update_mode_and_clean_query(raw_q, provided_mode)

    family = str(data.get("family", "")).strip().upper() if data.get("family") else None
    lang = detect_lang(q)
    fams = [family] if family else None

    best = find_best_block(q, fams, lang, mode)

    if not best:
        return JSONResponse({
            "ok": False,
            "q": q,
            "lang": lang,
            "mode": mode,
            "family": family,
            "text": "Nessuna risposta disponibile per questa domanda con la modalità attuale.",
        })

    base = extract_answer(best, lang, mode)
    if not base:
        return JSONResponse({
            "ok": False,
            "q": q,
            "lang": lang,
            "mode": mode,
            "family": best.get("_family", family),
            "id": best.get("id"),
            "text": "Blocco trovato ma senza contenuto utilizzabile nella modalità attuale.",
        })

    text = generate_gold_answer(
        q, base, best, best.get("_family", family) or "", lang, mode
    )

    text = re.sub(r"\bperni?\b", "chiodi idonei Tecnaria", text, flags=re.IGNORECASE)

    return JSONResponse({
        "ok": True,
        "q": q,
        "lang": lang,
        "mode": mode,
        "family": best.get("_family", family),
        "id": best.get("id"),
        "score": best.get("_score", 0.0),
        "text": text,
    })


@app.get("/api/ask")
def api_ask_get(
    q: str = Query(..., description="Domanda"),
    family: Optional[str] = Query(None),
    mode: Optional[str] = Query(None, description="(opzionale) gold/canonical per cambio persistente"),
):
    raw_q = q.strip()
    provided_mode = (mode or "").strip().lower() or None
    q_clean, mode_final = update_mode_and_clean_query(raw_q, provided_mode)

    lang = detect_lang(q_clean)
    fams = [family.upper()] if family else None

    best = find_best_block(q_clean, fams, lang, mode_final)

    if not best:
        return JSONResponse({
            "ok": False,
            "q": q_clean,
            "lang": lang,
            "mode": mode_final,
            "family": family,
            "text": "Nessuna risposta disponibile per questa domanda con la modalità attuale.",
        })

    base = extract_answer(best, lang, mode_final)
    if not base:
        return JSONResponse({
            "ok": False,
            "q": q_clean,
            "lang": lang,
            "mode": mode_final,
            "family": best.get("_family", family),
            "id": best.get("id"),
            "text": "Blocco trovato ma senza contenuto utilizzabile nella modalità attuale.",
        })

    text = generate_gold_answer(
        q_clean, base, best, best.get("_family", family) or "", lang, mode_final
    )

    text = re.sub(r"\bperni?\b", "chiodi idonei Tecnaria", text, flags=re.IGNORECASE)

    return JSONResponse({
        "ok": True,
        "q": q_clean,
        "lang": lang,
        "mode": mode_final,
        "family": best.get("_family", family),
        "id": best.get("id"),
        "score": best.get("_score", 0.0),
        "text": text,
    })


@app.get("/", response_class=HTMLResponse)
def root():
    if INDEX_HTML.exists():
        return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Tecnaria Sinapsi — Q/A</h1>", status_code=200)
