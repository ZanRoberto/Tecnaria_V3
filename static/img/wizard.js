const WIZARD_SCHEMAS = {
  CTF_CALC: [
    { id:"lamiera", label:"Altezza lamiera (mm)", type:"number", required:true, placeholder:"55" },
    { id:"soletta", label:"Spessore soletta (mm)", type:"number", required:true, placeholder:"60" },
    { id:"VLed", label:"V_L,Ed (kN/m)", type:"number", required:true, placeholder:"150" },
    { id:"cls", label:"Classe cls", type:"text", required:true, placeholder:"C30/37" },
    { id:"s_gola", label:"Passo gola (mm)", type:"number", required:true, placeholder:"150" },
    { id:"dir", label:"Direzione lamiera", type:"text", required:true, placeholder:"longitudinale/trasversale" },
    { id:"s_long", label:"Passo lungo trave (mm)", type:"number", required:true, placeholder:"200" }
  ]
};

function buildContextFromSchema(schema) {
  let parts = [];
  schema.forEach(f => {
    let el = document.getElementById("wiz_"+f.id);
    if (el && el.value) {
      let v = el.value;
      if (f.id === "lamiera") parts.push("lamiera H"+v);
      else if (f.id === "soletta") parts.push("soletta "+v+" mm");
      else if (f.id === "VLed") parts.push("V_L,Ed="+v+" kN/m");
      else if (f.id === "cls") parts.push("cls "+v);
      else if (f.id === "s_gola") parts.push("passo gola "+v+" mm");
      else if (f.id === "dir") parts.push("lamiera "+v);
      else if (f.id === "s_long") parts.push("passo lungo trave "+v+" mm");
    }
  });
  return parts.join(", ");
}
