# app.py — TecnariaBot FULL v2.3 stabile (rev P560+POSA)

import json, os, re
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
def detect_topic(q: str) -> str | None:
    t = q.lower()
    if any(k in t for k in [" p560", "p560 ", "chiodatrice", "spit p560"]): return "P560"
    if "diapason" in t: return "DIAPASON"
    if any(k in t for k in ["cem-e", "ceme", "cem e"]): return "CEME"
    if any(k in t for k in ["ctl", "acciaio-legno", "acciaio legno"]): return "CTL"
    if any(k in t for k in ["ctf", "connettore", "connettori", "lamiera", "soletta", "gola"]): return "CTF"
    return None

def detect_intent(q: str) -> str:
    t = q.lower()
    if any(k in t for k in ["altezz", "dimension", "v_l", "v l", "v_l,ed", "kn/m", "numero", "quanti", "portata"]):
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

def parse_ctf_context(ctx: str) -> dict:
    out = {}
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

def missing_ctf_keys(parsed: dict) -> list[str]:
    if parsed.get("piena"):
        needed = CRITICAL_PIENA
    else:
        needed = CRITICAL_LAMIERA
    return [k for k in needed if k not in parsed]

# =========================================
# 3) DB PRd + calcolo CTF
# =========================================
def load_ctf_db():
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

def choose_ctf_height(p: dict, safety=1.10):
    demand = float(p["vled"])
    s_long = float(p["s_long"])
    n_per_m = 1000.0 / s_long if s_long > 0 else 0.0
    cls = p.get("cls")
    rule = PRD_DB.get("lamiera_rule", {})
    P0 = rule.get("P0", {}).get(cls)
    t_mm = float(p.get("t_lamiera", 0) or 0)
    nr   = int(p.get("nr_gola", 0) or 0)
    if P0 and t_mm > 0 and nr > 0:
        kt = _kt_from_limits(t_mm, nr)
        prd_one = P0 * kt
        cap = prd_one * n_per_m
        util = demand / cap if cap else None
        if cap >= demand * safety:
            return ("80", n_per_m, cap, demand, util, safety,
                    f"P0={P0} kN, k_t={kt:.2f}, PRd={prd_one:.1f} kN/conn.")
        else:
            n_per_m_req = (demand * safety) / prd_one if prd_one > 0 else None
            passo_req = 1000.0 / n_per_m_req if n_per_m_req else None
            return ("da rivedere", n_per_m, cap, demand, util, safety,
                    f"Capacità {cap:.1f} < {demand*safety:.1f}. Riduci passo ≤{passo_req:.0f} mm.")
    return ("parametri mancanti", n_per_m, None, demand, None, safety,
            "Servono spessore lamiera t e nr connettori/gola")

# =========================================
# 4) Risposte A/B/C
# =========================================
def p560_answer(mode: str) -> str:
    """
    Risposta a tre livelli per SPIT P560 (chiodatrice a polvere).
    La C restituisce HTML strutturato per una lettura “da tecnico”.
    """
    if mode == "breve":
        # 2–3 frasi, zero numeri
        return (
            "P560 è la chiodatrice a polvere usata per fissare i connettori Tecnaria in modo rapido e controllato. "
            "È pensata per l’impiego su travi in acciaio e calcestruzzo, con consumabili dedicati e procedure di sicurezza precise."
        )

    if mode == "standard":
        # discorsiva, senza formule
        return (
            "La P560 è una chiodatrice a polvere professionale per la posa dei connettori Tecnaria su travi in acciaio e su calcestruzzo. "
            "Si utilizza con chiodi e cartucce idonei alla base di fissaggio e richiede messa a punto dell’utensile (pressione corretta, appoggio ortogonale, prova iniziale). "
            "In cantiere vanno eseguiti i controlli di tenuta, distanza dai bordi e passi; obbligatori DPI e personale formato. "
            "Pulizia e manutenzione periodica mantengono costante la qualità di fissaggio. Per i dettagli operativi attenersi al manuale Tecnaria/Spit."
        )

    # modalità "dettagliata" (tecnica) — HTML
    return """
    <h3>P560 — Scheda operativa per posa connettori</h3>

    <h4>Campo d’impiego</h4>
    <ul>
      <li>Fissaggio dei connettori Tecnaria su <strong>travi in acciaio</strong> e su <strong>calcestruzzo</strong> (dove previsto), in sistemi di solaio collaborante.</li>
      <li>Uso con <strong>consumabili dedicati</strong> (chiodi idonei al supporto e cartucce di potenza adeguata).</li>
    </ul>

    <h4>Set-up utensile e consumabili</h4>
    <ul>
      <li>Verificare integrità utensile, guida, puntale e protezioni.</li>
      <li>Selezionare <em>cartuccia</em> in funzione del supporto; eseguire 2–3 tiri di prova su scarti.</li>
      <li>Usare <em>chiodi</em> conformi alle specifiche del supporto (acciaio / cls) e del connettore.</li>
      <li>Impostare la P560 per appoggio ortogonale e pressione costante prima del tiro.</li>
    </ul>

    <h4>Procedura di tiro</h4>
    <ol>
      <li>Marcatura della posizione in accordo a <strong>passi e distanze</strong> di progetto.</li>
      <li>Appoggio in squadra, <strong>pressione completa</strong> e tiro singolo senza rotazioni.</li>
      <li>Controllo immediato: profondità d’infissione, assenza di spanciamenti o rotture del supporto.</li>
    </ol>

    <h4>Controlli di cantiere</h4>
    <ul>
      <li>Distanze minime dai bordi, interassi e passi come da elaborati; rispetto delle tolleranze.</li>
      <li>Campione di tiri su base reale; ripetere prova se si cambia lotto di cartucce o il supporto.</li>
      <li>Registrare eventuali scarti o ripetizioni e motivazioni (es. supporto irregolare, residui, vernici).</li>
    </ul>

    <h4>Sicurezza</h4>
    <ul>
      <li><strong>DPI obbligatori</strong>: protezione occhi/udito/mani; delimitare l’area di lavoro.</li>
      <li>Caricamento e stoccaggio cartucce in sicurezza; divieto di modifiche all’utensile.</li>
    </ul>

    <h4>Manutenzione</h4>
    <ul>
      <li>Pulizia ordinaria (puntale, guida, camera); verifica usura componenti soggetti a sostituzione.</li>
      <li>Manutenzione periodica secondo manuale per garantire costanza di prestazione.</li>
    </ul>

    <h4>Note e riferimenti</h4>
    <ul>
      <li>Usare solo consumabili compatibili; non sostituiscono bullonature/ancoraggi previsti dal progetto.</li>
      <li>Riferimenti: manuale Tecnaria/Spit, norme di sicurezza applicabili, indicazioni del PSC di cantiere.</li>
    </ul>
    """.strip()

def ctf_answer_info(mode: str) -> str:
    if mode == "breve":
        return "CTF: connettori acciaio-calcestruzzo per solai collaboranti, certificati ETA."
    if mode == "standard":
        return "CTF: pioli per solai collaboranti; verifica con PRd o P0×k_t; posa con P560."
    return ("CTF — guida tecnica: impiego su lamiera/soletta piena, verifica EC4 (capacità ≥ domanda×γ), "
            "parametri t e nr, posa con P560, riferimenti ETA-18/0447.")

def ctl_answer_info(mode: str) -> str:
    if mode == "breve": return "CTL: connettori per sistemi legno-calcestruzzo."
    if mode == "standard": return "CTL: usati in sistemi acciaio-legno/legno-cls; posa con viti dedicate."
    return "CTL — scheda tecnica: specie legno, spessori, verifiche EC5/EC4, posa con staffe/viti."

def ceme_answer_info(mode: str) -> str:
    if mode == "breve": return "CEM-E: unisce cls nuovo a esistente."
    if mode == "standard": return "CEM-E: connettori a foro+resina per collegare getti; verifiche ETA."
    return "CEM-E — scheda tecnica: cls esistente/nuovo, foratura e resina, controlli di estrazione."

def diapason_answer_info(mode: str) -> str:
    if mode == "breve": return "Diapason: rinforzo solai esistenti."
    if mode == "standard": return "Diapason: lamiera sagomata per riqualifica; posa con chiodi/ancoranti."
    return "Diapason — scheda tecnica: geometria, barre, verifiche taglio, posa e DPI."

def tpl_ctf_calc(mode: str, p: dict, h_cap: str, note: str|None=None) -> str:
    if mode == "breve":
        return f"Consiglio CTF {h_cap}."
    if mode == "standard":
        return f"Dati: H{p.get('h_lamiera','—')}, soletta {p.get('s_soletta','—')} mm, cls {p.get('cls','—')} → esito: CTF {h_cap}."
    return (f"Input: lamiera H{p.get('h_lamiera','—')}, soletta {p.get('s_soletta','—')} mm, cls {p.get('cls','—')}, "
            f"t={p.get('t_lamiera','—')} mm, nr={p.get('nr_gola','—')}/gola. "
            f"Esito: CTF {h_cap}. Note: {note}")

def tpl_ctf_posa(mode: str) -> str:
    """
    Istruzioni di posa CTF (richiamate quando l’intento è 'POSA').
    """
    if mode == "breve":
        return "Posa CTF su lamiera/soletta: marcatura, chiodatura con P560, controlli su passi e distanze, DPI."
    if mode == "standard":
        return (
            "Posa CTF: predisporre lamiera/cls, tracciare i passi, chiodare con P560 in appoggio ortogonale, "
            "verificare interassi, distanze dai bordi e qualità d’infissione. Usare solo consumabili idonei; "
            "obbligatori DPI e controlli di cantiere. Riferirsi al manuale Tecnaria."
        )
    # dettagliata in HTML
    return """
    <h3>CTF — Istruzioni di posa</h3>
    <ol>
      <li><strong>Tracciatura</strong>: marcatura posizioni secondo i disegni (passo in gola e lungo trave).</li>
      <li><strong>Appoggio e tiro</strong>: P560 in squadra, pressione completa, tiro singolo; verificare la chiodatura.</li>
      <li><strong>Controlli</strong>: interassi/distanze minime, qualità infissione, eventuali riprese.</li>
      <li><strong>Sicurezza</strong>: DPI, area delimitata, consumabili conformi.</li>
    </ol>
    <p>Per le verifiche di capacità usare le tabelle PR<sub>d</sub> o la regola P0×k<sub>t</sub> dove applicabile.</p>
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
        return jsonify({"answer":"Domanda non riconosciuta. Chiedi su prodotti Tecnaria (CTF/CTL/CEM-E/Diapason/P560).",
                        "meta":{"needs_params":False,"required_keys":[]}})

    if topic == "CTF" and intent == "CALC":
        parsed = parse_ctf_context(context)
        miss = missing_ctf_keys(parsed)
        if miss:
            labels = [UI_LABELS[k] for k in miss]
            return jsonify({"answer":"Per procedere servono: " + ", ".join(labels),
                            "meta":{"needs_params":True,"required_keys":labels}})
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
