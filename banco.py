# -*- coding: utf-8 -*-
"""IL BANCO DI PROVA — far girare il motore senza il web e senza pagare il modello.

    python3 banco.py              esegue tutte le misure e le stampa
    python3 banco.py --veloce     salta quelle lente

PERCHE' ESISTE
==============
Il 7 ottobre 2026 il credito DeepSeek si e' esaurito nella notte. Da quel
momento ogni risposta e' stata "Si e' verificato un errore durante la ricerca
documentale", il collaudo l'ha trattata come una risposta qualunque e ha
prodotto 64 fallimenti identici, novanta volte di fila. La causa vera stava in
una riga di log su Render.

Quel giorno si e' scoperto che il motore si puo' far girare FUORI dal web:
l'indice, le schede, il punteggio e i controlli non hanno bisogno ne' di
FastAPI ne' di DeepSeek. Serve solo mettere al loro posto delle controfigure.
Da li' in poi ogni verifica e' costata secondi invece di una batteria pagata.

Questo file rende quella scoperta permanente. Senza, vive solo nella sessione
di chi l'ha fatta e il giorno dopo non c'e' piu'.

COSA MISURA, E PERCHE' QUESTE MISURE
====================================
Non misura "quante risposte sono giuste": quello costa il modello. Misura la
meta' deterministica, che e' gratis e dove stanno quasi tutti i difetti:

  1. CODICI ATTESI NEL CONTESTO   il motore manda al modello la riga giusta?
     Valore al 7ott2026: 116 / 126. I dieci mancanti sono due casi soli
     (B16, C13) e sono un difetto noto, non un errore di misura.

  2. PRIME POSIZIONI              la scheda giusta e' in cima alla lista?
     Valore al 7ott2026: 11 / 20. Era 9 / 20 prima delle correzioni del giorno.

  3. DIMENSIONE DEL CONTESTO      quanto si paga a ogni domanda
     Valore al 7ott2026: ~102.000 caratteri. Era 351.589 prima del tetto
     sulle schede candidate.

  4. LETTURA DELLE COPPIE DI MISURE   "55,2 x 36,8" e' larghezza x altezza?

  5. SCHEDE CON PREZZI PERSI      le righe del catalogo portano piu' prezzi
     della scheda. Valore al 7ott2026: 66 schede su 7 pagine (tavoli e panche).

REGOLA D'USO
============
Una modifica al punteggio, al recupero o alle schede si tiene SOLO se queste
misure non peggiorano. Non e' una formalita': il 7ott2026 due correzioni su
cinque sono state scartate proprio qui, dopo essere sembrate giuste.
"""
import os
import sys
import types

# ============================================================
# LE CONTROFIGURE — si installano prima di importare il motore
# ============================================================
# Non imitano FastAPI: gli assomigliano quel tanto che basta perche' app.py si
# importi. Le rotte vengono registrate e mai chiamate; il client del modello
# esiste ma qualunque chiamata esplode, cosi' una misura che per sbaglio
# finisse a pagare il fornitore si ferma subito con un errore chiaro.


def _installa_controfigure() -> None:
    if "fastapi" in sys.modules:
        return

    class HTTPException(Exception):
        def __init__(self, status_code=500, detail=""):
            super().__init__(detail)
            self.status_code, self.detail = status_code, detail

    class _Rotte:
        def __init__(self):
            self.rotte = {}

        def _reg(self, metodo, percorso, **kw):
            def deco(fn):
                self.rotte[(metodo, percorso)] = fn
                return fn
            return deco

        def get(self, p, **kw): return self._reg("GET", p, **kw)
        def post(self, p, **kw): return self._reg("POST", p, **kw)
        def put(self, p, **kw): return self._reg("PUT", p, **kw)
        def delete(self, p, **kw): return self._reg("DELETE", p, **kw)
        def api_route(self, p, **kw): return self._reg("ANY", p, **kw)

    class FastAPI(_Rotte):
        def __init__(self, *a, **kw): super().__init__()
        def add_middleware(self, *a, **kw): pass
        def mount(self, *a, **kw): pass
        def on_event(self, *a, **kw):
            def deco(fn): return fn
            return deco

    fastapi = types.ModuleType("fastapi")
    fastapi.FastAPI, fastapi.HTTPException = FastAPI, HTTPException

    middleware = types.ModuleType("fastapi.middleware")
    cors = types.ModuleType("fastapi.middleware.cors")
    cors.CORSMiddleware = type("CORSMiddleware", (), {"__init__": lambda s, *a, **k: None})
    middleware.cors = cors

    class _Risposta:
        def __init__(self, content=None, *a, **kw):
            self.content = self.body = content

    responses = types.ModuleType("fastapi.responses")
    for nome in ("FileResponse", "HTMLResponse", "PlainTextResponse", "JSONResponse"):
        setattr(responses, nome, type(nome, (_Risposta,), {}))
    responses.RedirectResponse = type(
        "RedirectResponse", (_Risposta,),
        {"__init__": lambda s, url="", *a, **k: (_Risposta.__init__(s, url),
                                                 setattr(s, "url", url))[0]})

    staticfiles = types.ModuleType("fastapi.staticfiles")
    staticfiles.StaticFiles = type("StaticFiles", (), {"__init__": lambda s, *a, **k: None})

    class BaseModel:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)
            for nome, valore in type(self).__dict__.items():
                if not nome.startswith("_") and not callable(valore) and not hasattr(self, nome):
                    setattr(self, nome, valore)
        def dict(self): return self.__dict__.copy()
        def model_dump(self): return self.__dict__.copy()

    pydantic = types.ModuleType("pydantic")
    pydantic.BaseModel = BaseModel
    pydantic.Field = lambda default=None, **kw: default

    class _Completions:
        def create(self, **kw):
            raise RuntimeError("CHIAMATA AL MODELLO NON PREVISTA SUL BANCO: "
                               "una misura non deve pagare il fornitore")

    openai = types.ModuleType("openai")
    openai.OpenAI = type("OpenAI", (), {
        "__init__": lambda s, *a, **k: setattr(
            s, "chat", type("C", (), {"completions": _Completions()})())})

    for nome, modulo in (("fastapi", fastapi),
                         ("fastapi.middleware", middleware),
                         ("fastapi.middleware.cors", cors),
                         ("fastapi.responses", responses),
                         ("fastapi.staticfiles", staticfiles),
                         ("pydantic", pydantic),
                         ("openai", openai)):
        sys.modules[nome] = modulo
    fastapi.middleware, fastapi.responses, fastapi.staticfiles = middleware, responses, staticfiles


def carica_motore(cliente: str = "lago"):
    """Importa app.py con le controfigure al posto delle librerie web."""
    _installa_controfigure()
    os.environ.setdefault("DEEPSEEK_API_KEY", "banco")
    os.environ.setdefault("CLIENTE", cliente)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import app                                              # noqa: E402
    return app


# ============================================================
# LE MISURE
# ============================================================

import json   # noqa: E402
import re     # noqa: E402

CODICE_ATTESO = re.compile(r'\b(?=[A-Z0-9]{5,9}\b)(?:\d{6,7}|[A-Z]{2,3}\d{3,4}[A-Z]?)\b')
RIFERIMENTO = {
    "codici attesi nel contesto": "116 / 126",
    "prime posizioni": "11 / 20",
    # media sui primi 8 casi (domande di prezzo, pochi candidati). Una domanda
    # larga tipo "la madia 36e8 laccata" arriva a ~102.000: era 351.589 prima
    # del tetto sulle schede candidate del 7ott2026.
    "caratteri di contesto": "~70.000 medi",
    "schede con prezzi persi": "66",
}


def casi_del_cliente(app):
    percorso = os.path.join(os.path.dirname(app.PRODUCT_CARDS_PATH), "casi_collaudo.json")
    if not os.path.isfile(percorso):
        return []
    with open(percorso, encoding="utf-8") as f:
        return json.load(f)


def attesi_del_caso(app, caso):
    attesi = set()
    for regola in caso.get("deve", []):
        attesi |= set(CODICE_ATTESO.findall(regola))
    return attesi & set(app.CODE_ROWS)


def misura_recupero(app, casi):
    """Quanti codici attesi arrivano al modello, e in che posizione."""
    attesi_tot = dentro = primi = contati = 0
    buchi = []
    for caso in casi:
        attesi = attesi_del_caso(app, caso)
        if not attesi:
            continue
        ordine = [c["codice"] for c in app.verified_dimension_candidates(caso["turni"][0])]
        posizioni = [ordine.index(c) + 1 for c in attesi if c in ordine]
        attesi_tot += len(attesi)
        dentro += len(posizioni)
        contati += 1
        if posizioni and min(posizioni) == 1:
            primi += 1
        if len(posizioni) < len(attesi):
            buchi.append((caso["nome"][:38], len(attesi) - len(posizioni)))
    return {"attesi": attesi_tot, "dentro": dentro, "primi": primi,
            "casi": contati, "buchi": buchi}


def misura_contesto(app, casi, quanti=8):
    misure = []
    for caso in casi[:quanti]:
        q = caso["turni"][0]
        misure.append(len(app.certified_candidate_evidence(q)) + 1
                      + len(app.retrieve_local_evidence(q, max_pages=10, planned_terms="")))
    return {"minimo": min(misure), "massimo": max(misure),
            "medio": sum(misure) // max(1, len(misure))}


def misura_coppie(app, coppie=((55.2, 36.8), (184.0, 40.6), (110.4, 56.0),
                               (220.8, 78.7), (147.2, 75.3))):
    esiti = []
    for a, b in coppie:
        letta = app.exact_dimension_requests(f"prodotto da {a} x {b}".replace(".", ","))
        assi = [asse for asse, _ in letta]
        esiti.append((a, b, assi[1] if len(assi) > 1 else "-"))
    return esiti


def misura_prezzi_persi(app):
    """Pagine in cui la riga del catalogo porta due prezzi e la scheda uno solo.

    E' la firma del difetto misurato sui tavoli: la seconda colonna del colore
    delle gambe non entra nella scheda. Si contano solo le pagine con questa
    forma pulita e almeno tre righe cosi', per non scambiare per difetto un
    impaginato a due prodotti affiancati.
    """
    pagine = []
    for p in app.DOCUMENT_PAGES:
        schede = [c for g in app.PRODUCT_CARDS.values() for c in g if c["pagina"] == p["page"]]
        if not schede or max(len(c.get("prezzi") or {}) for c in schede) != 1:
            continue
        righe = [len(re.findall(r"\b\d{1,2}\.\d{3}\b", r))
                 for r in (p.get("text") or "").splitlines()
                 if [c for c in app.extract_document_codes(r) if c in app.CODE_ROWS]]
        if righe and max(righe) == 2 and sum(1 for x in righe if x == 2) >= 3:
            pagine.append((p["page"], len(schede)))
    return pagine


def esegui(veloce: bool = False) -> None:
    app = carica_motore()
    casi = casi_del_cliente(app)
    print("\n" + "=" * 62)
    print(f"BANCO — {app.DOCUMENT_CONTEXT}")
    print(f"{len(app.DOCUMENT_PAGES)} pagine · {len(app.CODE_ROWS)} codici · "
          f"{sum(len(g) for g in app.PRODUCT_CARDS.values())} schede · {len(casi)} casi")
    print("=" * 62)

    r = misura_recupero(app, casi)
    print(f"\n1. CODICI ATTESI NEL CONTESTO   {r['dentro']} / {r['attesi']}"
          f"        riferimento {RIFERIMENTO['codici attesi nel contesto']}")
    print(f"2. PRIME POSIZIONI              {r['primi']} / {r['casi']}"
          f"          riferimento {RIFERIMENTO['prime posizioni']}")
    for nome, quanti in r["buchi"]:
        print(f"     buco: {nome:40} {quanti} codici non pescati")

    c = misura_contesto(app, casi)
    print(f"\n3. CONTESTO PER DOMANDA         medio {c['medio']:,} caratteri"
          f"   (min {c['minimo']:,} max {c['massimo']:,})")
    print(f"                                riferimento {RIFERIMENTO['caratteri di contesto']}")

    print("\n4. LETTURA DELLE COPPIE DI MISURE")
    for a, b, asse in misura_coppie(app):
        print(f"     {a} x {b:<7} -> larghezza x {asse}")

    if not veloce:
        pagine = misura_prezzi_persi(app)
        tot = sum(n for _, n in pagine)
        print(f"\n5. SCHEDE CON PREZZI PERSI      {tot} schede su {len(pagine)} pagine"
              f"   riferimento {RIFERIMENTO['schede con prezzi persi']}")
        print(f"     pagine: {[p for p, _ in pagine]}")

    print("\nUna modifica si tiene solo se nessuno di questi numeri peggiora.\n")


if __name__ == "__main__":
    esegui(veloce="--veloce" in sys.argv)
