# app.py — TecnariaBot backend v1.3 (senza P800)
# Flask app con: intent/topic detection, parsing CTF, required dinamici, template A/B/C,
# allegati tecnici, fallback informativo e "gancio" PRd per scelta altezza CTF.

from flask import Flask, render_template, request, jsonify
import re, os, json

app = Flask(__name__, static_folder="static", template_folder="templates")

# ==============================
# 0) Costanti: whitelist/denylist
# ==============================
TECNARIA_TOPICS = {"CTF", "CTL", "CEME", "DIAPASON", "P560"}  # P800 rimosso
DENYLIST = {
    # marchi/sistemi NON Tecnaria: blocco
    "hbv", "x-hbv", "xhbv", "fva", "hi-bond", "hibond", "ribdeck", "sherpa",
    "lindapter", "hilti shear", "x-fcm", "x-hcc", "p800"  # P800 esplicitamente in denylist
}

# ============================
# 1) Riconoscimento TOPIC/INTENT
# ============================
def detect_topic(q: str) -> str | None:
    t = q.lower()
    if any(k in t for k in [" p560", "p560 ", "chiodatrice", "spit p560"]):
        return "P560"
    # P800 rimosso (non Tecnaria)
    if "diapason" in t:
        return "DIAPASON"
    if any(k in t for k in ["cem-e", "ceme", "cem e"]):
        return "CEME"
    if any(k in t for k in ["ctl", "acciaio-legno", "acciaio legno", "legno"]):
        return "CTL"
    if any(k in t for k in ["ctf", "connettore", "connettori", "lamiera", "solaio", "soletta", "gola", "passo gola"]):
        return "CTF"
    return None

def detect_intent(q: str) -> str:
    """CALC: dimensionamento/scelta; POSA: istruzioni; CONFRONTO; INFO: descrizione generale."""
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

# ============================
# 2) Parsing CTF: context -> dict
# ============================
CTX_RE = {
    "h_lamiera": re.compile(r"lamiera\s*h?\s*(\d+)", re.I),
    "s_soletta": re.compile(r"soletta\s*(\d+)\s*mm", re.I),
    "vled":      re.compile(r"v[\s_.,-]*l\s*,?ed\s*=\s*([\d.,]+)\s*kn/?m", re.I),
    "cls":       re.compile(r"cls\s*([Cc]\d+\/\d+)", re.I),
    "passo":     re.compile(r"passo\s*gola\s*(\d+)\s*mm", re.I),
    "dir":       re.compile(r"lamiera\s*(longitudinale|trasversale)", re.I),
    "s_long":    re.compile(r"passo\s+lungo\s+trave\s*(\d+)\s*mm", re.I),  # passo lungo trave
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
    if not ctx:
        return out
    m = CTX_RE["h_lamiera"].search(ctx)
    if m: out["h_lamiera"] = int(m.group(1))
    m = CTX_RE["s_soletta"].search(ctx)
    if m: out["s_soletta"] = int(m.group(1))
    m = CTX_RE["vled"].search(ctx)
    if m: out["vled"] = float(m.group(1).replace(",", "."))
    m = CTX_RE["cls"].search(ctx)
    if m: out["cls"] = m.group(1).upper()
    m = CTX_RE["passo"].search(ctx)
    if m: out["passo"] = int(m.group(1))
    m = CTX_RE["dir"].search(ctx)
    if m: out["dir"] = m.group(1).lower()
    m = CTX_RE["s_long"].search(ctx)
    if m: out["s_long"] = int(m.group(1))
    return out

def missing_ctf_keys(parsed: dict) -> list[str]:
    return [k for k in CRITICAL_CTF_KEYS if k not in parsed]

# ===========================================
# 3) Tabelle PRd (opzionale) + scelta altezza
# ===========================================
def load_ctf_prd():
    """
    Carica le PRd (kN per connettore) da /static/data/ctf_prd.json
    Struttura attesa (esempio):
    {
      "H55": {
        "longitudinale": {
          "passo_gola_150": {
            "C30/37": { "CTF_60": 12.3, "CTF_75": 16.1, "CTF_90": 19.8 }
          }
        }
      }
    }
    """
    path = os.path.join(app.static_folder, "data", "ctf_prd.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

PRD_TABLE = load_ctf_prd()

def choose_ctf_height(params: dict, safety=1.10):
    """
    Restituisce (height_mm, n_per_m, cap_per_m, demand_per_m, utilization, safety).
    Se mancano i dati nelle tabelle → height_mm = "da determinare".
    """
    H = f"H{params['h_lamiera']}"
    dirn = params["dir"]
    passo_gola = f"passo_gola_{params['passo']}"
    cls = params["cls"]
    v_required = float(params["vled"])       # kN/m (domanda)
    s_long = float(params["s_long"])         # mm
    n_per_m = 1000.0 / s_long                # connettori per metro

    try:
        leaf = PRD_TABLE[H][dirn][passo_gola][cls]  # dict: {"CTF_60": PRd, ...}
    except Exception:
        return ("da determinare (manca combinazione nelle tabelle PRd)", n_per_m, None, v_required, None, safety)

    candidates = sorted(leaf.items(), key=lambda kv: int(kv[0].split("_")[1]))
    for name, prd_one in candidates:
        cap_per_m = float(prd_one) * n_per_m          # kN/m (offerta)
        if cap_per_m >= v_required * safety:
            height_mm = name.replace("CTF_", "")      # "60"/"75"/"90"
            utilization = v_required / cap_per_m
            return (height_mm, n_per_m, cap_per_m, v_required, utilization, safety)

    return ("da determinare (nessuna altezza soddisfa la domanda con i dati correnti)", n_per_m, None, v_required, None, safety)

# ============================
# 4) Template risposte A/B/C
# ============================
def tpl_ctf_calc(mode: str, p: dict, h_caption: str) -> str:
    if mode == "breve":
        return (
            f"Per lamiera H{p['h_lamiera']} e soletta {p['s_soletta']} mm, con V_L,Ed={p['vled']} kN/m e cls {p['cls']}, "
            f"consiglio CTF {h_caption}."
        )
    if mode == "standard":
        return (
            f"Dati: H{p['h_lamiera']}, soletta {p['s_soletta']} mm, passo gola {p['passo']} mm, lamiera {p['dir']}, "
            f"passo lungo trave {p['s_long']} mm, V_L,Ed={p['vled']} kN/m, cls {p['cls']}.\n"
            f"Conclusione: CTF {h_caption} (criterio: capacità per metro ≥ domanda con margine)."
        )
    return (
        "1) Dati di input:\n"
        f"   - Lamiera H{p['h_lamiera']} (direzione {p['dir']}), passo gola {p['passo']} mm\n"
        f"   - Soletta {p['s_soletta']} mm, passo lungo trave {p['s_long']} mm\n"
        f"   - V_L,Ed={p['vled']} kN/m; cls {p['cls']}\n\n"
        "2) Procedura:\n"
        "   - PRd per connettore da tabelle/ETA in funzione di H lamiera, direzione, passo in gola e cls.\n"
        "   - Capacità per metro = PRd × n°/m (n°/m = 1000 / passo lungo trave).\n"
        "   - Criterio: capacità per metro ≥ V_L,Ed × margine.\n\n"
        f"3) Esito: CTF {h_caption}.\n"
        "4) Riferimenti: ETA-18/0447, EC4; posa P560 secondo manuale."
    )

def tpl_ctf_posa(mode: str) -> str:
    if mode == "breve":
        return "Posa CTF: fissaggio su trave attraverso lamiera; segui manuale Tecnaria. DPI obbligatori."
    if mode == "standard":
        return ("Posa CTF: allineamento sul corrugamento; fissaggio su trave tramite lamiera; "
                "controllo passi e centrature; uso P560 come da manuale Tecnaria.")
    return (
        "Posa CTF — linee tecniche:\n"
        "1) Tracciamento interassi/zone di accumulo.\n"
        "2) Fissaggio su trave attraverso lamiera (centratura, coppie/quote).\n"
        "3) Controllo passi in gola e vincoli.\n"
        "4) Getto, stagionatura, ispezioni.\n"
        "Riferimenti: manuale posa Tecnaria, DPI, chiodatrice P560."
    )

def tpl_generic(topic: str, intent: str, mode: str) -> str:
    base = {
        "CTF": "Connettori CTF per solai collaboranti acciaio–calcestruzzo.",
        "CTL": "Connettori CTL per sistemi collaboranti acciaio–legno.",
        "CEME": "CEM-E per collegare calcestruzzo esistente a nuovo getto.",
        "DIAPASON": "Diapason per rinforzo/adeguamento di solai esistenti.",
        "P560": "Chiodatrice a polvere SPIT P560 per posa connettori e fissaggi."
    }
    if mode == "breve":
        return base.get(topic, "Prodotto Tecnaria.")
    if mode == "standard":
        return f"{base.get(topic,'Prodotto Tecnaria.')} ({intent.title()})"
    return f"{base.get(topic,'Prodotto Tecnaria.')} Intento: {intent.title()} — dettagli disponibili a richiesta."

# ============================
# 5) Allegati / Note tecniche
# ============================
def tool_attachments(topic: str, intent: str) -> list[dict]:
    out = []
    if topic == "P560":
        out.append({"label": "Foto P560 (magazzino)", "href": "/static/img/p560_magazzino.jpg"})
    if topic == "CTF" and intent == "POSA":
        out.append({"label": "Nota di posa CTF (PDF)", "href": "/static/docs/ctf_posa.pdf"})
    return out

# ============================
# 6) ROUTES
# ============================
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

    # Blocca temi non Tecnaria
    if contains_denylist(q):
        return jsonify({
            "answer": "Questo assistente è dedicato esclusivamente a prodotti e servizi Tecnaria S.p.A.",
            "meta": {"needs_params": False, "required_keys": []}
        })

    topic = detect_topic(q)
    intent = detect_intent(q)

    # KB locale per risposte semplici (mai vuoto)
    KB = {
        "CTF": {
            "breve":   "Connettori per solai collaboranti acciaio–calcestruzzo; posa su trave attraverso lamiera.",
            "standard":"CTF: connettori per solai collaboranti acciaio–calcestruzzo. Compatibilità lamiera grecata; posa P560. Consultare ETA-18/0447.",
            "dettagliata":(
                "CTF — Connettori per solai collaboranti acciaio–calcestruzzo.\n"
                "• impiego: travi acciaio + lamiera grecata + soletta cls;\n"
                "• posa: fissaggio su trave attraverso lamiera, P560;\n"
                "• verifiche: PRd da ETA/tabelle, capacità per metro vs V_L,Ed;\n"
                "• riferimenti: ETA-18/0447, EC4, manuale posa."
            )
        },
        "CTL": {
            "breve":   "Connettori per sistemi collaboranti acciaio–legno.",
            "standard":"CTL: connettori per acciaio–legno. Posa e verifiche secondo manuale Tecnaria.",
            "dettagliata":"CTL — Progettazione con tabelle dedicate Tecnaria; posa secondo manuale, DPI e controlli."
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

    # Fuori ambito
    if topic is None:
        return jsonify({
            "answer": "Assistente dedicato a prodotti e servizi Tecnaria S.p.A. (CTF/CTL/CEM-E/Diapason, P560).",
            "meta": {"needs_params": False, "required_keys": []}
        })

    # --- CTF + CALC: parametri obbligatori (wizard) ---
    if topic == "CTF" and intent == "CALC":
        parsed = parse_ctf_context(context)
        missing = missing_ctf_keys(parsed)
        if missing:
            labels = [UI_LABELS[k] for k in missing]
            return jsonify({
                "answer": "Per procedere servono: " + ", ".join(labels) + ".",
                "meta": {"needs_params": True, "required_keys": labels}
            })

        height_mm, n_per_m, cap_per_m, demand, utilization, safety = choose_ctf_height(parsed)
        h_caption = f"h={height_mm} mm" if height_mm and str(height_mm).isdigit() else height_mm
        ans = tpl_ctf_calc(mode, parsed, h_caption)

        meta_calc = {
            "height_mm": height_mm,
            "n_per_m": round(n_per_m, 3) if n_per_m else None,
            "cap_per_m": round(cap_per_m, 2) if cap_per_m else None,
            "demand_per_m": round(demand, 2) if demand is not None else None,
            "utilization": round(utilization, 3) if utilization else None,
            "safety": safety
        }

        return jsonify({
            "answer": ans,
            "meta": {"needs_params": False, "required_keys": [], "calc": meta_calc},
            "attachments": tool_attachments(topic, intent)
        })

    # --- CTF + POSA ---
    if topic == "CTF" and intent == "POSA":
        return jsonify({
            "answer": tpl_ctf_posa(mode),
            "meta": {"needs_params": False, "required_keys": []},
            "attachments": tool_attachments(topic, intent)
        })

    # --- Altri topic/intent → scheda informativa (mai vuoto) ---
    info = kb_answer(topic, mode)
    attachments = tool_attachments(topic, intent)
    if not info:
        info = kb_answer(topic, "standard")
    return jsonify({
        "answer": info,
        "meta": {"needs_params": False, "required_keys": []},
        "attachments": attachments
    })

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
