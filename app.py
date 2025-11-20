<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8" />
  <title>TECNARIA Sinapsi – GOLD</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />

  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
    * { box-sizing: border-box; }

    body {
      margin: 0;
      padding: 0;
      font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background: radial-gradient(circle at top, #262b35 0, #111111 45%, #050608 100%);
      color: #ffffff;
    }

    /* HEADER */
    .header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 18px 32px;
      background: linear-gradient(90deg, #ef6c00, #d45d00);
      box-shadow: 0 8px 25px rgba(0,0,0,0.45);
    }

    .header-left {
      display: flex;
      align-items: center;
      gap: 18px;
    }

    .logo {
      width: 70px;
      height: auto;
      object-fit: contain;
      background: #ffffff;
      border-radius: 6px;
      padding: 4px;
    }

    .title-block h1 {
      margin: 0;
      font-size: 26px;
      font-weight: 700;
    }

    .subtitle {
      margin: 2px 0 0;
      font-size: 13px;
      opacity: 0.9;
    }

    .header-right {
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 4px;
      font-size: 13px;
    }

    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: #0f1b12;
      color: #49ff9c;
      padding: 6px 12px;
      border-radius: 999px;
      font-size: 13px;
      box-shadow: 0 0 12px rgba(0, 255, 140, 0.35);
    }

    .status-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #49ff9c;
    }

    .dataset-info {
      opacity: 0.85;
    }

    /* LAYOUT */
    .main {
      padding: 24px 26px 26px;
      display: flex;
      gap: 22px;
    }

    .panel {
      background: rgba(13, 18, 27, 0.96);
      border-radius: 18px;
      padding: 20px 22px;
      box-shadow: 0 0 30px rgba(0, 0, 0, 0.7);
      border: 1px solid rgba(255,255,255,0.03);
    }

    .left-panel {
      flex: 1.1;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .right-panel {
      flex: 1.2;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .panel-title-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 6px;
    }

    .panel-title {
      font-size: 15px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: #ffb469;
    }

    .panel-subtitle {
      font-size: 12px;
      opacity: 0.7;
    }

    .question-box {
      width: 100%;
      min-height: 170px;
      max-height: 260px;
      background: radial-gradient(circle at top left, #282f3b 0, #151923 55%, #101319 95%);
      border-radius: 16px;
      border: 1px solid rgba(255,255,255,0.08);
      padding: 16px;
      color: #ffffff;
      resize: vertical;
      outline: none;
      font-size: 14px;
    }

    .question-box::placeholder {
      color: rgba(255,255,255,0.45);
    }

    .send-wrapper {
      margin-top: 12px;
      display: flex;
      justify-content: flex-end;
    }

    .btn-send {
      padding: 10px 22px;
      border-radius: 999px;
      border: none;
      cursor: pointer;
      font-size: 14px;
      font-weight: 600;
      background: radial-gradient(circle at top left, #ffb467 0, #ef6c00 40%, #d45d00 100%);
      color: #ffffff;
      box-shadow: 0 0 20px rgba(239,108,0,0.7);
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }

    .btn-send span.arrow {
      font-size: 16px;
    }

    .hint {
      margin-top: 10px;
      font-size: 12px;
      opacity: 0.7;
    }

    .answer-area {
      background: radial-gradient(circle at top left, #283040 0, #151823 50%, #0f1117 100%);
      border-radius: 16px;
      padding: 16px 18px;
      min-height: 210px;
      border: 1px solid rgba(255,255,255,0.06);
      white-space: pre-wrap;
      font-size: 14px;
    }

    .answer-placeholder {
      opacity: 0.6;
      font-style: italic;
    }

    .meta-area {
      margin-top: 10px;
      background: rgba(8, 10, 14, 0.9);
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 11px;
      color: #d0d0d0;
      border: 1px solid rgba(255,255,255,0.05);
      white-space: pre-wrap;
    }

    .meta-label {
      font-weight: 600;
      color: #f5b46a;
    }

    @media (max-width: 900px) {
      .main { flex-direction: column; }
    }
  </style>
</head>

<body>
  <header class="header">
    <div class="header-left">
      <img src="/static/assets/logo.png" alt="Tecnaria Logo" class="logo" onerror="this.style.display='none';" />
      <div class="title-block">
        <h1>TECNARIA Sinapsi – GOLD</h1>
        <p class="subtitle">Assistente tecnico strutturale · Modalità GOLD Tecnaria</p>
      </div>
    </div>
    <div class="header-right">
      <div class="status-pill">
        <span class="status-dot"></span>
        <span>Backend online</span>
      </div>
      <div class="dataset-info">
        Endpoint: /api/ask (GOLD Tecnaria)
      </div>
    </div>
  </header>

  <main class="main">
    <section class="panel left-panel">
      <div class="panel-title-row">
        <div>
          <div class="panel-title">Domanda</div>
          <div class="panel-subtitle">
            Scrivi qui la domanda sui sistemi Tecnaria (CTF, P560, CTL, VCEM, CTCEM, DIAPASON, GTS…)
          </div>
        </div>
      </div>

      <textarea id="question" class="question-box"
        placeholder="Esempi:
- Mi parli del connettore CTF Tecnaria?
- Quanti connettori devo prevedere per un solaio con profili IPE?
- Come si fissa il sistema DIAPASON su travetti in laterocemento?
- È valido il fissaggio se il chiodo P560 entra nel calcestruzzo del travetto?"></textarea>

      <div class="send-wrapper">
        <button id="sendBtn" class="btn-send">
          Chiedi in GOLD
          <span class="arrow">➜</span>
        </button>
      </div>

      <p class="hint">
        La modalità GOLD utilizza un prompt tecnico–ingegneristico dedicato a Tecnaria S.p.A., con controlli su P560, CTF,
        DIAPASON, CTL, VCEM, CTCEM e sugli errori di posa. Nessuna risposta su prodotti non Tecnaria.
      </p>
    </section>

    <section class="panel right-panel">
      <div class="panel-title-row">
        <div>
          <div class="panel-title">Risposta GOLD</div>
          <div class="panel-subtitle">Esito dell'assistente Tecnaria in modalità GOLD</div>
        </div>
      </div>

      <div id="answerBox" class="answer-area">
        <span class="answer-placeholder">
          La risposta GOLD apparirà qui. Formula una domanda sui prodotti e sistemi Tecnaria S.p.A. e premi “Chiedi in GOLD”.
        </span>
      </div>

      <div id="metaBox" class="meta-area">
        <span class="meta-label">Meta:</span> in attesa della prima chiamata al backend…
      </div>
    </section>
  </main>

  <script>
    const sendBtn = document.getElementById("sendBtn");
    const qEl = document.getElementById("question");
    const answerBox = document.getElementById("answerBox");
    const metaBox = document.getElementById("metaBox");

    async function sendQuestion() {
      const question = qEl.value.trim();
      if (!question) return;

      answerBox.textContent = "Sto pensando in modalità GOLD Tecnaria…";
      metaBox.textContent = "Chiamata a /api/ask (GOLD) in corso…";

      try {
        const res = await fetch("/api/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question })
        });

        const data = await res.json();
        answerBox.textContent = data.answer || "— nessuna risposta —";
        metaBox.textContent =
          "source: " + (data.source || "?") + "\n" +
          "meta: " + JSON.stringify(data.meta || {}, null, 2);
      } catch (err) {
        answerBox.textContent = "Errore di comunicazione con il backend.";
        metaBox.textContent = String(err);
      }
    }

    sendBtn.addEventListener("click", sendQuestion);

    qEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        sendQuestion();
      }
    });
  </script>
</body>
</html>
