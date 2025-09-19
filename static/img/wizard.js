// Campi per CALCOLO CTF
const WIZ_FIELDS = [
  { id:"lamiera", label:"Altezza lamiera (mm)", type:"number", placeholder:"55", required:true },
  { id:"soletta", label:"Spessore soletta (mm)", type:"number", placeholder:"60", required:true },
  { id:"VLed",   label:"V_L,Ed (kN/m)", type:"number", placeholder:"150", required:true },
  { id:"cls",    label:"Classe cls", type:"text", placeholder:"C30/37", required:true },
  { id:"s_gola", label:"Passo gola (mm)", type:"number", placeholder:"150", required:true },
  { id:"dir",    label:"Direzione lamiera", type:"select", options:["longitudinale","trasversale"], required:true },
  { id:"s_long", label:"Passo lungo trave (mm)", type:"number", placeholder:"200", required:true }
];

function buildWizard(container){
  if (!container) return;
  container.innerHTML = "";
  const groups = [
    { title:"Geometria", ids:["lamiera","soletta"] },
    { title:"Azioni e cls", ids:["VLed","cls"] },
    { title:"Passi", ids:["s_gola","dir","s_long"] }
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
        input.type = f.type; input.placeholder = f.placeholder || "";
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
  textarea.value = parts.join(", ");
}

// Esporta
window.buildWizard = buildWizard;
window.fillContextFromWizard = fillContextFromWizard;

