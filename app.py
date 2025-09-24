# app.py
import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from openai import OpenAI

# ------------------- FastAPI -------------------
app = FastAPI(title="Tecnaria Bot - ChatGPT esteso universale")

# ------------------- OpenAI client -------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY non impostata nelle Environment Variables di Render.")
client = OpenAI(api_key=OPENAI_API_KEY)

# ------------------- Prompt universale -------------------
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

# ------------------- Schemi I/O -------------------
class AskBody(BaseModel):
    question: str

# ------------------- Healthcheck -------------------
@app.get("/")
def health():
    return {"status": "ok", "service": "Tecnaria Bot - ChatGPT esteso universale"}

# ------------------- Endpoint principale (Chat Completions) -------------------
@app.post("/ask")
def ask(body: AskBody):
    try:
        chat = client.chat.completions.create(
            model="gpt-5-turbo",  # modello forzato
            messages=[
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": body.question.strip()},
            ],
            temperature=0.3,
            max_tokens=1200,
        )
        # Log del modello usato
        try:
            print("✅ Modello usato:", chat.model)
        except Exception:
            pass
        answer = chat.choices[0].message.content
        return {"answer": answer}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ------------------- UI (pagina chat a /ui) -------------------
HTML = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Tecnaria Bot – ChatGPT esteso</title>
<style>
  :root{
    --bg:#0b1220; --card:#121a2b; --muted:#93a3b8; --accent:#4f8cff; --accent2:#22c55e; --danger:#ef4444;
    --border:#22304d; --text:#e6edf7; --chip:#1f2a44;
  }
  *{box-sizing:border-box}
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto;background:linear-gradient(160deg,#0b1220 0%,#0f1630 60%,#101a34 100%);
       color:var(--text);margin:0;padding:24px}
  .wrap{max-width:980px;margin:0 auto}
  header{display:flex;align-items:center;gap:12px;margin-bottom:18px}
  header .logo{width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,#4f8cff, #22c55e)}
  h1{font-size:22px;margin:0}
  .card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:18px;margin:14px 0;box-shadow:0 10px 30px rgba(0,0,0,.25)}
  textarea{width:100%;min-height:120px;padding:14px;border-radius:14px;border:1px solid var(--border);background:#0c1426;color:var(--text);font:inherit}
  button{padding:10px 16px;border-radius:12px;border:1px solid var(--accent);background:var(--accent);color:white;font-weight:700;cursor:pointer}
  button:disabled{opacity:.6;cursor:not-allowed}
  .muted{color:var(--muted);font-size:13px}
  .row{display:flex;gap:16px;flex-wrap:wrap}
  .col{flex:1 1 320px}
  .chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
  .chip{background:var(--chip);border:1px solid var(--border);color:var(--muted);padding:6px 10px;border-radius:999px;font-size:12px;cursor:pointer}
  .chip:hover{color:var(--text);border-color:var(--accent)}
  #answer{white-space:pre-wrap;line-height:1.55; font-size:15px}
  footer{margin-top:22px;color:var(--muted);font-size:12px}
  .note{border-left:4px solid var(--accent2);padding:10px 12px;background:#0f1a2e;border-radius:10px}
  .error{color:var(--danger)}
  img.hero{width:100%;max-height:220px;object-fit:cover;border-radius:14px;border:1px solid var(--border)}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo"></div>
    <div>
      <h1>🛠️ Tecnaria Bot – ChatGPT esteso</h1>
      <div class="muted">Fai domande su prodotti e posa Tecnaria (CTF, CTL, CTCEM/VCEM, P560, ecc.)</div>
    </div>
  </header>

  <div class="card">
    <img class="hero" alt="Tecnaria – sistemi collaboranti" src="https://images.unsplash.com/photo-1581091014534-8987c1d647c1?q=80&w=1600&auto=format&fit=crop"/>
    <div class="row" style="margin-top:12px">
      <div class="col">
        <label for="q" class="muted">Domanda</label>
        <textarea id="q" placeholder="Es.: Con i CTF posso usare una chiodatrice qualsiasi? Oppure: CTL su tavolato 2 cm con soletta 5 cm: quale modello?"></textarea>
        <div style="margin-top:10px; display:flex; gap:10px; align-items:center;">
          <button id="send">Chiedi</button>
          <span id="status" class="muted"></span>
        </div>
        <div class="chips">
          <div class="chip" data-q="Con i CTF posso usare una chiodatrice qualsiasi?">CTF & chiodatrice</div>
          <div class="chip" data-q="Tavolato 2 cm e soletta 5 cm: quale CTL MAXI uso?">CTL su tavolato</div>
          <div class="chip" data-q="I CTCEM per laterocemento richiedono resine?">CTCEM e resine</div>
          <div class="chip" data-q="Dove entra la SPIT P560 in un solaio con lamiera grecata?">P560 su lamiera</div>
          <div class="chip" data-q="Quali sono le certificazioni e i contatti aziendali Tecnaria?">Info aziendali</div>
        </div>
      </div>
      <div class="col">
        <div class="muted" style="margin-bottom:6px">Risposta</div>
        <div id="answer" class="card" style="min-height:160px;background:#0c1426"></div>
        <div id="error" class="error"></div>
      </div>
    </div>
  </div>

  <div class="card note">
    <strong>Note utili:</strong>
    <ul style="margin-top:8px">
      <li>Scrivi come faresti con un tecnico: indica <em>spessori, lunghezze, diametri</em>, tipo di solaio e travi.</li>
      <li>Lo stile della risposta è sempre “ChatGPT esteso tecnico”: apertura Sì/No/Dipende + bullet operativi + chiusura con riferimento alle istruzioni di posa.</li>
      <li>Il modello AI usato è GPT-5 Turbo tramite API OpenAI, con prompt tarato sui prodotti Tecnaria.</li>
    </ul>
  </div>

  <footer>
    © Tecnaria Bot – Demo UI. Questa pagina invia richieste POST a <code>/ask</code>.
  </footer>
</div>

<script>
const btn = document.getElementById('send');
const qEl = document.getElementById('q');
const ans = document.getElementById('answer');
const err = document.getElementById('error');
const statusEl = document.getElementById('status');
document.querySelectorAll('.chip').forEach(ch => ch.addEventListener('click', () => { qEl.value = ch.dataset.q; qEl.focus(); }));

async function ask() {
  const q = qEl.value.trim();
  if (!q) { qEl.focus(); return; }
  btn.disabled = true; statusEl.textContent = "Sto rispondendo…"; ans.textContent = ""; err.textContent = "";
  try {
    const r = await fetch("/ask", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({question: q})
    });
    const data = await r.json();
    if (r.ok) {
      ans.textContent = data.answer || JSON.stringify(data);
    } else {
      err.textContent = "Errore: " + (data.detail || r.statusText);
    }
  } catch(e) {
    err.textContent = "Errore di rete: " + e.message;
  } finally {
    btn.disabled = false; statusEl.textContent = "";
  }
}
btn.addEventListener('click', ask);
qEl.addEventListener('keydown', (e)=>{ if(e.key==="Enter" && (e.metaKey||e.ctrlKey)) ask(); });
</script>
</body>
</html>"""

@app.get("/ui", response_class=HTMLResponse)
def ui():
    return HTML
