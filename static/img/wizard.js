// Schema dei campi
const WIZARD_SCHEMAS = {
  CTF_CALC: [
    {id:"h_lamiera", label:"Altezza lamiera (mm)", type:"number", required:true, placeholder:"55"},
    {id:"s_soletta", label:"Spessore soletta (mm)", type:"number", required:true, placeholder:"60"},
    {id:"vled",      label:"V_L,Ed (kN/m)",        type:"number", required:true, placeholder:"150"},
    {id:"cls",       label:"Classe cls",           type:"text",   required:true, placeholder:"C30/37"},
    {id:"passo",     label:"Passo gola (mm)",      type:"number", required:true, placeholder:"150"},
    {id:"dir",       label:"Direzione lamiera",    type:"select", required:true,
      options:["","longitudinale","trasversale"]}
  ]
};

// Detect schema dalla domanda
function detectWizardSchema(text) {
  const t = (text||"").toLowerCase();
  const isCalc = /(altezza|dimension|v_l,?ed|portata|quale altezza|quanti)/.test(t);
  if (!isCalc) return null;
  if (/ctf|lamiera|soletta|solaio/.test(t)) return "CTF_CALC";
  return null;
}

// Render wizard dinamico
function renderWizard(schemaKey) {
  const box = document.getElementById("wizard-dynamic");
  box.innerHTML = "";
  if (!schemaKey) { box.style.display="none"; return; }

  const fields = WIZARD_SCHEMAS[schemaKey];
  fields.forEach(f => {
    const wrap = document.createElement("label");
    wrap.textContent = f.label;
    let el;
    if (f.type === "select") {
      el = document.createElement("select");
      f.options.forEach(opt => {
        const o = document.createElement("option");
        o.value = opt; o.textContent = opt || "—";
        el.appendChild(o);
      });
    } else {
      el = document.createElement("input");
      el.type = f.type;
      if (f.placeholder) el.placeholder = f.placeholder;
    }
    el.id = `w_${f.id}`;
    if (f.required) el.classList.add("required");
    el.addEventListener("input", syncWizardToContext);
    wrap.appendChild(el);
    box.appendChild(wrap);
  });
  box.style.display="block";
  box.dataset.schema = schemaKey;
  syncWizardToContext();
}

// Aggiorna campo context
function syncWizardToContext() {
  const schemaKey = document.getElementById("wizard-dynamic").dataset.schema;
  if (!schemaKey) return;
  const fields = WIZARD_SCHEMAS[schemaKey];
  const parts = [];
  fields.forEach(f => {
    const v = document.getElementById(`w_${f.id}`).value;
    if (v) {
      if (schemaKey==="CTF_CALC") {
        if (f.id==="h_lamiera") parts.push(`lamiera H${v}`);
        else if (f.id==="s_soletta") parts.push(`soletta ${v} mm`);
        else if (f.id==="vled") parts.push(`V_L,Ed=${v} kN/m`);
        else if (f.id==="cls") parts.push(`cls ${v}`);
        else if (f.id==="passo") parts.push(`passo gola ${v} mm`);
        else if (f.id==="dir") parts.push(`lamiera ${v}`);
      }
    }
  });
  document.getElementById("context").value = parts.join(", ");
}

// Submit domanda
async function sendQuestion() {
  const q = document.getElementById("question").value;
  const schema = detectWizardSchema(q);
  if (schema) renderWizard(schema);

  const body = {
    question: q,
    mode: "dettagliata",
    context: document.getElementById("context").value
  };

  const res = await fetch("/api/answer", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(body)
  });
  const data = await res.json();

  // Se servono dati obbligatori -> evidenzia
  if (data?.meta?.required_keys) {
    data.meta.required_keys.forEach(k => {
      const el = document.getElementById(`w_${k}`);
      if (el) el.classList.add("required");
    });
  }

  document.getElementById("answer-box").innerText = data.answer || "Nessuna risposta.";
}
