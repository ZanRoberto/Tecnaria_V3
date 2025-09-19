import json
from flask import Flask, render_template, request, jsonify

app = Flask(__name__, static_folder="static", template_folder="templates")


# -----------------------
# Helper per calcolo CTF
# -----------------------

def choose_ctf_height(inputs):
    lamiera = inputs.get("lamiera", "").lower()
    soletta = inputs.get("soletta")
    VLed = float(inputs.get("VLed", 0))
    cls = inputs.get("cls", "")
    s_gola = inputs.get("s_gola")
    direzione = inputs.get("dir", "")
    s_long = float(inputs.get("s_long", 0))

    # Carica database PRd
    try:
        with open("static/data/ctf_prd.json", "r", encoding="utf-8") as f:
            db = json.load(f)
    except Exception as e:
        return f"Errore caricamento database PRd: {e}"

    # Caso soletta piena
    if "piena" in lamiera:
        table = db.get("soletta_piena", {}).get(cls, {})
        if not table:
            return f"Nessuna tabella disponibile per cls {cls}"
        n_per_m = 1000.0 / s_long if s_long > 0 else 0
        results = []
        for ctf, prd in table.items():
            cap = prd * n_per_m
            results.append((ctf, cap))
        results.sort(key=lambda x: x[1])
        for ctf, cap in results:
            if cap >= VLed:
                return f"Consiglio: {ctf} con capacità {cap:.1f} ≥ domanda {VLed:.1f} kN/m"
        return "Nessuna altezza soddisfa la verifica."

    # Caso lamiera grecata (TR60, TR80, HiBond ecc.)
    else:
        rule = db.get("lamiera_rule", {})
        P0 = rule.get("P0", {}).get(cls)
        if not P0:
            return f"Nessun valore P0 disponibile per cls {cls}"
        # semplificazione iniziale: prendi il k_t max
        kt = max([lim.get("kt_max") for lim in rule.get("kt_limits", [])])
        PRd = P0 * kt
        return f"Calcolo lamiera {lamiera}: PRd base {PRd:.1f} kN per connettore (da completare con parametri hp, b0, t, nr)"


# -----------------------
# Rotte Flask
# -----------------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/answer", methods=["POST"])
def api_answer():
    data = request.get_json(force=True) or {}
    question = (data.get("question") or "").lower()
    mode = (data.get("mode") or "dettagliata")
    context = (data.get("context") or "")

    # Logica minima: riconosce se la domanda è per calcolo CTF
    if "ctf" in question or "connettore" in question:
        # parse input dal context
        inputs = {}
        for part in context.split(","):
            p = part.strip().lower()
            if p.startswith("lamiera h"):
                inputs["lamiera"] = p.replace("lamiera ", "").upper()
            elif p.startswith("soletta"):
                inputs["soletta"] = p.split()[1]
            elif "v_l,ed" in p:
                try:
                    inputs["VLed"] = float(p.split("=")[1].split()[0])
                except:
                    pass
            elif p.startswith("cls"):
                inputs["cls"] = p.replace("cls ", "").upper()
            elif p.startswith("passo gola"):
                inputs["s_gola"] = p.split()[2]
            elif p.startswith("lamiera longitudinale"):
                inputs["dir"] = "longitudinale"
            elif p.startswith("lamiera trasversale"):
                inputs["dir"] = "trasversale"
            elif p.startswith("passo lungo trave"):
                try:
                    inputs["s_long"] = float(p.split()[3])
                except:
                    pass

        result = choose_ctf_height(inputs)
        return jsonify({
            "answer": result,
            "mode": mode,
            "context": context
        })

    # Caso CTL (acciaio-legno)
    if "ctl" in question or "acciaio legno" in question:
        return jsonify({
            "answer": "Per acciaio-legno si impiegano i connettori CTL Tecnaria. La scelta dipende da spessore soletta, altezza trave e passo. Sono disponibili tabelle dedicate (ETA) e manuali di posa. È necessario rispettare copriferro, ancoraggi e DPI in cantiere.",
            "mode": mode,
            "context": context
        })

    # Default: risposta generica
    return jsonify({
        "answer": "Questo assistente risponde solo su prodotti e sistemi Tecnaria (CTF, CTL, CEM-E, Diapason, ecc.).",
        "mode": mode,
        "context": context
    })


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
