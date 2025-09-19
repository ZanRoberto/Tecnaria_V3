/* =========================================================================
   TecnariaBot - wizard.js (completo)
   - Gestione modalità A/B/C
   - Mini-wizard dinamico per domande di calcolo (CTF)
   - Compilazione automatica del campo "Dati tecnici richiesti"
   - Validazione, salvataggio localStorage, evidenziazione required_keys
   ========================================================================= */

let CURRENT_MODE = 'dettagliata';   // default C
const STORAGE_PREFIX = "tecnaria_wizard_";

/* ===========================
   1) SCHEMI DEI CAMPI (CONFIG)
   =========================== */
const WIZARD_SCHEMAS = {
  // Campi per calcolo CTF (esempio base)
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
function detectWizardSchema(text) {
  const t = (text || "").toLowerCase();

  // è un intento di calcolo/scelta tecnica?
  const isCalc = /(altezza|dimension|v_l,?ed|portata|quale\s+altezza|numero\s+connettori|pr[ _.,-]?d|kn\/m)/.test(t);
  if (!isCalc) return null;

  // routing per prodotto/ambito
  if (/(^|\s)ctf(\s|$)|lamiera|soletta|solaio|gola/.test(t)) return "CTF_CALC";

  return null; // nessun wizard specifico
}

/* =====================================================
   3) RENDER DEL WIZARD + SYNC -> TEXTAREA "context"
   ===================================================== */
function renderWizard(schemaKey) {
  const box = document.getElementById("wizard-dynamic");
  if (!box) return;
  box.innerHTML = "";
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
    }
    el.id = `w_${f.id}`;
    el.dataset.required = f.required ? "1" : "0";
    if (f.required) el.classList.add("required");

    // Cambiando un campo → aggiorna il context + salva
    el.addEventListener("input", () => {
      syncWizardToContext();
      saveParams(schemaKey);
    });

    wrap.appendChild(el);
    box.appendChild(wrap);
  });

  box.style.display = "block";
  box.dataset.schema = schemaKey;

  // carica eventuali valori salvati
  loadParams(schemaKey);
  // aggiorna subito il context
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

    // formattazione CTF dedicata
    if (schemaKey === "CTF_CALC") {
      if (f.id === "h_lamiera")   parts.push(`lamiera H${v}`);
      else if (f.id === "s_soletta") parts.push(`soletta ${v} mm`);
      else if (f.id === "vled")      parts.push(`V_L,Ed=${v} kN/m`);
      else if (f.id === "cls")       parts.push(`cls ${v}`);
      else if (f.id === "passo")     parts.push(`passo gola ${v} mm`);
      else if (f.id === "dir")       parts.push(`lamiera ${v}`);
      else parts.push(`${f.label}: ${v}`);
    } else {
      // fallback: etichetta=valore
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
  const fields = WIZARD_SCHEMAS[schemaKey] || [];
  const data = {};
  fields.forEach(f => {
    const el = document.getElementById(`w_${f.id}`);
    data[f.id] = el ? el.value : "";
  });
  localStorage.setItem(STORAGE_PREFIX + schemaKey, JSON.stringify(data));
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
  } catch (e) {
    // ignore
  }
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
  CURRENT_MODE = m || 'dettagliata';

  // feedback visivo (se esistono i bottoni con questi ID)
  const map = { breve: 'btnA', standard: 'btnB', dettagliata: 'btnC' };
  ['btnA','btnB','btnC'].forEach(id => document.getElementById(id)?.classList.remove('active'));
  const btnId = map[CURRENT_MODE];
  if (btnId) document.getElementById(btnId)?.classList.add('active');
}

/* =====================================
   7) INVIO DOMANDA → /api/answer (fetch)
   ===================================== */
async function sendQuestion() {
  const questionEl = document.getElementById('question');
  const contextEl  = document.getElementById('context');
  const answerBox  = document.getElementById('answer-box');

  const question = questionEl ? (questionEl.value || "") : "";
  const context  = contextEl ? (contextEl.value || "") : "";

  // accensione wizard anticipata (UX)
  const preSchema = detectWizardSchema(question);
  if (preSchema) renderWizard(preSchema);

  // se wizard visibile ed è un calcolo -> valida i campi
  const wizBox = document.getElementById("wizard-dynamic");
  const schemaKey = wizBox && wizBox.style.display !== "none" ? (wizBox.dataset.schema || "") : "";
  if (schemaKey) {
    const ok = validateWizard(schemaKey);
    if (!ok) return; // ferma invio
  }

  // invio al backend
  try {
    const res = await fetch('/api/answer', {
      method: 'POST',
      headers: { 'Content-Type':'application/json' },
      body: JSON.stringify({ question, mode: CURRENT_MODE, context })
    });

    let data = {};
    try { data = await res.json(); } catch(e) {}

    // Se il backend chiede parametri → evidenzia, mostra wizard e stop
    if (data?.meta?.needs_params) {
      const schema = detectWizardSchema(question) || "CTF_CALC";
      renderWizard(schema);

      // Mappa chiave → id campo del wizard
      const fieldMap = {
        "passo gola": "passo",
        "V_L,Ed": "vled",
        "cls": "cls",
        "direzione lamiera": "dir",
        // estendibile per altri schemi
      };
      (data.meta.required_keys || []).forEach(k => {
        const id = 'w_' + (fieldMap[k] || k);
        const el = document.getElementById(id);
        if (el) el.classList.add('required');
      });

      if (answerBox) {
        answerBox.innerText = 'Per completare la risposta, inserisci i dati richiesti nel riquadro sopra e premi "Invia" di nuovo.';
      }
      return;
    }

    // Stampa risposta e, se presenti, eventuali allegati
    if (answerBox) {
      let html = "";
      if (data?.answer) html += data.answer;
      if (data?.attachments && Array.isArray(data.attachments) && data.attachments.length) {
        html += "\n\nAllegati / Note collegate:\n";
        data.attachments.forEach(a => {
          const href = a.url || a.path || a.href || a;
          if (href) html += `- ${href}\n`;
        });
      }
      // mostra come testo semplice per compatibilità; se vuoi HTML, usa innerHTML
      answerBox.textContent = html || "Nessuna risposta.";
    }

  } catch (err) {
    if (answerBox) answerBox.innerText = "Errore di rete. Riprova.";
  }
}

/* ======================================
   8) INIZIALIZZAZIONE DOPO CARICAMENTO DOM
   ====================================== */
document.addEventListener("DOMContentLoaded", () => {
  // wiring pulsanti (se esistono in pagina)
  document.getElementById('btnA')?.addEventListener('click', () => setMode('breve'));
  document.getElementById('btnB')?.addEventListener('click', () => setMode('standard'));
  document.getElementById('btnC')?.addEventListener('click', () => setMode('dettagliata'));
  document.getElementById('btnSend')?.addEventListener('click', sendQuestion);

  // toggle automatico del wizard mentre l’utente digita la domanda
  const q = document.getElementById("question");
  if (q) {
    const toggle = () => {
      const schema = detectWizardSchema(q.value);
      if (schema) renderWizard(schema); else {
        const box = document.getElementById("wizard-dynamic");
        if (box) { box.style.display = "none"; box.dataset.schema = ""; }
      }
    };
    q.addEventListener("input", toggle);
    toggle(); // prima valutazione
  }

  // evidenzia default mode C
  setMode(CURRENT_MODE);
});
