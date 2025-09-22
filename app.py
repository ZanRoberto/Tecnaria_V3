<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>TecnariaBot - Assistente Tecnico</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    /* Stile come nello screenshot: dark UI, pill buttons, input leggibili */
    :root{ --bg:#0b0d0f; --card:#121317; --muted:#9ca3af; --text:#e5e7eb; --accent:#f59e0b; --field:#15171c; --border:#2a2e37; }
    body{ background:var(--bg); color:var(--text); }
    .card{ background:var(--card); border:1px solid var(--border); border-radius:18px; box-shadow:0 10px 30px rgba(0,0,0,.35);}    
    .label{ font-size:.9rem; color:#d1d5db; }
    .pill{ display:inline-block; background:#0f1115; color:#e5e7eb; border:1px solid #3f3f46; padding:.45rem .8rem; border-radius:12px; font-weight:600; }
    .pill.active{ background:rgba(245,158,11,.12); border-color:#f59e0b; color:#fbbf24; }
    .btn{ background:#f59e0b; color:#111827; border:1px solid #eab308; border-radius:10px; padding:.55rem 1.1rem; font-weight:700; }
    .btn:hover{ filter:brightness(1.05); }
    .input{ width:100%; background:var(--field); color:#e5e7eb; border:1px solid #3f3f46; border-radius:10px; padding:.6rem .75rem; }
    .input::placeholder{ color:#8b8f9a; }
    .input:focus{ outline:2px solid var(--accent); outline-offset:1px; }
    select.input{ background:var(--field); }
    .small{ font-size:.78rem; color:#9ca3af; }

    /* Pannello dati tecnici (allineato, non scrolla) */
    .ctxpanel{ background:var(--field); border:1px solid var(--border); border-radius:10px; padding:.75rem .9rem; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
    .kv{ display:grid; grid-template-columns: 260px 1fr; column-gap: 14px; row-gap: 4px; align-items:baseline; }
    .k{ color:#a1a1aa; }
    .v{ color:#e5e7eb; }
  </style>
</head>
<body>
  <div class="max-w-5xl mx-auto p-6">
    <header class="mb-6">
      <div class="flex items-center gap-4">
        <img src="/static/img/logo_t.png" alt="T" class="h-10 w-10 rounded-xl" onerror="this.outerHTML='<div class=\'h-10 w-10 grid place-items-center rounded-xl bg-amber-500 text-zinc-950 font-black text-xl\'>T</div>'">
        <div>
          <h1 class="text-2xl font-bold">TecnariaBot</h1>
          <p class="text-sm text-zinc-400">Assistente Tecnico · Risposte A/B/C sempre coerenti</p>
        </div>
        <div class="ml-auto text-sm text-zinc-300" id="modeBadge">Modalità: dettagliata</div>
      </div>
    </header>

    <div class="card p-5">
      <div class="grid gap-3">
        <label class="label">Domanda</label>
        <textarea id="question" rows="3" class="input" placeholder="Es.: Quale altezza di connettore CTF devo usare?"></textarea>

        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <button id="tabA" data-mode="breve" class="pill">A Breve</button>
            <button id="tabB" data-mode="standard" class="pill">B Standard</button>
            <button id="tabC" data-mode="dettagliata" class="pill active">C Dettagliata</button>
          </div>
          <label class="text-sm flex items-center gap-2"><input id="alwaysWizard" type="checkbox" class="h-4 w-4"/> Mostra sempre il mini-wizard</label>
        </div>
      </div>

      <details id="wizardBox" class="mt-3">
        <summary class="cursor-pointer text-amber-300">I valori del mini-wizard compilano automaticamente il campo “Dati tecnici”.</summary>
        <div class="grid md:grid-cols-2 gap-4 mt-3">
          <div class="space-y-3">
            <h3 class="font-semibold">Geometria</h3>
            <div>
              <label class="label">Altezza lamiera H (mm)</label>
              <input id="hLamiera" type="number" class="input" placeholder="es. 55" />
            </div>
            <div>
              <label class="label">Spessore soletta (mm)</label>
              <input id="sSoletta" type="number" class="input" placeholder="es. 60" />
            </div>
            <div>
              <label class="label">Copriferro (mm) <span class="small">(opzionale)</span></label>
              <input id="copriferro" type="number" class="input" placeholder="es. 25" />
            </div>
            <div>
              <label class="label">Direzione lamiera</label>
              <select id="dirLamiera" class="input">
                <option value="longitudinale">longitudinale</option>
                <option value="trasversale">trasversale</option>
              </select>
            </div>
            <div>
              <label class="label">Passo in gola (mm)</label>
              <input id="passoGola" type="number" class="input" placeholder="es. 150" />
            </div>
          </div>
          <div class="space-y-3">
            <h3 class="font-semibold">Azioni e cls</h3>
            <div>
              <label class="label">V<sub>L,Ed</sub> (kN/m)</label>
              <input id="vled" type="number" step="0.01" class="input" placeholder="es. 150" />
            </div>
            <div>
              <label class="label">Classe cls</label>
              <input id="cls" type="text" class="input" placeholder="es. C30/37" />
            </div>
            <div>
              <label class="label">Passo lungo trave (mm)</label>
              <input id="sLong" type="number" class="input" placeholder="es. 200" />
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="label">t lamiera (mm)</label>
                <input id="tLamiera" type="number" step="0.01" class="input" placeholder="es. 1.0" />
              </div>
              <div>
                <label class="label">nr in gola</label>
                <input id="nrGola" type="number" class="input" placeholder="es. 1" />
              </div>
            </div>
          </div>
        </div>
      </details>

      <div class="mt-4">
        <label class="label">Dati tecnici (mini-wizard / testo)</label>
        <div id="contextView" class="ctxpanel">
          <div class="kv"><span class="k">Lamiera</span><span class="v">—</span></div>
          <div class="kv"><span class="k">Soletta</span><span class="v">—</span></div>
          <div class="kv"><span class="k">V_L,Ed</span><span class="v">—</span></div>
          <div class="kv"><span class="k">Classe cls</span><span class="v">—</span></div>
          <div class="kv"><span class="k">Passo in gola</span><span class="v">—</span></div>
          <div class="kv"><span class="k">Direzione lamiera</span><span class="v">—</span></div>
          <div class="kv"><span class="k">Passo lungo trave</span><span class="v">—</span></div>
          <div class="kv"><span class="k">t lamiera</span><span class="v">—</span></div>
          <div class="kv"><span class="k">nr in gola</span><span class="v">—</span></div>
          <div class="kv"><span class="k">Copriferro</span><span class="v">—</span></div>
        </div>
        <input type="hidden" id="context" />
      </div>

      <div class="mt-4 flex items-center gap-3">
        <button id="sendBtn" class="btn">Invia</button>
        <span id="busy" class="hidden items-center gap-2 text-sm text-zinc-400">Elaboro...</span>
      </div>
    </div>

    <div class="card mt-6 p-5">
      <div class="flex items-center justify-between mb-3">
        <h3 class="font-semibold">Risposta</h3>
        <span id="modeShown" class="text-sm text-zinc-300">Modalità: dettagliata</span>
      </div>
      <div id="answer" class="prose prose-invert max-w-none"></div>
      <div id="attachments" class="mt-4 hidden"></div>
    </div>
  </div>

  <script>
    let mode = 'dettagliata';
    let questionStarted = false; // per capire quando pulire wizard al nuovo input

    const $  = sel => document.querySelector(sel);
    const $$ = sel => Array.from(document.querySelectorAll(sel));

    function setMode(m){
      mode=m; $('#modeShown').textContent = 'Modalità: ' + m; $('#modeBadge')?.textContent = 'Modalità: ' + m;
      $$('#tabA, #tabB, #tabC').forEach(b=>b.classList.remove('active'));
      if(m==='breve') $('#tabA').classList.add('active');
      if(m==='standard') $('#tabB').classList.add('active');
      if(m==='dettagliata') $('#tabC').classList.add('active');
    }
    $('#tabA').addEventListener('click', ()=>setMode('breve'));
    $('#tabB').addEventListener('click', ()=>setMode('standard'));
    $('#tabC').addEventListener('click', ()=>setMode('dettagliata'));

    // Preferenza wizard
    const alwaysWizard = $('#alwaysWizard');
    const wizardBox = $('#wizardBox');
    const saved = localStorage.getItem('tecnaria_alwaysWizard');
    if(saved==='1'){ alwaysWizard.checked=true; wizardBox.open=true; }
    alwaysWizard.addEventListener('change', ()=>{
      localStorage.setItem('tecnaria_alwaysWizard', alwaysWizard.checked?'1':'0');
      wizardBox.open = alwaysWizard.checked;
    });

    // Auto-apertura wizard se la domanda è pertinente al calcolo CTF
    const CALC_REGEX = /(ctf|connettore|connettori|altezza|scegliere|verifica|calcolo)/i;
    $('#question').addEventListener('input', (e)=>{
      const q = e.target.value;
      if(!questionStarted && q.length>0){ // primo carattere di una nuova domanda
        clearWizard();
        renderContextFromWizard();
        questionStarted = true;
      }
      if(CALC_REGEX.test(q)) wizardBox.open = true; // auto open
    });

    function clearWizard(){
      ['hLamiera','sSoletta','vled','cls','passoGola','dirLamiera','sLong','tLamiera','nrGola','copriferro'].forEach(id=>{
        const el = document.getElementById(id);
        if(el.tagName==='SELECT') el.selectedIndex = 0; else el.value = '';
      });
      $('#context').value = '';
    }

    // Wizard -> contesto (stringa per backend) + vista allineata
    const FIELDS = ['hLamiera','sSoletta','vled','cls','passoGola','dirLamiera','sLong','tLamiera','nrGola','copriferro'];
    FIELDS.forEach(id=>document.getElementById(id).addEventListener('input', renderContextFromWizard));

    function renderContextFromWizard(){
      const h=$('#hLamiera').value, s=$('#sSoletta').value, v=$('#vled').value, cls=$('#cls').value,
            p=$('#passoGola').value, dir=$('#dirLamiera').value, sl=$('#sLong').value,
            t=$('#tLamiera').value, nr=$('#nrGola').value, cf=$('#copriferro').value;

      const parts=[];
      if(h)  parts.push(`lamiera H${h}`);
      if(s)  parts.push(`soletta ${s} mm`);
      if(v)  parts.push(`V_L,Ed=${v} kN/m`);
      if(cls)parts.push(`cls ${cls}`);
      if(p)  parts.push(`passo gola ${p} mm`);
      if(dir)parts.push(`lamiera ${dir}`);
      if(sl) parts.push(`passo lungo trave ${sl} mm`);
      if(t)  parts.push(`t=${t} mm`);
      if(nr) parts.push(`nr=${nr}`);
      if(cf) parts.push(`copriferro ${cf} mm`);
      $('#context').value = parts.join(', ');

      // vista allineata
      const rows = [
        ['Lamiera', h?('H'+h+(dir?` (${dir})`:'')):'—'],
        ['Soletta', s?(`${s} mm`):'—'],
        ['V_L,Ed', v?(`${v} kN/m`):'—'],
        ['Classe cls', cls||'—'],
        ['Passo in gola', p?(`${p} mm`):'—'],
        ['Direzione lamiera', dir||'—'],
        ['Passo lungo trave', sl?(`${sl} mm`):'—'],
        ['t lamiera', t?(`${t} mm`):'—'],
        ['nr in gola', nr||'—'],
        ['Copriferro', cf?(`${cf} mm`):'—']
      ];
      const html = rows.map(([k,v])=>`<div class="kv"><span class="k">${k}</span><span class="v">${v}</span></div>`).join('');
      $('#contextView').innerHTML = html;
    }

    // Invio
    async function send(){
      const question = $('#question').value.trim();
      const context  = $('#context').value.trim();
      if(!question){ $('#question').focus(); return; }
      $('#sendBtn').disabled=true; $('#busy').classList.remove('hidden');
      $('#answer').innerHTML=''; $('#attachments').classList.add('hidden');
      try{
        const res = await fetch('/api/answer',{ method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question,mode,context}) });
        const data = await res.json();
        $('#answer').innerHTML = data.answer || '<em>Nessuna risposta.</em>';
        if(data.attachments && data.attachments.length){
          const box = $('#attachments'); box.classList.remove('hidden'); box.innerHTML = '<ul class="list-disc ml-6"></ul>';
          const ul = box.querySelector('ul');
          data.attachments.forEach(a=>{ const li=document.createElement('li'); li.innerHTML = `<a class="text-amber-300 hover:underline" target="_blank" rel="noopener" href="${a.href}">${a.label}</a>`; ul.appendChild(li); });
        }
      }catch(e){ $('#answer').innerHTML = `<span style="color:#ef4444">Errore: ${e}</span>`; }
      finally{ $('#sendBtn').disabled=false; $('#busy').classList.add('hidden'); questionStarted = false; }
    }

    $('#sendBtn').addEventListener('click', send);
    document.addEventListener('keydown', e=>{ if(e.ctrlKey && e.key==='Enter') send(); });

    // Setup iniziale coerente con lo screenshot
    setMode('dettagliata');
    renderContextFromWizard();
  </script>
</body>
</html>
