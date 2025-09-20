/* wizard.js — UI + chiamata API + viewer allegati (immagini/PDF) + clessidra */

(function(){
  const $ = (s)=>document.querySelector(s);
  const $$ = (s)=>document.querySelectorAll(s);

  // Modal viewer
  const mask = $('#modal-mask');
  const body = $('#modal-body');
  const title = $('#modal-title');
  const btnClose = $('#modal-close');

  function openModal(label, url){
    title.textContent = label || 'Allegato';
    body.innerHTML = ''; // reset

    const ext = (url.split('.').pop() || '').toLowerCase();
    if (['jpg','jpeg','png','webp','gif','bmp'].includes(ext)) {
      const img = new Image();
      img.alt = label || 'allegato';
      img.src = url;
      body.appendChild(img);
    } else if (ext === 'pdf') {
      const iframe = document.createElement('iframe');
      iframe.src = url;
      iframe.setAttribute('title', label || 'allegato pdf');
      body.appendChild(iframe);
    } else {
      // fallback: link per il download (nuova scheda)
      const a = document.createElement('a');
      a.href = url;
      a.textContent = 'Scarica allegato';
      a.className = 'attachment-link';
      a.target = '_blank';
      body.appendChild(a);
    }

    mask.style.display = 'flex';
    mask.setAttribute('aria-hidden','false');
  }

  function closeModal(){
    mask.style.display = 'none';
    mask.setAttribute('aria-hidden','true');
    body.innerHTML = '';
  }

  btnClose.addEventListener('click', closeModal);
  mask.addEventListener('click', (e)=>{
    if (e.target === mask) closeModal();
  });
  document.addEventListener('keydown', (e)=>{
    if (e.key === 'Escape') closeModal();
  });

  // Mode buttons
  let mode = 'dettagliata';
  $$('.mode-btn').forEach(b=>{
    b.addEventListener('click', ()=>{
      mode = b.dataset.mode;
      $('#mode-indicator').textContent = 'Modalità: ' + mode;
      $$('.mode-btn').forEach(x=>x.classList.remove('active'));
      b.classList.add('active');
    });
  });

  // Mini-wizard → compone il contesto
  $('#apply-wizard').addEventListener('click', ()=>{
    const h = $('#h_lamiera').value?.trim();
    const ss = $('#s_soletta').value?.trim();
    const v = $('#vled').value?.trim();
    const cls = $('#cls').value?.trim();
    const passo = $('#passo').value?.trim();
    const dir = $('#dir').value?.trim();
    const slong = $('#s_long').value?.trim();
    const t = $('#t_lamiera').value?.trim();
    const nr = $('#nr_gola').value?.trim();

    const parts = [];
    if (h) parts.push(`lamiera H${h}`);
    if (ss) parts.push(`soletta ${ss} mm`);
    if (v) parts.push(`V_L,Ed=${v} kN/m`);
    if (cls) parts.push(`cls ${cls}`);
    if (passo) parts.push(`passo gola ${passo} mm`);
    if (dir) parts.push(`lamiera ${dir}`);
    if (slong) parts.push(`passo lungo trave ${slong} mm`);
    if (t) parts.push(`t=${t} mm`);
    if (nr) parts.push(`nr=${nr}`);

    $('#context').value = parts.join(', ');
  });

  // invio domanda
  $('#send').addEventListener('click', async ()=>{
    const question = $('#question').value.trim();
    const context = $('#context').value.trim();

    if (!question) {
      $('#answer').innerHTML = '<p>Inserisci una domanda.</p>';
      return;
    }

    const sp = $('#spinner'); 
    sp.style.display = 'inline-flex'; // spinner ON

    try {
      const res = await fetch('/api/answer', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({question, mode, context})
      });
      const data = await res.json();

      // risposta
      $('#answer').innerHTML = data.answer || '';

      // allegati → render e apertura in modale
      const box = $('#attachments');
      const list = $('#attachments-list');
      list.innerHTML = '';
      if (data.attachments && data.attachments.length) {
        box.style.display = 'block';
        data.attachments.forEach(att=>{
          const a = document.createElement('a');
          a.href = '#';
          a.className = 'attachment-link';
          a.dataset.href = att.href;
          a.dataset.label = att.label || 'Allegato';
          a.innerHTML = `<span class="paperclip">📎</span> ${att.label || 'Allegato'}`;
          a.addEventListener('click',(e)=>{
            e.preventDefault();
            openModal(att.label, att.href);
          });
          list.appendChild(a);
        });
      } else {
        box.style.display = 'none';
      }

    } catch (e) {
      $('#answer').innerHTML = '<p>Errore di rete o server.</p>';
    } finally {
      sp.style.display = 'none'; // spinner OFF
    }
  });
})();
