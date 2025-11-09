import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import HTMLResponse
import re

# =======================================
# CONFIGURAZIONE NLM / OPENAI (GOLD)
# =======================================

# Sempre TRUE lato logica: se la chiave c'è, usiamo il modello.
USE_OPENAI = True

try:
    from openai import OpenAI
    if USE_OPENAI and os.getenv("OPENAI_API_KEY"):
        openai_client = OpenAI()
    else:
        openai_client = None
except Exception:
    openai_client = None

# =======================================
# PATH BASE
# =======================================

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = STATIC_DIR / "data"
INDEX_HTML = STATIC_DIR / "index.html"

app = FastAPI(title="Tecnaria Sinapsi — Q/A")

# Cache famiglie
_family_cache: Dict[str, List[Dict[str, Any]]] = {}

# Mappa parole chiave → famiglie
FAMILY_KEYWORDS = {
    "ctf": "CTF",
    "ctl maxi": "CTL_MAXI",
    "ctl_maxi": "CTL_MAXI",
    "ctl": "CTL",
    "vcem": "VCEM",
    "ctcem": "CTCEM",
    "p560": "P560",
    "diapason": "DIAPASON",
    "tecnaria": "TECNARIA_GOLD",
}

# =======================================
# UTILS LETTURA / JSON
# =======================================

def safe_read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def extract_blocks(data: Any) -> List[Dict[str, Any]]:
    """
    Estrae blocchi informativi dai JSON:
    - { "items": [...] }, { "blocks": [...] }, { "data": [...] }
    - oppure lista diretta.
    """
    blocks: List[Dict[str, Any]] = []

    if isinstance(data, list):
        blocks = [b for b in data if isinstance(b, dict)]
    elif isinstance(data, dict):
        for key in ("items", "blocks", "data"):
            v = data.get(key)
            if isinstance(v, list):
                blocks = [b for b in v if isinstance(b, dict)]
                break

    for i, b in enumerate(blocks):
        if "id" not in b:
            b["id"] = f"AUTO-{i:04d}"

    return blocks

def load_family(family: str) -> List[Dict[str, Any]]:
    """
    Carica il dataset di una famiglia.
    Priorità:
      FAM.json, FAM.gold.json, FAM.golden.json.
    Se non trovati, primo FAM*.json (esclude CONFIG.RUNTIME).
    """
    fam = family.upper()
    if fam in _family_cache:
        return _family_cache[fam]

    # file standard
    candidates = [
        DATA_DIR / f"{fam}.json",
        DATA_DIR / f"{fam}.gold.json",
        DATA_DIR / f"{fam}.golden.json",
    ]
    path: Optional[Path] = next((p for p in candidates if p.exists()), None)

    # fallback: qualsiasi FAM*.json
    if path is None and DATA_DIR.exists():
        for f in DATA_DIR.glob(f"{fam}*.json"):
            name_up = f.name.upper()
            if "CONFIG.RUNTIME" in name_up:
                continue
            path = f
            break

    if path is None:
        raise HTTPException(status_code=404, detail=f"File JSON per famiglia '{family}' non trovato.")

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
        if "CONFIG.RUNTIME" in name:
            continue
        if name.endswith(".GOLD"):
            name = name[:-5]
        fams.append(name)

    return sorted(set(fams))

# =======================================
# MATCHING / LINGUA
# =======================================

def norm(s: str) -> str:
    return " ".join(s.lower().strip().split())

def extract_queries(block: Dict[str, Any]) -> List[str]:
    """
    Testi usati per il matching.
    """
    out: List[str] = []

    # Campi singoli
    for key in ("q", "question", "domanda", "title", "label"):
        v = block.get(key)
        if isinstance(v, str):
            v = v.strip()
            if v:
                out.append(v)

    # Liste
    for key in ("questions", "paraphrases", "variants", "triggers"):
        v = block.get(key)
        if isinstance(v, list):
            for e in v:
                if isinstance(e, str):
                    e = e.strip()
                    if e:
                        out.append(e)

    # Tag
    tags = block.get("tags")
    if isinstance(tags, list):
        for t in tags:
            if isinstance(t, str):
                t = t.strip()
                if t:
                    out.append(t)

    # Canonical (accorciato)
    canon = block.get("canonical")
    if isinstance(canon, str):
        c = canon.strip()
        if c:
            out.append(c[:220])

    return out

def base_similarity(query: str, block: Dict[str, Any]) -> float:
    """
    Similarità testuale semplice (token overlap).
    """
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

        j = inter / len(sq | sc)
        if j > best:
            best = j

    return float(best)

def detect_lang(query: str) -> str:
    """
    Riconoscimento lingua minimale:
    IT / EN / FR / ES / DE.
    Serve a dire al modello in che lingua rispondere.
    """
    q = query.lower()

    # Inglese
    if "nail gun" in q or "shear connector" in q or "composite beam" in q:
        return "en"
    if re.search(r"\b(what|which|where|when|why|how|can|could|should|would|maintenance|safety)\b", q):
        if not any(t in q for t in [" calcestruzzo", " soletta", " lamiera", " trav", " laterocemento"]):
            return "en"

    # Francese
    if any(x in q for x in ["plancher", "béton", "connecteur", "acier", "chantier"]):
        return "fr"

    # Spagnolo
    if any(x in q for x in ["forjado", "hormigón", "conector", "viga de madera", "obra"]):
        return "es"

    # Tedesco
    if any(x in q for x in ["verbinder", "beton", "stahlträger", "holzdecken", "baustelle"]):
        return "de"

    # Italiano
    if any(x in q for x in [
        "soletta", "calcestruzzo", "trave", "travetto", "lamiera",
        "pistola", "cartucce", "connettore", "cantiere", "laterocemento"
    ]):
        return "it"

    # Default contesto Tecnaria
    return "it"

def detect_explicit_families(query: str) -> List[str]:
    """
    Se l'utente cita esplicitamente famiglie, blocchiamo la ricerca su quelle.
    """
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

def score_block_routed(query: str,
                       block: Dict[str, Any],
                       fam: str,
                       explicit_fams: List[str]) -> float:
    """
    Similarità + routing per famiglia (P560, CTF, VCEM, CTL, ecc).
    """
    base = base_similarity(query, block)
    if base <= 0:
        return 0.0

    fam_u = fam.upper()
    q_low = query.lower()

    # Lock esplicito
    if explicit_fams:
        if fam_u in explicit_fams:
            base *= 8.0
        else:
            base *= 0.05
        return base

    # Heuristica P560
    if any(k in q_low for k in ["p560", "pistola", "chiodatrice", "sparo", "cartuccia", "cartucce", "nail gun"]):
        if fam_u == "P560":
            base *= 5.0
        else:
            base *= 0.4

    # Heuristica CTL/CTL_MAXI → legno
    if any(k in q_low for k in ["legno", "trave in legno", "travi in legno", "timber", "wood beam"]):
        if fam_u in ["CTL", "CTL_MAXI"]:
            base *= 3.0
        elif fam_u in ["CTF", "VCEM", "CTCEM", "P560", "DIAPASON"]:
            base *= 0.4

    # Heuristica VCEM/CTCEM/DIAPASON → laterocemento
    if any(k in q_low for k in ["laterocemento", "travetto", "travetti", "hollow block slab"]):
        if fam_u in ["VCEM", "CTCEM", "DIAPASON"]:
            base *= 3.0
        elif fam_u in ["CTF", "CTL", "CTL_MAXI", "P560"]:
            base *= 0.4

    # Domanda molto generica "tutte le notizie sulla P560" → preferisci definizione
    if "p560" in q_low and "tutte" in q_low:
        tags = block.get("tags") or []
        if any(isinstance(t, str) and "definizione" in t for t in tags):
            base *= 2.5

    return base

# =======================================
# COSTRUZIONE BASE GOLD (da JSON)
# =======================================

def extract_answer(block: Dict[str, Any], lang: str = "it") -> Optional[str]:
    """
    Costruisce una base GOLD combinando:
    - answers[lang]
    - answer_it
    - canonical
    - response_variants (più significative)
    Mai singola riga secca se possiamo evitarlo.
    """
    pieces: List[str] = []

    # answers multilingua
    answers = block.get("answers")
    if isinstance(answers, dict):
        for key in (lang, lang.lower(), lang.upper()):
            v = answers.get(key)
            if isinstance(v, str) and v.strip():
                pieces.append(v.strip())
                break
        if not pieces:
            for v in answers.values():
                if isinstance(v, str) and v.strip():
                    pieces.append(v.strip())
                    break

    # answer_it
    answer_it = block.get("answer_it")
    if isinstance(answer_it, str) and answer_it.strip():
        if all(answer_it.strip() not in p for p in pieces):
            pieces.append(answer_it.strip())

    # canonical
    canonical = block.get("canonical")
    if isinstance(canonical, str) and canonical.strip():
        if all(canonical.strip() not in p for p in pieces):
            pieces.append(canonical.strip())

    # response_variants
    variants_raw = block.get("response_variants")
    variants: List[str] = []

    if isinstance(variants_raw, list):
        variants = [v.strip() for v in variants_raw if isinstance(v, str) and v.strip()]
    elif isinstance(variants_raw, dict):
        for v in variants_raw.values():
            if isinstance(v, list):
                for e in v:
                    if isinstance(e, str) and e.strip():
                        variants.append(e.strip())
            elif isinstance(v, str) and v.strip():
                variants.append(v.strip())

    if variants:
        variants_sorted = sorted(variants, key=len, reverse=True)
        for v in variants_sorted:
            if not any(v in p or p in v for p in pieces):
                pieces.append(v)
                if len(" ".join(pieces)) > 400:
                    break

    # fallback legacy
    if not pieces:
        for key in ("answer", "risposta", "text", "content"):
            v = block.get(key)
            if isinstance(v, str) and v.strip():
                pieces.append(v.strip())
                break

    if not pieces:
        return None

    return " ".join(pieces).strip()

# =======================================
# GOLD GENERATION (MAI SOLO CANONICO)
# =======================================

def generate_gold_answer(question: str,
                         base: str,
                         block: Dict[str, Any],
                         family: str,
                         lang: str) -> str:
    """
    BLINDATA:
    - Se OpenAI disponibile → GOLD dinamica vera (lingua = lang).
    - Se OpenAI non disponibile / errore → fallback interno ricco,
      mai una riga secca.
    """

    def build_fallback_gold() -> str:
        parts: List[str] = []

        fam = (family or block.get("_family") or "").upper()
        q_low = question.lower()

        # Apertura mirata per alcune famiglie chiave
        if fam == "P560":
            parts.append(
                "La P560 è la chiodatrice Tecnaria a sparo controllato dedicata "
                "al fissaggio dei connettori CTF su travi in acciaio o lamiera grecata. "
                "È uno strumento professionale che richiede uso corretto, manutenzione regolare e rispetto rigoroso delle norme di sicurezza."
            )
        elif fam == "CTF":
            parts.append(
                "I connettori CTF Tecnaria sono pioli a taglio per strutture miste acciaio–calcestruzzo. "
                "Realizzano il collegamento meccanico tra trave in acciaio e soletta in calcestruzzo, "
                "impedendo lo scorrimento relativo e aumentando rigidezza e capacità portante del solaio."
            )
        elif fam in ("VCEM", "CTCEM", "CTL", "CTL_MAXI", "DIAPASON"):
            canon = block.get("canonical") or base
            if canon:
                parts.append(canon.strip())
        else:
            if base:
                parts.append(base.strip())

        # Varianti extra dal blocco
        variants = []
        rv = block.get("response_variants")
        if isinstance(rv, list):
            variants = [v.strip() for v in rv if isinstance(v, str) and v.strip()]
        elif isinstance(rv, dict):
            for vv in rv.values():
                if isinstance(vv, list):
                    for e in vv:
                        if isinstance(e, str) and e.strip():
                            variants.append(e.strip())
                elif isinstance(vv, str) and vv.strip():
                    variants.append(vv.strip())

        extra = []
        for v in sorted(variants, key=len, reverse=True):
            if len(extra) >= 3:
                break
            if not any(v in p or p in v for p in parts):
                extra.append(v)

        if extra:
            parts.append(" ".join(extra))

        # Wrap-up finale tecnico
        if len(" ".join(parts)) < 400:
            parts.append(
                "In pratica, fai sempre riferimento alla documentazione Tecnaria, usa solo connettori e accessori "
                "della famiglia corretta per il tipo di solaio, rispetta i campi di impiego certificati "
                "e, in caso di dubbio, confrontati con il progettista strutturale o con il servizio tecnico Tecnaria."
            )

        text = " ".join(parts).strip()
        # Ultima difesa: se proprio non è riuscito a costruire nulla, torna base (ma qui è già ricco)
        return text or (base or "").strip()

    # Se OpenAI disponibile → GOLD dinamica
    if USE_OPENAI and openai_client is not None:
        try:
            resp = openai_client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
                temperature=0.35,
                max_tokens=1500,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Sei Sinapsi, assistente tecnico-commerciale di Tecnaria. "
                            "Rispondi SEMPRE nella stessa lingua indicata come LINGUA. "
                            "Stile GOLD dinamico: completo, tecnico, chiaro, con esempi di cantiere reali. "
                            "Rispetta rigorosamente il campo di impiego della famiglia indicata "
                            "e i contenuti del blocco dati fornito. "
                            "Non inventare prodotti o usi non previsti. "
                            "Non limitarti a ripetere il canonical: integra, collega le varianti e organizza in modo naturale."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"LINGUA: {lang}\n"
                            f"FAMIGLIA: {family}\n"
                            f"DOMANDA: {question}\n\n"
                            f"BLOCCO DATI (JSON): {json.dumps(block, ensure_ascii=False)}\n\n"
                            f"TESTO DI BASE (da rifinire in stile GOLD, senza stravolgere): {base}"
                        ),
                    },
                ],
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                return text
        except Exception:
            # Se qualcosa va storto con OpenAI, usiamo il fallback interno
            pass

    # Fallback GOLD interno: mai canonical secca
    return build_fallback_gold()

# =======================================
# SELEZIONE MIGLIOR BLOCCO
# =======================================

def find_best_block(query: str,
                    families: Optional[List[str]] = None,
                    lang: str = "it") -> Optional[Dict[str, Any]]:
    explicit_fams = detect_explicit_families(query)
    forced_fams = [f.upper() for f in families] if families else None
    target_lang = (lang or "it").lower()

    # famiglie candidate
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
            # fattore lingua: se il blocco dichiara lang, preferisci target
            block_lang = str(b.get("lang", "")).lower().strip()
            lang_factor = 1.0
            if block_lang:
                if block_lang == target_lang:
                    lang_factor = 2.0
                elif block_lang != target_lang:
                    lang_factor = 0.25

            # deve avere contenuto utilizzabile
            ans = extract_answer(b, lang) or extract_answer(b, "it") or extract_answer(b, "en")
            if not ans:
                continue

            s = score_block_routed(query, b, fam, explicit_fams) * lang_factor

            if s > best_score:
                best_score = s
                best_block = b
                best_family = fam

    # soglie
    if explicit_fams:
        min_score = 0.05
    else:
        min_score = 0.25

    if not best_block or best_score < min_score:
        # se cercavamo lingua non-IT e non troviamo nulla forte, fallback a IT
        if target_lang != "it":
            return find_best_block(query, families, lang="it")
        return None

    bb = dict(best_block)
    bb["_family"] = best_family
    bb["_score"] = best_score
    return bb

# =======================================
# ENDPOINTS
# =======================================

@app.get("/api/config")
def api_config():
    return {
        "app": "Tecnaria Sinapsi — Q/A",
        "status": "OK",
        "families_dir": str(DATA_DIR),
        "families": list_all_families(),
        "nlm": bool(openai_client is not None and USE_OPENAI),
    }

@app.post("/api/ask")
async def api_ask(request: Request):
    raw = await request.body()
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Body JSON non valido. Atteso: {\"q\":..., \"family\":...}",
        )

    q = str(data.get("q", "")).strip()
    family = str(data.get("family", "")).strip().upper() if data.get("family") else None

    if not q:
        raise HTTPException(status_code=400, detail="Campo 'q' mancante o vuoto.")

    lang = detect_lang(q)
    fams = [family] if family else None

    best = find_best_block(q, fams, lang)

    if not best:
        return {
            "ok": False,
            "q": q,
            "lang": lang,
            "family": family,
            "text": "Nessuna risposta trovata per questa domanda con i dati disponibili.",
        }

    base = (
        extract_answer(best, lang)
        or extract_answer(best, "it")
        or extract_answer(best, "en")
        or ""
    )

    text = generate_gold_answer(
        q,
        base,
        best,
        best.get("_family", family) or "",
        lang,
    )

    return {
        "ok": True,
        "q": q,
        "lang": lang,
        "family": best.get("_family", family),
        "id": best.get("id"),
        "score": best.get("_score", 0.0),
        "text": text,
    }

@app.get("/api/ask")
def api_ask_get(
    q: str = Query(..., description="Domanda"),
    family: Optional[str] = Query(None)
):
    """
    GET per test rapido: /api/ask?q=...&family=CTF
    """
    lang = detect_lang(q)
    fams = [family.upper()] if family else None

    best = find_best_block(q, fams, lang)

    if not best:
        return {
            "ok": False,
            "q": q,
            "lang": lang,
            "family": family,
            "text": "Nessuna risposta trovata per questa domanda con i dati disponibili.",
        }

    base = (
        extract_answer(best, lang)
        or extract_answer(best, "it")
        or extract_answer(best, "en")
        or ""
    )

    text = generate_gold_answer(
        q,
        base,
        best,
        best.get("_family", family) or "",
        lang,
    )

    return {
        "ok": True,
        "q": q,
        "lang": lang,
        "family": best.get("_family", family),
        "id": best.get("id"),
        "score": best.get("_score", 0.0),
        "text": text,
    }

@app.get("/", response_class=HTMLResponse)
def root():
    if INDEX_HTML.exists():
        return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Tecnaria Sinapsi — Q/A</h1>", status_code=200)
