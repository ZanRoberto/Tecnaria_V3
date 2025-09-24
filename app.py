# app.py
import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from openai import OpenAI

app = FastAPI(title="Tecnaria Bot - ChatGPT esteso universale")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata nelle Environment Variables di Render.")
client = OpenAI(api_key=OPENAI_API_KEY)

PROMPT = """
Sei un tecnico esperto di TECNARIA S.p.A. (tutte le linee prodotto dell’azienda di Bassano del Grappa: connettori per calcestruzzo/legno/acciaio e laterocemento – es. CTF, CTL, CTCEM, VCEM – più accessori di posa, attrezzaggi correlati come SPIT P560, reti/armature di completamento, indicazioni di posa e verifiche).
Rispondi SEMPRE in italiano con stile “ChatGPT esteso tecnico”: apertura sintetica (Sì:/No:/Dipende:) + spiegazione chiara + punti operativi concisi.
Formato obbligatorio:
- Frase iniziale netta (es. “Sì, ma …” oppure “No: …”).
- Subito dopo, elenco puntato di 3–6 punti con istruzioni pratiche e numeri utili (passi, diametri, spessori, attrezzi, modelli).
- Chiudi con una riga che indirizza alle istruzioni di posa o alle schede ufficiali quando servono.

Regole di dominio (usa quando pertinenti alla domanda; non inventare codici o valori non citati):
- CTF su acciaio/lamiera grecata: fissaggio a freddo con chiodatrice a cartuccia SPIT P560 + kit/adattatori dedicati Tecnaria; altre macchine NON ammesse. Ogni CTF: 2 chiodi HSBR14 con propulsori; posa “sopra la trave” anche con lamiera. Requisiti tipici: trave acciaio ≥ 6 mm; lamiera ok 1×1,5 mm oppure 2×1,0 mm ben aderenti.
- CTL su tavolato legno + soletta leggera: preferisci CTL MAXI 12/040 con soletta ~5 cm; testa del connettore sopra la rete (rete a metà spessore), copriferri rispettati. Viti Ø10 tip. 100 mm (se interposto/tavolato > 25–30 mm usa 120 mm; disponibili 140 mm). Se interferenze, valuta CTL MAXI 12/030 mantenendo la testa sopra la rete.
- CTCEM/VCEM per laterocemento: fissaggio meccanico a secco, senza resine. Procedura tipica: piccola incisione per alloggiare la piastra dentata, preforo Ø11 mm prof. ~75 mm, pulizia polvere, avvitatura del piolo con avvitatore a percussione/frizione fino a battuta.
- Per altri prodotti/linee Tecnaria (anche non citati qui): mantieni lo stesso stile tecnico-operativo; se servono dati specifici di catalogo (codici, portate, PRd/P0, classi cls), esprimi la procedura e rimanda alle schede ufficiali Tecnaria per i valori tabellari.

Copertura extra (domande non tecniche):
- Se la domanda non riguarda connettori o posa, rispondi comunque nello stesso stile tecnico-esteso fornendo informazioni aziendali (profilo, contatti, PEC, orari, mission, certificazioni, rete commerciale, assistenza).

Tono e stile:
- Evita frasi che sminuiscono (niente “non è un connettore”): spiega sempre cosa fa e quando si usa uno strumento/prodotto.
- Sii pratico e diretto: specifica modelli, diametri, lunghezze, spessori, “quando sì / quando no”.
- Se la domanda è vaga, scegli l’interpretazione più utile e dai comunque una risposta operativa.
- Una sola risposta completa (non 3 varianti). Evita tabelle salvo indispensabile.
"""

class AskBody(BaseModel):
    question: str

@app.get("/")
def health():
    return {"status": "ok", "service": "Tecnaria Bot - ChatGPT esteso universale"}

@app.post("/ask")
def ask(body: AskBody):
    try:
        resp = client.responses.create(
            model="gpt-5-turbo",
            instructions=PROMPT,
            input=[{"role": "user", "content": body.question.strip()}],
            temperature=0.3,
            max_output_tokens=1200
        )
        try:
            print("✅ Modello usato:", resp.model)
        except Exception:
            pass
        return {"answer": resp.output_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

HTML = """<!doctype html>
<html lang="it"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Tecnaria Bot</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto;max-width:860px;margin:40px auto;padding:0 16px}
h1{font-size:22px;margin:0 0 10px}
.card{border:1px solid #e5e7eb;border-radius:14px;padding:16px;margin:14px 0;box-shadow:0 1px 6px rgba(0,0,0,.04)}
#answer{white-space:pre-wrap;line-height:1.45}
textarea{width:100%;height:110px;padding:12px;border:1px solid #d1d5db;border-radius:12px;font:inherit}
button{padding:10px 16px;border-radius:12px;border:1px solid #111827;background:#111827;color:#fff;font-weight:600;cursor:pointer}
button:disabled{opacity:.55;cursor:not-allowed}
.muted{color:#6b7280;font-size:13px}
</style></head>
<body>
<h1>🛠️ Tecnaria Bot – ChatGPT esteso</h1>
<div class="card">
  <label for="q" class="muted">Fai una domanda (es. “Con i CTF posso usare una chiodatrice qualsiasi?”)</label>
  <textarea id="q" placeholder="Scrivi qui la tua domanda..."></textarea>
  <div style="margin-top:10px">
    <button id="send">Chiedi</button>
    <span id="status" class="muted" style="margin-left:10px;"></span>
  </div>
</div>
<div class="card">
  <div class="muted">Risposta</div>
  <div id="answer"></div>
</div>
<script>
const btn=document.getElementById('send');
const qEl=document.getElementById('q');
const ans=document.getElementById('answer');
const statusEl=document.getElementById('status');
async function ask(){
  const q=qEl.value.trim();
  if(!q){qEl.focus();return;}
  btn.disabled=true;statusEl.textContent="Sto rispondendo…";ans.textContent="";
  try{
    const r=await fetch("/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question:q})});
    const data=await r.json();
    if(r.ok){ans.textContent=data.answer||JSON.stringify(data);}else{ans.textContent="Errore: "+(data.detail||r.statusText);}
  }catch(e){ans.textContent="Errore di rete: "+e.message;}
  finally{btn.disabled=false;statusEl.textContent="";}
}
btn.addEventListener('click',ask);
qEl.addEventListener('keydown',e=>{if(e.key==="Enter"&&(e.metaKey||e.ctrlKey))ask();});
</script>
</body></html>"""

@app.get("/ui", response_class=HTMLResponse)
def ui():
    return HTML
