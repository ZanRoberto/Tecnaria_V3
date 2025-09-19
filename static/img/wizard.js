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
  // Ordine visivo a gruppi
  const groups = [
    { title:"Geometria", ids:["lamiera","soletta"] },
    { title:"Azioni e cls", ids:["VLed","cls"] },
    { title:"Passi", ids:["s_gola","dir","s_long"] }
  ];
  groups.forEach(g=>{
    const h = document.createElement("div");
    h.innerHTML = `<div class="hint" style="margin-top:.6rem;">${g.title}</div>`;
    container.appendChild(h);
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
      }else{
        input = document.createElement("input");
        input.type = f.type; input.placeholder = f.placeholder || "";
      }
      input.id = "wiz_"+f.id; input.className="wiz";
      wrap.appendChild(lbl); wrap.appendChild(input);
      grid.appendChild(wrap);
    });
    container.appendChild(grid);
  });
}

// Costruisce il context dal contenuto dei campi
function fillContextFromWizard(textarea){
  if(!textarea) return;
  const parts = [];
  const g = id => document.getElementById("wiz_"+id);

  const lam = g("lamiera")?.value;     if(lam) parts.push("lamiera H"+lam);
  const sol = g("soletta")?.value;     if(sol) parts.push("soletta "+sol+" mm");
  const V   = g("VLed")?.value;        if(V) parts.push("V_L,Ed="+V+" kN/m");
  const cls = g("cls")?.value;         if(cls) parts.push("cls "+cls);
  const pg  = g("s_gola")?.value;      if(pg) parts.push("passo gola "+pg+" mm");
  const dir = g("dir")?.value;         if(dir) parts.push("lamiera "+dir);
  const sl  = g("s_long")?.value;      if(sl) parts.push("passo lungo trave "+sl+" mm");

  textarea.value = parts.join(", ");
}

// Espone per index.html
window.buildWizard = buildWizard;
window.fillContextFromWizard = fillContextFromWizard;
