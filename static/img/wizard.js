// wizard.js — mini-wizard CTF + Copriferro

(function(){
  const $ = (id) => document.getElementById(id);

  // Pulsanti A/B/C
  ["btnA","btnB","btnC"].forEach(id=>{
    const el = $(id); if(!el) return;
    el.addEventListener("click", ()=>{
      const mode = id==="btnA"?"breve":(id==="btnB"?"standard":"dettagliata");
      const sel = $("mode"); if(sel) sel.value = mode;
      document.querySelectorAll(".mode-btn").forEach(b=>b.classList.remove("active"));
      el.classList.add("active");
    });
  });

  // Mostra sempre mini-wizard (toggle)
  const chk = $("showWizard");
  if (chk) {
    chk.addEventListener("change", ()=>{
      const box = $("wizardBox");
      if (!box) return;
      box.style.display = chk.checked ? "block" : "none";
    });
  }

  // Applica dati → compila il contesto testuale
  const applyBtn = $("applyWizard");
  if (applyBtn) {
    applyBtn.addEventListener("click", ()=>{
      const ctx = [];

      const h = $("h_lamiera")?.value?.trim();
      const ss = $("s_soletta")?.value?.trim();
      const v = $("vled")?.value?.trim();
      const cls = $("cls")?.value?.trim();
      const passo = $("passo")?.value?.trim();
      const dir = $("dir")?.value;
      const sl = $("s_long")?.value?.trim();
      const t = $("t_lamiera")?.value?.trim();
      const nr = $("nr_gola")?.value?.trim();
      const copri = $("copriferro")?.value?.trim();

      if (h)    ctx.push(`lamiera H${h}`);
      if (ss)   ctx.push(`soletta ${ss} mm`);
      if (v)    ctx.push(`V_L,Ed=${v} kN/m`);
      if (cls)  ctx.push(`cls ${cls}`);
      if (passo)ctx.push(`passo gola ${passo} mm`);
      if (dir && dir!=="") ctx.push(`lamiera ${dir}`);
      if (sl)   ctx.push(`passo lungo trave ${sl} mm`);
      if (t)    ctx.push(`t=${t} mm`);
      if (nr)   ctx.push(`nr=${nr}`);
      if (copri)ctx.push(`copriferro ${copri} mm`);

      const out = ctx.join(", ");
      const ta = $("context");
      if (ta) ta.value = out;

      const info = $("wizardInfo");
      if (info) {
        info.innerText = out ? "I valori del mini-wizard hanno compilato il campo “Dati tecnici”." :
                               "Compila qualche campo e premi Applica.";
      }
    });
  }

  // Clessidra UI mentre /api/answer elabora
  const form = $("qaForm");
  if (form) {
    form.addEventListener("submit", ()=>{
      const sp = $("spinner");
      if (sp) { sp.style.display = "inline-block"; }
      const ans = $("answer");
      if (ans) { ans.innerHTML = ""; }
    });
  }

  // Quando la risposta torna, nascondi clessidra (hook generico)
  window.hideSpinner = function(){
    const sp = $("spinner");
    if (sp) sp.style.display = "none";
  }
})();
