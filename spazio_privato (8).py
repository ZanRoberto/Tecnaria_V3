"""A'PLAUD'I privato: lo spazio personale del titolare e il suo accesso da WhatsApp.

Regole (tutte decise dal server, mai dal telefono o dal browser):
1. Lo spazio privato e' di chi possiede la chiave del tenant: il titolare. E' un archivio
   suo, separato da quello dell'azienda (documenti_aziendali.cliente_privato), con lo
   stesso formato: scheda, validita', registro. Ogni domanda va in UN solo spazio: una
   risposta dell'azienda non vede mai lo spazio privato, e viceversa.
2. Un numero WhatsApp si collega allo spazio privato solo con un codice mostrato nella
   dashboard (che richiede la chiave) e scritto da quel telefono entro 10 minuti: e' la
   prova di avere insieme la chiave e il telefono.
3. PIN di 6 cifre scelto dal titolare nella dashboard. Su WhatsApp non si chiede mai
   intero: 3 cifre in posizioni a caso, cosi' nella chat non resta il PIN completo.
   Vale 30 minuti. Dopo 3 errori lo spazio si blocca e si sblocca solo dalla dashboard.
4. Il PIN non si conserva in chiaro: per ognuna delle 20 terne di posizioni si conserva
   un'impronta scrypt con sale. Limite dichiarato: chi ha accesso ai file del server e
   tempo puo' ricavarlo per tentativi (sono 6 cifre). Il PIN protegge dal telefono in
   mano ad altri, non dal gestore del server: per quello serve la cifratura della
   cassaforte con la chiave del titolare (consegna successiva).
5. Lo spazio di partenza e' il privato. Si cambia scrivendo «privato» o il nome
   dell'azienda; dopo 8 ore di silenzio si torna al privato.
6. DISTRUGGI TUTTO (20ott2026): il proprietario puo' svuotare all'istante l'intero spazio
   privato (documenti, foto, video, registrazioni, indici, registro delle risposte) dalla
   dashboard (PIN intero + scritta DISTRUGGI) o da WhatsApp («distruggi», poi il PIN intero
   entro 2 minuti). Non resta traccia, nemmeno nel log del server. Restano solo PIN e
   telefoni collegati, per poter continuare a usare lo spazio. Fuori portata: la chat
   WhatsApp sul telefono e gli originali dentro l'app PLAUD.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import itertools
import json
import os
import re
import secrets
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Body, Header, HTTPException
from starlette.concurrency import run_in_threadpool

import documenti_aziendali as docs

router = APIRouter(prefix="/api/spazio", tags=["Spazio privato"])

# ------------------------------------------------------------------ collegamenti
# Impostati da app.py all'avvio (stesso schema degli altri moduli).
tenant_verifier: Optional[Callable[[str, Optional[str]], str]] = None
scrivi: Optional[Callable[[str, str, int, float], str]] = None          # il modello che scrive
tokenizer: Optional[Callable[[str], Iterable[str]]] = None
nome_azienda: Callable[[str], str] = lambda cliente: (cliente or "").upper()
e_catalogo: Callable[[str], bool] = lambda cliente: True      # l'archivio ha un catalogo accanto?
credito_ok: Callable[[str], bool] = lambda cliente: True      # credito del cliente (console del gestore)
MSG_CREDITO = "Credito esaurito."

PIN_CIFRE = 6
SFIDA_CIFRE = 3
SBLOCCO_S = 30 * 60
SFIDA_S = 5 * 60
MAX_ERRORI = 3
RITORNO_PRIVATO_S = 8 * 3600
CODICE_S = 10 * 60
CODICE_MAX_TENTATIVI = 5
DISTRUGGI_S = 2 * 60
PAROLA_DISTRUGGI = "DISTRUGGI"
EVIDENZE_PRIVATE = 8
_TERNE = list(itertools.combinations(range(PIN_CIFRE), SFIDA_CIFRE))   # 20 terne
_LOCK = threading.RLock()
_PAGINE: Dict[str, List[Dict[str, Any]]] = {}
SEGNO_PRIVATO = "·privato"          # "azienda" nel registro WhatsApp di un numero solo privato

# ------------------------------------------------------------------ messaggi WhatsApp
INTESTAZIONE = "🔒 *Privato*\n\n"
MSG_SENZA_PIN = ("🔒 Il tuo spazio privato non ha ancora un PIN. Impostalo dalla dashboard, "
                 "pagina *Sicurezza*, poi riscrivi la domanda.")
MSG_BLOCCATO = ("⛔ Spazio privato bloccato dopo 3 tentativi sbagliati. Si sblocca solo dalla "
                "dashboard, pagina *Sicurezza*.")
MSG_APERTO = "🔓 Spazio privato aperto per 30 minuti. Fai pure la tua domanda."
MSG_CHIUSO = "🔒 Spazio privato chiuso. Alla prossima domanda ti chiederò di nuovo il PIN."
MSG_PIN_INTERO = ("Non scrivere il PIN intero: in chat resterebbe leggibile. Scrivi solo le "
                  "3 cifre richieste.")
MSG_CODICE_ERRATO = ("Codice non valido o scaduto. Generane uno nuovo dalla dashboard, pagina "
                     "*Sicurezza*.")
MSG_VUOTO = ("Il tuo spazio privato è ancora vuoto: aggiungi registrazioni PLAUD o documenti "
             "dalla dashboard.")
MSG_NON_TROVATO = "Non l'ho trovato nei tuoi documenti."
MSG_DISTRUGGI_CHIEDI = ("⚠️ *Distruzione dello spazio privato*\nSparirà TUTTO, senza possibilità di "
                       "recupero: documenti, foto, video, registrazioni e risposte salvate.\n"
                       "Per confermare scrivi il tuo PIN *intero* (6 cifre) entro 2 minuti.\n"
                       "Per annullare scrivi *annulla*.")
MSG_DISTRUTTO = ("🗑️ Spazio privato distrutto: non resta nulla sul server. Cancella anche questa "
                 "chat dal telefono e, se serve, gli originali nell'app PLAUD.")
MSG_DISTRUGGI_ANNULLATO = "Distruzione annullata: non è stato toccato nulla."
_ORDINALI = ("1ª", "2ª", "3ª", "4ª", "5ª", "6ª")
_COLLEGA = re.compile(r"^\s*collega\s+(\d{6})\s*$", re.I)
_TRE_CIFRE = re.compile(r"^\D{0,3}(\d)\D{0,3}(\d)\D{0,3}(\d)\D{0,3}$")
_SOLO_CIFRE = re.compile(r"^[\d\s.,-]+$")


def msg_sfida(terna: List[int]) -> str:
    a, b, c = (_ORDINALI[i] for i in terna)
    schema = "".join("⬜" if i in terna else "⬛" for i in range(PIN_CIFRE))
    return (f"🔒 *Spazio privato*\nPer aprirlo scrivi la {a}, la {b} e la {c} cifra del tuo "
            f"PIN: solo quelle 3 cifre, una dopo l'altra.\n{schema}\n"
            "⬜ = le cifre da scrivere, da sinistra a destra.")


def pulisci(testo: str, canale: str) -> str:
    """Il modello a volte scrive in markdown. Sul web gli asterischi si vedrebbero come
    tali; su WhatsApp il grassetto e' un asterisco solo."""
    testo = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", testo or "")
    if canale == "whatsapp":
        return re.sub(r"\*\*(.+?)\*\*", r"*\1*", testo, flags=re.S)
    return re.sub(r"\*\*(.+?)\*\*", r"\1", testo, flags=re.S).replace("**", "")


# ------------------------------------------------------------------ archivio della sicurezza
def impronta_telefono(sender: str) -> str:
    chiave = os.getenv("WHATSAPP_APP_SECRET", "").encode()
    return hmac.new(chiave, ("spazio:" + str(sender)).encode(), hashlib.sha256).hexdigest()[:32]


def _percorso(tenant: str) -> str:
    return os.path.join(docs.client_dir(docs.cliente_privato(tenant)), "sicurezza.json")


def _leggi(tenant: str) -> Dict[str, Any]:
    try:
        with open(_percorso(tenant), encoding="utf-8") as stream:
            dati = json.load(stream)
    except (OSError, ValueError):
        dati = {}
    if not isinstance(dati, dict):
        dati = {}
    dati["tenant"] = tenant
    for chiave in ("telefoni", "sessioni"):
        if not isinstance(dati.get(chiave), dict):
            dati[chiave] = {}
    dati.setdefault("errori", 0)
    dati.setdefault("bloccato", False)
    return dati


def _salva(tenant: str, dati: Dict[str, Any]) -> None:
    docs._write_json_atomic(_percorso(tenant), dati)


def _titolari() -> List[str]:
    """I tenant che hanno uno spazio privato con una configurazione di sicurezza."""
    radice = docs.upload_root()
    out = []
    try:
        nomi = os.listdir(radice)
    except OSError:
        return []
    for nome in nomi:
        if not docs.e_privato(nome):
            continue
        try:
            with open(os.path.join(radice, nome, "sicurezza.json"), encoding="utf-8") as stream:
                tenant = json.load(stream).get("tenant")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(tenant, str) and docs.cliente_privato(tenant) == nome:
            out.append(tenant)
    return sorted(out)


def abilitato(tenant: str) -> bool:
    """Lo spazio privato esiste solo se il gestore lo ha abilitato (modalita' privato o mix)."""
    return "privato" in docs.spazi_ammessi(tenant)


def proprietario(sender: str) -> Optional[str]:
    """Il titolare il cui spazio privato (abilitato) e' collegato a questo numero, o None."""
    h = impronta_telefono(sender)
    with _LOCK:
        for tenant in _titolari():
            if abilitato(tenant) and h in _leggi(tenant)["telefoni"]:
                return tenant
    return None


def accesso_consentito(sender: str, testo: str) -> bool:
    """Per il gateway: un numero NON associato a un'azienda puo' scrivere solo se e'
    collegato a uno spazio privato, o se sta inviando il codice di collegamento."""
    return bool(_COLLEGA.match(testo or "")) or proprietario(sender) is not None


# ------------------------------------------------------------------ PIN
def _kdf(sale: bytes, terna: Tuple[int, ...], cifre: str) -> str:
    messaggio = (",".join(map(str, terna)) + ":" + cifre).encode()
    return hashlib.scrypt(messaggio, salt=sale, n=2 ** 14, r=8, p=1, dklen=32).hex()


def pin_debole(pin: str) -> bool:
    return (len(set(pin)) == 1 or pin in "01234567890123456789"
            or pin in "98765432109876543210")


def imposta_pin(tenant: str, pin: str) -> None:
    pin = str(pin or "").strip()
    if not re.fullmatch(r"\d{%d}" % PIN_CIFRE, pin):
        raise HTTPException(400, f"Il PIN deve essere di {PIN_CIFRE} cifre")
    if pin_debole(pin):
        raise HTTPException(400, "PIN troppo facile (cifre tutte uguali o in sequenza)")
    sale = secrets.token_bytes(16)
    terne = {",".join(map(str, t)): _kdf(sale, t, "".join(pin[i] for i in t)) for t in _TERNE}
    with _LOCK:
        dati = _leggi(tenant)
        dati.update({"pin": {"sale": sale.hex(), "terne": terne}, "pin_impostato": int(time.time()),
                     "bloccato": False, "errori": 0})
        for sessione in dati["sessioni"].values():      # un PIN nuovo chiude le sessioni aperte
            sessione.update({"sbloccato_fino": 0, "sfida": None})
        _salva(tenant, dati)


def _verifica(dati: Dict[str, Any], terna: List[int], cifre: str) -> bool:
    pin = dati.get("pin") or {}
    atteso = (pin.get("terne") or {}).get(",".join(map(str, terna)))
    if not atteso or not pin.get("sale"):
        return False
    calcolato = _kdf(bytes.fromhex(pin["sale"]), tuple(terna), cifre)
    return hmac.compare_digest(calcolato, atteso)


def verifica_pin_intero(tenant: str, pin: str) -> bool:
    """Il PIN completo (per le azioni irreversibili). Gli errori contano come quelli di
    WhatsApp: dopo MAX_ERRORI lo spazio si blocca e si sblocca solo dalla dashboard."""
    pin = re.sub(r"\D", "", str(pin or ""))
    with _LOCK:
        dati = _leggi(tenant)
        if dati.get("bloccato"):
            raise HTTPException(423, "Spazio bloccato dopo troppi PIN sbagliati: sbloccalo dalla "
                                     "pagina Sicurezza")
        if not dati.get("pin"):
            raise HTTPException(400, "Prima imposta il PIN nella pagina Sicurezza")
        giusto = (len(pin) == PIN_CIFRE and _verifica(dati, [0, 1, 2], pin[:3])
                  and _verifica(dati, [3, 4, 5], pin[3:]))
        if giusto:
            dati["errori"] = 0
        else:
            dati["errori"] = int(dati.get("errori") or 0) + 1
            if dati["errori"] >= MAX_ERRORI:
                dati["bloccato"] = True
                for sessione in dati["sessioni"].values():
                    sessione.update({"sbloccato_fino": 0, "sfida": None, "in_attesa": None})
        _salva(tenant, dati)
        return giusto


docs.verifica_pin = verifica_pin_intero


def distruggi_spazio(tenant: str) -> int:
    """Svuota lo spazio privato. Restano solo PIN e telefoni (sicurezza.json); le sessioni
    aperte si chiudono. Nessun titolo nel log."""
    cliente = docs.cliente_privato(tenant)
    with _LOCK:
        quanti = docs.distruggi_tutto(cliente, conserva=("sicurezza.json",))
        _PAGINE.pop(cliente, None)
        dati = _leggi(tenant)
        for sessione in dati["sessioni"].values():
            sessione.update({"sbloccato_fino": 0, "sfida": None, "in_attesa": None,
                             "distruggi_da": 0})
        _salva(tenant, dati)
    print("[SPAZIO] spazio privato svuotato dal proprietario")
    return quanti


# ------------------------------------------------------------------ collegamento del telefono
def _impronta_codice(codice: str) -> str:
    return hashlib.sha256(("codice:" + codice).encode()).hexdigest()


def genera_codice(tenant: str) -> str:
    codice = f"{secrets.randbelow(10 ** 6):06d}"
    with _LOCK:
        dati = _leggi(tenant)
        dati["codice"] = {"impronta": _impronta_codice(codice),
                          "scade": int(time.time()) + CODICE_S, "tentativi": 0}
        _salva(tenant, dati)
    return codice


def collega(sender: str, codice: str) -> Optional[str]:
    """Collega il numero al titolare che ha generato quel codice. None se non vale."""
    ora, h = int(time.time()), impronta_telefono(sender)
    with _LOCK:
        for tenant in _titolari():
            if not abilitato(tenant):
                continue
            dati = _leggi(tenant)
            pendente = dati.get("codice")
            if not pendente or ora > int(pendente.get("scade") or 0):
                continue
            if hmac.compare_digest(_impronta_codice(codice), pendente.get("impronta") or ""):
                dati["telefoni"][h] = {"collegato": ora}
                dati.pop("codice", None)
                _salva(tenant, dati)
                return tenant
            pendente["tentativi"] = int(pendente.get("tentativi") or 0) + 1
            if pendente["tentativi"] >= CODICE_MAX_TENTATIVI:
                dati.pop("codice", None)      # troppi tentativi: il codice muore
            _salva(tenant, dati)
    return None


# ------------------------------------------------------------------ la risposta privata
SYSTEM_PRIVATO = """Sei l'archivio personale e PRIVATO del titolare. Rispondi alla sua domanda usando SOLO le fonti qui sotto: documenti e registrazioni che lui stesso ha messo nel suo spazio.
Regole:
1. Risposta diretta e breve, in italiano. Niente premesse, niente ripetizioni.
2. Ogni informazione porta la sua fonte tra parentesi, cosi': (fonte: «titolo», data).
3. Nelle registrazioni parlano piu' persone: scrivi chi ha detto cosa solo se il testo lo indica, e non attribuire mai a una persona le parole di un'altra. Se non e' chiaro chi parla, dillo.
4. Se le fonti non contengono la risposta scrivi: «Non l'ho trovato nei tuoi documenti.» e, se c'e', indica in una riga cosa hai trovato di piu' vicino.
5. Non inventare e non usare conoscenze generali: solo le fonti.
6. Se una fonte ha una validita' o non e' ancora in vigore, scrivilo accanto al dato.
7. Le fonti IMMAGINE, VIDEO e DISEGNO sono file che arrivano ALLEGATI alla risposta: di loro conosci solo il trigger scritto dal titolare. Non descrivere cio' che non e' nel trigger; se servono, scrivi che sono allegate. Un file pertinente E' una risposta: se e' l'unica cosa trovata NON scrivere «Non l'ho trovato», scrivi cosa hai trovato (es.: «Su questo ho solo un video, allegato: ...» con il suo trigger) e, in una riga, cosa manca.
8. Scrivi testo semplice: niente markdown, niente asterischi, niente cancelletti.
Oggi e' il {oggi}."""
MAX_ALLEGATI = 3


def allegati(usate: List[Dict[str, Any]], pagine: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """I file da allegare: le foto/video/disegni scelti come fonte, poi quelli COLLEGATI a
    una fonte scelta (la foto di Antonio arriva con la telefonata di Antonio). Solo file
    utilizzabili oggi su questo canale; al massimo MAX_ALLEGATI."""
    scelti = [f.get("id") for f in usate]
    media: Dict[str, Dict[str, Any]] = {}
    for pagina, _ in docs.utilizzabili(pagine):
        scheda = pagina["scheda"]
        if scheda.get("media_path") and os.path.isfile(scheda["media_path"]):
            media.setdefault(scheda["id"], scheda)
    ordine: List[Tuple[int, int, str]] = []
    for mid, scheda in media.items():
        if mid in scelti:
            ordine.append((0, scelti.index(mid), mid))
            continue
        legami = [scelti.index(c) for c in (scheda.get("collegati") or []) if c in scelti]
        for fonte in scelti:              # anche il verso opposto: il documento cita la foto
            altra = next((p["scheda"] for p in pagine if p["scheda"].get("id") == fonte), {})
            if mid in (altra.get("collegati") or []):
                legami.append(scelti.index(fonte))
        if legami:
            ordine.append((1, min(legami), mid))
    out = []
    for _, _, mid in sorted(ordine)[:MAX_ALLEGATI]:
        scheda = media[mid]
        out.append({"id": mid, "titolo": scheda.get("titolo"), "tipo": scheda.get("tipo"),
                    "path": scheda["media_path"], "mime": scheda.get("media_mime"),
                    "nome": scheda.get("media_nome"), "bytes": scheda.get("media_bytes")})
    return out


def invalida(cliente: str) -> None:
    with _LOCK:
        _PAGINE.pop(cliente, None)


def _pagine(cliente: str) -> List[Dict[str, Any]]:
    with _LOCK:
        if cliente not in _PAGINE:
            _PAGINE[cliente] = docs.load_pages(cliente, tokenizer)
        return _PAGINE[cliente]


SYSTEM_AZIENDA = """Sei l'archivio dei documenti dell'azienda {azienda}. Rispondi alla domanda usando SOLO le fonti qui sotto: documenti, registrazioni, foto e video che l'azienda ha confermato.
Regole:
1. Risposta diretta e breve, in italiano. Niente premesse, niente ripetizioni.
2. Ogni informazione porta la sua fonte tra parentesi, cosi': (fonte: «titolo», data).
3. Nelle registrazioni parlano piu' persone: scrivi chi ha detto cosa solo se il testo lo indica.
4. Se le fonti non contengono la risposta scrivi: «Non l'ho trovato nei documenti dell'azienda.» e, se c'e', indica in una riga cosa hai trovato di piu' vicino.
5. Non inventare prezzi, codici, date o condizioni e non usare conoscenze generali: solo le fonti.
6. Se una fonte ha una validita' o non e' ancora in vigore, scrivilo accanto al dato.
7. Le fonti IMMAGINE, VIDEO e DISEGNO arrivano ALLEGATE: di loro conosci solo il trigger. Se sono l'unica cosa trovata, dillo («ho solo un video, allegato: ...»), non scrivere «non l'ho trovato».
8. Scrivi testo semplice: niente markdown, niente asterischi, niente cancelletti.
Oggi e' il {oggi}."""


def rispondi_azienda(cliente: str, domanda: str, canale: str = "web"
                     ) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Come rispondi(), ma sull'archivio di un'azienda SENZA catalogo (solo documenti,
    creata dalla console del gestore). Nessun catalogo accanto: solo le sue fonti."""
    if docs.e_privato(cliente):
        raise HTTPException(403, "Archivio non valido")
    sistema = SYSTEM_AZIENDA.replace("{azienda}", nome_azienda(cliente))
    return _rispondi(cliente, domanda, canale, sistema, privato=False)


def rispondi(tenant: str, domanda: str, canale: str = "web"
             ) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(risposta, fonti, allegati) dal SOLO spazio privato del titolare. Sincrona: va
    chiamata in un thread (run_in_threadpool): il modello e il significato bloccano."""
    return _rispondi(docs.cliente_privato(tenant), domanda, canale, SYSTEM_PRIVATO, privato=True)


def _rispondi(cliente: str, domanda: str, canale: str, sistema_tpl: str, privato: bool
              ) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    if tokenizer is None:
        raise RuntimeError("Spazio privato non configurato")
    domanda = (domanda or "").strip()
    if not domanda:
        raise HTTPException(400, "Domanda vuota")
    precedente = getattr(docs.contesto, "canale", "")
    docs.contesto.canale = canale
    docs.contesto.log_riservato = privato       # nel log del server niente titoli privati
    gettone = docs.consumo_cliente.set(cliente)
    try:
        pagine = _pagine(cliente)
        trovato, usate = ("", [])
        if pagine:
            trovato, usate = docs.evidence(domanda, pagine, tokenizer, EVIDENZE_PRIVATE,
                                           regola_prezzo=False, min_parole=1)
        if not credito_ok(cliente):
            risposta, trovato, usate = MSG_CREDITO, "", []
        elif not pagine:
            risposta = MSG_VUOTO if privato else "L'archivio dell'azienda è ancora vuoto."
        elif not trovato:                       # nessuna fonte: niente modello, niente costo
            risposta = MSG_NON_TROVATO if privato else "Non l'ho trovato nei documenti dell'azienda."
        else:
            if scrivi is None:
                raise RuntimeError("Modello non configurato")
            sistema = sistema_tpl.replace("{oggi}", docs.oggi().strftime("%d/%m/%Y"))
            risposta = (scrivi(sistema, f"DOMANDA: {domanda}\n\nFONTI:\n{trovato}", 900, 0.1)
                        or "").strip()
            if not risposta:
                raise RuntimeError("Risposta vuota dal modello")
            risposta = pulisci(risposta, canale)
        files = allegati(usate, pagine) if trovato else []
        docs.registra(cliente, domanda, risposta, usate)
        return risposta, usate, files
    finally:
        docs.contesto.canale = precedente
        docs.contesto.log_riservato = False
        docs.consumo_cliente.reset(gettone)


# ------------------------------------------------------------------ la conversazione WhatsApp
def _sessione(dati: Dict[str, Any], h: str) -> Dict[str, Any]:
    sessione = dati["sessioni"].setdefault(h, {})
    for chiave, valore in (("spazio", "privato"), ("sbloccato_fino", 0), ("ultimo", 0),
                           ("sfida", None), ("sfida_da", 0), ("in_attesa", None)):
        sessione.setdefault(chiave, valore)
    return sessione


def _nuova_sfida(sessione: Dict[str, Any], ora: int) -> List[int]:
    terna = sorted(secrets.SystemRandom().sample(range(PIN_CIFRE), SFIDA_CIFRE))
    sessione.update({"sfida": terna, "sfida_da": ora})
    return terna


def _decidi(sender: str, azienda: Optional[str], testo: str
            ) -> Tuple[str, Any]:
    """La parte che decide (sotto lucchetto, niente modello). Restituisce:
    ("risposta", testo) | ("azienda", intestazione) | ("privata", (tenant, domanda, prefisso))."""
    testo = (testo or "").strip()
    codice = _COLLEGA.match(testo)
    if codice:
        tenant = collega(sender, codice.group(1))
        if not tenant:
            return "risposta", MSG_CODICE_ERRATO
        dati = _leggi(tenant)
        seguito = ("Scrivi una domanda: ti chiederò 3 cifre del PIN." if dati.get("pin")
                   else "Ora imposta il PIN dalla dashboard, pagina *Sicurezza*.")
        return "risposta", "✅ Questo numero è collegato al tuo spazio privato. " + seguito
    tenant = proprietario(sender)
    if not tenant:
        return "azienda", ""                 # nessuno spazio privato: tutto come prima
    ora, h = int(time.time()), impronta_telefono(sender)
    if "azienda" not in docs.spazi_ammessi(tenant):
        azienda = None                       # modalita' "solo privato": nessun cambio di spazio
    etichetta = nome_azienda(azienda) if azienda else ""
    comando = testo.lower().strip(" .!?")
    with _LOCK:
        dati = _leggi(tenant)
        sessione = _sessione(dati, h)
        if ora - int(sessione["ultimo"] or 0) > RITORNO_PRIVATO_S:
            sessione["spazio"] = "privato"
        sessione["ultimo"] = ora
        if comando in ("privato", "spazio privato"):
            sessione["spazio"] = "privato"
            _salva(tenant, dati)
            return "risposta", "🔒 Ora parli con il tuo *spazio privato*."
        if azienda and comando in {"azienda", azienda.lower(), etichetta.lower()}:
            sessione["spazio"] = "azienda"
            _salva(tenant, dati)
            return "risposta", f"🏢 Ora parli con *{etichetta}*."
        # ---- distruzione: «distruggi», poi il PIN INTERO entro 2 minuti
        if comando in ("distruggi", "distruggi tutto"):
            sessione.update({"distruggi_da": ora, "sfida": None, "in_attesa": None})
            _salva(tenant, dati)
            return "risposta", MSG_DISTRUGGI_CHIEDI
        attesa = int(sessione.get("distruggi_da") or 0)
        if attesa and ora - attesa <= DISTRUGGI_S:
            sessione["distruggi_da"] = 0
            _salva(tenant, dati)
            if comando == "annulla" or not re.fullmatch(r"\d{%d}" % PIN_CIFRE, re.sub(r"\s", "", testo)):
                return "risposta", MSG_DISTRUGGI_ANNULLATO
            try:
                giusto = verifica_pin_intero(tenant, testo)
            except HTTPException:
                return "risposta", MSG_BLOCCATO
            if not giusto:
                dati = _leggi(tenant)
                if dati.get("bloccato"):
                    return "risposta", MSG_BLOCCATO
                return "risposta", ("❌ PIN non corretto: non è stato distrutto nulla. "
                                    f"Tentativi rimasti: {MAX_ERRORI - int(dati.get('errori') or 0)}.")
            distruggi_spazio(tenant)
            return "risposta", MSG_DISTRUTTO
        if attesa:
            sessione["distruggi_da"] = 0
        if comando in ("chiudi", "blocca", "esci"):
            sessione.update({"sbloccato_fino": 0, "sfida": None, "in_attesa": None})
            _salva(tenant, dati)
            return "risposta", MSG_CHIUSO
        if comando in ("spazi", "aiuto", "help", "?"):
            _salva(tenant, dati)
            righe = ["Scrivi *privato* per il tuo spazio personale"]
            if azienda:
                righe.append(f"*{etichetta.lower()}* per l'azienda")
            righe.append("*chiudi* per richiudere lo spazio privato.")
            ora_in = "privato" if sessione["spazio"] == "privato" or not azienda else etichetta
            return "risposta", ", ".join(righe) + f"\nOra sei in: *{ora_in}*."
        if sessione["spazio"] == "azienda" and azienda:
            _salva(tenant, dati)
            return "azienda", f"🏢 *{etichetta}*\n\n"
        sessione["spazio"] = "privato"
        # ---- spazio privato: PIN
        if dati.get("bloccato"):
            _salva(tenant, dati)
            return "risposta", MSG_BLOCCATO
        if not dati.get("pin"):
            _salva(tenant, dati)
            return "risposta", MSG_SENZA_PIN
        if ora < int(sessione["sbloccato_fino"] or 0):
            _salva(tenant, dati)
            return "privata", (tenant, testo, INTESTAZIONE)
        sfida = sessione.get("sfida")
        sfida_viva = bool(sfida) and ora - int(sessione.get("sfida_da") or 0) <= SFIDA_S
        if sfida_viva and _SOLO_CIFRE.match(testo):
            if len(re.sub(r"\D", "", testo)) == PIN_CIFRE:
                _salva(tenant, dati)
                return "risposta", MSG_PIN_INTERO + "\n" + msg_sfida(sfida)
            cifre = _TRE_CIFRE.match(testo)
            if cifre and _verifica(dati, sfida, "".join(cifre.groups())):
                dati["errori"] = 0
                domanda = sessione.get("in_attesa")
                sessione.update({"sbloccato_fino": ora + SBLOCCO_S, "sfida": None,
                                 "in_attesa": None})
                _salva(tenant, dati)
                if domanda:
                    return "privata", (tenant, domanda, "🔓 " + INTESTAZIONE)
                return "risposta", MSG_APERTO
            dati["errori"] = int(dati.get("errori") or 0) + 1
            if dati["errori"] >= MAX_ERRORI:
                dati["bloccato"] = True
                for altra in dati["sessioni"].values():
                    altra.update({"sbloccato_fino": 0, "sfida": None, "in_attesa": None})
                _salva(tenant, dati)
                print(f"[SPAZIO] bloccato dopo {MAX_ERRORI} errori titolare={tenant}")
                return "risposta", MSG_BLOCCATO
            _salva(tenant, dati)
            rimasti = MAX_ERRORI - dati["errori"]
            return "risposta", (f"❌ Cifre non corrette. Tentativi rimasti: {rimasti}.\n"
                                + msg_sfida(sfida))
        # una domanda vera: si tiene da parte e si chiede il PIN
        if not _SOLO_CIFRE.match(testo):
            sessione["in_attesa"] = testo[:3000]
        if not sfida_viva:
            sfida = _nuova_sfida(sessione, ora)
        _salva(tenant, dati)
        return "risposta", msg_sfida(sfida)


async def conversa(sender: str, azienda: Optional[str], testo: str) -> Tuple[Any, ...]:
    """Per il gateway: ("risposta", testo da inviare[, allegati]) oppure ("azienda",
    intestazione) se la domanda va all'azienda con il percorso di sempre."""
    tipo, valore = _decidi(sender, azienda, testo)
    if tipo != "privata":
        return tipo, valore
    tenant, domanda, prefisso = valore
    risposta, _, files = await run_in_threadpool(rispondi, tenant, domanda, "whatsapp")
    return "risposta", prefisso + risposta, files


# ------------------------------------------------------------------ dashboard (pagina Sicurezza)
def _titolare(tenant: str, chiave: Optional[str]) -> str:
    if tenant_verifier is None:
        raise HTTPException(503, "Spazio privato non configurato")
    titolare = tenant_verifier(tenant, chiave)
    if not abilitato(titolare):
        raise HTTPException(403, "Lo spazio privato non e' abilitato per questo cliente "
                                 f"(modalita' {docs.modalita(titolare)}).")
    return titolare


def stato(tenant: str) -> Dict[str, Any]:
    dati = _leggi(tenant)
    ora = int(time.time())
    return {"titolare": tenant, "spazio": docs.cliente_privato(tenant),
            "pin_impostato": bool(dati.get("pin")),
            "pin_impostato_il": dati.get("pin_impostato"),
            "bloccato": bool(dati.get("bloccato")), "errori": int(dati.get("errori") or 0),
            "telefoni_collegati": len(dati["telefoni"]),
            "sessioni_aperte": sum(1 for s in dati["sessioni"].values()
                                   if int(s.get("sbloccato_fino") or 0) > ora),
            "codice_in_attesa": bool(dati.get("codice"))
            and ora <= int((dati.get("codice") or {}).get("scade") or 0),
            "persistente": bool(os.getenv("DOCUMENT_UPLOAD_ROOT", "").strip())}


@router.get("/{tenant}/sicurezza")
async def sicurezza(tenant: str, x_applaudi_tenant_key: Optional[str] = Header(
        None, alias="X-Applaudi-Tenant-Key")):
    return stato(_titolare(tenant, x_applaudi_tenant_key))


@router.post("/{tenant}/pin")
async def nuovo_pin(tenant: str, dati: Dict[str, Any] = Body(...),
                    x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    titolare = _titolare(tenant, x_applaudi_tenant_key)
    pin, conferma = str(dati.get("pin") or ""), str(dati.get("conferma") or "")
    if pin != conferma:
        raise HTTPException(400, "I due PIN non coincidono")
    await run_in_threadpool(imposta_pin, titolare, pin)
    print(f"[SPAZIO] PIN impostato titolare={titolare}")
    return stato(titolare)


@router.post("/{tenant}/sblocca")
async def sblocca(tenant: str, x_applaudi_tenant_key: Optional[str] = Header(
        None, alias="X-Applaudi-Tenant-Key")):
    titolare = _titolare(tenant, x_applaudi_tenant_key)
    with _LOCK:
        dati = _leggi(titolare)
        dati.update({"bloccato": False, "errori": 0})
        _salva(titolare, dati)
    return stato(titolare)


@router.post("/{tenant}/collega")
async def nuovo_codice(tenant: str, x_applaudi_tenant_key: Optional[str] = Header(
        None, alias="X-Applaudi-Tenant-Key")):
    titolare = _titolare(tenant, x_applaudi_tenant_key)
    codice = genera_codice(titolare)
    return {"codice": codice, "minuti": CODICE_S // 60,
            "istruzione": f"Dal telefono da collegare scrivi al numero A'PLAUD'I: collega {codice}"}


@router.post("/{tenant}/scollega")
async def scollega(tenant: str, x_applaudi_tenant_key: Optional[str] = Header(
        None, alias="X-Applaudi-Tenant-Key")):
    titolare = _titolare(tenant, x_applaudi_tenant_key)
    with _LOCK:
        dati = _leggi(titolare)
        dati.update({"telefoni": {}, "sessioni": {}})
        dati.pop("codice", None)
        _salva(titolare, dati)
    return stato(titolare)


@router.post("/{tenant}/distruggi")
async def distruggi(tenant: str, dati: Dict[str, Any] = Body(...),
                    x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    """Svuota TUTTO lo spazio privato: PIN intero + la parola DISTRUGGI scritta a mano."""
    titolare = _titolare(tenant, x_applaudi_tenant_key)
    if str(dati.get("conferma") or "").strip() != PAROLA_DISTRUGGI:
        raise HTTPException(400, f"Per confermare scrivi {PAROLA_DISTRUGGI} in maiuscolo")
    if not await run_in_threadpool(verifica_pin_intero, titolare, str(dati.get("pin") or "")):
        raise HTTPException(403, "PIN non corretto: non è stato distrutto nulla")
    quanti = await run_in_threadpool(distruggi_spazio, titolare)
    return {"ok": True, "documenti_distrutti": quanti, **stato(titolare)}


@router.post("/{tenant}/chiedi_azienda")
async def chiedi_azienda(tenant: str, dati: Dict[str, Any] = Body(...),
                         x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    """Domanda all'archivio di un'azienda senza catalogo (solo documenti)."""
    if tenant_verifier is None:
        raise HTTPException(503, "Archivio non configurato")
    verificato = tenant_verifier(tenant, x_applaudi_tenant_key)
    if "azienda" not in docs.spazi_ammessi(verificato):
        raise HTTPException(403, "Questo cliente non ha lo spazio aziendale")
    cliente = docs.client_for_tenant(verificato)
    if e_catalogo(cliente):
        raise HTTPException(409, "Questa azienda ha un catalogo: la ricerca passa dal motore del catalogo")
    domanda = str(dati.get("domanda") or "").strip()[:3000]
    if not domanda:
        raise HTTPException(400, "Domanda vuota")
    risposta, fonti, _ = await run_in_threadpool(rispondi_azienda, cliente, domanda, "web")
    return {"risposta": risposta,
            "fonti": [{"titolo": f.get("titolo"), "data": f.get("data_documento"),
                       "versione": f.get("versione")} for f in fonti], "allegati": []}


@router.post("/{tenant}/chiedi")
async def chiedi(tenant: str, dati: Dict[str, Any] = Body(...),
                 x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    """Domanda al proprio spazio privato dalla dashboard (protetta dalla chiave)."""
    titolare = _titolare(tenant, x_applaudi_tenant_key)
    domanda = str(dati.get("domanda") or "").strip()[:3000]
    if not domanda:
        raise HTTPException(400, "Domanda vuota")
    risposta, fonti, files = await run_in_threadpool(rispondi, titolare, domanda, "web")
    return {"risposta": risposta,
            "fonti": [{"titolo": f.get("titolo"), "data": f.get("data_documento"),
                       "versione": f.get("versione")} for f in fonti],
            "allegati": [{k: a[k] for k in ("id", "titolo", "tipo", "mime")} for a in files]}
