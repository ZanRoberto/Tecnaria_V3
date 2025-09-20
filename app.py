# app.py — TecnariaBot FULL v4.0 (A/B/C proporzionate via LLM + CTF calcolo da matrice)
# Requisiti: OPENAI_API_KEY, templates/index.html, static/img/wizard.js, static/data/ctf_prd.json

import json, os, re
from typing import Any, Dict, Optional, Tuple, List
from flask import Flask, render_template, request, jsonify

# =========================
# OpenAI client (ChatGPT)
# =========================
from openai import OpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

app = Flask(__name__, static_folder="static", template_folder="templates")

# =========================================
# 0) Scope / denylist
# =========================================
DENYLIST = {
    "hbv", "x-hbv", "xhbv", "fva", "hi-bond ", "hibond ", "ribdeck", "hilti shear", "p800"
}
TEC_PRODUCTS = {"CTF","CTL","CEME","DIAPASON","P560"}

# =========================================
# 1) Topic / Intent detection
# =========================================
def detect_topic(q: str) -> Optional[str]:
    t = q.lower()
    if any(k in t for k in [" p560", "p560 ", "chiodatrice", "spit p560"]): return "P560"
    if "diapason" in t: return "DIAPASON"
    if any(k in t for k in ["cem-e", "ceme", "cem e"]): return "CEME"
    if any(k in t for k in ["ctl", "acciaio-legno", "acciaio legno", "legno"]): return "CTL"
    if any(k in t for k in ["ctf", "connettore", "connettori", "lamiera", "soletta", "gola"]): return "CTF"
    return None

def detect_intent(q: str) -> str:
    t = q.lower()
    if any(k in t for k in ["altezz", "dimension", "v_l", "v l", "v_l,ed", "kn/m", "numero", "quanti", "portata", "scegliere", "che altezza"]):
        return "CALC"
    if any(k in t for k in ["posa", "installazione", "fissare", "uso in cantiere", "come si posa", "istruzioni"]):
        return "POSA"
    if any(k in t for k in ["differenza", "vs", "confronto", "meglio"]):
        return "CONFRONTO"
    return "INFO"

def contains_denylist(q: str) -> bool:
    return any(d in q.lower() for d in DENYLIST)

# =========================================
# 2) Parsing context (wizard)
# =========================================
CTX_RE = {
    "h_lamiera": re.compile(r"lamiera\s*h?\s*(\d+)", re.I),
    "s_soletta": re.compile(r"soletta\s*(\d+)\s*mm", re.I),
    "vled":      re.compile(r"v[\s_.,-]*l\s*,?ed\s*=\s*([\d.,]+)\s*kn/?m", re.I),
    "cls":       re.compile(r"cls\s*([Cc]\d+\/\d+)", re.I),
    "passo":     re.compile(r"passo\s*gola\s*(\d+)\s*mm", re.I),
    "dir":       re.compile(r"lamiera\s*(longitudinale|trasversale)", re.I),
    "s_long":    re.compile(r"passo\s+lungo\s+trave\s*(\d+)\s*mm", re.I),
    "piena":     re.compile(r"soletta\s+piena", re.I),
    "t_lamiera": re.compile(r"t\s*=\s*([\d.,]+)\s*mm", re.I),
    "nr_gola":   re.compile(r"nr\s*=\s*(\d+)", re.I),
}
UI_LABELS = {
    "h_lamiera":"altezza lamiera",
    "s_soletta":"spessore soletta",
    "vled":"V_L,Ed",
    "cls":"cls",
    "passo":"passo gola",
    "dir":"direzione lamiera",
    "s_long":"passo lungo trave",
    "t_lamiera":"spessore lamiera t",
    "nr_gola":"n° connettori per gola"
}
CRITICAL_LAMIERA = ["h_lamiera","s_soletta","vled","cls","passo","dir","s_long","t_lamiera","nr_gola"]
CRITICAL_PIENA   = ["s_soletta","vled","cls","s_long"]

def parse_ctf_context(ctx: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not ctx: return out
    def f(k, cast=None, repl=False):
        m = CTX_RE[k].search(ctx)
        if not m: return None
        v = m.group(1)
        if repl: v = v.replace(",", ".")
        if cast:
            try: return cast(v)
            except: return None
        return v
    out["h_lamiera"] = f("h_lamiera", int)
    out["s_soletta"] = f("s_soletta", int)
    out["vled"]      = f("vled", float, repl=True)
    cls = f("cls"); out["cls"] = cls.upper() if cls else None
    out["passo"]     = f("passo", int)
    dirn = f("dir"); out["dir"] = dirn.lower() if dirn else None
    out["s_long"]    = f("s_long", int)
    out["piena"]     = True if CTX_RE["piena"].search(ctx) else False
    out["t_lamiera"] = f("t_lamiera", float, repl=True)
    out["nr_gola"]   = f("nr_gola", int)
    return {k:v for k,v in out.items() if v is not None}

def missing_ctf_keys(parsed: Dict[str, Any]) -> List[str]:
    if parsed.get("piena"):
        needed = CRITICAL_PIENA
    else:
        needed = CRITICAL_LAMIERA
    return [k for k in needed if k not in parsed]

# =========================================
# 3) DB PRd (matrice ricca) + fallback (P0×k_t) + calcolo CTF
# =========================================
def load_ctf_db() -> Dict[str, Any]:
    path = os.path.join(app.static_folder, "data", "ctf_prd.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

PRD_DB = load_ctf_db()

def _kt_from_limits(t_mm: float, nr: int) -> float:
    if nr <= 1:
        return 1.00 if t_mm > 1.0 else 0.85
    return 0.80 if t_mm > 1.0 else 0.70

def _norm(s: str) -> str:
    return s.strip().lower().replace(" ", "").replace("-", "").replace("_","")

def _dir_key(d: str) -> List[str]:
    d = _norm(d)
    if d.startswith("long"): return ["longitudinale","long","parallel","parallela","||","l"]
    if d.startswith("tras") or d.startswith("perp"): return ["trasversale","perpendicolare","perp","⊥","t"]
    return [d]

def _passo_keys(passo: int) -> List[str]:
    p = str(passo)
    return [f"passo_{p}", f"passogola_{p}", f"gola_{p}", p]

def _h_keys(h: int) -> List[str]:
    hstr = f"h{h}"
    return [hstr, hstr.upper(), str(h), f"H{h}"]

def _cls_keys(cls: str) -> List[str]:
    c = cls.upper().replace(" ", "")
    return [c, c.replace("C","C "), c.replace("/", " / ")]

def _is_height_key(k: str) -> bool:
    return bool(re.match(r"^ctf[_-]?\d{3}$", k.lower()))

def _height_order_key(k: str) -> int:
    m = re.search(r"(\d{3})", k)
    return int(m.group(1)) if m else 999

def find_prd_table(db: Dict[str, Any], h_lamiera: int, dir_lam: str, passo_gola: int, cls: str) -> Optional[Dict[str, float]]:
    if not all([h_lamiera, dir_lam, passo_gola, cls]): return None
    # livello H
    Hnode = None
    for hk in db.keys():
        if _norm(hk) in map(_norm, _h_keys(h_lamiera)):
            Hnode = db[hk]; break
    if not isinstance(Hnode, dict): return None
    # livello direzione
    Dnode = None
    for dk in Hnode.keys():
        if _norm(dk) in map(_norm, _dir_key(dir_lam)):
            Dnode = Hnode[dk]; break
    if not isinstance(Dnode, dict): return None
    # livello passo (opzionale)
    Pnode = None
    for pk in Dnode.keys():
        if _norm(pk) in map(_norm, _passo_keys(passo_gola)):
            Pnode = Dnode[pk]; break
    if Pnode is None: Pnode = Dnode
    if not isinstance(Pnode, dict): return None
    # livello classe
    Cnode = None
    for ck in Pnode.keys():
        if _norm(ck) in map(_norm, _cls_keys(cls)):
            Cnode = Pnode[ck]; break
    if not isinstance(Cnode, dict): return None
    # estrai coppie CTF_xxx: PRd
    result = {}
    for k, v in Cnode.items():
        if _is_height_key(k):
            try:
                result[k.upper().replace("-", "_")] = float(v)
            except:
                continue
    return result or None

def choose_ctf_from_matrix(p: Dict[str, Any], safety: float = 1.10) -> Tuple[str, float, float, float, Optional[float], float, str]:
    prd_map = find_prd_table(PRD_DB, p["h_lamiera"], p["dir"], p["passo"], p["cls"])
    if not prd_map:
        return ("tabella mancante", 0.0, None, float(p["vled"]), None, safety,
                "Tabella PRd non trovata per H{h}, {d}, passo {pg} mm, cls {c}.".format(
                    h=p.get("h_lamiera","—"), d=p.get("dir","—"), pg=p.get("passo","—"), c=p.get("cls","—")
                ))
    s_long = float(p["s_long"])
    n_per_m = 1000.0 / s_long if s_long > 0 else 0.0
    demand = float(p["vled"])
    items = sorted(prd_map.items(), key=lambda kv: _height_order_key(kv[0]))
    best = None
    for key, prd_one in items:
        cap = prd_one * n_per_m
        if cap >= demand * safety:
            best = (key, prd_one, cap)
            break
    if best:
        key, prd_one, cap = best
        util = demand / cap if cap else None
        note = f"PRd/conn={prd_one:.2f} kN; n°/m={n_per_m:.2f}."
        m = re.search(r"(\d{3})", key); h_code = m.group(1) if m else key
        return (h_code, n_per_m, cap, demand, util, safety, note)
    key, prd_one = items[-1]
    n_req = (demand * safety) / prd_one if prd_one > 0 else None
    passo_req = 1000.0 / n_req if n_req else None
    msg = (f"Nessuna altezza soddisfa. Con {key} serve passo ≤{passo_req:.0f} mm "
           f"(PRd/conn={prd_one:.2f} kN).")
    return ("da rivedere", 1000.0/float(p["s_long"]), prd_one*(1000.0/float(p["s_long"])), demand, None, safety, msg)

def choose_ctf_from_rule(p: Dict[str, Any], safety: float = 1.10) -> Tuple[str, float, float, float, Optional[float], float, str]:
    rule = PRD_DB.get("lamiera_rule", {})
    P0 = (rule.get("P0", {}) or {}).get(p.get("cls"))
    t_mm = float(p.get("t_lamiera", 0) or 0)
    nr   = int(p.get("nr_gola", 0) or 0)
    s_long = float(p["s_long"])
    n_per_m = 1000.0 / s_long if s_long > 0 else 0.0
    demand = float(p["vled"])
    if not P0 or P0 <= 0:
        return ("non determinabile", n_per_m, None, demand, None, safety,
                f"Manca P0 per {p.get('cls')} nel database (static/data/ctf_prd.json).")
    if t_mm <= 0 or nr <= 0:
        return ("parametri mancanti", n_per_m, None, demand, None, safety,
                "Servono spessore lamiera t (mm) e nr connettori per gola (nr).")
    kt = _kt_from_limits(t_mm, nr)
    prd_one = P0 * kt
    cap = prd_one * n_per_m
    util = demand / cap if cap else None
    if cap >= demand * safety:
        return ("80", n_per_m, cap, demand, util, safety, f"P0={P0} kN, k_t={kt:.2f}, PRd/conn={prd_one:.2f} kN.")
    else:
        n_req = (demand * safety) / prd_one if prd_one > 0 else None
        passo_req = 1000.0 / n_req if n_req else None
        return ("da rivedere", n_per_m, cap, demand, util, safety,
                f"Capacità {cap:.1f} < richiesta {demand*safety:.1f}. Riduci passo ≤{passo_req:.0f} mm.")

def choose_ctf_height(p: Dict[str, Any], safety: float = 1.10):
    try:
        return choose_ctf_from_matrix(p, safety)
    except Exception:
        pass
    return choose_ctf_from_rule(p, safety)

# =========================================
# 4) LLM: risposte A/B/C proporzionate
# =========================================
SYSTEM_BASE = (
    "Sei TecnariaBot, assistente tecnico di Tecnaria S.p.A. (Bassano del Grappa). "
    "Rispondi in italiano, solo su prodotti/servizi Tecnaria (CTF, CTL, CEM-E, Diapason, P560). "
    "Se la domanda è fuori scope, spiega gentilmente che il bot è dedicato ai prodotti Tecnaria. "
    "Tono professionale, niente marketing vuoto, niente inventare norme o valori non forniti."
)

def build_style_block(mode: str) -> str:
    mode = (mode or "").lower()
    if mode == "breve":
        return (
            "Stile=A (breve). 90–130 parole, chiaro, nessun elenco puntato se non strettamente utile, "
            "nessuna formula, nessun numero specifico se non inevitabile. Conclusione sintetica."
        )
    if mode == "standard":
        return (
            "Stile=B (standard). 180–260 parole, discorsivo tecnico, 1–2 elenchi brevi ammessi, "
            "niente formule pesanti; cita principi (ETA/EC4) senza dettagliare paragrafi."
        )
    # dettagliata
    return (
        "Stile=C (dettagliata). 380–600 parole. Restituisci HTML strutturato con sezioni: "
        "<h3>Cos’è</h3>, <h4>Componenti</h4>, <h4>Varianti</h4>, <h4>Prestazioni</h4>, "
        "<h4>Posa</h4>, <h4>Norme e riferimenti</h4>, <h4>Vantaggi e limiti</h4>. "
        "Niente fluff, niente dati inventati; non usare formule lunghe, usa termini chiari. "
        "Non uscire dallo scope Tecnaria."
    )

def llm_reply(topic: str, intent: str, mode: str, question: str, context: str) -> str:
    if not client:
        # Fallback se manca la chiave API
        return "Configurare OPENAI_API_KEY per ottenere risposte A/B/C avanzate."
    style = build_style_block(mode)
    # vincoli scope
    guard = (
        "Se il contenuto richiesto non è relativo a Tecnaria (CTF/CTL/CEM-E/Diapason/P560), rispondi: "
        "'Assistente dedicato ai prodotti e servizi Tecnaria S.p.A.'"
    )
    # hint per il topic
    topic_hint = f"Topic prodotto: {topic}. Intent: {intent}."
    # context opzionale
    ctx = f"Contesto aggiuntivo: {context}" if context else "Nessun contesto aggiuntivo."
    prompt = (
        f"{topic_hint}\n{ctx}\n\n"
        "Obiettivo: fornisci una risposta proporzionata allo Stile richiesto.\n"
        f"{style}\n{guard}\n"
        "Scrivi in italiano."
    )
    messages = [
        {"role":"system","content": SYSTEM_BASE},
        {"role":"user","content": f"Domanda: {question}\n{prompt}"}
    ]
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
        temperature=0.2,
        top_p=0.9,
        max_tokens=900
    )
    return resp.choices[0].message.content.strip()

# Risposte prodotto “INFO/CONFRONTO/POSA” (default via LLM, con fallback locale)
def product_info_llm(topic: str, intent: str, mode: str, question: str, context: str) -> str:
    try:
        return llm_reply(topic, intent, mode, question, context)
    except Exception:
        # Fallback minimale locale
        if topic == "P560":
            return ("P560 — chiodatrice a polvere per posa connettori Tecnaria. "
                    "Uso con consumabili idonei e DPI; vedere manuale Tecnaria/Spit.")
        if topic == "CTF":
            if mode == "breve":
                return ("I CTF sono connettori a taglio per solai collaboranti acciaio–calcestruzzo; "
                        "permettono la collaborazione tra lamiera/trave e soletta in cls.")
            if mode == "standard":
                return ("CTF: connettori per acciaio–cls; scelta in funzione di lamiera/direzione/passi/cls; "
                        "posa con P560; verifica secondo tabelle PRd ETA.")
            return ("<h3>CTF — scheda</h3><p>Consulta ETA-18/0447 e manuale Tecnaria per dettagli completi.</p>")
        if topic == "CTL":
            return "CTL: connettori per sistemi legno–calcestruzzo; verifica EC5/EC4; posa con viti/staffe."
        if topic == "CEME":
            return "CEM-E: collegamento cls esistente/nuovo con resine; foratura e pulizia conformi a ETA."
        if topic == "DIAPASON":
            return "Diapason: lamiera per riqualifica solai; posa con chiodi/ancoranti; verifiche a taglio."
        return "Assistente dedicato ai prodotti Tecnaria S.p.A."

# =========================================
# 5) Allegati
# =========================================
def tool_attachments(topic: str, intent: str):
    out = []
    if topic == "P560":
        out.append({"label":"Foto P560","href":"/static/img/p560_magazzino.jpg"})
    if topic == "CTF" and intent == "POSA":
        out.append({"label":"Nota posa CTF (PDF)","href":"/static/docs/ctf_posa.pdf"})
    if topic == "CTF" and intent == "INFO":
        out.append({"label":"Scheda CTF (PDF)","href":"/static/docs/ctf_scheda.pdf"})
    return out

# =========================================
# 6) Routes
# =========================================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/answer", methods=["POST"])
def api_answer():
    data = request.get_json(force=True) or {}
    question = (data.get("question") or "").strip()
    mode = (data.get("mode") or "dettagliata").strip().lower()
    context = (data.get("context") or "").strip()

    if contains_denylist(question):
        return jsonify({"answer":"Assistente dedicato a prodotti Tecnaria S.p.A.","meta":{"needs_params":False,"required_keys":[]}})

    topic = detect_topic(question)
    intent = detect_intent(question)

    if topic is None:
        return jsonify({"answer":"Assistente dedicato ai prodotti Tecnaria (CTF/CTL/CEM-E/Diapason/P560).",
                        "meta":{"needs_params":False,"required_keys":[]}})

    # CTF — intent di calcolo usa la matrice PRd
    if topic == "CTF" and intent == "CALC":
        parsed = parse_ctf_context(context)
        miss = missing_ctf_keys(parsed)
        if miss:
            labels = [UI_LABELS[k] for k in miss]
            return jsonify({"answer":"Per procedere servono: " + ", ".join(labels),
                            "meta":{"needs_params":True,"required_keys":labels}})
        h, npm, capm, dem, util, safety, note = choose_ctf_height(parsed)
        ans = (
            f"<h3>CTF — Selezione altezza consigliata</h3>"
            f"<ul>"
            f"<li>Lamiera: H{parsed.get('h_lamiera','—')} ({parsed.get('dir','—')}) — passo in gola {parsed.get('passo','—')} mm</li>"
            f"<li>Soletta: {parsed.get('s_soletta','—')} mm; cls: {parsed.get('cls','—')}</li>"
            f"<li>Passo lungo trave: {parsed.get('s_long','—')} mm → n°/m = {1000.0/float(parsed.get('s_long',1)):.2f}</li>"
            f"<li>t lamiera: {parsed.get('t_lamiera','—')} mm; nr in gola: {parsed.get('nr_gola','—')}</li>"
            f"</ul>"
            f"<p><strong>Esito:</strong> CTF <strong>{h}</strong>. <em>{note or ''}</em></p>"
        )
        return jsonify({"answer":ans,"meta":{"needs_params":False,"required_keys":[]},
                        "attachments":tool_attachments(topic,intent)})

    # Tutto il resto (INFO/CONFRONTO/POSA) va a LLM con stile A/B/C
    answer = product_info_llm(topic, intent, mode, question, context)
    return jsonify({"answer":answer,"meta":{"needs_params":False,"required_keys":[]},
                    "attachments":tool_attachments(topic,intent)})

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
