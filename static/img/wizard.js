// Mini-wizard per CALCOLO CTF con parametri ETA (inclusi t e nr)
const WIZ_FIELDS = [
  // Geometria
  { id:"lamiera", label:"Altezza lamiera (mm)", type:"number", placeholder:"55", required:true },
  { id:"soletta", label:"Spessore soletta (mm)", type:"number", placeholder:"60", required:true },
  // Azioni/cls
  { id:"VLed",   label:"V_L,Ed (kN/m)", type:"number", placeholder:"150", required:true },
  { id:"cls",    label:"Classe cls", type:"text", placeholder:"C30/37", required:true },
  // Passi
  { id:"s_gola", label:"Passo gola (mm)", type:"number", placeholder:"150", required:true },
  { id:"dir",    label:"Direzione lamiera", type:"select", options:["longitudinale","trasversale"], required:true },
  { id:"s_long", label:"Passo lungo trave (mm)", type:"number", placeholder:"200", required:true },
  // Lamiera – parametri per k_t (OBBLIGATORI su lamiera)
  { id:"t",      label:"Spessore lamiera t (mm)", type:"number", step:"0.1", placeholder:"1.0", required:true },
  { id:"nr",     label:"N° connettori per gola (nr)", type:"number", placeholder:"1", required:true }
];

function buildWizard(container){
  if (!container) return;
  container.innerHTML = "";
  const groups = [
    { title:"Geometria", ids:["lamiera","soletta"] },
    { title:"Azioni e cls", ids:["VLed","cls"] },
    { title:"Passi", ids:["s_gola","dir","s_long"] },
    { title:"Parametri lamiera (ETA)", ids:["t","nr"] }
  ];
  groups.forEach(g=>{
    const hdr = document.createElement("div");
    hdr.innerHTML = `<div class="hint" style="margin-top:.6rem">${g.title}</div>`;
    container.appendChild(hdr);
    const grid = document.createElement("div");
    grid.className = "field-grid";
    g.ids.forEach(fid=>{
      const f = WIZ_FIELDS.find(x=>x.id===fid);
      const wrap = document.createElement("div");
      const lbl = document.createElement("label"); lbl.textContent = f.label;
      let input;
      if (f.type==="select"){
        input = document.createElement("select");
        f.options.forEach(o=>{
          const op = document.createElement("option");
          op.value=o; op.textContent=o; input.appendChild(op);
        });
      } else {
        input = document.createElement("input");
        input.type = f.type; 
        if (f.step) input.step = f.step;
        input.placeholder = f.placeholder || "";
      }
      input.id = "wiz_"+f.id; input.className = "wiz";
      wrap.appendChild(lbl); wrap.appendChild(input);
      grid.appendChild(wrap);
    });
    container.appendChild(grid);
  });
}

function fillContextFromWizard(textarea){
  if(!textarea) return;
  const get = id => document.getElementById("wiz_"+id)?.value;
  const parts = [];
  const lam = get("lamiera");  if(lam) parts.push("lamiera H"+lam);
  const sol = get("soletta");  if(sol) parts.push("soletta "+sol+" mm");
  const V   = get("VLed");     if(V)   parts.push("V_L,Ed="+V+" kN/m");
  const cls = get("cls");      if(cls) parts.push("cls "+cls);
  const pg  = get("s_gola");   if(pg)  parts.push("passo gola "+pg+" mm");
  const dir = get("dir");      if(dir) parts.push("lamiera "+dir);
  const sl  = get("s_long");   if(sl)  parts.push("passo lungo trave "+sl+" mm");
  const t   = get("t");        if(t)   parts.push("t="+t+" mm");
  const nr  = get("nr");       if(nr)  parts.push("nr="+nr);
  textarea.value = parts.join(", ");
}

// Esporta
window.buildWizard = buildWizard;
window.fillContextFromWizard = fillContextFromWizard;
