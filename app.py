import os
import json
import random
import unicodedata
from functools import lru_cache
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# =========================================================
# CONFIG
# =========================================================

APP_NAME = "Tecnaria Sinapsi — Q/A"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FAMILIES_DIR = os.path.join(BASE_DIR, "static", "data")

SUPPORTED_LANGS = ["it", "en", "fr", "de", "es"]
FALLBACK_LANG = "en"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

openai_client = None
if OPENAI_API_KEY:
    try:
        from openai import OpenAI
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception:
        openai_client = None

# =========================================================
# FRONTEND INLINE (INTERFACCIA)
# =========================================================

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8" />
  <title>Tecnaria Sinapsi — Q/A</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <style>
    :root {
      --bg: #050816;
      --bg-soft: #0b1020;
      --accent: #00e0ff;
      --accent-soft: rgba(0, 224, 255, 0.12);
      --danger: #ff3366;
      --text: #f5f5f5;
      --muted: #8f9bb3;
      --radius-xl: 22px;
      --radius-md: 14px;
      --border-soft: rgba(255,255,255,0.06);
      --shadow-soft: 0 14px 40px rgba(0,0,0,0.42);
      --font: system-ui, -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
      --transition-fast: 0.18s ease-out;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 0;
      font-family: var(--font);
      background: radial-gradient(circle at top, #151b30 0, #050816 40%, #000000 100%);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      justify-content: center;
      align-items: stretch;
    }
    .app {
      width: 100%;
      max-width: 1200px;
      margin: 26px auto;
      padding: 20px;
    }
    .app-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 18px;
    }
    .brand {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .brand-title {
      font-size: 22px;
      font-weight: 600;
      letter-spacing: 0.6px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .brand-pill {
      font-size: 10px;
      padding: 4px 9px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      border: 1px solid rgba(0, 224, 255, 0.32);
      text-transform: uppercase;
      letter-spacing: 1px;
    }
    .brand-sub {
      font-size: 11px;
      color: var(--muted);
    }
    .status {
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 4px;
      font-size: 11px;
      color: var(--muted);
    }
    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 9px;
      border-radius: 999px;
      font-size: 10px;
      border: 1px solid var(--border-soft);
      background: rgba(8,12,24,0.96);
    }
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: #00ff9d;
      box-shadow: 0 0 10px #00ff9d;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 3fr) minmax(260px, 1.4fr);
      gap: 16px;
    }
    @media (max-width: 900px) {
      .layout { grid-template-columns: 1fr; }
      .status { align-items: flex-start; }
    }
    .chat {
      background: rgba(5, 10, 22, 0.98);
      border-radius: var(--radius-xl);
      padding: 14px 14px 12px;
      border: 1px solid var(--border-soft);
      box-shadow: var(--shadow-soft);
      display: flex;
      flex-direction: column;
      gap: 10px;
      height: min(76vh, 620px);
    }
    .chat-messages {
      flex: 1;
      overflow-y: auto;
      padding-right: 4px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      scrollbar-width: thin;
      scrollbar-color: var(--accent-soft) transparent;
    }
    .chat-messages::-webkit-scrollbar { width: 4px; }
    .chat-messages::-webkit-scrollbar-thumb {
      background: var(--accent-soft);
      border-radius: 999px;
    }
    .msg {
      display: flex;
      gap: 8px;
      align-items: flex-start;
      animation: fadeIn 0.18s ease-out;
    }
    .msg.user { justify-content: flex-end; }
    .msg-avatar {
      width: 20px;
      height: 20px;
      border-radius: 999px;
      background: var(--accent-soft);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 11px;
      flex-shrink: 0;
    }
    .msg.user .msg-avatar {
      background: rgba(0, 224, 255, 0.24);
      color: var(--accent);
    }
    .msg-bubble {
      max-width: 92%;
      padding: 7px 9px;
      border-radius: 16px;
      font-size: 12px;
      line-height: 1.5;
      border: 1px solid transparent;
      background: #0c1324;
      color: var(--text);
    }
    .msg.user .msg-bubble {
      background: var(--accent-soft);
      color: var(--accent);
      border-color: rgba(0, 224, 255, 0.38);
    }
    .msg-meta {
      display: flex;
      gap: 8px;
      margin-top: 4px;
      font-size: 9px;
      color: var(--muted);
      opacity: 0.86;
    }
    .msg-tag {
      padding: 2px 6px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.12);
    }
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(4px);}
      to { opacity: 1; transform: translateY(0);}
    }
    .chat-input-wrap {
      display: flex;
      flex-direction: column;
      gap: 4px;
      margin-top: 4px;
    }
    .top-row {
      display: flex;
      gap: 8px;
      align-items: center;
      font-size: 10px;
      color: var(--muted);
    }
    .select-family {
      padding: 6px 9px;
      border-radius: 999px;
      background: #050816;
      border: 1px solid var(--border-soft);
      color: var(--accent);
      font-size: 10px;
      outline: none;
    }
    .select-mode {
      padding: 5px 8px;
      border-radius: 999px;
      border: 1px solid var(--border-soft);
      background: transparent;
      color: var(--muted);
      font-size: 9px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      transition: var(--transition-fast);
    }
    .select-mode.active {
      color: var(--accent);
      background: var(--accent-soft);
      border-color: rgba(0, 224, 255, 0.45);
    }
    .input-row {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .chat-input {
      flex: 1;
      padding: 8px 10px;
      border-radius: 999px;
      border: 1px solid var(--border-soft);
      background: #050816;
      color: var(--text);
      font-size: 12px;
      outline: none;
      transition: var(--transition-fast);
    }
    .chat-input:focus {
      border-color: var(--accent);
      box-shadow: 0 0 12px rgba(0, 224, 255, 0.2);
    }
    .btn-send {
      padding: 8px 14px;
      border-radius: 999px;
      border: none;
      background: var(--accent);
      color: #020308;
      font-weight: 600;
      font-size: 11px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      box-shadow: 0 6px 18px rgba(0, 224, 255, 0.3);
      transition: var(--transition-fast);
      flex-shrink: 0;
    }
    .btn-send:hover {
      transform: translateY(-1px);
      box-shadow: 0 9px 24px rgba(0, 224, 255, 0.4);
    }
    .btn-send:active {
      transform: translateY(1px);
      box-shadow: 0 4px 10px rgba(0, 224, 255, 0.25);
    }
    .side {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .card {
      background: rgba(5, 10, 22, 0.98);
      border-radius: var(--radius-xl);
      padding: 10px 11px;
      border: 1px solid var(--border-soft);
      box-shadow: var(--shadow-soft);
      font-size: 10px;
      color: var(--muted);
    }
    .card-title {
      font-size: 11px;
      font-weight: 600;
      color: var(--accent);
      margin-bottom: 4px;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 2px 7px;
      border-radius: 999px;
      font-size: 8px;
      border: 1px solid rgba(255,255,255,0.14);
      color: var(--muted);
      margin-right: 3px;
      margin-top: 2px;
    }
    .hint-list {
      list-style: none;
      padding-left: 0;
      margin: 4px 0 0;
      display: grid;
      gap: 2px;
    }
    .hint-list li {
      cursor: pointer;
      padding: 3px 6px;
      border-radius: 8px;
      transition: var(--transition-fast);
      border: 1px solid transparent;
      color: var(--muted);
    }
    .hint-list li:hover {
      border-color: var(--accent-soft);
      color: var(--accent);
      background: rgba(0, 224, 255, 0.03);
    }
    .small-label {
      font-size: 8px;
      opacity: 0.7;
    }
    .danger { color: var(--danger); }
  </style>
</head>
<body>
  <div class="app">
    <div class="app-header">
      <div class="brand">
        <div class="brand-title">
          Tecnaria Sinapsi
          <span class="brand-pill">Q/A GOLD • NLM-v3</span>
        </div>
        <div class="brand-sub">
          Assistente tecnico-commerciale per connettori collaboranti Tecnaria.
        </div>
      </div>
      <div class="status">
        <div class="status-pill">
          <span class="status-dot" id="status-dot"></span>
          <span id="status-text">Verifica connessione...</span>
        </div>
        <div class="small-label">
          Backend: <code>/api/ask</code> • Config: <code>/api/config</code>
        </div>
      </div>
    </div>

    <div class="layout">
      <div class="chat">
        <div class="chat-messages" id="messages"></div>
        <div class="chat-input-wrap">
          <div class="top-row">
            <select id="family" class="select-family">
              <option value="VCEM">VCEM — Laterocemento</option>
              <option value="CTCEM">CTCEM — Travetti pieni</option>
              <option value="CTF">CTF — Lamiera grecata / acciaio</option>
              <option value="CTL">CTL — Legno-calcestruzzo</option>
              <option value="CTL_MAXI">CTL MAXI — Legno heavy duty</option>
              <option value="DIAPASON">DIAPASON — Recupero laterocemento</option>
              <option value="P560">P560 — Pistola e chiodi</option>
              <option value="COMM">COMM — Contatti / azienda</option>
            </select>
            <button id="mode-gold" class="select-mode active">
              <span>GOLD dynamic</span>
            </button>
            <div class="small-label">
              Scrivi in linguaggio naturale. Lingua riconosciuta automaticamente.
            </div>
          </div>
          <div class="input-row">
            <input id="q" class="chat-input"
              placeholder="Fai una domanda sui connettori Tecnaria..."
              autocomplete="off" />
            <button class="btn-send" id="send">
              <span>Invia</span><span>➜</span>
            </button>
          </div>
        </div>
      </div>

      <div class="side">
        <div class="card">
          <div class="card-title">Famiglie supportate</div>
          <div>
            <span class="pill">VCEM</span>
            <span class="pill">CTCEM</span>
            <span class="pill">CTF</span>
            <span class="pill">CTL / CTL MAXI</span>
            <span class="pill">DIAPASON</span>
            <span class="pill">P560</span>
            <span class="pill">COMM</span>
          </div>
          <p style="margin-top:6px;">
            Le risposte arrivano solo dai contenuti GOLD che hai caricato.
            Se manca una risposta, è un segnale per arricchire il JSON, non per inventare.
          </p>
        </div>

        <div class="card">
          <div class="card-title">Esempi di domande</div>
          <ul class="hint-list" id="hints">
            <li data-q="Posso sparare i VCEM con la P560?">Posso sparare i VCEM con la P560?</li>
            <li data-q="Dove posso usare i connettori CTF?">Dove posso usare i connettori CTF?</li>
            <li data-q="Come si posano i CTL su solaio in legno esistente?">Come si posano i CTL su solaio in legno esistente?</li>
            <li data-q="Differenza tra VCEM e CTCEM?">Differenza tra VCEM e CTCEM?</li>
            <li data-q="Quando usare DIAPASON invece dei VCEM?">Quando usare DIAPASON invece dei VCEM?</li>
            <li data-q="Che manutenzione richiede la P560?">Che manutenzione richiede la P560?</li>
          </ul>
        </div>

        <div class="card">
          <div class="card-title">Legend risposta</div>
          <ul class="hint-list">
            <li><strong>ID</strong>: blocco logico (es. VCEM-0002).</li>
            <li><strong>mode</strong>: canonical / dynamic.</li>
            <li><strong>lang</strong>: lingua risposta.</li>
          </ul>
          <p class="danger" style="margin-top:4px;">
            Se leggi “Nessuna risposta trovata”, il backend è ok: va esteso il JSON di quella famiglia.
          </p>
        </div>
      </div>
    </div>
  </div>

  <script>
    const messagesEl = document.getElementById("messages");
    const inputEl = document.getElementById("q");
    const sendBtn = document.getElementById("send");
    const familyEl = document.getElementById("family");
    const hintsEl = document.getElementById("hints");
    const statusDot = document.getElementById("status-dot");
    const statusText = document.getElementById("status-text");
    const modeGoldBtn = document.getElementById("mode-gold");

    let backendOK = false;

    async function checkConfig() {
      try {
        const res = await fetch("/api/config");
        if (!res.ok) throw new Error();
        const data = await res.json();
        statusDot.style.background = "#00ff9d";
        statusDot.style.boxShadow = "0 0 10px #00ff9d";
        statusText.textContent = (data.app || "Sinapsi") + " online • " + (data.families_dir || "");
        backendOK = true;
      } catch (e) {
        statusDot.style.background = "#ff3366";
        statusDot.style.boxShadow = "0 0 10px #ff3366";
        statusText.textContent = "Backend non raggiungibile /api/config";
        backendOK = false;
      }
    }

    function appendMessage(text, from = "bot", meta = null) {
      const wrap = document.createElement("div");
      wrap.className = "msg " + (from === "user" ? "user" : "bot");

      const avatar = document.createElement("div");
      avatar.className = "msg-avatar";
      avatar.textContent = from === "user" ? "TU" : "AI";

      const bubble = document.createElement("div");
      bubble.className = "msg-bubble";
      bubble.textContent = text;

      if (meta) {
        const metaEl = document.createElement("div");
        metaEl.className = "msg-meta";
        if (meta.id) {
          const id = document.createElement("div");
          id.className = "msg-tag";
          id.textContent = meta.id;
          metaEl.appendChild(id);
        }
        if (meta.mode) {
          const md = document.createElement("div");
          md.className = "msg-tag";
          md.textContent = meta.mode;
          metaEl.appendChild(md);
        }
        if (meta.lang) {
          const lg = document.createElement("div");
          lg.className = "msg-tag";
          lg.textContent = meta.lang;
          metaEl.appendChild(lg);
        }
        if (meta.note) {
          const nt = document.createElement("div");
          nt.className = "msg-tag";
          nt.textContent = meta.note;
          metaEl.appendChild(nt);
        }
        bubble.appendChild(metaEl);
      }

      wrap.appendChild(avatar);
      wrap.appendChild(bubble);
      messagesEl.appendChild(wrap);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    async function ask() {
      const q = inputEl.value.trim();
      if (!q) return;
      if (!backendOK) {
        appendMessage("Backend non raggiungibile. Controlla /api/config.", "bot", { mode: "error" });
        return;
      }
      const family = familyEl.value || "VCEM";

      appendMessage(q, "user");
      inputEl.value = "";
      inputEl.focus();

      try {
        const res = await fetch("/api/ask", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ q, family })
        });

        if (!res.ok) {
          appendMessage("Errore dal server (" + res.status + "). Controlla app.py o i JSON.", "bot", {
            mode: "server-error"
          });
          return;
        }

        const data = await res.json();

        if (!data.ok) {
          appendMessage(data.text || "Nessuna risposta trovata per questa domanda.", "bot", {
            id: family,
            mode: "no-match",
            lang: data.lang || "it",
            note: "Arricchire JSON famiglia " + family
          });
          return;
        }

        appendMessage(data.text || "", "bot", {
          id: data.id || family,
          mode: data.mode || "dynamic",
          lang: data.lang || "it"
        });

      } catch (err) {
        appendMessage("Errore di connessione a /api/ask.", "bot", { mode: "network-error" });
      }
    }

    sendBtn.addEventListener("click", ask);
    inputEl.addEventListener("keydown", e => { if (e.key === "Enter") ask(); });

    if (hintsEl) {
      hintsEl.addEventListener("click", e => {
        const li = e.target.closest("li");
        if (!li) return;
        inputEl.value = li.dataset.q || li.textContent.trim();
        inputEl.focus();
      });
    }

    modeGoldBtn.addEventListener("click", () => {
      modeGoldBtn.classList.add("active");
    });

    appendMessage(
      "Benvenuto in Tecnaria Sinapsi — Q/A.\nSeleziona la famiglia e fai una domanda in linguaggio naturale. Le risposte arrivano solo dai contenuti GOLD che hai caricato.",
      "bot",
      { mode: "intro", lang: "it" }
    );

    checkConfig();
  </script>
</body>
</html>
"""

# =========================================================
# UTILS TESTO / LINGUA / JSON
# =========================================================

def _normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    cleaned = []
    for ch in s:
        if ch.isalnum() or ch.isspace():
            cleaned.append(ch)
    return " ".join("".join(cleaned).split())


def _tokenize(s: str) -> List[str]:
    s = _normalize_text(s)
    if not s:
        return []
    return [t for t in s.split() if len(t) > 1]


def _read_json(path: str) -> Any:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File non trovato: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def detect_language(text: str) -> str:
    if not text:
        return "it"
    for ch in text:
        if "\u0400" <= ch <= "\u04FF":
            return "other"
        if "\u4E00" <= ch <= "\u9FFF":
            return "other"
        if "\u0600" <= ch <= "\u06FF":
            return "other"
        if "\u0590" <= ch <= "\u05FF":
            return "other"
    txt = _normalize_text(text)
    tokens = set(txt.split())
    it_words = {"che","cosa","posso","come","quando","dove","non","uso","connettori","soletta","travetto"}
    en_words = {"what","can","use","how","when","where","not","connector","slab","beam","steel"}
    fr_words = {"quoi","puis","utiliser","comment","quand","ou","non","connecteur","dalle"}
    de_words = {"was","kann","verwenden","wie","wann","wo","nicht","verbinder","platte","stahl"}
    es_words = {"que","puedo","usar","como","cuando","donde","no","conector","losa","viga"}
    scores = {
        "it": len(tokens & it_words),
        "en": len(tokens & en_words),
        "fr": len(tokens & fr_words),
        "de": len(tokens & de_words),
        "es": len(tokens & es_words),
    }
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "other"
    return best


def choose_language(q: str) -> str:
    lang = detect_language(q)
    if lang in SUPPORTED_LANGS:
        return lang
    return FALLBACK_LANG


def translate_question_to_it(q: str) -> str:
    # opzionale: se vuoi vera traduzione, usa openai_client
    if detect_language(q) == "it":
        return q
    return q  # per ora lasciamo così per non bloccare se manca API


def translate_dynamic_answer(text: str, target_lang: str) -> str:
    if target_lang == "it":
        return text
    # se vuoi traduzione reale, aggancia OpenAI sopra
    if target_lang not in SUPPORTED_LANGS:
        target_lang = FALLBACK_LANG
    return text  # fallback: niente trad, ma non rompiamo


def get_families_dir() -> str:
    cfg_path = os.path.join(DEFAULT_FAMILIES_DIR, "config.runtime.json")
    if os.path.isfile(cfg_path):
        try:
            cfg = _read_json(cfg_path)
            d = cfg.get("families_dir")
            if d:
                return d
        except Exception:
            pass
    return DEFAULT_FAMILIES_DIR


@lru_cache(maxsize=128)
def load_family(family: str) -> Dict[str, Any]:
    families_dir = get_families_dir()
    filename = f"{family}.json"
    path = os.path.join(families_dir, filename)
    data = _read_json(path)
    items = data.get("items", [])
    for item in items:
        item.setdefault("questions", [])
        item.setdefault("tags", [])
        item.setdefault("canonical", "")
        item.setdefault("response_variants", [])
        item.setdefault("mode", data.get("variants_strategy", "dynamic"))
    return data


def score_item(q_tokens: List[str], item: Dict[str, Any]) -> float:
    if not q_tokens:
        return 0.0
    bag: List[str] = []
    for qq in item.get("questions", []):
        bag.extend(_tokenize(qq))
    for tg in item.get("tags", []):
        bag.extend(_tokenize(tg))
    bag.extend(_tokenize(item.get("canonical", "")))
    if not bag:
        return 0.0
    bag_set = set(bag)
    q_set = set(q_tokens)
    inter = len(bag_set & q_set)
    if inter == 0:
        return 0.0
    score = inter / len(q_set)
    for qq in item.get("questions", []):
        qq_tokens = set(_tokenize(qq))
        if len(qq_tokens & q_set) >= max(2, int(0.6 * len(q_set))):
            score += 0.5
            break
    return score


def pick_best_item(family_data: Dict[str, Any], q: str) -> Optional[Dict[str, Any]]:
    items = family_data.get("items", [])
    if not items:
        return None
    q_tokens = _tokenize(q)
    if not q_tokens:
        return None
    best = None
    best_score = 0.0
    for item in items:
        s = score_item(q_tokens, item)
        if s > best_score:
            best_score = s
            best = item
    if best is None or best_score < 0.18:
        return None
    return best


def pick_response_text(item: Dict[str, Any]) -> str:
    mode = item.get("mode", "dynamic")
    canonical = (item.get("canonical") or "").strip()
    variants = item.get("response_variants") or []
    if mode == "canonical":
        return canonical or (variants[0].strip() if variants else "")
    if mode == "dynamic":
        pool = []
        if canonical:
            pool.append(canonical)
        pool.extend([v for v in variants if isinstance(v, str) and v.strip()])
        if not pool:
            return ""
        return random.choice(pool).strip()
    return canonical or (variants[0].strip() if variants else "")

# =========================================================
# API FASTAPI
# =========================================================

class AskPayload(BaseModel):
    q: str
    family: str
    lang: Optional[str] = None

app = FastAPI(title=APP_NAME)

static_dir = os.path.join(BASE_DIR, "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse(INDEX_HTML)

@app.get("/api/config")
def api_config():
    return {
        "ok": True,
        "app": APP_NAME,
        "families_dir": get_families_dir(),
        "supported_langs": SUPPORTED_LANGS,
        "fallback_lang": FALLBACK_LANG,
        "translation": False  # in questa versione niente OpenAI obbligatorio
    }

@app.post("/api/ask")
def api_ask(payload: AskPayload):
    original_q = (payload.q or "").strip()
    if not original_q:
        raise HTTPException(status_code=400, detail="Domanda vuota.")
    family = (payload.family or "").strip().upper()
    if not family:
        raise HTTPException(status_code=400, detail="Famiglia mancante.")

    if payload.lang:
        lang = payload.lang.lower()
        if lang not in SUPPORTED_LANGS:
            lang = FALLBACK_LANG
    else:
        lang = choose_language(original_q)

    q_for_match = original_q
    if detect_language(original_q) != "it":
        q_for_match = translate_question_to_it(original_q)

    try:
        fam = load_family(family)
    except FileNotFoundError:
        msg_it = "Nessuna base conoscitiva disponibile per questa famiglia."
        text = translate_dynamic_answer(msg_it, lang)
        return {"ok": False, "family": family, "q": original_q, "lang": lang, "text": text}

    item = pick_best_item(fam, q_for_match)
    if not item:
        msg_it = "Nessuna risposta trovata per questa domanda."
        text = translate_dynamic_answer(msg_it, lang)
        return {"ok": False, "family": family, "q": original_q, "lang": lang, "text": text}

    base_text_it = pick_response_text(item)
    if not base_text_it:
        msg_it = "Contenuto non disponibile per questa voce."
        text = translate_dynamic_answer(msg_it, lang)
        return {
            "ok": False,
            "family": family,
            "q": original_q,
            "lang": lang,
            "id": item.get("id"),
            "text": text
        }

    final_text = translate_dynamic_answer(base_text_it, lang)

    return {
        "ok": True,
        "family": family,
        "q": original_q,
        "lang": lang,
        "id": item.get("id"),
        "mode": item.get("mode", "dynamic"),
        "text": final_text
    }
