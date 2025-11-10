import os
import json
import re
import unicodedata
import random
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# =========================================================
# OpenAI client (solo per traduzioni se disponibile)
# =========================================================

try:
    from openai import OpenAI  # type: ignore
except Exception:
    OpenAI = None  # type: ignore

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
USE_OPENAI = bool(OPENAI_API_KEY) and OpenAI is not None
openai_client = OpenAI() if USE_OPENAI else None

# =========================================================
# Percorsi
# =========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DATA_DIR = os.path.join(STATIC_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.runtime.json")

# =========================================================
# FastAPI app
# =========================================================

app = FastAPI(title="TECNARIA Sinapsi Backend", version="3.3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# =========================================================
# Stato globale
# =========================================================

_family_cache: Dict[str, Dict[str, Any]] = {}
_config_cache: Optional[Dict[str, Any]] = None

MODE_GOLD = "gold"
MODE_CANONICAL = "canonical"
_current_mode: str = MODE_GOLD  # default: GOLD finché non lo cambi

# =========================================================
# Modelli
# =========================================================

class AskRequest(BaseModel):
    q: str
    family: Optional[str] = None

# =========================================================
# Utility
# =========================================================

def normalize(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def safe_family_filename(family: str) -> str:
    return f"{family.upper()}.json"


def load_config() -> Dict[str, Any]:
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    cfg: Dict[str, Any] = {
        "admin": {
            "response_policy": {
                "mode": "dynamic",
                "variant_selection": "longest",
                "variant_seed": 20251110,
            }
        }
    }
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            file_cfg = json.load(f)
        if isinstance(file_cfg, dict):
            admin = file_cfg.get("admin") or {}
            rp = admin.get("response_policy") or {}
            cfg["admin"]["response_policy"].update(rp)
    except FileNotFoundError:
        pass
    except Exception:
        pass

    _config_cache = cfg
    return cfg


def get_response_policy() -> Dict[str, Any]:
    cfg = load_config()
    return (cfg.get("admin") or {}).get("response_policy") or {}


def list_families() -> List[str]:
    if not os.path.isdir(DATA_DIR):
        return []
    fams: List[str] = []
    for fname in os.listdir(DATA_DIR):
        if not fname.lower().endswith(".json"):
            continue
        base = fname.rsplit(".", 1)[0]
        if base.lower().startswith("config.runtime"):
            continue
        fams.append(base.upper())
    return sorted(set(fams))


def load_family(family: str) -> Dict[str, Any]:
    family = family.upper()
    if family in _family_cache:
        return _family_cache[family]

    path = os.path.join(DATA_DIR, safe_family_filename(family))
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Family '{family}' not found")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error loading family '{family}': {e}"
        )

    if isinstance(data, list):
        items = data
        data = {"family": family, "items": items}
    else:
        items = data.get("items") or []
        data["items"] = items

    for idx, item in enumerate(items):
        if "id" not in item:
            item["id"] = f"{family}-{idx+1:04d}"

    _family_cache[family] = data
    return data

# =========================================================
# Lingua
# =========================================================

SUPPORTED_LANGS = ["it", "en", "fr", "de", "es"]

def detect_lang(text: str) -> str:
    t = (text or "").strip().lower()
    if not t:
        return "it"

    if re.search(r"\b(what|which|can|how|where|why)\b", t):
        return "en"
    if re.search(r"\b(qué|dónde|como|cómo|cuándo|por qué)\b", t):
        return "es"
    if re.search(r"\b(quel|quelle|quels|quelles|comment|pourquoi|où)\b", t):
        return "fr"
    if re.search(r"\b(was|wie|warum|wo|welche|welcher|welches)\b", t):
        return "de"
    if any(ch in t for ch in "àèéìòù"):
        return "it"
    return "it"


def openai_translate(text: str, target_lang: str, source_lang: Optional[str] = None) -> str:
    if not text or not USE_OPENAI or not openai_client:
        return text

    target_lang = target_lang.lower()
    if target_lang not in SUPPORTED_LANGS:
        return text

    try:
        completion = openai_client.chat.completions.create(
            model=os.getenv("OPENAI_TRANSLATION_MODEL", "gpt-4.1-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Sei un traduttore tecnico per prodotti strutturali Tecnaria. "
                        f"Traduci il testo nella lingua '{target_lang}'. "
                        "Non tradurre nomi prodotti o sigle. Rispondi solo con il testo tradotto."
                    ),
                },
                {"role": "user", "content": text},
            ],
            max_tokens=800,
        )
        out = (completion.choices[0].message.content or "").strip()
        return out or text
    except Exception:
        return text

# =========================================================
# GOLD / CANONICO selezione testo
# =========================================================

def iter_variants(variants: Any) -> List[str]:
    out: List[str] = []
    if isinstance(variants, str):
        s = variants.strip()
        if s:
            out.append(s)
    elif isinstance(variants, list):
        for v in variants:
            if isinstance(v, str):
                s = v.strip()
                if s:
                    out.append(s)
    elif isinstance(variants, dict):
        for v in variants.values():
            if isinstance(v, str):
                s = v.strip()
                if s:
                    out.append(s)
            elif isinstance(v, list):
                for s2 in v:
                    if isinstance(s2, str):
                        s2 = s2.strip()
                        if s2:
                            out.append(s2)
    return out


def pick_gold_text(block: Dict[str, Any]) -> str:
    """
    GOLD: scegli il contenuto più ricco disponibile (longest).
    """
    policy = get_response_policy()
    sel = (policy.get("variant_selection") or "longest").lower()
    seed = policy.get("variant_seed")

    variants = iter_variants(block.get("response_variants"))
    canonical = (block.get("canonical") or block.get("answer_it") or "").strip()

    candidates: List[str] = []
    candidates.extend(variants)
    if canonical:
        candidates.append(canonical)

    if not candidates:
        return ""

    if sel == "random":
        rnd = random.Random(seed or None)
        return rnd.choice(candidates)

    # default GOLD: usa la più lunga
    return max(candidates, key=len)


def pick_canonical_text(block: Dict[str, Any]) -> str:
    """
    CANONICO: risposta sintetica (canonical o la più corta).
    """
    canonical = (block.get("canonical") or block.get("answer_it") or "").strip()
    if canonical:
        return canonical

    variants = iter_variants(block.get("response_variants"))
    if not variants:
        return ""
    return min(variants, key=len)

# =========================================================
# Matching domanda → item
# =========================================================

STOPWORDS = {
    "dove","posso","puo","puoi","si","no","come","quando","quanto","quanti",
    "quale","quali","il","lo","la","i","gli","le","un","una","uno",
    "di","del","della","dei","degli","da","in","su","per","con","e","ed","o",
    "oppure","any","can","do","use","utilizzare","usare",
    "please","cliente","vorrei","voglio","devo","si possono","è possibile"
}

FAMILY_SYNONYMS: Dict[str, List[str]] = {
    "CTF": ["ctf"],
    "CTL": ["ctl"],
    "CTL_MAXI": ["ctl maxi", "maxi"],
    "VCEM": ["vcem"],
    "CTCEM": ["ctcem"],
    "P560": ["p560", "spit p560"],
    "DIAPASON": ["diapason"],
}

def guess_families_from_text(text: str) -> List[str]:
    t = text.lower()
    found: List[str] = []
    for fam, keys in FAMILY_SYNONYMS.items():
        for k in keys:
            if k in t:
                found.append(fam)
                break
    return found


def collect_item_text(item: Dict[str, Any]) -> Tuple[str, str]:
    q_parts: List[str] = []
    all_parts: List[str] = []

    for key in ("questions", "q", "question", "paraphrases"):
        v = item.get(key)
        if isinstance(v, str):
            q_parts.append(v)
        elif isinstance(v, list):
            q_parts.extend([s for s in v if isinstance(s, str)])

    for key in ("questions", "q", "question", "paraphrases",
                "tags", "canonical", "answer_it"):
        v = item.get(key)
        if isinstance(v, str):
            all_parts.append(v)
        elif isinstance(v, list):
            all_parts.extend([s for s in v if isinstance(s, str)])

    return " ".join(all_parts), " ".join(q_parts)


def score_item(q_norm: str, item: Dict[str, Any]) -> float:
    if not q_norm:
        return 0.0

    q_terms = [t for t in q_norm.split() if t and t not in STOPWORDS]
    if not q_terms:
        return 0.0
    q_set = set(q_terms)

    full_text, q_text = collect_item_text(item)
    full_norm = normalize(full_text)
    if not full_norm:
        return 0.0
    full_set = set(full_norm.split())

    overlap = q_set & full_set
    if not overlap:
        return 0.0

    base = len(overlap) / len(q_set)

    if q_text:
        q_norm2 = normalize(q_text)
        q_set2 = set(q_norm2.split())
        if q_set2:
            overlap_q = q_set & q_set2
            if overlap_q:
                base += 0.25 * (len(overlap_q) / len(q_set))

    return base


def find_best_block(query_it: str, families: Optional[List[str]] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str], float]:
    q_norm = normalize(query_it)
    if not q_norm:
        return (None, None, 0.0)

    if not families:
        families = list_families()

    best_item = None
    best_family = None
    best_score = 0.0

    for fam in families:
        try:
            data = load_family(fam)
        except HTTPException:
            continue
        items = data.get("items") or []
        for item in items:
            s = score_item(q_norm, item)
            if s > best_score:
                best_score = s
                best_item = item
                best_family = fam

    if best_score < 0.15:
        return (None, None, 0.0)

    return best_item, best_family, best_score

# =========================================================
# Guardrail tecnico P560 / famiglie
# =========================================================

def enforce_guardrails(question: str, family: str, text: str, mode: str) -> str:
    """
    Applica vincoli tecnici fondamentali (P560, uso corretto famiglie).
    """
    q = (question or "").lower()
    fam = (family or "").upper()
    out = text.strip()

    # 1) CTF: GOLD deve SEMPRE ricordare che il sistema certificato è con P560
    if fam == "CTF":
        note = (
            " I connettori CTF Tecnaria sono parte di un sistema certificato: "
            "il fissaggio è previsto esclusivamente con chiodatrice a polvere P560 Tecnaria "
            "dotata di accessori e chiodi idonei. L'uso di utensili diversi non è coperto "
            "dal perimetro tecnico e prestazionale Tecnaria."
        )
        if "p560" not in out.lower():
            if mode == MODE_GOLD:
                out = out + "\n\n" + note
            else:
                # anche in CANONICO, breve ma chiaro
                out = out + " (Fissaggio certificato solo con P560 Tecnaria.)"

    # 2) Domande che mischiano P560 con famiglie non compatibili
    if "p560" in q and any(k in q for k in ["vcem", "ctl", "ctl maxi", "ctcem", "diapason"]):
        extra = (
            "\n\nNota: la P560 è dedicata al sistema CTF (pioli a sparo su acciaio/lamiera). "
            "VCEM, CTL, CTL MAXI, CTCEM e DIAPASON utilizzano fissaggi meccanici specifici "
            "e non devono essere posati con P560."
        )
        if extra.strip().lower() not in out.lower():
            out = out + extra

    return out

# =========================================================
# ROUTES
# =========================================================

@app.get("/", include_in_schema=False)
async def root_page():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {
        "ok": True,
        "message": "TECNARIA Sinapsi backend attivo",
        "mode": _current_mode,
        "families": list_families(),
    }


@app.get("/api/config")
async def api_config():
    rp = get_response_policy()
    return {
        "ok": True,
        "current_mode": _current_mode,
        "admin_response_policy": rp,
        "families": list_families(),
        "translation_enabled": bool(USE_OPENAI and openai_client),
    }


@app.post("/api/ask")
async def api_ask(payload: AskRequest):
    global _current_mode

    raw_q = (payload.q or "").strip()
    if not raw_q:
        raise HTTPException(status_code=400, detail="Missing 'q' in request body")

    txt = raw_q.lstrip()
    low = txt.lower()

    # comandi persistenti GOLD / CANONICO
    if low.startswith("gold:"):
        _current_mode = MODE_GOLD
        txt = txt[5:].strip()
    elif low.startswith("canonico:") or low.startswith("canonical:"):
        _current_mode = MODE_CANONICAL
        idx = low.find(":")
        txt = txt[idx + 1 :].strip()

    if not txt:
        return {
            "ok": True,
            "message": f"Modalità aggiornata a '{_current_mode}'. Inserisci la domanda successiva.",
            "mode": _current_mode,
        }

    mode = _current_mode

    # lingua utente
    user_lang = detect_lang(txt)

    # per il match usiamo italiano (traduzione interna se serve)
    query_for_match = txt
    if user_lang != "it":
        query_for_match = openai_translate(txt, "it", source_lang=user_lang)

    # famiglie candidate (hint o auto)
    if payload.family:
        families = [payload.family.upper()]
    else:
        guessed = guess_families_from_text(txt)
        families = guessed or None

    item, fam, score = find_best_block(query_for_match, families)

    if not item or not fam:
        return {
            "ok": False,
            "message": "Blocco trovato ma senza risposta valida.",
            "lang": user_lang,
            "mode": mode,
        }

    # GOLD / CANONICO
    if mode == MODE_GOLD:
        base_it = pick_gold_text(item)
    else:
        base_it = pick_canonical_text(item)

    if not base_it:
        return {
            "ok": False,
            "message": "Blocco trovato ma senza risposta valida.",
            "family": fam,
            "id": item.get("id"),
            "lang": user_lang,
            "mode": mode,
        }

    # guardrail tecnici (P560, ecc.)
    base_it = enforce_guardrails(query_for_match, fam, base_it, mode)

    # traduzione se necessario
    if user_lang != "it":
        final_text = openai_translate(base_it, user_lang, source_lang="it")
    else:
        final_text = base_it

    return {
        "ok": True,
        "family": fam,
        "id": item.get("id"),
        "score": round(score, 4),
        "lang": user_lang,
        "mode": mode,
        "text": final_text,
    }
