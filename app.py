# app.py — TecnariaBot FULL v3.0 (CTF da tabella completa + fallback P0×k_t)
# Compatibile con: templates/index.html + static/img/wizard.js
# Dati CTF: static/data/ctf_prd.json (usa la tua matrice ricca; fallback P0×k_t se assente)

import json, os, re
from typing import Any, Dict, Optional, Tuple, List
from flask import Flask, render_template, request, jsonify

# Istanza Flask globale → Gunicorn deve trovare app qui
app = Flask(__name__, static_folder="static", template_folder="templates")

# =========================================
# 0) Scope / denylist
# =========================================
DENYLIST = {
    "hbv", "x-hbv", "xhbv", "fva", "hi-bond ", "hibond ", "ribdeck", "hilti shear", "p800"
}

# =========================================
# 1) Topic / Intent detection
# =========================================
def detect_topic(q: str) -> Optional[str]:
    t = q.lower()
    if any(k in t for k in [" p560", "p560 ", "chiodatrice", "spit p560"]): return "P560"
    if "diapason" in t: return "DIAPASON"
    if any(k in t for k in ["cem-e", "ceme", "cem e"]): return "CEME"
    if any(k in t for k in ["ctl", "acciaio-legno", "acciaio legno"]): return "CTL"
    if any(k in t for k in ["ctf", "connettore", "connettori", "lamiera", "soletta", "gola"]): return "CTF"
    return None

def detect_intent(q: str) -> str:
    t = q.lower()
    if any(k in t for k in ["altezz", "dimension", "v_l", "v l", "v_l,ed", "kn/m", "numero", "quanti", "portata", "scegliere"]):
        return "CALC"
    if any(k in t for k in ["posa", "installazione", "fissare", "uso in cantiere"]):
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
# 3) DB PRd (rich) + fallback (P0×k_t) + calcolo CTF
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
    # Soglie generiche; adegua se diverso nelle tue tabelle
    if nr <= 1:
        return 1.00 if t_mm > 1.0 else 0.85
    return 0.80 if t_mm > 1.0 else 0.70

# ---------- helper: normalizzazione chiavi ----------
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

# ---------- lookup nella matrice “ricca” ----------
def find_prd_table(db: Dict[str, Any], h_lamiera: int, dir_lam: str, passo_gola: int, cls: str) -> Optional[Dict[str, float]]:
    """
    Ritorna un dict: { 'CTF_020': PRd, 'CTF_030': PRd, ... } per la combinazione richiesta.
    Accetta varianti nei nomi chiave.
    """
    if not all([h_lamiera, dir_lam, passo_gola, cls]): return None
    # 1) livello H
    H_candidates = _h_keys(h_lamiera)
    Hnode = None
    for hk in db.keys():
        if _norm(hk) in map(_norm, H_candidates):
            Hnode = db[hk]; break
    if not isinstance(Hnode, dict):
        return None  # non c'è la famiglia Hxx

    # 2) livello direzione
    Dnode = None
    for dk in Hnode.keys():
        if _norm(dk) in map(_norm, _dir_key(dir_lam)):
            Dnode = Hnode[dk]; break
    if not isinstance(Dnode, dict):
        return None

    # 3) livello passo
    Pnode = None
    for pk in Dnode.keys():
        if _norm(pk) in map(_norm, _passo_keys(passo_gola)):
            Pnode = Dnode[pk]; break
    # In alcune strutture il livello "passo" può mancare (valori unici per quella direzione)
    if Pnode is None:
        Pnode = Dnode
    if not isinstance(Pnode, dict):
        return None

    # 4) livello classe
    Cnode = None
    for ck in Pnode.keys():
        if _norm(ck) in map(_norm, _cls_keys(cls)):
            Cnode = Pnode[ck]; break
    if not isinstance(Cnode, dict):
        return None

    # 5) estrai coppie CTF_xxx: PRd
    result = {}
    for k, v in Cnode.items():
        if _is_height_key(k):
            try:
                result[k.upper().replace("-", "_")] = float(v)
            except:
                continue
    return result or None

# ---------- calcolo da matrice ----------
def choose_ctf_from_matrix(p: Dict[str, Any], safety: float = 1.10) -> Tuple[str, float, float, float, Optional[float], float, str]:
    prd_map = find_prd_table(PRD_DB, p["h_lamiera"], p["dir"], p["passo"], p["cls"])
    if not prd_map:
        return ("tabella mancante", 0.0, None, float(p["vled"]), None, safety,
                "Tabella PRd non trovata per H{h}, {d}, passo {pg} mm, cls {c}.".format(
                    h=p.get("h_lamiera","—"), d=p.get("dir","—"), pg=p.get("passo","—"), c=p.get("cls","—")
                ))
    # n° connettori per metro (dal passo lungo trave)
    s_long = float(p["s_long"])
    n_per_m = 1000.0 / s_long if s_long > 0 else 0.0
    demand = float(p["vled"])

    # ordina altezze crescenti
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
        # normalizza altezza "CTF_080" -> "080"
        m = re.search(r"(\d{3})", key)
        h_code = m.group(1) if m else key
        return (h_code, n_per_m, cap, demand, util, safety, note)

    # nessuna altezza soddisfa → suggerisci passo
    # prendi l'ultima (max) per dire di stringere il passo
    key, prd_one = items[-1]
    n_req = (demand * safety) / prd_one if prd_one > 0 else None
    passo_req = 1000.0 / n_req if n_req else None
    msg = (f"Nessuna altezza soddisfa la richiesta. Con {key} serve passo ≤{passo_req:.0f} mm "
           f"(PRd/conn={prd_one:.2f} kN).")
    return ("da rivedere", n_per_m, prd_one*n_per_m, demand, None, safety, msg)

# ---------- fallback P0×k_t ----------
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
                f"Manca P0 per la classe {p.get('cls')} nel database (static/data/ctf_prd.json).")
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

# ---------- API “unica” per CTF ----------
def choose_ctf_height(p: Dict[str, Any], safety: float = 1.10):
    """
    1) prova con la matrice ricca (H/dir/passo/cls → CTF_xxx:PRd)
    2) se non disponibile, fallback su P0×k_t (lamiera_rule)
    """
    # prova matrice
    try:
        return choose_ctf_from_matrix(p, safety)
    except Exception as e:
        # continua al fallback
        pass
    # fallback
    return choose_ctf_from_rule(p, safety)

# =========================================
# 4) Risposte A/B/C (INFO + POSA + P560)
# =========================================
def p560_answer(mode: str) -> str:
    if mode == "breve":
        return ("P560 è la chiodatrice a polvere usata per fissare i connettori Tecnaria in modo rapido e controllato. "
                "Pensata per travi in acciaio e calcestruzzo, con consumabili dedicati e procedure di sicurezza precise.")
    if mode == "standard":
        return ("La P560 è una chiodatrice a polvere professionale per la posa dei connettori su travi in acciaio e calcestruzzo. "
                "Si utilizza con chiodi e cartucce idonei; richiede messa a punto (pressione, appoggio ortogonale, prove). "
                "In cantiere: controlli di tenuta e passi; DPI obbligatori; manutenzione periodica.")
    return """
    <h3>P560 — Scheda operativa per posa connettori</h3>
    <h4>Campo d’impiego</h4>
    <ul>
      <li>Fissaggio dei connettori Tecnaria su <strong>travi in acciaio</strong> e su <strong>calcestruzzo</strong> (dove previsto), in sistemi di solaio collaborante.</li>
      <li>Uso con <strong>consumabili dedicati</strong> (chiodi idonei al supporto e cartucce adeguate).</li>
    </ul>
    <h4>Set-up utensile e consumabili</h4>
    <ul>
      <li>Verifica utensile, guida, puntale; prove su scarti (2–3 tiri).</li>
      <li>Scelta cartuccia per supporto; chiodi conformi al supporto e al connettore.</li>
      <li>Appoggio ortogonale e pressione costante al tiro.</li>
    </ul>
    <h4>Procedura</h4>
    <ol>
      <li>Tracciare le posizioni (passi/distanze).</li>
      <li>Tiro singolo senza rotazioni; controllo profondità e assenza difetti.</li>
    </ol>
    <h4>Controlli & Sicurezza</h4>
    <ul>
      <li>Distanze dai bordi, interassi, tolleranze; campione di tiri se cambia supporto/lotti.</li>
      <li>DPI obbligatori; area delimitata; stoccaggio cartucce sicuro.</li>
    </ul>
    <h4>Manutenzione</h4>
    <ul>
      <li>Pulizia puntale/guida/camera; sostituzioni periodiche componenti usurati.</li>
    </ul>
    """.strip()

def ctf_answer_info(mode: str) -> str:
    if mode == "breve":
        return "CTF: connettori per solai collaboranti acciaio-calcestruzzo, certificati ETA."
    if mode == "standard":
        return "CTF: pioli per solai collaboranti; verifica con PRd di tabella o regola P0×k_t; posa con P560."
    return ("CTF — guida tecnica: impiego su lamiera grecata/soletta piena; verifica EC4 (capacità ≥ domanda×γ); "
            "uso di tabelle PRd (per H/dir/passo/cls) oppure P0×k_t; posa con P560; riferimenti ETA-18/0447.")

def ctl_answer_info(mode: str) -> str:
    if mode == "breve": return "CTL: connettori per legno-calcestruzzo."
    if mode == "standard": return "CTL: impiego in sistemi legno-cls/acciaio-legno; verifica EC5/EC4; posa con viti/staffe dedicate."
    return "CTL — scheda tecnica: specie legno, spessori, dettagli costruttivi, verifiche e posa."

def ceme_answer_info(mode: str) -> str:
    if mode == "breve": return "CEM-E: connessione tra cls esistente e nuovo getto."
    if mode == "standard": return "CEM-E: foratura + resina; verifiche ETA; controllo estrazione; posa secondo manuale."
    return "CEM-E — scheda tecnica: parametri di adesione, profondità foro, pulizia, resina, controlli."

def diapason_answer_info(mode: str) -> str:
    if mode == "breve": return "Diapason: rinforzo e riqualifica solai esistenti."
    if mode == "standard": return "Diapason: lamiera sagomata; posa con chiodi/ancoranti; verifiche taglio."
    return "Diapason — scheda tecnica: geometria, armature, calcoli di taglio, posa e DPI."

def tpl_ctf_calc(mode: str, p: Dict[str, Any], h_cap: str, note: Optional[str]=None) -> str:
    if mode == "breve":
        return f"Consiglio CTF {h_cap}."
    if mode == "standard":
        return (f"Dati: H{p.get('h_lamiera','—')}, soletta {p.get('s_soletta','—')} mm, cls {p.get('cls','—')}, "
                f"passo gola {p.get('passo','—')} mm, direzione {p.get('dir','—')} → esito: CTF {h_cap}.")
    # dettagliata
    return (f"<h3>CTF — Selezione altezza consigliata</h3>"
            f"<ul>"
            f"<li>Lamiera: H{p.get('h_lamiera','—')} ({p.get('dir','—')}) — passo in gola {p.get('passo','—')} mm</li>"
            f"<li>Soletta: {p.get('s_soletta','—')} mm; cls: {p.get('cls','—')}</li>"
            f"<li>Passo lungo trave: {p.get('s_long','—')} mm → n°/m = {1000.0/float(p.get('s_long',1)):.2f}</li>"
            f"<li>t lamiera: {p.get('t_lamiera','—')} mm; nr in gola: {p.get('nr_gola','—')}</li>"
            f"</ul>"
            f"<p><strong>Esito:</strong> CTF <strong>{h_cap}</strong>. <em>{note or ''}</em></p>")

def tpl_ctf_posa(mode: str) -> str:
    if mode == "breve":
        return "Posa CTF: tracciatura, chiodatura con P560, verifica passi/distanze, DPI."
    if mode == "standard":
        return ("Posa CTF: predisposizione lamiera/cls, tracciatura passi, tiro con P560 in appoggio ortogonale, "
                "controllo interassi/bordi/infissione; DPI e controlli di cantiere; attenersi al manuale Tecnaria.")
    return """
    <h3>CTF — Istruzioni di posa</h3>
    <ol>
      <li><strong>Tracciatura</strong>: posizioni secondo elaborati (passo in gola e lungo trave).</li>
      <li><strong>Appoggio e tiro</strong>: P560 in squadra, pressione completa, tiro singolo; verifica chiodatura.</li>
      <li><strong>Controlli</strong>: interassi/distanze minime, qualità infissione, riprese se necessario.</li>
      <li><strong>Sicurezza</strong>: DPI, area delimitata, consumabili conformi.</li>
    </ol>
    <p>Per la verifica di capacità usare le tabelle PR<sub>d</sub> (H/dir/passo/cls) oppure la regola P0×k<sub>t</sub>.</p>
    """.strip()

# =========================================
# 5) Allegati
# =========================================
def tool_attachments(topic: str, intent: str):
    out = []
    if topic == "P560":
        out.append({"label":"Foto P560","href":"/static/img/p560_magazzino.jpg"})
    if topic == "CTF" and intent == "POSA":
        out.append({"label":"Nota posa CTF (PDF)","href":"/static/docs/ctf_posa.pdf"})
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

    if topic == "CTF" and intent == "CALC":
        parsed = parse_ctf_context(context)
        miss = missing_ctf_keys(parsed)
        if miss:
            labels = [UI_LABELS[k] for k in miss]
            return jsonify({"answer":"Per procedere servono: " + ", ".join(labels),
                            "meta":{"needs_params":True,"required_keys":labels}})
        # calcolo (prima matrice, poi fallback)
        h, npm, capm, dem, util, safety, note = choose_ctf_height(parsed)
        ans = tpl_ctf_calc(mode, parsed, h, note)
        return jsonify({"answer":ans,"meta":{"needs_params":False,"required_keys":[]},
                        "attachments":tool_attachments(topic,intent)})

    if topic == "CTF" and intent == "POSA":
        return jsonify({"answer":tpl_ctf_posa(mode),"meta":{"needs_params":False,"required_keys":[]},
                        "attachments":tool_attachments(topic,intent)})

    if topic == "CTF":
        return jsonify({"answer":ctf_answer_info(mode),"meta":{"needs_params":False,"required_keys":[]},
                        "attachments":tool_attachments(topic,"INFO")})
    if topic == "CTL":
        return jsonify({"answer":ctl_answer_info(mode),"meta":{"needs_params":False,"required_keys":[]}})
    if topic == "CEME":
        return jsonify({"answer":ceme_answer_info(mode),"meta":{"needs_params":False,"required_keys":[]}})
    if topic == "DIAPASON":
        return jsonify({"answer":diapason_answer_info(mode),"meta":{"needs_params":False,"required_keys":[]}})
    if topic == "P560":
        return jsonify({"answer":p560_answer(mode),"meta":{"needs_params":False,"required_keys":[]},
                        "attachments":tool_attachments(topic,"INFO")})

    return jsonify({"answer":"Prodotto non gestito.","meta":{"needs_params":False,"required_keys":[]}})

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
