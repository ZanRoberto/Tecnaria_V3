# -*- coding: utf-8 -*-
"""Recupera le colonne di prezzo che l'estrazione delle schede ha scartato.

    python3 completa_prezzi.py catalogo.pdf schede_prodotto.json schede_nuove.json

NON rigenera le schede: le apre, aggiunge i prezzi mancanti e le riscrive. Le
4.346 schede che gia' funzionano restano identiche, campo per campo.

IL DIFETTO, MISURATO IL 7ott2026
================================
Sulle pagine dei tavoli e delle panche ogni riga di listino porta DUE prezzi -
le gambe in vetro extra-chiaro e quelle in vetro fume' grigio - e la scheda ne
teneva uno solo:

    TAW561   pagina: 3.526 e 3.786     scheda: {'Wildwood': '3.526'}
    TAW565   pagina: 4.790 e 5.119     scheda: {'Wildwood': '4.790'}
    TAW568   pagina: 6.469 e 6.961     scheda: {'Wildwood': '6.469'}

    66 schede su 7 pagine: 332, 333, 339, 341, 343, 345, 391

LA CAUSA, LETTA DALLA TABELLA
=============================
L'intestazione di quelle tabelle e' alta due righe, e l'estrattore ne legge una:

    riga 0 (con "Codice")   ... 'Codice Code' | 'Wildwood'              | ''
    riga 1 (continuazione)  ... ''            | 'Gambe vetro extra-chi' | 'Gambe vetro fume' grigi'
    riga 4 (dati)           ... 'TAW561'      | '3.526'                 | '3.786'

Nella riga 0 l'ultima colonna ha l'etichetta VUOTA, e una colonna senza
etichetta viene saltata. Il prezzo non e' illeggibile: e' leggibile e scartato.
E l'etichetta che sopravvive, 'Wildwood', non e' nemmeno il nome della colonna:
e' il nome della gamma finito nella cella sbagliata.

CONSEGUENZA PRIMA DELLA CORREZIONE
==================================
Quando il motore SCRIVE una risposta legge il testo della pagina e i due prezzi
li vede entrambi. Quando CONTA - confronto col budget, "qual e' il piu'
economico" - legge la scheda e ne vede uno solo. Quindi su un tavolo il conto
poteva uscire sul prezzo sbagliato.

COME SI VERIFICA CHE IL LAVORO SIA FATTO BENE
=============================================
Lo strumento stampa, alla fine, quante schede hanno guadagnato un prezzo e su
quali pagine, e NON tocca nient'altro: ogni valore gia' presente resta com'e'.
Dopo, 'python3 banco.py' non deve peggiorare di un punto.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

CODICE_INTESTAZIONE = re.compile(r"\bcodice\b|\bcode\b|\bsku\b", re.I)
MISURA = re.compile(r"larghezz|width|profondit|depth|altezz|height|lunghezz|length|diametr|diameter", re.I)
IMPORTO = re.compile(r"^\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?$|^\d{2,4}(?:,\d{1,2})?$")
CODICE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{3,}$")


def pulisci(cella) -> str:
    return re.sub(r"\s+", " ", (cella or "").replace("\n", " ")).strip()


def intestazioni_complete(tabella, riga_dati: int):
    """L'etichetta di ogni colonna e' TUTTO cio' che sta sopra la prima riga di
    dati, non solo la riga che contiene "Codice".

    E' qui la correzione. Unendo le righe, la colonna che nella riga del codice
    era vuota riceve la sua etichetta vera ("Gambe vetro fume' grigio / Smoked
    grey glass legs") e smette di essere scartata.
    """
    larghezza = max(len(r) for r in tabella[:riga_dati + 1])
    etichette = []
    for colonna in range(larghezza):
        pezzi = []
        for riga in tabella[:riga_dati]:
            if colonna < len(riga):
                testo = pulisci(riga[colonna])
                if testo and testo not in pezzi:
                    pezzi.append(testo)
        etichette.append(" ".join(pezzi).strip())
    return etichette


def prima_riga_di_dati(tabella, colonna_codice: int):
    for indice, riga in enumerate(tabella):
        if colonna_codice < len(riga) and CODICE.fullmatch(pulisci(riga[colonna_codice]).upper()):
            if any(c.isdigit() for c in pulisci(riga[colonna_codice])):
                return indice
    return None


def prezzi_della_pagina(pagina):
    """Le righe di listino della pagina: [(codice, misure, prezzi), ...].

    I prezzi si tengono legati alla LORO RIGA, con le misure che quella riga
    dichiara. Non basta il codice.

    Misurato il 7ott2026 mentre provavo questo stesso strumento: a pagina 19 il
    codice 1409071 compare su due righe diverse,

        36e8 TV UNIT  368    40,6  18,4  1409071  2.619
        36e8 TV UNIT  331,2  40,6  18,4  1409071  4.108

    cioe' due prodotti di larghezza diversa che condividono il codice. Una
    prima versione raccoglieva i prezzi per codice e li mescolava: alla scheda
    larga 368 sarebbe finito anche il prezzo della larga 331,2. Un danno
    silenzioso, e peggiore del difetto che lo strumento deve correggere.
    """
    trovate = []
    for tabella in pagina.extract_tables():
        if not tabella:
            continue
        colonna_codice = None
        for riga in tabella:
            for indice, cella in enumerate(riga):
                if CODICE_INTESTAZIONE.search(pulisci(cella)):
                    colonna_codice = indice
                    break
            if colonna_codice is not None:
                break
        if colonna_codice is None:
            continue
        inizio = prima_riga_di_dati(tabella, colonna_codice)
        if inizio is None:
            continue
        etichette = intestazioni_complete(tabella, inizio)
        for riga in tabella[inizio:]:
            if colonna_codice >= len(riga):
                continue
            codice = pulisci(riga[colonna_codice]).upper()
            if not CODICE.fullmatch(codice) or not any(c.isdigit() for c in codice):
                continue
            misure, prezzi = {}, {}
            for colonna, cella in enumerate(riga):
                if colonna == colonna_codice or colonna >= len(etichette):
                    continue
                etichetta = etichette[colonna]
                valore = pulisci(cella)
                if not etichetta or not valore:
                    continue
                if MISURA.search(etichetta):
                    misure[etichetta] = valore
                elif colonna > colonna_codice and IMPORTO.fullmatch(valore):
                    prezzi[etichetta] = valore
            if prezzi:
                trovate.append((codice, misure, prezzi))
    return trovate


def riga_della_scheda(scheda, righe):
    """La riga di listino che appartiene a questa scheda, o None.

    Si confrontano le misure: la scheda e la riga devono concordare su tutte le
    etichette che hanno in comune. Se restano due righe indistinguibili non si
    sceglie a caso - si restituisce None e la scheda resta com'e'. Meglio un
    prezzo non recuperato che un prezzo attaccato al prodotto sbagliato.
    """
    codice = scheda["codice"].upper()
    attributi = scheda.get("attributi") or {}
    candidate = [(m, p) for c, m, p in righe if c == codice]
    if not candidate:
        return None
    if len(candidate) == 1:
        return candidate[0][1]
    coerenti = []
    for misure, prezzi in candidate:
        comuni = set(misure) & set(attributi)
        if comuni and all(misure[k] == attributi[k] for k in comuni):
            coerenti.append(prezzi)
    return coerenti[0] if len(coerenti) == 1 else None


def completa(pdf_path: str, schede_path: str, destinazione: str) -> dict:
    import pdfplumber

    dati = json.loads(Path(schede_path).read_text(encoding="utf-8"))
    schede = dati["schede"]
    per_pagina = {}
    for scheda in schede:
        per_pagina.setdefault(scheda["pagina"], []).append(scheda)

    aggiunti = 0
    schede_toccate = 0
    pagine_toccate = {}
    with pdfplumber.open(pdf_path) as pdf:
        for numero in sorted(per_pagina):
            if numero < 1 or numero > len(pdf.pages):
                continue
            righe = prezzi_della_pagina(pdf.pages[numero - 1])
            if not righe:
                continue
            for scheda in per_pagina[numero]:
                nuovi = riga_della_scheda(scheda, righe)
                if not nuovi:
                    continue
                prezzi = scheda.setdefault("prezzi", {})
                prima = len(prezzi)
                for etichetta, valore in nuovi.items():
                    # NON si sovrascrive mai un valore gia' presente: si aggiunge
                    # solo cio' che manca. Una correzione che cambia dati gia'
                    # buoni non e' una correzione, e' un rischio.
                    if etichetta in prezzi:
                        continue
                    if valore in prezzi.values():
                        continue        # stesso importo gia' presente con altra etichetta
                    prezzi[etichetta] = valore
                if len(prezzi) > prima:
                    aggiunti += len(prezzi) - prima
                    schede_toccate += 1
                    pagine_toccate[numero] = pagine_toccate.get(numero, 0) + 1
            if numero % 50 == 0:
                print(f"  ...pagina {numero}", flush=True)

    dati["report"]["prezzi_recuperati"] = aggiunti
    dati["report"]["schede_completate"] = schede_toccate
    dati["pdf_sha256"] = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()
    Path(destinazione).write_text(json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"prezzi_recuperati": aggiunti, "schede_completate": schede_toccate,
            "pagine": dict(sorted(pagine_toccate.items(), key=lambda x: -x[1])[:15])}


if __name__ == "__main__":
    pdf = sys.argv[1] if len(sys.argv) > 1 else "catalogo.pdf"
    dentro = sys.argv[2] if len(sys.argv) > 2 else "static/data/schede_prodotto.json"
    fuori = sys.argv[3] if len(sys.argv) > 3 else "schede_prodotto_nuove.json"
    esito = completa(pdf, dentro, fuori)
    print(f"\nprezzi recuperati : {esito['prezzi_recuperati']}")
    print(f"schede completate : {esito['schede_completate']}")
    print(f"pagine piu' toccate: {esito['pagine']}")
    print(f"scritto           : {fuori}")
