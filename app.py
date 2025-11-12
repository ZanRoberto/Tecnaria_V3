import os
import json
import re
from typing import Dict, Any, List, Tuple, Optional
from fastapi import FastAPI, Body
from fastapi.responses import JSONResponse

# === Config ===
# Percorsi di default: possono essere sovrascritti da config.runtime.json
DEFAULT_CONFIG = {
    "gold_mode": True,
    "data_paths": {
        "index": "static/data/index_tecnaria.json",
        "router": "static/data/overlays/tecnaria_router_gold.json",
        "gold_content": "static/data/patches/tecnaria_gold_consolidato.json",
        "tests": {
            "must_pass": "static/data/tests/must_pass.json",
            "smoke_200": "static/data/tests/smoke_200.json",
            "expected_patterns": "static/data/tests/expected_patterns.json"
        }
    },
    "routing": {
        "normalize": {"lowercase": True, "strip_accents": True, "collapse_spaces": True},
        "fallback_family": "COMM",
        "fallback_id": "COMM-CANALE-CORE-001"
    },
    "terminologia": {
        "vietati": ["perni"],
        "obbligatori": {
            "CTF": ["P560", "chiodi idonei Tecnaria"]
        }
    }
}

CONFIG_PATH = os.environ.get(
    "TECNARIA_CONFIG",
    os.path.join("static", "data", "config.runtime.json")
)

app = FastAPI(title="Tecnaria Sinapsi – GOLD")

# === Stato in memoria ===
class State:
    config: Dict[str, Any] = {}
    index: List[Dict[str, Any]] = []
    router_rules: List[Dict[str, Any]] = []
    items: Dict[str, Dict[str, Any]] = {}  # by id
    items_by_family: Dict[str, List[Dict[str, Any]]] = {}

S = State()

# === Utils ===
def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def strip_accents(s: str) -> str:
    # quick & robust without external deps
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

def normalize_text(s: str, policy: Dict[str, Any]) -> str:
    t = s or ""
    if policy.get("strip_accents", True):
        t = strip_accents(t)
    if policy.get("lowercase", True):
        t = t.lower()
    if policy.get("collapse_spaces", True):
        t = re.sub(r"\s+", " ", t).strip()
    return t

def ensure_list_items(payload: Any) -> List[Dict[str, Any]]:
    """Accetta sia {"items":[...]} che una lista di oggetti."""
    if isinstance(payload, dict) and "items" in payload and isinstance(payload["items"], list):
        return payload["items"]
    if isinstance(payload, list):
        return payload
    return []

def build_items_index(items: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    by_id = {}
    by_family = {}
    for it in items:
        _id = it.get("id")
        fam = it.get("family", "COMM")
        if not _id:
            continue
        by_id[_id] = it
        by_family.setdefault(fam, []).append(it)
    return by_id, by_family

def match_any_substring(text_norm: str, patterns: List[str], policy: Dict[str, Any]) -> bool:
    for p in patterns:
        if normalize_text(p, policy) in text_norm:
            return True
    return False

def find_by_triggers(q_norm: str, family_hint: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Se abbiamo un 'family_hint' cerchiamo prima dentro quella family.
    Altrimenti cerchiamo globalmente l'item con triggers_brevi che matchano q_norm.
    """
    search_families = [family_hint] if family_hint else list(S.items_by_family.keys())
    for fam in search_families:
        for it in S.items_by_family.get(fam, []):
            triggers = it.get("triggers_brevi", [])
            if not isinstance(triggers, list): 
                continue
            if any(normalize_text(t, S.config["routing"]["normalize"]) in q_norm for t in triggers):
                return it
    # fallback: cerca ovunque
    for it in S.items.values():
        triggers = it.get("triggers_brevi", [])
        if not isinstance(triggers, list):
            continue
        if any(normalize_text(t, S.config["routing"]["normalize"]) in q_norm for t in triggers):
            return it
    return None

def route_query(q: str) -> Tuple[str, str]:
    """
    Ordine: Router (regole prioritarie) -> Index (intents/patterns) -> triggers_brevi -> Fallback.
    Ritorna (family, id).
    """
    policy = S.config["routing"]["normalize"]
    qn = normalize_text(q, policy)

    # 1) Router: regole ordinate per priority desc
    rules = sorted(S.router_rules, key=lambda r: r.get("priority", 0), reverse=True)
    for r in rules:
        pats = r.get("if_text_matches_any", [])
        if match_any_substring(qn, pats, policy):
            route_to = r.get("route_to") or {}
            fam = route_to.get("family")
            _id = route_to.get("id")
            if fam and _id:
                return fam, _id
            # stop_on_match senza route_to definita -> ignora
            if r.get("stop_on_match", False):
                break

    # 2) Index: intents con patterns
    intents = sorted(S.index, key=lambda x: x.get("priority", 0), reverse=True)
    for it in intents:
        pats = it.get("patterns", [])
        if match_any_substring(qn, pats, policy):
            route_to = it.get("route") or {}
            fam = route_to.get("family")
            _id = route_to.get("id")
            if fam and _id:
                return fam, _id

    # 3) triggers_brevi: se l’utente ha scritto una cosa vicina ai triggers
    hit = find_by_triggers(qn, family_hint=None)
    if hit:
        return hit.get("family", S.config["routing"]["fallback_family"]), hit.get("id", S.config["routing"]["fallback_id"])

    # 4) Fallback
    return S.config["routing"]["fallback_family"], S.config["routing"]["fallback_id"]

def pick_gold_answer(item: Dict[str, Any]) -> str:
    rv = item.get("response_variants", {})
    gold = rv.get("gold", {})
    # lingua IT obbligatoria per noi
    txt = gold.get("it") or ""
    return txt

def enforce_terminology(family: str, txt: str) -> str:
    # vietati
    for v in S.config.get("terminologia", {}).get("vietati", []):
        # se compare il termine vietato, lo “neutralizziamo” (opzionale: sostituire/omissis)
        txt = re.sub(rf"\b{re.escape(v)}\b", "[termine-non-ammesso]", txt, flags=re.IGNORECASE)

    # obbligatori per famiglia (si assicura che compaiano almeno una volta)
    obbl = S.config.get("terminologia", {}).get("obbligatori", {}).get(family, [])
    for req in obbl:
        if req.lower() not in txt.lower():
            # Aggiungi una riga nota tecnica in coda
            txt += f"\n\nNota tecnica: requisito terminologico — {req}."
    return txt

def load_all() -> Dict[str, str]:
    # 1) config
    conf = DEFAULT_CONFIG.copy()
    try:
        if os.path.exists(CONFIG_PATH):
            disk = load_json(CONFIG_PATH)
            # merge shallow per semplicità
            conf.update(disk)
            # merge sub-dict noti
            for k in ("data_paths", "routing", "terminologia"):
                if k in disk and isinstance(disk[k], dict):
                    conf[k].update(disk[k])
    except Exception as e:
        print(f"[WARN] config.runtime.json non letto: {e}. Uso DEFAULT_CONFIG.")
    S.config = conf

    # 2) index
    idx_path = conf["data_paths"]["index"]
    S.index = []
    if os.path.exists(idx_path):
        payload = load_json(idx_path)
        S.index = payload if isinstance(payload, list) else payload.get("intents", [])
    else:
        print(f"[WARN] index non trovato: {idx_path}")

    # 3) router
    router_path = conf["data_paths"]["router"]
    S.router_rules = []
    if os.path.exists(router_path):
        payload = load_json(router_path)
        # accetta sia {"rules":[...]} che lista
        rules = payload.get("rules") if isinstance(payload, dict) else payload
        if isinstance(rules, list):
            S.router_rules = rules
    else:
        print(f"[WARN] router non trovato: {router_path}")

    # 4) contenuti GOLD consolidati
    gold_path = conf["data_paths"]["gold_content"]
    items_raw = []
    if os.path.exists(gold_path):
        payload = load_json(gold_path)
        items_raw = ensure_list_items(payload)
    else:
        print(f"[WARN] gold_content non trovato: {gold_path}")

    S.items, S.items_by_family = build_items_index(items_raw)

    # Log di controllo
    return {
        "config": os.path.abspath(CONFIG_PATH),
        "index": os.path.abspath(idx_path),
        "router": os.path.abspath(router_path),
        "gold_content": os.path.abspath(gold_path),
        "n_index": str(len(S.index)),
        "n_rules": str(len(S.router_rules)),
        "n_items": str(len(S.items)),
    }

# === Avvio: carica tutto una volta ===
LOAD_INFO = load_all()
print("[BOOT] Caricati file:")
for k,v in LOAD_INFO.items():
    print(f"  - {k}: {v}")

# === API ===
@app.get("/health")
def health():
    return {"status": "ok", "gold_mode": S.config.get("gold_mode", True), "loaded": LOAD_INFO}

@app.post("/reload")
def reload_all():
    info = load_all()
    return {"status":"reloaded", "loaded": info}

@app.post("/api/ask")
def ask(payload: Dict[str, Any] = Body(...)):
    q = payload.get("q") or payload.get("question") or ""
    lang = payload.get("lang", "it")
    mode = payload.get("mode", "gold")  # al momento gestiamo solo gold

    fam, _id = route_query(q)
    item = S.items.get(_id)
    if not item:
        # fallback se id mancante: prova a cercare via triggers nella famiglia proposta
        hit = find_by_triggers(normalize_text(q, S.config["routing"]["normalize"]), fam)
        if hit:
            fam = hit.get("family", fam)
            _id = hit.get("id", _id)
            item = hit

    if not item:
        # fallback finale
        fam = S.config["routing"]["fallback_family"]
        _id = S.config["routing"]["fallback_id"]
        item = S.items.get(_id, {"id": _id, "family": fam, "response_variants":{"gold":{"it":"Per questa domanda non trovo una risposta GOLD consolidata."}}})

    answer = pick_gold_answer(item)
    answer = enforce_terminology(fam, answer)

    return JSONResponse({
        "answer": answer,
        "family": fam,
        "id": _id,
        "mode": "gold",
        "lang": lang
    })
