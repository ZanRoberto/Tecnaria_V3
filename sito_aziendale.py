"""Il sito dell'azienda come fonte della conoscenza (22ott2026, come «Sito» di Bookli, ma
con le versioni).

1. Si incolla l'indirizzo del sito o della sua mappa (sitemap.xml): si vedono le pagine
   trovate, si scelgono, entrano nei Documenti come «Pagina del sito», con indirizzo e data
   di lettura. Ogni risposta le cita come gli altri documenti.
2. Il sito si rilegge da solo (ogni N giorni, scelto dal cliente). Una pagina cambiata NON
   sovrascrive: diventa una versione nuova, la vecchia va nello storico, e nella storia del
   documento si scrive cosa e' cambiato (le righe tolte e aggiunte).
3. Si rispetta robots.txt. Si leggono solo indirizzi pubblici: mai indirizzi interni del
   server (localhost, reti private), nemmeno dopo un reindirizzamento.
4. Pagine costruite solo con JavaScript: il testo non c'e' nell'HTML, e si dice.
"""
from __future__ import annotations

import difflib
import hashlib
import ipaddress
import json
import os
import re
import socket
import threading
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

from fastapi import APIRouter, Body, Header, HTTPException, Query
from starlette.concurrency import run_in_threadpool

import documenti_aziendali as docs

MAX_BYTE = 3 * 1024 * 1024
MAX_PAGINE_SCOPERTE = 300
MAX_PAGINE_SITO = 60
TEMPO_S = 15
AGENTE = "APLAUDI-lettore-sito/1.0 (+conoscenza aziendale)"
FREQUENZE = (1, 7, 30)
MIN_TESTO = 200            # meno di cosi': pagina vuota o fatta solo di JavaScript
_LOCK = threading.Lock()

router = APIRouter(prefix="/api/documents", tags=["Sito aziendale"])


# ------------------------------------------------------------------ rete, con prudenza
def _indirizzo_pubblico(url: str) -> bool:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                or ip.is_unspecified):
            return False
    return True


def scarica(url: str) -> Tuple[str, str, str]:
    """(url finale, tipo, testo). Reindirizzamenti seguiti a mano, ognuno controllato."""
    import httpx
    with httpx.Client(timeout=TEMPO_S, follow_redirects=False, headers={"User-Agent": AGENTE}) as client:
        for _ in range(6):
            if not _indirizzo_pubblico(url):
                raise HTTPException(400, "Indirizzo non raggiungibile o non pubblico")
            with client.stream("GET", url) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code >= 400:
                    raise HTTPException(422, f"Il sito ha risposto {r.status_code}")
                dati = b""
                for blocco in r.iter_bytes():
                    dati += blocco
                    if len(dati) > MAX_BYTE:
                        raise HTTPException(422, "Pagina troppo grande")
                tipo = r.headers.get("content-type", "")
                return url, tipo, dati.decode(r.encoding or "utf-8", errors="replace")
    raise HTTPException(422, "Troppi reindirizzamenti")


# ------------------------------------------------------------------ testo di una pagina
class _Lettore(HTMLParser):
    SALTA = {"script", "style", "noscript", "svg", "template", "nav", "footer", "form", "iframe"}
    BLOCCHI = {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
               "table", "ul", "ol", "header", "main", "td", "th", "dd", "dt"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parti: List[str] = []
        self.link: List[str] = []
        self.titolo = ""
        self._salta = 0
        self._in_titolo = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SALTA:
            self._salta += 1
        if tag == "title":
            self._in_titolo = True
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.link.append(href)
        if tag in self.BLOCCHI:
            self.parti.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SALTA and self._salta:
            self._salta -= 1
        if tag == "title":
            self._in_titolo = False
        if tag in self.BLOCCHI:
            self.parti.append("\n")

    def handle_data(self, data):
        if self._in_titolo:
            self.titolo += data
            return
        if not self._salta:
            self.parti.append(data)


def leggi_html(html: str) -> Dict[str, Any]:
    p = _Lettore()
    try:
        p.feed(html)
    except Exception:          # HTML rotto: si tiene quello che si e' letto
        pass
    righe = [re.sub(r"[ \t\r\f\v]+", " ", r).strip() for r in "".join(p.parti).split("\n")]
    testo = "\n".join(r for r in righe if r)
    return {"titolo": re.sub(r"\s+", " ", p.titolo).strip()[:160], "testo": testo, "link": p.link}


# ------------------------------------------------------------------ scoperta delle pagine
def _robots(base: str) -> RobotFileParser:
    rp = RobotFileParser()
    try:
        _, _, testo = scarica(urljoin(base, "/robots.txt"))
        rp.parse(testo.splitlines())
    except HTTPException:
        rp.parse([])
    return rp


def _sitemap(url: str, livello: int = 0) -> List[str]:
    _, _, testo = scarica(url)
    if "<!DOCTYPE" in testo[:2000].upper() or "<!ENTITY" in testo[:5000].upper():
        raise HTTPException(422, "Mappa del sito non valida")
    try:
        radice = ET.fromstring(testo.encode("utf-8", errors="replace"))
    except ET.ParseError:
        raise HTTPException(422, "Mappa del sito non leggibile")
    ns = lambda t: t.split("}")[-1]                     # noqa: E731
    pagine, figlie = [], []
    for el in radice:
        loc = next((c.text.strip() for c in el if ns(c.tag) == "loc" and c.text), None)
        if not loc:
            continue
        (figlie if ns(radice.tag) == "sitemapindex" else pagine).append(loc)
    if livello < 1:
        for f in figlie[:10]:
            pagine += _sitemap(f, livello + 1)
    return pagine


def _stesso_sito(a: str, b: str) -> bool:
    ha, hb = urlparse(a).hostname or "", urlparse(b).hostname or ""
    return ha.removeprefix("www.") == hb.removeprefix("www.")


def scopri(indirizzo: str) -> Dict[str, Any]:
    """Le pagine del sito: dalla mappa se c'e' (o se l'indirizzo E' la mappa), altrimenti dai
    collegamenti della pagina indicata."""
    indirizzo = (indirizzo or "").strip()
    if not re.match(r"^https?://", indirizzo):
        indirizzo = "https://" + indirizzo
    indirizzo = urldefrag(indirizzo)[0]
    rp = _robots(indirizzo)
    fonte, pagine = "collegamenti", []
    candidati = [indirizzo] if indirizzo.lower().endswith(".xml") else [urljoin(indirizzo, "/sitemap.xml")]
    for mappa in candidati:
        try:
            pagine = _sitemap(mappa)
            fonte = "mappa del sito"
            break
        except HTTPException:
            pagine = []
    if not pagine:
        _, _, html = scarica(indirizzo)
        letto = leggi_html(html)
        pagine = [indirizzo] + [urldefrag(urljoin(indirizzo, h))[0] for h in letto["link"]]
    visti, out = set(), []
    for u in pagine:
        u = u.strip()
        if not re.match(r"^https?://", u) or not _stesso_sito(u, indirizzo) or u in visti:
            continue
        if re.search(r"\.(jpg|jpeg|png|gif|webp|svg|zip|mp4|mp3|css|js)(\?|$)", u, re.I):
            continue
        visti.add(u)
        out.append({"url": u, "consentita": rp.can_fetch(AGENTE, u)})
        if len(out) >= MAX_PAGINE_SCOPERTE:
            break
    return {"sito": indirizzo, "fonte": fonte, "pagine": out}


# ------------------------------------------------------------------ archivio
def _siti_path(cliente: str) -> str:
    return os.path.join(docs.client_dir(cliente), "siti.json")


def leggi_siti(cliente: str) -> List[Dict[str, Any]]:
    try:
        with open(_siti_path(cliente), encoding="utf-8") as stream:
            dati = json.load(stream)
        return dati if isinstance(dati, list) else []
    except (OSError, ValueError):
        return []


def _salva_siti(cliente: str, siti: List[Dict[str, Any]]) -> None:
    os.makedirs(docs.client_dir(cliente), exist_ok=True)
    docs._write_json_atomic(_siti_path(cliente), siti)


def _cambiamenti(vecchio: str, nuovo: str, massimo: int = 6) -> List[str]:
    out = []
    for r in difflib.unified_diff(vecchio.splitlines(), nuovo.splitlines(), lineterm="", n=0):
        if r.startswith(("---", "+++", "@@")):
            continue
        out.append(("tolto: " if r.startswith("-") else "aggiunto: ") + r[1:].strip()[:200])
        if len(out) >= massimo:
            break
    return out


def _attuale(cliente: str, url: str) -> Optional[Dict[str, Any]]:
    return next((i for i in docs.read_manifest(cliente) if i.get("sito_url") == url
                 and i.get("stato") in ("attivo", "da_confermare")), None)


def leggi_pagina(cliente: str, url: str, chi: str = "") -> Dict[str, Any]:
    """Una pagina: nuova, invariata o cambiata (versione nuova, la vecchia nello storico)."""
    finale, tipo, html = scarica(url)
    if "html" not in tipo.lower() and not html.lstrip().startswith("<"):
        return {"url": url, "esito": "saltata", "motivo": "non e' una pagina web"}
    letto = leggi_html(html)
    if len(letto["testo"]) < MIN_TESTO:
        return {"url": url, "esito": "saltata",
                "motivo": "pochissimo testo: pagina vuota o costruita solo con JavaScript"}
    testo = letto["testo"]
    impronta = hashlib.sha256(testo.encode()).hexdigest()
    vecchio = _attuale(cliente, url)
    if vecchio and vecchio.get("impronta") == impronta:
        with docs._WRITE_LOCK:
            manifest = docs.read_manifest(cliente)
            for i in manifest:
                if i.get("id") == vecchio["id"]:
                    i["sito_letto_il"] = int(time.time())
            docs._write_json_atomic(docs.manifest_path(cliente), manifest)
        return {"url": url, "esito": "invariata", "id": vecchio["id"]}
    intestazione = f"Pagina del sito: {url}\nLetta il: {time.strftime('%d/%m/%Y')}\n\n"
    item = docs.aggiungi_testo(cliente, titolo=letto["titolo"] or url, testo=intestazione + testo, tipo="sito",
                               esterno=f"sito:{url}:{impronta[:16]}:{time.time_ns()}", chi=chi or "sito", origine="sito")
    cambi = _cambiamenti(_testo_di(vecchio), intestazione + testo) if vecchio else []
    with docs._WRITE_LOCK:
        manifest = docs.read_manifest(cliente)
        for i in manifest:
            if i.get("id") == item["id"]:
                i.update({"sito_url": url, "sito_letto_il": int(time.time()), "impronta": impronta})
                if vecchio:
                    i["sostituisce"] = vecchio["id"]
                    i["versione"] = int(vecchio.get("versione") or 1) + 1
                    i["storia"].append({"quando": int(time.time()), "chi": "sito",
                                        "cosa": "pagina cambiata sul sito", "cambi": cambi})
            elif vecchio and i.get("id") == vecchio["id"]:
                i.update({"stato": "sostituito", "sostituito_da": item["id"]})
                i["storia"].append({"quando": int(time.time()), "chi": "sito",
                                    "cosa": "sostituito dalla versione nuova della pagina"})
        docs._write_json_atomic(docs.manifest_path(cliente), manifest)
    if docs.on_change:
        docs.on_change(cliente)
    return {"url": url, "esito": "cambiata" if vecchio else "nuova", "id": item["id"], "cambi": cambi}


def _testo_di(item: Optional[Dict[str, Any]]) -> str:
    if not item:
        return ""
    try:
        with open(item.get("extracted_path") or "", encoding="utf-8") as stream:
            return "\n".join(c.get("text", "") for c in json.load(stream))
    except (OSError, ValueError):
        return ""


def segui(cliente: str, sito: str, pagine: List[str], frequenza: int, chi: str = "") -> Dict[str, Any]:
    if frequenza not in FREQUENZE:
        raise HTTPException(400, "Frequenza non valida")
    pagine = [p for p in dict.fromkeys(pagine) if re.match(r"^https?://", p or "") and _stesso_sito(p, sito)]
    if not pagine:
        raise HTTPException(400, "Scegli almeno una pagina del sito")
    if len(pagine) > MAX_PAGINE_SITO:
        raise HTTPException(400, f"Al massimo {MAX_PAGINE_SITO} pagine per sito")
    with _LOCK:
        siti = [s for s in leggi_siti(cliente) if s.get("sito") != sito]
        siti.append({"sito": sito, "pagine": pagine, "frequenza_giorni": frequenza, "creato": int(time.time()),
                     "ultimo": 0})
        _salva_siti(cliente, siti)
    return aggiorna(cliente, sito, chi)


def aggiorna(cliente: str, sito: str, chi: str = "") -> Dict[str, Any]:
    s = next((x for x in leggi_siti(cliente) if x.get("sito") == sito), None)
    if not s:
        raise HTTPException(404, "Sito non seguito")
    rp = _robots(sito)
    esiti = []
    for url in s["pagine"]:
        if not rp.can_fetch(AGENTE, url):
            esiti.append({"url": url, "esito": "saltata", "motivo": "il sito chiede di non leggerla (robots.txt)"})
            continue
        try:
            esiti.append(leggi_pagina(cliente, url, chi))
        except HTTPException as error:
            esiti.append({"url": url, "esito": "errore", "motivo": str(error.detail)})
        except Exception as error:          # una pagina guasta non ferma le altre
            esiti.append({"url": url, "esito": "errore", "motivo": type(error).__name__})
    conta = {k: sum(1 for e in esiti if e["esito"] == k) for k in ("nuova", "cambiata", "invariata", "saltata", "errore")}
    with _LOCK:
        siti = leggi_siti(cliente)
        for x in siti:
            if x.get("sito") == sito:
                x.update({"ultimo": int(time.time()), "esito": conta})
        _salva_siti(cliente, siti)
    print(f"[SITO] {cliente} {sito}: {conta}")
    return {"sito": sito, **conta, "pagine": esiti}


def smetti(cliente: str, sito: str) -> None:
    """Il sito non si rilegge piu'. Le pagine gia' lette restano nei Documenti."""
    with _LOCK:
        _salva_siti(cliente, [s for s in leggi_siti(cliente) if s.get("sito") != sito])


def da_aggiornare(ora: Optional[float] = None) -> List[Tuple[str, str]]:
    ora = ora or time.time()
    radice = docs.upload_root()
    out = []
    try:
        clienti = os.listdir(radice)
    except OSError:
        return out
    for cliente in clienti:
        for s in leggi_siti(cliente):
            if ora - int(s.get("ultimo") or 0) >= int(s.get("frequenza_giorni") or 7) * 86400:
                out.append((cliente, s["sito"]))
    return out


def avvia_aggiornamenti(ogni_s: int = 3600) -> None:
    """Un controllo ogni ora: i siti scaduti si rileggono in sottofondo."""
    def giro() -> None:
        while True:
            time.sleep(ogni_s)
            for cliente, sito in da_aggiornare():
                try:
                    aggiorna(cliente, sito, "aggiornamento automatico")
                except Exception as error:
                    print(f"[SITO] aggiornamento non riuscito {cliente} {sito}: {type(error).__name__}")
    threading.Thread(target=giro, daemon=True, name="sito-aggiornamenti").start()


# ------------------------------------------------------------------ rotte
@router.get("/{tenant}/sito")
async def sito_elenco(tenant: str, spazio: str = Query("azienda", pattern="^(azienda|privato)$"),
                      x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    _, cliente = docs._verified_client(tenant, x_applaudi_tenant_key, spazio)
    return {"siti": leggi_siti(cliente), "frequenze": FREQUENZE}


@router.post("/{tenant}/sito/scopri")
async def sito_scopri(tenant: str, dati: Dict[str, Any] = Body(...),
                      x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    docs._verified_client(tenant, x_applaudi_tenant_key, str(dati.get("spazio") or "azienda"))
    return await run_in_threadpool(scopri, str(dati.get("url") or ""))


@router.post("/{tenant}/sito/importa")
async def sito_importa(tenant: str, dati: Dict[str, Any] = Body(...),
                       x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    verified, cliente = docs._verified_client(tenant, x_applaudi_tenant_key, str(dati.get("spazio") or "azienda"))
    try:
        frequenza = int(dati.get("frequenza_giorni") or 7)
    except (TypeError, ValueError):
        raise HTTPException(400, "Frequenza non valida")
    pagine = dati.get("pagine") if isinstance(dati.get("pagine"), list) else []
    return await run_in_threadpool(segui, cliente, str(dati.get("sito") or ""), [str(p) for p in pagine],
                                   frequenza, verified)


@router.post("/{tenant}/sito/aggiorna")
async def sito_aggiorna(tenant: str, dati: Dict[str, Any] = Body(...),
                        x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    verified, cliente = docs._verified_client(tenant, x_applaudi_tenant_key, str(dati.get("spazio") or "azienda"))
    return await run_in_threadpool(aggiorna, cliente, str(dati.get("sito") or ""), verified)


@router.post("/{tenant}/sito/smetti")
async def sito_smetti(tenant: str, dati: Dict[str, Any] = Body(...),
                      x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    _, cliente = docs._verified_client(tenant, x_applaudi_tenant_key, str(dati.get("spazio") or "azienda"))
    smetti(cliente, str(dati.get("sito") or ""))
    return {"ok": True}
