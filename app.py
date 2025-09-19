# app.py — TecnariaBot FULL v2.1
import json, os, re
from flask import Flask, render_template, request, jsonify

app = Flask(__name__, static_folder="static", template_folder="templates")

# ------------ Scope / denylist ------------
DENYLIST = {"hbv", "x-hbv", "xhbv", "fva", "hi-bond ", "hibond ", "ribdeck", "p800", "hilti shear"}

# ------------ Topic / Intent --------------
def detect_topic(q: str) -> str | None:
    t = q.lower()
    if any(k in t for k in [" p560", "p560 ", "chiodatrice", "spit p560"]): return "P560"
    if any(k in t for k in ["ctl", "acciaio-legno", "acciaio legno"]): return "CTL"
    if any(k in t for k in ["cem-e", "ceme", "cem e"]): return "CEME"
    if "diapason" in t: return "DIAPASON"
    if any(k in t for k in ["ctf", "connettore"]): return "CTF"
    return None

def detect_intent(q: str) -> str:
    t = q.lower()
    if any(k in t for k in ["altezz", "dimension", "v_l", "v l", "v_l,ed", "kn/m", "numero", "quanti", "portata"]):
        return "CALC"
    if any(k in t for k in ["posa", "installazione", "fissare", "uso in cantiere"]):
        return "POSA"
    if any(k in t for k in ["differenza", "vs", "confronto"]):
        return "CONFRONTO"
    return "INFO"

def contains_denylist(q: str) -> bool:
    t = q.lower()
    return any(d in t for d in DENYLIST)

# ------------ Parsing wizard/context -------
CTX_RE = {
    "h_lamiera": re.compile(r"lamiera\s*h?\s*(\d+)", re.I),
    "s_soletta": re.compile(r"soletta\s*(\d+)\s*mm", re.I),
    "vled":      re.compile(r"v[\s_.,-]*l\s*,?ed\s*=\s*([\d.,]+)\s*kn/?m", re.I),
    "cls":       re.compile(r"cls\s*([Cc]\d+\/\d+)", re.I),
    "passo":     re.compile(r"passo\s*gola\s*(\d+)\s*mm", re.I),
    "dir":       re.compile(r"lamiera\s*(longitudinale|trasversale)", re.I),
    "s_long":    re.compile(r"passo\s+lungo\s+trave\s*(\d+)\s*mm", re.I),
    "piena":     re.compile(r"soletta\s+piena", re.I),
}
CRITICAL = ["h_lamiera", "s_soletta", "vled", "cls", "passo", "dir", "s_long"]
UI_LABELS = {
    "h_lamiera":"altezza lamiera",
    "s_soletta":"spessore soletta",
    "vled":"V_L,Ed",
    "cls":"cls",
    "passo":"passo gola",
    "dir":"direzione lamiera",
    "s_long":"passo lungo trave"
}

def parse_ctf_context(ctx: str) -> dict:
    out = {}
    if not ctx: return out
    m = CTX_RE["h_lamiera"].search(ctx); out["h_lamiera"] = int(m.group(1)) if m else None
    m = CTX_RE["s_soletta"].search(ctx); out["s_soletta"] = int(m.group(1)) if m else None
    m = CTX_RE["vled"].search(ctx);      out["vled"] = float(m.group(1).replace(",", ".")) if m else None
    m = CTX_RE["cls"].search(ctx);       out["cls"] = m.group(1).upper() if m else None
    m = CTX_RE["passo"].search(ctx);     out["passo"] = int(m.group(1)) if m else None
    m = CTX_RE["dir"].search(ctx);       out["dir"] = m.group(1).lower() if m else None
    m = CTX_RE["s_long"].search(ctx);    out["s_long"] = int(m.group(1)) if m else None
    m = CTX_RE["piena"].search(ctx);     out["piena"] = True if m else False
    return {k:v for k,v in out.items() if v is not None}

def missing_ctf_keys(parsed: dict) -> list[str]:
    if parsed.get("piena"):     # su soletta piena non servono h_lamiera/dir/passo
        needed = ["s_soletta","vled","cls","s_long"]
        return [k for k in needed if k not in parsed]
    return [k for k in CRITICAL if k not in parsed]

# ------------ DB PRd -----------------------
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

def choose_ctf_height(p: dict, safety=1.10):
    demand = float(p["vled"])
    s_long = float(p["s_long"])
    n_per_m = 1000.0 / s_long if s_long > 0 else 0.0
    H = f"H{p['h_lamiera']}" if "h_lamiera" in p else None
    dirn = p.get("dir")
    passo = f"passo_gola_{p['passo']}" if "passo" in p else None
    cls = p.get("cls")

    # 1) Tabella completa H/dir/passo/cls
    if H and dirn and passo and cls:
        try:
            leaf = PRD_DB[H][dirn][passo][cls]
            r = _pick_height_from_leaf(leaf, n_per_m, demand, safety)
            if r: return r + (None,)
        except Exception:
            pass

    # 2) Soletta piena
    if p.get("piena") or ("soletta_piena" in PRD_DB and cls in PRD_DB["soletta_piena"]):
        try:
            leaf = PRD_DB["soletta_piena"][cls]
            r = _pick_height_from_leaf(leaf, n_per_m, demand, safety)
            if r: return r + (None,)
        except Exception:
            pass

    # 3) Regola lamiera P0×k_t (base: k_t max; per risultato definitivo servono hp, b0, t, nr)
    rule = PRD_DB.get("lamiera_rule", {})
    P0 = rule.get("P0", {}).get(cls)
    if P0:
        kt_max = 0.0
        for lim in rule.get("kt_limits", []):
            kt_max = max(kt_max, float(lim.get("kt_max", 0.0)))
        prd_base = P0 * kt_max
        cap = prd_base * n_per_m
        util = demand / cap if cap else None
        return ("da determinare (servono hp, b0, t, nr)", n_per_m, cap, demand, util, safety,
                "Lamiera grecata: completare hp (altezza profilo), b0 (larghezza gola), spessore t, n° connettori/gola.")

    return ("da determinare (manca combinazione PRd)", n_per_m, None, demand, None, safety, None)

# ------------ Risposte A/B/C ---------------
def tpl_ctf_calc(mode: str, p: dict, h_cap: str, note: str | None=None) -> str:
    if mode == "breve":
        s = f"Consiglio CTF {h_cap} (criterio: capacità ≥ domanda)."
        return s + (f" {note}" if note else "")
    if mode == "standard":
        s = (f"Dati: H{p.get('h_lamiera','—')}, soletta {p.get('s_soletta','—')} mm, "
             f"passo gola {p.get('passo','—')} mm, lamiera {p.get('dir','—')}, "
             f"passo lungo trave {p.get('s_long','—')} mm, V_L,Ed={p.get('vled','—')} kN/m, cls {p.get('cls','—')}.\n"
             f"Esito: CTF {h_cap}.")
        return s + (f"\nNota: {note}" if note else "")
    return (
        "1) Dati di input:\n"
        f"   • Lamiera H{p.get('h_lamiera','—')} ({p.get('dir','—')}), passo gola {p.get('passo','—')} mm\n"
        f"   • Soletta {p.get('s_soletta','—')} mm, passo lungo trave {p.get('s_long','—')} mm\n"
        f"   • V_L,Ed={p.get('vled','—')} kN/m; cls {p.get('cls','—')}\n\n"
        "2) Procedura: PRd da tabelle/ETA o P0×k_t; capacità per metro = PRd × (1000/passo lungo trave).\n"
        "3) Criterio: capacità ≥ domanda × margine.\n"
        f"4) Esito: CTF {h_cap}.\n"
        "5) Riferimenti: ETA-18/0447, EC4; posa P560.\n"
    ) + (f"6) Note: {note}" if note else "")

def tpl_ctf_posa(mode: str) -> str:
    if mode == "breve": return "Posa CTF su trave attraverso lamiera; seguire manuale Tecnaria. DPI."
    if mode == "standard": return "Allineamento, centratura, fissaggio con P560; controlli di passo e staffe; vedi manuale Tecnaria."
    return ("Posa CTF (tecnico): tracciamento interassi, fissaggio attraverso lamiera, controlli in gola, getto e collaudo. "
            "Riferimenti: manuale Tecnaria, DPI, P560.")

KB = {
    "CTL": {
        "breve": "Connettori CTL per sistemi acciaio-legno.",
        "standard": "CTL: progettazione con tabelle Tecnaria; verifiche EC5/EC4 parziali; posa con DPI.",
        "dettagliata": "CTL — Dati: specie legno, spessori, interassi. Verifiche secondo tabelle Tecnaria; posa e controlli come da manuale."
    },
    "CEME": {"standard": "CEM-E: collegamento cls esistente/nuovo; verifiche e posa da manuale Tecnaria."},
    "DIAPASON": {"standard": "Diapason: rinforzo/adeguamento solai; verifiche e posa da manuale Tecnaria."},
    "P560": {
        "breve":"SPIT P560: chiodatrice a polvere per posa connettori/fissaggi.",
        "standard":"P560: scelte cariche/chiodi, DPI; manutenzione e prove su materiale; vedi manuale.",
        "dettagliata":"P560 — specifiche d’impiego, selezione cariche/chiodi, sicurezza e manutenzione; conforme manuale Tecnaria."
    },
    "CTF": {
        "standard":"CTF: connettori per solai collaboranti acciaio-cls; verifica con PRd da ETA o P0×k_t; posa P560."
    }
}

def kb_answer(topic, mode):
    block = KB.get(topic, {})
    return block.get(mode) or block.get("standard") or "Prodotto Tecnaria."

# ------------ Allegati ---------------------
def tool_attachments(topic: str, intent: str):
    out = []
    if topic == "P560":
        out.append({"label":"Foto P560 (magazzino)","href":"/static/img/p560_magazzino.jpg"})
    if topic == "CTF" and intent == "POSA":
        out.append({"label":"Nota di posa CTF (PDF)","href":"/static/docs/ctf_posa.pdf"})
    return out

# ------------ Routes -----------------------
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
        return jsonify({"answer":"Assistente dedicato esclusivamente a prodotti e sistemi Tecnaria S.p.A.",
                        "meta":{"needs_params":False,"required_keys":[]}})

    topic = detect_topic(question)
    intent = detect_intent(question)

    if topic is None:
        return jsonify({"answer":"Fornisci una domanda su prodotti/sistemi Tecnaria (CTF/CTL/CEM-E/Diapason, P560).",
                        "meta":{"needs_params":False,"required_keys":[]}})

    if topic == "CTF" and intent == "CALC":
        parsed = parse_ctf_context(context)
        miss = missing_ctf_keys(parsed)
        if miss:
            labels = [UI_LABELS[k] for k in miss]
            return jsonify({"answer":"Per procedere servono: " + ", ".join(labels) + ".",
                            "meta":{"needs_params":True,"required_keys":labels}})
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
        return jsonify({"answer":ans, "meta":{"needs_params":False,"required_keys":[],"calc":calc},
                        "attachments": tool_attachments(topic,intent)})

    if topic == "CTF" and intent == "POSA":
        return jsonify({"answer": tpl_ctf_posa(mode),
                        "meta":{"needs_params":False,"required_keys":[]},
                        "attachments": tool_attachments(topic,intent)})

    # CTL / CEME / DIAPASON / P560
    return jsonify({"answer": kb_answer(topic, mode),
                    "meta":{"needs_params":False,"required_keys":[]},
                    "attachments": tool_attachments(topic,intent)})

@app.route("/health")
def health(): return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
