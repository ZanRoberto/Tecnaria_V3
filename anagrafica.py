"""Anagrafica di A'PLAUD'I: clienti e persone gestiti da una console, non da Render.

Regole (20ott2026):
1. Il GESTORE della piattaforma (Roberto) entra con APPLAUDI_GESTORE_KEY, l'unica
   variabile da mettere su Render una volta. Senza di lei la console non si apre.
2. Un CLIENTE e' un'azienda o un privato: nome, modalita' (aziendale / privato / mix),
   catalogo collegato (per un'azienda). La sua chiave nasce qui, si mostra UNA volta e
   si conserva solo l'impronta: nessuno la puo' rileggere, nemmeno il gestore.
3. Le PERSONE appartengono a un cliente: titolare (uno solo), responsabile, membro, con
   il loro numero WhatsApp. Un numero appartiene a una sola persona.
4. Sospendere non cancella: un cliente sospeso non entra piu' con la sua chiave, una
   persona sospesa non viene piu' riconosciuta su WhatsApp. Tutto va nel registro.
5. Le variabili di Render gia' in uso (APPLAUDI_TENANT_KEYS_JSON e le altre) restano
   valide e vincono sempre: l'anagrafica AGGIUNGE, non sostituisce. Un nome gia'
   definito su Render non si puo' ricreare qui.
6. L'anagrafica sta accanto all'archivio dei documenti (disco persistente): con
   DOCUMENT_UPLOAD_ROOT=/var/data/documenti il file e' /var/data/anagrafica.json.
7. CONSUMI: per ogni archivio e ogni mese si contano le domande (web, WhatsApp, analisi
   PLAUD) e i token del modello; il costo si calcola con le tariffe che il gestore scrive
   nella console (quelle del suo fornitore). Il catalogo di un'azienda conta anche le
   domande del suo sito pubblico.
8. CREDITO: il gestore ricarica il cliente di un importo in euro; ogni chiamata al modello
   scala dal credito il suo costo moltiplicato per il RICARICO (la variabile del prezzo di
   vendita, decisa dal gestore e cambiabile quando vuole). Il cliente vede quanto gli resta
   come un anello, rispetto all'ultima ricarica. A credito finito, se "blocca a zero" e'
   acceso (predefinito), le risposte si fermano con un messaggio chiaro; un cliente senza
   credito impostato non ha limiti.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Header, HTTPException
from fastapi.responses import HTMLResponse

import documenti_aziendali as docs

router = APIRouter(tags=["Anagrafica"])

# ------------------------------------------------------------------ collegamenti (da app.py)
tenant_env: Callable[[], List[str]] = lambda: []          # i tenant definiti su Render
cataloghi: Callable[[], List[str]] = lambda: []           # i cataloghi disponibili (lago, ...)
archivio_valido: Callable[[str], bool] = lambda nome: False
tenant_verifier: Optional[Callable[[str, Optional[str]], str]] = None

RUOLI = {"titolare": "Titolare", "responsabile": "Responsabile", "membro": "Membro"}
TIPI = {"azienda": "Azienda", "privato": "Privato"}
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")
_TELEFONO = re.compile(r"^\d{7,16}$")
_LOCK = threading.RLock()
try:
    from zoneinfo import ZoneInfo
    _ROMA = ZoneInfo("Europe/Rome")
except Exception:          # pragma: no cover
    _ROMA = None


# ------------------------------------------------------------------ archivio su disco
def percorso() -> str:
    esplicito = os.getenv("ANAGRAFICA_PATH", "").strip()
    if esplicito:
        return os.path.abspath(esplicito)
    return os.path.join(os.path.dirname(docs.upload_root()), "anagrafica.json")


def consumi_path() -> str:
    return os.path.splitext(percorso())[0] + "_consumi.json"


def crediti_path() -> str:
    return os.path.splitext(percorso())[0] + "_crediti.json"


def registro_path() -> str:
    return os.path.splitext(percorso())[0] + "_registro.jsonl"


def _vuota() -> Dict[str, Any]:
    return {"clienti": {}, "persone": {}}


def leggi() -> Dict[str, Any]:
    try:
        with open(percorso(), encoding="utf-8") as stream:
            dati = json.load(stream)
    except (OSError, ValueError):
        return _vuota()
    if not isinstance(dati, dict):
        return _vuota()
    for chiave in ("clienti", "persone"):
        if not isinstance(dati.get(chiave), dict):
            dati[chiave] = {}
    return dati


def _salva(dati: Dict[str, Any]) -> None:
    docs._write_json_atomic(percorso(), dati)


def _ora() -> str:
    return (dt.datetime.now(_ROMA) if _ROMA else dt.datetime.now()).isoformat(timespec="seconds")


def _annota(chi: str, azione: str, **campi: Any) -> None:
    riga = {"quando": _ora(), "chi": chi, "azione": azione}
    riga.update({k: v for k, v in campi.items() if v is not None})
    path = registro_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(riga, ensure_ascii=False) + "\n")


def _impronta(chiave: str) -> str:
    return hashlib.sha256(("aplaudi-chiave:" + chiave).encode()).hexdigest()


def _nuova_chiave() -> str:
    return secrets.token_urlsafe(24)


# ------------------------------------------------------------------ cosa usa il resto del servizio
def cliente(tenant: str) -> Optional[Dict[str, Any]]:
    return leggi()["clienti"].get((tenant or "").strip().lower())


def verifica_chiave(tenant: str, chiave: Optional[str]) -> Optional[bool]:
    """None = cliente sconosciuto all'anagrafica; False = chiave sbagliata. Un cliente
    sospeso risponde con un 403 esplicito."""
    voce = cliente(tenant)
    if voce is None:
        return None
    if voce.get("sospeso"):
        raise HTTPException(403, "Cliente sospeso dal gestore della piattaforma")
    attesa = voce.get("chiave_impronta") or ""
    return bool(chiave) and hmac.compare_digest(_impronta(chiave), attesa)


def tenant_attivi() -> List[str]:
    return sorted(t for t, v in leggi()["clienti"].items() if not v.get("sospeso"))


def archivio_di(tenant: str) -> Optional[str]:
    voce = cliente(tenant)
    if voce is None or voce.get("sospeso") or voce.get("tipo") != "azienda":
        return None
    return voce.get("archivio") or None


def archivi_solo_documenti() -> set:
    """Gli archivi delle aziende SENZA catalogo (solo documenti), di clienti attivi."""
    return {v.get("archivio") for v in leggi()["clienti"].values()
            if v.get("tipo") == "azienda" and v.get("solo_documenti") and not v.get("sospeso")
            and v.get("archivio")}


def modalita_di(tenant: str) -> Optional[str]:
    voce = cliente(tenant)
    if voce is None:
        return None
    return "privato" if voce.get("tipo") == "privato" else voce.get("modalita") or "aziendale"


def numeri_whatsapp(archivio_del_tenant: Callable[[str], Optional[str]]) -> Dict[str, str]:
    """telefono -> archivio dell'azienda, per le persone attive di clienti attivi.
    I privati (senza azienda) non si legano qui: entrano con il codice di collegamento."""
    dati = leggi()
    out: Dict[str, str] = {}
    for persona in dati["persone"].values():
        tel = persona.get("telefono")
        voce = dati["clienti"].get(persona.get("tenant"))
        if not tel or persona.get("sospeso") or (voce is not None and voce.get("sospeso")):
            continue
        try:
            archivio = archivio_del_tenant(persona.get("tenant"))
        except HTTPException:
            archivio = None
        if archivio:
            out[tel] = archivio
    return out


def persone_di(tenant: str) -> List[Dict[str, Any]]:
    return sorted((dict(p, id=pid) for pid, p in leggi()["persone"].items()
                   if p.get("tenant") == tenant),
                  key=lambda p: (list(RUOLI).index(p.get("ruolo", "membro")), p.get("nome", "")))


# ------------------------------------------------------------------ consumi
_CONSUMI_LOCK = threading.Lock()


def _mese() -> str:
    return (dt.datetime.now(_ROMA) if _ROMA else dt.datetime.now()).strftime("%Y-%m")


def _leggi_consumi() -> Dict[str, Any]:
    try:
        with open(consumi_path(), encoding="utf-8") as stream:
            dati = json.load(stream)
        return dati if isinstance(dati, dict) else {}
    except (OSError, ValueError):
        return {}


def _conta(archivio: str, **somme: int) -> None:
    """Non deve mai fermare una risposta: un errore qui si scrive nel log e basta."""
    if not archivio:
        return
    try:
        with _CONSUMI_LOCK:
            dati = _leggi_consumi()
            voce = dati.setdefault(archivio, {}).setdefault(_mese(), {})
            for k, v in somme.items():
                voce[k] = int(voce.get(k) or 0) + int(v or 0)
            docs._write_json_atomic(consumi_path(), dati)
    except Exception as error:
        print(f"[CONSUMI] non registrato: {type(error).__name__}")


def conta_domanda(archivio: str, canale: str) -> None:
    canale = canale if canale in ("web", "whatsapp", "plaud") else "web"
    _conta(archivio, **{"domande_" + canale: 1})


def conta_token(archivio: str, ingresso: int, uscita: int) -> None:
    _conta(archivio, token_ingresso=ingresso, token_uscita=uscita, chiamate=1)
    try:
        _scala_credito(archivio, ingresso, uscita)
    except Exception as error:
        print(f"[CREDITO] non scalato: {type(error).__name__}")


def tariffe() -> Dict[str, float]:
    t = leggi().get("tariffe") or {}
    return {"ingresso_eur_milione": float(t.get("ingresso_eur_milione") or 0),
            "uscita_eur_milione": float(t.get("uscita_eur_milione") or 0),
            "ricarico": float(t.get("ricarico") or 1)}


# ------------------------------------------------------------------ credito
def _leggi_crediti() -> Dict[str, Any]:
    try:
        with open(crediti_path(), encoding="utf-8") as stream:
            dati = json.load(stream)
        return dati if isinstance(dati, dict) else {}
    except (OSError, ValueError):
        return {}


def tenant_di_archivio(archivio: str) -> Optional[str]:
    """A quale cliente appartiene un archivio (lo spazio privato o quello aziendale)."""
    if not archivio:
        return None
    if docs.e_privato(archivio):
        return archivio[len(docs.PREFISSO_PRIVATO):] or None
    for t in sorted(set(tenant_env()) | set(leggi()["clienti"])):
        if "azienda" in docs.spazi_ammessi(t) and _archivio_sicuro(t) == archivio:
            return t
    return None


def credito_di(tenant: str) -> Optional[Dict[str, Any]]:
    voce = _leggi_crediti().get(tenant)
    if not isinstance(voce, dict):
        return None
    residuo = float(voce.get("residuo_eur") or 0)
    base = float(voce.get("base_eur") or 0)
    return {"residuo_eur": round(residuo, 4), "base_eur": round(base, 2),
            "percentuale": max(0, min(100, round(100 * residuo / base))) if base > 0 else 0,
            "blocca_a_zero": voce.get("blocca_a_zero", True) is not False,
            "ricaricato_il": voce.get("ricaricato_il"), "esaurito": residuo <= 0}


def credito_ok(archivio: str) -> bool:
    """False solo se il cliente dell'archivio ha un credito, e' finito e va bloccato."""
    tenant = tenant_di_archivio(archivio)
    c = credito_di(tenant) if tenant else None
    return not (c and c["esaurito"] and c["blocca_a_zero"])


MSG_CREDITO = ("Il credito di questo servizio è esaurito: le risposte riprendono dopo la ricarica. "
               "Contatta chi ti fornisce A'PLAUD'I.")


def _scala_credito(archivio: str, ingresso: int, uscita: int) -> None:
    tenant = tenant_di_archivio(archivio)
    if not tenant:
        return
    t = tariffe()
    costo = (ingresso * t["ingresso_eur_milione"] + uscita * t["uscita_eur_milione"]) / 1_000_000
    if costo <= 0:
        return
    with _CONSUMI_LOCK:
        dati = _leggi_crediti()
        voce = dati.get(tenant)
        if not isinstance(voce, dict):
            return                                 # nessun credito impostato: nessun limite
        voce["residuo_eur"] = round(float(voce.get("residuo_eur") or 0) - costo * t["ricarico"], 6)
        docs._write_json_atomic(crediti_path(), dati)


def consumi_di(archivi: List[str]) -> List[Dict[str, Any]]:
    """Mese per mese, sommando gli archivi di un cliente (azienda + privato)."""
    dati, t = _leggi_consumi(), tariffe()
    mesi: Dict[str, Dict[str, int]] = {}
    for a in archivi:
        for mese, voce in (dati.get(a) or {}).items():
            somma = mesi.setdefault(mese, {})
            for k, v in voce.items():
                somma[k] = somma.get(k, 0) + int(v or 0)
    out = []
    for mese in sorted(mesi, reverse=True):
        v = mesi[mese]
        costo = (v.get("token_ingresso", 0) * t["ingresso_eur_milione"]
                 + v.get("token_uscita", 0) * t["uscita_eur_milione"]) / 1_000_000
        out.append(dict(v, mese=mese, costo_eur=round(costo, 4)))
    return out


def _archivi_del_tenant(tenant: str) -> List[str]:
    out = []
    spazi = docs.spazi_ammessi(tenant)
    if "azienda" in spazi:
        archivio = _archivio_sicuro(tenant)
        if archivio:
            out.append(archivio)
    if "privato" in spazi:
        out.append(docs.cliente_privato(tenant))
    return out


# ------------------------------------------------------------------ regole comuni
def _pulisci_nome(valore: Any, campo: str = "Il nome") -> str:
    nome = re.sub(r"\s+", " ", str(valore or "")).strip()
    if len(nome) < 2:
        raise HTTPException(400, f"{campo} è obbligatorio")
    return nome[:80]


def _pulisci_telefono(valore: Any) -> Optional[str]:
    tel = re.sub(r"\D", "", str(valore or ""))
    if not tel:
        return None
    if tel.startswith("00"):
        tel = tel[2:]
    if len(tel) == 10 and tel.startswith("3"):           # cellulare italiano senza prefisso
        tel = "39" + tel
    if not _TELEFONO.fullmatch(tel):
        raise HTTPException(400, "Numero WhatsApp non valido: scrivilo con il prefisso, es. 393401234567")
    return tel


def _id_da_nome(nome: str, esistenti: set) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", nome.lower()).strip("-")[:24] or "cliente"
    if not base[0].isalnum():
        base = "c" + base
    candidato, n = base, 2
    while candidato in esistenti or candidato.startswith(docs.PREFISSO_PRIVATO):
        candidato, n = f"{base}-{n}", n + 1
    return candidato


def _numeri_render() -> set:
    try:
        mappa = json.loads(os.getenv("WHATSAPP_PHONE_BINDINGS_JSON", "{}") or "{}")
    except ValueError:
        return set()
    return {str(k) for k in mappa} if isinstance(mappa, dict) else set()


def _archivio_occupato(archivio: str, tranne: str) -> Optional[str]:
    """Un archivio appartiene a UN solo cliente: due aziende sullo stesso archivio
    vedrebbero l'una i documenti dell'altra. Restituisce chi lo usa gia'."""
    dati = leggi()
    for t in set(tenant_env()) | set(dati["clienti"]):
        if t == tranne:
            continue
        voce = dati["clienti"].get(t)
        if voce is not None:
            uso = voce.get("archivio") if voce.get("tipo") == "azienda" else None
        else:
            uso = _archivio_sicuro(t)
        if uso == archivio:
            return t
    return None


def _tenant_esistente(tenant: str) -> bool:
    return tenant in set(tenant_env()) or tenant in leggi()["clienti"]


def _aggiungi_persona(dati: Dict[str, Any], tenant: str, corpo: Dict[str, Any],
                      chi: str, ruoli_ammessi: Tuple[str, ...]) -> Dict[str, Any]:
    nome = _pulisci_nome(corpo.get("nome"))
    ruolo = str(corpo.get("ruolo") or "membro")
    if ruolo not in ruoli_ammessi:
        raise HTTPException(400, "Ruolo non ammesso")
    if ruolo == "titolare" and any(p.get("tenant") == tenant and p.get("ruolo") == "titolare"
                                   and not p.get("sospeso") for p in dati["persone"].values()):
        raise HTTPException(409, "Questo cliente ha già un titolare")
    tel = _pulisci_telefono(corpo.get("telefono"))
    if tel and any(p.get("telefono") == tel and not p.get("sospeso") for p in dati["persone"].values()):
        raise HTTPException(409, "Questo numero WhatsApp appartiene già a un'altra persona")
    if tel and tel in _numeri_render():
        raise HTTPException(409, "Questo numero è già legato su Render (WHATSAPP_PHONE_BINDINGS_JSON)")
    pid = secrets.token_hex(8)
    persona = {"tenant": tenant, "nome": nome, "ruolo": ruolo, "telefono": tel,
               "sospeso": False, "creato": _ora(), "creato_da": chi}
    dati["persone"][pid] = persona
    _salva(dati)
    _annota(chi, "persona_aggiunta", tenant=tenant, persona=pid, nome=nome, ruolo=ruolo)
    return dict(persona, id=pid)


def _modifica_persona(dati: Dict[str, Any], pid: str, corpo: Dict[str, Any], chi: str,
                      tenant: Optional[str], ruoli_ammessi: Tuple[str, ...]) -> Dict[str, Any]:
    persona = dati["persone"].get(pid)
    if persona is None or (tenant is not None and persona.get("tenant") != tenant):
        raise HTTPException(404, "Persona non trovata")
    if tenant is not None and persona.get("ruolo") == "titolare":
        raise HTTPException(403, "Il titolare lo modifica solo il gestore della piattaforma")
    if "nome" in corpo:
        persona["nome"] = _pulisci_nome(corpo.get("nome"))
    if "ruolo" in corpo:
        ruolo = str(corpo.get("ruolo") or "")
        if ruolo not in ruoli_ammessi:
            raise HTTPException(400, "Ruolo non ammesso")
        if ruolo == "titolare" and any(p.get("tenant") == persona["tenant"] and p.get("ruolo") == "titolare"
                                       and not p.get("sospeso") and k != pid
                                       for k, p in dati["persone"].items()):
            raise HTTPException(409, "Questo cliente ha già un titolare")
        persona["ruolo"] = ruolo
    if "telefono" in corpo:
        tel = _pulisci_telefono(corpo.get("telefono"))
        if tel and any(p.get("telefono") == tel and not p.get("sospeso") and k != pid
                       for k, p in dati["persone"].items()):
            raise HTTPException(409, "Questo numero WhatsApp appartiene già a un'altra persona")
        persona["telefono"] = tel
    if "sospeso" in corpo:
        persona["sospeso"] = bool(corpo.get("sospeso"))
    _salva(dati)
    _annota(chi, "persona_sospesa" if corpo.get("sospeso") else
            ("persona_riattivata" if "sospeso" in corpo else "persona_modificata"),
            tenant=persona["tenant"], persona=pid, nome=persona.get("nome"))
    return dict(persona, id=pid)


# ------------------------------------------------------------------ accesso del gestore
def _gestore(chiave: Optional[str]) -> str:
    attesa = os.getenv("APPLAUDI_GESTORE_KEY", "").strip()
    if len(attesa) < 12:
        raise HTTPException(503, "Console non attiva: su Render manca APPLAUDI_GESTORE_KEY "
                                 "(almeno 12 caratteri)")
    if not chiave or not hmac.compare_digest(chiave.encode(), attesa.encode()):
        raise HTTPException(401, "Chiave del gestore non valida")
    return "gestore"


def _vista_cliente(tenant: str, voce: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: voce.get(k) for k in ("nome", "tipo", "modalita", "archivio", "sospeso", "creato",
                                     "chiave_creata", "solo_documenti")}
    out["id"] = tenant
    out["origine"] = "console"
    out["modalita"] = modalita_di(tenant)
    out["persone"] = persone_di(tenant)
    out["consumi"] = consumi_di(_archivi_del_tenant(tenant))
    out["credito"] = credito_di(tenant)
    return out


@router.get("/api/gestore/stato")
async def gestore_stato(x_applaudi_gestore_key: Optional[str] = Header(None, alias="X-Applaudi-Gestore-Key")):
    _gestore(x_applaudi_gestore_key)
    dati = leggi()
    clienti = [_vista_cliente(t, v) for t, v in sorted(dati["clienti"].items())]
    for t in sorted(set(tenant_env()) - set(dati["clienti"])):
        clienti.append({"id": t, "nome": t.upper(), "origine": "render", "tipo": None,
                        "modalita": docs.modalita(t), "archivio": _archivio_sicuro(t),
                        "sospeso": False, "persone": persone_di(t),
                        "consumi": consumi_di(_archivi_del_tenant(t)), "credito": credito_di(t)})
    return {"clienti": clienti, "cataloghi": sorted(cataloghi()), "ruoli": RUOLI, "tipi": TIPI,
            "tariffe": tariffe(),
            "modalita": list(docs.MODALITA), "file": percorso(),
            "persistente": docs.disco_persistente()}


def _archivio_sicuro(tenant: str) -> Optional[str]:
    try:
        return docs.client_for_tenant(tenant)
    except HTTPException:
        return None


@router.post("/api/gestore/clienti")
async def gestore_nuovo_cliente(corpo: Dict[str, Any] = Body(...),
                                x_applaudi_gestore_key: Optional[str] = Header(None, alias="X-Applaudi-Gestore-Key")):
    chi = _gestore(x_applaudi_gestore_key)
    nome = _pulisci_nome(corpo.get("nome"), "Il nome del cliente")
    tipo = str(corpo.get("tipo") or "")
    if tipo not in TIPI:
        raise HTTPException(400, "Scegli se è un'azienda o un privato")
    modalita = "privato" if tipo == "privato" else str(corpo.get("modalita") or "aziendale")
    if modalita not in docs.MODALITA:
        raise HTTPException(400, "Modalità non valida")
    archivio = None
    solo_documenti = False
    if tipo == "azienda":
        archivio = str(corpo.get("archivio") or "").strip().lower()
        solo_documenti = not archivio            # nessun catalogo: un archivio suo, solo documenti
        if archivio and not archivio_valido(archivio):
            raise HTTPException(400, "Catalogo non disponibile")
        occupato = _archivio_occupato(archivio, "") if archivio else None
        if occupato:
            raise HTTPException(409, f"Il catalogo {archivio.upper()} è già di «{occupato}»: due aziende "
                                     "sullo stesso archivio vedrebbero i documenti l'una dell'altra")
    with _LOCK:
        dati = leggi()
        esistenti = set(dati["clienti"]) | set(tenant_env())
        richiesto = str(corpo.get("id") or "").strip().lower()
        if richiesto:
            if not _ID.fullmatch(richiesto) or richiesto.startswith(docs.PREFISSO_PRIVATO):
                raise HTTPException(400, "Identificativo non valido: lettere minuscole, cifre e trattini")
            if richiesto in esistenti:
                raise HTTPException(409, "Esiste già un cliente con questo identificativo")
            tenant = richiesto
        else:
            tenant = _id_da_nome(nome, esistenti | set(cataloghi()))
        if solo_documenti:
            if tenant in set(cataloghi()):
                raise HTTPException(409, "Identificativo uguale al nome di un catalogo: scegline un altro")
            archivio = tenant
        chiave = _nuova_chiave()
        dati["clienti"][tenant] = {"nome": nome, "tipo": tipo, "modalita": modalita,
                                   "archivio": archivio, "solo_documenti": solo_documenti,
                                   "sospeso": False, "creato": _ora(),
                                   "chiave_impronta": _impronta(chiave), "chiave_creata": _ora()}
        _salva(dati)
        _annota(chi, "cliente_creato", tenant=tenant, nome=nome, tipo=tipo, modalita=modalita,
                archivio=archivio)
    print(f"[GESTORE] cliente creato {tenant} tipo={tipo} modalita={modalita}")
    return {"ok": True, "cliente": _vista_cliente(tenant, dati["clienti"][tenant]), "chiave": chiave,
            "avviso": "Copia la chiave adesso: non verrà più mostrata."}


@router.post("/api/gestore/clienti/{tenant}")
async def gestore_modifica_cliente(tenant: str, corpo: Dict[str, Any] = Body(...),
                                   x_applaudi_gestore_key: Optional[str] = Header(None, alias="X-Applaudi-Gestore-Key")):
    chi = _gestore(x_applaudi_gestore_key)
    with _LOCK:
        dati = leggi()
        voce = dati["clienti"].get(tenant)
        if voce is None:
            if tenant in tenant_env():
                raise HTTPException(409, "Questo cliente è definito su Render: si modifica lì")
            raise HTTPException(404, "Cliente non trovato")
        cambi = {}
        if "nome" in corpo:
            voce["nome"] = cambi["nome"] = _pulisci_nome(corpo.get("nome"), "Il nome del cliente")
        if "modalita" in corpo and voce.get("tipo") == "azienda":
            if corpo["modalita"] not in docs.MODALITA:
                raise HTTPException(400, "Modalità non valida")
            voce["modalita"] = cambi["modalita"] = corpo["modalita"]
        if "archivio" in corpo and voce.get("tipo") == "azienda" and not voce.get("solo_documenti"):
            archivio = str(corpo.get("archivio") or "").strip().lower()
            if not archivio_valido(archivio):
                raise HTTPException(400, "Catalogo non disponibile")
            occupato = _archivio_occupato(archivio, tenant)
            if occupato:
                raise HTTPException(409, f"Il catalogo {archivio.upper()} è già di «{occupato}»")
            voce["archivio"] = cambi["archivio"] = archivio
        if "sospeso" in corpo:
            voce["sospeso"] = cambi["sospeso"] = bool(corpo.get("sospeso"))
        _salva(dati)
        _annota(chi, "cliente_modificato", tenant=tenant, cambi=cambi)
    return {"ok": True, "cliente": _vista_cliente(tenant, voce)}


@router.post("/api/gestore/clienti/{tenant}/chiave")
async def gestore_nuova_chiave(tenant: str, x_applaudi_gestore_key: Optional[str] = Header(None, alias="X-Applaudi-Gestore-Key")):
    """Chiave persa o rubata: la vecchia smette subito di funzionare."""
    chi = _gestore(x_applaudi_gestore_key)
    with _LOCK:
        dati = leggi()
        voce = dati["clienti"].get(tenant)
        if voce is None:
            raise HTTPException(404, "Cliente non trovato (quelli di Render si cambiano su Render)")
        chiave = _nuova_chiave()
        voce.update({"chiave_impronta": _impronta(chiave), "chiave_creata": _ora()})
        _salva(dati)
        _annota(chi, "chiave_rigenerata", tenant=tenant)
    return {"ok": True, "chiave": chiave, "avviso": "La chiave precedente non funziona più."}


@router.post("/api/gestore/clienti/{tenant}/persone")
async def gestore_nuova_persona(tenant: str, corpo: Dict[str, Any] = Body(...),
                                x_applaudi_gestore_key: Optional[str] = Header(None, alias="X-Applaudi-Gestore-Key")):
    chi = _gestore(x_applaudi_gestore_key)
    if not _tenant_esistente(tenant):
        raise HTTPException(404, "Cliente non trovato")
    with _LOCK:
        return {"ok": True, "persona": _aggiungi_persona(leggi(), tenant, corpo, chi, tuple(RUOLI))}


@router.post("/api/gestore/persone/{pid}")
async def gestore_modifica_persona(pid: str, corpo: Dict[str, Any] = Body(...),
                                   x_applaudi_gestore_key: Optional[str] = Header(None, alias="X-Applaudi-Gestore-Key")):
    chi = _gestore(x_applaudi_gestore_key)
    with _LOCK:
        return {"ok": True, "persona": _modifica_persona(leggi(), pid, corpo, chi, None, tuple(RUOLI))}


@router.post("/api/gestore/tariffe")
async def gestore_tariffe(corpo: Dict[str, Any] = Body(...),
                          x_applaudi_gestore_key: Optional[str] = Header(None, alias="X-Applaudi-Gestore-Key")):
    """Le tariffe del fornitore del modello, in euro per milione di token."""
    chi = _gestore(x_applaudi_gestore_key)
    nuove = {}
    for k in ("ingresso_eur_milione", "uscita_eur_milione", "ricarico"):
        if k == "ricarico" and str(corpo.get(k, "")).strip() == "":
            nuove[k] = tariffe()["ricarico"]
            continue
        try:
            valore = float(str(corpo.get(k, "")).replace(",", "."))
        except ValueError:
            raise HTTPException(400, "Tariffa non valida: scrivi un numero, es. 0,25")
        if not 0 <= valore <= 1000 or (k == "ricarico" and valore < 1):
            raise HTTPException(400, "Valore fuori misura (il ricarico parte da 1 = prezzo di costo)")
        nuove[k] = valore
    with _LOCK:
        dati = leggi()
        dati["tariffe"] = nuove
        _salva(dati)
        _annota(chi, "tariffe_modificate", cambi=nuove)
    return {"ok": True, "tariffe": nuove}


@router.post("/api/gestore/clienti/{tenant}/ricarica")
async def gestore_ricarica(tenant: str, corpo: Dict[str, Any] = Body(...),
                           x_applaudi_gestore_key: Optional[str] = Header(None, alias="X-Applaudi-Gestore-Key")):
    """Aggiunge credito in euro. L'anello del cliente riparte da qui (100%)."""
    chi = _gestore(x_applaudi_gestore_key)
    if not _tenant_esistente(tenant):
        raise HTTPException(404, "Cliente non trovato")
    try:
        importo = float(str(corpo.get("importo_eur", "")).replace(",", "."))
    except ValueError:
        raise HTTPException(400, "Importo non valido: scrivi un numero, es. 50")
    if not 0 < importo <= 100000:
        raise HTTPException(400, "Importo fuori misura")
    with _CONSUMI_LOCK:
        dati = _leggi_crediti()
        voce = dati.setdefault(tenant, {"residuo_eur": 0, "blocca_a_zero": True})
        voce["residuo_eur"] = round(float(voce.get("residuo_eur") or 0) + importo, 6)
        voce["base_eur"] = voce["residuo_eur"]
        voce["ricaricato_il"] = _ora()
        if "blocca_a_zero" in corpo:
            voce["blocca_a_zero"] = bool(corpo.get("blocca_a_zero"))
        docs._write_json_atomic(crediti_path(), dati)
    _annota(chi, "ricarica", tenant=tenant, importo_eur=importo, residuo_eur=voce["residuo_eur"])
    return {"ok": True, "credito": credito_di(tenant)}


@router.post("/api/gestore/clienti/{tenant}/blocco")
async def gestore_blocco(tenant: str, corpo: Dict[str, Any] = Body(...),
                         x_applaudi_gestore_key: Optional[str] = Header(None, alias="X-Applaudi-Gestore-Key")):
    chi = _gestore(x_applaudi_gestore_key)
    with _CONSUMI_LOCK:
        dati = _leggi_crediti()
        if tenant not in dati:
            raise HTTPException(409, "Prima fai una ricarica")
        dati[tenant]["blocca_a_zero"] = bool(corpo.get("blocca_a_zero"))
        docs._write_json_atomic(crediti_path(), dati)
    _annota(chi, "blocco_a_zero", tenant=tenant, cambi={"blocca_a_zero": bool(corpo.get("blocca_a_zero"))})
    return {"ok": True, "credito": credito_di(tenant)}


@router.get("/api/credito/{tenant}")
async def credito_cliente(tenant: str, x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    """Quanto credito resta al cliente: per l'anello della sua dashboard."""
    verificato = _titolare(tenant, x_applaudi_tenant_key)
    return {"tenant": verificato, "credito": credito_di(verificato)}


@router.get("/api/gestore/registro")
async def gestore_registro(x_applaudi_gestore_key: Optional[str] = Header(None, alias="X-Applaudi-Gestore-Key")):
    _gestore(x_applaudi_gestore_key)
    try:
        with open(registro_path(), encoding="utf-8") as stream:
            righe = [json.loads(r) for r in stream if r.strip()]
    except (OSError, ValueError):
        righe = []
    return {"righe": list(reversed(righe))[:300]}


# ------------------------------------------------------------------ il titolare: le sue persone
def _titolare(tenant: str, chiave: Optional[str]) -> str:
    if tenant_verifier is None:
        raise HTTPException(503, "Anagrafica non configurata")
    return tenant_verifier(tenant, chiave)


@router.get("/api/persone/{tenant}")
async def persone_elenco(tenant: str, x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    verificato = _titolare(tenant, x_applaudi_tenant_key)
    return {"tenant": verificato, "persone": persone_di(verificato),
            "ruoli": {k: v for k, v in RUOLI.items() if k != "titolare"}}


@router.post("/api/persone/{tenant}")
async def persone_aggiungi(tenant: str, corpo: Dict[str, Any] = Body(...),
                           x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    """Il titolare aggiunge responsabili e membri; il titolare lo nomina solo il gestore."""
    verificato = _titolare(tenant, x_applaudi_tenant_key)
    with _LOCK:
        return {"ok": True, "persona": _aggiungi_persona(leggi(), verificato, corpo, "titolare:" + verificato,
                                                         ("responsabile", "membro"))}


@router.post("/api/persone/{tenant}/{pid}")
async def persone_modifica(tenant: str, pid: str, corpo: Dict[str, Any] = Body(...),
                           x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    verificato = _titolare(tenant, x_applaudi_tenant_key)
    with _LOCK:
        return {"ok": True, "persona": _modifica_persona(leggi(), pid, corpo, "titolare:" + verificato,
                                                         verificato, ("responsabile", "membro"))}


# ------------------------------------------------------------------ la pagina della console
@router.get("/gestore", response_class=HTMLResponse)
async def pagina_gestore():
    return HTMLResponse(PAGINA, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                         "Referrer-Policy": "no-referrer"})


PAGINA = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>A'PLAUD'I · Console del gestore</title>
<style>
:root{--bg:#070b12;--card:#111826;--line:#26324a;--fg:#e8eef8;--muted:#93a3ba;--cyan:#22d3ee;--ok:#34d399;--warn:#fbbf24;--danger:#f87171}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:24px max(16px,env(safe-area-inset-left))}
h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:0 0 10px}.muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-top:16px}
input,select{font:inherit;background:#090e16;color:var(--fg);border:1px solid #3a4659;border-radius:9px;padding:10px 12px;min-width:0}
button{font:inherit;font-weight:700;background:var(--cyan);color:#031014;border:0;border-radius:9px;padding:10px 14px;cursor:pointer}
button.secondary{background:#202b3b;color:var(--fg);border:1px solid #36445a}button.danger{background:#b4232f;color:#fff}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.row>input,.row>select{flex:1;min-width:160px}
.cliente{border:1px solid var(--line);border-radius:12px;padding:14px;margin-top:12px;background:#0c1220}
.cliente.spento{opacity:.55}.badge{display:inline-block;font-size:12px;font-weight:700;border-radius:20px;padding:2px 10px;border:1px solid #36445a;color:var(--muted);margin-left:6px}
.badge.ok{color:var(--ok);border-color:#1f664e}.badge.warn{color:var(--warn);border-color:#6b5317}
table{width:100%;border-collapse:collapse;margin-top:10px}td,th{text-align:left;padding:7px 6px;border-top:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-size:13px;font-weight:600}
.scroll{overflow-x:auto}.chiave{font:700 18px ui-monospace,monospace;color:var(--cyan);word-break:break-all;background:#06101a;border:1px dashed var(--cyan);border-radius:10px;padding:12px;margin:10px 0}
.ok{color:var(--ok)}.warn{color:var(--warn)}.error{color:var(--danger)}[hidden]{display:none!important}
</style></head><body><div class="wrap">
<h1>Console del gestore</h1><div class="muted">Clienti, persone e chiavi di A'PLAUD'I. Nessuna modifica a mano su Render.</div>
<section class="card"><div class="row"><input id="gk" type="password" autocomplete="off" placeholder="Chiave del gestore (APPLAUDI_GESTORE_KEY)"><button onclick="entra()">Entra</button></div><div id="stato" class="muted" style="margin-top:8px">Inserisci la chiave del gestore.</div></section>
<div id="dentro" hidden>
<section class="card"><h2>Nuovo cliente</h2><div class="row"><input id="nNome" placeholder="Nome (es. Thermogen srl, Mario Rossi)"><select id="nTipo" onchange="tipoCambiato()"><option value="azienda">Azienda</option><option value="privato">Privato</option></select><select id="nModo"><option value="aziendale">Solo aziendale</option><option value="mix">Azienda + privato del titolare</option></select><select id="nArch"></select><button onclick="nuovoCliente()">Crea</button></div><div id="nOut" style="margin-top:10px"></div></section>
<section class="card"><h2>Tariffe del modello</h2><div class="muted">Euro per milione di token, come da listino del tuo fornitore (es. DeepSeek): servono a calcolare il costo di ogni cliente.</div><div class="row" style="margin-top:10px;align-items:flex-end"><label style="flex:1;min-width:160px">Ingresso €/milione<input id="tIn" inputmode="decimal" style="width:100%"></label><label style="flex:1;min-width:160px">Uscita €/milione<input id="tOut" inputmode="decimal" style="width:100%"></label><label style="flex:1;min-width:160px">Ricarico (3 = tre volte il costo)<input id="tRic" inputmode="decimal" style="width:100%"></label><button class="secondary" onclick="salvaTariffe()">Salva tariffe</button></div><div id="tOutMsg" class="muted" style="margin-top:6px"></div></section>
<section class="card"><div class="row" style="justify-content:space-between"><h2 style="margin:0">Clienti</h2><button class="secondary" onclick="carica()">Aggiorna</button></div><div id="clienti"></div></section>
<section class="card"><div class="row" style="justify-content:space-between"><h2 style="margin:0">Registro</h2><button class="secondary" onclick="registro()">Mostra</button></div><div id="reg" class="scroll"></div></section>
</div></div>
<script>
let stato={};const RU={titolare:'Titolare',responsabile:'Responsabile',membro:'Membro'};const MO={aziendale:'Solo aziendale',privato:'Solo privato',mix:'Azienda + privato'};
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function key(){return document.getElementById('gk').value.trim()}
async function api(url,corpo){const o={headers:{'X-Applaudi-Gestore-Key':key()}};if(corpo!==undefined){o.method='POST';o.headers['Content-Type']='application/json';o.body=JSON.stringify(corpo)}const r=await fetch(url,o);const d=await r.json().catch(()=>({detail:'Risposta non leggibile'}));if(!r.ok)throw new Error(d.detail||('Errore '+r.status));return d}
async function entra(){try{sessionStorage.setItem('aplaudi-gestore',key())}catch(e){}await carica()}
function tipoCambiato(){const az=document.getElementById('nTipo').value==='azienda';document.getElementById('nModo').hidden=!az;document.getElementById('nArch').hidden=!az}
async function carica(){const st=document.getElementById('stato');try{stato=await api('/api/gestore/stato');st.textContent=stato.persistente?'Anagrafica sul disco persistente · '+stato.file:'ATTENZIONE: disco persistente non configurato: l’anagrafica sparisce al prossimo riavvio.';st.className=stato.persistente?'ok':'error';document.getElementById('dentro').hidden=false;const a=document.getElementById('nArch');document.getElementById('tIn').value=String(stato.tariffe.ingresso_eur_milione||'').replace('.',',');document.getElementById('tOut').value=String(stato.tariffe.uscita_eur_milione||'').replace('.',',');document.getElementById('tRic').value=String(stato.tariffe.ricarico||1).replace('.',',');a.innerHTML='<option value="">Nessun catalogo: solo documenti</option>'+stato.cataloghi.map(c=>'<option value="'+esc(c)+'">'+esc(c.toUpperCase())+'</option>').join('');disegna()}catch(e){st.textContent=e.message;st.className='error';document.getElementById('dentro').hidden=true}}
function disegna(){const box=document.getElementById('clienti');box.innerHTML=stato.clienti.length?'':'<div class="muted">Nessun cliente.</div>';stato.clienti.forEach(c=>{const d=document.createElement('div');d.className='cliente'+(c.sospeso?' spento':'');const render=c.origine==='render';d.innerHTML='<div class="row" style="justify-content:space-between"><div><strong>'+esc(c.nome)+'</strong> <span class="muted">('+esc(c.id)+')</span>'+(render?'<span class="badge">da Render</span>':'<span class="badge">'+esc(c.tipo==='privato'?'Privato':'Azienda')+'</span>')+(c.sospeso?'<span class="badge warn">SOSPESO</span>':'<span class="badge ok">attivo</span>')+'<div class="muted">'+esc(MO[c.modalita]||c.modalita)+(c.solo_documenti?' · solo documenti':(c.archivio?' · catalogo '+esc(String(c.archivio).toUpperCase()):''))+(c.chiave_creata?' · chiave del '+esc(String(c.chiave_creata).slice(0,10)):'')+'</div></div>'+(render?'':'<div class="row"><button class="secondary" data-a="k">Nuova chiave</button><button class="'+(c.sospeso?'secondary':'danger')+'" data-a="s">'+(c.sospeso?'Riattiva':'Sospendi')+'</button></div>')+'</div><div class="out"></div><div class="scroll"><table><tr><th>Persona</th><th>Ruolo</th><th>WhatsApp</th><th></th></tr>'+(c.persone||[]).map(p=>'<tr'+(p.sospeso?' class="muted"':'')+'><td>'+esc(p.nome)+'</td><td>'+esc(RU[p.ruolo]||p.ruolo)+'</td><td>'+esc(p.telefono||'—')+'</td><td><button class="secondary" data-p="'+esc(p.id)+'" data-s="'+(p.sospeso?'0':'1')+'">'+(p.sospeso?'Riattiva':'Sospendi')+'</button></td></tr>').join('')+'</table></div>'+creditoHtml(c)+consumiHtml(c.consumi||[])+'<div class="row" style="margin-top:10px"><input placeholder="Nome e cognome" data-f="nome"><select data-f="ruolo">'+Object.entries(RU).map(([k,v])=>'<option value="'+k+'"'+(k==='membro'?' selected':'')+'>'+v+'</option>').join('')+'</select><input placeholder="WhatsApp con prefisso, es. 393401234567" data-f="telefono"><button data-a="p">Aggiungi persona</button></div>';const out=d.querySelector('.out');const kb=d.querySelector('[data-a=k]');if(kb)kb.onclick=async()=>{try{const x=await api('/api/gestore/clienti/'+c.id+'/chiave',{});out.innerHTML='<div class="chiave">'+esc(x.chiave)+'</div><div class="warn">'+esc(x.avviso)+' Copiala adesso: non verrà più mostrata.</div>'}catch(e){out.innerHTML='<div class="error">'+esc(e.message)+'</div>'}};const sb=d.querySelector('[data-a=s]');if(sb)sb.onclick=async()=>{try{await api('/api/gestore/clienti/'+c.id,{sospeso:!c.sospeso});await carica()}catch(e){out.innerHTML='<div class="error">'+esc(e.message)+'</div>'}};d.querySelectorAll('[data-p]').forEach(b=>b.onclick=async()=>{try{await api('/api/gestore/persone/'+b.dataset.p,{sospeso:b.dataset.s==='1'});await carica()}catch(e){out.innerHTML='<div class="error">'+esc(e.message)+'</div>'}});d.querySelector('[data-a=r]').onclick=async()=>{try{await api('/api/gestore/clienti/'+c.id+'/ricarica',{importo_eur:d.querySelector('[data-f=ricarica]').value});await carica()}catch(e){out.innerHTML='<div class="error">'+esc(e.message)+'</div>'}};const bb=d.querySelector('[data-a=b]');if(bb)bb.onchange=async()=>{try{await api('/api/gestore/clienti/'+c.id+'/blocco',{blocca_a_zero:bb.checked});await carica()}catch(e){out.innerHTML='<div class="error">'+esc(e.message)+'</div>'}};d.querySelector('[data-a=p]').onclick=async()=>{const v=f=>d.querySelector('[data-f='+f+']').value;try{await api('/api/gestore/clienti/'+c.id+'/persone',{nome:v('nome'),ruolo:v('ruolo'),telefono:v('telefono')});await carica()}catch(e){out.innerHTML='<div class="error">'+esc(e.message)+'</div>'}};box.appendChild(d)})}
function eur(v){return Number(v||0).toLocaleString('it-IT',{style:'currency',currency:'EUR',minimumFractionDigits:2,maximumFractionDigits:4})}
function creditoHtml(c){const k=c.credito;let t='<div class="row" style="margin-top:10px">';if(k){t+='<span><strong>Credito</strong> '+eur(k.residuo_eur)+' su '+eur(k.base_eur)+' ('+k.percentuale+'%)'+(k.esaurito?' <span class="badge warn">ESAURITO</span>':'')+'</span><label class="muted"><input type="checkbox" data-a="b"'+(k.blocca_a_zero?' checked':'')+'> blocca a zero</label>'}else{t+='<span class="muted">Credito: nessun limite impostato</span>'}return t+'<input data-f="ricarica" inputmode="decimal" placeholder="Ricarica €" style="max-width:140px;flex:0 1 140px"><button class="secondary" data-a="r">Ricarica</button></div>'}
function num(v){return Number(v||0).toLocaleString('it-IT')}
function consumiHtml(rr){if(!rr.length)return '<div class="muted" style="margin-top:8px">Consumi: nessuna domanda ancora.</div>';return '<div class="scroll"><table><tr><th>Mese</th><th>Domande web</th><th>WhatsApp</th><th>Analisi PLAUD</th><th>Token</th><th>Costo stimato</th></tr>'+rr.map(r=>'<tr><td>'+esc(r.mese)+'</td><td>'+num(r.domande_web)+'</td><td>'+num(r.domande_whatsapp)+'</td><td>'+num(r.domande_plaud)+'</td><td>'+num((r.token_ingresso||0)+(r.token_uscita||0))+'</td><td>'+(stato.tariffe&&(stato.tariffe.ingresso_eur_milione||stato.tariffe.uscita_eur_milione)?eur(r.costo_eur):'<span class="muted">imposta le tariffe</span>')+'</td></tr>').join('')+'</table></div>'}
async function salvaTariffe(){const m=document.getElementById('tOutMsg');try{await api('/api/gestore/tariffe',{ingresso_eur_milione:document.getElementById('tIn').value,uscita_eur_milione:document.getElementById('tOut').value,ricarico:document.getElementById('tRic').value});m.textContent='Tariffe salvate.';m.className='ok';await carica()}catch(e){m.textContent=e.message;m.className='error'}}
async function nuovoCliente(){const out=document.getElementById('nOut');try{const x=await api('/api/gestore/clienti',{nome:document.getElementById('nNome').value,tipo:document.getElementById('nTipo').value,modalita:document.getElementById('nModo').value,archivio:document.getElementById('nArch').value});document.getElementById('nNome').value='';out.innerHTML='<div class="ok">Creato «'+esc(x.cliente.nome)+'» con identificativo <strong>'+esc(x.cliente.id)+'</strong>. Indirizzo della sua dashboard: /plaud/'+esc(x.cliente.id)+'</div><div class="chiave">'+esc(x.chiave)+'</div><div class="warn">'+esc(x.avviso)+'</div>';await carica()}catch(e){out.innerHTML='<div class="error">'+esc(e.message)+'</div>'}}
async function registro(){const box=document.getElementById('reg');try{const x=await api('/api/gestore/registro');box.innerHTML=x.righe.length?'<table><tr><th>Quando</th><th>Chi</th><th>Azione</th><th>Dettagli</th></tr>'+x.righe.map(r=>'<tr><td>'+esc(String(r.quando).replace('T',' ').slice(0,16))+'</td><td>'+esc(r.chi)+'</td><td>'+esc(r.azione)+'</td><td class="muted">'+esc([r.tenant,r.nome,r.ruolo,r.cambi?JSON.stringify(r.cambi):''].filter(Boolean).join(' · '))+'</td></tr>').join('')+'</table>':'<div class="muted">Registro vuoto.</div>'}catch(e){box.innerHTML='<div class="error">'+esc(e.message)+'</div>'}}
try{const k=sessionStorage.getItem('aplaudi-gestore');if(k){document.getElementById('gk').value=k;carica()}}catch(e){}
</script></body></html>"""
