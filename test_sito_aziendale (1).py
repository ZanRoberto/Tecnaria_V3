"""Collaudo del sito come fonte (sito_aziendale.py). La rete e' finta: un piccolo sito in memoria."""
import pytest
from fastapi import HTTPException

import sito_aziendale as sito
from test_documenti_aziendali import H, ask_answer, env  # noqa: F401  (fixture condivisa)
from test_whatsapp_core import FakeLLM, core  # noqa: F401

LUNGO = " Testo descrittivo della bicicletta elettrica per la citta', con telaio in alluminio." * 4

SITO = {
    "https://volta-ebike.example/robots.txt": "User-agent: *\nDisallow: /privato/\n",
    "https://volta-ebike.example/sitemap.xml": (
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://volta-ebike.example/</loc></url>"
        "<url><loc>https://volta-ebike.example/urban-500</loc></url>"
        "<url><loc>https://volta-ebike.example/privato/listino-interno</loc></url>"
        "<url><loc>https://altro-sito.example/pagina</loc></url>"
        "<url><loc>https://volta-ebike.example/vuota</loc></url></urlset>"),
    "https://volta-ebike.example/": ("<html><head><title>Volta E-Bike</title><script>var x=1;</script></head><body>"
                                    "<nav>Menu Home Contatti</nav><h1>Volta E-Bike</h1><p>Negozio in Via Roma 10, Milano."
                                    + LUNGO + "</p><footer>Copyright</footer></body></html>"),
    "https://volta-ebike.example/urban-500": ("<html><head><title>Urban 500</title></head><body><h1>Urban 500</h1>"
                                             "<p>Prezzo: 1.990 euro.</p><p>Autonomia fino a 80 km in modalita' Eco."
                                             + LUNGO + "</p></body></html>"),
    "https://volta-ebike.example/vuota": "<html><body><div id='app'></div><script>render()</script></body></html>",
}


@pytest.fixture
def web(monkeypatch):
    pagine = dict(SITO)

    def scarica(url):
        if url not in pagine:
            raise HTTPException(422, "Il sito ha risposto 404")
        return url, "text/html" if not url.endswith((".txt", ".xml")) else "text/plain", pagine[url]
    monkeypatch.setattr(sito, "scarica", scarica)
    monkeypatch.setattr(sito, "leggi_con_browser", lambda url: None)    # niente rete nei collaudi
    return pagine


def test_indirizzi_interni_mai(monkeypatch):
    assert not sito._indirizzo_pubblico("http://127.0.0.1/admin")
    assert not sito._indirizzo_pubblico("http://10.0.0.5/")
    assert not sito._indirizzo_pubblico("http://169.254.169.254/latest/meta-data")
    assert not sito._indirizzo_pubblico("file:///etc/passwd")
    assert not sito._indirizzo_pubblico("ftp://example.com/x")


def test_testo_della_pagina_senza_menu_e_script():
    letto = sito.leggi_html(SITO["https://volta-ebike.example/"])
    assert letto["titolo"] == "Volta E-Bike"
    assert "Via Roma 10" in letto["testo"] and "var x" not in letto["testo"]
    assert "Menu Home" not in letto["testo"] and "Copyright" not in letto["testo"]


def test_scopri_dalla_mappa_rispetta_robots_e_dominio(web):
    r = sito.scopri("volta-ebike.example")
    assert r["fonte"] == "mappa del sito"
    pagine = {p["url"]: p["consentita"] for p in r["pagine"]}
    assert "https://altro-sito.example/pagina" not in pagine           # altro sito: fuori
    assert pagine["https://volta-ebike.example/urban-500"] is True
    assert pagine["https://volta-ebike.example/privato/listino-interno"] is False


def test_pagine_nei_documenti_con_versioni_e_cambiamenti(env, web, monkeypatch):  # noqa: F811
    h = H("lago")
    r = env.http.post("/api/documents/lago/sito/importa", headers=h, json={
        "sito": "https://volta-ebike.example/", "frequenza_giorni": 7,
        "pagine": ["https://volta-ebike.example/", "https://volta-ebike.example/urban-500",
                   "https://volta-ebike.example/vuota", "https://volta-ebike.example/privato/listino-interno"]}).json()
    assert (r["nuova"], r["saltata"]) == (2, 2)
    motivi = " ".join(p.get("motivo", "") for p in r["pagine"])
    assert "JavaScript" in motivi and "robots.txt" in motivi
    elenco = env.http.get("/api/documents/lago", headers=h).json()["documents"]
    urban = [d for d in elenco if d["titolo"] == "Urban 500"]
    assert len(urban) == 1 and urban[0]["tipo"] == "sito"
    # rilettura senza cambiamenti: nessuna versione nuova
    r = env.http.post("/api/documents/lago/sito/aggiorna", headers=h, json={"sito": "https://volta-ebike.example/"}).json()
    assert r["invariata"] == 2 and r["nuova"] == r["cambiata"] == 0
    # il prezzo cambia sul sito: versione nuova, la vecchia nello storico, con cosa e' cambiato
    web["https://volta-ebike.example/urban-500"] = web["https://volta-ebike.example/urban-500"].replace("1.990", "2.190")
    r = env.http.post("/api/documents/lago/sito/aggiorna", headers=h, json={"sito": "https://volta-ebike.example/"}).json()
    assert r["cambiata"] == 1
    cambi = next(p for p in r["pagine"] if p["esito"] == "cambiata")["cambi"]
    assert any("1.990" in c and c.startswith("tolto") for c in cambi)
    assert any("2.190" in c and c.startswith("aggiunto") for c in cambi)
    attivi = [d for d in env.http.get("/api/documents/lago", headers=h).json()["documents"] if d["titolo"] == "Urban 500"]
    assert len(attivi) == 1 and attivi[0]["versione"] == 2
    storico = env.http.get("/api/documents/lago?storico=true", headers=h).json()["documents"]
    assert any(d["titolo"] == "Urban 500" and d["stato"] == "sostituito" for d in storico)
    # la risposta usa la pagina del sito, con il prezzo nuovo
    llm = FakeLLM(env.core, answer="La Urban 500 costa 2.190 euro.")
    monkeypatch.setattr(env.core, "client", llm)
    ask_answer(env, "Quanto costa la Urban 500?")
    contesto = str(llm.calls)
    assert "2.190" in contesto and "1.990" not in contesto
    # un altro cliente non vede il sito di lago
    assert env.http.get("/api/documents/covolo/sito", headers=H("covolo")).json()["siti"] == []
    assert env.http.get("/api/documents/lago/sito", headers=h).json()["siti"][0]["frequenza_giorni"] == 7
    # smettere di seguire: le pagine restano nei documenti
    env.http.post("/api/documents/lago/sito/smetti", headers=h, json={"sito": "https://volta-ebike.example/"})
    assert env.http.get("/api/documents/lago/sito", headers=h).json()["siti"] == []
    assert any(d["titolo"] == "Urban 500" for d in env.http.get("/api/documents/lago", headers=h).json()["documents"])


def test_aggiornamento_automatico_solo_se_scaduto(env, web):  # noqa: F811
    env.http.post("/api/documents/lago/sito/importa", headers=H("lago"), json={
        "sito": "https://volta-ebike.example/", "frequenza_giorni": 7, "pagine": ["https://volta-ebike.example/urban-500"]})
    import time
    assert sito.da_aggiornare(time.time()) == []
    clienti = [c for c, _ in sito.da_aggiornare(time.time() + 8 * 86400)]
    assert clienti and all(c == clienti[0] for c in clienti)


def test_chiave_sbagliata_e_frequenza(env, web):  # noqa: F811
    assert env.http.post("/api/documents/lago/sito/scopri", json={"url": "volta-ebike.example"},
                         headers={"X-Applaudi-Tenant-Key": "sbagliata"}).status_code in (401, 403)
    assert env.http.post("/api/documents/lago/sito/importa", headers=H("lago"), json={
        "sito": "https://volta-ebike.example/", "frequenza_giorni": 3,
        "pagine": ["https://volta-ebike.example/"]}).status_code == 400


def test_pagina_javascript_letta_con_il_browser(env, web, monkeypatch):  # noqa: F811
    """24ott2026: gruppoitas.it e' fatto con JavaScript e il lettore saltava tutte le pagine.
    Ora la pagina vuota si apre con il lettore a browser; e la pagina indicata entra nell'elenco
    anche se la mappa del sito non la elenca."""
    aperte = []

    def browser(url):
        aperte.append(url)
        return {"titolo": "Agenzia di Bassano", "testo": "Agenzia ITAS di Bassano del Grappa, Via Roma 1. " * 6}
    monkeypatch.setattr(sito, "leggi_con_browser", browser)
    r = sito.scopri("https://volta-ebike.example/vuota?codice=A342")
    assert r["pagine"][0]["url"] == "https://volta-ebike.example/vuota?codice=A342"
    web["https://volta-ebike.example/vuota?codice=A342"] = web["https://volta-ebike.example/vuota"]
    r = env.http.post("/api/documents/lago/sito/importa", headers=H("lago"), json={
        "sito": "https://volta-ebike.example/", "frequenza_giorni": 7,
        "pagine": ["https://volta-ebike.example/vuota?codice=A342", "https://volta-ebike.example/urban-500"]}).json()
    assert r["nuova"] == 2 and aperte == ["https://volta-ebike.example/vuota?codice=A342"]
    agenzia = next(p for p in r["pagine"] if p["url"].endswith("A342"))
    assert agenzia["browser"] is True
    titoli = [d["titolo"] for d in env.http.get("/api/documents/lago", headers=H("lago")).json()["documents"]]
    assert "Agenzia di Bassano" in titoli


def test_markdown_del_browser_ripulito():
    testo = sito._pulisci_markdown("# Titolo\n![logo](http://x/l.png)\nTelefono: [0424 123](tel:0424123)\n---\n| Orari | 9-12 |")
    assert testo == "Titolo\nTelefono: 0424 123\nOrari | 9-12"
