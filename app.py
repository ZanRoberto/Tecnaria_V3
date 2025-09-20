# app.py — TecnariaBot FULL v4.2
# Requisiti: OPENAI_API_KEY (opzionale), templates/index.html, static/img/wizard.js, static/data/ctf_prd.json

import os, re, json, math
from typing import Any, Dict, Optional, Tuple, List
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_cors import CORS

# ========== OpenAI opzionale ==========
try:
    from openai import OpenAI
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
    client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
except Exception:
    client = None
    OPENAI_MODEL = "gpt-4o-mini"

# ========== Flask ==========
app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# ========== Scope / denylist ==========
DENYLIST = {
    "hbv", "x-hbv", "xhbv", "hi-bond ", "hibond ", "ribdeck", "hilti shear",
    "fva", "comflor", "metsec", "holorib", "p800"   # non Tecnaria
}
TEC_PRODUCTS = {"CTF","CTL","CEME","DIAPASON","P560"}

def contains_denylist(q: str) -> bool:
    return any(d in (q or "").lower() for d in DENYLIST)

# ========== Topic / Intent ==========
def detect_topic(q: str) -> Optional[str]:
    t = (q or "").lower()
    if any(k in t for k in [" p560", "p560 ", "chiodatrice", "spit p560"]): return "P560"
    if "diapason" in t: return "DIAPASON"
    if any(k in t for k in ["cem-e", "ceme", "cem e"]): return "CEME"
    if any(k in t for k in ["ctl", "acciaio-legno", "acciaio legno", "legno"]): return "CTL"
    if any(k in t for k in ["ctf", "connettore", "connettori", "lamiera", "soletta", "gola"]): return "CTF"
    return None

def detect_intent(q: str) -> str:
    t = (q or "").lower()
    if any(k in t for k in ["altezz", "dimension", "v_l", "v l", "v_l,ed", "kn/m", "numero", "quanti", "portata", "scegliere", "che altezza", "verifica", "calcolo"]):
        return "CALC"
    if any(k in t for k in ["posa", "installazione", "fissare", "uso in cantiere", "come si posa", "istruzioni"]):
        return "POSA"
    if any(k in t for k in ["differenza", "vs", "confronto", "meglio"]):
        return "CONFRONTO"
    return "INFO"

# ========== Parsing context (wizard) ==========
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
    "h_lamiera":"Altezza lamiera (mm)",
    "s_soletta":"Spessore soletta (mm)",
    "vled":"V_L,Ed (kN/m)",
    "cls":"Classe cls",
    "passo":"Passo gola (mm)",
    "dir":"Direzione lamiera",
    "s_long":"Passo lungo trave (mm)",
    "t_lamiera":"Spessore lamiera t (mm)",
    "nr_gola":"N° connettori per gola"
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
    needed = CRITICAL_PIENA if parsed.get("piena") else CRITICAL_LAMIERA
    return [k for k in needed if k not in parsed]

# ========== DB PRd + ricerca ==========
def load_ctf_db() -> Dict[str, Any]:
    path = os.path.join(app.static_folder, "data", "ctf_prd.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

PRD_DB = load_ctf_db()

def _norm(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "").replace("-", "").replace("_","")

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
    c = (cls or "").upper().replace(" ", "")
    return [c, c.replace("C","C "), c.replace("/", " / ")]

def _is_height_key(k: str) -> bool:
    return bool(re.match(r"^ctf[_-]?\d{3}$", (k or "").lower()))

def _height_order_key(k: str) -> int:
    m = re.search(r"(\d{3})", k or "")
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
    # estrai PRd
    result = {}
    for k, v in Cnode.items():
        if _is_height_key(k):
            try: result[k.upper().replace("-", "_")] = float(v)
            except: pass
    return result or None

# ========== Fallback solid_base (Annex C1) ==========
def prd_from_solid_base(db: Dict[str, Any], cls: str, direzione: str, ctf_code: str) -> float:
    orient = "parallel" if (direzione or "").lower().startswith("long") else "perpendicular"
    try:
        return float(
            db.get("solid_base", {})
              .get(orient, {})
              .get((cls or "").upper(), {})
              .get((ctf_code or "").upper(), 0.0) or 0.0
        )
    except Exception:
        return 0.0

def _kt_from_limits(t_mm: float, nr: int) -> float:
    # clamp semplificato (rispettoso dei limiti ETA): t ↑ e nr ↑ aiutano
    if nr <= 1:
        return 1.00 if t_mm > 1.0 else 0.85
    return 0.80 if t_mm > 1.0 else 0.70

def choose_ctf_from_matrix_or_fallback(p: Dict[str, Any], safety: float = 1.10) -> Tuple[str, float, float, float, Optional[float], float, str]:
    """1) Prova PRd specifiche H/dir/passo/cls; 2) se mancano, usa solid_base (Annex C1)."""
    s_long = float(p["s_long"])
    n_per_m = 1000.0 / s_long if s_long > 0 else 0.0
    demand = float(p["vled"])

    prd_map = find_prd_table(PRD_DB, p["h_lamiera"], p["dir"], p["passo"], p["cls"])
    used_source = "tabella PRd (H/dir/passo/cls)"
    if not prd_map:
        used_source = "solid_base (Annex C1)"
        # costruisci mappa dalle base
        orient = "parallel" if p["dir"].startswith("l") else "perpendicular"
        base_cls = PRD_DB.get("solid_base", {}).get(orient, {}).get(p["cls"].upper(), {})
        prd_map = {}
        for k, v in base_cls.items():
            if _is_height_key(k):
                try: prd_map[k.upper().replace("-", "_")] = float(v or 0.0)
                except: pass

    if not prd_map:
        return ("non determinabile", n_per_m, None, demand, None, safety,
                "Database PRd non disponibile (né matrice né solid_base). Popola static/data/ctf_prd.json.")

    items = sorted(prd_map.items(), key=lambda kv: _height_order_key(kv[0]))

    apply_kt = (p.get("t_lamiera") is not None) and (p.get("nr_gola") is not None) and (not p.get("piena"))
    kt = _kt_from_limits(float(p.get("t_lamiera", 0) or 0), int(p.get("nr_gola", 0) or 0)) if apply_kt else 1.0

    best = None
    last_key, last_prd_one = None, 0.0
    for key, prd_one_raw in items:
        prd_one = prd_one_raw * (kt if apply_kt else 1.0)
        cap = prd_one * n_per_m
        last_key, last_prd_one = key, prd_one
        if cap >= demand * safety:
            best = (key, prd_one, cap)
            break

    if best:
        key, prd_one, cap = best
        util = demand / cap if cap else None
        note = (f"Fonte={used_source}; PRd/conn={prd_one:.2f} kN"
                f"{' (×k_t)' if apply_kt else ''}; n°/m={n_per_m:.2f}.")
        m = re.search(r"(\d{3})", key); h_code = m.group(1) if m else key
        return (h_code, n_per_m, cap, demand, util, safety, note)

    # Nessuna altezza soddisfa → suggerisci passo richiesto con l’ultima (più alta)
    if last_prd_one > 0:
        n_req = (demand * safety) / last_prd_one
        passo_req = 1000.0 / n_req
        msg = (f"Nessuna altezza soddisfa. Con {last_key} serve passo ≤{passo_req:.0f} mm "
               f"(Fonte={used_source}; PRd/conn={last_prd_one:.2f} kN"
               f"{' (×k_t)' if apply_kt else ''}).")
    else:
        msg = "Nessuna altezza soddisfa e PRd base assente. Verifica ctf_prd.json."
    return ("da rivedere", n_per_m, last_prd_one * n_per_m, demand, None, safety, msg)

def choose_ctf_height(p: Dict[str, Any], safety: float = 1.10):
    return choose_ctf_from_matrix_or_fallback(p, safety)

# ========== BLOCCO “C perfetta” (VERIFICATO / NON VERIFICATO) ==========
def s_long_max(prd_conn: float, demand_kNm: float):
    if prd_conn <= 0: return math.inf
    return (1000.0 * prd_conn) / demand_kNm  # mm

def render_calc_block(parsed: Dict[str, Any], result_tuple):
    (best_h, n_per_m, cap_m, demand, util, safety, note) = result_tuple

    header = (
        "<h3>CTF — Selezione altezza consigliata</h3>"
        "<ul>"
        f"<li>Lamiera: H{parsed.get('h_lamiera','—')} ({parsed.get('dir','—')}) — passo in gola {parsed.get('passo','—')} mm</li>"
        f"<li>Soletta: {parsed.get('s_soletta','—')} mm; cls: {parsed.get('cls','—')}</li>"
        f"<li>Passo lungo trave: {parsed.get('s_long','—')} mm → n°/m = {1000.0/float(parsed.get('s_long',1)):.2f}</li>"
        f"<li>t lamiera: {parsed.get('t_lamiera','—')} mm; nr in gola: {parsed.get('nr_gola','—')}</li>"
        "</ul>"
    )

    # Caso verificato
    if best_h and best_h not in ("da rivedere","non determinabile"):
        return (
            header +
            "<div style='border-left:4px solid #2ecc71;padding-left:10px;margin:8px 0'>"
            f"<p><strong>✅ VERIFICATO</strong> — Altezza consigliata: <strong>CTF {best_h}</strong>.<br>"
            f"n°/m = {n_per_m:.2f}; Capacità = <strong>{cap_m:.1f} kN/m</strong>; "
            f"Domanda = {demand:.1f} kN/m; Utilization = { (demand/cap_m)*100:.1f}%.</p>"
            f"<p><em>{note}</em></p>"
            "</div>"
        )

    # Caso NON verificato → headline + piano d’azione
    # Proviamo a ricostruire prd_conn stimato dalla coppia cap_m,n_per_m
    prd_conn = (cap_m / n_per_m) if (n_per_m and cap_m is not None) else 0.0
    smax = s_long_max(prd_conn, demand) if (prd_conn and demand) else math.inf
    gap = (demand - (cap_m or 0.0))
    over = (gap / demand * 100.0) if demand else 0.0

    why = ("<p><strong>Perché non verifica</strong><br>"
           "(Capacità/m = PRd<sub>conn</sub> × n/m = PRd<sub>conn</sub> × (1000 / s<sub>long</sub>)) "
           "→ insufficiente con s<sub>long</sub> attuale.</p>")

    plan = (
        "<ol>"
        f"<li><strong>Riduci il passo lungo trave</strong> a ≤ <strong>{smax:.0f} mm</strong> "
        f"(con <strong>CTF 135</strong> o il più prestante disponibile).</li>"
        "<li>In alternativa, aumenta la <strong>resistenza per connettore</strong>: "
        "nr/gola → 3 (se ammesso), t lamiera → 1.25 mm, cls → C35/45, orientamento più favorevole.</li>"
        "<li>Se i vincoli non lo consentono: <strong>ridistribuire i carichi</strong> / riconsiderare lo "
        "<strong>schema di connessione</strong>.</li>"
        "</ol>"
    )

    params = (
        f"<p><strong>Parametri verificati</strong><br>"
        f"Lamiera H{parsed.get('h_lamiera','—')} ({parsed.get('dir','—')}), passo in gola {parsed.get('passo','—')} mm • "
        f"Soletta {parsed.get('s_soletta','—')} mm, cls {parsed.get('cls','—')} • "
        f"s<sub>long</sub> {parsed.get('s_long','—')} mm ({n_per_m:.2f}/m) • "
        f"t {parsed.get('t_lamiera','—')} mm • nr {parsed.get('nr_gola','—')}</p>"
    )

    headline = (
        "<div style='border-left:4px solid #e74c3c;padding-left:10px;margin:8px 0'>"
        "<p><strong>❌ ESITO: NON VERIFICATO</strong> — <em>Capacità inferiore alla domanda di progetto</em><br>"
        f"<strong>Domanda</strong> {demand:.1f} kN/m • <strong>Capacità</strong> {cap_m:.1f} kN/m → "
        f"<strong>Gap</strong> {gap:.1f} kN/m (<strong>+{over:.1f}%</strong> richiesti)</p>"
    )

    notes = (
        f"<p><strong>Note di calcolo</strong><br>{note} • "
        f"s<sub>long,max</sub> = {smax:.0f} mm</p></div>"
    )

    return header + headline + why + "<h4>Piano d’azione (priorità)</h4>" + plan + params + notes

# ========== Allegati / Note tecniche ==========
def tool_attachments(topic: str, intent: str):
    out = []
    if topic == "P560":
        out.append({"label":"Foto P560","href":"/static/img/p560_magazzino.jpg"})
    if topic == "CTF" and intent == "POSA":
        out.append({"label":"Nota posa CTF (PDF)","href":"/static/docs/ctf_posa.pdf"})
    if topic == "CTF" and intent == "INFO":
        out.append({"label":"Scheda CTF (PDF)","href":"/static/docs/ctf_scheda.pdf"})
    # .txt mostrati inline sul frontend (modale testo)
    return out

# ========== LLM (A/B/C) ==========
SYSTEM_BASE = (
    "Sei TecnariaBot, assistente tecnico di Tecnaria S.p.A. (Bassano del Grappa). "
    "Rispondi in italiano, solo su prodotti/servizi Tecnaria (CTF, CTL, CEM-E, Diapason, P560). "
    "Se la domanda è fuori scope, spiega gentilmente che il bot è dedicato ai prodotti Tecnaria. "
    "Tono professionale, niente marketing vuoto, niente inventare norme o valori non forniti."
)

def build_style_block(mode: str) -> str:
    mode = (mode or "").lower()
    if mode == "breve":
        return ("Stile=A (breve). 90–130 parole, chiaro, senza formule, senza numeri non necessari. "
                "Chiudi con una raccomandazione pratica.")
    if mode == "standard":
        return ("Stile=B (standard). 180–260 parole, discorsivo tecnico, 1–2 elenchi brevi ammessi, "
                "cita principi (ETA/EC4) senza dettagli normativi puntuali.")
    return ("Stile=C (dettagliata). 380–600 parole. HTML strutturato con sezioni: "
            "<h3>Cos’è</h3>, <h4>Componenti</h4>, <h4>Varianti</h4>, <h4>Prestazioni</h4>, "
            "<h4>Posa</h4>, <h4>Norme e riferimenti</h4>, <h4>Vantaggi e limiti</h4>. "
            "Niente fluff; non inventare valori; resta nello scope Tecnaria.")

def llm_reply(topic: str, intent: str, mode: str, question: str, context: str) -> str:
    if not client:
        return "Configura OPENAI_API_KEY per ottenere risposte A/B/C avanzate."
    style = build_style_block(mode)
    guard = ("Se il contenuto richiesto non è relativo a Tecnaria "
             "(CTF/CTL/CEM-E/Diapason/P560), rispondi: "
             "'Assistente dedicato ai prodotti e servizi Tecnaria S.p.A.'")
    topic_hint = f"Topic prodotto: {topic}. Intent: {intent}."
    ctx = f"Contesto aggiuntivo: {context}" if context else "Nessun contesto aggiuntivo."
    prompt = (f"{topic_hint}\n{ctx}\n\nObiettivo: risposta proporzionata allo Stile richiesto.\n"
              f"{style}\n{guard}\nScrivi in italiano.")
    messages = [
        {"role":"system","content": SYSTEM_BASE},
        {"role":"user","content": f"Domanda: {question}\n{prompt}"}
    ]
    resp = client.chat.completions.create(
        model=OPENAI_MODEL, messages=messages,
        temperature=0.2, top_p=0.9, max_tokens=900
    )
    return resp.choices[0].message.content.strip()

def product_info_llm(topic: str, intent: str, mode: str, question: str, context: str) -> str:
    try:
        return llm_reply(topic, intent, mode, question, context)
    except Exception:
        # Fallback locali sobri
        if topic == "P560":
            if mode == "breve":
                return ("P560: chiodatrice a polvere per posa connettori Tecnaria su travi/lamiera; "
                        "uso con DPI, scelta cartucce adeguata, controlli 3.5–7.5 mm.")
            if mode == "standard":
                return ("La P560 fissa i connettori su acciaio/lamiera; richiede regolazione potenza, "
                        "bending test iniziale, e manutenzione periodica.")
            return ("<h3>P560</h3><h4>Impiego</h4><p>Fissaggio connettori Tecnaria.</p>"
                    "<h4>Controlli</h4><p>Quota testa-chiodo 3.5–7.5 mm; bending test; DPI.</p>")
        if topic == "CTF":
            if mode == "breve":
                return ("CTF: connettori a taglio per solai collaboranti acciaio–calcestruzzo; "
                        "dimensionamento da PRd (ETA) e verifica EC4.")
            if mode == "standard":
                return ("CTF: selezione in funzione di lamiera/direzione/passi/cls; verifica con PRd ETA e passo lungo trave; "
                        "posa con P560; riferimenti EC4/ETA.")
            return ("<h3>CTF — scheda</h3><p>Consulta ETA-18/0447 e manuale Tecnaria per valori e posa.</p>")
        if topic == "CTL":
            return "CTL: connettori per sistemi legno–calcestruzzo; verifica EC5/EC4; posa con viti/staffe."
        if topic == "CEME":
            return "CEM-E: collegamento cls esistente/nuovo con resine; foratura e pulizia conformi a ETA."
        if topic == "DIAPASON":
            return "Diapason: lamiera per riqualifica solai; posa con chiodi/ancoranti; verifiche a taglio."
        return "Assistente dedicato ai prodotti Tecnaria S.p.A."

# ========== Routes ==========
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/answer", methods=["POST"])
def api_answer():
    data = request.get_json(force=True) or {}
    question = (data.get("question") or "").strip()
    mode     = (data.get("mode") or "dettagliata").strip().lower()
    context  = (data.get("context") or "").strip()

    if contains_denylist(question):
        return jsonify({"answer":"Assistente dedicato a prodotti Tecnaria S.p.A.",
                        "meta":{"needs_params":False,"required_keys":[]}})

    topic  = detect_topic(question)
    intent = detect_intent(question)

    if topic is None:
        return jsonify({"answer":"Assistente dedicato ai prodotti Tecnaria (CTF/CTL/CEM-E/Diapason/P560).",
                        "meta":{"needs_params":False,"required_keys":[]}})

    # === CTF: ramo calcolo ===
    if topic == "CTF" and intent == "CALC":
        parsed = parse_ctf_context(context)
        miss = missing_ctf_keys(parsed)
        if miss:
            labels = [UI_LABELS[k] for k in miss]
            return jsonify({"answer":"Per procedere servono: " + ", ".join(labels),
                            "meta":{"needs_params":True,"required_keys":labels}})
        result = choose_ctf_height(parsed)
        answer = render_calc_block(parsed, result)
        return jsonify({"answer":answer, "meta":{"needs_params":False}, "attachments":tool_attachments(topic,intent)})

    # === INFO / POSA / CONFRONTO → LLM ===
    prose = product_info_llm(topic, intent, mode, question, context)

    # Modalità ibrida: se i dati CTF sono completi, append calcolo “C perfetta”
    extra = ""
    if topic == "CTF":
        parsed = parse_ctf_context(context)
        miss = missing_ctf_keys(parsed)
        if not miss:
            result = choose_ctf_height(parsed)
            extra = "<hr>" + render_calc_block(parsed, result)
        elif any(k in parsed for k in ("h_lamiera","s_soletta","vled","passo","s_long","t_lamiera","nr_gola")):
            labels = [UI_LABELS[k] for k in miss]
            extra = f"<hr><p><em>Dati calcolo incompleti:</em> mancano {', '.join(labels)}.</p>"

    return jsonify({"answer": prose + extra, "meta":{"needs_params":False}, "attachments":tool_attachments(topic,intent)})

# Static file proxy (utile su alcuni hosting)
@app.route("/static/<path:path>")
def static_proxy(path):
    return send_from_directory("static", path)

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    # run locale
    app.run(host="0.0.0.0", port=8000, debug=True)
