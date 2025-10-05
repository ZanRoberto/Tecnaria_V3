import os, re, json, html, textwrap, time
from typing import List, Dict, Any
from urllib.parse import urlparse, urlencode

import requests
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# ----------------------------
# CONFIG & ENV
# ----------------------------
ALLOWED_DOMAINS = set([d.strip().lower() for d in os.getenv("PREFERRED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com").split(",") if d.strip()])
SEARCH_PROVIDER   = os.getenv("SEARCH_PROVIDER","brave").lower()
BRAVE_API_KEY     = os.getenv("BRAVE_API_KEY","").strip()
BING_API_KEY      = os.getenv("BING_API_KEY","").strip()
MIN_WEB_SCORE     = float(os.getenv("MIN_WEB_SCORE","0.35"))
WEB_TIMEOUT       = float(os.getenv("WEB_TIMEOUT","6"))
WEB_RETRIES       = int(os.getenv("WEB_RETRIES","2"))
FETCH_WEB_FIRST   = os.getenv("FETCH_WEB_FIRST","1") in ("1","true","True")
MODE              = os.getenv("MODE","web_first_then_local")
CRITICI_DIR       = os.getenv("CRITICI_DIR","static/data")
SINAPSI_FILE      = os.path.join(CRITICI_DIR, "sinapsi_rules.json")
OPENAI_MODEL      = os.getenv("OPENAI_MODEL","gpt-4o")  # (non usiamo chiamate esterne in questo file)
DEBUG             = os.getenv("DEBUG","0") in ("1","true","True")

# ----------------------------
# FASTAPI
# ----------------------------
app = FastAPI(title="Tecnaria QA Bot")

# Static: serve index se presente
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# ----------------------------
# UTILS
# ----------------------------
def now_ms() -> int:
    return int(time.time()*1000)

def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except:
        return ""

def is_allowed(url: str) -> bool:
    d = domain_of(url)
    return any(d.endswith(dom) for dom in ALLOWED_DOMAINS)

def clean_pdf_noise(text: str) -> str:
    # rimuove intestazioni binarie tipo “%PDF-1.7 ...”
    text = re.sub(r"^%PDF-.*?(?:\r?\n)+", "", text.strip(), flags=re.DOTALL)
    # rimuove blocchi binari lunghi (XObject, stream compressi ecc.)
    text = re.sub(r"(?:obj|endobj|stream|endstream)[\s\S]{0,200}", "", text, flags=re.IGNORECASE)
    return text.strip()

def bullets_to_html(items: List[str]) -> str:
    items = [html.escape(x).replace("\n"," ").strip() for x in items if x.strip()]
    if not items: return ""
    return "<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>"

def paragraphize(text: str) -> str:
    # niente asterischi ** ** — restituiamo <p> puliti
    text = text.replace("**","")
    chunks = [x.strip() for x in re.split(r"\n\s*\n", text.strip()) if x.strip()]
    return "".join(f"<p>{html.escape(c)}</p>" for c in chunks)

def render_answer(title: str, body_html: str, sources: List[Dict[str,str]]=None, note: str="") -> str:
    src_html = ""
    if sources:
        rows = []
        for s in sources:
            if not s.get("url") or not is_allowed(s["url"]):
                continue
            label = html.escape(s.get("title") or s["url"])
            rows.append(f"📎 <a href='{html.escape(s['url'])}' target='_blank'>{label}</a>")
        if rows:
            src_html = "<div class='sources'><div class='src-title'>Fonti</div>" + "<br>".join(rows) + "</div>"

    note_html = f"<p><small>{html.escape(note)}</small></p>" if note else ""
    return f"""
    <div class="card">
      <h2>{html.escape(title)}</h2>
      {body_html}
      {src_html}
      {note_html}
    </div>
    """.strip()

# ----------------------------
# SINAPSI
# ----------------------------
SINAPSI: List[Dict[str,Any]] = []
def load_sinapsi() -> int:
    global SINAPSI
    try:
        with open(SINAPSI_FILE,"r",encoding="utf-8") as f:
            data = json.load(f)
        # normalizzazione minima
        norm = []
        for r in data:
            if not r.get("id") or not r.get("pattern") or not r.get("answer"):
                continue
            r.setdefault("mode","augment")  # override | augment | postscript
            r.setdefault("lang","it")
            norm.append(r)
        SINAPSI = norm
        return len(SINAPSI)
    except Exception as e:
        SINAPSI = []
        return 0

def sinapsi_hook(q: str, base_title: str, base_html: str) -> str:
    # Applica la prima regola che matcha in ordine: override > augment > postscript
    matches_override = []
    matches_augment  = []
    matches_ps       = []
    for r in SINAPSI:
        try:
            if re.search(r["pattern"], q, flags=re.IGNORECASE):
                if r["mode"] == "override":
                    matches_override.append(r)
                elif r["mode"] == "augment":
                    matches_augment.append(r)
                else:
                    matches_ps.append(r)
        except re.error:
            continue

    # 1) override: restituiamo solo Sinapsi (già narrativo).
    if matches_override:
        r = matches_override[0]
        body = paragraphize(r["answer"])
        return render_answer(base_title, body, [], "Risposta da Sinapsi (override).")

    # 2) augment: appiccichiamo sotto come sezione “In breve”
    if matches_augment:
        augment = matches_augment[0]["answer"]
        aug_html = paragraphize(augment)
        base_html = base_html + "<hr>" + "<h3>In breve (Sinapsi)</h3>" + aug_html

    # 3) postscript: nota finale breve
    if matches_ps:
        ps = matches_ps[0]["answer"]
        base_html = base_html + paragraphize("\n\nPS · " + ps)

    return base_html

# ----------------------------
# RICERCA (WHITELIST DURA)
# ----------------------------
def brave_search(query: str) -> List[Dict[str, Any]]:
    if not BRAVE_API_KEY:
        return []
    url = "https://api.search.brave.com/res/v1/web/search"
    params = {
        "q": f"{query} site:tecnaria.com OR site:spit.eu OR site:spitpaslode.com",
        "count": 6,
        "country": "it",
        "search_lang": "it",
        "spellcheck": 1,
        "freshness": "year"
    }
    headers = {"Accept":"application/json","X-Subscription-Token":BRAVE_API_KEY}
    for _ in range(WEB_RETRIES):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=WEB_TIMEOUT)
            if r.status_code != 200:
                continue
            js = r.json()
            out = []
            for item in (js.get("web",{}) or {}).get("results",[]):
                if "url" not in item: 
                    continue
                if not is_allowed(item["url"]):
                    continue
                out.append({
                    "title": item.get("title"),
                    "url":   item.get("url"),
                    "description": item.get("description","")
                })
            return out
        except Exception:
            continue
    return []

# ----------------------------
# ESTRATTORE SNIPPET (HTML/PDF)
# ----------------------------
def fetch_snippet(u: str) -> str:
    # niente contenuto binario in risposta: per PDF mostriamo solo snippet testuale breve
    try:
        if not is_allowed(u):
            return ""
        h = requests.head(u, timeout=WEB_TIMEOUT, allow_redirects=True)
        ctype = h.headers.get("content-type","").lower()
        if "pdf" in ctype:
            # NON scarichiamo tutto: fermiamoci a un messaggio “estratto PDF”
            return "Estratto tecnico da PDF ufficiale."
        # HTML
        r = requests.get(u, timeout=WEB_TIMEOUT)
        txt = r.text
        txt = clean_pdf_noise(txt)
        # micro-snippet da <title> o prime frasi visibili
        m = re.search(r"<title[^>]*>(.*?)</title>", txt, flags=re.IGNORECASE|re.DOTALL)
        if m:
            title = re.sub(r"\s+"," ", html.unescape(m.group(1))).strip()
            return title[:240]
        # fallback: prime parole
        body = re.sub(r"<[^>]+>"," ", txt)
        body = re.sub(r"\s+"," ", body).strip()
        return body[:240]
    except Exception:
        return ""

# ----------------------------
# RENDER NARRATIVO
# ----------------------------
def compose_narrative(query: str, hits: List[Dict[str,Any]]) -> str:
    """
    Crea una risposta “tecnico-commerciale” breve (4-6 frasi) + fonti cliccabili.
    Evitiamo elenchi con asterischi: paragrafi puliti.
    """
    if not hits:
        body = paragraphize("Non ho trovato una pagina ufficiale esatta sul tema all’interno dei domini consentiti. "
                            "Posso riformulare la ricerca oppure indirizzarti alla documentazione Tecnaria.")
        return render_answer("Risposta Tecnaria", body, [])

    # Dedup & piccoli indizi per cucire 4–6 frasi
    sel = hits[:4]
    phrases = []
    # Frase 1 — inquadramento generico
    phrases.append("Ho consultato la documentazione ufficiale Tecnaria e i partner tecnici per fornirti un riepilogo affidabile.")
    # Frase 2–4 — proviamo a usare i title/snippet come indizi (senza incollare schifezze)
    for h in sel:
        t = (h.get("title") or "").strip()
        d = (h.get("description") or "").strip()
        if t:
            phrases.append(f"Dalla pagina “{t}” emergono indicazioni utili per il caso in esame.")
        elif d:
            phrases.append(d)
    # Limita a 5–6 frasi totali
    phrases = phrases[:5]
    # Chiusa
    phrases.append("Trovi i dettagli completi nelle schede tecniche e nelle guide collegate qui sotto.")
    body = paragraphize("\n\n".join(phrases))
    return render_answer("Risposta Tecnaria", body, sel)

# ----------------------------
# ENDPOINTS
# ----------------------------
@app.get("/", response_class=HTMLResponse)
def home():
    # prova a servire static/index.html, altrimenti mini messaggio
    index_path = os.path.join("static","index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html; charset=utf-8")
    return HTMLResponse("<pre>{\"ok\":true,\"msg\":\"Use /ask or place static/index.html\"}</pre>")

@app.get("/ping")
def ping():
    return {"ok": True, "pong": True}

@app.get("/health")
def health():
    return {
        "status":"ok",
        "web_search":{
            "provider": SEARCH_PROVIDER,
            "brave_key": bool(BRAVE_API_KEY),
            "bing_key":  bool(BING_API_KEY),
            "preferred_domains": list(ALLOWED_DOMAINS),
            "min_web_score": MIN_WEB_SCORE
        },
        "critici":{
            "dir": CRITICI_DIR,
            "exists": os.path.isdir(CRITICI_DIR),
            "sinapsi_file": SINAPSI_FILE,
            "sinapsi_loaded": len(SINAPSI)
        }
    }

def answer_flow(q: str) -> str:
    # 1) ricerca SOLO nei domini whitelisted (hard gate)
    hits = brave_search(q) if FETCH_WEB_FIRST else []
    # 2) componi risposta narrativa breve + fonti
    html_body = compose_narrative(q, hits)
    # 3) aggancio Sinapsi (override/augment/postscript)
    html_out = sinapsi_hook(q, "Risposta Tecnaria", html_body)
    return html_out

@app.get("/ask", response_class=HTMLResponse)
def ask_get(q: str = Query(..., min_length=2)):
    start = now_ms()
    out_html = answer_flow(q)
    if DEBUG:
        out_html += f"<div class='card' style='margin-top:10px'><small>⏱ {now_ms()-start} ms</small></div>"
    return HTMLResponse(out_html)

@app.post("/api/ask")
async def ask_post(payload: Dict[str,Any]):
    q = (payload or {}).get("q","").strip()
    if not q:
        return JSONResponse({"ok":True,"answer":"OK\n- Domanda vuota. Inserisci una richiesta valida.\n"})
    html_out = answer_flow(q)
    return HTMLResponse(html_out)

# ----------------------------
# BOOT
# ----------------------------
if __name__ == "__main__":
    load_sinapsi()
else:
    load_sinapsi()
