# app.py — TecnariaBot (A/B/C) con:
# - calcolo CTF (PRd, k_t, k_cop, verifica, piano d’azione)
# - allegati/anteprime
# - contatti aziendali (ramo CONTACTS)
# - mini-wizard lato front già gestito da index.html

import os, re, json, math
from typing import Any, Dict, Optional, List
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_cors import CORS

# ============== OpenAI opzionale (fallback interno se non c'è) ==============
try:
    from openai import OpenAI
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
    client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
except Exception:
    client, OPENAI_MODEL = None, "gpt-4o-mini"

# ======================= Flask =======================
app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# ======================= Contatti =======================
CONTACTS = {
    "ragione_sociale": "TECNARIA S.p.A.",
    "indirizzo": "Viale Pecori Giraldi, 55 – 36061 Bassano del Grappa (VI)",
    "telefono": "+39 0424 502029",
    "fax": "+39 0424 502386",
    "email": "info@tecnaria.com",
    "sito": "https://tecnaria.com"
}
CONTACTS_KEYWORDS = [
    "contatti","contatto","telefono","numero","chiamare","email","mail","indirizzo",
    "sede","dove siete","orari","pec","ufficio","assistenza","referente","commerciale"
]

# ======================= Scope / denylist (no prodotti terzi) =======================
DENYLIST = {"hbv","x-hbv","xhbv","hi-bond ","hibond ","ribdeck","hilti shear","fva","comflor","metsec","holorib","p800"}
def contains_denylist(q: str) -> bool:
    return any(d in (q or "").lower() for d in DENYLIST)

# ======================= Topic & Intent =======================
def detect_topic(q: str) -> Optional[str]:
    t = (q or "").lower()
    if any(k in t for k in CONTACTS_KEYWORDS): return "CONTACTS"
    if any(k in t for k in [" p560","p560 ","chiodatrice","spit p560"]): return "P560"
    if "diapason" in t: return "DIAPASON"
    if any(k in t for k in ["cem-e","ceme","cem e"]): return "CEME"
    if any(k in t for k in ["ctl","acciaio-legno","acciaio legno","legno"]): return "CTL"
    if any(k in t for k in ["ctf","connettore","connettori","lamiera","soletta","gola","altezza connettore"]): return "CTF"
    return None

def detect_intent(q: str) -> str:
    t = (q or "").lower()
    if any(k in t for k in ["altezz","dimension","v_l","v l","v_l,ed","kn/m","numero","quanti","portata","scegliere","che altezza","verifica","calcolo"]):
        return "CALC"
    if any(k in t for k in ["posa","installazione","fissare","uso in cantiere","come si posa","istruzioni"]):
        return "POSA"
    if any(k in t for k in ["differenza","vs","confronto","meglio"]):
        return "CONFRONTO"
    return "INFO"

# ======================= Parser contesto (mini-wizard) =======================
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
    "copriferro":re.compile(r"copriferro\s*([\d.,]+)\s*mm", re.I),
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
    "nr_gola":"N° connettori per gola",
    "copriferro":"Copriferro (mm)"
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
    out["copriferro"]= f("copriferro", float, repl=True)
    return {k:v for k,v in out.items() if v is not None}

def missing_ctf_keys(parsed: Dict[str, Any]) -> List[str]:
    needed = CRITICAL_PIENA if parsed.get("piena") else CRITICAL_LAMIERA
    return [k for k in needed if k not in parsed]  # copriferro non bloccante

# ======================= Database PRd (JSON) =======================
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
    Hnode = None
    for hk in db.keys():
        if _norm(hk) in map(_norm, _h_keys(h_lamiera)):
            Hnode = db[hk]; break
    if not isinstance(Hnode, dict): return None
    Dnode = None
    for dk in Hnode.keys():
        if _norm(dk) in map(_norm, _dir_key(dir_lam)):
            Dnode = Hnode[dk]; break
    if not isinstance(Dnode, dict): return None
    Pnode = None
    for pk in Dnode.keys():
        if _norm(pk) in map(_norm, _passo_keys(passo_gola)):
            Pnode = Dnode[pk]; break
    if Pnode is None: Pnode = Dnode
    if not isinstance(Pnode, dict): return None
    Cnode = None
    for ck in Pnode.keys():
        if _norm(ck) in map(_norm, _cls_keys(cls)):
            Cnode = Pnode[ck]; break
    if not isinstance(Cnode, dict): return None
    result = {}
    for k, v in Cnode.items():
        if _is_height_key(k):
            try: result[k.upper().replace("-", "_")] = float(v)
            except: pass
    return result or None

# ======================= k_t / k_cop / scelta CTF =======================
def _kt_from_limits(t_mm: float, nr: int) -> float:
    if nr <= 1:
        return 1.00 if t_mm > 1.0 else 0.85
    return 0.80 if t_mm > 1.0 else 0.70

def _kcop_from_json_or_default(db: Dict[str, Any], copriferro_mm: Optional[float]) -> float:
    if copriferro_mm is None: return 1.00
    rules = (db.get("copriferro_rule") or {}).get("ranges") if isinstance(db.get("copriferro_rule"), dict) else None
    if isinstance(rules, list):
        for r in rules:
            try:
                lo = float(r.get("min_mm", "-inf")); hi = float(r.get("max_mm", "inf")); f = float(r.get("factor", 1.0))
                if copriferro_mm >= lo and copriferro_mm < hi: return f
            except: pass
    if copriferro_mm >= 25.0: return 1.00
    if copriferro_mm >= 15.0: return 0.85
    return 0.70

def choose_ctf_from_matrix_or_fallback(p: Dict[str, Any], safety: float = 1.10):
    s_long = float(p["s_long"])
    n_per_m = 1000.0 / s_long if s_long > 0 else 0.0
    demand = float(p["vled"])
    prd_map = find_prd_table(PRD_DB, p["h_lamiera"], p["dir"], p["passo"], p["cls"])
    used_source = "tabella PRd (H/dir/passo/cls)"
    if not prd_map:
        used_source = "solid_base (Annex C1)"
        orient = "parallel" if p["dir"].startswith("l") else "perpendicular"
        base_cls = PRD_DB.get("solid_base", {}).get(orient, {}).get(p["cls"].upper(), {})
        prd_map = {}
        for k, v in base_cls.items():
            if _is_height_key(k):
                try: prd_map[k.upper().replace("-", "_")] = float(v or 0.0)
                except: pass
    if not prd_map:
        return ("non determinabile", n_per_m, None, demand, None, safety,
                "Database PRd non disponibile: completa static/data/ctf_prd.json.")

    items = sorted(prd_map.items(), key=lambda kv: _height_order_key(kv[0]))
    apply_kt  = (p.get("t_lamiera") is not None) and (p.get("nr_gola") is not None) and (not p.get("piena"))
    kt = _kt_from_limits(float(p.get("t_lamiera", 0) or 0), int(p.get("nr_gola", 0) or 0)) if apply_kt else 1.0
    kcop = _kcop_from_json_or_default(PRD_DB, p.get("copriferro"))

    best = None
    last_key, last_prd_one = None, 0.0
    for key, prd_one_raw in items:
        prd_one = prd_one_raw * (kt if apply_kt else 1.0) * kcop
        cap = prd_one * n_per_m
        last_key, last_prd_one = key, prd_one
        if cap >= demand * safety:
            best = (key, prd_one, cap); break

    if best:
        key, prd_one, cap = best
        util = demand / cap if cap else None
        note = (f"Fonte={used_source}; PRd/conn={prd_one:.2f} kN"
                f"{' (×k_t)' if apply_kt else ''}"
                f"{' (×k_cop)' if p.get('copriferro') is not None else ''}; "
                f"n°/m={n_per_m:.2f}.")
        m = re.search(r"(\d{3})", key); h_code = m.group(1) if m else key
        return (h_code, n_per_m, cap, demand, util, safety, note)

    if last_prd_one > 0:
        n_req = (demand * safety) / last_prd_one
        passo_req = 1000.0 / n_req
        msg = (f"Nessuna altezza soddisfa. Con {last_key} serve passo ≤{passo_req:.0f} mm "
               f"(Fonte={used_source}; PRd/conn={last_prd_one:.2f} kN"
               f"{' (×k_t)' if apply_kt else ''}"
               f"{' (×k_cop)' if p.get('copriferro') is not None else ''}).")
    else:
        msg = "Nessuna altezza soddisfa e PRd base assente. Verifica ctf_prd.json."
    return ("da rivedere", n_per_m, last_prd_one * n_per_m, demand, None, safety, msg)

def choose_ctf_height(p: Dict[str, Any], safety: float = 1.10):
    return choose_ctf_from_matrix_or_fallback(p, safety)

# ======================= Render calcolo (HTML) =======================
def s_long_max(prd_conn: float, demand_kNm: float):
    if prd_conn <= 0: return math.inf
    return (1000.0 * prd_conn) / demand_kNm

def render_calc_block(parsed: Dict[str, Any], result_tuple):
    (best_h, n_per_m, cap_m, demand, util, safety, note) = result_tuple
    header = (
        "<h3>CTF — Selezione altezza consigliata</h3>"
        "<ul>"
        f"<li>Lamiera: H{parsed.get('h_lamiera','—')} ({parsed.get('dir','—')}) — passo in gola {parsed.get('passo','—')} mm</li>"
        f"<li>Soletta: {parsed.get('s_soletta','—')} mm; cls: {parsed.get('cls','—')}</li>"
        f"<li>Passo lungo trave: {parsed.get('s_long','—')} mm → n°/m = {1000.0/float(parsed.get('s_long',1)):.2f}</li>"
        f"<li>t lamiera: {parsed.get('t_lamiera','—')} mm; nr in gola: {parsed.get('nr_gola','—')}"
        f"{'; copriferro: ' + str(parsed.get('copriferro')) + ' mm' if parsed.get('copriferro') is not None else ''}</li>"
        "</ul>"
    )
    if best_h and best_h not in ("da rivedere","non determinabile"):
        return (header +
            "<div style='border-left:4px solid #2ecc71;padding-left:10px;margin:8px 0'>"
            f"<p><strong>✅ VERIFICATO</strong> — Altezza consigliata: <strong>CTF {best_h}</strong>.<br>"
            f"n°/m = {n_per_m:.2f}; Capacità = <strong>{cap_m:.1f} kN/m</strong>; "
            f"Domanda = {demand:.1f} kN/m; Utilization = {(demand/cap_m)*100:.1f}%.</p>"
            f"<p><em>{note}</em></p></div>"
        )
    prd_conn = (cap_m / n_per_m) if (n_per_m and cap_m is not None) else 0.0
    smax = s_long_max(prd_conn, demand) if (prd_conn and demand) else math.inf
    gap = (demand - (cap_m or 0.0)); over = (gap / demand * 100.0) if demand else 0.0
    why = ("<p><strong>Perché non verifica</strong><br>(Capacità/m = PRd×n/m = PRd×(1000/s_long)) insufficiente con s_long attuale.</p>")
    plan = (
        "<ol>"
        f"<li><strong>Riduci il passo lungo trave</strong> a ≤ <strong>{smax:.0f} mm</strong> (con <strong>CTF 135</strong> o il più prestante).</li>"
        "<li>Aumenta <strong>PRd/conn</strong>: nr/gola → 3 (se ammesso), t lamiera → 1.25 mm, cls → C35/45, orientamento più favorevole.</li>"
        "<li>In alternativa: <strong>redistribuzione carichi</strong> / modifica schema di connessione.</li>"
        "</ol>"
    )
    params = (
        f"<p><strong>Parametri</strong> — H{parsed.get('h_lamiera','—')} ({parsed.get('dir','—')}), "
        f"gola {parsed.get('passo','—')} mm; soletta {parsed.get('s_soletta','—')} mm, cls {parsed.get('cls','—')}; "
        f"s_long {parsed.get('s_long','—')} mm ({n_per_m:.2f}/m); t {parsed.get('t_lamiera','—')} mm; nr {parsed.get('nr_gola','—')}"
        f"{' • copriferro ' + str(parsed.get('copriferro')) + ' mm' if parsed.get('copriferro') is not None else ''}</p>"
    )
    headline = (
        "<div style='border-left:4px solid #e74c3c;padding-left:10px;margin:8px 0'>"
        "<p><strong>⚠️ NON VERIFICATO</strong> — capacità < domanda di progetto<br>"
        f"<strong>Domanda</strong> {demand:.1f} kN/m • <strong>Capacità</strong> {cap_m:.1f} kN/m → "
        f"<strong>Gap</strong> {gap:.1f} kN/m (+{over:.1f}%)</p>"
    )
    notes = f"<p><strong>Note</strong> — {note} • s_long,max = {smax:.0f} mm</p></div>"
    return header + headline + why + "<h4>Piano d’azione</h4>" + plan + params + notes

# ======================= Allegati / Note =======================
def tool_attachments(topic: str, intent: str):
    out = []
    if topic == "P560":
        out.append({"label":"Foto P560","href":"/static/img/p560_magazzino.jpg","preview":True})
        # esempio PDF: out.append({"label":"Manuale P560 (PDF)","href":"/static/docs/p560_manual.pdf","preview":True})
    if topic == "CTF" and intent == "POSA":
        out.append({"label":"Istruzioni posa CTF (PDF)","href":"/static/docs/ctf_posa.pdf","preview":True})
    if topic == "CTF" and intent == "INFO":
        out.append({"label":"Scheda tecnica CTF (PDF)","href":"/static/docs/ctf_scheda.pdf","preview":True})
    return out

# ======================= LLM A/B/C (fallback coerente) =======================
SYSTEM_BASE = ("Sei TecnariaBot, assistente tecnico di Tecnaria S.p.A. Rispondi solo su CTF, CTL, CEM-E, Diapason, P560. "
               "Niente prodotti terzi. Niente valori inventati. Italiano.")
def build_style_block(mode: str) -> str:
    mode = (mode or "").lower()
    if mode == "breve":
        return "Stile A: 90–130 parole, chiaro, senza formule."
    if mode == "standard":
        return "Stile B: 180–260 parole, discorsivo tecnico, 1 elenco breve ammesso."
    return ("Stile C: 380–600 parole. HTML sezioni: <h3>Cos’è</h3>, <h4>Componenti</h4>, <h4>Varianti</h4>, "
            "<h4>Prestazioni</h4>, <h4>Posa</h4>, <h4>Norme e riferimenti</h4>, <h4>Vantaggi e limiti</h4>.")

def llm_reply(topic: str, intent: str, mode: str, question: str, context: str) -> str:
    if not client:
        # risposte sintetiche ma coerenti A/B/C se manca API
        if topic == "P560":
            if mode == "breve":
                return "P560: chiodatrice a polvere per fissare connettori Tecnaria. DPI, controllo quota 3.5–7.5 mm, bending test a campione."
            if mode == "standard":
                return ("La P560 è una chiodatrice a polvere per fissaggi rapidi su travi/lamiera. "
                        "Regolazione potenza, pressione ortogonale, controllo quota 3.5–7.5 mm tra testa chiodo e piastra, "
                        "bending test iniziale e a campione. DPI obbligatori e manutenzione regolare.")
            return ("<h3>P560</h3><h4>Impiego</h4><p>Fissaggio connettori su travi/lamiera; regolazione potenza; pressatura ortogonale; "
                    "verifica quota 3.5–7.5 mm.</p><h4>Controlli</h4><p>Bending test all’avvio/campione; registro controlli; DPI.</p>"
                    "<h4>Manutenzione</h4><p>Pulizia giornaliera, ispezione guarnizioni, kit ricambi.</p>")
        if topic == "CTF":
            if mode == "breve":
                return "CTF: connettori a taglio per solai acciaio–calcestruzzo; scelta da PRd (ETA) e passo lungo trave; posa con P560."
            if mode == "standard":
                return ("I CTF collegano lamiera/soletta a travi acciaio. La selezione dipende da lamiera/direzione/passo/cls "
                        "e si verifica con PRd per connettore e n°/m. Posa con P560 e controlli di cantiere.")
            return ("<h3>CTF</h3><p>Usa ETA-18/0447 e manuale Tecnaria per PRd; verifica EC4; posa con P560; controlli quota chiodi e bending test.</p>")
        if topic == "CTL":
            return "CTL: sistema per solai legno–calcestruzzo; verifica EC5/EC4; posa con viti/staffe e getto collaborante."
        if topic == "CEME":
            return "CEM-E: collegamento cls esistente/nuovo con resina; preparazione foro, pulizia, iniezione; prove di estrazione."
        if topic == "DIAPASON":
            return "Diapason: soluzione per riqualifica solai; posa con chiodi/ancoranti; verifica a taglio/scorrimento."
        return "Assistente dedicato ai prodotti Tecnaria S.p.A."
    style = build_style_block(mode)
    messages = [
        {"role":"system","content": SYSTEM_BASE},
        {"role":"user","content": f"Prodotto: {topic} • Intent: {intent}\nDomanda: {question}\nContesto: {context or '—'}\n{style}"},
    ]
    resp = client.chat.completions.create(model=OPENAI_MODEL, messages=messages, temperature=0.2, top_p=0.9, max_tokens=900)
    return resp.choices[0].message.content.strip()

# ======================= Routes =======================
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
        return jsonify({"answer":"Assistente dedicato ai prodotti Tecnaria S.p.A.",
                        "meta":{"needs_params":False,"required_keys":[]}})

    topic  = detect_topic(question)
    intent = detect_intent(question)

    # --- Contatti aziendali
    if topic == "CONTACTS":
        txt = (
            f"<strong>{CONTACTS['ragione_sociale']}</strong><br>"
            f"{CONTACTS['indirizzo']}<br>"
            f"Tel: {CONTACTS['telefono']} — Fax: {CONTACTS['fax']}<br>"
            f"Email: {CONTACTS['email']} — Sito: {CONTACTS['sito']}"
        )
        attachments = [{"label":"Pagina contatti","href":CONTACTS["sito"]+"/contatti/","preview":False}]
        return jsonify({"answer": txt, "attachments": attachments, "meta":{"needs_params":False}})

    if topic is None:
        return jsonify({"answer":"Assistente dedicato ai prodotti Tecnaria (CTF/CTL/CEM-E/Diapason/P560).",
                        "meta":{"needs_params":False,"required_keys":[]}})

    # --- CTF calcolo (mini-wizard)
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

    # --- Altri casi → LLM A/B/C
    prose = llm_reply(topic, intent, mode, question, context)

    # se l’utente ha già dati completi, aggiungo il blocco calcolo in coda
    extra = ""
    if topic == "CTF":
        parsed = parse_ctf_context(context)
        miss = missing_ctf_keys(parsed)
        if not miss:
            result = choose_ctf_height(parsed)
            extra = "<hr>" + render_calc_block(parsed, result)
        elif any(k in parsed for k in ("h_lamiera","s_soletta","vled","passo","s_long","t_lamiera","nr_gola","copriferro")):
            labels = [UI_LABELS[k] for k in miss]
            extra = f"<hr><p><em>Dati calcolo incompleti:</em> mancano {', '.join(labels)}.</p>"

    return jsonify({"answer": prose + extra, "meta":{"needs_params":False}, "attachments":tool_attachments(topic,intent)})

@app.route("/static/<path:path>")
def static_proxy(path):
    return send_from_directory("static", path)

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
