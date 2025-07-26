from flask import Flask, request, jsonify

app = Flask(__name__)

def get_tecnaria_connettori():
    """
    Restituisce HTML con immagini ufficiali dei connettori Tecnaria.
    """
    return """
    <h2 style="color:#004080;">Connettori Tecnaria per Solai in Legno</h2>

    <!-- CTL BASE -->
    <div style="background:#f9f9f9; padding:15px; margin-bottom:20px; border-radius:8px;">
      <h3>🔩 Connettore CTL BASE</h3>
      <img src="https://tecnaria.com/wp-content/uploads/2019/05/connettore_base-CE.png"
           alt="Connettore CTL BASE" style="max-width:300px; border-radius:6px;">
      <p><strong>Descrizione:</strong> connettore a piolo Ø12 mm con piastra 50×50×4 mm a ramponi.<br>
         <strong>Viti:</strong> Ø8 mm (70, 100, 120 mm).<br>
         <a href="https://tecnaria.com/prodotto/connettore-per-legno-ctl-base/" target="_blank">
         Scheda tecnica CTL BASE</a>
      </p>
    </div>

    <!-- CTL MAXI -->
    <div style="background:#f9f9f9; padding:15px; margin-bottom:20px; border-radius:8px;">
      <h3>🔩 Connettore CTL MAXI</h3>
      <img src="https://tecnaria.com/wp-content/uploads/2019/05/maxi-CE.png"
           alt="Connettore CTL MAXI" style="max-width:300px; border-radius:6px;">
      <p><strong>Descrizione:</strong> connettore a piolo Ø12 mm con piastra 75×50×4 mm a ramponi.<br>
         <strong>Viti:</strong> Ø10 mm (100, 120, 140 mm).<br>
         <a href="https://tecnaria.com/prodotto/connector-to-pin-ctl-maxi/" target="_blank">
         Scheda tecnica CTL MAXI</a>
      </p>
    </div>

    <!-- CTL OMEGA -->
    <div style="background:#f9f9f9; padding:15px; border-radius:8px;">
      <h3>🔩 Connettore CTL OMEGA</h3>
      <img src="https://tecnaria.com/wp-content/uploads/2019/05/omega-CE.png"
           alt="Connettore CTL OMEGA" style="max-width:300px; border-radius:6px;">
      <p><strong>Descrizione:</strong> piastra piegata a Ω (90×30×4 mm) con vite Ø10 mm.<br>
         <strong>Viti:</strong> lunghezze 100, 120, 140 mm.<br>
         <a href="https://tecnaria.com/prodotto/connessioni-legno-calcestruzzo-ctl-omega/" target="_blank">
         Scheda tecnica CTL OMEGA</a>
      </p>
    </div>
    """


@app.route("/ask", methods=["POST"])
def ask():
    user_question = request.json.get("question", "").lower()

    # 1) Se la domanda riguarda connettori, restituisce immagini + schede
    if "connettori" in user_question or "ctl" in user_question:
        return jsonify({"answer": get_tecnaria_connettori()})

    # 2) Fallback - Risposta testuale dai documenti
    return jsonify({
        "answer": "📚 Dai documenti:\nNon ci sono immagini per questa domanda, ma posso fornirti le informazioni tecniche dai documenti caricati."
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
