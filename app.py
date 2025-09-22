<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>TecnariaBot - Assistente Tecnico</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    :root{ --bg:#0b0d0f; --card:#121317; --muted:#9ca3af; --text:#e5e7eb; --accent:#f59e0b; --field:#121317; --border:#2a2e37; }
    body{ background:var(--bg); color:var(--text); }
    .card{ background:var(--card); border:1px solid var(--border); border-radius:14px; box-shadow:0 8px 22px rgba(0,0,0,.35);}    
    .label{ font-size:1rem; font-weight:700; color:#eab308; }
    .input{ width:100%; background:#0f1013; color:#e5e7eb; border:1px solid #3f3f46; border-radius:10px; padding:.7rem .85rem; }
    .input::placeholder{ color:#7b7f89; }
    .input:focus{ outline:2px solid var(--accent); outline-offset:1px; }
    textarea.input{ min-height:110px; resize:vertical; }
    .pill{ display:inline-block; background:transparent; color:#cbd5e1; border:1px solid #3f3f46; padding:.5rem .9rem; border-radius:12px; font-weight:700; }
    .pill.active{ background:rgba(245,158,11,.16); color:#fbbf24; border-color:#f59e0b; }
    .btn{ background:#f59e0b; color:#111827; border:1px solid #eab308; border-radius:10px; padding:.55rem 1.1rem; font-weight:700; }
    .btn:hover{ filter:brightness(1.06); }
    details>summary{ list-style:none; }
    details>summary::-webkit-details-marker{ display:none; }
    .summary-row{ display:flex; align-items:center; gap:.5rem; cursor:pointer; }
    .triangle{ width:0; height:0; border-left:6px solid transparent; border-right:6px solid transparent; border-top:8px solid #fbbf24; transform:rotate(-90deg); transition:transform .2s ease; }
    details[open] .triangle{ transform:rotate(0deg); }
  </style>
</head>
<body>
  <div class="max-w-5xl mx-auto p-6">
    <!-- Header -->
    <header class="mb-6">
      <div class="flex items-center gap-4">
        <div class="h-10 w-10 grid place-items-center rounded-xl bg-amber-500 text-zinc-950 font-black text-xl">T</div>
        <div>
          <h1 class="text-2xl font-bold">TecnariaBot</h1>
          <p class="text-sm text-zinc-400">Assistente Tecnico • Risposte A/B/C sempre coerenti</p>
        </div>
      </div>
    </header>

    <!-- Card principale -->
    <div class="card p-5">
      <!-- Domanda -->
      <label class="label mb-2 block">Domanda</label>
      <textarea id="question" class="input" placeholder="Es.: Quale altezza di connettore CTF devo usare?"></textarea>

      <!-- Tabs + badge modalità -->
      <div class="flex items-center justify-between mt-4">
        <div class="flex items-center gap-3">
          <button id="tabA" data-mode="breve" class="pill">A Breve</button>
          <button id="tabB" data-mode="standard" class="pill">B Standard</button>
          <button id="tabC" data-mode="dettagliata" class="pill active">C Dettagliata</button>
        </div>
        <div class="flex items-center gap-3 text-sm">
          <input id="alwaysWizard" type="checkbox" class="h-4 w-4"/>
          <label for="alwaysWizard" class="cursor-pointer">Mostra sempre il mini-wizard</label>
        </div>
      </div>

      <!-- Wizard (chiuso di default) -->
      <details id="wizardBox" class="mt-3">
        <summary class="summary-row text-amber-300"><span class="triangle"></span><span>I valori del mini-wizard compilano automaticamente il campo “Dati tecnici”.</span></summary>
        <div class="grid md:grid-cols-2 gap-4 mt-3">
          <div class="space-y-3">
            <h3 class="font-semibold text-zinc-300">Geometria</h3>
            <div>
              <label class="text-sm text-zinc-300">Altezza lamiera H (mm)</label>
              <input id="hLamiera" type="number" class="input" placeholder="es. 55" />
            </div>
            <div>
              <label class="text-sm text-zinc-300">Spessore soletta (mm)</label>
              <input id="sSoletta" type="number" class="input" placeholder="es. 60" />
            </div>
            <div>
              <label class="text-sm text-zinc-300">Copriferro (mm) <span class="text-zinc-500">(opzionale)</span></label>
              <input id="copriferro" type="number" class="input" placeholder="es. 25" />
            </div>
            <div>
              <label class="text-sm text-zinc-300">Direzione lamiera</label>
              <select id="dirLamiera" class="input">
                <option value="longitudinale">longitudinale</option>
                <option value="trasversale">trasversale</option>
              </select>
            </div>
            <div>
              <label class="text-sm text-zinc-300">Passo in gola (mm)</label>
              <input id="passoGola" type="number" class="input" placeholder="es. 150" />
            </div>
          </div>
          <div class="space-y-3">
            <h3 class="font-semibold text-zinc-300">Azioni e cls</h3>
            <div>
              <label class="text-sm text-zinc-300">V<sub>L,Ed</sub> (kN/m)</label>
              <input id="vled" type="number" step="0.01" class="input" placeholder="es. 150" />
            </div>
            <div>
              <label class="text-sm text-zinc-300">Classe cls</label>
              <input id="cls" type="text" class="input" placeholder="es. C30/37" />
            </div>
            <div>
              <label class="text-sm text-zinc-300">Passo lungo trave (mm)</label>
              <input id="sLong" type="number" class="input" placeholder="es. 200" />
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="text-sm text-zinc-300">t lamiera (mm)</label>
                <input id="tLamiera" type="number" step="0.01" class="input" placeholder="es. 1.0" />
              </div>
              <div>
                <label class="text-sm text-zinc-300">nr in gola</label>
                <input id="nrGola" type="number" class="input" placeholder="es. 1" />
              </div>
            </div>
          </div>
        </div>
      </details>

      <!-- Dati tecnici (textarea dark che si compila dal wizard) -->
      <div class="mt-4">
        <label class="text-base text-zinc-200 font-semibold">Dati tecnici (mini-wizard / testo)</label>
        <textarea id="context" rows="4" class="input" placeholder="Se necessario verrà compilato dal mini-wizard…"></textarea>
      </div>

      <div class="mt-4 flex justify-end">
        <button id="sendBtn" class="btn">Invia</button>
      </div>
    </div>

    <!-- Risposta -->
    <div class="card mt-6 p-5">
      <div class="flex items-center justify-between mb-3">
        <h3 class="font-semibold">Risposta</h3>
        <span id="modeBadge" class="text-sm text-zinc-300">Modalità: dettagliata</span>
      </div>
      <div id="answer" class="prose prose-invert max-w-none"></div>
      <div id="attachments" class="mt-4 hidden"></div>
    </div>
  </div>

  <script>
    let mode = 'dettagliata';
    let newQuestion = true; // per reset wizard/contesto al primo carattere

    const $ = (s)=>document.querySelector(s);
    const $$= (s)=>Array.from(document.querySelectorAll(s));

    fu
