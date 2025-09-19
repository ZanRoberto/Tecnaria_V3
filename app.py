# app.py — TecnariaBot (full) v2.0
# - Scope prodotti Tecnaria: CTF / CTL / CEM-E / DIAPASON / P560 (P800 escluso)
# - Modalità A/B/C (breve / standard / dettagliata)
# - Wizard: parametri CTF con parsing robusto dal campo "context"
# - Calcolo altezza CTF da static/data/ctf_prd.json:
#     1) Tabelle per combinazione (Hxx → direzione → passo_gola_xx → cls → {CTF_xx: PRd})
#     2) Se assenti: blocco "soletta_piena" (cls → {CTF_xx: PRd})
#     3) Se assenti: "lamiera_rule" (P0 × k_t) → richiede parametri lamiera (hp, b0, t, nr) per risultato definitivo
# - Allegati tecnici (immagini/PDF)
# - Denylist marchi/sistemi non Tecnaria
# - Risposte sempre non vuote e coerenti con il topic/intent

from flask import Flask, render_template, request, jsonify
import re, os, json

app = Flask(__name__, static_folder="static", template_folder="templates")

# ==============================
# 0) Scope / denylist
# ==============================
TECNARIA_TOPICS = {"CTF", "CTL", "CEME", "DIAPASON", "P560"}  # P800 escluso
DENYLIST = {
    # marchi/sistemi NON Tecnaria
    "hbv", "x-hbv", "xhbv", "fva", "hi-bond ", "hibond ", "ribdeck", "sherpa",
    "lindapter", "hilti shear", "x-fcm", "x-hcc", "p800"
}

# ==============================
# 1) Riconoscimento topic/intent
# ==============================
def detect_topic(q: str) -> str | None:
    t = q.lower()
    if any(k in t for k in [" p560", "p560 ", "chiodatrice", "spit p560"]): return "P560"
    if "diapason" in t: return "DIAPASON"
    if any(k in t for k in ["cem-e", "ceme", "cem e"]): return "CEME"
    if any(k in t for k in ["ctl", "acciaio-legno", "acciaio legno", "legno"]): return "CTL"
    if any(k in t for k in ["ctf", "connettore", "connettori", "lamiera", "solaio", "soletta", "gola", "passo gola"]): return "CTF"
    return None

def detect_intent(q: str) -> str:
    t = q.lower()
    calc_hints = [
        "altezza", "altezze", "dimension", "dimensiona", "dimensionamento",
        "v_l", "v l", "v_l,ed", "kn/m", "portata", "quanti", "quante",
        "numero connettori", "pr d", "pr_d", "pr,d"
    ]
    posa_hints = ["posa", "posare", "installazione", "fissare", "utilizzo", "uso in cantiere"]
    conf_hints = ["differenza", "vs", "confronto", "meglio", "alternativa"]
    if any(k in t for k in calc_hints):   return "CALC"
    if any(k in t for k in posa_hints):   return "POSA"
    if any(k in t for k in conf_hints):   return "CONFRONTO"
    return "INFO"

def contains_denylist(q: str) -> bool:
    t = q.lower()
    return any(bad in t for bad in DENYLIST)

# ==============================
# 2) Parsing del "context" (wizard)
# ==============================
CTX_RE = {
    "h_lamiera": re.compile(r"lamiera\s*h?\s*(\d+)", re.I),  # lamiera H55
    "s_soletta": re.compile(r"soletta\s*(\d+)\s*mm", re.I),
    "vled":      re.compile(r"v[\s_.,-]*l\s*,?ed\s*=\s*([\d.,]+)\s*kn/?m", re.I),
    "cls":       re.compile(r"cls\s*([Cc]\d+\/\d+)", re.I),
    "passo":     re.compile(r"passo\s*gola\s*(\d+)\s*mm", re.I),
    "dir":       re.compile(r"lamiera\s*(longitudinale|trasversale)", re.I),
    "s_long":    re.compile(r"passo\s+lungo\s+trave\s*(\d+)\s*mm", re.I),
    # opzionale: caso "soletta piena" (senza lamiera)
    "piena":     re.compile(r"soletta\s+piena", re.I),
}

CRITICAL_CTF_KEYS = ["h_lamiera", "s_soletta", "vled", "cls", "passo", "dir", "s_long"]
UI_LABELS = {
    "h_lamiera": "altezza lamiera",
    "s_soletta": "spessore soletta",
    "vled": "V_L,Ed",
    "cls": "cls",
    "passo": "passo gola",
    "dir": "direzione lamiera",
    "s_long": "passo lungo trave",
}

def parse_ctf_context(ctx: str) -> dict:
    out = {}
    if not ctx: return out
    m = CTX_RE["h_lamiera"].search(ctx);   out["h_lamiera"] = int(m.group(1)) if m else None
    m = CTX_RE["s_soletta"].search(ctx);   out["s_soletta"] = int(m.group(1)) if m else None
    m = CTX_RE["vled"].search(ctx);        out["vled"]      = float(m.group(1).replace(",", ".")) if m else None
    m = CTX_RE["cls"].search(ctx);         out["cls"]       = m.group(1).upper() if m else None
    m = CTX_RE["passo"].search(ctx);       out["passo"]     = int(m.group(1)) if m else None
    m = CTX_RE["dir"].search(ctx);         out["dir"]       = m.group(1).lower() if m else None
    m = CTX_RE["s_long"].search(ctx);      out["s_long"]    = int(m.group(1)) if m else None
    m = CTX_RE["piena"].search(ctx);       out["piena"]     = True if m else False
    # pulizia None
    return {k:v for k,v in out.items() if v is not None}

def missing_ctf_keys(parsed: dict) -> list[str]:
    if parsed.get("piena"):  # per soletta piena, non serve h_lamiera/dir/passo
        needed = ["s_soletta", "vled", "cls", "s_long"]
        return [k for k in needed if k not in parsed]
    return [k for k in CRITICAL_CTF_KEYS if k not in parsed]

# ==============================
# 3) Lettura PRd + scelta altezza
# ==============================
def load_ctf_db():
    path = os.path.join(app.static_folder, "data", "ctf_prd.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

PRD_DB = load_ctf_db()

def choose_ctf_height(params: dict, safety=1.10):
    """
    Ritorna: (height_mm, n_per_m, cap_per_m, demand_per_m, utilization, safety, note)
    - prova ordine:
      1) Tabelle per combinazione Hxx/dir/passo/cls
      2) Blocco "soletta_piena" → cls
      3) Regola "lamiera_rule" (P0 × k_t) → serve hp, b0, t, nr ⇒ solo PRd base
    """
    demand = float(params["vled"])            # kN/m
    s_long = float(params["s_long"])          # mm
    n_per_m = 1000.0 / s_long if s_long > 0 else 0.0

    # 3.1) Tabella per combinazione completa (se c'è H e dir/passo)
    H = None
    if "h_lamiera" in params:
        H = f"H{params['h_lamiera']}"
    dirn = params.get("dir")
    passo_gola = f"passo_gola_{params['passo']}" if "passo" in params else None
    cls = params.get("cls")

    if H and dirn and passo_gola and cls:
        try:
            leaf = PRD_DB[H][dirn][passo_gola][cls]  # dict: {"CTF_060": PRd, ...}
            result = _pick_height_from_leaf(leaf, n_per_m, demand, safety)
            if result: return result + (None,)  # note None
        except Exception:
            pass  # combina con fallback

    # 3.2) Soletta piena (se segnalata o se non si trova la 3.1 ma c'è la tabella)
    if params.get("piena") or ("soletta_piena" in PRD_DB and cls in PRD_DB["soletta_piena"]):
        try:
            leaf = PRD_DB["soletta_piena"][cls]  # dict: {"CTF_060": PRd, ...}
            result = _pick_height_from_leaf(leaf, n_per_m, demand, safety)
            if result: return result + (None,)
        except Exception:
            pass

    # 3.3) Regola lamiera (P0 × k_t) → serve set parametri lamiera per k_t definitivo
    rule = PRD_DB.get("lamiera_rule", {})
    P0 = rule.get("P0", {}).get(cls)
    if P0:
        # Semplificazione: k_t max consentito dalla tabella limiti (placeholder finché non chiediamo hp/b0/t/nr dal wizard)
        kt_max = 0.0
        for lim in rule.get("kt_limits", []):
            kt_max = max(kt_max, float(lim.get("kt_max", 0.0)))
        prd_base = P0 * kt_max  # kN per connettore (base)
        cap_per_m = prd_base * n_per_m
        # con regola base non differenziamo per altezza: non possiamo indicare "h=.."
        return ("da determinare (mancano hp, b0, t, nr per lamiera)", n_per_m, cap_per_m, demand,
                (demand / cap_per_m) if cap_per_m else None, safety,
                "Per lamiera grecata serve completare i parametri: altezza profilo (hp), larghezza gola (b0), spessore lamiera (t), n° connettori per gola (nr).")

    # 3.4) Nessun dato disponibile
    return ("da determinare (manca combinazione nelle tabelle PRd)", n_per_m, None, demand, None, safety, None)

def _pick_height_from_leaf(leaf: dict, n_per_m: float, demand: float, safety: float):
    # leaf: {"CTF_060": PRd, ...}
    if not isinstance(leaf, dict) or not leaf: return None
    # ordina per altezza crescente
    try:
        candidates = sorted(leaf.items(), key=lambda kv: int(kv[0].split("_")[1]))
    except:
        candidates = list(leaf.items())
    for name, prd_one in candidates:
        cap_per_m = float(prd_one) * n_per_m
        if cap_per_m >= demand * safety:
            height_mm = name.replace("CTF_", "")
            utilization = demand / cap_per_m if cap_per_m else None
            return (height_mm, n_per_m, cap_per_m, demand, utilization, safety)
    return None

# ==============================
# 4) Template risposte A/B/C
# ==============================
def tpl_ctf_calc(mode: str, p: dict, h_caption: str, note: str | None = None) -> str:
    if mode == "breve":
        base = (
            f"Per H{p.get('h_lamiera','—')} / soletta {p.get('s_soletta','—')} mm, "
            f"V_L,Ed={p.get('vled','—')} kN/m, cls {p.get('cls','—')}: consiglio CTF {h_caption}."
        )
        return base + (f" {note}" if note else "")
    if mode == "standard":
        base = (
            f"Dati: H{p.get('h_lamiera','—')}, soletta {p.get('s_soletta','—')} mm, "
            f"passo gola {p.get('passo','—')} mm, lamiera {p.get('dir','—')}, "
            f"passo lungo trave {p.get('s_long','—')} mm, V_L,Ed={p.get('vled','—')} kN/m, cls {p.get('cls','—')}.\n"
            f"Conclusione: CTF {h_caption} (criterio: capacità per metro ≥ domanda con margine)."
        )
        return base + (f"\nNota: {note}" if note else "")
    # dettagliata
    body = (
        "1) Dati di input:\n"
        f"   - Lamiera H{p.get('h_lamiera','—')} (direzione {p.get('dir','—')}), passo gola {p.get('passo','—')} mm\n"
        f"   - Soletta {p.get('s_soletta','—')} mm, passo lungo trave {p.get('s_long','—')} mm\n"
        f"   - V_L,Ed={p.get('vled','—')} kN/m; cls {p.get('cls','—')}\n\n"
        "2) Procedura:\n"
        "   - PRd per connettore da tabelle/ETA (oppure da regola P0×k_t per lamiera) in funzione di H lamiera, direzione, passo in gola e cls.\n"
        "   - Capacità per metro = PRd × n°/m (n°/m = 1000 / passo lungo trave).\n"
        "   - Criterio: capacità per metro ≥ V_L,Ed × margine.\n\n"
        f"3) Esito: CTF {h_caption}.\n"
        "4) Riferimenti: ETA-18/0447, EC4; posa P560 secondo manuale."
    )
    return body + (f"\n5) Note: {note}" if note else "")

def tpl_ctf_posa(mode: str) -> str:
    if mode == "breve":
        return "Posa CTF: fissaggio su trave attraverso lamiera; segui manuale Tecnaria. DPI obbligatori."
    if mode == "standard":
        return ("Posa CTF: allineamento sul corrugamento; fissaggio su trave tramite lamiera; "
                "controllo passi e centrature; uso P560 come da manuale Tecnaria.")
    return (
        "Posa CTF — linee tecniche:\n"
        "1) Tracciamento interassi/zone di accumulo.\n"
        "2) Fissaggio su trave attraverso lamiera (centratura, quote).\n"
        "3) Controllo passi in gola e vincoli.\n"
        "4) Getto, stagionatura, ispezioni.\n"
        "Riferimenti: manuale posa Tecnaria, DPI, chiodatrice P560."
    )

def tpl_generic(topic: str, intent: str, mode: str) -> str:
    base = {
        "CTF": "Connettori CTF per solai collaboranti acciaio–calcestruzzo.",
        "CTL": "Connettori CTL per sistemi collaboranti acciaio–legno.",
        "CEME": "CEM-E per collegare cls esistente a nuovo getto.",
        "DIAPASON": "Diapason per rinforzo/adeguamento di solai esistenti.",
        "P560": "Chiodatrice a polvere SPIT P560 per posa connettori e fissaggi."
    }
    if mode == "breve":
        return base.get(topic, "Prodotto Tecnaria.")
    if mode == "standard":
        return f"{base.get(topic,'Prodotto Tecnaria.')} ({intent.title()})"
    return f"{base.get(topic,'Prodotto Tecnaria.')} Intento: {intent.title()} — dettagli disponibili a richiesta."

# ==============================
# 5) Allegati
# ==============================
def tool_attachments(topic: str, intent: str) -> list[dict]:
    out = []
    if topic == "P560":
        out.append({"label": "Foto P560 (magazzino)", "href": "/static/img/p560_magazzino.jpg"})
    if topic == "CTF" and intent == "POSA":
        out.append({"label": "Nota di posa CTF (PDF)", "href": "/static/docs/ctf_posa.pdf"})
    return out

# ==============================
# 6) KB sintetico (risposte non vuote)
# ==============================
KB = {
    "CTF": {
        "breve":   "Connettori per solai collaboranti acciaio–calcestruzzo; posa su trave attraverso lamiera.",
        "standard":"CTF: connettori per solai collaboranti acciaio–calcestruzzo. Compatibilità lamiera grecata; posa P560. Consultare ETA-18/0447.",
        "dettagliata":(
            "CTF — Connettori per solai collaboranti acciaio–calcestruzzo.\n"
            "• impiego: travi acciaio + lamiera grecata + soletta cls;\n"
            "• posa: fissaggio su trave attraverso lamiera, P560;\n"
            "• verifiche: PRd da ETA/tabelle o P0×k_t, capacità per metro vs V_L,Ed;\n"
            "• riferimenti: ETA-18/0447, EC4, manuale posa."
        )
    },
    "CTL": {
        "breve":   "Connettori per sistemi collaboranti acciaio–legno.",
        "standard":"CTL: progettazione con tabelle Tecnaria; posa con verifiche di ancoraggio e copriferro.",
        "dettagliata":"CTL — Dati minimi: specie legno/lamellare, spessore soletta, interassi; verifiche secondo tabelle Tecnaria e norme EC5/EC4 parziali; posa con DPI."
    },
    "CEME": {
        "breve":   "Connettore per collegare cls esistente a nuovo getto.",
        "standard":"CEM-E: collegamento cls esistente/nuovo; verifiche e posa da manuale Tecnaria.",
        "dettagliata":"CEM-E — Scelta/posa secondo tabelle Tecnaria; verifiche di ancoraggio e dettagli costruttivi."
    },
    "DIAPASON": {
        "breve":   "Sistema per rinforzo di solai esistenti.",
        "standard":"Diapason: rinforzo/adeguamento; verifiche e posa da manuale Tecnaria.",
        "dettagliata":"Diapason — Rinforzo solai: indicazioni d’uso, verifica e posa secondo manuale e tabelle."
    },
    "P560": {
        "breve":   "Chiodatrice a polvere SPIT P560 per posa connettori e fissaggi.",
        "standard":"P560: fissaggi rapidi su acciaio/calcestruzzo; DPI; cariche/chiodi secondo manuale.",
        "dettagliata":"P560 — Impiego, scelta cariche/chiodi, DPI, manutenzione; vedi manuale Tecnaria."
    }
}

def kb_answer(topic_key: str, mode_key: str) -> str:
    block = KB.get(topic_key, {})
    return block.get(mode_key, block.get("standard", "Prodotto Tecnaria."))

# ==============================
# 7) ROUTES
# ==============================
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/api/answer", methods=["POST"])
def api_answer():
    data = request.get_json(force=True) or {}
    q_raw = (data.get("question") or "").strip()
    q = q_raw.lower()
    mode = (data.get("mode") or "dettagliata").strip().lower()
    context = (data.get("context") or "").strip()

    # Fuori scope / denylist
    if contains_denylist(q):
        return jsonify({
            "answer": "Questo assistente è dedicato esclusivamente a prodotti e servizi Tecnaria S.p.A.",
            "meta": {"needs_params": False, "required_keys": []}
        })

    topic = detect_topic(q)
    intent = detect_intent(q)

    # Topic non riconosciuto → messaggio chiaro
    if topic is None:
        return jsonify({
            "answer": "Assistente dedicato a prodotti e servizi Tecnaria S.p.A. (CTF/CTL/CEM-E/Diapason, P560).",
            "meta": {"needs_params": False, "required_keys": []}
        })

    # CTF + CALC → servono parametri
    if topic == "CTF" and intent == "CALC":
        parsed = parse_ctf_context(context)
        missing = missing_ctf_keys(parsed)
        if missing:
            labels = [UI_LABELS[k] for k in missing]
            return jsonify({
                "answer": "Per procedere servono: " + ", ".join(labels) + ".",
                "meta": {"needs_params": True, "required_keys": labels}
            })

        height_mm, n_per_m, cap_per_m, demand, utilization, safety, note = choose_ctf_height(parsed)
        h_caption = f"h={height_mm} mm" if height_mm and str(height_mm).isdigit() else str(height_mm)
        ans = tpl_ctf_calc(mode, parsed, h_caption, note)

        meta_calc = {
            "height_mm": height_mm,
            "n_per_m": round(n_per_m, 3) if n_per_m else None,
            "cap_per_m": round(cap_per_m, 2) if cap_per_m else None,
            "demand_per_m": round(demand, 2) if demand is not None else None,
            "utilization": round(utilization, 3) if isinstance(utilization,(int,float)) else None,
            "safety": safety
        }

        return jsonify({
            "answer": ans,
            "meta": {"needs_params": False, "required_keys": [], "calc": meta_calc},
            "attachments": tool_attachments(topic, intent)
        })

    # CTF + POSA
    if topic == "CTF" and intent == "POSA":
        return jsonify({
            "answer": tpl_ctf_posa(mode),
            "meta": {"needs_params": False, "required_keys": []},
            "attachments": tool_attachments(topic, intent)
        })

    # CTL / CEME / DIAPASON / P560 → scheda informativa coerente con A/B/C
    info = kb_answer(topic, mode) or kb_answer(topic, "standard")
    return jsonify({
        "answer": info,
        "meta": {"needs_params": False, "required_keys": []},
        "attachments": tool_attachments(topic, intent)
    })

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
