import os
import json
import re
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# =========================================================
# CONFIG DI BASE
# =========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "static", "data")

DEFAULT_FAMILIES = [
    "COMM",
    "CTF",
    "VCEM",
    "CTCEM",
    "CTL",
    "CTL_MAXI",
    "DIAPASON",
    "P560",
]

app = FastAPI()

# monta static se esiste
static_path = os.path.join(BASE_DIR, "static")
if os.path.isdir(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

runtime_mode = "gold"  # gold | canonical, di default GOLD fisso

kb: Dict[str, List[Dict[str, Any]]] = {}
config_runtime: Dict[str, Any] = {}


# =========================================================
# UTILITA'
# =========================================================

def load_json(path: str) -> Optional[Any]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def detect_language(text: str) -> str:
    t = text.lower()
    # euristiche semplici, niente librerie strane
    if re.search(r"\b(the |can |use |with |on |steel|concrete)\b", t):
        return "en"
    if re.search(r"[àèéìòù]", t) or "soletta" in t or "solaio" in t:
        return "it"
    if "¿" in t or "qué" in t or "conectores" in t:
        return "es"
    if "quelle" in t or "plancher" in t or "béton" in t:
        return "fr"
    if "welche" in t or "verbinder" in t or "beton" in t:
        return "de"
    return "it"  # fallback


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def text_from_block(block: Dict[str, Any]) -> str:
    parts = []
    for key in [
        "question",
        "canonical",
        "answer",
        "answer_it",
        "gold",
        "note",
        "notes",
    ]:
        v = block.get(key)
        if isinstance(v, str):
            parts.append(v)
    rv = block.get("response_variants")
    if isinstance(rv, dict):
        for v in rv.values():
            if isinstance(v, str):
                parts.append(v)
    elif isinstance(rv, list):
        for v in rv:
            if isinstance(v, str):
                parts.append(v)
    return normalize(" ".join(parts))


def score_block(query: str, block: Dict[str, Any]) -> float:
    """
    Scoring semplice: overlap parole chiave, niente magia.
    Serve SOLO per scegliere il blocco migliore tra quelli GIUSTI.
    """
    qt = re.findall(r"\w+", query.lower())
    bt = text_from_block(block).lower()
    if not bt:
        return 0.0

    q_terms = [w for w in qt if len(w) > 2]
    if not q_terms:
        return 0.0

    score = 0
    for w in set(q_terms):
        if w in bt:
            score += 1
    # piccolo bonus se matcha tag family_name
    fam = block.get("family") or block.get("famiglia")
    if isinstance(fam, str) and fam.lower() in bt:
        score += 0.5
    return float(score)


def pick_family_from_question(question: str) -> Optional[str]:
    q = question.lower()
    # mappa semplice, estendibile
    if "ctf" in q:
        return "CTF"
    if "v-cem" in q or "vcem" in q:
        return "VCEM"
    if "ctcem" in q:
        return "CTCEM"
    if "ctl maxi" in q or "maxi" in q:
        return "CTL_MAXI"
    if "ctl" in q:
        return "CTL"
    if "diapason" in q:
        return "DIAPASON"
    if "p560" in q:
        return "P560"
    # fallback: nessuna, decideremo dopo
    return None


def load_kb() -> None:
    global kb, config_runtime

    # config.runtime.json se c'è
    cfg_path = os.path.join(DATA_DIR, "config.runtime.json")
    cfg = load_json(cfg_path)
    if isinstance(cfg, dict):
        config_runtime = cfg
    else:
        config_runtime = {
            "mode_default": "gold",
            "families": DEFAULT_FAMILIES,
        }

    fams = config_runtime.get("families") or DEFAULT_FAMILIES

    loaded: Dict[str, List[Dict[str, Any]]] = {}
    for fam in fams:
        name = f"{fam}.json"
        path = os.path.join(DATA_DIR, name)
        data = load_json(path)
        if isinstance(data, list):
            # normalizza, assicura campo family
            norm_list = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                if "family" not in item:
                    item["family"] = fam
                norm_list.append(item)
            loaded[fam] = norm_list
        elif isinstance(data, dict) and "items" in data:
            items = data.get("items") or []
            norm_list = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                if "family" not in item:
                    item["family"] = fam
                norm_list.append(item)
            loaded[fam] = norm_list
        else:
            loaded[fam] = []

    kb = loaded


load_kb()


# =========================================================
# COSTRUZIONE RISPOSTE
# =========================================================

def extract_gold_answer(block: Dict[str, Any], family: str, question: str) -> str:
    """
    Prende il testo più ricco disponibile dal blocco:
    - campi GOLD/variants se ci sono
    - altrimenti answer_it / canonical / answer
    + regole tipo P560 per CTF quando serve.
    """

    candidates: List[str] = []

    # preferiti espliciti GOLD / variants
    for key in ["gold", "answer_gold"]:
        v = block.get(key)
        if isinstance(v, str) and v.strip():
            candidates.append(v.strip())

    rv = block.get("response_variants")
    if isinstance(rv, dict):
        for v in rv.values():
            if isinstance(v, str) and v.strip():
                candidates.append(v.strip())
    elif isinstance(rv, list):
        for v in rv:
            if isinstance(v, str) and v.strip():
                candidates.append(v.strip())

    # fallback su campi standard
    for key in ["answer_it", "canonical", "answer"]:
        v = block.get(key)
        if isinstance(v, str) and v.strip():
            candidates.append(v.strip())

    if not candidates:
        return ""

    # prendi la più ricca (più lunga) come base GOLD
    answer = max(candidates, key=len)

    # Regola dura: CTF & chiodatrice → P560 obbligatoria
    q_low = question.lower()
    need_p560 = False
    if family == "CTF":
        if any(w in q_low for w in ["p560", "chiodatrice", "sparo", "powder", "pistola"]):
            need_p560 = True
    if need_p560 and "p560" not in answer.lower():
        answer = (
            answer.rstrip(". ")
            + " Il sistema CTF è certificato esclusivamente con la chiodatrice a polvere P560 Tecnaria "
              "e relativi chiodi idonei; l’uso di utensili diversi non rientra nel perimetro tecnico Tecnaria."
        )

    return normalize(answer)


def extract_canonical_answer(block: Dict[str, Any]) -> str:
    # Canonico = versione sintetica e tecnica
    for key in ["canonical", "answer_it", "answer"]:
        v = block.get(key)
        if isinstance(v, str) and v.strip():
            return normalize(v)
    rv = block.get("response_variants")
    if isinstance(rv, dict):
        for k, v in rv.items():
            if "synth" in k.lower() or "short" in k.lower():
                if isinstance(v, str) and v.strip():
                    return normalize(v)
    return ""


def build_answer(question: str, mode: str) -> Dict[str, Any]:
    """
    Core Q/A:
    - sceglie famiglia,
    - fa retrieval,
    - monta risposta GOLD o CANONICAL,
    - fallback sicuri.
    """
    q = normalize(question)
    if not q:
        return {
            "ok": False,
            "error": "Domanda vuota.",
        }

    # override manuale via prefissi
    override_mode = None
    if q.lower().startswith("gold:"):
        override_mode = "gold"
        q = q[5:].strip()
    elif q.lower().startswith("canonico:") or q.lower().startswith("canonical:"):
        override_mode = "canonical"
        q = re.sub(r"^(canonico:|canonical:)\s*", "", q, flags=re.IGNORECASE)

    eff_mode = override_mode or mode or "gold"

    lang = detect_language(q)
    hinted_family = pick_family_from_question(q)

    # cand: se famiglia nota, cerca lì; altrimenti tutte
    families_to_search = [hinted_family] if hinted_family else list(kb.keys())

    best_block = None
    best_score = 0.0
    best_family = hinted_family

    for fam in families_to_search:
        items = kb.get(fam) or []
        for block in items:
            s = score_block(q, block)
            if s > best_score:
                best_score = s
                best_block = block
                best_family = fam

    if not best_block or best_score <= 0:
        # niente trovato in modo affidabile
        return {
            "ok": False,
            "answer": (
                "Per questa domanda non trovo una risposta GOLD affidabile nei dati Tecnaria caricati. "
                "Serve una verifica tecnica specifica con l’ufficio Tecnaria."
            ),
            "meta": {
                "mode_runtime": eff_mode,
                "detected_lang": lang,
                "score": best_score,
            },
        }

    # costruisci risposta in base alla modalità
    if eff_mode == "canonical":
        answer = extract_canonical_answer(best_block)
        mode_label = "canonical"
    else:
        # GOLD di default
        answer = extract_gold_answer(best_block, best_family or "", q)
        mode_label = "gold"

    if not answer:
        # blocco trovato ma senza testo valido → fallback safe
        answer = (
            "Esiste un riferimento a questo tema nei dati Tecnaria, ma il blocco non contiene ancora "
            "un testo GOLD utilizzabile. Per non rischiare una risposta errata, ti invito a contattare "
            "direttamente il supporto tecnico Tecnaria con i dettagli del caso."
        )

    return {
        "ok": True,
        "answer": answer,
        "family": best_family,
        "id": best_block.get("id"),
        "meta": {
            "mode_runtime": mode_label,
            "detected_lang": lang,
            "score": round(best_score, 4),
        },
    }


# =========================================================
# ENDPOINTS
# =========================================================

@app.get("/api/health")
def api_health():
    return {
        "ok": True,
        "message": "TECNARIA Sinapsi backend attivo",
        "mode": runtime_mode,
        "families": list(kb.keys()),
    }


@app.get("/api/debug/kb_stats")
def api_kb_stats():
    fam_stats = {}
    for fam, items in kb.items():
        fam_stats[fam] = {
            "count": len(items),
        }
    return {
        "ok": True,
        "families": fam_stats,
    }


@app.get("/api/config")
def get_config():
    return {
        "ok": True,
        "mode": runtime_mode,
        "config_runtime": config_runtime,
    }


@app.post("/api/config")
async def set_config(req: Request):
    global runtime_mode
    data = await req.json()
    mode = (data.get("mode") or "").lower()
    if mode in ("gold", "canonical"):
        runtime_mode = mode
    return {
        "ok": True,
        "mode": runtime_mode,
    }


@app.post("/api/ask")
async def api_ask(req: Request):
    data = await req.json()
    question = data.get("question") or data.get("q") or ""
    result = build_answer(question, runtime_mode)
    return JSONResponse(result)


# =========================================================
# UI SEMPLICE TECNARIA SINAPSI
# =========================================================

HTML_UI = """
<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8" />
  <title>TECNARIA Sinapsi • GOLD Assistant</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <style>
    body {
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background: #0f172a;
      color: #e5e7eb;
      display: flex;
      justify-content: center;
      padding: 24px;
    }
    .app {
      width: 100%;
      max-width: 1100px;
      background: #020817;
      border-radius: 22px;
      padding: 24px 24px 18px;
      box-shadow: 0 18px 45px rgba(0,0,0,0.6);
      border: 1px solid rgba(148,163,253,0.18);
    }
    .header {
      display: flex;
      align-items: center;
      gap: 14px;
      margin-bottom: 12px;
    }
    .logo {
      width: 40px;
      height: 40px;
      border-radius: 11px;
      background: #f97316;
      display:flex;
      align-items:center;
      justify-content:center;
      font-weight:700;
      color:#111827;
      font-size:19px;
    }
    .title {
      font-size: 20px;
      font-weight: 600;
      color: #e5e7eb;
    }
    .subtitle {
      font-size: 12px;
      color: #9ca3af;
    }
    .pill {
      display: inline-flex;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 10px;
      gap: 6px;
      align-items: center;
      background: rgba(79,70,229,0.2);
      color: #a5b4fc;
      margin-left: 8px;
    }
    .mode-indicator {
      margin-left: auto;
      font-size: 10px;
      padding: 3px 8px;
      border-radius: 999px;
      background: #22c55e22;
      color: #4ade80;
      border: 1px solid #22c55e55;
    }
    .chat-box {
      margin-top: 8px;
      padding: 12px;
      background: #020817;
      border-radius: 16px;
      border: 1px solid #111827;
      min-height: 220px;
      max-height: 430px;
      overflow-y: auto;
      font-size: 13px;
    }
    .line-system {
      color: #6b7280;
      font-size: 10px;
      margin-bottom: 4px;
    }
    .line-q {
      color: #e5e7eb;
      margin-top: 8px;
      margin-bottom: 2px;
    }
    .line-a {
      color: #9ca3af;
      margin-bottom: 6px;
    }
    .meta {
      font-size: 9px;
      color: #6b7280;
      margin-bottom: 4px;
    }
    .input-row {
      margin-top: 10px;
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .input-row input {
      flex: 1;
      padding: 9px 10px;
      border-radius: 999px;
      border: 1px solid #111827;
      background: #020817;
      color: #e5e7eb;
      font-size: 12px;
      outline: none;
    }
    .input-row button {
      padding: 8px 14px;
      border-radius: 999px;
      border: none;
      cursor: pointer;
      font-size: 11px;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      background: #f97316;
      color: #111827;
      font-weight: 600;
    }
    .input-row button span {
      font-size: 14px;
    }
    .tips {
      margin-top: 6px;
      font-size: 9px;
      color: #6b7280;
    }
    .pills {
      margin-top: 6px;
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      font-size: 9px;
    }
    .pill-btn {
      padding: 4px 9px;
      border-radius: 999px;
      border: 1px solid #111827;
      background: #020817;
      color: #9ca3af;
      cursor: pointer;
    }
    .pill-btn:hover {
      border-color: #4f46e5;
      color: #e5e7eb;
    }
  </style>
</head>
<body>
  <div class="app">
    <div class="header">
      <div class="logo">T</div>
      <div>
        <div class="title">TECNARIA Sinapsi</div>
        <div class="subtitle">
          Assistente GOLD strutturale • connettori & sistemi misti
          <span class="pill">Instradamento automatico • CTF • VCEM • CTCEM • CTL • CTL MAXI • DIAPASON • P560</span>
        </div>
      </div>
      <div class="mode-indicator" id="mode-indicator">GOLD attivo</div>
    </div>

    <div class="chat-box" id="chat">
      <div class="line-system">Sistema • init • mode: GOLD</div>
      <div class="line-a">
        Benvenuto in Tecnaria Sinapsi. Modalità GOLD attiva: fai domande reali di cantiere o progetto
        (CTF, VCEM, CTCEM, CTL, CTL MAXI, DIAPASON, P560). Se scrivi <b>CANONICO:</b> prima della domanda,
        la risposta sarà più sintetica e tecnica.
      </div>
    </div>

    <div class="input-row">
      <input id="q" placeholder="Scrivi la tua domanda (es. 'Dove posso usare i connettori CTF?')" />
      <button onclick="ask()">
        <span>➜</span> Chiedi
      </button>
    </div>
    <div class="tips">
      GOLD = risposta completa strutturale • CANONICO: risposta tecnica sintetica (usalo solo se ti serve).
    </div>
    <div class="pills">
      <div class="pill-btn" onclick="setQ('Dove posso usare i connettori CTF?')">Uso CTF</div>
      <div class="pill-btn" onclick="setQ('Quando scegliere VCEM o CTCEM per un solaio in laterocemento?')">VCEM vs CTCEM</div>
      <div class="pill-btn" onclick="setQ('Differenza tra CTL e CTL MAXI?')">CTL vs CTL MAXI</div>
      <div class="pill-btn" onclick="setQ('Quando è meglio usare i DIAPASON?')">Uso DIAPASON</div>
      <div class="pill-btn" onclick="setQ('Con riferimento ai connettori CTF Tecnaria si possono posare con chiodatrice qualsiasi?')">P560 & CTF</div>
      <div class="pill-btn" onclick="setQ('Come contatto Tecnaria per assistenza tecnica in cantiere?')">Supporto Tecnaria</div>
    </div>
  </div>

<script>
async function ask() {
  const inp = document.getElementById('q');
  const chat = document.getElementById('chat');
  const text = (inp.value || "").trim();
  if (!text) return;
  chat.innerHTML += `<div class="line-q">Domanda • ${escapeHtml(text)}</div>`;
  inp.value = "";
  try {
    const res = await fetch('/api/ask', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({question: text})
    });
    const data = await res.json();
    if (!data.ok) {
      chat.innerHTML += `<div class="line-a">${escapeHtml(data.answer || data.error || 'Nessuna risposta utile.')}</div>`;
    } else {
      const meta = data.meta || {};
      const fam = data.family || 'N/D';
      const id = data.id || 'N/D';
      const score = meta.score !== undefined ? meta.score : '';
      chat.innerHTML += `<div class="line-a">${escapeHtml(data.answer)}</div>`;
      chat.innerHTML += `<div class="meta">Famiglia: ${fam} • ID: ${id} • score: ${score} • mode: ${meta.mode_runtime}</div>`;
    }
  } catch (e) {
    chat.innerHTML += `<div class="line-a">Errore di comunicazione con il backend.</div>`;
  }
  chat.scrollTop = chat.scrollHeight;
}

function setQ(t) {
  const inp = document.getElementById('q');
  inp.value = t;
  inp.focus();
}

function escapeHtml(t) {
  return t.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def root():
    return HTML_UI
