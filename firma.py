# -*- coding: utf-8 -*-
"""LA FIRMA DI UN DOCUMENTO — misurata, non ipotizzata.

    python3 firma.py documento.pdf

Prima di rispondere a una qualsiasi domanda, un documento deve dire chi e'. Non
"sembra un listino": deve dichiarare, con la prova, COME lega un prezzo al
prodotto a cui appartiene. E' la sola domanda che conta, perche' tutto il resto
- confronti, budget, "il piu' economico" - poggia su quella relazione.

PERCHE' QUESTO FILE ESISTE
==========================
Il 7ott2026, dopo una giornata passata a correggere il motore su un catalogo di
arredo, e' entrato un listino mai visto: Mitsubishi Electric, 100 pagine,
condizionatori. Gli strumenti esistenti hanno detto:

    costruisci_indice.py   100 pagine, numerazione verificata        corretto
    profila_documento.py   "SI, so rispondere sul prezzo di un codice"  FALSO

Falso perche' quel profilatore conta le PRESENZE: 776 importi, 241 codici,
quindi "si puo' fare". Non si era chiesto se quegli importi appartenessero a
qualcosa. Su quel documento i prezzi stanno a pagina 21 e i prodotti a pagina
20, affiancate, e il legame e' la coordinata verticale:

    y=137   MSZ-LN VG2   (pag 20)  |  1.170 €  1.262 €  1.550 €   (pag 21)
    y=183   MSZ-EF VGK   (pag 20)  |  920 €  942 €  1.018 € ...   (pag 21)

Quattordici modelli, quattordici righe di prezzi, stessa banda orizzontale.
L'informazione c'era tutta. Mancava chi la misurasse - e, peggio, mancava chi
dicesse che non era stata misurata. Il motore avrebbe risposto lo stesso,
sbagliando con sicurezza.

COSA NON C'E' QUI DENTRO
========================
Nessuna parola di nessun catalogo. Nessun nome di azienda, di prodotto, di
colonna. La firma si ricava dal documento misurando tre cose che qualsiasi
documento stampato ha: dove stanno le parole, dove stanno i numeri, e se si
allineano.

LE FORME DEL LEGAME
===================
Sono poche. Un listino lega un prezzo al suo prodotto in uno di questi modi, e
si riconoscono contando:

    riga                 il codice e il prezzo stanno sulla stessa riga
                         (LAGO: "147,2 40,6 75,3 0064613 2.511 2.924 ...")
    matrice              le etichette in una colonna a sinistra, i prezzi in
                         griglia sulla stessa pagina
    matrice_affiancata   le etichette su una pagina, i prezzi su quella
                         accanto, legate dalla coordinata verticale
                         (Mitsubishi: pagine 20 e 21)
    non_determinato      nessuna delle precedenti regge sul campione

Un documento che finisce in 'non_determinato' NON e' pronto, e lo si sa prima
che qualcuno gli faccia una domanda - non davanti al cliente.
"""
import collections
import json
import re
import statistics
import sys

# ============================================================
# COSA E' UN NUMERO DI PREZZO E COSA E' UN CODICE
# ============================================================
# Due forme, non due elenchi. Valgono su qualsiasi documento in cui i prezzi
# siano scritti all'europea e i codici siano sigle.

IMPORTO = re.compile(r"^\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?$|^\d{2,4},\d{2}$")
VALUTA = re.compile(r"^[€$£]$|^(?:eur|euro|usd)$", re.I)
# Un codice e' una sigla: lettere e cifre insieme, oppure cifre sole ma lunghe.
# Non si elencano i formati: si descrive la forma e poi si misura quale domina.
CODICE_MISTO = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9][A-Za-z0-9.\-_/+]{3,19}$")
CODICE_NUMERICO = re.compile(r"^\d{5,9}$")
BANDA = 6.0          # due parole sono sulla stessa riga entro questa distanza verticale
MIN_PREZZI_RIGA = 1  # una riga di listino ha almeno un prezzo


def e_codice(testo: str) -> bool:
    t = testo.strip()
    return bool(CODICE_MISTO.fullmatch(t) or CODICE_NUMERICO.fullmatch(t))


def e_prezzo(testo: str) -> bool:
    return bool(IMPORTO.fullmatch(testo.strip()))


def bande_orizzontali(parole, tolleranza: float = BANDA):
    """Raggruppa le parole in righe visive. E' l'unica nozione di 'riga' che
    questo file usa, e non dipende da come il documento e' fatto."""
    righe = []
    for parola in sorted(parole, key=lambda w: (w["top"], w["x0"])):
        if righe and abs(parola["top"] - righe[-1]["y"]) <= tolleranza:
            righe[-1]["parole"].append(parola)
        else:
            righe.append({"y": parola["top"], "parole": [parola]})
    return righe


def dentro_pagina(parola, larghezza: float) -> bool:
    """Scarta cio' che sta fuori dal foglio.

    Su Mitsubishi pagina 21 ci sono parole a x = -561: nomi di modelli lasciati
    fuori dall'area stampata dall'impaginazione. Sono nel file e non sulla
    carta. Contarle vorrebbe dire misurare qualcosa che nessuno vede.
    """
    return -1 <= parola["x0"] <= larghezza + 1


# ============================================================
# LE TRE IPOTESI SUL LEGAME, MESSE ALLA PROVA
# ============================================================

def prove_del_legame(pagine) -> dict:
    """Conta, su un campione di pagine, quante righe di prezzo trovano il loro
    prodotto in ciascuno dei tre modi possibili.

    Non sceglie: conta. La scelta viene dopo, e si vede nei numeri.
    """
    esiti = {"riga": 0, "matrice": 0, "matrice_affiancata": 0, "orfane": 0}
    righe_di_prezzo = 0
    esempi = {"riga": [], "matrice": [], "matrice_affiancata": [], "orfane": []}

    for indice, pagina in enumerate(pagine):
        larghezza = pagina["larghezza"]
        parole = [w for w in pagina["parole"] if dentro_pagina(w, larghezza)]
        if not parole:
            continue
        precedente = pagine[indice - 1] if indice > 0 else None

        for riga in bande_orizzontali(parole):
            prezzi = [w for w in riga["parole"] if e_prezzo(w["text"])]
            if len(prezzi) < MIN_PREZZI_RIGA:
                continue
            righe_di_prezzo += 1
            primo_prezzo = min(w["x0"] for w in prezzi)

            # 1) il codice sta sulla stessa riga, a sinistra del primo prezzo
            codici = [w for w in riga["parole"]
                      if e_codice(w["text"]) and w["x0"] < primo_prezzo]
            if codici:
                esiti["riga"] += 1
                if len(esempi["riga"]) < 3:
                    esempi["riga"].append(
                        f"pag {pagina['numero']}: {codici[0]['text']} -> {prezzi[0]['text']}")
                continue

            # 2) un'etichetta di testo a sinistra, sulla stessa riga: matrice
            etichette = [w for w in riga["parole"]
                         if w["x0"] < primo_prezzo
                         and not e_prezzo(w["text"]) and not VALUTA.fullmatch(w["text"].strip())
                         and any(c.isalpha() for c in w["text"])]
            if etichette:
                esiti["matrice"] += 1
                if len(esempi["matrice"]) < 3:
                    testo = " ".join(w["text"] for w in etichette[:3])
                    esempi["matrice"].append(
                        f"pag {pagina['numero']}: {testo} -> {prezzi[0]['text']}")
                continue

            # 3) l'etichetta sta sulla pagina accanto, alla stessa altezza.
            #
            # "Accanto" va verificato, non dedotto dalla posizione nell'elenco.
            # Campionando un documento di 568 pagine l'elemento precedente puo'
            # essere la pagina 34 mentre quella corrente e' la 55: contarle come
            # affiancate produceva legami inventati, ed e' comparso negli esempi
            # come "pag 34+55". Si confrontano i NUMERI di pagina.
            if precedente is not None and precedente["numero"] == pagina["numero"] - 1:
                vicine = [w for w in precedente["parole"]
                          if dentro_pagina(w, precedente["larghezza"])
                          and abs(w["top"] - riga["y"]) <= BANDA + 2
                          and any(c.isalpha() for c in w["text"])
                          and not e_prezzo(w["text"])]
                if vicine:
                    esiti["matrice_affiancata"] += 1
                    if len(esempi["matrice_affiancata"]) < 3:
                        testo = " ".join(w["text"] for w in vicine[:3])
                        esempi["matrice_affiancata"].append(
                            f"pag {precedente['numero']}+{pagina['numero']}: "
                            f"{testo} -> {prezzi[0]['text']}")
                    continue

            esiti["orfane"] += 1
            if len(esempi["orfane"]) < 3:
                esempi["orfane"].append(
                    f"pag {pagina['numero']} y={riga['y']:.0f}: "
                    + " ".join(w["text"] for w in riga["parole"][:6]))

    return {"righe_di_prezzo": righe_di_prezzo, "conteggi": esiti, "esempi": esempi}


FORZA_MINIMA = 0.55      # sotto questa quota nessuna forma e' dichiarata vincente


def legame_dichiarato(prove: dict) -> dict:
    """Trasforma i conteggi in una dichiarazione, o in un'ammissione.

    La soglia esiste per una ragione sola: una forma che spiega meno della
    meta' delle righe non e' la forma del documento, e dirlo lo stesso sarebbe
    il ritorno all'ipotesi. Sotto soglia la firma dice 'non determinato', che e'
    un'informazione vera e utile.
    """
    totale = prove["righe_di_prezzo"]
    if not totale:
        return {"forma": "nessun prezzo trovato", "quota": 0.0, "prova": ""}
    conteggi = prove["conteggi"]
    forma, quante = max(((k, v) for k, v in conteggi.items() if k != "orfane"),
                        key=lambda kv: kv[1])
    quota = quante / totale
    if quota < FORZA_MINIMA:
        return {"forma": "non_determinato", "quota": quota,
                "prova": f"la forma piu' frequente ({forma}) spiega solo il {quota:.0%} "
                         f"delle {totale} righe di prezzo: sotto la soglia del "
                         f"{FORZA_MINIMA:.0%}, il documento non e' pronto"}
    return {"forma": forma, "quota": quota,
            "prova": f"{quante} righe di prezzo su {totale} ({quota:.0%}) legano il prezzo "
                     f"al prodotto in questo modo"}


# ============================================================
# LE ALTRE DOMANDE DELLA FIRMA
# ============================================================

def forma_dei_codici(pagine) -> dict:
    """Che aspetto ha un codice su QUESTO documento, misurato."""
    misti = numerici = 0
    campioni = []
    for pagina in pagine:
        for parola in pagina["parole"]:
            if not dentro_pagina(parola, pagina["larghezza"]):
                continue
            testo = parola["text"].strip()
            if CODICE_MISTO.fullmatch(testo):
                misti += 1
                if len(campioni) < 6:
                    campioni.append(testo)
            elif CODICE_NUMERICO.fullmatch(testo):
                numerici += 1
                if len(campioni) < 6:
                    campioni.append(testo)
    totale = misti + numerici
    if not totale:
        return {"forma": "nessun codice riconosciuto", "campioni": []}
    prevalente = "sigla con lettere e cifre" if misti >= numerici else "numerico lungo"
    return {"forma": prevalente, "misti": misti, "numerici": numerici,
            "campioni": campioni}


def forma_dei_prezzi(pagine) -> dict:
    con_valuta = senza_valuta = 0
    decimali = interi = 0
    for pagina in pagine:
        righe = bande_orizzontali([w for w in pagina["parole"]
                                   if dentro_pagina(w, pagina["larghezza"])])
        for riga in righe:
            testi = [w["text"].strip() for w in riga["parole"]]
            for indice, testo in enumerate(testi):
                if not e_prezzo(testo):
                    continue
                if indice + 1 < len(testi) and VALUTA.fullmatch(testi[indice + 1]):
                    con_valuta += 1
                else:
                    senza_valuta += 1
                if "," in testo:
                    decimali += 1
                else:
                    interi += 1
    return {"con_simbolo_di_valuta": con_valuta, "senza_simbolo": senza_valuta,
            "con_decimali": decimali, "senza_decimali": interi}


def colonne_di_prezzo(pagine) -> dict:
    """Quanti prezzi porta una riga: una colonna sola o diverse varianti."""
    per_riga = []
    for pagina in pagine:
        for riga in bande_orizzontali([w for w in pagina["parole"]
                                       if dentro_pagina(w, pagina["larghezza"])]):
            quanti = sum(1 for w in riga["parole"] if e_prezzo(w["text"]))
            if quanti:
                per_riga.append(quanti)
    if not per_riga:
        return {"massimo": 0, "tipico": 0}
    return {"massimo": max(per_riga),
            "tipico": statistics.mode(per_riga),
            "righe_con_piu_di_uno": sum(1 for q in per_riga if q > 1)}


# ============================================================
# LA FIRMA
# ============================================================

def leggi_pagine(pdf_path: str, massimo: int = 50):
    """Un campione di pagine distribuito su tutto il documento.

    Si campiona, non si legge tutto: su un catalogo di 568 pagine leggere ogni
    pagina costa una decina di minuti, e per riconoscere una FORMA non serve
    vederla 568 volte - serve vederla in punti diversi del documento, perche'
    una forma che vale solo all'inizio non e' la forma del documento.

    Di ogni pagina campionata si legge anche la PRECEDENTE: se le etichette
    stanno sulla pagina affiancata, e' li' che vanno cercate.
    """
    import pdfplumber
    scelte, viste = [], set()
    with pdfplumber.open(pdf_path) as pdf:
        totale = len(pdf.pages)
        passo = max(1, totale // massimo)
        def prendi(indice):
            if indice in viste or indice < 0 or indice >= totale:
                return None
            viste.add(indice)
            pagina = pdf.pages[indice]
            dato = {"numero": indice + 1, "parole": pagina.extract_words(),
                    "larghezza": float(pagina.width)}
            try:
                pagina.flush_cache()
            except Exception:
                pass
            return dato
        for indice in range(0, totale, passo):
            corrente = prendi(indice)
            if corrente is None:
                continue
            if sum(1 for w in corrente["parole"] if e_prezzo(w["text"])) < 3:
                continue
            precedente = prendi(indice - 1)
            if precedente is not None:
                scelte.append(precedente)
            scelte.append(corrente)
    # l'ordine conta: prove_del_legame guarda la pagina che sta prima nell'elenco
    scelte.sort(key=lambda p: p["numero"])
    return scelte, totale


def firma_del_documento(pdf_path: str) -> dict:
    pagine, totale = leggi_pagine(pdf_path)
    prove = prove_del_legame(pagine)
    return {
        "documento": pdf_path.split("/")[-1],
        "pagine_totali": totale,
        "pagine_misurate": len(pagine),
        "legame": legame_dichiarato(prove),
        "conteggi_del_legame": prove["conteggi"],
        "esempi": prove["esempi"],
        "codici": forma_dei_codici(pagine),
        "prezzi": forma_dei_prezzi(pagine),
        "colonne_di_prezzo": colonne_di_prezzo(pagine),
    }


def stampa(f: dict) -> None:
    print("\n" + "=" * 68)
    print(f"FIRMA DI  {f['documento']}")
    print(f"{f['pagine_totali']} pagine · misurate {f['pagine_misurate']} (quelle con prezzi, e le precedenti)")
    print("=" * 68)

    legame = f["legame"]
    print(f"\nCOME UN PREZZO SA DI CHI E'   ->  {legame['forma'].upper()}")
    print(f"   {legame['prova']}")
    print("\n   come si distribuiscono le righe di prezzo:")
    for forma, quante in f["conteggi_del_legame"].items():
        if quante:
            print(f"      {forma:22} {quante:>6}")
    for forma, righe in f["esempi"].items():
        for riga in righe[:2]:
            print(f"      [{forma}] {riga[:80]}")

    c = f["codici"]
    print(f"\nFORMA DEI CODICI              ->  {c['forma']}")
    if c.get("campioni"):
        print(f"   esempi: {', '.join(c['campioni'])}")
        print(f"   sigle {c.get('misti', 0)} · numerici lunghi {c.get('numerici', 0)}")

    p = f["prezzi"]
    print("\nFORMA DEI PREZZI")
    print(f"   con simbolo di valuta accanto : {p['con_simbolo_di_valuta']}")
    print(f"   senza simbolo                 : {p['senza_simbolo']}")
    print(f"   con decimali                  : {p['con_decimali']}")

    k = f["colonne_di_prezzo"]
    print(f"\nPREZZI PER RIGA               ->  tipico {k['tipico']}, massimo {k['massimo']}")
    print(f"   righe con piu' di un prezzo: {k.get('righe_con_piu_di_uno', 0)}")
    print()


if __name__ == "__main__":
    percorso = sys.argv[1] if len(sys.argv) > 1 else "catalogo.pdf"
    esito = firma_del_documento(percorso)
    stampa(esito)
    if "--json" in sys.argv:
        print(json.dumps(esito, ensure_ascii=False, indent=2, default=str))
