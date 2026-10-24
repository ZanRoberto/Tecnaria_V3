"""Collaudo del confronto per CONCETTI (confronto_catalogo.py + profilo_vita_tcm.json).

Le polizze sono i PDF di prova veri (polizze_prova.py) letti come in produzione. Simulato
solo il modello, con DUE letture: la seconda a volte non coincide con la prima, a volte
confonde un concetto con un altro (il termine di 3 mesi per annullare scambiato per la non
contestabilita', come ha fatto la lettura libera sul set informativo di Sara il 22ott2026).
Cosa deve restare vero:
- si confronta solo un concetto con lo stesso concetto, con il verso del catalogo;
- due letture diverse = «da verificare», mai un verdetto;
- un valore a condizione (carenza ridotta con referto) si mostra ma non si confronta;
- lo zero scritto a parole («senza maggiorazione») e' un valore;
- cio' che non si trova e' «non l'ho trovato», mai un vantaggio dell'altro.
"""
import json

import pytest

import confronto_catalogo as cc
import confronto_polizze as cp
import polizze_prova


def K(cid, valore, citazione, pagina, numero=None, unita=None, si_no=None, condizione="", forma=""):
    return {"id": cid, "valore": valore, "numero": numero, "unita": unita, "si_no": si_no,
            "condizione": condizione, "forma": forma, "citazione": citazione, "pagina": pagina}


A_COMUNE = [
    K("premio_annuo", "348,00 EUR annui", "348,00 EUR", "A1", 348, "EUR"),
    K("capitale_assicurato", "200.000,00 EUR", "200.000,00 EUR - costante per tutta la durata", "A1", 200000, "EUR"),
    K("andamento_capitale", "costante per tutta la durata", "200.000,00 EUR - costante per tutta la durata", "A1"),
    K("durata", "15 anni", "15 anni", "A1", 15, "anni"),
    K("carenza_malattia", "6 mesi", "Per i decessi dovuti a malattia la garanzia e' operante trascorsi 6 mesi dalla decorrenza",
      "A2", 6, "mesi"),
    K("carenza_malattia", "3 mesi con referto", "il periodo di carenza per decesso da malattia e' ridotto a 3 mesi a fronte "
      "della consegna di referto ematochimico", "A4", 3, "mesi", condizione="con referto ematochimico entro 90 giorni"),
    K("non_contestabilita", "24 mesi", "Trascorsi 24 mesi dalla decorrenza, la Compagnia non puo' contestare le dichiarazioni",
      "A2", 24, "mesi"),
    K("suicidio", "24 mesi", "Il contratto non e' operante qualora il decesso avvenga entro 24 mesi dalla decorrenza per atto "
      "volontario", "A2", 24, "mesi"),
    K("esclusione_sport_aerei", "esclusi paracadutismo, deltaplano e parapendio",
      "Paracadutismo e volo con mezzi non di linea compreso deltaplano e parapendio", "A2", si_no="si"),
    K("esclusione_immersione_metri", "esclusa oltre 40 metri", "Immersione subacquea con autorespiratore oltre i 40 metri di "
      "profondita'", "A2", 40, "metri"),
    K("guerra_tolleranza", "14 giorni", "salvo che l'assicurato vi sia coinvolto suo malgrado nei primi 14 giorni", "A2", 14,
      "giorni"),
    K("invalidita_soglia", "dal 60%", "pari o superiore al 60%", "A3", 60, "%"),
    K("invalidita_da_malattia", "anche da malattia", "Invalidita' permanente da infortunio o malattia", "A3", si_no="si"),
    K("doppio_capitale", "compreso", "Doppio capitale per infortunio compresa nel premio base", "A3", si_no="si"),
    K("malattie_gravi", "non prevista", "Malattie gravi non prevista dal prodotto", "A3", si_no="no"),
    K("inabilita_temporanea", "opzionale non attivata", "opzionale, non attivata in proposta", "A3", si_no="no"),
    K("caricamenti", "22,0%", "22,0% del premio lordo", "A3", 22, "%"),
    K("frazionamento", "senza maggiorazione", "annuale, semestrale, trimestrale, mensile - senza maggiorazione", "A1", 0, "%"),
    K("trattenuta_recesso", "25 EUR di spese", "premio versato al netto delle spese di emissione, pari a 25 EUR", "A3", 25, "EUR"),
    K("riscatto", "non previsto", "Il contratto non prevede valore di riscatto", "A1", si_no="no"),
]
B_COMUNE = [
    K("premio_annuo", "156,00 EUR", "156,00 EUR", "B1", 156, "EUR"),
    K("capitale_assicurato", "200.000,00 EUR iniziale", "200.000,00 EUR", "B1", 200000, "EUR"),
    K("andamento_capitale", "decrescente fino a zero, capitale medio 100.000 EUR",
      "decrescente in quote annue costanti fino a zero al termine del 15. anno (capitale medio 100.000 EUR circa)", "B1"),
    K("durata", "15 anni", "15 anni", "B1", 15, "anni"),
    K("carenza_malattia", "12 mesi", "Per i decessi conseguenti a malattia la garanzia opera decorsi 12 mesi dalla data di effetto",
      "B2", 12, "mesi"),
    K("non_contestabilita", "12 mesi", "Decorsi 12 mesi dalla data di effetto, la Societa' rinuncia a contestare le dichiarazioni",
      "B2", 12, "mesi"),
    K("suicidio", "12 mesi", "La garanzia non opera qualora il decesso avvenga entro 12 mesi dalla data di effetto per atto "
      "volontario", "B2", 12, "mesi"),
    K("esclusione_sport_aerei", "esclusi tutti gli sport aerei", "Sport aerei di ogni genere compreso il volo da diporto sportivo",
      "B2", si_no="si"),
    K("esclusione_immersione_metri", "esclusa oltre 30 metri", "Immersione subacquea oltre i 30 metri di profondita'", "B2", 30,
      "metri"),
    K("guerra_tolleranza", "nessuna tolleranza", "Guerra, insurrezione, eventi nucleari senza periodo di tolleranza", "B2", 0,
      "giorni"),
    K("esclusione_pandemie", "escluse", "Epidemie e pandemie dichiarate", "B2", si_no="si"),
    K("invalidita_soglia", "dal 66%", "superiore al 66%", "B3", 66, "%"),
    K("invalidita_da_malattia", "esclusa da malattia", "esclusa l'invalidita' da malattia", "B3", si_no="no"),
    K("doppio_capitale", "non previsto", "Doppio capitale per infortunio non prevista", "B3", si_no="no"),
    K("malattie_gravi", "compresa", "anticipazione del 30% del capitale residuo alla diagnosi", "B3", si_no="si"),
    K("inabilita_temporanea", "attivata, compresa nei 156 EUR", "48 EUR/anno compresi nei 156 EUR", "B3", si_no="si"),
    K("caricamenti", "31,5%", "31,5% del premio lordo", "B3", 31.5, "%"),
    K("frazionamento", "semestrale +3%", "il frazionamento semestrale comporta una maggiorazione del 3%", "B1", 3, "%"),
    K("trattenuta_recesso", "nessuna trattenuta", "integrale, senza trattenute", "B3", 0, "EUR"),
    K("revisione_premio", "al 5. e al 10. anno", "la Societa' si riserva la revisione della tariffa al 5. e al 10. anno", "B3",
      si_no="si"),
    K("riscatto", "non previsto", "Riscatto non previsto", "B3", si_no="no"),
]


class DueLetture:
    """Prima e seconda lettura. Nella seconda: A legge al contrario le competizioni motoristiche
    (da verificare), B scambia il termine di 3 mesi per annullare con la non contestabilita'
    (deve restare da verificare, non diventare «meglio B»)."""

    def __init__(self):
        self.chiamate = []
        self.sistemi = []

    def __call__(self, sistema, utente, max_tokens, temperatura):
        self.chiamate.append((utente, temperatura))
        self.sistemi.append(sistema)
        if "confronto" in utente[:40]:
            return json.dumps({"trappole": [{"testo": "B costa 156 EUR ma su capitale decrescente.", "voci": []},
                                            {"testo": "B fa risparmiare 250 EUR.", "voci": []}],
                               "azioni": [{"testo": "Chiedere il referto per ridurre la carenza di A.", "voci": []}],
                               "sintesi": "A protegge di piu', B costa meno su un capitale che scende."})
        seconda = utente.startswith(cc.SECONDA_LETTURA)
        lato_a = "[[A1]]" in utente
        risposta = list(A_COMUNE if lato_a else B_COMUNE)
        if lato_a:
            risposta.append(K("esclusione_motori", "escluse le gare su circuito",
                              "esclusa la partecipazione a gare, prove e allenamenti su circuito", "A2",
                              si_no="no" if seconda else "si"))
        elif seconda:
            risposta.append(K("non_contestabilita", "annullamento entro 3 mesi", "Decorsi 12 mesi dalla data di effetto",
                              "B2", 3, "mesi"))           # citazione vera, numero non suo: scartato dal controllo
        return json.dumps({"concetti": risposta})


@pytest.fixture(scope="module")
def pdf(tmp_path_factory):
    return polizze_prova.genera(str(tmp_path_factory.mktemp("polizze")))


@pytest.fixture
def modello(monkeypatch):
    m = DueLetture()
    monkeypatch.setattr(cp, "scrivi", m)
    return m


def _doc(path, titolo, did):
    return {"id": did, "titolo": titolo, "chunks": cp.pagine_pdf(path)}


@pytest.fixture
def r(pdf, modello):
    return cc.confronta([_doc(pdf[0], "Versilia", "a1")], [_doc(pdf[1], "Nordest", "b1")], "La nostra", "Concorrente")


def _riga(r, cid):
    return next(x for x in r["righe"] if x["concetto"] == cid)


def test_catalogo_valido():
    p = cc.carica_profilo("vita_tcm")
    assert p["stato"] == "da certificare" and len(p["concetti"]) >= 25
    for c in p["concetti"]:
        assert c["tipo"] in ("importo", "durata", "percentuale", "misura", "si_no", "testo")
        assert c["meglio"] in (None, "basso", "alto", "si", "no")
        assert (c["meglio"] in ("si", "no")) == (c["tipo"] == "si_no" and c["meglio"] is not None)
        assert c["domanda"].endswith(("?", ".")) and c["chiave"] in cp.ETICHETTE
    with pytest.raises(ValueError):
        cc.carica_profilo("../segreti")


def test_due_letture_per_ogni_parte(r, modello):
    letture = [u for u, _ in modello.chiamate if "FASCICOLO:" in u]
    prime = [u for u in letture if not u.startswith(cc.SECONDA_LETTURA)]
    seconde = [u for u in letture if u.startswith(cc.SECONDA_LETTURA)]
    assert len(prime) == len(seconde) >= 2
    # la seconda lettura e' formulata diversamente: concetti in ordine inverso, altra temperatura
    assert prime[0].index("premio_annuo") < prime[0].index("assistenza")
    assert seconde[0].index("premio_annuo") > seconde[0].index("assistenza")
    assert {t for u, t in modello.chiamate if u.startswith(cc.SECONDA_LETTURA)} == {0.3}


def test_si_confronta_solo_lo_stesso_concetto(r):
    voci = {v["id"]: v for v in r["a"]["voci"] + r["b"]["voci"]}
    for riga in r["righe"]:
        for i in riga["a"] + riga["b"]:
            assert voci[i]["concetto"] == riga["concetto"]


def test_giudizi_con_il_verso_del_catalogo(r):
    attesi = {"carenza_malattia": "meglio_a", "suicidio": "meglio_b", "esclusione_immersione_metri": "meglio_a",
              "guerra_tolleranza": "meglio_a", "invalidita_soglia": "meglio_a", "invalidita_da_malattia": "meglio_a",
              "doppio_capitale": "meglio_a", "malattie_gravi": "meglio_b", "inabilita_temporanea": "meglio_b",
              "caricamenti": "meglio_a", "frazionamento": "meglio_a", "trattenuta_recesso": "meglio_b",
              "riscatto": "uguale", "durata": "uguale", "esclusione_sport_aerei": "uguale",
              "andamento_capitale": "diverso"}
    for cid, g in attesi.items():
        assert _riga(r, cid)["giudizio"] == g, (cid, _riga(r, cid))
    assert _riga(r, "carenza_malattia")["perche"] == "A 6 mesi, B 12 mesi: per l'assicurato conta il valore più basso."
    # l'anno di B e i 12 mesi sono lo stesso valore: unita' convertite
    assert _riga(r, "suicidio")["perche"].startswith("A 24 mesi, B 12 mesi")


def test_la_condizione_si_mostra_ma_non_si_confronta(r):
    assert r["a"]["concetti"]["carenza_malattia"] == {"stato": "certo", "valore": 6.0}
    assert any("referto" in v["valore"] for v in r["a"]["voci"] if v["concetto"] == "carenza_malattia")


def test_letture_diverse_da_verificare(r):
    motori = _riga(r, "esclusione_motori")
    assert motori["giudizio"] == "non_confrontata"
    assert motori["perche"] == "In A le due letture non coincidono; non l'ho trovato in B: da verificare."
    # la seconda lettura di B ha confuso il termine di 3 mesi: non diventa «meglio B»
    nc = _riga(r, "non_contestabilita")
    assert nc["giudizio"] == "meglio_b" and nc["perche"].startswith("A 24 mesi, B 12 mesi")
    assert any(s.get("concetto") == "non_contestabilita" and s["motivo"].startswith("numeri non presenti")
               for s in r["controlli"]["scartate"])


def test_confusione_che_passa_la_citazione_resta_da_verificare(pdf, monkeypatch):
    m = DueLetture()

    def confonde(s, u, t, temp):
        out = json.loads(m(s, u, t, temp)) if "FASCICOLO:" in u else None
        if out is None:
            return m(s, u, t, temp)
        if "[[B1]]" in u and u.startswith(cc.SECONDA_LETTURA):
            out["concetti"] = [c for c in out["concetti"] if c["id"] != "non_contestabilita"] + [
                K("non_contestabilita", "1 anno", "La carenza non opera nei casi di decesso da infortunio", "B2", 1, "anni")]
        return json.dumps(out)
    monkeypatch.setattr(cp, "scrivi", confonde)
    r = cc.confronta([_doc(pdf[0], "A", "a1")], [_doc(pdf[1], "B", "b1")])
    # il numero della seconda lettura non e' nella citazione: scartato -> la seconda lettura non ha
    # il concetto -> trovato da una sola lettura -> da verificare, nessun verdetto
    nc = _riga(r, "non_contestabilita")
    assert nc["giudizio"] == "non_confrontata" and "una sola lettura" in nc["perche"]


def test_non_trovato_non_e_un_vantaggio(r):
    p = _riga(r, "esclusione_pandemie")
    assert p["giudizio"] == "non_confrontata" and p["perche"].startswith("Non l'ho trovato in A")
    assert p.get("vantaggio") == ""
    assert _riga(r, "revisione_premio")["perche"].startswith("Non l'ho trovato in A")
    assert any(x["voce"] == p["voce"] for x in r["evidenze"]["verificare"])


def test_zero_scritto_a_parole(r):
    assert r["a"]["concetti"]["frazionamento"] == {"stato": "certo", "valore": 0.0}
    assert r["b"]["concetti"]["trattenuta_recesso"] == {"stato": "certo", "valore": 0.0}
    assert r["b"]["concetti"]["guerra_tolleranza"] == {"stato": "certo", "valore": 0.0}


def test_quadro_dai_concetti(r):
    carte = {c["voce"]: c for c in r["quadro"]["carte"]}
    assert (carte["Carenza"]["a"], carte["Carenza"]["b"], carte["Carenza"]["meglio"]) == ("6 mesi", "12 mesi", "a")
    assert (carte["Caricamenti sul premio"]["a"], carte["Caricamenti sul premio"]["b"]) == ("22%", "31,5%")
    p = carte["Prezzo per 1.000 € di capitale medio"]
    assert (p["a"], p["b"], p["meglio"]) == ("1,74 €", "1,56 €", "b")
    e = carte["Esclusioni"]
    # 24ott2026: la carta nomina le voci, non solo il conteggio
    assert e["a"] == "sport aerei (1 su 5)" and e["b"].endswith("(2 su 5)") and e["meglio"] == "nd"   # motori da verificare
    assert "da verificare" in e["nota"] and "competizioni a motore" in e["nota"]


def test_trappole_controllate(r):
    assert [t["testo"] for t in r["trappole"]] == ["B costa 156 EUR ma su capitale decrescente."]
    assert r["sintesi"].startswith("A protegge")
    assert any(s.get("dove") == "trappole" and "250" in s["motivo"] for s in r["controlli"]["scartate"])


def test_risultato_completo_per_pagina_ed_excel(r):
    from openpyxl import load_workbook
    import io
    assert r["modo"] == "concetti" and r["profilo"]["nome"] == "vita_tcm"
    assert r["evidenze"]["quante_a"] >= 5 and r["evidenze"]["quante_b"] >= 3
    wb = load_workbook(io.BytesIO(cp.excel(r)))
    assert wb.sheetnames[0] == "Quadro"


def test_il_lavoro_usa_il_catalogo(pdf, modello, monkeypatch, tmp_path):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    import documenti_aziendali as docs
    import time
    monkeypatch.setenv("DOCUMENT_UPLOAD_ROOT", str(tmp_path / "archivio"))
    monkeypatch.setenv("APPLAUDI_TENANT_CLIENT_MAP", json.dumps({"agenzia": "agenzia"}))

    def verifica(t, k):
        if k != "k-agenzia":
            raise HTTPException(401, "no")
        return t
    monkeypatch.setattr(docs, "tenant_verifier", verifica)
    app = FastAPI()
    app.include_router(docs.router)
    app.include_router(cp.router)
    c = TestClient(app)
    h = {"X-Applaudi-Tenant-Key": "k-agenzia"}
    ids = []
    for path in pdf:
        with open(path, "rb") as f:
            ids.append(c.post("/api/documents/agenzia/upload", files={"file": (path.split("/")[-1], f, "application/pdf")},
                              headers=h).json()["document"]["id"])
    # Confronta lo attiva il gestore: senza un profilo attivo non parte e non consuma
    r0 = c.post("/api/confronta/agenzia", json={"a": [ids[0]], "b": [ids[1]]}, headers=h)
    assert r0.status_code == 403 and "gestore" in r0.json()["detail"]
    assert c.get("/api/confronta/agenzia/profili", headers=h).json()["profili"] == []
    monkeypatch.setattr(cp, "profili_di", lambda t: ["vita_tcm"] if t == "agenzia" else [])
    profili = c.get("/api/confronta/agenzia/profili", headers=h).json()["profili"]
    assert [p["nome"] for p in profili] == ["vita_tcm"] and profili[0]["testi"]["titolo"] == "Confronta polizze"
    vietato = c.post("/api/confronta/agenzia", json={"a": [ids[0]], "b": [ids[1]], "profilo": "piastrelle"}, headers=h)
    assert vietato.status_code == 403          # il cliente non sceglie un mercato non attivo
    lid = c.post("/api/confronta/agenzia", json={"a": [ids[0]], "b": [ids[1]]}, headers=h).json()["lavoro"]
    for _ in range(300):
        s = c.get(f"/api/confronta/agenzia/lavori/{lid}", headers=h).json()
        if s["stato"] != "in_corso":
            break
        time.sleep(0.05)
    assert s["stato"] == "fatto", s
    dati = c.get(f"/api/confronta/agenzia/{s['confronto']}", headers=h).json()
    assert dati["modo"] == "concetti" and any(u.startswith("CONCETTI:") for u, _ in modello.chiamate)


def test_unita_convertite():
    p = {c["id"]: c for c in cc.carica_profilo()["concetti"]}
    assert cc._normalizza(p["suicidio"], {"numero": 2, "unita": "anni"}) == 24
    assert cc._normalizza(p["guerra_tolleranza"], {"numero": 1, "unita": "mesi"}) == 30
    assert cc._normalizza(p["durata"], {"numero": 180, "unita": "mesi"}) == 15
    assert cc._normalizza(p["suicidio"], {"numero": 2, "unita": "settimane"}) is None   # unita' ignota: niente valore


def test_deroga_senza_condizione_sostituisce_con_condizione_no():
    base = {"forma": "", "normale": 6.0, "deroga": False, "condizionata": False}
    a_condizione = {"forma": "", "normale": 3.0, "deroga": True, "condizionata": True}
    sostituisce = {"forma": "", "normale": 3.0, "deroga": True, "condizionata": False}
    assert cc._valori_base([base, a_condizione]) == {("", 6.0)}
    assert cc._valori_base([base, sostituisce]) == {("", 3.0)}


def test_piu_forme_non_si_confrontano():
    c = cc.carica_profilo()["concetti"][0]
    f1 = {"forma": "tar. 242", "normale": 300.0, "deroga": False, "condizionata": False}
    f2 = {"forma": "tar. 244", "normale": 250.0, "deroga": False, "condizionata": False}
    st = cc.valuta_concetto(c, [f1, f2], [f1, f2])
    assert st["stato"] == "piu_forme"
    g, perche = cc.confronta_concetto(c, {"stato": "certo", "valore": 348.0}, st)
    assert g == "non_confrontata" and "più forme" in perche


def test_premio_non_giudicato_se_capitale_medio_diverso():
    prof = cc.carica_profilo()
    c = next(x for x in prof["concetti"] if x["id"] == "premio_annuo")
    perche = "A 348 EUR, B 156 EUR: per l'assicurato conta il valore più basso."
    ma = {"capitale_medio": {"calcolabile": True, "valore": 200000.0}}
    mb = {"capitale_medio": {"calcolabile": True, "valore": 100000.0}}
    g, p = cc._vincolo(prof, c, "meglio_b", perche, ma, mb)
    assert g == "diverso" and "1.000" in p and p.startswith("A 348 EUR, B 156 EUR")
    assert cc._vincolo(prof, c, "meglio_b", perche, ma, dict(ma))[0] == "meglio_b"
    assert cc._vincolo(prof, c, "meglio_a", perche, ma, {"capitale_medio": {"calcolabile": False}})[0] == "diverso"
    altro = next(x for x in prof["concetti"] if x["id"] == "carenza_malattia")
    assert cc._vincolo(prof, altro, "meglio_a", perche, ma, mb)[0] == "meglio_a"


def test_premio_versilia_nordest_non_e_meglio_b(r):
    riga = next(x for x in r["righe"] if x["concetto"] == "premio_annuo")
    assert riga["giudizio"] == "diverso" and riga.get("vantaggio") in (None, "")


def test_quadro_e_prompt_dal_profilo(r, modello):
    voci = [c["voce"] for c in r["quadro"]["carte"]]
    assert voci == ["Prezzo per 1.000 € di capitale medio", "Capitale medio assicurato", "Carenza", "Esclusioni",
                    "Garanzie comprese nel premio", "Caricamenti sul premio"]
    pu = next(c for c in r["quadro"]["carte"] if c["voce"].startswith("Prezzo"))
    assert pu["a"] == "1,74 €" and pu["b"] == "1,56 €" and pu["meglio"] == "b"
    assert r["profilo"]["testi"]["trappole"] == "Trappole per l'assicurato"
    letture = [s for s in modello.sistemi if "OGNI concetto" in s]
    assert letture and all(s.startswith("Sei un perito assicurativo") and "compreso X" in s for s in letture)
    assert any("una trappola per l'assicurato" in s for s in modello.sistemi)
    assert not any("{" + campo + "}" in s for s in modello.sistemi
                   for campo in ("lettore", "documento", "per_chi", "utente", "regole"))


def test_profilo_sbagliato_si_ferma_al_caricamento(tmp_path, monkeypatch):
    p = json.load(open(cc.os.path.join(cc.CARTELLA, "profilo_vita_tcm.json"), encoding="utf-8"))
    p["nome"] = "rotto"
    p["quadro"].append({"tipo": "concetto", "concetto": "non_esiste"})
    (tmp_path / "profilo_rotto.json").write_text(json.dumps(p), encoding="utf-8")
    monkeypatch.setattr(cc, "CARTELLA", str(tmp_path))
    monkeypatch.setattr(cc, "_PROFILI", {})
    with pytest.raises(ValueError):
        cc.carica_profilo("rotto")
    assert cc.profili_disponibili() == []          # un profilo rotto non si offre al gestore


# ------------------------------------------------------------------ un mercato diverso, stesso motore
PIASTRELLE = {
    "nome": "piastrelle", "titolo": "Preventivi di piastrelle", "versione": 1, "stato": "prova",
    "lettore": "un posatore esperto", "documento": "preventivo di fornitura di piastrelle",
    "documenti": "preventivi", "per_chi": "il cliente", "utente": "il rivenditore",
    "testi": {"titolo": "Confronta preventivi", "lato": "preventivo", "trappole": "Trappole per il cliente"},
    "misure": {},
    "prezzo_unitario": {"etichetta": "Prezzo al m²", "prezzo": "prezzo_totale", "quantita": "superficie",
                        "per": 1, "unita": "EUR/m²"},
    "quadro": [{"tipo": "prezzo_unitario"},
               {"tipo": "concetto", "concetto": "spessore", "etichetta": "Spessore"},
               {"tipo": "conteggio", "etichetta": "Servizi compresi", "chiave": "servizio", "conta": "si",
                "meglio": "alto", "elenca": True}],
    "concetti": [
        {"id": "prezzo_totale", "etichetta": "Totale fornitura", "tipo": "importo", "unita": "EUR", "meglio": "basso",
         "peso": 3, "chiave": "prezzo", "solo_a_pari": "superficie", "domanda": "Totale del preventivo?", "non_confondere": []},
        {"id": "superficie", "etichetta": "Superficie", "tipo": "misura", "unita": "m2", "meglio": None, "peso": 3,
         "chiave": "quantita", "domanda": "Quanti m2?", "non_confondere": []},
        {"id": "spessore", "etichetta": "Spessore", "tipo": "misura", "unita": "mm", "meglio": "alto", "peso": 2,
         "chiave": "tecnica", "domanda": "Spessore della piastrella?", "non_confondere": []},
        {"id": "consegna", "etichetta": "Consegna entro", "tipo": "durata", "unita": "giorni", "meglio": "basso",
         "peso": 1, "chiave": "tempi", "domanda": "Entro quanti giorni?", "non_confondere": []},
        {"id": "posa", "etichetta": "Posa compresa", "tipo": "si_no", "unita": None, "meglio": "si", "peso": 2,
         "chiave": "servizio", "domanda": "La posa e' compresa?", "non_confondere": []},
        {"id": "trasporto", "etichetta": "Trasporto compreso", "tipo": "si_no", "unita": None, "meglio": "si",
         "peso": 1, "chiave": "servizio", "domanda": "Il trasporto e' compreso?", "non_confondere": []}]}

TESTI_PIASTRELLE = {
    "A": ["Preventivo Ceramiche Rossi", "Totale fornitura: 4.200,00 euro", "Superficie: 120 m2",
          "Spessore: 10 mm", "Consegna entro 15 giorni", "Posa compresa nel prezzo", "Trasporto escluso"],
    "B": ["Preventivo Gres Bianchi", "Totale fornitura: 3.900,00 euro", "Superficie: 100 m2",
          "Spessore: 0,9 cm", "Consegna entro 30 giorni", "Posa non compresa", "Trasporto compreso"]}
LETTURE_PIASTRELLE = {
    "A": [("prezzo_totale", "4.200,00 euro", 4200, "EUR", None, "Totale fornitura: 4.200,00 euro"),
          ("superficie", "120 m2", 120, "m2", None, "Superficie: 120 m2"),
          ("spessore", "10 mm", 10, "mm", None, "Spessore: 10 mm"),
          ("consegna", "15 giorni", 15, "giorni", None, "Consegna entro 15 giorni"),
          ("posa", "compresa", None, None, "si", "Posa compresa nel prezzo"),
          ("trasporto", "escluso", None, None, "no", "Trasporto escluso")],
    "B": [("prezzo_totale", "3.900,00 euro", 3900, "EUR", None, "Totale fornitura: 3.900,00 euro"),
          ("superficie", "100 m2", 100, "m2", None, "Superficie: 100 m2"),
          ("spessore", "0,9 cm", 0.9, "cm", None, "Spessore: 0,9 cm"),
          ("consegna", "30 giorni", 30, "giorni", None, "Consegna entro 30 giorni"),
          ("posa", "non compresa", None, None, "no", "Posa non compresa"),
          ("trasporto", "compreso", None, None, "si", "Trasporto compreso")]}


class LettorePiastrelle:
    def __init__(self):
        self.sistemi = []

    def __call__(self, sistema, utente, max_tokens=0, temperatura=0.0):
        import re
        self.sistemi.append(sistema)
        if "FASCICOLO:" not in utente:
            return json.dumps({"trappole": [], "azioni": [], "sintesi": ""})
        out = []
        for pid in re.findall(r"\[\[([AB]\d+)\]\]", utente):
            for cid, valore, numero, unita, si_no, cit in LETTURE_PIASTRELLE[pid[0]]:
                out.append({"id": cid, "valore": valore, "numero": numero, "unita": unita, "si_no": si_no,
                            "condizione": "", "forma": "", "citazione": cit, "pagina": pid})
        return json.dumps({"concetti": out})


@pytest.fixture
def piastrelle(tmp_path, monkeypatch):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    (tmp_path / "profilo_piastrelle.json").write_text(json.dumps(PIASTRELLE), encoding="utf-8")
    monkeypatch.setattr(cc, "CARTELLA", str(tmp_path))
    monkeypatch.setattr(cc, "_PROFILI", {})
    docs = []
    for lato in ("A", "B"):
        path = str(tmp_path / f"preventivo_{lato}.pdf")
        pag = canvas.Canvas(path, pagesize=A4)
        for n, riga in enumerate(TESTI_PIASTRELLE[lato]):
            pag.drawString(60, 780 - 22 * n, riga)
        pag.save()
        docs.append(_doc(path, f"Preventivo {lato}", lato.lower() + "1"))
    m = LettorePiastrelle()
    monkeypatch.setattr(cp, "scrivi", m)
    return docs, m


def test_un_altro_mercato_con_lo_stesso_motore(piastrelle):
    docs, modello = piastrelle
    r = cc.confronta([docs[0]], [docs[1]], "", "", None, "piastrelle")
    # nessuna parola del mondo assicurativo nei prompt
    assert all("assicura" not in s and "polizz" not in s for s in modello.sistemi)
    assert any("un posatore esperto" in s and "preventivo di fornitura di piastrelle" in s for s in modello.sistemi)
    assert r["a"]["nome"] == "Preventivo A" and r["profilo"]["testi"]["titolo"] == "Confronta preventivi"
    g = {x["concetto"]: x for x in r["righe"]}
    # 4.200 per 120 m2 contro 3.900 per 100 m2: il totale piu' basso NON e' il migliore
    assert g["prezzo_totale"]["giudizio"] == "diverso" and "Prezzo al m²" in g["prezzo_totale"]["perche"]
    assert g["spessore"]["giudizio"] == "meglio_a"          # 10 mm contro 0,9 cm = 9 mm
    assert g["consegna"]["giudizio"] == "meglio_a" and g["posa"]["giudizio"] == "meglio_a"
    assert g["trasporto"]["giudizio"] == "meglio_b" and "per il cliente" in g["trasporto"]["perche"]
    carte = {c["voce"]: c for c in r["quadro"]["carte"]}
    assert list(carte) == ["Prezzo al m²", "Spessore", "Servizi compresi"]
    assert carte["Prezzo al m²"]["a"] == "35,00 €/m²" and carte["Prezzo al m²"]["b"] == "39,00 €/m²"
    assert carte["Prezzo al m²"]["meglio"] == "a"
    assert carte["Spessore"]["a"] == "10 mm" and carte["Spessore"]["b"] == "9 mm"
    assert carte["Servizi compresi"]["a"].endswith("(1 su 2)") and carte["Servizi compresi"]["meglio"] == "pari"
    from openpyxl import load_workbook
    import io
    wb = load_workbook(io.BytesIO(cp.excel(r)))
    assert "Prezzo unitario" in wb.sheetnames and "Base premio" not in wb.sheetnames
    assert wb["Confronto"]["A1"].value.startswith("Confronta preventivi")
    assert wb["Trappole e azioni"]["A4"].value == "Trappole per il cliente"


def test_da_verificare_non_conta_come_pari(r):
    q = r["quadro"]
    assert q["da_verificare"] == sum(1 for x in r["righe"] if x["giudizio"] == "non_confrontata") > 0
    assert q["vince_a"] + q["vince_b"] + q["pari"] + q["da_verificare"] == len(r["righe"])


# ------------------------------------------------------------------ casi veri del 22ott2026 (Versilia / Sara)
def test_valore_base_con_la_variante_nella_condizione_e_certo():
    """Lettura vera di A: un solo oggetto «6 mesi (se ridotta a 3 mesi con referto...)»,
    numero 6, la variante nella condizione. Prima veniva scartato: carenza «non leggibile»."""
    c = next(x for x in cc.carica_profilo()["concetti"] if x["id"] == "carenza_malattia")
    unico = {"forma": "", "normale": 6.0, "deroga": False, "condizionata": False, "con_condizione": True}
    assert cc.valuta_concetto(c, [unico], [dict(unico)]) == {"stato": "certo", "valore": 6.0}
    variante = {"forma": "", "normale": 3.0, "deroga": False, "condizionata": False, "con_condizione": True}
    base = {"forma": "", "normale": 6.0, "deroga": False, "condizionata": False, "con_condizione": False}
    assert cc.valuta_concetto(c, [base, variante], [base, variante])["valore"] == 6.0


def test_numeri_scritti_a_parole_e_in_altra_unita():
    cit = "qualora il decesso dell'Assicurato avvenga entro i primi sei mesi dall'entrata in vigore"
    assert cc._numeri_senza_prova("6 mesi", 6, cit, "") == set()
    assert cc._numeri_senza_prova("2 anni", 24, "suicidio entro due anni dalla decorrenza", "") == set()
    assert cc._numeri_senza_prova("2 anni (24 mesi)", 24, "entro 2 anni dalla decorrenza", "") == {"24"}
    assert cc._numeri_senza_prova("6 mesi (se ridotta a 3 mesi con referto)", 6,
                                  "trascorsi 6 mesi dalla decorrenza", "ridotta a 3 mesi con referto") == set()
    # un numero che il testo non dice resta fuori
    assert cc._numeri_senza_prova("3 mesi", 3, "Decorsi 12 mesi dalla data di effetto", "") == {"3"}
    assert cc._numeri_senza_prova("12 mesi", 18, "Decorsi 12 mesi dalla data di effetto", "") == {"18"}


def test_citazione_con_parole_incollate():
    incollato = "Incasodimortedell'Assicuratoentrolascadenzadelcontratto,l'Impresaliquida il capitale"
    assert cp.trova_citazione("In caso di morte dell'Assicurato entro la scadenza del contratto", incollato)
    assert cp.trova_citazione("Incasodimortedell'Assicuratoentrolascad", incollato)
    assert not cp.trova_citazione("In caso di invalidita' dell'Assicurato", incollato)
    assert not cp.trova_citazione("in caso", incollato)          # troppo corto per dimostrare qualcosa


def test_garanzia_accessoria_e_esempio_non_si_confrontano():
    prof = cc.carica_profilo()
    c = next(x for x in prof["concetti"] if x["id"] == "esclusione_motori")
    infortuni = {"forma": "Assicurazione Complementare Infortuni", "normale": "si", "deroga": False,
                 "condizionata": False, "con_condizione": True,
                 "accessoria": cc._accessoria(prof, "Assicurazione Complementare Infortuni")}
    st = cc.valuta_concetto(c, [infortuni], [infortuni])
    assert st["stato"] == "da_verificare" and "accessoria" in st["motivo"]
    assert not cc._accessoria(prof, "tar. 242")
    # «300.000 euro (esempio)» non entra nel capitale
    fasc = {"pagine": [{"id": "B1", "documento": "sara", "label": "pagina 15", "deroga": False,
                        "testo": "al fine di garantirsi un capitale assicurato di Euro 300.000, e' pari a Euro 450"}]}
    scartate = []
    out = cc._verifica("B", fasc, prof, [{"id": "capitale_assicurato", "valore": "300.000 euro (esempio)",
                                          "numero": 300000, "unita": "EUR", "citazione":
                                          "al fine di garantirsi un capitale assicurato di Euro 300.000",
                                          "pagina": "B1"}], scartate, 1)
    assert out == {} and scartate[0]["motivo"].startswith("valore di un esempio")


# ------------------------------------------------------------------ lettura con Gemini (finta: niente rete)
def test_gemini_confronto_diretto_forma_accessoria_ed_esempio(pdf, monkeypatch, tmp_path):
    """23ott2026: i due lati letti insieme (come una chat con i due PDF). Il modello sceglie la
    forma di B confrontabile con A; il codice verifica ogni citazione, tiene fuori la garanzia
    solo accessoria e gli esempi, e filtra i numeri inventati nelle trappole."""
    import lettore_gemini
    monkeypatch.setenv("GEMINI_API_KEY", "finta")
    chiamate = []

    def diretto(file_a, file_b, profilo, cliente=""):
        chiamate.append((file_a, file_b))
        return {"forma_a": {"codice": "unica", "ha_piu_forme": False},
                "forma_b": {"codice": "T1", "descrizione": "capitale e premio annuo costanti", "ha_piu_forme": True,
                            "perche": "stessa forma di A"},
                "concetti": [
                    {"id": "caricamenti",
                     "A": {"valore": "22,0%", "numero": 22, "unita": "%", "documento": "A1", "pagina": 3,
                           "citazione": "Caricamenti sul premio | Valore: 22,0% del premio lordo"},
                     "B": {"valore": "12%", "numero": 12, "unita": "%", "documento": "B1", "pagina": 2,
                           "citazione": "Decorsi 12 mesi dalla data di effetto"}},
                    {"id": "esclusione_motori",
                     "A": {"valore": "escluse", "si_no": "si", "documento": "A1", "pagina": 2,
                           "citazione": "Partecipazione a competizioni motoristiche"},
                     "B": {"valore": "non esclusa: elenco chiuso", "si_no": "no", "documento": "B1", "pagina": 2,
                           "citazione": "Per i decessi conseguenti a malattia la garanzia opera decorsi 12 mesi"}},
                    {"id": "doppio_capitale", "A": None,
                     "B": {"valore": "facoltativo", "si_no": "si", "solo_accessoria": True,
                           "nome_accessoria": "Garanzia Infortuni Extra", "documento": "B1", "pagina": 2,
                           "citazione": "Per i decessi conseguenti a malattia"}},
                    {"id": "durata", "A": None,
                     "B": {"valore": "12 mesi", "numero": 12, "unita": "mesi", "esempio": True, "documento": "B1",
                           "pagina": 2, "citazione": "Decorsi 12 mesi dalla data di effetto"}},
                    {"id": "invalidita_da_malattia",
                     "A": {"valore": "si", "si_no": "si", "solo_accessoria": True, "compresa_nel_premio": True,
                           "nome_accessoria": "Garanzia complementare invalidita'", "documento": "A1", "pagina": 2,
                           "citazione": "Partecipazione a competizioni motoristiche"}, "B": None},
                    {"id": "carenza_aids", "A": None,
                     "B": {"valore": "12 mesi (esteso a 99 mesi senza esami)", "numero": 12, "unita": "mesi",
                           "documento": "B1", "pagina": 2, "citazione": "Decorsi 12 mesi dalla data di effetto"}},
                    {"id": "frazionamento", "A": None,
                     "B": {"valore": "7%", "numero": 7, "unita": "%", "documento": "B1", "pagina": 2,
                           "citazione": "una frase che nel documento non c'e' proprio"}}],
                "trappole": [{"testo": "B ha caricamenti del 12% contro il 22,0% di A: va verificata la forma."},
                             {"testo": "B nasconde un costo del 99%."}],
                "azioni": [{"testo": "Chiedere quale forma di B e' stata proposta."}],
                "altre_differenze": [
                    {"voce": "Attesa iniziale", "perche": "conta per chi si ammala presto",
                     "A": None, "B": {"valore": "12 mesi", "documento": "B1", "pagina": 2,
                                      "citazione": "Decorsi 12 mesi dalla data di effetto"}},
                    {"voce": "Inventata", "A": {"valore": "77 euro", "documento": "A1", "pagina": 1,
                                                "citazione": "una frase che non esiste da nessuna parte"}, "B": None}],
                "sintesi": "", "_modello": "gemini-prova"}
    monkeypatch.setattr(lettore_gemini, "confronto_diretto", diretto)
    monkeypatch.setattr(cp, "scrivi", lambda s, u, t, temp: json.dumps({"trappole": [], "azioni": [], "sintesi": ""}))
    doc = lambda p, t, i: {**_doc(p, t, i), "source_path": p}       # noqa: E731
    r = cc.confronta([doc(pdf[0], "Versilia", "a1")], [doc(pdf[1], "Nordest", "b1")], "A", "B")
    assert len(chiamate) == 1 and r["lettura"]["modo"] == "gemini"
    assert r["lettura"]["forma_scelta"]["lato"] == "B" and r["lettura"]["forma_scelta"]["codice"] == "T1"
    g = {x["concetto"]: x for x in r["righe"]}
    assert g["caricamenti"]["giudizio"] == "meglio_b"
    assert r["b"]["concetti"]["esclusione_motori"] == {"stato": "certo", "valore": "no"}
    assert g["esclusione_motori"]["giudizio"] == "meglio_b"
    # garanzia solo accessoria: si mostra, non vale come «compresa»
    assert r["b"]["concetti"]["doppio_capitale"]["stato"] == "da_verificare"
    motivi = " ".join(s.get("motivo", "") for s in r["controlli"]["scartate"])
    assert "esempio" in motivi and "citazione non trovata" in motivi
    assert "frazionamento" not in {v["concetto"] for v in r["b"]["voci"]}
    trappole = " ".join(t["testo"] for t in r["trappole"])
    assert "12%" in trappole and "99%" not in trappole
    assert r["azioni"] and r["azioni"][0]["testo"].startswith("Chiedere")
    # garanzia «complementare» ma compresa nel premio: conta come compresa (Versilia, 24ott2026)
    assert r["a"]["concetti"]["invalidita_da_malattia"] == {"stato": "certo", "valore": "si"}
    # la variante fra parentesi non fa scartare il valore provato (AIDS di Sara, 24ott2026)
    assert r["b"]["concetti"]["carenza_aids"] == {"stato": "certo", "valore": 12.0}
    # altre differenze: entra solo quella verificata sulla pagina
    assert [d["voce"] for d in r["altre_differenze"]] == ["Attesa iniziale"]
    assert r["altre_differenze"][0]["b"]["label"] and r["altre_differenze"][0]["a"] is None
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(cp.excel(r)))
    testo = " ".join(str(c.value) for c in wb["Trappole e azioni"]["A"] if c.value)
    assert "Altre differenze trovate" in testo and "Attesa iniziale" in testo


def test_gemini_non_risponde_si_torna_alla_lettura_di_prima(pdf, modello, monkeypatch):
    import lettore_gemini
    monkeypatch.setenv("GEMINI_API_KEY", "finta")

    def guasto(*a, **k):
        raise lettore_gemini.ErroreGemini(404)
    monkeypatch.setattr(lettore_gemini, "confronto_diretto", guasto)
    doc = lambda p, t, i: {**_doc(p, t, i), "source_path": p}       # noqa: E731
    r = cc.confronta([doc(pdf[0], "Versilia", "a1")], [doc(pdf[1], "Nordest", "b1")], "A", "B")
    assert r["lettura"]["modo"] == "testo" and r["lettura"]["avvisi"]
    assert "modello Gemini non trovato" in r["lettura"]["avvisi"][0]
    assert _riga(r, "carenza_malattia")["giudizio"] == "meglio_a"


def test_scheda_letta_una_volta_sola(tmp_path, monkeypatch):
    import lettore_gemini
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 finto")
    volte = []
    monkeypatch.setattr(lettore_gemini, "leggi_scheda", lambda p, prof, c="": volte.append(1) or {"concetti": []})
    prof = cc.carica_profilo()
    lettore_gemini.scheda_salvata(str(pdf), prof)
    lettore_gemini.scheda_salvata(str(pdf), prof)
    assert volte == [1]


def test_misura_sulla_chiave(tmp_path, monkeypatch):
    # la chiave di correzione e' materiale di collaudo: non sta nel prodotto, si crea qui
    import shutil
    shutil.copy(cc.os.path.join(cc.CARTELLA, "profilo_vita_tcm.json"), tmp_path / "profilo_vita_tcm.json")
    righe = [{"concetto": c, "atteso": a} for c, a in
             [("frazionamento", "meglio_a"), ("caricamenti", "meglio_b"), ("non_contestabilita", "non_confrontabile")]]
    righe += [{"concetto": f"altro_{n}", "atteso": "diverso"} for n in range(27)]
    (tmp_path / "chiave_versilia_sara.json").write_text(json.dumps({"profilo": "vita_tcm", "righe": righe}))
    monkeypatch.setattr(cc, "CARTELLA", str(tmp_path))
    monkeypatch.setattr(cc, "_PROFILI", {})
    conf = {"righe": [{"concetto": "frazionamento", "giudizio": "meglio_a"},
                      {"concetto": "caricamenti", "giudizio": "meglio_a"},
                      {"concetto": "non_contestabilita", "giudizio": "non_confrontata"}]}
    m = cc.misura_su_chiave(conf, "versilia_sara")
    esiti = {r["concetto"]: r["esito"] for r in m["righe"]}
    assert esiti["frazionamento"] == "giusto" and esiti["caricamenti"] == "sbagliato"
    assert esiti["non_contestabilita"] == "giusto" and m["totale"] == 30
    assert cc.chiavi_disponibili("vita_tcm") == ["versilia_sara"]


def test_gemini_chiave_mai_nell_indirizzo_e_motivo_leggibile(monkeypatch, tmp_path):
    """23ott2026: un 402 di Google ha scritto la chiave nei log perche' stava nell'indirizzo.
    Ora la chiave viaggia nell'intestazione e l'errore dice il motivo in italiano."""
    import lettore_gemini
    chiave = "AQ.chiave-segreta-di-prova"
    monkeypatch.setenv("GEMINI_API_KEY", chiave)
    pdf = tmp_path / "grande.pdf"
    pdf.write_bytes(b"%PDF-1.4 " + b"x" * (lettore_gemini.MAX_INLINE_BYTE + 10))
    chiamate = []

    class Risposta:
        status_code = 402
        headers = {}

        def json(self):
            return {"error": {"message": "Payment required"}}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **kw):
            chiamate.append((url, kw))
            return Risposta()

        get = post

    monkeypatch.setattr(lettore_gemini, "_client", lambda: Client())
    with pytest.raises(lettore_gemini.ErroreGemini) as errore:
        lettore_gemini.leggi_scheda(str(pdf), cc.carica_profilo("vita_tcm"))
    assert errore.value.codice == 402 and "credito" in errore.value.motivo
    assert chiave not in str(errore.value)
    for url, kw in chiamate:
        assert chiave not in url and "params" not in kw
        assert kw["headers"]["x-goog-api-key"] == chiave
