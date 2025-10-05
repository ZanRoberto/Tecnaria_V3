import os, re, json, html, time
from typing import List, Dict, Any
from urllib.parse import quote_plus
import requests
from bs4 import BeautifulSoup

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

APP_TITLE = "Tecnaria QA Bot"
ALLOWED_DOMAINS = os.getenv("ALLOWED_DOMAINS", "tecnaria.com,spit.eu,spitpaslode.com").split(",")
ALLOWED_DOMAINS = [d.strip().lower() for d in ALLOWED_DOMAINS if d.strip()]
SINAPSI_FILE = os.getenv("SINAPSI_FILE", "./static/data/sinapsi_rules.json")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
PREFERRED_LANGS = os.getenv("PREFERRED_LANGS", "it,en,de,fr,es").split(",")

# -----------------------------
# Sinapsi: pre-caricamento
# -----------------------------
class Rule:
    def __init__(self, r: Dict[str, Any]):
        self.id = r.get("id", "")
        self.mode = r.get("mode", "augment")
        self.lang = r.get("lang", "it")
        self.answer = r.get("answer", "").strip()
        pat = r.get("pattern", ".*")
        self.pattern = re.compile(pat, re.IGNORECASE)

    def matches(self, q: str) -> bool:
        return bool(self.pattern.search(q or ""))

def load_sinapsi_rules(path: str) -> List[Rule]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        rules = data.get("rules", data)  # accetta anche la forma lista
        return [Rule(r) for r in rules]
    except Exception as e:
        print(f"[WARN] Impossibile caricare Sinapsi: {e}")
        return []

SINAPSI_RULES: List[Rule] = load_sinapsi_rules(SINAPSI_FILE)

# -----------------------------
# Utils
# -----------------------------
def clean_text(txt: str) -> str:
    # rimuove caratteri binari / PDF garbage e normalizza spazi
    txt = txt.replace("\x00", " ")
    txt = re.sub(r"[^\S\r\n]+", " ", txt)
    txt = re.sub(r"[\u0000-\u001F\u007F]", " ", txt)  # ctrl chars
    txt = re.sub(r"\s+\n", "\n", txt)
    txt = re.sub(r"\n\s+", "\n", txt)
    return txt.strip()

def clip(txt: str, n: int = 550) -> str:
    txt = txt.strip()
    if len(txt) <= n: 
        return txt
    cut = txt[:n]
    cut = cut.rsplit(" ", 1)[0]
    return cut + "…"

def is_allowed(url: str) -> bool:
    url_l = url.lower()
    return any(("://"+d) in url_l or (".%s" % d) in url_l or url_l.endswith(d) or f"//{d}/" in url_l for d in ALLOWED_DOMAINS)

def brave_search(query: str, count: int = 5) -> List[Dict[str, str]]:
    if not BRAVE_API_KEY:
        return []
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": BRAVE_API_KEY
    }
    # forza filtro su domini consentiti
    site_filter = " OR ".join([f"site:{d}" for d in ALLOWED_DOMAINS])
    q = f"{query} ({site_filter})"
    url = f"https://api.search.brave.com/res/v1/web/search?q={quote_plus(q)}&count={count}"
    try:
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code != 200:
            return []
        data = r.json()
        items = []
        for res in (data.get("web", {}).get("results", []) or []):
            href = res.get("url") or res.get("link") or ""
            if href and is_allowed(href):
                title = res.get("title") or res.get("pageTitle") or ""
                desc = res.get("description") or ""
                items.append({"url": href, "title": title, "description": desc})
        return items
    except Exception as e:
        print(f"[WARN] Brave search error: {e}")
        return []

def fetch_snippet(url: str) -> str:
    try:
        r = requests.get(url, timeout=12)
        ct = r.headers.get("Content-Type", "")
        if "text/html" in ct:
            soup = BeautifulSoup(r.text, "html.parser")
            # meta description o primi paragrafi utili
            md = soup.find("meta", {"name": "description"})
            if md and md.get("content"):
                return clean_text(md["content"])
            ps = soup.find_all("p")
            for p in ps[:5]:
                t = clean_text(p.get_text(" ", strip=True))
                if len(t) > 60:
                    return clip(t, 600)
            # fallback al titolo
            if soup.title:
                return clip(clean_text(soup.title.get_text(" ", strip=True)), 200)
            return ""
        else:
            # Evita di iniettare binari (PDF/XML), nessun parsing qui
            return ""
    except Exception:
        return ""

def compose_narrative(q: str, web_hits: List[Dict[str, str]]) -> Dict[str, Any]:
    # testo principale in frasi, senza bullet/asterischi
    lines = []
    used = []
    for hit in web_hits[:3]:
        snippet = fetch_snippet(hit["url"])
        if snippet:
            used.append({"title": hit["title"] or hit["url"], "url": hit["url"]})
            lines.append(snippet)

    body = ""
    if lines:
        # unisci 2–3 frasi, pulizia leggera
        joined = " ".join(lines)
        joined = re.sub(r"\s{2,}", " ", joined)
        # evita testo troppo lungo
        body = clip(joined, 900)

    return {"body": body, "sources": used}

def apply_sinapsi(q: str, base_html: str, mode_hits: List[Rule]) -> str:
    # Se c'è un override, vince lui
    override_rules = [r for r in mode_hits if r.mode == "override"]
    if override_rules:
        # prendi il più specifico (il primo che ha matchato)
        txt = override_rules[0].answer
        return render_card(title="Risposta Tecnaria", paragraphs=[txt], sources=[], footer="Risposta da Sinapsi (override).")

    # Altrimenti augment (+ postscript)
    augment_parts = [r.answer for r in mode_hits if r.mode == "augment" and r.answer]
    post_parts = [r.answer for r in mode_hits if r.mode == "postscript" and r.answer]

    if not augment_parts and not post_parts:
        return base_html  # nessun intervento

    # Inserisci le frasi di augment in coda (o se base vuoto, diventano il corpo)
    # Ricomponi il contenuto HTML estraendo body e sources dal base_html
    content = extract_card_content(base_html)
    paragraphs = []
    if content["body"]:
        paragraphs.append(content["body"])
    if augment_parts:
        paragraphs.append(" ".join(augment_parts))
    if post_parts:
        paragraphs.append(" ".join(post_parts))

    return render_card(
        title="Risposta Tecnaria",
        paragraphs=paragraphs,
        sources=content["sources"],
        footer="Sintesi da web ufficiale + rifinitura Sinapsi."
    )

def extract_card_content(card_html: str) -> Dict[str, Any]:
    # parsing molto semplice: estrae <p> principali e link <a> nella sezione fonti
    body = ""
    sources = []
    try:
        soup = BeautifulSoup(card_html, "html.parser")
        # primo .card -> paragrafi
        card = soup.find("div", {"class": "card"})
        if card:
            ps = card.find_all("p")
            if ps:
                # unisci i primi paragrafi (ignorando la riga "⏱")
                texts = []
                for p in ps:
                    t = p.get_text(" ", strip=True)
                    if t and not t.startswith("⏱"):
                        texts.append(t)
                body = " ".join(texts).strip()

        # Fonti: link all’interno del card
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if href and is_allowed(href):
                sources.append({"title": a.get_text(strip=True) or href, "url": href})
    except Exception:
        pass

    return {"body": body, "sources": sources}

def render_sources(sources: List[Dict[str, str]]) -> str:
    if not sources:
        return ""
    items = []
    for s in sources:
        title = html.escape(s.get("title") or "Fonte")
        url = html.escape(s.get("url") or "#")
        items.append(f"📎 <a href='{url}' target='_blank'>{title}</a>")
    return "<div class='sources'>" + "<br>".join(items) + "</div>"

def render_card(title: str, paragraphs: List[str], sources: List[Dict[str, str]], footer: str = "", took_ms: int = None) -> str:
    safe_ps = [html.escape(clean_text(p)) for p in paragraphs if p and p.strip()]
    body_html = "".join([f"<p>{p}</p>" for p in safe_ps])

    src_html = render_sources(sources)
    timer = f"<div class='card' style='margin-top:10px'><small>⏱ {took_ms} ms</small></div>" if took_ms is not None else ""

    footer_html = f"<p><small>{html.escape(footer)}</small></p>" if footer else ""
    return f"""
    <div class="card">
      <h2>{html.escape(title)}</h2>
      {body_html}
      {src_html}
      {footer_html}
    </div>{timer}
    """

def answer_html_for(q: str) -> str:
    t0 = time.time()

    # 1) applica regole Sinapsi per capire se c'è override o augment
    hits = [r for r in SINAPSI_RULES if r.matches(q)]

    # 2) se c'è override: risposta diretta (niente web)
    if any(r.mode == "override" for r in hits):
        html_out = apply_sinapsi(q, base_html="", mode_hits=hits)
        took = int((time.time() - t0) * 1000)
        return render_card("Risposta Tecnaria", [BeautifulSoup(html_out, "html.parser").get_text(" ", strip=True)], [], "Risposta da Sinapsi (override).", took_ms=took)

    # 3) altrimenti: cerca SOLO su domini consentiti (Brave). NIENTE fonti esterne.
    web_hits = brave_search(q, count=6)

    # Se niente Brave o niente risultati sul dominio → risposta minima, ma sicura
    if not web_hits:
        base = render_card("Risposta Tecnaria",
                           [ "Ho consultato esclusivamente le fonti ufficiali consentite. Al momento non ho trovato un estratto testuale adatto, ma posso indicare le pagine utili qui sotto." ],
                           [ {"title": d, "url": f"https://{d}"} for d in ALLOWED_DOMAINS ],
                           "Nota: ricerca limitata ai domini autorizzati.")
        # poi Sinapsi in augment se presente
        html_out = apply_sinapsi(q, base, hits)
        return html_out

    # 4) comporre narrativa (pulita) dai primi snippet
    comp = compose_narrative(q, web_hits)

    # Se proprio non c'è testo, mostra comunque i link filtrati
    if not comp["body"]:
        comp["body"] = "Ho selezionato le pagine ufficiali più pertinenti. Apri i collegamenti per i dettagli tecnici completi."
        comp["sources"] = web_hits[:4]

    base = render_card("Risposta Tecnaria", [comp["body"]], comp["sources"], "Sintesi da fonti ufficiali (domini consentiti).", took_ms=int((time.time()-t0)*1000))

    # 5) augment/postscript di Sinapsi (se ci sono)
    final_html = apply_sinapsi(q, base, hits)
    return final_html

# -----------------------------
# FastAPI
# -----------------------------
app = FastAPI(title=APP_TITLE)

# static (index.html)
if not os.path.exists("./static"):
    os.makedirs("./static", exist_ok=True)
if not os.path.exists("./static/data"):
    os.makedirs("./static/data", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
def home():
    # se c'è un index.html nella /static, servilo
    idx = os.path.join("static", "index.html")
    if os.path.exists(idx):
        with open(idx, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("""<pre>{"ok":true,"msg":"Use /ask or place static/index.html"}</pre>""")

@app.get("/ping")
def ping():
    return {"ok": True, "msg": "pong"}

@app.get("/health")
def health():
    return {
        "status": "ok",
        "web_search": {
            "provider": "brave",
            "brave_key": bool(BRAVE_API_KEY),
            "preferred_domains": ALLOWED_DOMAINS
        },
        "critici": {
            "dir": "./static/data",
            "exists": os.path.exists("./static/data"),
            "sinapsi_file": SINAPSI_FILE,
            "sinapsi_loaded": len(SINAPSI_RULES)
        }
    }

@app.get("/ask", response_class=HTMLResponse)
def ask_get(q: str = Query(..., description="Domanda")):
    q = (q or "").strip()
    if not q:
        return HTMLResponse(render_card("Risposta Tecnaria", ["Inserisci una domanda."], [], ""))
    html_answer = answer_html_for(q)
    return HTMLResponse(html_answer)

@app.post("/api/ask", response_class=JSONResponse)
async def ask_post(payload: Dict[str, Any]):
    q = (payload or {}).get("q", "").strip()
    if not q:
        return JSONResponse({"ok": False, "error": "Missing q"})
    html_answer = answer_html_for(q)
    return JSONResponse({"ok": True, "answer_html": html_answer})
