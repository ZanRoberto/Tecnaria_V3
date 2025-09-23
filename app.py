<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>TecnariaBot • Assistente Tecnico</title>
  <style>
    :root{
      --bg:#0f1115; --panel:#151924; --muted:#97a1b3; --text:#e9edf3;
      --accent:#ff7a00; /* arancione Tecnaria */
      --ok:#2ecc71; --bad:#ff4d4d;
      --radius:14px;
    }
    *{box-sizing:border-box}
    body{
      margin:0; background:var(--bg); color:var(--text);
      font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,"Helvetica Neue",Arial;
      line-height:1.45;
    }
    .wrap{max-width:960px; margin:0 auto; padding:24px}
    header{
      display:flex; align-items:center; gap:14px; margin-bottom:18px;
    }
    header img{height:36px; width:auto}
    header h1{font-size:18px; margin:0; font-weight:700}
    header .sub{color:var(--muted); font-size:13px}

    .card{
      background:var(--panel); border:1px solid #1e2432; border-radius:var(--radius);
      padding:18px;
    }
    label{display:block; color:var(--muted); font-size:12px; margin:6px 0 6px}
    textarea, input, button, select{
      width:100%; font:inherit; color:var(--text);
    }
    textarea{
      background:#0f1420; border:1px solid #20283a; border-radius:10px;
      padding:12px 14px; min-height:90px; resize:vertical;
    }
    button{
      background:var(--accent); color:#000; border:0; border-radius:10px;
      padding:12px 14px; font-weight:700; cursor:pointer;
      transition:transform .04s ease-in-out, opacity .2s;
    }
    button:hover{opacity:.92}
    .row{display:flex; gap:12px; align-items:flex-end}
    .row .grow{flex:1}
    .muted{color:var(--muted)}
    .spacer{height:10px}

    .answer{
      white-space:pre-wrap;
      background:#0f1420; border:1px solid #20283a; border-radius:10px;
      padding:14px;
      min-height:80px;
    }
    .attachments{
      margin-top:10px; display:flex; flex-wrap:wrap; gap:10px;
    }
    .chip{
      display:inline-flex; align-items:center; gap:8px;
      background:#0e1522; border:1px solid #20283a; color:var(--text);
      padding:8px 10px; border-radius:999px; font-size:12px; cursor:pointer;
    }
    .chip .dot{width:8px; height:8px; border-radius:50%}
    .chip.doc .dot{background:#8ab4ff}
    .chip.img .dot{background:#ffd479}
    .chip.vid .dot{background:#9dff8a}

    /* spinner */
    .spinner{display:none; align-items:center; gap:10px; color:var(--muted); font-size:13px}
    .hourglass{
      width:18px; height:18px; border:2px solid var(--muted); border-radius:50%;
      border-top-color:transparent; animation:spin 1s linear infinite;
    }
    @keyframes spin{to{transform:rotate(360deg)}}

    /* modal viewer */
    .modal{
      position:fixed; inset:0; background:rgba(0,0,0,.78);
      display:none; align-items:center; justify-content:center; padding:24px;
      z-index:50;
    }
    .modal.open{display:flex}
    .viewer{
      position:relative; width:min(100%, 920px); height:min(90vh, 800px);
      background:#0b0f17; border:1px solid #20283a; border-radius:12px; overflow:hidden;
    }
    .viewer header{
      display:flex; align-items:center; justify-content:space-between;
      padding:10px 14px; border-bottom:1px solid #20283a; margin:0;
    }
    .viewer header h3{font-size:14px; margin:0; color:#e9edf3; font-weight:600}
    .closex{
      background:transparent; color:#fff; border:0; font-size:18px; cursor:pointer;
      line-height:1; padding:6px 10px; border-radius:8px;
    }
    .closex:hover{background:#1a2335}
    .content{
      width:100%; height:calc(100% - 48px); background:#0b0f17;
    }
    .content iframe{width:100%; height:100%; border:0; background:#0b0f17}
    .content img{max-width:100%; max-height:100%; display:block; margin:0 auto}

    /* (commentato) selettori A/B/C */
    /*
    .abc{display:flex; gap:8px; margin-top:8px}
    .abc button{flex:1}
    */
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <img src="/static/img/logo.jpg" alt="Tecnaria logo" />
      <div>
        <h1>TecnariaBot</h1>
        <div class="sub">Assistente tecnico (solo prodotti/servizi Tecnaria)</div>
      </div>
    </header>

    <div class="card">
      <label for="q">Domanda</label>
      <textarea id="q" placeholder="Es. Mi dai i codici dei connettori CTF?"></textarea>

      <!-- (DISATTIVATO) Scelta modalità A/B/C
      <div class="abc">
        <button data-mode="A" title="Breve (2-3 frasi)">A Breve</button>
        <button data-mode="B" title="Standard">B Standard</button>
        <button data-mode="C" title="Dettagliata tecnica">C Dettagliata</button>
      </div>
      -->

      <div class="spacer"></div>
      <div class="row">
        <div class="grow"></div>
        <button id="sendBtn">Invia</button>
      </div>

      <div class="spacer"></div>
      <div class="spinner" id="spinner"><div class="hourglass"></div> Elaborazione in corso…</div>

      <div class="spacer"></div>
      <label>Risposta</label>
      <div id="answer" class="answer">—</div>

      <div id="attachWrap" class="attachments"></div>
      <div class="muted" style="margin-top:6px">Suggerimento: se chiedi “codici CTF” o “contatti Tecnaria” la risposta è deterministica.</div>
    </div>
  </div>

  <!-- Modal viewer con X -->
  <div id="modal" class="modal" aria-hidden="true">
    <div class="viewer">
      <header>
        <h3 id="mTitle">Allegato</h3>
        <button class="closex" id="mClose" aria-label="Chiudi">✕</button>
      </header>
      <div class="content" id="mContent"></div>
    </div>
  </div>

  <script>
    const qEl = document.getElementById('q');
    const answerEl = document.getElementById('answer');
    const sendBtn = document.getElementById('sendBtn');
    const spinner = document.getElementById('spinner');
    const attachWrap = document.getElementById('attachWrap');

    const modal = document.getElementById('modal');
    const mTitle = document.getElementById('mTitle');
    const mContent = document.getElementById('mContent');
    const mClose = document.getElementById('mClose');

    function openModal(title, url){
      mTitle.textContent = title;
      mContent.innerHTML = ''; // pulisci
      // rileva tipo base dall'estensione
      const lower = url.toLowerCase();
      if (/\.(png|jpg|jpeg|webp|gif)$/.test(lower)){
        const img = document.createElement('img');
        img.src = url;
        img.alt = title;
        mContent.appendChild(img);
      } else {
        // per PDF e qualsiasi file mostriamo in iframe (browser permettendo)
        const ifr = document.createElement('iframe');
        ifr.src = url;
        mContent.appendChild(ifr);
      }
      modal.classList.add('open');
      modal.setAttribute('aria-hidden','false');
    }
    function closeModal(){
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden','true');
      mContent.innerHTML = '';
    }
    mClose.addEventListener('click', closeModal);
    modal.addEventListener('click', (e)=>{ if(e.target===modal) closeModal(); });
    document.addEventListener('keydown', (e)=>{ if(e.key==='Escape') closeModal(); });

    async function ask(){
      const question = qEl.value.trim();
      if(!question){
        answerEl.textContent = 'Scrivi una domanda…';
        return;
      }
      answerEl.textContent = '';
      attachWrap.innerHTML = '';
      spinner.style.display = 'flex';
      sendBtn.disabled = true;

      try{
        const res = await fetch('/api/answer', {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ question })
        });
        const data = await res.json();

        answerEl.textContent = data.answer || '—';

        // Allegati cliccabili (aprono modale)
        if(Array.isArray(data.attachments) && data.attachments.length){
          data.attachments.forEach(a=>{
            const chip = document.createElement('button');
            chip.className = 'chip ' + (a.type==='document'?'doc':a.type==='image'?'img':a.type==='video'?'vid':'file');
            chip.innerHTML = `<span class="dot"></span><span>${a.title}</span>`;
            chip.addEventListener('click', ()=>openModal(a.title, a.url));
            attachWrap.appendChild(chip);
          });
        }
      }catch(err){
        answerEl.textContent = 'Errore: ' + (err?.message || err);
      }finally{
        spinner.style.display = 'none';
        sendBtn.disabled = false;
      }
    }

    sendBtn.addEventListener('click', ask);
    qEl.addEventListener('keydown', (e)=>{ if(e.key==='Enter' && (e.ctrlKey||e.metaKey)) ask(); });
  </script>
</body>
</html>
