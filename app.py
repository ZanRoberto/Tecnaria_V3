# app.py — TecnariaBot FULL v2.3 (kt-ready, A/B/C all products, attachments)
import json, os, re
from flask import Flask, render_template, request, jsonify

app = Flask(__name__, static_folder="static", template_folder="templates")

# =========================================
# 0) Scope / denylist (fuori Tecnaria)
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
    t = q.lower()
    return any(d in t for d in DENYLIST)

# =========================================
# 2) Parsing del context (wizard)
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
    # parametri ETA per k_t
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

def _pick_height_from_leaf(leaf: dict, n_per_m: float, demand: float, safety: float):
    if not isinstance(leaf, dict) or not leaf: return None
    try:
        items = sorted(leaf.items(), key=lambda kv: int(kv[0].split("_")[1]))
    except Exception:
        items = list(leaf.items())
    for name, prd_one in items:
        cap = float(prd_one) * n_per_m
        if cap >= demand * safety:
            h = name.replace("CTF_", "")
            util = demand / cap if cap else None
            return (h, n_per_m, cap, demand, util, safety)
    return None

def _kt_from_limits(t_mm: float, nr: int) -> float:
    # Semplificazione coerente con ETA:
    # - nr=1: k_t = 1.00 (t>1.0) oppure 0.85 (t<=1.0)
    # - nr>=2: k_t = 0.80 (t>1.0) oppure 0.70 (t<=1.0)
    if nr <= 1:
        return 1.00 if t_mm > 1.0 else 0.85
    return 0.80 if t_mm > 1.0 else 0.70

def choose_ctf_height(p: dict, safety=1.10):
    demand = float(p["vled"])
    s_long = float(p["s_long"])
    n_per_m = 1000.0 / s_long if s_long > 0 else 0.0
    cls = p.get("cls")

    # 1) Soletta piena (se richiesto)
    if p.get("piena"):
        leaf = PRD_DB.get("soletta_piena", {}).get(cls, {})
        r = _pick_height_from_leaf(leaf, n_per_m, demand, safety)
        if r: return r + (None,)
        return ("da determinare (tabelle cls assenti)", n_per_m, None, demand, None, safety, None)

    # 2) Tabella specifica H/dir/passo/cls (se presente)
    H = f"H{p['h_lamiera']}" if "h_lamiera" in p else None
    dirn = p.get("dir")
    passo = f"passo_gola_{p['passo']}" if "passo" in p else None
    if H and dirn and passo and cls and H in PRD_DB and dirn in PRD_DB[H] and passo in PRD_DB[H][dirn] and cls in PRD_DB[H][dirn][passo]:
        leaf = PRD_DB[H][dirn][passo][cls]
        r = _pick_height_from_leaf(leaf, n_per_m, demand, safety)
        if r: return r + (None,)

    # 3) Regola lamiera P0×k_t con (t, nr)
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
                    f"Verifica su lamiera: P0={P0} kN, k_t={kt:.2f} ⇒ PRd={prd_one:.1f} kN/conn.; t={t_mm} mm, nr={nr}/gola.")
        else:
            n_per_m_req = (demand * safety) / prd_one if prd_one > 0 else None
            passo_req = 1000.0 / n_per_m_req if n_per_m_req else None
            note = (f"Capacità {cap:.1f} < domanda×γ {demand*safety:.1f}. "
                    f"Riduci passo lungo trave a ≤ {passo_req:.0f} mm oppure aumenta cls/varia layout.")
            return ("da determinare (rivedere passo)", n_per_m, cap, demand, util, safety,
                    f"Verifica su lamiera: P0={P0} kN, k_t={kt:.2f} ⇒ PRd={prd_one:.1f} kN/conn.; {note}")

    # 4) Mancano parametri lamiera/P0
    missing = []
    if not P0: missing.append("P0 (cls non presente in lamiera_rule)")
    if t_mm <= 0: missing.append("spessore lamiera t")
    if nr   <= 0: missing.append("n° connettori/gola")
    return ("da determinare (parametri lamiera mancanti)", n_per_m, None, demand, None, safety,
            " / ".join(missing) if missing else None)

# =========================================
# 4) Template risposte A/B/C per TUTTI
# =========================================
def ctf_answer_info(mode: str) -> str:
    if mode == "breve":
        return ("I connettori CTF rendono collaborante il solaio acciaio-calcestruzzo. "
                "Sono certificati e rapidi da posare con chiodatrice.")
    if mode == "standard":
        return ("CTF per solai collaboranti acciaio-cls: trasferiscono taglio tra trave e soletta. "
                "Verifica mediante PRd (ETA) o P0×k_t su lamiera; posa con P560 secondo manuale Tecnaria.")
    return ("CTF — Scheda tecnica (INFO):\n"
            "• Impiego: travi acciaio + lamiera grecata o soletta piena.\n"
            "• Progetto: PRd da tabelle/ETA o P0×k_t (dipende da cls, profilo, t, nr/gola); criterio EC4 su capacità per metro.\n"
            "• Posa: P560, controllo passi in gola, distanze, DPI.\n"
            "• Riferimenti: ETA-18/0447, EC4, manuale Tecnaria.")

def ctl_answer_info(mode: str) -> str:
    if mode == "breve":
        return ("I CTL uniscono legno e calcestruzzo migliorando rigidezza e comfort del solaio.")
    if mode == "standard":
        return ("CTL per sistemi collaboranti acciaio-legno/legno-cls: dimensionamento con tabelle Tecnaria; "
                "attenzione a deformabilità/ancoraggi; posa con viti dedicate e DPI.")
    return ("CTL — Scheda tecnica (INFO):\n"
            "• Parametri: specie legno, spessore soletta, interassi.\n"
            "• Verifiche: tabelle Tecnaria + EC5/EC4; scorrimenti e fessurazione.\n"
            "• Posa: viti/staffe, controlli di cantiere, DPI.")

def ceme_answer_info(mode: str) -> str:
    if mode == "breve":
        return ("CEM-E collega il calcestruzzo nuovo a quello esistente garantendo continuità.")
    if mode == "standard":
        return ("CEM-E: connettori cls/cls per ampliamenti e rinforzi; posa a foro con resina; "
                "verifiche secondo ETA e norme locali.")
    return ("CEM-E — Scheda tecnica (INFO): parametri di resistenza cls, profondità ancoraggio, "
            "procedura di foratura/pulizia/iniezione; controlli di estrazione; riferimenti ETA.")

def diapason_answer_info(mode: str) -> str:
    if mode == "breve":
        return ("Diapason consente il rinforzo dei solai esistenti senza demolizioni invasive.")
    if mode == "standard":
        return ("Diapason: connettore a lamiera per riqualifica/adeguamento; distribuisce i carichi in modo diffuso; "
                "posa con chiodi/ancoranti e integrazione nel getto.")
    return ("Diapason — Scheda tecnica (INFO): geometria, ancoraggi, armature locali; "
            "verifiche di trasferimento taglio e compatibilità col cls esistente; posa e DPI.")

def p560_answer(mode: str) -> str:
    if mode == "breve":
        return ("SPIT P560 è la chiodatrice a polvere per la posa rapida dei connettori Tecnaria su acciaio/cls. "
                "Riduce i tempi e garantisce fissaggi affidabili. Usare sempre i DPI.")
    if mode == "standard":
        return ("P560: chiodatrice a polvere per fissaggi strutturali (es. posa CTF su lamiera). "
                "Scegli chiodi/cariche in base al supporto; prova su materiale reale; appoggio perpendicolare; "
                "manutenzione regolare e DPI obbligatori.")
    return ("P560 — Scheda tecnica (DETTAGLIATA):\n"
            "1) Campo d’impiego: fissaggio connettori su acciaio attraverso lamiera grecata; fissaggi su cls non fessurato.\n"
            "2) Procedura: appoggio perpendicolare, pressione piena, tiro controllato; prove preliminari e verifica penetrazione.\n"
            "3) Controlli: centratura in gola, stabilità fissaggio, ripetizione test se cambia carica/supporto.\n"
            "4) Sicurezza: DPI (occhi/udito/mani), area sgombra, attrezzo efficiente; stoccaggio/smaltimento cariche a norma.\n"
            "5) Manutenzione: pulizia camera di scoppio e cursori, ricambi usura; seguire manuale.\n"
            "6) Integrazione: posa CTF su lamiera con corrugamento corretto; riferimenti manuale Tecnaria/EC4.")

def tpl_ctf_calc(mode: str, p: dict, h_cap: str, note: str | None=None) -> str:
    if mode == "breve":
        s = f"Consiglio CTF {h_cap} (capacità ≥ domanda sulla combinazione indicata)."
        return s + (f" {note}" if note else "")
    if mode == "standard":
        s = (f"Dati: H{p.get('h_lamiera','—')}, soletta {p.get('s_soletta','—')} mm, "
             f"passo gola {p.get('passo','—')} mm, lamiera {p.get('dir','—')}, "
             f"passo lungo trave {p.get('s_long','—')} mm, V_L,Ed={p.get('vled','—')} kN/m, cls {p.get('cls','—')}.\n"
             f"Esito: CTF {h_cap}.")
        return s + (f"\nNota: {note}" if note else "")
    return (
        "1) Dati di input:\n"
        f"   • Lamiera H{p.get('h_lamiera','—')} ({p.get('dir','—')}), passo gola {p.get('passo','—')} mm, "
        f"t={p.get('t_lamiera','—')} mm, nr={p.get('nr_gola','—')}/gola\n"
        f"   • Soletta {p.get('s_soletta','—')} mm, passo lungo trave {p.get('s_long','—')} mm\n"
        f"   • V_L,Ed={p.get('vled','—')} kN/m; cls {p.get('cls','—')}\n\n"
        "2) Procedura: PRd da tabelle/ETA o P0×k_t (con t e nr); capacità per metro = PRd × (1000/passo).\n"
        "3) Criterio: capacità ≥ domanda × margine.\n"
        f"4) Esito: CTF {h_cap}.\n"
        "5) Riferimenti: ETA-18/0447, EC4; posa P560.\n"
    ) + (f"6) Note: {note}" if note else "")

def tpl_ctf_posa(mode: str) -> str:
    if mode == "breve": return "Posa CTF su trave attraverso lamiera; seguire manuale Tecnaria. DPI."
    if mode == "standard": return "Allineamento, centratura, fissaggio con P560; controlli di passo e staffe; vedi manuale Tecnaria."
    return ("Posa CTF (tecnico): tracciamento interassi, fissaggio attraverso lamiera, controlli in gola, getto e collaudo. "
            "Riferimenti: manuale Tecnaria, DPI, P560.")

# =========================================
# 5) Allegati / Note tecniche (ripristino)
# =========================================
def tool_attachments(topic: str, intent: str) -> list[dict]:
    out = []
    # P560: foto esemplificativa
    if topic == "P560":
        out.append({"label":"Foto P560 (magazzino)", "href":"/static/img/p560_magazzino.jpg"})
    # CTF: nota di posa
    if topic == "CTF" and intent == "POSA":
        out.append({"label":"Nota di posa CTF (PDF)", "href":"/static/docs/ctf_posa.pdf"})
    # CTF: calcolo (se vuoi aggiungere schede PRd PDF, basta metterle in static/docs e aggiungere qui)
    if topic == "CTF" and intent in {"CALC","INFO"}:
        # esempi opzionali:
        # out.append({"label":"Estratto tabelle PRd (PDF)", "href":"/static/docs/ctf_prd_tabelle.pdf"})
        pass
    # DIAPASON / CEM-E / CTL: placeholder per note tecniche specifiche (aggiungi i tuoi file quando li hai)
    if topic == "DIAPASON":
        # out.append({"label":"Guida rinforzo Diapason (PDF)", "href":"/static/docs/diapason_guida.pdf"})
        pass
    if topic == "CEME":
        # out.append({"label":"Istruzioni posa CEM-E (PDF)", "href":"/static/docs/ceme_posa.pdf"})
        pass
    if topic == "CTL":
        # out.append({"label":"Tabelle CTL (PDF)", "href":"/static/docs/ctl_tabelle.pdf"})
        pass
    return out

# =========================================
# 6) ROUTES
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

    # fuori scope
    if contains_denylist(question):
        return jsonify({
            "answer":"Assistente dedicato esclusivamente a prodotti e sistemi Tecnaria S.p.A.",
            "meta":{"needs_params":False,"required_keys":[]}
        })

    topic = detect_topic(question)
    intent = detect_intent(question)

    # topic non riconosciuto
    if topic is None:
        return jsonify({
            "answer":"Fornisci una domanda su prodotti/sistemi Tecnaria (CTF/CTL/CEM-E/Diapason, P560).",
            "meta":{"needs_params":False,"required_keys":[]}
        })

    # ----- CTF: CALCOLO -----
    if topic == "CTF" and intent == "CALC":
        parsed = parse_ctf_context(context)
        miss = missing_ctf_keys(parsed)
        if miss:
            labels = [UI_LABELS[k] for k in miss]
            return jsonify({
                "answer":"Per procedere servono: " + ", ".join(labels) + ".",
                "meta":{"needs_params":True,"required_keys":labels}
            })
        h, npm, capm, dem, util, safety, note = choose_ctf_height(parsed)
        hcap = f"h={h} mm" if h and str(h).isdigit() else str(h)
        ans = tpl_ctf_calc(mode, parsed, hcap, note)
        calc = {
            "height_mm": h,
            "n_per_m": round(npm,3) if npm is not None else None,
            "cap_per_m": round(capm,2) if capm is not None else None,
            "demand_per_m": round(dem,2) if dem is not None else None,
            "utilization": round(util,3) if isinstance(util,(int,float)) else None,
            "safety": safety
        }
        return jsonify({
            "answer": ans,
            "meta": {"needs_params":False,"required_keys":[],"calc":calc},
            "attachments": tool_attachments(topic,intent)
        })

    # ----- CTF: POSA -----
    if topic == "CTF" and intent == "POSA":
        return jsonify({
            "answer": tpl_ctf_posa(mode),
            "meta": {"needs_params":False,"required_keys":[]},
            "attachments": tool_attachments(topic,intent)
        })

    # ----- CTF: INFO/CONFRONTO -----
    if topic == "CTF":
        return jsonify({
            "answer": ctf_answer_info(mode),
            "meta": {"needs_params":False,"required_keys":[]},
            "attachments": tool_attachments(topic,"INFO")
        })

    # ----- CTL / CEM-E / DIAPASON / P560 -----
    if topic == "CTL":
        return jsonify({
            "answer": ctl_answer_info(mode),
            "meta": {"needs_params":False,"required_keys":[]},
            "attachments": tool_attachments(topic,"INFO")
        })
    if topic == "CEME":
        return jsonify({
            "answer": ceme_answer_info(mode),
            "meta": {"needs_params":False,"required_keys":[]},
            "attachments": tool_attachments(topic,"INFO")
        })
    if topic == "DIAPASON":
        return jsonify({
            "answer": diapason_answer_info(mode),
            "meta": {"needs_params":False,"required_keys":[]},
            "attachments": tool_attachments(topic,"INFO")
        })
    if topic == "P560":
        return jsonify({
            "answer": p560_answer(mode),
            "meta": {"needs_params":False,"required_keys":[]},
            "attachments": tool_attachments(topic,"INFO")
        })

    # fallback
    return jsonify({
        "answer":"Questo assistente copre prodotti e sistemi Tecnaria (CTF/CTL/CEM-E/Diapason, P560).",
        "meta":{"needs_params":False,"required_keys":[]}
    })

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
