"""Confronto fra due polizze (o due fascicoli: condizioni + appendici + deroghe).

Il modello LEGGE e propone; il codice VERIFICA e decide. Regole (20ott2026):
1. Ogni dato estratto porta la sua citazione copiata dal testo e la pagina. La citazione
   deve esistere davvero nel fascicolo (anche se il PDF ha spezzato le celle di una tabella
   su piu' righe); se e' su un'altra pagina la pagina si corregge, se non c'e' il dato si
   SCARTA e si dichiara nei Controlli.
2. Ogni numero del valore deve comparire nella citazione: un «60%» che nel testo non c'e'
   non entra nella griglia.
3. La deroga prevale: un dato marcato come deroga lo e' solo se la pagina o la citazione
   lo dicono (deroga, appendice, condizioni particolari...). Il dato base che la deroga
   modifica resta visibile, marcato «superato dalla deroga».
4. Il premio si confronta anche sulla stessa base: premio per 1.000 EUR di capitale MEDIO,
   calcolato dal codice, mai dal modello. Se la base non si puo' calcolare si dice perche'.
5. Nel giudizio, nelle trappole e nelle azioni ogni numero deve venire da un dato
   verificato o da un calcolo del codice: altrimenti la frase si toglie (e si dichiara).
6. Nessun dato verificato puo' sparire: se il modello non lo mette in una riga del
   confronto, la riga la aggiunge il codice («non confrontata»).
7. Una voce essenziale assente si scrive «non l'ho trovato, ho cercato nelle pagine ...»,
   mai «non c'e'».
"""
from __future__ import annotations

import concurrent.futures
import contextvars
import io
import json
import os
import re
import threading
import time
import unicodedata
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

import documenti_aziendali as docs

router = APIRouter(prefix="/api/confronta", tags=["Confronto polizze"])

# ------------------------------------------------------------------ collegamenti (da app.py)
scrivi: Optional[Callable[[str, str, int, float], str]] = None
credito_ok: Callable[[str], bool] = lambda cliente: True

# testo mandato al modello in una chiamata di lettura: parti piccole, lette in parallelo,
# perche' il tempo lo prende la SCRITTURA del modello e una risposta lunga si tronca
MAX_CARATTERI_LOTTO = 16000
MAX_DOCUMENTI_LATO = 8
STATI_AMMESSI = ("attivo", "da_confermare")

# le voci: un numero finito di forme, uguali per ogni compagnia
VOCI: List[Tuple[str, str, bool]] = [
    # chiave, etichetta, essenziale (se manca si dichiara «non trovato»)
    ("prodotto", "Compagnia e prodotto", True),
    ("assicurato", "Assicurato / profilo", False),
    ("premio", "Premio", True),
    ("frazionamento", "Frazionamento del premio", False),
    ("durata", "Durata", True),
    ("capitale", "Capitale / somma assicurata / massimale principale", True),
    ("andamento_capitale", "Andamento del capitale", False),
    ("prestazione", "Prestazione principale", True),
    ("carenza", "Carenza", True),
    ("esclusione", "Esclusioni", True),
    ("franchigia", "Franchigie e scoperti", False),
    ("massimale", "Massimali e limiti di indennizzo", False),
    ("garanzia_complementare", "Garanzie complementari", False),
    ("servizi", "Servizi e assistenza", False),
    ("costi", "Costi e caricamenti", False),
    ("sconti", "Sconti", False),
    ("recesso", "Recesso e rimborso", False),
    ("riscatto", "Riscatto e riduzione", False),
    ("revisione_premio", "Revisione del premio / della tariffa", False),
    ("non_contestabilita", "Non contestabilità delle dichiarazioni", False),
    ("limitazioni", "Altre limitazioni (es. limiti di legge)", False),
    ("accertamenti", "Accertamenti sanitari / assuntivi", False),
    ("altro", "Altre condizioni", False),
]
ETICHETTE = {k: e for k, e, _ in VOCI}
ESSENZIALI = [k for k, _, ess in VOCI if ess]
# voci che hanno UN valore per polizza: una deroga sostituisce il valore base
VOCI_SINGOLE = {"premio", "frazionamento", "durata", "capitale", "andamento_capitale", "prestazione",
                "carenza", "costi", "recesso", "riscatto", "revisione_premio", "non_contestabilita",
                "accertamenti", "prodotto", "assicurato"}
GIUDIZI = {"meglio_a", "meglio_b", "uguale", "diverso", "solo_a", "solo_b", "non_confrontata"}

_SEGNI_DEROGA = re.compile(r"\bderog|\bappendic|condizioni\s+(?:particolari|speciali|aggiuntive)|"
                           r"\ba\s+parziale\s+modifica|\bin\s+luogo\s+d|\bsostituisc|\bclausola\s+speciale|"
                           r"\bcondizione\s+particolare|\ballegato\s+di\s+modifica", re.I)


# ------------------------------------------------------------------ testo: le forme dei PDF
def _piano(testo: str) -> str:
    """Testo confrontabile: legature, accenti, apostrofi, a capo con trattino, spazi."""
    testo = unicodedata.normalize("NFKC", str(testo or ""))
    testo = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", testo)          # assicu-\nrato -> assicurato
    testo = unicodedata.normalize("NFD", testo.lower())
    testo = "".join(c for c in testo if unicodedata.category(c) != "Mn")
    testo = re.sub(r"(\d)[.](\d{3})(?!\d)", r"\1\2", testo)       # 200.000 -> 200000
    testo = re.sub(r"[^a-z0-9%,]+", " ", testo)
    testo = re.sub(r"(\d),(\d)", r"\1.\2", testo)                 # 22,0 -> 22.0
    testo = testo.replace(",", " ")
    return re.sub(r"\s+", " ", testo).strip()


def _parole(testo: str) -> List[str]:
    return _piano(testo).split()


_NUM = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?")


def _numero(token: str) -> Optional[float]:
    t = token.strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", t):
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _forma(valore: float) -> str:
    return str(int(round(valore))) if abs(valore - round(valore)) < 1e-9 else f"{valore:.4f}".rstrip("0").rstrip(".")


def numeri(testo: str) -> Set[str]:
    """I numeri di un testo in forma unica: «200.000,00» e «200000» sono lo stesso numero."""
    out: Set[str] = set()
    for m in _NUM.finditer(unicodedata.normalize("NFKC", str(testo or ""))):
        v = _numero(m.group(0))
        if v is not None:
            out.add(_forma(v))
    return out


_PERCENTO = re.compile(r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)\s*(?:%|per\s*cento\b)", re.I)


def percentuali(testo: str) -> Set[str]:
    """I numeri scritti come percentuale: «40%» non e' lo stesso dato di «40 metri»."""
    out: Set[str] = set()
    for m in _PERCENTO.finditer(unicodedata.normalize("NFKC", str(testo or ""))):
        v = _numero(m.group(1))
        if v is not None:
            out.add(_forma(v))
    return out


def trova_citazione(citazione: str, testo: str) -> bool:
    """La citazione c'e' nel testo? Uguale, oppure con le stesse parole nello stesso ordine
    entro uno spazio stretto: i PDF spezzano le celle di una tabella su piu' righe e mettono
    in mezzo le celle vicine («Invalidita' permanente da / infortunio o malattia / compresa
    nel premio / base / ... 60%»)."""
    pc, pt = _piano(citazione), _piano(testo)
    if not pc or len(pc.split()) < 3:
        return False
    if pc in pt:
        return True
    cit, txt = pc.split(), pt.split()
    finestra = len(cit) * 3 + 12
    for start, parola in enumerate(txt):
        if parola != cit[0]:
            continue
        i, j = 1, start + 1
        while i < len(cit) and j < len(txt) and j - start <= finestra:
            if txt[j] == cit[i]:
                i += 1
            j += 1
        if i == len(cit):
            return True
    return False


# ------------------------------------------------------------------ il fascicolo di una polizza
def _numero_pagina(label: str) -> Optional[int]:
    m = re.match(r"pagina\s+(\d+)", label or "")
    return int(m.group(1)) if m else None


def fascicolo(lato: str, documenti: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Le pagine di un lato, in ordine, con un id breve per il modello (A1, A2, ...).
    documenti = [{"titolo", "id", "chunks": [{"label", "text"}]}]. Le parti di una pagina
    lunga (pagina 3 · parte 2) tornano insieme; le pagine senza testo si dichiarano."""
    pagine: List[Dict[str, Any]] = []
    senza_testo: List[str] = []
    for doc in documenti:
        ordine: List[str] = []
        testi: Dict[str, List[str]] = {}
        numeri_visti: List[int] = []
        for chunk in doc.get("chunks") or []:
            label = str(chunk.get("label") or "documento")
            base = label.split(" · ")[0]
            if base not in testi:
                ordine.append(base)
                testi[base] = []
                n = _numero_pagina(base)
                if n:
                    numeri_visti.append(n)
            testi[base].append(str(chunk.get("text") or ""))
        if numeri_visti:
            mancanti = sorted(set(range(1, max(numeri_visti) + 1)) - set(numeri_visti))
            senza_testo.extend(f"«{doc.get('titolo')}» pagina {n}" for n in mancanti)
        for base in ordine:
            testo = "\n".join(testi[base]).strip()
            if not testo:
                continue
            pid = f"{lato}{len(pagine) + 1}"
            pagine.append({"id": pid, "documento": doc.get("titolo") or "", "documento_id": doc.get("id"),
                           "label": base, "testo": testo,
                           "deroga": bool(_SEGNI_DEROGA.search(testo))})
    return {"lato": lato, "pagine": pagine, "senza_testo": senza_testo}


def _lotti(pagine: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    lotti: List[List[Dict[str, Any]]] = [[]]
    peso = 0
    for p in pagine:
        dim = len(p["testo"]) + 80
        if lotti[-1] and peso + dim > MAX_CARATTERI_LOTTO:
            lotti.append([])
            peso = 0
        lotti[-1].append(p)
        peso += dim
    return [l for l in lotti if l]


# ------------------------------------------------------------------ lettura (modello)
SYSTEM_LETTURA = """Sei un perito assicurativo che legge un fascicolo di polizza per un confronto di parte (come un CTP). Estrai TUTTE le condizioni che contano per l'assicurato, dalla prima all'ultima pagina, deroghe e appendici comprese.
Il testo viene da PDF. Sotto «[RIGHE DELLE TABELLE]» ogni riga di tabella e' gia' ricostruita, con il nome della colonna accanto a ogni valore: per le tabelle usa QUELLE righe e copia la citazione da li'. Se una tabella non e' ricostruita, le sue celle possono arrivare una per riga: ricostruisci con prudenza.
Per ogni condizione scrivi un oggetto con:
- "chiave": una tra {chiavi}
- "nome": nome specifico breve (es. "Inabilita' temporanea totale", "Immersione subacquea", "Carenza per malattia")
- "valore": il contenuto in poche parole, con i numeri esatti e le unita' (es. "6 mesi", "348,00 EUR annui", "esclusa oltre 40 metri")
- "citazione": la frase del testo che lo prova, COPIATA ESATTAMENTE parola per parola (massimo 300 caratteri, nessuna parola cambiata, nessun riassunto)
- "pagina": l'id della pagina tra doppie parentesi quadre in cui sta la citazione (es. "A3")
- "deroga": true solo se la condizione viene da una deroga, appendice o condizione particolare che modifica le condizioni generali
- "inclusa": true se la garanzia e' attiva/compresa nel premio, false se opzionale non attivata o non prevista, null se non si applica
Regole:
1. Una esclusione, una franchigia, una garanzia complementare, un servizio = un oggetto ciascuno.
2. Se una condizione non e' prevista dal prodotto ma il testo lo dice ("non prevista", "non previsto"), estraila con inclusa false: e' un'informazione.
3. Se una deroga modifica una condizione, estrai SIA la condizione base SIA la deroga, ognuna con la sua citazione.
4. Non inventare: solo quello che e' scritto. Niente conoscenze generali.
Rispondi SOLO con JSON: {"ramo": "...", "compagnia": "...", "prodotto": "...", "voci": [ ... ]}"""


def _json(raw: str) -> Optional[Any]:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    try:
        return json.loads(raw)
    except ValueError:
        pass
    inizio, fine = raw.find("{"), raw.rfind("}")
    if inizio >= 0 and fine > inizio:
        try:
            return json.loads(raw[inizio:fine + 1])
        except ValueError:
            return None
    return None


def _chiedi_json(sistema: str, utente: str, max_tokens: int) -> Dict[str, Any]:
    if scrivi is None:
        raise RuntimeError("Modello non configurato")
    for tentativo in range(2):
        raw = scrivi(sistema, utente if not tentativo else
                     utente + "\n\nATTENZIONE: la risposta precedente non era JSON valido. Rispondi SOLO con il JSON.",
                     max_tokens, 0.0)
        dati = _json(raw)
        if isinstance(dati, dict):
            return dati
    raise RuntimeError("Il modello non ha restituito dati leggibili")


def _testo_lotto(lotto: List[Dict[str, Any]]) -> str:
    return "\n\n".join(f"[[{p['id']}]] documento «{p['documento']}» {p['label']}\n{p['testo']}" for p in lotto)


Avanzamento = Optional[Callable[[str, int, int], None]]
MAX_PARALLELO = 6            # chiamate al modello contemporanee


def _sistema_lettura() -> str:
    return SYSTEM_LETTURA.replace("{chiavi}", ", ".join(k for k, _, _ in VOCI))


def _leggi_lotto(lotto: List[Dict[str, Any]], non_lette: List[str]) -> List[Dict[str, Any]]:
    """Una parte del fascicolo. Se il modello restituisce qualcosa di illeggibile (risposta
    troncata perche' troppo lunga) la parte si divide in due e si rilegge: un pezzo andato
    storto non fa fallire tutto il confronto. Una pagina sola che non si legge si dichiara."""
    try:
        return [_chiedi_json(_sistema_lettura(), "FASCICOLO:\n" + _testo_lotto(lotto), 7000)]
    except RuntimeError:
        if len(lotto) == 1:
            non_lette.append(f"{lotto[0]['documento']} {lotto[0]['label']}")
            return []
        meta = len(lotto) // 2
        return _leggi_lotto(lotto[:meta], non_lette) + _leggi_lotto(lotto[meta:], non_lette)


def _unisci_letture(risultati: List[Dict[str, Any]]) -> Dict[str, Any]:
    voci: List[Dict[str, Any]] = []
    testa = {"ramo": "", "compagnia": "", "prodotto": ""}
    for r in risultati:
        for k in testa:
            if not testa[k] and isinstance(r.get(k), str):
                testa[k] = r[k].strip()[:200]
        for v in r.get("voci") or []:
            if isinstance(v, dict):
                voci.append(v)
    return {**testa, "voci": voci}


def leggi_lati(fascicoli: List[Dict[str, Any]], avanza: Avanzamento = None) -> List[Dict[str, Any]]:
    """Tutte le parti di tutti i lati, lette insieme in parallelo (prova dal vivo 21ott2026:
    un fascicolo vero di 27 pagine letto in un solo blocco superava il tempo della richiesta)."""
    lavori = [(n, lotto) for n, f in enumerate(fascicoli) for lotto in _lotti(f["pagine"])]
    risultati: List[List[Dict[str, Any]]] = [[] for _ in fascicoli]
    non_lette: List[List[str]] = [[] for _ in fascicoli]
    fatte = 0
    if avanza:
        avanza("lettura", 0, len(lavori))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(MAX_PARALLELO, len(lavori)))) as pool:
        futuri = {pool.submit(contextvars.copy_context().run, _leggi_lotto, lotto, non_lette[n]): n
                  for n, lotto in lavori}
        for futuro in concurrent.futures.as_completed(futuri):
            risultati[futuri[futuro]].extend(futuro.result())
            fatte += 1
            if avanza:
                avanza("lettura", fatte, len(lavori))
    return [{**_unisci_letture(r), "non_lette": nl} for r, nl in zip(risultati, non_lette)]


def leggi_lato(fasc: Dict[str, Any]) -> Dict[str, Any]:
    """Le voci proposte dal modello per un lato (piu' chiamate se il fascicolo e' lungo)."""
    return leggi_lati([fasc])[0]


# ------------------------------------------------------------------ verifica (codice)
_SEGNO_RIGHE = "[RIGHE DELLE TABELLE]"
_ETICHETTA_COLONNA = re.compile(r"(^|\|\s*)[^|:]{1,60}:\s")


def _righe(testo: str) -> List[Tuple[str, str]]:
    """(riga com'e', riga senza i nomi di colonna). Nelle righe di tabella ricostruite i
    nomi di colonna («Premio:», «Valore:») non contano come contenuto: «Premio: inclusa»
    non e' una riga che parla del premio."""
    out: List[Tuple[str, str, bool]] = []
    in_tabella = False
    for riga in testo.splitlines():
        if riga.strip() == _SEGNO_RIGHE:
            in_tabella = True
            continue
        if riga.strip():
            out.append((riga, _ETICHETTA_COLONNA.sub(r"\1", riga) if in_tabella else riga, in_tabella))
    return out


def _riga_con_nome(citazione: str, testo: str, nome: str) -> Optional[str]:
    """La riga del testo che contiene la citazione E almeno una parola del nome della voce.
    E' la forma normale delle tabelle: la cella del valore e' corta («348,00 EUR», «15
    anni», «non previsto») e il nome della voce sta nella cella accanto della stessa riga."""
    pc = _piano(citazione)
    if not pc:
        return None
    parole_nome = {p for p in _parole(nome) if len(p) >= 4} - _PAROLE_VUOTE
    if not parole_nome:
        return None
    tutte = _righe(testo)
    contengono = []
    for n, (r, c, t) in enumerate(tutte):
        if f" {pc} " not in f" {_piano(c)} ":
            continue
        if not t and n and not tutte[n - 1][2]:
            # testo semplice con una cella per riga: il nome della voce sta nella riga sopra
            # («Premio annuo lordo» / «348,00 EUR»): la prova sono le due righe insieme
            sopra = tutte[n - 1][0]
            if not (set(_parole(r)) & parole_nome) and set(_parole(sopra)) & parole_nome:
                r, c = sopra + " " + r, sopra + " " + c
        contengono.append((r, _piano(c), t))
    # se la cella sta in una riga di TABELLA ricostruita, decide la tabella: il testo semplice
    # della stessa pagina mescola le celle di righe e colonne diverse sulla stessa linea
    # («compresa nel premio anticipazione del capitale inclusa»)
    if any(t for _, _, t in contengono):
        contengono = [x for x in contengono if x[2]]
    for riga, pr, tabella in contengono:
        if tabella:
            # in una riga di tabella chi e' la riga lo dice la sua prima cella con parole
            # («Premio annuo lordo», «Riscatto»): «compresa nel premio base», in un'altra
            # cella, non fa della riga dell'invalidita' una riga sul premio
            celle = [c for c in _ETICHETTA_COLONNA.sub(r"\1", riga).split("|")]
            identita = next((c for c in celle if any(len(p) >= 4 and p.isalpha() for p in _parole(c))), "")
            if parole_nome & set(_parole(identita)):
                return riga
        elif parole_nome & set(pr.split()):
            return riga
    return None


def _riga_di_tabella_equivalente(voce: Dict[str, Any], testo: str) -> Optional[str]:
    """Il testo semplice di un PDF mescola le colonne di una tabella sulla stessa linea, e il
    modello a volte copia proprio quella («Doppio capitale per non prevista - - infortunio»,
    prova dal vivo 21ott2026). Se la stessa prova sta tutta in UNA riga di tabella
    ricostruita, si mostra quella: si legge, e tiene insieme nome, valore e colonna."""
    parole = set(_parole(voce["citazione"]))
    if len(parole) < 4:
        return None
    migliore, punti = None, 0.0
    for riga, _, tabella in _righe(testo):
        if not tabella:
            continue
        quota = len(parole & set(_parole(riga))) / len(parole)
        if quota > punti:
            migliore, punti = riga, quota
    if migliore and punti >= 0.8 and not (numeri(voce["valore"]) - numeri(migliore)) \
            and not (numeri(voce["citazione"]) - numeri(migliore)):
        return migliore
    return None


def _trova_prova(voce: Dict[str, Any], dichiarata: Optional[Dict[str, Any]],
                 pagine: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(pagina, riga). Prima la pagina dichiarata, poi le altre: la citazione intera, oppure
    la cella corta dentro la riga che porta il nome della voce."""
    ordine = ([dichiarata] if dichiarata else []) + [p for p in pagine if p is not dichiarata]
    for pagina in ordine:
        if trova_citazione(voce["citazione"], pagina["testo"]):
            return pagina, _riga_di_tabella_equivalente(voce, pagina["testo"])
        riga = _riga_con_nome(voce["citazione"], pagina["testo"], voce["nome"])
        if riga:
            return pagina, riga
    return None, None


def verifica_lato(fasc: Dict[str, Any], proposte: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(verificate, scartate). Il modello propone, qui si decide."""
    per_id = {p["id"]: p for p in fasc["pagine"]}
    verificate: List[Dict[str, Any]] = []
    scartate: List[Dict[str, Any]] = []
    visti: Set[Tuple[str, str, str]] = set()
    for v in proposte:
        chiave = str(v.get("chiave") or "").strip().lower()
        if chiave not in ETICHETTE:
            chiave = "altro"
        voce = {"chiave": chiave, "nome": str(v.get("nome") or ETICHETTE[chiave]).strip()[:120],
                "valore": str(v.get("valore") or "").strip()[:300],
                "citazione": str(v.get("citazione") or "").strip()[:600],
                "inclusa": v.get("inclusa") if isinstance(v.get("inclusa"), bool) else None}
        pid = str(v.get("pagina") or "").strip().strip("[]").upper()
        if not voce["valore"] or not voce["citazione"]:
            scartate.append({**voce, "lato": fasc["lato"], "motivo": "senza valore o senza citazione"})
            continue
        pagina, riga = _trova_prova(voce, per_id.get(pid), fasc["pagine"])
        if pagina is None:
            scartate.append({**voce, "lato": fasc["lato"], "pagina": pid,
                             "motivo": "citazione non trovata nel fascicolo"})
            continue
        if riga:
            voce["citazione"] = riga                   # la prova e' la riga intera
        mancano = numeri(voce["valore"]) - numeri(voce["citazione"])
        if mancano and not riga:
            # il valore puo' prendere un numero dalla cella accanto (premio «72 EUR/anno» nella
            # stessa riga della garanzia): vale solo se e' la STESSA riga della citazione
            riga = _riga_con_nome(voce["citazione"], pagina["testo"], voce["nome"])
            if riga and not (numeri(voce["valore"]) - numeri(riga)):
                voce["citazione"], mancano = riga, set()
        if mancano:
            scartate.append({**voce, "lato": fasc["lato"], "pagina": pagina["id"],
                             "motivo": "numeri non presenti nella citazione: " + ", ".join(sorted(mancano))})
            continue
        deroga = bool(v.get("deroga")) and (pagina["deroga"] or bool(_SEGNI_DEROGA.search(voce["citazione"])))
        deroga = deroga or bool(re.search(r"\bin\s+(?:parziale\s+)?deroga", voce["citazione"], re.I))
        chiave_unica = (chiave, _piano(voce["nome"]), _piano(voce["citazione"]))
        if chiave_unica in visti:
            continue
        visti.add(chiave_unica)
        verificate.append({**voce, "deroga": deroga, "pagina": pagina["id"],
                           "documento": pagina["documento"], "label": pagina["label"]})
    for n, v in enumerate(verificate, 1):
        v["id"] = f"{fasc['lato']}-{n}"
    _applica_deroghe(verificate)
    return verificate, scartate


# una deroga «a fronte di un referto», «per i contraenti gia' titolari»... vale SOLO a quella
# condizione: il valore base resta quello che vale per tutti gli altri
_CONDIZIONE = re.compile(r"\ba\s+fronte\s+d|\ba\s+condizione|\bqualora|\bpurch|\bprevi[ao]\b|\bsolo\s+se\b|"
                         r"\bnel\s+caso\s+in\s+cui|\bper\s+i\s+contraenti|\bse\s+l|\bin\s+presenza\s+d|"
                         r"\bsubordinat", re.I)
_PAROLE_VUOTE = {"di", "da", "per", "e", "la", "il", "con", "del", "della", "dei", "in", "a", "al", "o"}


def _riguarda(base: Dict[str, Any], deroga: Dict[str, Any]) -> bool:
    if base["chiave"] in VOCI_SINGOLE:
        return True
    nome = set(_parole(base["nome"])) - _PAROLE_VUOTE
    testo = set(_parole(deroga["nome"] + " " + deroga["citazione"]))
    return bool(nome) and len(nome & testo) >= max(1, (len(nome) + 1) // 2)


def _applica_deroghe(voci: List[Dict[str, Any]]) -> None:
    """La deroga prevale, ma solo per quello che dice.
    - voce con un valore per polizza (carenza, premio...) e deroga SENZA condizioni:
      il valore base e' «superato» e non vale piu';
    - deroga CON condizione, o voce a piu' valori (esclusioni, garanzie: «in parziale
      deroga»): il valore base resta, marcato «modificato dalla deroga» con il suo id."""
    deroghe = [v for v in voci if v["deroga"]]
    for v in voci:
        v["superata"] = False
        v["modificata_da"] = []
        v["condizionata"] = bool(v["deroga"] and _CONDIZIONE.search(v["citazione"]))
    for v in voci:
        if v["deroga"]:
            continue
        for d in deroghe:
            if d["chiave"] != v["chiave"] or not _riguarda(v, d):
                continue
            if v["chiave"] in VOCI_SINGOLE and not d["condizionata"]:
                v["superata"] = True
            v["modificata_da"].append(d["id"])


# ------------------------------------------------------------------ calcoli del codice
def _euro(testo: str) -> Optional[float]:
    m = re.search(r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)\s*(?:€|eur\b|euro\b)", testo or "", re.I)
    if not m:
        m = re.search(r"(?:€|eur\b|euro\b)\s*(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)", testo or "", re.I)
    return _numero(m.group(1)) if m else None


def _vigenti(voci: List[Dict[str, Any]], chiave: str) -> List[Dict[str, Any]]:
    return [v for v in voci if v["chiave"] == chiave and not v["superata"]]


def base_premio(voci: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Premio per 1.000 EUR di capitale MEDIO: la base su cui due premi si confrontano.
    Capitale costante -> medio = capitale. Decrescente -> il capitale medio SCRITTO nel
    fascicolo, oppure meta' del capitale solo se il testo dice che scende fino a zero in
    modo lineare. Altrimenti non si calcola, e si dice perche'."""
    # il premio della polizza e' l'importo piu' alto fra le voci «premio»: le altre sono le
    # quote di una garanzia («48 EUR/anno compresi nei 156 EUR»), non il premio (prova dal vivo 21ott2026)
    premi = [p for p in (_euro(v["valore"]) for v in _vigenti(voci, "premio")) if p]
    premio = max(premi) if premi else None
    capitali = [c for c in (_euro(v["valore"]) for v in _vigenti(voci, "capitale")) if c]
    if premio is None:
        return {"calcolabile": False, "motivo": "premio non trovato"}
    if not capitali:
        return {"calcolabile": False, "motivo": "capitale non trovato", "premio": premio}
    capitale = max(capitali)
    testi = " ".join(v["valore"] + " " + v["citazione"]
                     for v in _vigenti(voci, "andamento_capitale") + _vigenti(voci, "capitale")
                     + _vigenti(voci, "prodotto") + _vigenti(voci, "prestazione"))
    piano = _piano(testi)
    out = {"premio": premio, "capitale": capitale}
    if "decrescent" in piano:
        m = re.search(r"capitale medio\D{0,20}(\d{1,3}(?:\.\d{3})+|\d+)", testi, re.I)
        if m:
            medio = _numero(m.group(1))
            out.update({"andamento": "decrescente", "capitale_medio": medio,
                        "come": "capitale medio scritto nel fascicolo"})
        elif "zero" in piano and ("quote annue costanti" in piano or "lineare" in piano or "costanti" in piano):
            out.update({"andamento": "decrescente", "capitale_medio": capitale / 2,
                        "come": "decrescenza in quote costanti fino a zero: medio = metà del capitale iniziale"})
        else:
            return {**out, "calcolabile": False, "andamento": "decrescente",
                    "motivo": "capitale decrescente ma piano di decrescenza non trovato"}
    elif "costante" in piano or "rivalut" not in piano:
        out.update({"andamento": "costante", "capitale_medio": capitale,
                    "come": "capitale costante: medio = capitale assicurato" if "costante" in piano
                    else "andamento non scritto: trattato come costante (da verificare)"})
    else:
        return {**out, "calcolabile": False, "andamento": "rivalutabile",
                "motivo": "capitale rivalutabile: serve il tasso di rivalutazione"}
    if not out.get("capitale_medio"):
        return {**out, "calcolabile": False, "motivo": "capitale medio nullo"}
    out["per_mille"] = round(premio / (out["capitale_medio"] / 1000.0), 2)
    out["calcolabile"] = True
    return out


class Ammessi:
    """I numeri che il giudizio puo' usare: quelli verificati, i calcoli del codice e le
    differenze fra i valori della stessa voce nelle due polizze. Le PERCENTUALI a parte:
    solo quelle scritte come percentuale nelle polizze, o calcolate dal codice."""

    def __init__(self) -> None:
        self.numeri: Set[str] = {"1", "2", "100", "1000"}      # «per 1.000 EUR di capitale medio»
        self.percentuali: Set[str] = set()

    def ok(self, testo: str) -> bool:
        return numeri(testo) <= self.numeri and percentuali(testo) <= self.percentuali

    def fuori(self, testo: str) -> List[str]:
        return sorted((numeri(testo) - self.numeri) | {p + "%" for p in percentuali(testo) - self.percentuali})


def _scarto_relativo(ammessi: Ammessi, x: float, y: float) -> None:
    for base in (x, y):
        if base:
            p = _forma(round(abs(x - y) / base * 100))
            ammessi.percentuali.add(p)
            ammessi.numeri.add(p)


def _numeri_ammessi(voci_a: List[Dict[str, Any]], voci_b: List[Dict[str, Any]],
                    base_a: Dict[str, Any], base_b: Dict[str, Any]) -> Ammessi:
    ammessi = Ammessi()
    per_chiave: Dict[str, Tuple[Set[float], Set[float]]] = {}
    for lato, voci in ((0, voci_a), (1, voci_b)):
        for v in voci:
            nums = numeri(v["valore"]) | numeri(v["citazione"])
            ammessi.numeri |= nums
            ammessi.percentuali |= percentuali(v["valore"]) | percentuali(v["citazione"])
            if v["chiave"] in VOCI_SINGOLE and not v["superata"]:
                # differenze solo fra le voci con UN valore per polizza (premio, carenza...):
                # fra le decine di numeri delle garanzie ogni differenza diventerebbe «vera»
                per_chiave.setdefault(v["chiave"], (set(), set()))[lato].update(float(n) for n in nums)
    for base in (base_a, base_b):
        for k in ("premio", "capitale", "capitale_medio", "per_mille"):
            if isinstance(base.get(k), (int, float)):
                ammessi.numeri |= numeri(f"{base[k]:.2f}".replace(".", ","))
                ammessi.numeri.add(_forma(float(base[k])))
    for a, b in per_chiave.values():
        for x in a:
            for y in b:
                ammessi.numeri.add(_forma(abs(x - y)))
                _scarto_relativo(ammessi, x, y)
    if base_a.get("per_mille") and base_b.get("per_mille"):
        d = abs(base_a["per_mille"] - base_b["per_mille"])
        ammessi.numeri |= numeri(f"{d:.2f}".replace(".", ","))
        _scarto_relativo(ammessi, base_a["per_mille"], base_b["per_mille"])
    return ammessi


# ------------------------------------------------------------------ giudizio (modello) + controllo
SYSTEM_GIUDIZIO = """Sei un perito assicurativo di parte (CTP). Hai due polizze, A ({nome_a}) e B ({nome_b}), gia' lette e VERIFICATE: ogni voce ha un id. Le voci "superata": true sono state sostituite da una deroga e NON valgono piu'; vale la deroga. Le voci con "modificata_da" valgono ancora, ma una deroga le cambia in parte o solo a una condizione ("condizionata": true): scrivi la condizione, non darla per acquisita.
Confronta voce per voce come un perito: chi copre di piu', chi esclude di piu', chi costa davvero di meno a parita' di prestazione.
Per il prezzo usa la BASE PREMIO calcolata (premio per 1.000 EUR di capitale medio): due premi su capitali diversi o con garanzie diverse incluse non si confrontano a occhio.
Rispondi SOLO con JSON:
{"righe": [{"voce": "nome della voce", "a": ["A-1"], "b": ["B-3"], "giudizio": "meglio_a|meglio_b|uguale|diverso|solo_a|solo_b", "perche": "una frase"}],
 "trappole": [{"testo": "trappola per l'assicurato o falso confronto, una o due frasi", "voci": ["A-2","B-5"]}],
 "azioni": [{"testo": "cosa deve fare o dire l'assicuratore al cliente", "voci": ["B-5"]}],
 "sintesi": "tre o quattro frasi per l'assicuratore"}
Regole:
1. Ogni voce verificata va in una riga. Raggruppa nella stessa riga le voci che parlano della stessa cosa (es. la stessa esclusione in A e in B).
2. Usa SOLO i numeri delle voci o della base premio, scritti come nelle voci. Non fare altri calcoli.
3. "solo_a"/"solo_b" quando la condizione c'e' in una polizza sola (o una sola la include nel premio).
4. Le azioni sono concrete: cosa chiedere al cliente, cosa fargli notare, cosa proporre.
5. Il verso del meglio e' quello dell'ASSICURATO: carenza, non contestabilita', limiti di tempo (es. atto volontario), franchigie, scoperti e caricamenti sono migliori quando sono PIU' BASSI; capitale, massimali e garanzie quando sono piu' alti. Un'esclusione presente in una sola polizza e' uno svantaggio di quella polizza.
6. Testo semplice: niente markdown."""


def _compatta(voci: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{k: v[k] for k in ("id", "chiave", "nome", "valore", "deroga", "condizionata", "superata",
                               "modificata_da", "inclusa")} for v in voci]


# il giudizio si divide per gruppi di voci, in parallelo: la stessa voce di A e di B sta
# sempre nello stesso gruppo, cosi' resta sulla stessa riga
GRUPPI_GIUDIZIO = [
    {"prodotto", "assicurato", "premio", "frazionamento", "durata", "capitale", "andamento_capitale",
     "prestazione", "costi", "sconti", "recesso", "riscatto", "revisione_premio"},
    {"carenza", "non_contestabilita", "limitazioni", "accertamenti", "franchigia", "massimale"},
    {"esclusione"},
    {"garanzia_complementare", "servizi", "altro"},
]


def giudica(voci_a: List[Dict[str, Any]], voci_b: List[Dict[str, Any]], base_a: Dict[str, Any],
            base_b: Dict[str, Any], nome_a: str, nome_b: str, avanza: Avanzamento = None) -> Dict[str, Any]:
    sistema = SYSTEM_GIUDIZIO.replace("{nome_a}", nome_a).replace("{nome_b}", nome_b)
    base = {"A": base_a, "B": base_b}
    compiti: List[Tuple[str, Set[str], str]] = []
    for gruppo in GRUPPI_GIUDIZIO:
        ga = [v for v in voci_a if v["chiave"] in gruppo]
        gb = [v for v in voci_b if v["chiave"] in gruppo]
        if ga or gb:
            ids = {v["id"] for v in ga + gb}
            utente = json.dumps({"compito": "Solo le righe di QUESTE voci: trappole, azioni e sintesi vuote.",
                                 "A": _compatta(ga), "B": _compatta(gb), "base_premio": base}, ensure_ascii=False)
            compiti.append(("righe", ids, utente))
    utente = json.dumps({"compito": "Solo trappole, azioni e sintesi su tutte le voci: righe vuote.",
                         "A": _compatta(voci_a), "B": _compatta(voci_b), "base_premio": base}, ensure_ascii=False)
    compiti.append(("sintesi", set(), utente))
    fatte = 0
    if avanza:
        avanza("giudizio", 0, len(compiti))
    risposte: List[Tuple[str, Set[str], Dict[str, Any]]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(MAX_PARALLELO, len(compiti))) as pool:
        futuri = {pool.submit(contextvars.copy_context().run, _chiedi_json, sistema, u, 5000): (tipo, ids)
                  for tipo, ids, u in compiti}
        for futuro in concurrent.futures.as_completed(futuri):
            tipo, ids = futuri[futuro]
            try:
                risposte.append((tipo, ids, futuro.result()))
            except RuntimeError:
                risposte.append((tipo, ids, {}))      # le voci di quel gruppo restano «non confrontate»
            fatte += 1
            if avanza:
                avanza("giudizio", fatte, len(compiti))
    unito: Dict[str, Any] = {"righe": [], "trappole": [], "azioni": [], "sintesi": ""}
    for tipo, ids, r in risposte:
        if tipo == "righe":
            for riga in r.get("righe") or []:
                if isinstance(riga, dict):
                    # una riga di un gruppo parla solo delle voci di quel gruppo
                    riga = dict(riga, a=[i for i in (riga.get("a") or []) if i in ids],
                                b=[i for i in (riga.get("b") or []) if i in ids])
                    unito["righe"].append(riga)
        else:
            unito.update({k: r.get(k) or unito[k] for k in ("trappole", "azioni", "sintesi")})
    return unito


# Per l'assicurato, su queste voci e' meglio il valore PIU' BASSO: una carenza corta, una
# non contestabilita' che scatta presto, un limite (es. atto volontario) breve, una franchigia
# piccola, caricamenti bassi. Il modello il 21ott2026 ha scritto «24 mesi contro 12: meglio A»
# sulla non contestabilita': e' il contrario. Qui il verso lo decide il codice.
MENO_E_MEGLIO = {"carenza", "non_contestabilita", "limitazioni", "franchigia", "costi"}
_QUANTITA = re.compile(r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)\s*(mesi|mese|giorni|giorno|gg|anni|anno|%)",
                       re.I)
_UNITA = {"mese": "mesi", "giorno": "giorni", "gg": "giorni", "anno": "anni"}


def _quantita(voci: List[Dict[str, Any]]) -> Optional[Tuple[float, str]]:
    """L'unica quantita' (numero + unita') delle voci base di una riga; None se zero o piu'."""
    trovate = set()
    for v in voci:
        if v["deroga"] or v["superata"] or v["chiave"] not in MENO_E_MEGLIO:
            continue
        for m in _QUANTITA.finditer(v["valore"]):
            n = _numero(m.group(1))
            if n is not None:
                unita = m.group(2).lower()
                trovate.add((n, _UNITA.get(unita, unita)))
    return next(iter(trovate)) if len(trovate) == 1 else None


def _forma_q(q: Tuple[float, str]) -> str:
    n = _forma(q[0]).replace(".", ",")
    return f"{n}%" if q[1] == "%" else f"{n} {q[1]}"


def _verso_del_meglio(righe: List[Dict[str, Any]], ids_a: Dict[str, Dict[str, Any]],
                      ids_b: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    corretti = []
    for r in righe:
        if r["giudizio"] not in ("meglio_a", "meglio_b", "uguale") or not r["a"] or not r["b"]:
            continue
        qa = _quantita([ids_a[i] for i in r["a"]])
        qb = _quantita([ids_b[i] for i in r["b"]])
        if not qa or not qb or qa[1] != qb[1]:
            continue
        giusto = "uguale" if qa[0] == qb[0] else ("meglio_a" if qa[0] < qb[0] else "meglio_b")
        if giusto == r["giudizio"]:
            continue
        corretti.append({"dove": "giudizio", "testo": f"{r['voce']}: il modello aveva scritto «{r['giudizio']}»",
                         "motivo": "giudizio corretto dal codice: per l'assicurato conta il valore più basso"})
        r["giudizio"] = giusto
        r["perche"] = (f"Per l'assicurato conta il valore più basso: A {_forma_q(qa)}, B {_forma_q(qb)}."
                       if giusto != "uguale" else f"Stesso valore: {_forma_q(qa)}.")
    return corretti


def _compresa_nel_premio(righe: List[Dict[str, Any]], ids_a: Dict[str, Dict[str, Any]],
                         ids_b: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Una garanzia che in una polizza e' opzionale non attivata (o non prevista) e nell'altra
    e' compresa nel premio non puo' essere «meglio» dove non c'e'. Prova dal vivo 21ott2026:
    inabilita' temporanea «Meglio A» con A opzionale non attivata e B compresa nei 156 EUR."""
    corretti = []
    for r in righe:
        if r["giudizio"] not in ("meglio_a", "meglio_b") or not r["a"] or not r["b"]:
            continue
        va = [ids_a[i] for i in r["a"] if ids_a[i]["chiave"] in ("garanzia_complementare", "servizi")]
        vb = [ids_b[i] for i in r["b"] if ids_b[i]["chiave"] in ("garanzia_complementare", "servizi")]
        if not va or not vb:
            continue
        dentro_a = any(v.get("inclusa") is True for v in va)
        fuori_a = all(v.get("inclusa") is False for v in va if v.get("inclusa") is not None) and \
            any(v.get("inclusa") is False for v in va)
        dentro_b = any(v.get("inclusa") is True for v in vb)
        fuori_b = all(v.get("inclusa") is False for v in vb if v.get("inclusa") is not None) and \
            any(v.get("inclusa") is False for v in vb)
        giusto = ""
        if r["giudizio"] == "meglio_a" and fuori_a and not dentro_a and dentro_b:
            giusto, chi, altra = "solo_b", "A", "B"
        elif r["giudizio"] == "meglio_b" and fuori_b and not dentro_b and dentro_a:
            giusto, chi, altra = "solo_a", "B", "A"
        if not giusto:
            continue
        corretti.append({"dove": "giudizio", "testo": f"{r['voce']}: il modello aveva scritto «{r['giudizio']}»",
                         "motivo": f"giudizio corretto dal codice: in {chi} la garanzia non è compresa nel premio"})
        r["giudizio"] = giusto
        r["perche"] = (f"In {chi} la garanzia non è compresa nel premio (opzionale o non prevista); "
                       f"in {altra} è compresa.")
    return corretti


def controlla_giudizio(grezzo: Dict[str, Any], voci_a: List[Dict[str, Any]], voci_b: List[Dict[str, Any]],
                       ammessi: Ammessi) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    ids_a = {v["id"]: v for v in voci_a}
    ids_b = {v["id"]: v for v in voci_b}
    scartate: List[Dict[str, Any]] = []
    righe: List[Dict[str, Any]] = []
    usati: Set[str] = set()
    for r in grezzo.get("righe") or []:
        if not isinstance(r, dict):
            continue
        a = [i for i in (r.get("a") or []) if i in ids_a]
        b = [i for i in (r.get("b") or []) if i in ids_b]
        if not a and not b:
            continue
        giudizio = str(r.get("giudizio") or "").strip().lower()
        if giudizio not in GIUDIZI:
            giudizio = "diverso"
        if a and not b:
            giudizio = "solo_a"
        elif b and not a:
            giudizio = "solo_b"
        elif giudizio in ("solo_a", "solo_b"):
            # una sola delle due la INCLUDE nel premio: va bene, purche' sia vero
            inc_a = any(ids_a[i].get("inclusa") is not False for i in a)
            inc_b = any(ids_b[i].get("inclusa") is not False for i in b)
            if (giudizio == "solo_a") != (inc_a and not inc_b):
                giudizio = "diverso"
        perche = str(r.get("perche") or "").strip()[:500]
        if perche and not ammessi.ok(perche):
            scartate.append({"dove": "giudizio", "testo": perche,
                             "motivo": "numeri non presenti nelle voci: " + ", ".join(ammessi.fuori(perche))})
            perche = ""
        usati.update(a + b)
        righe.append({"voce": str(r.get("voce") or "").strip()[:120] or ETICHETTE[(ids_a.get(a[0]) if a else ids_b[b[0]])["chiave"]],
                      "a": a, "b": b, "giudizio": giudizio, "perche": perche})
    # nessuna voce verificata sparisce: quelle che il modello non ha messo in una riga le
    # mette il codice, e la stessa voce di A e di B (stessa durata, stessa garanzia) va
    # sulla STESSA riga, una accanto all'altra
    orfane: Dict[str, Dict[str, Any]] = {}
    for v in voci_a + voci_b:
        if v["id"] in usati or v["superata"]:
            continue
        gruppo = v["chiave"] if v["chiave"] in VOCI_SINGOLE else v["chiave"] + ":" + _piano(v["nome"])
        riga = orfane.setdefault(gruppo, {"voce": v["nome"], "a": [], "b": [],
                                          "giudizio": "non_confrontata", "perche": ""})
        riga["a" if v["id"].startswith("A-") else "b"].append(v["id"])
    righe.extend(orfane.values())
    corretti = _verso_del_meglio(righe, ids_a, ids_b) + _compresa_nel_premio(righe, ids_a, ids_b)
    scartate.extend(corretti)
    ordine = {k: n for n, (k, _, _) in enumerate(VOCI)}

    def _pos(r: Dict[str, Any]) -> int:
        v = ids_a.get(r["a"][0]) if r["a"] else ids_b.get(r["b"][0])
        return ordine.get(v["chiave"], 99) if v else 99
    righe.sort(key=_pos)
    liste: Dict[str, List[Dict[str, Any]]] = {}
    for nome in ("trappole", "azioni"):
        liste[nome] = []
        for t in grezzo.get(nome) or []:
            if not isinstance(t, dict):
                continue
            testo = str(t.get("testo") or "").strip()[:700]
            voci = [i for i in (t.get("voci") or []) if i in ids_a or i in ids_b]
            if not testo:
                continue
            if not ammessi.ok(testo):
                scartate.append({"dove": nome, "testo": testo,
                                 "motivo": "numeri non presenti nelle voci: " + ", ".join(ammessi.fuori(testo))})
                continue
            liste[nome].append({"testo": testo, "voci": voci})
    sintesi = str(grezzo.get("sintesi") or "").strip()[:1500]
    if sintesi and not ammessi.ok(sintesi):
        scartate.append({"dove": "sintesi", "testo": sintesi,
                         "motivo": "numeri non presenti nelle voci: " + ", ".join(ammessi.fuori(sintesi))})
        sintesi = ""
    elif sintesi and corretti:
        # la sintesi e' stata scritta con un giudizio che il codice ha dovuto rovesciare:
        # puo' ripetere l'errore, quindi non si mostra (resta nei Controlli)
        scartate.append({"dove": "sintesi", "testo": sintesi,
                         "motivo": "scritta con un giudizio rovesciato poi corretto dal codice"})
        sintesi = ""
    return {"righe": righe, "trappole": liste["trappole"], "azioni": liste["azioni"], "sintesi": sintesi}, scartate


# ------------------------------------------------------------------ il quadro a colpo d'occhio
# Calcolato dal codice sulle voci VERIFICATE e sui giudizi controllati: l'assicuratore vede
# subito chi e' messo meglio, poi legge la relazione.
POSITIVE = {"capitale", "prestazione", "garanzia_complementare", "servizi", "sconti", "massimale"}
NEGATIVE = {"esclusione", "limitazioni", "revisione_premio", "franchigia"}


def vantaggio(riga: Dict[str, Any], voci: Dict[str, Dict[str, Any]]) -> str:
    """A chi conviene una riga: "a", "b" oppure "" (pari, diverso, non confrontata).
    «Solo B» su un'esclusione vuol dire che B esclude e A no: conviene ad A."""
    g = riga["giudizio"]
    if g in ("meglio_a", "meglio_b"):
        return g[-1]
    if g not in ("solo_a", "solo_b"):
        return ""
    ids = riga["a"] if g == "solo_a" else riga["b"]
    chiave = voci[ids[0]]["chiave"] if ids else ""
    if chiave in NEGATIVE:
        return "b" if g == "solo_a" else "a"
    if chiave in POSITIVE and any(voci[i].get("inclusa") is not False for i in ids):
        return g[-1]
    return ""


def _euro_it(v: Optional[float]) -> str:
    if v is None:
        return ""
    testo = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return testo[:-3] if testo.endswith(",00") and v >= 1000 else testo


def _chi_vince(a: Optional[float], b: Optional[float], piu_basso: bool) -> str:
    if a is None or b is None:
        return "nd"
    if a == b:
        return "pari"
    return ("a" if a < b else "b") if piu_basso else ("a" if a > b else "b")


def _riammette(lato: str, n: int) -> str:
    if not n:
        return ""
    return f"{lato}: 1 deroga che riammette" if n == 1 else f"{lato}: {n} deroghe che riammettono"


def quadro(conf: Dict[str, Any]) -> Dict[str, Any]:
    A, B = conf["a"], conf["b"]
    voci = {v["id"]: v for v in A["voci"] + B["voci"]}
    conta = {"a": 0, "b": 0, "pari": 0}
    for r in conf["righe"]:
        chi = vantaggio(r, voci)
        r["vantaggio"] = chi
        conta[chi or "pari"] += 1
    carte = []
    ba, bb = A["base_premio"], B["base_premio"]
    carte.append({"voce": "Prezzo per 1.000 € di capitale medio",
                  "a": _euro_it(ba.get("per_mille")) + " €" if ba.get("calcolabile") else "non calcolabile",
                  "b": _euro_it(bb.get("per_mille")) + " €" if bb.get("calcolabile") else "non calcolabile",
                  "meglio": _chi_vince(ba.get("per_mille") if ba.get("calcolabile") else None,
                                       bb.get("per_mille") if bb.get("calcolabile") else None, True),
                  "nota": f"premio annuo A {_euro_it(ba.get('premio'))} €, B {_euro_it(bb.get('premio'))} €"
                  if ba.get("premio") and bb.get("premio") else (ba.get("motivo") or bb.get("motivo") or "")})
    ca, cb = ba.get("capitale_medio"), bb.get("capitale_medio")
    carte.append({"voce": "Capitale medio assicurato", "a": _euro_it(ca) + " €" if ca else "non trovato",
                  "b": _euro_it(cb) + " €" if cb else "non trovato", "meglio": _chi_vince(ca, cb, False),
                  "nota": " · ".join(x for x in (f"A {ba['andamento']}" if ba.get("andamento") else "",
                                                 f"B {bb['andamento']}" if bb.get("andamento") else "") if x)})

    def _q(lato: Dict[str, Any], chiave: str) -> Optional[Tuple[float, str]]:
        return _quantita([v for v in lato["voci"] if v["chiave"] == chiave])

    def _deroghe_cond(lato: Dict[str, Any], chiave: str) -> str:
        return "; ".join(v["valore"] for v in lato["voci"] if v["chiave"] == chiave and v["deroga"])

    for chiave, titolo in (("carenza", "Carenza"), ("costi", "Caricamenti sul premio")):
        qa, qb = _q(A, chiave), _q(B, chiave)
        stessa = qa and qb and qa[1] == qb[1]
        nota = " · ".join(x for x in (f"A con deroga: {_deroghe_cond(A, chiave)}" if _deroghe_cond(A, chiave) else "",
                                       f"B con deroga: {_deroghe_cond(B, chiave)}" if _deroghe_cond(B, chiave) else "") if x)
        carte.append({"voce": titolo, "a": _forma_q(qa) if qa else "non trovato", "b": _forma_q(qb) if qb else "non trovato",
                      "meglio": _chi_vince(qa[0], qb[0], True) if stessa else "nd", "nota": nota})
    ea = [v for v in A["voci"] if v["chiave"] == "esclusione" and not v["deroga"]]
    eb = [v for v in B["voci"] if v["chiave"] == "esclusione" and not v["deroga"]]
    ra = sum(1 for v in A["voci"] if v["chiave"] == "esclusione" and v["deroga"])
    rb = sum(1 for v in B["voci"] if v["chiave"] == "esclusione" and v["deroga"])
    carte.insert(3, {"voce": "Esclusioni", "a": str(len(ea)), "b": str(len(eb)),
                     "meglio": _chi_vince(len(ea), len(eb), True),
                     "nota": " · ".join(x for x in (_riammette("A", ra), _riammette("B", rb)) if x)})
    ga = [v["nome"] for v in A["voci"] if v["chiave"] == "garanzia_complementare" and v.get("inclusa") is True]
    gb = [v["nome"] for v in B["voci"] if v["chiave"] == "garanzia_complementare" and v.get("inclusa") is True]
    carte.insert(4, {"voce": "Garanzie comprese nel premio", "a": str(len(ga)), "b": str(len(gb)),
                     "meglio": _chi_vince(len(ga), len(gb), False),
                     "nota": " · ".join(x for x in ("A: " + ", ".join(ga) if ga else "", "B: " + ", ".join(gb) if gb else "") if x)})
    return {"vince_a": conta["a"], "vince_b": conta["b"], "pari": conta["pari"], "carte": carte}


def non_trovate(voci: List[Dict[str, Any]], fasc: Dict[str, Any]) -> List[Dict[str, str]]:
    presenti = {v["chiave"] for v in voci}
    pagine = ", ".join(f"{p['documento']} {p['label']}" for p in fasc["pagine"]) or "nessuna pagina con testo"
    return [{"chiave": k, "voce": ETICHETTE[k], "testo": f"Non l'ho trovato. Ho cercato in: {pagine}."}
            for k in ESSENZIALI if k not in presenti]


# ------------------------------------------------------------------ il confronto intero
def confronta(documenti_a: List[Dict[str, Any]], documenti_b: List[Dict[str, Any]],
              nome_a: str = "", nome_b: str = "", avanza: Avanzamento = None) -> Dict[str, Any]:
    fa, fb = fascicolo("A", documenti_a), fascicolo("B", documenti_b)
    for f, nome in ((fa, "A"), (fb, "B")):
        if not f["pagine"]:
            raise HTTPException(422, f"La polizza {nome} non ha testo leggibile: forse è una scansione. "
                                     "Serve il PDF con il testo (o passato da un OCR).")
    # tutte le parti di A e di B in parallelo; ogni thread porta con se' il cliente a cui
    # contare i token
    letto_a, letto_b = leggi_lati([fa, fb], avanza)
    fa["senza_testo"] = fa["senza_testo"] + [p + " (non letta dal modello)" for p in letto_a["non_lette"]]
    fb["senza_testo"] = fb["senza_testo"] + [p + " (non letta dal modello)" for p in letto_b["non_lette"]]
    voci_a, scarti_a = verifica_lato(fa, letto_a["voci"])
    voci_b, scarti_b = verifica_lato(fb, letto_b["voci"])
    nome_a = (nome_a or letto_a.get("compagnia") or "Polizza A").strip()[:80]
    nome_b = (nome_b or letto_b.get("compagnia") or "Polizza B").strip()[:80]
    base_a, base_b = base_premio(voci_a), base_premio(voci_b)
    ammessi = _numeri_ammessi(voci_a, voci_b, base_a, base_b)
    giudizio, scarti_g = controlla_giudizio(giudica(voci_a, voci_b, base_a, base_b, nome_a, nome_b, avanza),
                                            voci_a, voci_b, ammessi)
    risultato = {"versione": 2, "creato": int(time.time()),
            "a": {"nome": nome_a, "ramo": letto_a.get("ramo"), "prodotto": letto_a.get("prodotto"),
                  "documenti": [{"id": d.get("id"), "titolo": d.get("titolo")} for d in documenti_a],
                  "voci": voci_a, "base_premio": base_a, "non_trovate": non_trovate(voci_a, fa),
                  "senza_testo": fa["senza_testo"], "pagine": len(fa["pagine"]),
                  "pagine_deroga": [p["id"] for p in fa["pagine"] if p["deroga"]]},
            "b": {"nome": nome_b, "ramo": letto_b.get("ramo"), "prodotto": letto_b.get("prodotto"),
                  "documenti": [{"id": d.get("id"), "titolo": d.get("titolo")} for d in documenti_b],
                  "voci": voci_b, "base_premio": base_b, "non_trovate": non_trovate(voci_b, fb),
                  "senza_testo": fb["senza_testo"], "pagine": len(fb["pagine"]),
                  "pagine_deroga": [p["id"] for p in fb["pagine"] if p["deroga"]]},
            **giudizio,
            "controlli": {"scartate": scarti_a + scarti_b + scarti_g,
                          "verificate": len(voci_a) + len(voci_b)}}
    risultato["quadro"] = quadro(risultato)
    return risultato


# ------------------------------------------------------------------ Excel
GIUDIZIO_TESTO = {"meglio_a": "Meglio A", "meglio_b": "Meglio B", "uguale": "Uguale", "diverso": "Diverso",
                  "solo_a": "Solo A", "solo_b": "Solo B", "non_confrontata": "Non confrontata"}


def _cella_voci(ids: List[str], voci: Dict[str, Dict[str, Any]]) -> Tuple[str, str]:
    valori, fonti = [], []
    for i in ids:
        v = voci[i]
        if v["deroga"]:
            segno = " [DEROGA, a condizione]" if v.get("condizionata") else " [DEROGA]"
        elif v["superata"]:
            segno = " [superato dalla deroga]"
        elif v.get("modificata_da"):
            segno = " [modificato dalla deroga " + ", ".join(v["modificata_da"]) + "]"
        else:
            segno = ""
        valori.append(f"{v['nome']}: {v['valore']}{segno}")
        fonti.append(f"{v['documento']} {v['label']}")
    return "\n".join(valori), "\n".join(dict.fromkeys(fonti))


def excel(confronto: Dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    font = Font(name="Arial", size=10)
    grassetto = Font(name="Arial", size=10, bold=True)
    titolo = Font(name="Arial", size=13, bold=True)
    testa = PatternFill("solid", fgColor="DDE4EE")
    colori = {"meglio_a": "D9EAD3", "meglio_b": "F4CCCC", "solo_a": "D9EAD3", "solo_b": "F4CCCC",
              "uguale": "EEEEEE", "diverso": "FFF2CC", "non_confrontata": "FFFFFF"}
    a, b = confronto["a"], confronto["b"]
    voci = {v["id"]: v for v in a["voci"] + b["voci"]}
    a_capo = Alignment(wrap_text=True, vertical="top")

    # primo foglio: il quadro a colpo d'occhio
    q = confronto.get("quadro") or quadro(confronto)
    wq = wb.active
    wq.title = "Quadro"
    wq["A1"] = f"A = {a['nome']}   ·   B = {b['nome']}"
    wq["A1"].font = titolo
    wq["A3"] = "Voci a favore dell'assicurato"
    wq["A3"].font = grassetto
    for c, (et, n, col) in enumerate(((f"A · {a['nome']}", q["vince_a"], "D9EAD3"),
                                      (f"B · {b['nome']}", q["vince_b"], "F4CCCC"),
                                      ("pari o diverse", q["pari"], "EEEEEE")), 2):
        wq.cell(row=3, column=c, value=f"{et}: {n}").font = grassetto
        wq.cell(row=3, column=c).fill = PatternFill("solid", fgColor=col)
    for c, t in enumerate(["Voce decisiva", f"A · {a['nome']}", f"B · {b['nome']}", "Meglio", "Nota"], 1):
        cella = wq.cell(row=5, column=c, value=t)
        cella.font, cella.fill = grassetto, testa
    meglio_testo = {"a": "A", "b": "B", "pari": "Pari", "nd": "Non confrontabile"}
    for n, carta in enumerate(q["carte"], 6):
        for c, t in enumerate([carta["voce"], carta["a"], carta["b"], meglio_testo.get(carta["meglio"], ""),
                               carta["nota"]], 1):
            cella = wq.cell(row=n, column=c, value=t)
            cella.font, cella.alignment = font, a_capo
        colore = {"a": "D9EAD3", "b": "F4CCCC"}.get(carta["meglio"])
        if colore:
            wq.cell(row=n, column=2 if carta["meglio"] == "a" else 3).fill = PatternFill("solid", fgColor=colore)
            wq.cell(row=n, column=4).fill = PatternFill("solid", fgColor=colore)
    for col, larg in zip("ABCDE", (36, 30, 30, 18, 60)):
        wq.column_dimensions[col].width = larg

    ws = wb.create_sheet("Confronto")
    ws["A1"] = f"Confronto polizze: A = {a['nome']}  ·  B = {b['nome']}"
    ws["A1"].font = titolo
    ws["A2"] = ("Ogni valore è verificato sulla citazione del documento (foglio Dettaglio). "
                "Verde = meglio A, rosso = meglio B, giallo = diverso.")
    ws["A2"].font = font
    intest = ["Voce", "A - valore", "A - fonte", "B - valore", "B - fonte", "Giudizio", "Perché"]
    for c, t in enumerate(intest, 1):
        cella = ws.cell(row=4, column=c, value=t)
        cella.font, cella.fill = grassetto, testa
    riga = 5
    for r in confronto["righe"]:
        va, fa = _cella_voci(r["a"], voci)
        vb, fb = _cella_voci(r["b"], voci)
        valori = [r["voce"], va, fa, vb, fb, GIUDIZIO_TESTO.get(r["giudizio"], r["giudizio"]), r["perche"]]
        for c, t in enumerate(valori, 1):
            cella = ws.cell(row=riga, column=c, value=t)
            cella.font, cella.alignment = font, a_capo
        ws.cell(row=riga, column=6).fill = PatternFill("solid", fgColor=colori.get(r["giudizio"], "FFFFFF"))
        riga += 1
    for lato in (a, b):
        for n in lato["non_trovate"]:
            for c, t in enumerate([n["voce"], n["testo"] if lato is a else "", "", n["testo"] if lato is b else "",
                                   "", "Non trovato", ""], 1):
                cella = ws.cell(row=riga, column=c, value=t)
                cella.font, cella.alignment = font, a_capo
            riga += 1
    for col, larg in zip("ABCDEFG", (26, 42, 22, 42, 22, 14, 50)):
        ws.column_dimensions[col].width = larg
    ws.freeze_panes = "B5"

    # base premio: i dati sono celle, il conto e' una formula
    wp = wb.create_sheet("Base premio")
    wp["A1"] = "Premio sulla stessa base: premio annuo per 1.000 EUR di capitale medio"
    wp["A1"].font = titolo
    for c, t in enumerate(["", "A", "B"], 1):
        wp.cell(row=3, column=c, value=t).font = grassetto
    etichette = [("Premio annuo (EUR)", "premio"), ("Capitale iniziale (EUR)", "capitale"),
                 ("Capitale medio (EUR)", "capitale_medio")]
    for n, (et, k) in enumerate(etichette, 4):
        wp.cell(row=n, column=1, value=et).font = font
        for c, base in ((2, a["base_premio"]), (3, b["base_premio"])):
            cella = wp.cell(row=n, column=c, value=base.get(k))
            cella.font = Font(name="Arial", size=10, color="0000FF")
            cella.number_format = "#,##0.00"
    wp.cell(row=7, column=1, value="Premio per 1.000 EUR di capitale medio").font = grassetto
    for c, col in ((2, "B"), (3, "C")):
        cella = wp.cell(row=7, column=c, value=f'=IF(AND(ISNUMBER({col}4),ISNUMBER({col}6),{col}6>0),{col}4/({col}6/1000),"n.d.")')
        cella.font, cella.number_format = grassetto, "#,##0.00"
    wp.cell(row=9, column=1, value="Come è stato ricavato il capitale medio").font = grassetto
    for c, base in ((2, a["base_premio"]), (3, b["base_premio"])):
        testo = base.get("come") or base.get("motivo") or ""
        wp.cell(row=9, column=c, value=testo).font = font
        wp.cell(row=9, column=c).alignment = a_capo
    wp.cell(row=11, column=1, value="Fonte dei numeri: le voci verificate del foglio Dettaglio (in blu i dati inseriti).").font = font
    for col, larg in zip("ABC", (42, 30, 30)):
        wp.column_dimensions[col].width = larg

    wt = wb.create_sheet("Trappole e azioni")
    wt["A1"] = "Sintesi"
    wt["A1"].font = titolo
    wt["A2"] = confronto.get("sintesi") or ""
    wt["A2"].font, wt["A2"].alignment = font, a_capo
    riga = 4
    for nome, lista in (("Trappole per l'assicurato", confronto["trappole"]), ("Azioni da fare", confronto["azioni"])):
        wt.cell(row=riga, column=1, value=nome).font = titolo
        riga += 1
        for t in lista:
            cella = wt.cell(row=riga, column=1, value=t["testo"])
            cella.font, cella.alignment = font, a_capo
            fonti = ", ".join(f"{voci[i]['documento']} {voci[i]['label']}" for i in t["voci"] if i in voci)
            wt.cell(row=riga, column=2, value=fonti).font = font
            riga += 1
        riga += 1
    wt.column_dimensions["A"].width = 100
    wt.column_dimensions["B"].width = 40

    for lato, nome in ((a, "Dettaglio A"), (b, "Dettaglio B")):
        wd = wb.create_sheet(nome)
        intest = ["Id", "Voce", "Nome", "Valore", "Deroga", "Superata / modificata", "Inclusa", "Documento",
                  "Pagina", "Citazione"]
        for c, t in enumerate(intest, 1):
            cella = wd.cell(row=1, column=c, value=t)
            cella.font, cella.fill = grassetto, testa
        for n, v in enumerate(lato["voci"], 2):
            inc = {True: "sì", False: "no", None: ""}[v.get("inclusa")]
            valori = [v["id"], ETICHETTE.get(v["chiave"], v["chiave"]), v["nome"], v["valore"],
                      ("sì, a condizione" if v.get("condizionata") else "sì") if v["deroga"] else "",
                      "superata" if v["superata"] else ("modificata da " + ", ".join(v["modificata_da"])
                                                        if v.get("modificata_da") else ""), inc,
                      v["documento"], v["label"], v["citazione"]]
            for c, t in enumerate(valori, 1):
                cella = wd.cell(row=n, column=c, value=t)
                cella.font, cella.alignment = font, a_capo
        for col, larg in zip("ABCDEFGHIJ", (7, 22, 28, 34, 8, 9, 8, 24, 12, 70)):
            wd.column_dimensions[col].width = larg
        wd.freeze_panes = "A2"

    wc = wb.create_sheet("Controlli")
    wc["A1"] = "Cosa il controllo ha tolto, e perché"
    wc["A1"].font = titolo
    wc["A2"] = (f"Voci verificate: {confronto['controlli']['verificate']}. Le righe qui sotto NON sono "
                "entrate nel confronto perché la citazione o i numeri non tornavano con il testo.")
    wc["A2"].font = font
    for c, t in enumerate(["Dove", "Testo", "Motivo"], 1):
        cella = wc.cell(row=4, column=c, value=t)
        cella.font, cella.fill = grassetto, testa
    riga = 5
    for s in confronto["controlli"]["scartate"]:
        dove = s.get("dove") or f"lettura {s.get('lato', '')}"
        testo = s.get("testo") or f"{s.get('nome', '')}: {s.get('valore', '')} — «{s.get('citazione', '')}»"
        for c, t in enumerate([dove, testo, s.get("motivo", "")], 1):
            cella = wc.cell(row=riga, column=c, value=t)
            cella.font, cella.alignment = font, a_capo
        riga += 1
    for lato in (a, b):
        for p in lato["senza_testo"]:
            for c, t in enumerate(["pagine", p, "pagina senza testo leggibile: forse una scansione"], 1):
                wc.cell(row=riga, column=c, value=t).font = font
            riga += 1
    wc.column_dimensions["A"].width = 16
    wc.column_dimensions["B"].width = 90
    wc.column_dimensions["C"].width = 45
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ archivio dei confronti
def _cartella(cliente: str) -> str:
    return os.path.join(docs.client_dir(cliente), "confronti")


def _id_valido(cid: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{12}", cid or ""):
        raise HTTPException(404, "Confronto non trovato")
    return cid


def salva(cliente: str, confronto: Dict[str, Any]) -> str:
    cid = uuid.uuid4().hex[:12]
    confronto["id"] = cid
    os.makedirs(_cartella(cliente), exist_ok=True)
    tmp = os.path.join(_cartella(cliente), cid + ".tmp")
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(confronto, stream, ensure_ascii=False)
    os.replace(tmp, os.path.join(_cartella(cliente), cid + ".json"))
    return cid


def leggi(cliente: str, cid: str) -> Dict[str, Any]:
    path = os.path.join(_cartella(cliente), _id_valido(cid) + ".json")
    if not os.path.isfile(path):
        raise HTTPException(404, "Confronto non trovato")
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def elenco(cliente: str, limite: int = 30) -> List[Dict[str, Any]]:
    cartella = _cartella(cliente)
    if not os.path.isdir(cartella):
        return []
    out = []
    for nome in os.listdir(cartella):
        if not nome.endswith(".json"):
            continue
        try:
            with open(os.path.join(cartella, nome), encoding="utf-8") as stream:
                c = json.load(stream)
            out.append({"id": c.get("id"), "creato": c.get("creato"), "a": c["a"]["nome"], "b": c["b"]["nome"]})
        except (OSError, ValueError, KeyError):
            continue
    return sorted(out, key=lambda x: -(x.get("creato") or 0))[:limite]


VERSIONE_LETTURA = 1     # da aumentare ogni volta che cambia pagine_pdf()


def _cella(valore: Any) -> str:
    return re.sub(r"\s+", " ", str(valore or "")).strip()


def pagine_pdf(path: str) -> List[Dict[str, Any]]:
    """Il PDF letto come lo legge LAGO (document_indexer.py): pdfplumber, che conserva le
    RIGHE delle tabelle. Ogni riga di tabella diventa una riga di testo con il nome della
    sua colonna accanto a ogni valore («Garanzia: Invalidita' permanente | Prestazione:
    ... 60% | Premio: inclusa»): il legame riga-colonna lo fa il PDF, non il modello."""
    import pdfplumber
    out: List[Dict[str, Any]] = []
    with pdfplumber.open(path) as pdf:
        for numero, pagina in enumerate(pdf.pages, 1):
            testo = (pagina.extract_text() or "").strip()
            righe: List[str] = []
            for tabella in pagina.extract_tables() or []:
                tabella = [[_cella(c) for c in riga] for riga in tabella if riga and any(riga)]
                if len(tabella) < 2:
                    continue
                testa = tabella[0]
                for riga in tabella[1:]:
                    celle = []
                    for i, valore in enumerate(riga):
                        nome = testa[i] if i < len(testa) and testa[i] else ""
                        if valore:
                            celle.append(f"{nome}: {valore}" if nome else valore)
                    if celle:
                        righe.append(" | ".join(celle))
            if righe:
                testo += "\n[RIGHE DELLE TABELLE]\n" + "\n".join(righe)
            if testo.strip():
                out.append({"label": f"pagina {numero}", "text": testo})
    return out


def _pagine_del_documento(item: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """Per un PDF: le pagine con le righe di tabella (calcolate una volta, poi tenute
    accanto al file). Se pdfplumber non c'e' o il PDF non si apre: None, e si usa il testo
    dei Documenti come prima."""
    sorgente = item.get("source_path") or ""
    if not sorgente.lower().endswith(".pdf") or not os.path.isfile(sorgente):
        return None
    # un documento caricato non cambia mai (un nuovo caricamento e' una cartella nuova);
    # cambia invece il MODO di leggere: una lettura fatta con una versione vecchia si rifa'
    cache = os.path.join(os.path.dirname(sorgente), "pagine_tabelle.json")
    try:
        if os.path.isfile(cache):
            with open(cache, encoding="utf-8") as stream:
                salvata = json.load(stream)
            if isinstance(salvata, dict) and salvata.get("versione") == VERSIONE_LETTURA:
                return salvata["pagine"]
        pagine = pagine_pdf(sorgente)
        tmp = cache + ".tmp"
        with open(tmp, "w", encoding="utf-8") as stream:
            json.dump({"versione": VERSIONE_LETTURA, "pagine": pagine}, stream, ensure_ascii=False)
        os.replace(tmp, cache)
        return pagine
    except Exception as error:
        print(f"[CONFRONTO] righe di tabella non lette ({type(error).__name__}): uso il testo semplice")
        return None


def documenti_scelti(cliente: str, ids: Iterable[str]) -> List[Dict[str, Any]]:
    """I documenti di QUESTO archivio, con il loro testo. Un id di un altro archivio non
    esiste qui: non si legge mai il documento di un altro cliente."""
    manifest = {i.get("id"): i for i in docs.read_manifest(cliente)}
    out = []
    for did in ids:
        item = manifest.get(str(did))
        if not item or item.get("stato") not in STATI_AMMESSI:
            raise HTTPException(404, "Documento non trovato in questo archivio")
        if item.get("media_path"):
            raise HTTPException(400, f"«{item.get('titolo') or item.get('name')}» è un file multimediale, non un testo")
        chunks = _pagine_del_documento(item)
        if not chunks:
            try:
                with open(item.get("extracted_path") or "", encoding="utf-8") as stream:
                    chunks = json.load(stream)
            except (OSError, ValueError):
                raise HTTPException(422, f"«{item.get('titolo') or item.get('name')}» non ha testo leggibile")
        out.append({"id": item["id"], "titolo": item.get("titolo") or item.get("name") or "",
                    "chunks": chunks if isinstance(chunks, list) else []})
    return out


# ------------------------------------------------------------------ rotte
def _lista_ids(valore: Any) -> List[str]:
    if not isinstance(valore, list):
        raise HTTPException(400, "Scegli i documenti di ciascuna polizza")
    ids = list(dict.fromkeys(str(x) for x in valore if str(x).strip()))
    if not ids or len(ids) > MAX_DOCUMENTI_LATO:
        raise HTTPException(400, f"Ogni polizza: da 1 a {MAX_DOCUMENTI_LATO} documenti")
    return ids


@router.post("/{tenant}")
async def nuovo_confronto(tenant: str, dati: Dict[str, Any] = Body(...),
                          x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    spazio = str(dati.get("spazio") or "azienda")
    _, cliente = docs._verified_client(tenant, x_applaudi_tenant_key, spazio)
    ids_a, ids_b = _lista_ids(dati.get("a")), _lista_ids(dati.get("b"))
    if set(ids_a) & set(ids_b):
        raise HTTPException(400, "Lo stesso documento non può stare in tutte e due le polizze")
    if not credito_ok(cliente):
        raise HTTPException(402, "Credito esaurito.")
    in_corso = lavoro_in_corso(cliente)
    if in_corso:                      # un doppio clic non paga due confronti
        raise HTTPException(409, "C'è già un confronto in corso: aspetta che finisca.")
    doc_a, doc_b = documenti_scelti(cliente, ids_a), documenti_scelti(cliente, ids_b)
    lid = avvia(cliente, doc_a, doc_b, str(dati.get("nome_a") or ""), str(dati.get("nome_b") or ""))
    return {"lavoro": lid, **stato_lavoro(cliente, lid)}


# ------------------------------------------------------------------ il lavoro in sottofondo
# Il confronto di un fascicolo vero dura minuti: la richiesta del browser non puo' restare
# appesa (prova dal vivo 21ott2026: Render l'ha tagliata). Si avvia, si risponde subito con
# il numero del lavoro, e la pagina chiede a che punto e'. Lo stato sta su disco: lo legge
# qualunque processo del server, e un risultato finito con la pagina chiusa non si perde.
LAVORO_FERMO_S = 8 * 60      # nessun avanzamento da tanto: il processo e' morto (riavvio, deploy)


def _lavoro_path(cliente: str, lid: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{12}", lid or ""):
        raise HTTPException(404, "Lavoro non trovato")
    return os.path.join(_cartella(cliente), "lavori", lid + ".json")


def _scrivi_lavoro(cliente: str, lid: str, stato: Dict[str, Any]) -> None:
    path = _lavoro_path(cliente, lid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    stato["aggiornato"] = time.time()
    tmp = path + f".{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(stato, stream, ensure_ascii=False)
    os.replace(tmp, path)


def stato_lavoro(cliente: str, lid: str) -> Dict[str, Any]:
    path = _lavoro_path(cliente, lid)
    try:
        with open(path, encoding="utf-8") as stream:
            stato = json.load(stream)
    except (OSError, ValueError):
        raise HTTPException(404, "Lavoro non trovato")
    ultimo = float(stato.get("aggiornato") or stato.get("inizio") or 0)
    if stato.get("stato") == "in_corso" and time.time() - ultimo > LAVORO_FERMO_S:
        stato.update({"stato": "errore", "errore": "Il confronto si è interrotto (server riavviato?): rilancialo."})
    return stato


def lavoro_in_corso(cliente: str) -> Optional[str]:
    cartella = os.path.join(_cartella(cliente), "lavori")
    if not os.path.isdir(cartella):
        return None
    for nome in os.listdir(cartella):
        if nome.endswith(".json"):
            try:
                if stato_lavoro(cliente, nome[:-5]).get("stato") == "in_corso":
                    return nome[:-5]
            except HTTPException:
                continue
    return None


FASI = {"lettura": "Leggo le polizze", "giudizio": "Confronto voce per voce"}


def avvia(cliente: str, doc_a: List[Dict[str, Any]], doc_b: List[Dict[str, Any]], nome_a: str, nome_b: str) -> str:
    lid = uuid.uuid4().hex[:12]
    stato: Dict[str, Any] = {"stato": "in_corso", "inizio": time.time(), "fase": "Leggo le polizze",
                             "fatte": 0, "totale": 0, "confronto": None, "errore": None}
    _scrivi_lavoro(cliente, lid, stato)
    blocco = threading.Lock()

    def avanza(fase: str, fatte: int, totale: int) -> None:
        with blocco:
            stato.update({"fase": FASI.get(fase, fase), "fatte": fatte, "totale": totale})
            _scrivi_lavoro(cliente, lid, stato)

    def lavoro() -> None:
        gettone = docs.consumo_cliente.set(cliente)
        try:
            risultato = confronta(doc_a, doc_b, nome_a, nome_b, avanza)
            cid = salva(cliente, risultato)
            docs.annota(cliente, "confronto", nota=f"{risultato['a']['nome']} / {risultato['b']['nome']}")
            with blocco:
                stato.update({"stato": "fatto", "confronto": cid, "fase": "Fatto"})
        except HTTPException as error:
            with blocco:
                stato.update({"stato": "errore", "errore": str(error.detail)})
        except Exception as error:
            print(f"[CONFRONTO] errore {type(error).__name__}: {error}")
            with blocco:
                stato.update({"stato": "errore", "errore": "Il confronto non è riuscito: riprova tra poco."})
        finally:
            docs.consumo_cliente.reset(gettone)
            with blocco:
                stato["durata_s"] = round(time.time() - stato["inizio"])
                _scrivi_lavoro(cliente, lid, stato)

    threading.Thread(target=contextvars.copy_context().run, args=(lavoro,), daemon=True,
                     name=f"confronto-{lid}").start()
    return lid


@router.get("/{tenant}/lavori/{lid}")
async def a_che_punto(tenant: str, lid: str, spazio: str = Query("azienda", pattern="^(azienda|privato)$"),
                      x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    _, cliente = docs._verified_client(tenant, x_applaudi_tenant_key, spazio)
    return stato_lavoro(cliente, lid)


@router.get("/{tenant}")
async def elenco_confronti(tenant: str, spazio: str = Query("azienda", pattern="^(azienda|privato)$"),
                           x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    _, cliente = docs._verified_client(tenant, x_applaudi_tenant_key, spazio)
    return {"confronti": elenco(cliente)}


@router.get("/{tenant}/{cid}")
async def un_confronto(tenant: str, cid: str, spazio: str = Query("azienda", pattern="^(azienda|privato)$"),
                       x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    _, cliente = docs._verified_client(tenant, x_applaudi_tenant_key, spazio)
    return leggi(cliente, cid)


@router.get("/{tenant}/{cid}/excel")
async def confronto_excel(tenant: str, cid: str, spazio: str = Query("azienda", pattern="^(azienda|privato)$"),
                          x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    _, cliente = docs._verified_client(tenant, x_applaudi_tenant_key, spazio)
    dati = await run_in_threadpool(excel, leggi(cliente, cid))
    nome = re.sub(r"[^A-Za-z0-9_-]+", "_", f"Confronto_{cid}")
    return Response(dati, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{nome}.xlsx"'})
