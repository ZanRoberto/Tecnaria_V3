import os
import json
import re
from typing import Dict, Any, List, Optional, Tuple

from fastapi import FastAPI, Body
from fastapi.responses import JSONResponse

# ============================================================
#  CONFIG DI BASE (sovrascrivibile con static/data/config.runtime.json)
# ============================================================

DEFAULT_CONFIG: Dict[str, Any] = {
    "gold_mode": True,
    "data_paths": {
        "index": "static/data/index_tecnaria.json",
        "router": "static/data/overlays/tecnaria_router_gold.json",
        "gold_content": "static/data/patches/tecnaria_gold_consolidato.json",
    },
    "routing": {
        # fallback assoluto se non troviamo niente
        "fallback_family": "COMM",
        "fallback_id": "COMM-FALLBACK-NOANSWER-0001",
        # normalizzazione testo
        "normalize": {
            "lower": True,
            "collapse_spaces": True,
            "strip_accents": False,
        },
    },
}


# ============================================================
#  FUNZIONI DI UTILITÀ
# ============================================================

def load_json(path: str) -> Any:
    """Carica JSON da file; se non esiste o è rotto, ritorna None."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def normalize_text(s: str, cfg: Dict[str, Any]) -> str:
    if not isinstance(s, str):
        return ""
    txt = s
    if cfg.get("lower", True):
        txt = txt.lower()
    if cfg.get("strip_accents", False):
        # opzionale: semplice sostituzione, evita dipendenze esterne
        accents = {
            "à": "a", "è": "e", "é": "e", "ì": "i", "ò": "o", "ù": "u",
            "À": "A", "È": "E", "É": "E", "Ì": "I", "Ò": "O", "Ù": "U",
        }
        for k, v in accents.items():
            txt = txt.replace(k, v)
    if cfg.get("collapse_spaces", True):
        txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def safe_get(d: Dict[str, Any], *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


# ============================================================
#  STATO CARICATO (CONFIG + CONTENUTI)
# ============================================================

class TecnariaState:
    def __init__(self):
        self.config: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self.index_entries: List[Dict[str, Any]] = []
        self.router_rules: List[Dict[str, Any]] = []
        self.items: Dict[str, Dict[str, Any]] = {}
        self.items_by_family: Dict[str, List[Dict[str, Any]]] = {}

    # ---------------- CONFIG ----------------

    def load_config_runtime(self):
        cfg_path = "static/data/config.runtime.json"
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    runtime_cfg = json.load(f)
                # merge molto semplice
                for k, v in runtime_cfg.items():
                    if isinstance(v, dict) and isinstance(self.config.get(k), dict):
                        self.config[k].update(v)
                    else:
                        self.config[k] = v
            except Exception:
                # se è rotto, ignoro e tengo DEFAULT_CONFIG
                pass

    # ---------------- CONTENUTI GOLD ----------------

    def load_gold_content(self):
        path = self.config["data_paths"]["gold_content"]
        data = load_json(path)
        if data is None:
            self.items = {}
            self.items_by_family = {}
            return

        if isinstance(data, dict) and "items" in data:
            items = data["items"]
        elif isinstance(data, list):
            items = data
        else:
            items = []

        self.items = {}
        self.items_by_family = {}

        for it in items:
            if not isinstance(it, dict):
                continue
            _id = it.get("id")
            fam = it.get("family") or it.get("famiglia") or "COMM"
            if not _id:
                continue
            self.items[_id] = it
            self.items_by_family.setdefault(fam, []).append(it)

    # ---------------- INDEX ----------------

    def load_index(self):
        path = self.config["data_paths"]["index"]
        data = load_json(path)
        if data is None:
            self.index_entries = []
            return

        # supporta sia {"entries":[...]} sia lista pura
        if isinstance(data, dict) and "entries" in data:
            entries = data["entries"]
        elif isinstance(data, list):
            entries = data
        else:
            entries = []

        norm_cfg = self.config["routing"]["normalize"]
        norm_entries: List[Dict[str, Any]] = []

        for e in entries:
            if not isinstance(e, dict):
                continue
            patterns = e.get("patterns") or e.get("triggers") or e.get("keywords") or []
            if isinstance(patterns, str):
                patterns = [patterns]
            norm_patterns = [normalize_text(p, norm_cfg) for p in patterns if isinstance(p, str)]
            if not norm_patterns:
                continue
            norm_entries.append({
                "id": e.get("id"),
                "family": e.get("family"),
                "patterns": norm_patterns,
                "priority": e.get("priority", 0),
            })

        # ordina per priority desc per avere prima i più specifici
        self.index_entries = sorted(norm_entries, key=lambda x: x["priority"], reverse=True)

    # ---------------- ROUTER ----------------

    def load_router(self):
        path = self.config["data_paths"]["router"]
        data = load_json(path)
        if data is None:
            self.router_rules = []
            return

        if isinstance(data, dict) and "rules" in data:
            rules = data["rules"]
        elif isinstance(data, list):
            rules = data
        else:
            rules = []

        norm_cfg = self.config["routing"]["normalize"]
        norm_rules: List[Dict[str, Any]] = []

        for r in rules:
            if not isinstance(r, dict):
                continue
            contains = r.get("contains") or r.get("contains_any") or []
            if isinstance(contains, str):
                contains = [contains]
            contains_norm = [normalize_text(c, norm_cfg) for c in contains if isinstance(c, str)]

            regex = r.get("regex")
            try:
                regex_compiled = re.compile(regex, re.IGNORECASE) if isinstance(regex, str) else None
            except re.error:
                regex_compiled = None

            norm_rules.append({
                "id": r.get("id"),
                "family_hint": r.get("family_hint"),
                "target_id": r.get("target_id"),
                "contains": contains_norm,
                "regex": regex_compiled,
                "priority": r.get("priority", 0),
            })

        self.router_rules = sorted(norm_rules, key=lambda x: x["priority"], reverse=True)


S = TecnariaState()
S.load_config_runtime()
S.load_gold_content()
S.load_index()
S.load_router()


# ============================================================
#  LOGICA DI RISPOSTA GOLD
# ============================================================

def enforce_terminology(family: str, answer: str) -> str:
    """Pulisce terminologia (perni → chiodi idonei Tecnaria, ecc.)."""
    if not isinstance(answer, str):
        return answer

    # mai "perni"
    answer = re.sub(r"\bperni\b", "chiodi idonei Tecnaria", answer, flags=re.IGNORECASE)

    # CTF / P560: assicurati che compaiano i termini chiave
    fam = (family or "").upper()
    if fam in ("CTF", "P560"):
        if "chiodi idonei tecnaria" not in answer.lower():
            answer += "\n\nNota: i connettori CTF si fissano esclusivamente con chiodi idonei Tecnaria."
        if "p560" not in answer.lower():
            answer += "\nLa posa è prevista solo con chiodatrice a polvere P560 Tecnaria con adattatori dedicati."

    return answer


def pick_gold_answer(item: Dict[str, Any], lang: str = "it") -> str:
    """Estrae la risposta GOLD in italiano; fallback su altri campi se necessario."""
    if not item:
        return "Per questa domanda non trovo una risposta GOLD consolidata."

    # 1) response_variants.gold.<lang>
    rv = item.get("response_variants") or {}
    gold = rv.get("gold") or rv.get("GOLD") or {}
    if isinstance(gold, dict):
        txt = gold.get(lang) or gold.get("it") or gold.get("default")
        if isinstance(txt, str) and txt.strip():
            return txt.strip()

    # 2) campi alternativi
    for key in ("answer", "risposta", "text", "contenuto"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()

    return "Per questa domanda non trovo una risposta GOLD consolidata."


# ============================================================
#  STRATI DI MATCHING
# ============================================================

def router_match(q_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Usa tecnaria_router_gold.json: regole con contains/regex,
    eventualmente con target_id diretto.
    """
    for r in S.router_rules:
        matched = False
        # contains_any
        if r["contains"]:
            if any(c and c in q_norm for c in r["contains"]):
                matched = True
        # regex
        if not matched and r["regex"] is not None:
            if r["regex"].search(q_norm):
                matched = True

        if not matched:
            continue

        target_id = r.get("target_id")
        family_hint = r.get("family_hint")

        if target_id:
            it = S.items.get(target_id)
            fam = (it.get("family") if it else family_hint) or family_hint
            return fam, target_id

        return family_hint, None

    return None, None


def index_match(q_norm: str, family_hint: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Usa index_tecnaria.json: patterns → id/family.
    """
    candidates = S.index_entries
    for e in candidates:
        fam = e.get("family")
        if family_hint and fam and fam != family_hint:
            continue
        if any(pat and pat in q_norm for pat in e["patterns"]):
            return fam, e.get("id")
    return None, None


def triggers_match(q_norm: str, family_hint: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Cerca nei connettori (tecnaria_gold_consolidato.json) usando triggers_brevi.
    """
    search_fams: List[str]
    if family_hint:
        search_fams = [family_hint]
    else:
        search_fams = list(S.items_by_family.keys())

    norm_cfg = S.config["routing"]["normalize"]

    for fam in search_fams:
        for it in S.items_by_family.get(fam, []):
            triggers = it.get("triggers_brevi") or it.get("triggers") or []
            if isinstance(triggers, str):
                triggers = [triggers]
            norm_triggers = [normalize_text(t, norm_cfg) for t in triggers if isinstance(t, str)]
            if any(t and t in q_norm for t in norm_triggers):
                return fam, it.get("id")

    return None, None


def keyword_fuzzy_match(q_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Fallback fuzzy: conteggio token in comune fra domanda e risposta GOLD.
    Non è 'AI', ma un banale scoring bag-of-words.
    """
    tokens = [t for t in re.split(r"\W+", q_norm) if t]
    if not tokens:
        return None, None

    best_score = 0.0
    best_item_id = None
    best_family = None

    for _id, it in S.items.items():
        txt = pick_gold_answer(it)
        txt_norm = normalize_text(txt, S.config["routing"]["normalize"])
        if not txt_norm:
            continue
        score = 0
        for t in tokens:
            if t in txt_norm:
                score += 1
        if score > best_score:
            best_score = score
            best_item_id = _id
            best_family = it.get("family")

    if best_score <= 0:
        return None, None

    return best_family, best_item_id


def hardwired_shortcuts(q_norm: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Agganci 'hard' per domande critiche già note:
    - VCEM vs CTCEM
    - DIAPASON 'quando usarli'
    - CTF preforo
    ecc.
    Questo serve a non dipendere da eventuali buchi di index/router.
    """
    # VCEM vs CTCEM (quando scegliere)
    if "vcem" in q_norm and "ctcem" in q_norm and ("quando" in q_norm or "sceglier" in q_norm):
        it = S.items.get("CROSS-VCEMvsCTCEM-DECIDER-001")
        if it:
            return it.get("family") or "CROSS", it.get("id")

    # DIAPASON: "quando è meglio usare i diapason"
    if "diapason" in q_norm and ("quando" in q_norm or "meglio usare" in q_norm or "quando è meglio" in q_norm):
        it = S.items.get("DIAPASON-QUANDO-0001")
        if it:
            return it.get("family") or "DIAPASON", it.get("id")

    # CTF preforo
    if "ctf" in q_norm and "preforo" in q_norm:
        it = S.items.get("CTF-PREFORO-0001")
        if it:
            return it.get("family") or "CTF", it.get("id")

    # P560 DPI minimi
    if "p560" in q_norm and ("dpi" in q_norm or "protezione" in q_norm):
        it = S.items.get("P560-DPI-0001")
        if it:
            return it.get("family") or "P560", it.get("id")

    # CTL fessure
    if ("ctl" in q_norm or "connettore ctl" in q_norm) and ("fessur" in q_norm or "fessure" in q_norm):
        it = S.items.get("CTL-FESSURE-0001")
        if it:
            return it.get("family") or "CTL", it.get("id")

    return None, None


def route_question(q: str) -> Tuple[str, str]:
    """
    Pipeline di routing:
    1) scorciatoie hardwired per domande critiche
    2) router (overlays)
    3) index (index_tecnaria)
    4) triggers connettori
    5) fuzzy keywords
    6) fallback COMM
    """
    norm_cfg = S.config["routing"]["normalize"]
    q_norm = normalize_text(q, norm_cfg)

    # 1) short-cuts
    fam, _id = hardwired_shortcuts(q_norm)
    if _id:
        return fam or S.config["routing"]["fallback_family"], _id

    # 2) router
    fam_hint, _id = router_match(q_norm)
    if _id:
        return fam_hint or S.config["routing"]["fallback_family"], _id

    # 3) index
    fam_from_index, _id = index_match(q_norm, fam_hint)
    if _id:
        return fam_from_index or fam_hint or S.config["routing"]["fallback_family"], _id

    # 4) triggers (famiglia suggerita)
    fam_trig, _id = triggers_match(q_norm, fam_from_index or fam_hint)
    if _id:
        return fam_trig or fam_from_index or fam_hint or S.config["routing"]["fallback_family"], _id

    # 5) fuzzy
    fam_fuzzy, _id = keyword_fuzzy_match(q_norm)
    if _id:
        return fam_fuzzy or S.config["routing"]["fallback_family"], _id

    # 6) fallback COMM
    fb_fam = S.config["routing"]["fallback_family"]
    fb_id = S.config["routing"]["fallback_id"]
    return fb_fam, fb_id


# ============================================================
#  FASTAPI APP
# ============================================================

app = FastAPI(title="Tecnaria Sinapsi – GOLD", version="1.0.0")


@app.get("/")
def root():
    return {
        "ok": True,
        "message": "Tecnaria Sinapsi – GOLD attivo",
        "gold_mode": S.config.get("gold_mode", True),
    }


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/ask")
def api_ask(
    payload: Dict[str, Any] = Body(..., example={"q": "Quando scegliere VCEM o CTCEM per un solaio in laterocemento?", "lang": "it", "mode": "gold"})
):
    q = (payload.get("q") or "").strip()
    lang = (payload.get("lang") or "it").lower()
    mode = (payload.get("mode") or "gold").lower()

    if not q:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Domanda mancante (campo 'q')."},
        )

    # per ora mode diverso da gold viene trattato comunque come GOLD
    family, item_id = route_question(q)
    item = S.items.get(item_id)

    # se item non trovato, fallback secco
    if not item:
        fb_fam = S.config["routing"]["fallback_family"]
        fb_id = S.config["routing"]["fallback_id"]
        item = S.items.get(fb_id, {
            "id": fb_id,
            "family": fb_fam,
            "response_variants": {
                "gold": {
                    "it": "Per questa domanda non trovo una risposta GOLD consolidata."
                }
            },
        })
        family = fb_fam
        item_id = fb_id

    answer = pick_gold_answer(item, lang=lang)
    answer = enforce_terminology(family, answer)

    return JSONResponse(
        {
            "ok": True,
            "answer": answer,
            "family": family,
            "id": item_id,
            "mode": "gold",
            "lang": lang,
        }
    )


# Avvio locale (non usato su Render, ma utile in debug)
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
