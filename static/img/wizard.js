/* =========================================================================
   TecnariaBot - wizard.js (COMPLETO)
   ========================================================================= */

const WIZARD_VERSION = "2025-09-19-ctf-02";
let CURRENT_MODE = "dettagliata";   // default = C
const STORAGE_PREFIX = "tecnaria_wizard_";

/* ===========================
   1) SCHEMI DEI CAMPI (CONFIG)
   =========================== */
const WIZARD_SCHEMAS = {
  CTF_CALC: [
    { id:"h_lamiera", label:"Altezza lamiera (mm)", type:"number", required:true,  placeholder:"55",  min:"30", step:"1" },
    { id:"s_soletta", label:"Spessore soletta (mm)", type:"number", required:true, placeholder:"60",  min:"40", step:"5" },
    { id:"vled",      label:"V_L,Ed (kN/m)",        type:"number", required:true, placeholder:"150", min:"1",  step:"1" },
    { id:"cls",       label:"Classe cls",           type:"text",   required:true, placeholder:"C30/37" },
    { id:"passo",     label:"Passo gola (mm)",      type:"number", required:true, placeholder:"150", min:"50", step:"5" },
    { id:"dir",       label:"Direzione lamiera",    type:"select", required:true, options:["","longitudinale","trasversale"] }
  ],
  // futuri schemi: CTL_CALC, CEME_CALC, DIAPASON_CALC, P560_CALC, ecc.
};

/* ===================================
   2) RICONOSCIMENTO INTENTO (frontend)
   =================================== */
// -> copre plurali, sinonimi, varianti di scrittura (V_L,Ed / V L Ed / kn/m…), “quanti/quante”
function detectWizardSchema(text) {
  const t = (text || "").toLowerCase();

  // indizi di calcolo/scelta tecnica
  const calcHints =
    /(altezza|altezze|dimension|dimensiona|dimensionamento|v[\s_.,-]*l[\s_.,-]*,?ed|v[\s_.,-]*l|kn\/?m|portata|quanti|quante|quale\s+altezza|numero\s+connettori|pr[\s_.,-]*d)/;

  // ambito CTF/solaio/lamiera
  const isCTF =
    /(ctf|connettore|connettori|lamiera|soletta|solaio|gola|passo\s+gola)/;

  if (isCTF.test(t) && calcHints.test(t)) return "CTF_CALC";

  // se parla di CTF ma non troviamo calcHints, lasciamo decidere al backend;
  // se il backend chiede parametri, forzeremo comunque CTF_CALC
  return null;
}

/* =====================================================
   3) RENDER DEL WIZARD + SYNC -> TEXTAREA "context"
   ===================================================== */
function renderWizard(schemaKey) {
  const box = document.getElementById("wizard-dynamic");
  if (!box) return;
  const fieldsWrap = document.getElementById("wizard-fields") || box;

  fieldsWrap.innerHTML = "";
  box.dataset.schema = "";

  if (!schemaKey || !WIZARD_SCHEMAS[schemaKey]) {
    box.style.display = "none";
    return;
  }

  const fields = WIZARD_SCHEMAS[schemaKey];

  fields.forEach(f => {
    const wrap = document.createElement("label");
    wrap.style.display = "block";
    wrap.style.marginTop = "8px";
    wrap.textContent = f.label;

    let el;
    if (f.type === "select") {
      el = document.createElement("select");
      (f.options || ["","—"]).forEach(opt => {
        const o = document.createElement("option");
        o.value = opt;
        o.textContent = opt || "—";
        el.appendChild(o);
      });
    } else {
      el = document.createElement("input");
      el.type = f.type || "text";
      if (f.placeholder) el.placeholder = f.placeholder;
      if (f.min) el.min = f.min;
      if (f.step) el.step = f.step;
      el.inputMode = (f.type === "number") ? "decimal" : "text";
    }
    el.id = `w_${f.id}`;
    el.dataset.required = f.required ? "1" : "0";
    if (f.required) el.classList.add("required");

    el.addEventListener("input", () => {
      syncWizardToContext();
      saveParams(schemaKey);
    });

    wrap.appendChild(el);
    fieldsWrap.appendChild(wrap);
  });

  box.style.display = "block";
  box.dataset.schema = schemaKey;

  // ripristina eventuali valori salvati
  loadParams(schemaKey);
  // sincronizza subito il context
  syncWizardToContext();
}

function buildContextFromSchema(schemaKey) {
  const fields = WIZARD_SCHEMAS[schemaKey] || [];
  const parts = [];

  fields.forEach(f => {
    const el = document.getElementById(`w_${f.id}`);
    if (!el) return;
    const v = el.value && String(el.value).trim();
    if (!v) return;

    if (schemaKey === "CTF_CALC") {
      if (f.id === "h_lamiera")      parts.push(`lamiera H${v}`);
      else if (f.id === "s_soletta") parts.push(`soletta ${v} mm`);
      else if (f.id === "vled")      parts.push(`V_L,Ed=${v} kN/m`);
      else if (f.id === "cls")       parts.push(`cls ${v}`);
      else if (f.id === "passo")     parts.push(`passo gola ${v} mm`);
      else if (f.id === "dir")       parts.push(`lamiera ${v}`);
      else                           parts.push(`${f.label}: ${v}`);
    } else {
      parts.push(`${f.label}: ${v}`);
    }
  });

  return parts.join(", ");
}

function syncWizardToContext() {
  const box = document.getElementById("wizard-dynamic");
  const ta  = document.getElementById("context");
  if (!box || !ta) return;

  const schemaKey = box.dataset.schema || "";
  if (!schemaKey) return;

  ta.value = buildContextFromSchema(schemaKey);
}

/* ===============================
   4) SALVATAGGIO / RIPRISTINO DATI
   =============================== */
function saveParams(schemaKey) {
  try {
    const fields = WIZARD_SCHEMAS[schemaKey] || [];
    const data = {};
    fields.forEach(f => {
      const el = document.getElementById(`w_${f.id}`);
      data[f.id] = el ? el.value : "";
    });
    localStorage.setItem(STORAGE_PREFIX + schemaKey, JSON.stringify(data));
  } catch (e) {}
}

function loadParams(schemaKey) {
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + schemaKey);
    if (!raw) return;
    const data = JSON.parse(raw);
    Object.keys(data || {}).forEach(id => {
      const el = document.getElementById(`w_${id}`);
      if (el) el.value = data[id];
    });
  } catch (e) {}
}

/* ======================
   5) VALIDAZIONE WIZARD
   ====================== */
function validateWizard(schemaKey) {
  const box = document.getElementById("wizard-dynamic");
  if (!box || !schemaKey) return true;

  const fields = WIZARD_SCHEMAS[schemaKey] || [];
  const missing = [];
  fields.forEach(f => {
    if (!f.required) return;
    const el = document.getElementById(`w_${f.id}`);
    const val = el && String(el.value).trim();
    if (!val) missing.push(f.label);
  });

  if (missing.length) {
    alert("Per procedere servono: " + missing.join(", "));
    return false;
  }
  return true;
}

/* =========================
   6) MODALITÀ A/B/C (pulsanti)
   ========================= */
function setMode(m) {
  CURRENT_MODE = m || "dettagliata";

  const map = { breve: "btnA", standard: "btnB", dettagliata: "btnC" };
  ["btnA","btnB","btnC"].forEach(id => document.getElementById(id)?.classList.remove("active"));
  const btnId = map[CURRENT_MODE];
  if (btnId) document.getElementById(btnId)?.classList.add("active");
}

/* =====================================
   7) INVIO DOMANDA → /api/answer (fetch)
   ===================================== */
async function sendQuestion() {
  const questionEl = document.getElementById("question");
  const contextEl  = document.getElementById("context");
  const answerBox  = document.getElementById("answer-box");

  const question = questionEl ? (questionEl.value || "") : "";
  const context  = contextEl ? (contextEl.value || "") : "";

  // rilevamento anticipato: accendi wizard se necessario
  const preSchema = detectWizardSchema(question);
  if (preSchema) renderWizard(preSchema);

  // validazione se il wizard è visibile
  const wizBox = document.getElementById("wizard-dynamic");
  const schemaKey = wizBox && wizBox.style.display !== "none" ? (wizBox.dataset.schema || "") : "";
  if (schemaKey) {
    const ok = validateWizard(schemaKey);
    if (!ok) return;
  }

  // invio (no-store per evitare cache nelle risposte)
  let data = {};
  try {
    const res = await fetch("/api/answer", {
      method: "POST",
      headers: { "Content-Type":"application/json", "X-Wizard-Version": WIZARD_VERSION },
      cache: "no-store",
      body: JSON.stringify({ question, mode: CURRENT_MODE, context })
    });
    try { data = await res.json(); } catch(e) {}
  } catch (err) {
    if (answerBox) answerBox.innerText = "Errore di rete. Riprova.";
    return;
  }

  // Se il backend segnala che servono parametri → forza il wizard e evidenzia i campi richiesti
  if (data?.meta?.needs_params) {
    const schema = detectWizardSchema(question) || "CTF_CALC";
    renderWizard(schema);

    const fieldMap = {
      "passo gola": "passo",
      "V_L,Ed": "vled",
      "cls": "cls",
      "direzione lamiera": "dir",
      // estendibile per altri schemi/prodotti
    };
    (data.meta.required_keys || []).forEach(k => {
      const id = "w_" + (fieldMap[k] || k);
      document.getElementById(id)?.classList.add("required");
    });

    if (answerBox) {
      answerBox.textContent =
        'Per completare la risposta, inserisci i dati richiesti nel riquadro sopra e premi "Invia" di nuovo.';
    }
    return;
  }

  // stampa la risposta (e gli eventuali allegati)
  if (answerBox) {
    let out = data?.answer || "Nessuna risposta.";
    if (data?.attachments && Array.isArray(data.attachments) && data.attachments.length) {
      out += "\n\nAllegati / Note collegate:\n";
      data.attachments.forEach(a => {
        const href = a.url || a.path || a.href || a;
        if (href) out += `- ${href}\n`;
      });
    }
    answerBox.textContent = out;
  }
}

/* ======================================
   8) INIZIALIZZAZIONE DOPO CARICAMENTO DOM
   ====================================== */
document.addEventListener("DOMContentLoaded", () => {
  // wiring pulsanti (se presenti)
  document.getElementById("btnA")?.addEventListener("click", () => setMode("breve"));
  document.getElementById("btnB")?.addEventListener("click", () => setMode("standard"));
  document.getElementById("btnC")?.addEventListener("click", () => setMode("dettagliata"));
  document.getElementById("btnSend")?.addEventListener("click", sendQuestion);

  // toggle wizard mentre si digita la domanda
  const q = document.getElementById("question");
  if (q) {
    const toggle = () => {
      const schema = detectWizardSchema(q.value);
      if (schema) renderWizard(schema);
      else {
        const box = document.getElementById("wizard-dynamic");
        if (box) { box.style.display = "none"; box.dataset.schema = ""; }
      }
    };
    q.addEventListener("input", toggle);
    toggle();
  }

  // evidenzia modalità di default
  setMode(CURRENT_MODE);
});
