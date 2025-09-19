# app.py — TecnariaBot backend v1.1
# Flask app con helper per: intent/topic detection, parsing CTF, required dinamici,
# template A/B/C e allegati tecnici.

from flask import Flask, render_template, request, jsonify
import re

app = Flask(__name__, static_folder="static", template_folder="templates")

# ==============================
# 0) Costanti: whitelist/denylist
# ==============================
TECNARIA_TOPICS = {"CTF", "CTL", "CEME", "DIAPASON", "P560", "P800"}
# Denylist marchi/prodotti non Tecnaria
DENYLIST = {
    "hbv", "x-hbv", "xhbv", "fva", "x-hibond", "hi-bond", "hibond", "ribdeck", "sherpa",
    "lindapter", "hilti shear", "x-fcm", "x-hcc"
}

# ============================
# 1) Riconoscimento TOPIC/INTENT
# ============================
def detect_topic(q: str) -> str | None:
    t = q.lower()
    if any(k in t for k in [" p560", "p560 ", "chiodatrice", "spit p560"]):
        return "P560"
    if any(k in t for k in [" p800", "p800 ", "spit p800"]):
        return "P800"
    if any(k in t for k in ["diapason"]):
        return "DIAPASON"
    if any(k in t for k in ["cem-e", "ceme", "cem e"]):
        return "CEME"
    if any(k in t for k in ["ctl", "legno"]):
        return "CTL"
    if any(k in t for k in ["ctf", "connettore", "connettori", "lamiera", "solaio", "soletta", "gola"]):
        return "CTF"
    return None

def detect_intent(q: str) -> str:
    """CALC: dimensionamento/scelta; POSA: istruzioni; CONFRONTO; INFO: descrizione generale."""
    t = q.lower()
    calc_hints = [
        "altezza", "altezze", "dimension", "dimensiona", "dimensionamento", "v_l", "v l",
        "v_l,ed", "kn/m", "portata", "quanti", "numero connettori", "pr d", "pr_d", "pr,d"
    ]
    posa_hints = ["posa", "posare", "installazione", "fissare", "utilizzo", "uso in cantiere"]
    conf_hints = ["differenza", "vs", "confronto", "meglio", "alternativa"]

    if any(k in t for k in calc_hints):
        return "CALC"
    if any(k in t for k in posa_hints):
        return "POSA"
    if any(k in t for k in conf_hints):
        return "CONFRONTO"
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
}

CRITICAL_CTF_KEYS = ["h_lamiera", "s_soletta", "vled", "cls", "passo", "dir"]
UI_LABELS = {
    "h_lamiera": "altezza lamiera",
    "s_soletta": "spessore soletta",
    "vled": "V_L,Ed",
    "cls": "cls",
    "passo": "passo gola",
    "dir": "direzione lamiera",
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
    if m:
        out["vled"] = float(m.group(1).replace(",", "."))
    m = CTX_RE["cls"].search(ctx)
    if m: out["cls"] = m.group(1).upper()
    m = CTX_RE["passo"].search(ctx)
    if m: out["passo"] = int(m.group(1))
    m = CTX_RE["dir"].search(ctx)
    if m: out["dir"] = m.group(1).lower()
    return out

def missing_ctf_keys(parsed: dict) -> list[str]:
    return [k for k in CRITICAL_CTF_KEYS if k not in parsed]

# ============================
# 3) Stub calcolo/suggerimento CTF
# ============================
def suggest_ctf_height(params: dict) -> str:
    """
    Placeholder sicuro: segnala che la selezione numerica richiede tabelle/ETA interne.
    Integrare qui la logica reale (lookup PRd, verifica a taglio/scorr.).
    """
    # esempio di nota: puoi personalizzarla
    return "da determinare (richiede verifica su tabelle ETA/PRd Tecnaria)"

# ============================
# 4) Template risposte A/B/C
# ============================
def tpl_ctf_calc(mode: str, p: dict, h_suggerita: str) -> str:
    if mode == "breve":
        return (
            f"Per lamiera H{p['h_lamiera']} e soletta {p['s_soletta']} mm, con V_L,Ed={p['vled']} kN/m e cls {p['cls']}, "
            f"la scelta dell’altezza CTF è {h_suggerita}."
        )
    if mode == "standard":
        return (
            f"In base ai dati forniti (lamiera H{p['h_lamiera']}, soletta {p['s_soletta']} mm, passo gola {p['passo']} mm, "
            f"lamiera {p['dir']}, V_L,Ed={p['vled']} kN/m, cls {p['cls']}), l’altezza del connettore CTF risulta {h_suggerita}.\n"
            f"Nota: la definizione numerica richiede il confronto con le tabelle PRd/ETA Tecnaria."
        )
    # dettagliata
    return (
        "1) Dati di input riconosciuti:\n"
        f"   - Lamiera: H{p['h_lamiera']} (direzione {p['dir']})\n"
        f"   - Soletta collaborante: {p['s_soletta']} mm\n"
        f"   - Passo gola: {p['passo']} mm\n"
        f"   - V_L,Ed: {p['vled']} kN/m; cls: {p['cls']}\n\n"
        "2) Procedura di verifica (sintesi):\n"
        "   - Selezione PRd connettore da ETA/tabelle in funzione di lamiera/direzione/passo.\n"
        "   - Calcolo capacità per metro e confronto con V_L,Ed.\n"
        "   - Verifica dettagli costruttivi (interasse, zone d’estremità, ancoraggi).\n\n"
        f"3) Esito tecnico: altezza CTF {h_suggerita}.\n"
        "   (Determinazione numerica subordinata a verifica su tabelle PRd/ETA Tecnaria.)\n"
        "4) Riferimenti: ETA-18/0447, EC4; posa tramite chiodatrice P560/P800 secondo manuale Tecnaria."
    )

def tpl_ctf_posa(mode: str) -> str:
    if mode == "breve":
        return "Posa CTF: fissaggio su trave attraverso lamiera, seguendo manuale Tecnaria. DPI obbligatori."
    if mode == "standard":
        return ("Posa CTF: allinea sul corrugamento conforme al passo gola; fissa su trave tramite lamiera; "
                "controlla coppie e centratura. Usa P560/P800 come da manuale.")
    return (
        "Posa CTF — istruzioni tecniche:\n"
        "1) Tracciamento interassi e zone di accumulo.\n"
        "2) Fissaggio su trave attraverso lamiera (verifica centratura/coppia).\n"
        "3) Controllo passi in gola e rispettivi vincoli.\n"
        "4) Getto e stagionatura, ispezioni e collaudi.\n"
        "Riferimenti: manuale posa Tecnaria, DPI, P560/P800."
    )

def tpl_generic(topic: str, intent: str, mode: str) -> str:
    base = {
        "CTF": "Connettori CTF per solai collaboranti acciaio–calcestruzzo.",
        "CTL": "Connettori CTL per sistemi acciaio–legno.",
        "CEME": "CEM-E per collegamenti calcestruzzo esistente/nuovo.",
        "DIAPASON": "Diapason per rinforzo/adeguamento solai esistenti.",
        "P560": "Chiodatrice a polvere SPIT P560 per posa connettori e fissaggi.",
        "P800": "Chiodatrice SPIT P800 per impieghi intensivi."
    }
    if mode == "breve":
        return base.get(topic, "Prodotto Tecnaria.")
    if mode == "standard":
        return f"{base.get(topic,'Prodotto Tecnaria.')} ({intent.title()})"
    # dettagliata
    return f"{base.get(topic,'Prodotto Tecnaria.')} Intento: {intent.title()} — dettagli disponibili a richiesta."

# ============================
# 5) Allegati / Note tecniche
# ============================
def tool_attachments(topic: str, intent: str) -> list[dict]:
    out = []
    if topic == "P560":
        # se esiste il file nel tuo static/img aggiungerà il link
        out.append({"label": "Foto magazzino P560", "href": "/static/img/p560_magazzino.jpg"})
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
    q = (data.get("question") or "").strip()
    ql = q.lower()
    mode = (data.get("mode") or "dettagliata").strip().lower()
    context = (data.get("context") or "").strip()

    # Blocca subito temi non Tecnaria
    if contains_denylist(ql):
        return jsonify({
            "answer": "Questo assistente è dedicato esclusivamente a prodotti e servizi Tecnaria S.p.A.",
            "meta": {"needs_params": False, "required_keys": []}
        })

    topic = detect_topic(ql)
    intent = detect_intent(ql)

    # Se non è un tema Tecnaria riconosciuto
    if topic not in TECNARIA_TOPICS and topic is not None:
        return jsonify({
            "answer": "Questo assistente tratta solo prodotti Tecnaria (CTF/CTL/CEM-E/Diapason, P560/P800, ecc.).",
            "meta": {"needs_params": False, "required_keys": []}
        })
    if topic is None:
        # fuori ambito chiaro
        return jsonify({
            "answer": "Assistente dedicato a prodotti e servizi Tecnaria S.p.A.",
            "meta": {"needs_params": False, "required_keys": []}
        })

    # Branch CTF — CALC: richiedi parametri se mancano
    if topic == "CTF" and intent == "CALC":
        parsed = parse_ctf_context(context)
        missing = missing_ctf_keys(parsed)
        if missing:
            labels = [UI_LABELS[k] for k in missing]
            return jsonify({
                "answer": "Per procedere servono: " + ", ".join(labels) + ".",
                "meta": {"needs_params": True, "required_keys": labels}
            })

        # Tutti i parametri presenti → prepara risposta A/B/C
        h_suggerita = suggest_ctf_height(parsed)
        answer_text = tpl_ctf_calc(mode, parsed, h_suggerita)
        return jsonify({
            "answer": answer_text,
            "meta": {"needs_params": False, "required_keys": []},
            "attachments": tool_attachments(topic, intent)
        })

    # CTF — POSA (nessun parametro obbligatorio)
    if topic == "CTF" and intent == "POSA":
        return jsonify({
            "answer": tpl_ctf_posa(mode),
            "meta": {"needs_params": False, "required_keys": []},
            "attachments": tool_attachments(topic, intent)
        })

    # Altri topic/intent (INFO/CONFRONTO)
    return jsonify({
        "answer": tpl_generic(topic, intent, mode),
        "meta": {"needs_params": False, "required_keys": []},
        "attachments": tool_attachments(topic, intent)
    })

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
