import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import HTMLResponse

# =======================================
# CONFIG OPENAI (per GOLD dinamico + traduzioni)
# =======================================

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

# cache famiglie
_family_cache: Dict[str, List[Dict[str, Any]]] = {}

# mappa parole chiave -> famiglia
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
# UTILS LETTURA JSON
# =======================================

def safe_read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def extract_blocks(data: Any) -> List[Dict[str, Any]]:
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
        # fallback: primo file che inizia così
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
# NORMALIZZAZIONE / MATCHING BASE
# =======================================

def norm(s: str) -> str:
    return " ".join(s.lower().strip().split())

def extract_queries(block: Dict[str, Any]) -> List[str]:
    out: List[str] = []

    # campi singoli
    for key in ("q", "question", "domanda", "title", "label"):
        v = block.get(key)
        if isinstance(v, str):
            v = v.strip()
            if v:
                out.append(v)

    # liste di varianti
    for key in ("questions", "paraphrases", "variants", "triggers"):
        v = block.get(key)
        if isinstance(v, list):
            for e in v:
                if isinstance(e, str):
                    e = e.strip()
                    if e:
                        out.append(e)

    # tags
    tags = block.get("tags")
    if isinstance(tags, list):
        for t in tags:
            if isinstance(t, str):
                t = t.strip()
                if t:
                    out.append(t)

    # canonical
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

        j = inter / len(sq | sc)
        if j > best:
            best = j

    return float(best)

# =======================================
# LINGUA + FAMIGLIE
# =======================================

def detect_lang(query: str) -> str:
    q = query.lower()

    # english
    if "nail gun" in q or "shear connector" in q or "composite beam" in q:
        return "en"
    if re.search(r"\b(what|which|where|when|why|how|can|could|should|would|safety|maintenance)\b", q):
        if not any(t in q for t in [" soletta", " calcestruzzo", " laterocemento", " travi "]):
            return "en"

    # french
    if any(x in q for x in ["plancher", "béton", "connecteur", "chantier"]):
        return "fr"

    # spanish
    if any(x in q for x in ["forjado", "hormigón", "conector", "obra"]):
        return "es"

    # german
    if any(x in q for x in ["verbunddecke", "holz-beton", "baustelle", "verbinder"]):
        return "de"

    # italian (default tecnico)
    if any(x in q for x in [
        "soletta", "calcestruzzo", "trave", "travetto",
        "lamiera", "laterocemento", "chiodatrice", "connettore"
    ]):
        return "it"

    return "it"

def detect_explicit_families(query: str) -> List[str]:
    q = query.lower()
    hits: List[str] = []

    # ctl maxi esplicito
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
    base = base_similarity(query, block)
    if base <= 0:
        return 0.0

    fam_u = fam.upper()
    q_low = query.lower()

    # se famiglia esplicita: fortissima priorità
    if explicit_fams:
        if fam_u in explicit_fams:
            base *= 8.0
        else:
            base *= 0.05
        return base

    # regole soft per parole chiave
    if any(k in q_low for k in ["p560", "chiodatrice", "nail gun", "cartuccia", "cartucce"]):
        base *= 5.0 if fam_u == "P560" else 0.3

    if any(k in q_low for k in ["legno", "trav", "holz", "timber", "wood"]):
        if fam_u in ["CTL", "CTL_MAXI", "CTCEM"]:
            base *= 3.0
        elif fam_u in ["CTF", "VCEM", "P560"]:
            base *= 0.4

    if any(k in q_low for k in ["laterocemento", "travetto", "latero", "hollow block"]):
        if fam_u in ["VCEM", "CTCEM", "DIAPASON"]:
            base *= 3.0
        elif fam_u in ["CTF", "CTL", "CTL_MAXI", "P560"]:
            base *= 0.4

    if any(k in q_low for k in ["acciaio", "steel", "lamiera"]):
        if fam_u in ["CTF", "P560"]:
            base *= 3.0
        elif fam_u in ["VCEM", "CTL", "CTL_MAXI"]:
            base *= 0.5

    return base

# =======================================
# ESTRAZIONE RISPOSTA BASE (GOLD SOURCE)
# =======================================

def extract_answer(block: Dict[str, Any], lang: str = "it") -> Optional[str]:
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

    # risposta italiana specifica
    answer_it = block.get("answer_it")
    if isinstance(answer_it, str) and answer_it.strip():
        if all(answer_it.strip() not in p for p in pieces):
            pieces.append(answer_it.strip())

    # canonical come materiale base GOLD, non secca
    canonical = block.get("canonical")
    if isinstance(canonical, str) and canonical.strip():
        if all(canonical.strip() not in p for p in pieces):
            pieces.append(canonical.strip())

    # varianti GOLD
    rv = block.get("response_variants")
    variants: List[str] = []
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

    if variants:
        for v in sorted(variants, key=len, reverse=True):
            if len(" ".join(pieces)) > 400:
                break
            if not any(v in p or p in v for p in pieces):
                pieces.append(v)

    # fallback altri campi
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
# TRADUZIONE (solo se modello disponibile)
# =======================================

def translate_text(text: str, target_lang: str) -> str:
    if not text:
        return text

    tl = (target_lang or "it").lower()
    if tl == "it":
        return text

    if not (USE_OPENAI and openai_client is not None):
        return text

    try:
        resp = openai_client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            temperature=0.2,
            max_tokens=1500,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a precise technical translator for structural engineering Q&A. "
                        "Translate into the target language, keep Tecnaria terminology and safety constraints. "
                        "Do NOT add comments."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Target language: {tl}\n\nText:\n{text}",
                },
            ],
        )
        out = (resp.choices[0].message.content or "").strip()
        return out or text
    except Exception:
        return text

# =======================================
# GOLD GENERATION (sempre GOLD, monolingua)
# =======================================

def generate_gold_answer(question: str,
                         base: str,
                         block: Dict[str, Any],
                         family: str,
                         lang: str) -> str:
    target_lang = (lang or "it").lower()
    fam = (family or block.get("_family") or "").upper()

    # -------- 1) GOLD dinamico con modello, direttamente nella lingua giusta --------
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
                            "Rispondi SEMPRE nella lingua indicata come LINGUA. "
                            "Stile GOLD dinamico: completo, tecnico, chiaro, con esempi di cantiere. "
                            "Rispetta rigorosamente il campo di impiego della famiglia indicata "
                            "e i contenuti del blocco dati fornito. "
                            "Non inventare usi o prodotti non previsti."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"LINGUA: {target_lang}\n"
                            f"FAMIGLIA: {fam}\n"
                            f"DOMANDA: {question}\n\n"
                            f"DATI BLOCCO (JSON): {json.dumps(block, ensure_ascii=False)}\n\n"
                            f"TESTO BASE GOLD DA RIFINIRE: {base}"
                        ),
                    },
                ],
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                return text
        except Exception:
            pass

    # -------- 2) Fallback interno: costruiamo prima in IT, poi adattiamo per lingua --------

    def build_it_gold() -> str:
        parts: List[str] = []
        if base:
            parts.append(base.strip())

        rv = block.get("response_variants")
        variants: List[str] = []
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

        if variants:
            for v in sorted(variants, key=len, reverse=True):
                if len(" ".join(parts)) > 400:
                    break
                if not any(v in p or p in v for p in parts):
                    parts.append(v)

        if len(" ".join(parts)) < 260:
            parts.append(
                "Usa sempre il connettore della famiglia corretta per il tipo di solaio, "
                "rispetta schemi di posa, diametri, passi e limiti indicati da Tecnaria "
                "e confrontati con il progettista o con il servizio tecnico Tecnaria per i dettagli di calcolo."
            )

        return " ".join(parts).strip()

    it_gold = build_it_gold()

    # --- IT: usiamo direttamente la versione GOLD italiana ---
    if target_lang == "it":
        return it_gold

    # --- EN/FR/ES/DE: testi compatti per famiglie, senza mischiare lingue ---

    if target_lang == "en":
        if fam == "P560":
            return (
                "When using the P560 on site, you must always wear safety glasses, hearing protection, "
                "protective gloves and safety footwear. Treat the P560 as a controlled-shot professional tool: "
                "never aim it at people or non-working surfaces, keep it clean and maintained, use only "
                "Tecnaria-approved cartridges and CTF connectors, and follow Tecnaria's official safety instructions."
            )
        if fam == "CTL" or fam == "CTL_MAXI":
            return (
                "For timber–concrete composite slabs with timber beams you must use CTL or CTL MAXI connectors. "
                "They are screwed at the specified inclination into sound timber and embedded in the concrete topping "
                "to create full composite action. Do not use VCEM or CTF on timber; each family has its own certified field."
            )
        if fam == "CTF":
            return (
                "CTF connectors are Tecnaria shear studs for steel–concrete composite beams or slabs. "
                "They are fixed on steel beams (often through decking) and embedded in the slab to prevent slip "
                "and ensure composite behaviour according to design and ETA documentation."
            )
        if fam == "VCEM":
            return (
                "VCEM connectors are used to strengthen existing hollow-block or concrete slabs by connecting "
                "the new concrete topping to the existing slab. They are mechanically installed following Tecnaria "
                "specifications to improve stiffness, load capacity and seismic behaviour."
            )
        if fam == "CTCEM":
            return (
                "CTCEM connectors are designed for hollow-block slabs with reinforced concrete ribs. "
                "They are mechanically screwed into the rib after proper drilling and cleaning; generic resins "
                "or improvised anchors are not allowed."
            )
        if fam == "DIAPASON":
            return (
                "The DIAPASON system is adopted when a higher structural and seismic upgrade is required for existing slabs, "
                "offering an engineered, certified solution beyond simple connectors."
            )
        return (
            "Use each Tecnaria connector only in its certified field of application and follow Tecnaria's "
            "official technical documentation and installation guidelines."
        )

    if target_lang == "fr":
        if fam == "P560":
            return (
                "Avec la P560, le port de lunettes, protection auditive, gants et chaussures de sécurité est obligatoire. "
                "C'est un outil à tir contrôlé pour les connecteurs CTF, à utiliser uniquement selon les instructions Tecnaria."
            )
        return (
            "Chaque connecteur Tecnaria doit être utilisé uniquement dans son domaine d'emploi certifié, "
            "en respectant la documentation technique officielle."
        )

    if target_lang == "es":
        if fam == "P560":
            return (
                "Al utilizar la P560 en obra es obligatorio llevar gafas de seguridad, protección auditiva, guantes "
                "y calzado de seguridad. Trátala como una herramienta de disparo controlado profesional y sigue "
                "las instrucciones de Tecnaria."
            )
        return (
            "Utiliza cada conector Tecnaria solo en su campo de aplicación certificado y siguiendo las "
            "instrucciones técnicas oficiales."
        )

    if target_lang == "de":
        if fam == "P560":
            return (
                "Bei der Verwendung der P560 auf der Baustelle sind Schutzbrille, Gehörschutz, Schutzhandschuhe "
                "und Sicherheitsschuhe zwingend erforderlich. Behandeln Sie das Gerät als professionelles "
                "Bolzenschusswerkzeug und beachten Sie strikt die Tecnaria-Anweisungen."
            )
        return (
            "Verwenden Sie jeden Tecnaria-Verbinder nur in seinem zertifizierten Anwendungsbereich und beachten Sie "
            "die technischen Unterlagen und Einbauhinweise."
        )

    # fallback generico se lingua non prevista
    return (
        "Use each Tecnaria connector only in its certified field of application and follow the official "
        "Tecnaria technical documentation and installation instructions."
    )

# =======================================
# CORE: TROVA MIGLIOR BLOCCO (con fallback lingue + semantico)
# =======================================

def _find_best_block_core(query: str,
                          fams: List[str],
                          lang: str) -> (Optional[Dict[str, Any]], Optional[str], float):
    explicit_fams = detect_explicit_families(query)
    target_lang = (lang or "it").lower()

    best_block: Optional[Dict[str, Any]] = None
    best_family: Optional[str] = None
    best_score: float = 0.0

    for fam in fams:
        try:
            blocks = load_family(fam)
        except HTTPException:
            continue

        for b in blocks:
            block_lang = str(b.get("lang", "")).lower().strip()
            lang_factor = 1.0
            if block_lang:
                if block_lang == target_lang:
                    lang_factor = 2.0
                elif block_lang != target_lang and target_lang != "it":
                    # penalizza se lingue diverse (ma non azzera: abbiamo fallback dopo)
                    lang_factor = 0.4

            ans = extract_answer(b, lang) or extract_answer(b, "it") or extract_answer(b, "en")
            if not ans:
                continue

            s = score_block_routed(query, b, fam, explicit_fams) * lang_factor

            if s > best_score:
                best_score = s
                best_block = b
                best_family = fam

    return best_block, best_family, best_score

def find_best_block(query: str,
                    families: Optional[List[str]] = None,
                    lang: str = "it") -> Optional[Dict[str, Any]]:
    target_lang = (lang or "it").lower()
    explicit_fams = detect_explicit_families(query)

    if families:
        fams = [f.upper() for f in families]
    else:
        fams = list_all_families()

    # 1) primo tentativo nella lingua target
    best_block, best_family, best_score = _find_best_block_core(query, fams, target_lang)

    min_score = 0.25 if not explicit_fams else 0.05

    # se buono, restituisci
    if best_block and best_score >= min_score:
        bb = dict(best_block)
        bb["_family"] = best_family
        bb["_score"] = best_score
        return bb

    # 2) fallback cross-lingua: se non ha trovato in EN/FR/ES/DE, prova sui dati IT
    if target_lang in ("en", "fr", "es", "de"):
        best_block_it, best_family_it, best_score_it = _find_best_block_core(query, fams, "it")
        if best_block_it and best_score_it >= min_score:
            bb = dict(best_block_it)
            bb["_family"] = best_family_it
            bb["_score"] = best_score_it
            return bb

    # 3) fallback semantico: deduci famiglia da parole chiave e riprova
    q = query.lower()
    sem_family: Optional[str] = None

    if any(w in q for w in ["p560", "nail gun", "chiodatrice"]):
        sem_family = "P560"
    elif any(w in q for w in ["legno", "holz", "timber", "wood"]):
        # solai misti legno-calcestruzzo: CTL/CTL MAXI
        if "maxi" in q:
            sem_family = "CTL_MAXI"
        else:
            sem_family = "CTL"
    elif any(w in q for w in ["laterocemento", "latero", "hollow block"]):
        sem_family = "VCEM"
    elif any(w in q for w in ["acciaio", "steel", "lamiera"]):
        sem_family = "CTF"

    if sem_family:
        best_block2, best_family2, best_score2 = _find_best_block_core(query, [sem_family], "it")
        if best_block2:
            bb = dict(best_block2)
            bb["_family"] = best_family2
            bb["_score"] = best_score2
            return bb

    # 4) nessun match davvero: NO GOLD
    return None

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
        raise HTTPException(status_code=400, detail="Body JSON non valido.")

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

    if lang != "it":
        text = translate_text(text, lang)

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
    family: Optional[str] = Query(None),
):
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

    if lang != "it":
        text = translate_text(text, lang)

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
