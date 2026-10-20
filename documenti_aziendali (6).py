"""Documenti aziendali di A'PLAUD'I: archivio, scheda della fonte, validita', registro.

Principio: nessun ordine in ingresso, massimo ordine in uscita. L'ordine si mette
UNA volta, all'ingresso, con la SCHEDA DELLA FONTE; da li' in poi decide il codice,
non il modello.

1. I documenti NON entrano nell'indice del catalogo. Restano in un archivio separato
   per azienda e arrivano al modello come blocco a parte: il catalogo resta identico.
2. Ogni documento ha una scheda: tipo, titolo, data, validita' (dal / fino al),
   "sostituisce", stato, riservatezza, impronta, storia delle modifiche.
3. Ciclo di vita: caricato -> DA CONFERMARE (non usato) -> ATTIVO -> SOSTITUITO o
   ARCHIVIATO. Niente si cancella per scadenza: esce dalle risposte, resta nello storico.
4. La validita' si controlla A OGNI DOMANDA con la data di oggi (ora italiana): una
   promozione nasce e muore da sola.
5. Riservato = mai su WhatsApp.
6. Registro: per ogni risposta, quando, canale, domanda, fonti usate con la versione.
   Serve per le contestazioni ("cosa vi abbiamo risposto il 10 ottobre, e su che base").
7. Uscita tutelata (19-20ott2026): niente esce senza certificatore, come niente entra.
   "Togli" sposta nel CESTINO con un motivo obbligatorio: il documento esce SUBITO dalle
   risposte ma il file resta. Si ripristina, oppure si DISTRUGGE con il PIN del titolare
   e (nello spazio aziendale) solo dopo CESTINO_GIORNI, salvo richiesta privacy. Troppe
   rimozioni in un'ora bloccano le rimozioni (freno contro chi agisce per rabbia). Ogni
   operazione va nel registro delle operazioni, che nessuna rotta permette di cancellare.

Sicurezza: l'azienda la decide il server (chiave del tenant + mappa esplicita
APPLAUDI_TENANT_CLIENT_MAP), mai il browser; un tenant senza archivio dichiarato non
puo' caricare. Persistenza: DOCUMENT_UPLOAD_ROOT su disco persistente.
"""
from __future__ import annotations

import contextvars
import csv
import datetime as dt
import email
import email.policy
import hashlib
import html as html_lib
import io
import json
import os
import re
import shutil
import threading
import time
import unicodedata
import uuid
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Body, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

try:
    from zoneinfo import ZoneInfo
    _ROMA = ZoneInfo("Europe/Rome")
except Exception:          # pragma: no cover - zoneinfo assente: si usa l'ora del server
    _ROMA = None

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md", ".json", ".eml"}
TIPI = {
    "listino": "Listino", "promozione": "Promozione", "scheda_tecnica": "Scheda tecnica",
    "manuale": "Manuale", "contratto": "Contratto", "polizza": "Polizza", "mail": "Mail",
    "registrazione": "Registrazione", "altro": "Altro",
    "immagine": "Immagine", "video": "Video", "disegno": "Disegno / PDF",
}
# Foto, video e disegni: entrano SOLO con il loro TRIGGER, il testo che dice cosa sono
# ("Antonio, quello della telefonata del 15/10"; "Fiat targata JK345PT"). Si cerca il
# trigger; il file arriva come allegato alla risposta. Il tipo si riconosce dal contenuto
# del file (firma dei primi byte), non dal nome.
TIPI_MEDIA = ("immagine", "video", "disegno")
MEDIA_EXT = {".jpg": ("image/jpeg", "immagine"), ".jpeg": ("image/jpeg", "immagine"),
             ".png": ("image/png", "immagine"), ".mp4": ("video/mp4", "video"),
             ".3gp": ("video/3gpp", "video"), ".pdf": ("application/pdf", "disegno")}
TRIGGER_MIN = 8


def firma_media(head: bytes, mime: str) -> bool:
    """I primi byte devono corrispondere al tipo dichiarato dall'estensione."""
    if mime == "image/jpeg":
        return head[:3] == b"\xff\xd8\xff"
    if mime == "image/png":
        return head[:8] == b"\x89PNG\r\n\x1a\n"
    if mime in ("video/mp4", "video/3gpp"):
        return head[4:8] == b"ftyp"
    if mime == "application/pdf":
        return head[:5] == b"%PDF-"
    return False
STATI = ("da_confermare", "attivo", "sostituito", "archiviato", "cestinato")
MOTIVI = {
    "errore_caricamento": "Errore di caricamento",
    "superato": "Superato da un documento più recente",
    "non_veritiero": "Contenuto non veritiero o sbagliato",
    "riservato_errore": "Contenuto riservato caricato per errore",
    "privacy": "Richiesta di cancellazione di dati personali",
    "altro": "Altro (spiegato nella nota)",
}
EVIDENCE_PAGES = 6            # sezioni di documenti mandate al modello
EVIDENCE_CHARS = 3500         # per sezione: il catalogo resta la parte principale
SECTION_HEADER = "DAI DOCUMENTI AZIENDALI"
_WRITE_LOCK = threading.Lock()

# Contesto della domanda in corso (una domanda = un thread, api_ask non ha await):
# canale ("web" o "whatsapp") e fonti effettivamente mandate al modello.
contesto = threading.local()
# a chi va il consumo del modello in questa domanda (archivio): lo imposta chi risponde.
# ContextVar e non threading.local: passa anche nei thread di run_in_threadpool.
consumo_cliente: "contextvars.ContextVar[str]" = contextvars.ContextVar("consumo_cliente", default="")
on_registra: Optional[Callable[[str, str], None]] = None      # (archivio, canale): una domanda

# ------------------------------------------------------------------ collegamenti
# Impostati da app.py all'avvio (stesso schema di whatsapp_gateway).
tenant_verifier: Optional[Callable[[str, Optional[str]], str]] = None
client_exists: Optional[Callable[[str], bool]] = None
default_client: Optional[Callable[[], str]] = None
on_change: Optional[Callable[[str], None]] = None
# verifica del PIN INTERO del titolare (impostato da spazio_privato): (tenant, pin) -> bool
verifica_pin: Optional[Callable[[str, str], bool]] = None
# clienti della console del gestore (anagrafica.py): archivio e modalita' (Render vince)
archivio_extra: Optional[Callable[[str], Optional[str]]] = None
modalita_extra: Optional[Callable[[str], Optional[str]]] = None
e_catalogo: Callable[[str], bool] = lambda cliente: True    # l'archivio ha un catalogo accanto?


def oggi() -> dt.date:
    """La data di oggi in Italia: e' lei che fa nascere e morire una promozione."""
    return dt.datetime.now(_ROMA).date() if _ROMA else dt.date.today()


def canale() -> str:
    return getattr(contesto, "canale", "") or "web"


def upload_root() -> str:
    return os.path.abspath(os.getenv("DOCUMENT_UPLOAD_ROOT", "").strip()
                           or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "uploaded_documents"))


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(low, min(value, high))


def disco_persistente() -> bool:
    return bool(os.getenv("DOCUMENT_UPLOAD_ROOT", "").strip())


def max_bytes() -> int:
    return _int_env("DOCUMENT_MAX_MB", 30, 1, 100) * 1024 * 1024


def durata_mail_giorni() -> int:
    """Mail e registrazioni valgono questo tempo dalla loro data (poi escono dalle risposte)."""
    return _int_env("DURATA_MAIL_GIORNI", 730, 1, 36500)


def registro_giorni() -> int:
    return _int_env("REGISTRO_GIORNI", 730, 1, 36500)


def cestino_giorni() -> int:
    """Giorni di cestino prima che un documento AZIENDALE si possa distruggere."""
    return _int_env("CESTINO_GIORNI", 30, 0, 3650)


def rimozioni_ora() -> int:
    """Rimozioni consentite in un'ora nello spazio aziendale: oltre, si blocca."""
    return _int_env("RIMOZIONI_ORA", 5, 1, 1000)


def _clean_id(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "", (value or "").strip().lower())[:40]


# Lo SPAZIO PRIVATO del titolare (chi possiede la chiave del tenant): un archivio suo,
# separato da quello dell'azienda, con lo stesso formato. Il nome lo decide solo il
# server a partire dal tenant verificato; nessuna mappa puo' puntare a uno spazio privato.
PREFISSO_PRIVATO = "privato-"


def cliente_privato(tenant: str) -> str:
    clean = _clean_id(tenant)[:40 - len(PREFISSO_PRIVATO)]
    if not clean:
        raise HTTPException(400, "Titolare non valido")
    return PREFISSO_PRIVATO + clean


def e_privato(cliente: str) -> bool:
    return (cliente or "").startswith(PREFISSO_PRIVATO)


# MODALITA' del cliente, decisa SOLO dal gestore della piattaforma (APPLAUDI_MODALITA_JSON,
# es. {"roberto":"mix","covolo":"aziendale"}). In un'azienda non esistono aree private,
# tranne quella del titolare e solo se il gestore la abilita:
#   aziendale -> solo l'archivio dell'azienda (predefinito)
#   privato   -> solo lo spazio privato (A'PLAUD'I per una persona)
#   mix       -> azienda + spazio privato del titolare
MODALITA = {"aziendale": ("azienda",), "privato": ("privato",), "mix": ("privato", "azienda")}


def modalita(tenant: str) -> str:
    try:
        mappa = json.loads(os.getenv("APPLAUDI_MODALITA_JSON", "{}") or "{}")
    except ValueError:
        mappa = {}
    valore = str(mappa.get(_clean_id(tenant), "") if isinstance(mappa, dict) else "").strip().lower()
    if valore not in MODALITA and modalita_extra is not None:
        valore = str(modalita_extra(_clean_id(tenant)) or "")
    return valore if valore in MODALITA else "aziendale"


def spazi_ammessi(tenant: str) -> Tuple[str, ...]:
    return MODALITA[modalita(tenant)]


def client_for_tenant(tenant: str) -> str:
    """L'archivio del tenant, deciso solo dalla configurazione del server.

    Mappa esplicita APPLAUDI_TENANT_CLIENT_MAP ({"roberto":"lago"}); poi il tenant stesso
    se e' un cliente con cartella propria o il predefinito. Altrimenti 403: meglio un
    rifiuto chiaro che un documento nell'archivio di un'altra azienda.
    """
    clean = _clean_id(tenant)
    try:
        mapping = json.loads(os.getenv("APPLAUDI_TENANT_CLIENT_MAP", "{}") or "{}")
    except ValueError:
        mapping = {}
    mapped = _clean_id(str(mapping.get(clean, ""))) if isinstance(mapping, dict) else ""
    if mapped and not e_privato(mapped):
        return mapped
    extra = _clean_id(archivio_extra(clean) or "") if archivio_extra is not None else ""
    if extra and not e_privato(extra):
        return extra
    if clean and not e_privato(clean) and ((client_exists and client_exists(clean))
                  or (default_client and clean == default_client())):
        return clean
    raise HTTPException(403, "Per questa azienda l'archivio documenti non e' configurato "
                             "(APPLAUDI_TENANT_CLIENT_MAP).")


# ------------------------------------------------------------------ archivio su disco
def client_dir(cliente: str) -> str:
    return os.path.join(upload_root(), _clean_id(cliente) or "default")


def manifest_path(cliente: str) -> str:
    return os.path.join(client_dir(cliente), "manifest.json")


def registro_path(cliente: str) -> str:
    return os.path.join(client_dir(cliente), "registro.jsonl")


def operazioni_path(cliente: str) -> str:
    return os.path.join(client_dir(cliente), "operazioni.jsonl")


def _normalizza(item: Dict[str, Any]) -> Dict[str, Any]:
    """Schede scritte da versioni precedenti: si completano senza perdere nulla."""
    item.setdefault("titolo", os.path.splitext(item.get("name") or "documento")[0])
    item.setdefault("tipo", "altro")
    item.setdefault("stato", "attivo")
    item.setdefault("riservato", False)
    item.setdefault("versione", 1)
    item.setdefault("storia", [])
    for key in ("data_documento", "valido_dal", "valido_fino", "sostituisce",
                "sostituito_da", "note", "impronta"):
        item.setdefault(key, None)
    return item


def read_manifest(cliente: str) -> List[Dict[str, Any]]:
    try:
        with open(manifest_path(cliente), encoding="utf-8") as stream:
            value = json.load(stream)
        return [_normalizza(i) for i in value if isinstance(i, dict)] if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


def _write_json_atomic(path: str, value: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def safe_name(name: str) -> str:
    base = os.path.basename((name or "documento").replace("\\", "/"))
    stem, ext = os.path.splitext(base)
    stem = re.sub(r"[^A-Za-z0-9._ -]", "_", stem).strip(" ._")[:100] or "documento"
    return stem + ext.lower()


# ------------------------------------------------------------------ date
MESI = {"gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
        "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
        "gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6, "lug": 7, "ago": 8,
        "set": 9, "ott": 10, "nov": 11, "dic": 12}
_D_NUM = r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})"
_D_TXT = r"(\d{1,2})\s+(" + "|".join(sorted(MESI, key=len, reverse=True)) + r")\.?\s+(\d{4})"
_DATA = re.compile(r"(?:" + _D_NUM + r"|" + _D_TXT + r")", re.I)
_FINO = re.compile(r"(fino\s+al|entro\s+il|scade(?:nza)?(?:\s+il)?|valid[ao]\s+fino\s+al|"
                   r"termina\s+il|sino\s+al|\bal)\s*$", re.I)
_DAL = re.compile(r"(dal|a\s+partire\s+dal|decorrenza|in\s+vigore\s+dal|valid[ao]\s+dal)\s*$", re.I)


def _to_date(m: re.Match) -> Optional[dt.date]:
    try:
        if m.group(1):
            d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            d, mth, y = int(m.group(4)), MESI[m.group(5).lower()], int(m.group(6))
        if y < 100:
            y += 2000
        return dt.date(y, mth, d)
    except (ValueError, KeyError, TypeError):
        return None


def parse_date(value: Any) -> Optional[dt.date]:
    """'2026-11-30', '30/11/2026', '30 novembre 2026' -> data; altro -> None."""
    if not value:
        return None
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        pass
    m = _DATA.search(text)
    return _to_date(m) if m else None


def proponi_date(text: str) -> Dict[str, Optional[str]]:
    """Date trovate nel testo, da CONFERMARE a mano: mai usate senza conferma."""
    out: Dict[str, Optional[str]] = {"data_documento": None, "valido_dal": None, "valido_fino": None}
    sample = (text or "")[:20000]
    for m in _DATA.finditer(sample):
        value = _to_date(m)
        if not value:
            continue
        before = sample[max(0, m.start() - 30):m.start()]
        if _FINO.search(before) and not out["valido_fino"]:
            out["valido_fino"] = value.isoformat()
        elif _DAL.search(before) and not out["valido_dal"]:
            out["valido_dal"] = value.isoformat()
        elif not out["data_documento"]:
            out["data_documento"] = value.isoformat()
    return out


def proponi_tipo(name: str, text: str) -> str:
    sample = ((name or "") + " " + (text or "")[:4000]).lower()
    regole = (("mail", r"^\s*(da|from)\s*:|\boggetto\s*:|\bsubject\s*:"),
              ("promozione", r"promo|promozion|offert|sconto|scontat"),
              ("listino", r"listino|price ?list"),
              ("scheda_tecnica", r"scheda tecnica|technical data|datasheet"),
              ("manuale", r"manuale|istruzion|montaggio|manutenzion"),
              ("contratto", r"contratto|accordo quadro|condizioni generali"),
              ("polizza", r"polizza"))
    for tipo, pattern in regole:
        if re.search(pattern, sample, re.M):
            return tipo
    return "altro"


# ------------------------------------------------------------------ estrazione
def _cell(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def _eml(path: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    with open(path, "rb") as stream:
        msg = email.message_from_binary_file(stream, policy=email.policy.default)
    meta: Dict[str, Any] = {"oggetto": str(msg.get("subject") or "").strip(),
                            "mittente": str(msg.get("from") or "").strip(),
                            "destinatari": str(msg.get("to") or "").strip()}
    try:
        meta["data"] = parsedate_to_datetime(str(msg.get("date"))).date().isoformat()
    except (TypeError, ValueError, IndexError):
        meta["data"] = None
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body is not None else ""
    if body is not None and body.get_content_type() == "text/html":
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", text))
    head = (f"Da: {meta['mittente']}\nA: {meta['destinatari']}\n"
            f"Data: {meta['data'] or 'non indicata'}\nOggetto: {meta['oggetto']}\n\n")
    text = head + re.sub(r"[ \t]+\n", "\n", text).strip()
    return [{"label": "mail", "text": text}], meta


def extract(path: str, extension: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Testo diviso per pagina/foglio + dati certi del file (per le mail .eml)."""
    chunks: List[Dict[str, Any]] = []
    meta: Dict[str, Any] = {}
    if extension == ".pdf":
        from pypdf import PdfReader
        for number, page in enumerate(PdfReader(path).pages, 1):
            text = (page.extract_text() or "").strip()
            if text:
                chunks.append({"label": f"pagina {number}", "text": text})
    elif extension == ".docx":
        from docx import Document
        doc = Document(path)
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [_cell(c.text) for c in row.cells]
                if any(cells):
                    parts.append("\t".join(cells))
        text = "\n".join(parts).strip()
        if text:
            chunks.append({"label": "documento", "text": text})
    elif extension == ".xlsx":
        from openpyxl import load_workbook
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            for sheet in book.worksheets:
                lines = []
                for row in sheet.iter_rows(values_only=True):
                    values = [_cell(v) for v in row]
                    while values and not values[-1]:
                        values.pop()
                    if any(values):
                        lines.append("\t".join(values))
                if lines:
                    chunks.append({"label": f"foglio {sheet.title}", "text": "\n".join(lines)})
        finally:
            book.close()
    elif extension == ".csv":
        decoded = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(decoded[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        lines = ["\t".join(_cell(v) for v in row)
                 for row in csv.reader(io.StringIO(decoded), dialect) if any(row)]
        if lines:
            chunks.append({"label": "tabella", "text": "\n".join(lines)})
    elif extension == ".eml":
        chunks, meta = _eml(path)
    else:
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace").strip()
        if extension == ".json":
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except ValueError:
                pass
        if text:
            chunks.append({"label": "documento", "text": text})
    out: List[Dict[str, Any]] = []
    for chunk in chunks:           # sezioni lunghe divise: la ricerca sceglie il passaggio
        text = chunk["text"]
        if len(text) <= EVIDENCE_CHARS:
            out.append(chunk)
            continue
        for n, start in enumerate(range(0, len(text), EVIDENCE_CHARS), 1):
            out.append({"label": f"{chunk['label']} · parte {n}",
                        "text": text[start:start + EVIDENCE_CHARS]})
    return out, meta


# ------------------------------------------------------------------ validita'
def periodo(item: Dict[str, Any]) -> Tuple[Optional[dt.date], Optional[dt.date]]:
    """Inizio e fine di validita' effettivi della fonte."""
    inizio = parse_date(item.get("valido_dal"))
    fine = parse_date(item.get("valido_fino"))
    # solo le mail scadono da sole: una registrazione e' memoria, non un'offerta
    if fine is None and item.get("tipo") == "mail":
        base = parse_date(item.get("data_documento"))
        if base is None and item.get("uploaded_at"):
            base = dt.date.fromtimestamp(int(item["uploaded_at"]))
        if base:
            fine = base + dt.timedelta(days=durata_mail_giorni())
    return inizio, fine


def situazione(item: Dict[str, Any], giorno: Optional[dt.date] = None) -> str:
    """da_confermare | in_arrivo | vigente | scaduto | sostituito | archiviato."""
    stato = item.get("stato") or "attivo"
    if stato != "attivo":
        return stato
    giorno = giorno or oggi()
    inizio, fine = periodo(item)
    if inizio and giorno < inizio:
        return "in_arrivo"
    if fine and giorno > fine:
        return "scaduto"
    return "vigente"


def _fmt(value: Any) -> str:
    d = parse_date(value)
    return d.strftime("%d/%m/%Y") if d else ""


def etichetta_fonte(item: Dict[str, Any]) -> str:
    parti = [f"«{item.get('titolo') or item.get('name')}»",
             TIPI.get(item.get("tipo") or "altro", "Altro")]
    if item.get("data_documento"):
        parti.append(f"del {_fmt(item['data_documento'])}")
    # si mostra solo la validita' SCRITTA nella scheda, non quella calcolata d'ufficio
    inizio, fine = parse_date(item.get("valido_dal")), parse_date(item.get("valido_fino"))
    if inizio or fine:
        parti.append("valido" + (f" dal {inizio.strftime('%d/%m/%Y')}" if inizio else "")
                     + (f" al {fine.strftime('%d/%m/%Y')}" if fine else ""))
    return " · ".join(parti)


def titolo_breve(testo: str, limite: int = 80) -> str:
    """Un titolo ricavato da un testo lungo si taglia a fine parola, con i puntini."""
    testo = re.sub(r"\s+", " ", testo or "").strip()
    if len(testo) <= limite:
        return testo
    taglio = testo[:limite].rsplit(" ", 1)[0].rstrip(" ,;:.-")
    return (taglio or testo[:limite]) + "…"


# ------------------------------------------------------------------ significato
# Ricerca per significato (embeddings). Il "motore del significato" e' iniettato da app.py:
# oggi OpenAI; domani un modello in casa, senza cambiare questo file. Se manca, si cerca
# solo per parole: il sistema funziona lo stesso, solo meno fine.
embedder: Optional[Callable[[List[str]], List[List[float]]]] = None
SOGLIA_SIGNIFICATO_DEFAULT = 0.35
_QUERY_CACHE: Dict[str, Any] = {}


def soglia_significato() -> float:
    try:
        return max(0.0, min(1.0, float(os.getenv("DOCUMENTI_SOGLIA_SIGNIFICATO",
                                                  str(SOGLIA_SIGNIFICATO_DEFAULT)))))
    except ValueError:
        return SOGLIA_SIGNIFICATO_DEFAULT


def _norm(vec: List[float]) -> List[float]:
    total = sum(x * x for x in vec) ** 0.5 or 1.0
    return [x / total for x in vec]


def _dot(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def vettori_path(item: Dict[str, Any]) -> str:
    return os.path.join(os.path.dirname(item.get("extracted_path") or ""), "vettori.json")


def calcola_significato(item: Dict[str, Any]) -> bool:
    """Calcola e salva i vettori di significato di un documento confermato.

    Mai per i riservati: il loro testo non esce verso il servizio del significato.
    Restituisce True se i vettori ci sono. Un errore non blocca il documento: resta
    cercabile per parole."""
    path = vettori_path(item)
    if item.get("riservato"):
        if os.path.exists(path):
            os.remove(path)
        return False
    if embedder is None or not item.get("extracted_path"):
        return False
    try:
        with open(item["extracted_path"], encoding="utf-8") as stream:
            chunks = json.load(stream)
        testi = [str(c.get("text") or "")[:8000] for c in chunks]
        vettori: List[List[float]] = []
        for start in range(0, len(testi), 64):
            vettori.extend(embedder(testi[start:start + 64]))
        if len(vettori) != len(testi):
            raise ValueError("numero di vettori diverso dalle sezioni")
        _write_json_atomic(path, [[round(x, 6) for x in _norm(v)] for v in vettori])
        print(f"[DOCUMENTI] significato calcolato: {len(vettori)} sezioni")
        return True
    except Exception as error:
        print(f"[DOCUMENTI] significato non calcolato ({type(error).__name__}): "
              "il documento resta cercabile per parole")
        return False


def _vettore_domanda(question: str) -> Optional[List[float]]:
    if embedder is None or not question.strip():
        return None
    key = question.strip()[:2000]
    if key in _QUERY_CACHE:
        return _QUERY_CACHE[key]
    try:
        vec = _norm(embedder([key])[0])
    except Exception as error:
        print(f"[DOCUMENTI] significato della domanda non calcolato ({type(error).__name__})")
        return None
    if len(_QUERY_CACHE) > 512:
        _QUERY_CACHE.clear()
    _QUERY_CACHE[key] = vec
    return vec


# ------------------------------------------------------------------ chi puo' rispondere a cosa
# Regole scritte nel codice, non lasciate al modello: una domanda di prezzo la possono
# toccare solo documenti commerciali (caso "rubinetto Manzoni" contro "I promessi sposi").
TIPI_COMMERCIALI = {"listino", "promozione", "contratto"}
_DOMANDA_PREZZO = re.compile(
    r"quant[oaie]\s+cost|\bcost[aoi]\b|\bcosto\b|prezz|listino|scont|\bpromo|offert|"
    r"\bsaldi\b|\beuro\b|€|\btariff|\bpreventiv", re.I)
_DOMANDA_PROMO = re.compile(r"\bpromo|promozion|offert|\bscont|\bsaldi\b", re.I)


def domanda_di_prezzo(question: str) -> bool:
    return bool(_DOMANDA_PREZZO.search(question or ""))


def _e_codice(token: str) -> bool:
    """Un codice prodotto: lettere e cifre insieme (CVZ900), o almeno 5 cifre (211605).
    Un anno (2026) o una quantita' (15) non sono codici."""
    cifre = any(c.isdigit() for c in token)
    lettere = any(c.isalpha() for c in token)
    return (cifre and lettere and len(token) >= 4) or (token.isdigit() and len(token) >= 5)


# ------------------------------------------------------------------ pagine e evidenze
def _senza_accenti(parola: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", parola) if unicodedata.category(c) != "Mn")


def _parole(tokenizer: Callable[[str], Iterable[str]], testo: str) -> List[str]:
    """Le parole per la ricerca nell'archivio, senza accenti da entrambe le parti:
    «mercoledì» nella domanda trova «mercoledi» scritto nel trigger (collaudo 20ott2026)."""
    return [_senza_accenti(t) for t in tokenizer(testo)]


def load_pages(cliente: str, tokenizer: Callable[[str], Iterable[str]]) -> List[Dict[str, Any]]:
    """Sezioni dei documenti ATTIVI (confermati, non sostituiti, non archiviati).

    La validita' nel tempo NON si filtra qui ma a ogni domanda (evidence): una
    promozione caricata oggi e valida da domani deve accendersi da sola domani.
    """
    pages: List[Dict[str, Any]] = []
    for item in read_manifest(cliente):
        if item.get("stato") != "attivo":
            continue
        extracted = item.get("extracted_path") or ""
        if not extracted or not os.path.isfile(extracted):
            continue
        try:
            with open(extracted, encoding="utf-8") as stream:
                chunks = json.load(stream)
        except (OSError, ValueError):
            continue
        vettori: List[Any] = []
        if not item.get("riservato") and os.path.isfile(vettori_path(item)):
            try:
                with open(vettori_path(item), encoding="utf-8") as stream:
                    vettori = json.load(stream)
            except (OSError, ValueError):
                vettori = []
        scheda = {k: item.get(k) for k in ("id", "name", "titolo", "tipo", "data_documento",
                                           "valido_dal", "valido_fino", "riservato", "versione",
                                           "stato", "uploaded_at", "media_path", "media_mime",
                                           "media_nome", "media_bytes", "collegati")}
        chunks = chunks if isinstance(chunks, list) else []
        if len(vettori) != len(chunks):
            vettori = []
        for n, chunk in enumerate(chunks):
            text = str(chunk.get("text") or "").strip()
            if not text:
                continue
            tokens = _parole(tokenizer, text)
            counts: Dict[str, int] = {}
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
            pages.append({"scheda": scheda, "label": str(chunk.get("label") or ""),
                          "text": text, "token_counts": counts, "token_set": set(tokens),
                          "vec": vettori[n] if vettori else None})
    return pages


def utilizzabili(pages: List[Dict[str, Any]], giorno: Optional[dt.date] = None,
                 su_canale: Optional[str] = None) -> List[Tuple[Dict[str, Any], str]]:
    """Le sezioni che oggi, su questo canale, possono entrare in una risposta."""
    giorno = giorno or oggi()
    su_canale = su_canale or canale()
    out = []
    for page in pages:
        scheda = page["scheda"]
        if scheda.get("riservato") and su_canale == "whatsapp":
            continue
        stato = situazione(scheda, giorno)
        if stato in ("vigente", "in_arrivo"):
            out.append((page, stato))
    return out


def firma_attivi(pages: List[Dict[str, Any]]) -> Tuple:
    """Cosa vale oggi su questo canale: entra nella chiave della memoria delle risposte,
    cosi' quando una promozione nasce o muore la risposta si ricalcola."""
    return tuple(sorted({(p["scheda"]["id"], p["scheda"].get("versione"), stato)
                         for p, stato in utilizzabili(pages)}))


def scegli(question: str, pages: List[Dict[str, Any]],
           tokenizer: Callable[[str], Iterable[str]], max_pages: int = EVIDENCE_PAGES,
           regola_prezzo: bool = True, min_parole: int = 2
           ) -> List[Tuple[float, Dict[str, Any], str, str]]:
    """Le sezioni pertinenti: (punteggio, sezione, stato, motivo). Regole, nell'ordine:

    1. validita' e canale (utilizzabili);
    2. domanda di prezzo -> solo documenti commerciali (listino, promozione, contratto);
    3. domanda sulle promozioni -> tutte le promozioni vigenti entrano per tipo;
    4. un codice citato alla lettera basta;
    5. altrimenti almeno DUE parole significative in comune, oppure significato vicino
       (sopra soglia) con almeno una parola in comune.

    Lo spazio privato non ha catalogo accanto: la regola 2 non vale (un prezzo detto in
    una telefonata e' proprio cio' che si cerca) e basta UNA parola in comune.
    """
    candidates = utilizzabili(pages)
    if not candidates:
        return []
    query = set(_parole(tokenizer, question))
    prezzo = regola_prezzo and domanda_di_prezzo(question)
    promo = bool(_DOMANDA_PROMO.search(question or ""))
    if prezzo:
        candidates = [(p, s) for p, s in candidates if p["scheda"].get("tipo") in TIPI_COMMERCIALI]
    if not candidates:
        return []
    qvec = _vettore_domanda(question) if any(p.get("vec") for p, _ in candidates) else None
    soglia = soglia_significato()
    scelte = []
    for page, stato in candidates:
        overlap = query & page["token_set"]
        codici = {t for t in overlap if _e_codice(t)}
        sem = _dot(qvec, page["vec"]) if qvec is not None and page.get("vec") else None
        lessico = len(overlap) * 5 + min(sum(page["token_counts"].get(t, 0) for t in overlap), 30) \
            + sum(2 for t in overlap if len(t) >= 7)
        motivo = ""
        if codici:
            motivo = "codice"
        elif promo and page["scheda"].get("tipo") == "promozione":
            motivo = "promozione"
        elif len(overlap) >= max(1, min_parole):
            motivo = "parole"
        elif sem is not None and sem >= soglia and overlap:
            motivo = "significato"
        elif sem is not None and sem >= soglia + 0.15:
            motivo = "significato"
        if not motivo:
            continue
        punteggio = (sem or 0.0) * 100 + lessico + (50 if motivo == "codice" else 0)
        scelte.append((punteggio, page, stato, motivo))
    scelte.sort(key=lambda r: r[0], reverse=True)
    return scelte[:max_pages]


def evidence(question: str, pages: List[Dict[str, Any]],
             tokenizer: Callable[[str], Iterable[str]], max_pages: int = EVIDENCE_PAGES,
             regola_prezzo: bool = True, min_parole: int = 2
             ) -> Tuple[str, List[Dict[str, Any]]]:
    """(testo per il modello, schede usate) delle sezioni pertinenti e valide oggi."""
    parts, used = [], {}
    scelte = scegli(question, pages, tokenizer, max_pages, regola_prezzo, min_parole)
    for punteggio, page, stato, motivo in scelte:
        scheda = page["scheda"]
        avviso = ""
        if stato == "in_arrivo":
            inizio, _ = periodo(scheda)
            avviso = (f"\nNON ANCORA IN VIGORE: vale dal {inizio.strftime('%d/%m/%Y')}. "
                      "Citalo solo come cosa futura, mai come condizione di oggi.")
        parts.append(f"===== DOCUMENTO: {etichetta_fonte(scheda)} · {page['label']} ====={avviso}\n"
                     f"{page['text'][:EVIDENCE_CHARS]}")
        used[scheda["id"]] = scheda
    if scelte and getattr(contesto, "log_riservato", False):
        print(f"[DOCUMENTI] scelte {len(scelte)} sezioni (spazio privato: titoli non scritti nel log)")
    elif scelte:
        print("[DOCUMENTI] scelti: " + "; ".join(
            f"{p['scheda'].get('titolo')} · {p['label']} ({m}, {sc:.0f})" for sc, p, _, m in scelte))
    return "\n\n".join(parts), list(used.values())


def evidence_block(question: str, pages: List[Dict[str, Any]],
                   tokenizer: Callable[[str], Iterable[str]]) -> str:
    """Blocco da aggiungere al messaggio per il modello ('' se non c'e' niente).

    Registra in `contesto.fonti` le schede mandate davvero al modello (per il registro).
    """
    found, used = evidence(question, pages, tokenizer)
    contesto.fonti = used
    if not found:
        return ""
    return (
        f"\n\nDOCUMENTI AZIENDALI CARICATI (oggi e' il {oggi().strftime('%d/%m/%Y')}): fonte "
        "DISTINTA dal catalogo.\nRegole per usarli:\n"
        "1. Rispondi in modo DIRETTO e BREVE a cio' che la domanda chiede, come un buon "
        "commerciale: niente premesse, niente ripetizioni. La parte principale contiene solo i "
        "dati del catalogo, con le pagine, e nessun dato preso da questi documenti. Se la "
        "risposta alla domanda sta in questi documenti (per esempio una promozione), la parte "
        "principale e' al massimo una o due righe con i dati del catalogo che servono al "
        "confronto (per i codici nominati dai documenti trovi la riga di listino nelle SCHEDE E "
        "RIGHE DI LISTINO: usala, il prezzo di listino c'e'). Non scrivere cosa il catalogo NON "
        "contiene, non scrivere MAI 'informazione non trovata', non ripetere nella parte "
        "principale cio' che va nella sezione dei documenti.\n"
        f"2. Se e solo se questi documenti aggiungono, cambiano o confermano qualcosa di "
        f"pertinente alla domanda, chiudi la risposta con una riga che contiene soltanto "
        f"{SECTION_HEADER} e sotto un punto per ogni contributo, in questa forma:\n"
        "- [NUOVO] cosa aggiunge rispetto al catalogo — fonte: «titolo» (data; validita' se "
        "indicata), posizione\n"
        "- [DIVERSO DAL CATALOGO] cosa dice il documento e cosa dice il catalogo (pagina) — "
        "fonte: «titolo» (data; validita'), posizione\n"
        "- [CONFERMA] cosa conferma — fonte: «titolo» (data), posizione\n"
        "Ogni punto e' breve (una o due righe). Un punto per ogni contributo, mai due cose diverse nello stesso punto. Un prezzo, "
        "una misura o un dato che cambia un valore del catalogo e' SEMPRE [DIVERSO DAL "
        "CATALOGO]; condizioni, tempi, destinatari o informazioni che il catalogo non ha sono "
        "[NUOVO], ciascuna nel suo punto.\n"
        "3. Non fondere mai un dato del documento con un dato del catalogo: un prezzo "
        "promozionale non sostituisce il prezzo di listino, si mostrano entrambi con le "
        "rispettive fonti. Riporta le condizioni del documento (prodotti, finiture, quantita', "
        "destinatari) con le sue parole.\n"
        "4. Se un documento ha una validita', scrivila sempre accanto al dato.\n"
        f"5. Se i documenti non c'entrano con la domanda, non scrivere la sezione {SECTION_HEADER}.\n"
        "Testo dei documenti:\n" + found)


_SECTION_RE = re.compile(r"(?mi)^[ \t>*#_-]*" + SECTION_HEADER.replace(" ", r"\s+")
                         + r"[ \t*_]*:?[ \t*_]*$")


def split_section(answer: str) -> tuple:
    """(parte del catalogo, sezione documenti). I controlli del motore sono tarati sul
    catalogo e toglierebbero un dato vero del documento solo perche' non e' nel listino:
    la sezione passa a lato e si riattacca alla fine, intatta."""
    text = answer or ""
    match = _SECTION_RE.search(text)
    if not match:
        return text, ""
    body = text[match.end():].strip()
    if not body:
        return text[:match.start()].rstrip(), ""
    return text[:match.start()].rstrip(), SECTION_HEADER + "\n" + body


_NON_TROVATA = "Informazione non trovata nel documento collegato."


def join_section(answer: str, section: str) -> str:
    """Riattacca la sezione. Se c'e', la frase standard "non trovata" del motore (tarata
    sul solo catalogo) contraddirebbe la sezione: diventa un rimando, non una negazione."""
    if not section:
        return answer
    head = (answer or "").replace(_NON_TROVATA, "")
    # anche le varianti ("Informazione non trovata ... per quanto riguarda ...") si tolgono:
    # con la sezione presente sarebbero una contraddizione
    head = re.sub(r"[^.\n]*\b[Ii]nformazion[ei] non trovat[ae]\b[^.\n]*\.?", "", head)
    head = re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", head))
    return head.rstrip() + "\n\n" + section


_WA_MARKS = (("[NUOVO]", "🟢 *NUOVO*"), ("[DIVERSO DAL CATALOGO]", "🟠 *DIVERSO DAL CATALOGO*"),
             ("[CONFERMA]", "🔵 *CONFERMA*"))


def per_whatsapp(answer: str) -> str:
    """WhatsApp non ha colori: simboli e grassetto, stesso significato del web."""
    head, section = split_section(answer)
    if not section:
        return answer
    body = section[len(SECTION_HEADER):]
    for plain, mark in _WA_MARKS:
        body = body.replace(plain, mark)
    return join_section(head, "*" + SECTION_HEADER + "*" + body)


# ------------------------------------------------------------------ registro
_PAGINE = re.compile(r"\b(?:pagin[ae]|pagg?\.)\s*((?:\d{1,4}\s*(?:,|e|-)?\s*)+)", re.I)
_ultima_pulizia: Dict[str, float] = {}


def registra(cliente: str, domanda: str, risposta: str,
             fonti: Optional[List[Dict[str, Any]]] = None, da_memoria: bool = False) -> None:
    """Una riga per risposta: quando, canale, domanda, pagine del catalogo, documenti
    usati (con versione). Non blocca mai la risposta."""
    try:
        pagine = sorted({int(n) for g in _PAGINE.findall(risposta or "")
                         for n in re.findall(r"\d{1,4}", g)})
        head, section = split_section(risposta or "")
        riga = {"quando": dt.datetime.now(_ROMA).isoformat(timespec="seconds") if _ROMA
                else dt.datetime.now().isoformat(timespec="seconds"),
                "canale": canale(), "domanda": (domanda or "")[:2000],
                "pagine_catalogo": pagine[:50],
                "documenti": [{"id": f.get("id"), "titolo": f.get("titolo"),
                               "versione": f.get("versione")} for f in (fonti or [])],
                "sezione_documenti": bool(section), "da_memoria": da_memoria,
                "impronta_risposta": hashlib.sha256((risposta or "").encode()).hexdigest()[:16],
                "risposta": (risposta or "")[:6000]}
        if on_registra is not None:
            on_registra(cliente, canale())
        path = registro_path(cliente)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _WRITE_LOCK:
            with open(path, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(riga, ensure_ascii=False) + "\n")
            if time.time() - _ultima_pulizia.get(path, 0) > 86400:
                _pulisci_registro(path)
                _ultima_pulizia[path] = time.time()
    except Exception as error:         # il registro non deve mai fermare una risposta
        print(f"[DOCUMENTI] registro non scritto: {type(error).__name__}")


def _pulisci_registro(path: str) -> None:
    """Le righe piu' vecchie della durata fissata (REGISTRO_GIORNI) si tolgono."""
    limite = dt.date.today() - dt.timedelta(days=registro_giorni())
    try:
        with open(path, encoding="utf-8") as stream:
            righe = stream.readlines()
    except OSError:
        return
    def data_riga(riga: str) -> dt.date:
        try:
            return parse_date(json.loads(riga).get("quando")) or dt.date.max
        except ValueError:
            return dt.date.max
    tenute = [r for r in righe if data_riga(r) >= limite]
    if len(tenute) != len(righe):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as stream:
            stream.writelines(tenute)
        os.replace(tmp, path)


def leggi_registro(cliente: str, testo: str = "", limite: int = 200) -> List[Dict[str, Any]]:
    try:
        with open(registro_path(cliente), encoding="utf-8") as stream:
            righe = stream.readlines()
    except OSError:
        return []
    out = []
    needle = (testo or "").lower().strip()
    for line in reversed(righe):
        try:
            riga = json.loads(line)
        except ValueError:
            continue
        if needle and needle not in (riga.get("domanda", "") + " " + riga.get("risposta", "")).lower():
            continue
        out.append(riga)
        if len(out) >= limite:
            break
    return out


# ------------------------------------------------------------------ testi da altre fonti
def gia_presente(cliente: str, esterno: str) -> Optional[Dict[str, Any]]:
    return next((i for i in read_manifest(cliente) if i.get("esterno") == esterno), None)


def aggiungi_testo(cliente: str, *, titolo: str, testo: str, tipo: str, esterno: str,
                   data_documento: Optional[str] = None, riservato: bool = False,
                   chi: str = "", origine: str = "", stato: str = "attivo") -> Dict[str, Any]:
    """Mette nell'archivio un testo che arriva da un'altra fonte (es. una registrazione
    PLAUD scelta dall'utente). Entra ATTIVO, perche' l'utente l'ha scelto uno per uno:
    la scelta manuale e' la sua conferma. Non entra due volte (chiave `esterno`)."""
    if tipo not in TIPI:
        raise HTTPException(400, "Tipo di documento non valido")
    testo = (testo or "").strip()
    if not testo:
        raise HTTPException(422, "Nessun testo da archiviare")
    with _WRITE_LOCK:
        doppio = gia_presente(cliente, esterno)
        if doppio:
            raise HTTPException(409, f"Gia' nella conoscenza: «{doppio.get('titolo')}».")
        document_id = uuid.uuid4().hex
        folder = os.path.join(client_dir(cliente), "files", document_id)
        os.makedirs(folder, exist_ok=False)
        chunks = [{"label": f"parte {n}", "text": testo[start:start + EVIDENCE_CHARS]}
                  for n, start in enumerate(range(0, len(testo), EVIDENCE_CHARS), 1)]
        if len(chunks) == 1:
            chunks[0]["label"] = "testo"
        extracted_path = os.path.join(folder, "extracted.json")
        _write_json_atomic(extracted_path, chunks)
        data = parse_date(data_documento)
        item = _normalizza({
            "id": document_id, "name": (origine or "testo") + ".txt", "size": len(testo.encode()),
            "uploaded_at": int(time.time()), "sections": len(chunks),
            "stato": stato if stato in ("attivo", "da_confermare") else "attivo",
            "impronta": hashlib.sha256(testo.encode()).hexdigest(), "esterno": esterno,
            "extracted_path": extracted_path, "titolo": re.sub(r"\s+", " ", titolo or "").strip()[:160]
            or "Testo", "tipo": tipo, "data_documento": data.isoformat() if data else None,
            "riservato": bool(riservato),
            "storia": [{"quando": int(time.time()), "chi": chi,
                        "cosa": f"aggiunto alla conoscenza da {origine or 'altra fonte'}"}]})
        manifest = read_manifest(cliente)
        manifest.append(item)
        _write_json_atomic(manifest_path(cliente), manifest)
    if not item["riservato"]:
        calcola_significato(dict(item))
    if on_change:
        on_change(cliente)
    print(f"[DOCUMENTI] aggiunto da {origine or 'altra fonte'} cliente={cliente} tipo={tipo} "
          f"sezioni={len(chunks)} riservato={item['riservato']}")
    return item


# ------------------------------------------------------------------ API
router = APIRouter(prefix="/api/documents", tags=["Documenti aziendali"])


def _verified_client(tenant: str, key: Optional[str], spazio: str = "azienda") -> Tuple[str, str]:
    """(tenant verificato, archivio). spazio="privato" -> lo spazio privato del titolare,
    cioe' di chi ha la chiave; "azienda" -> l'archivio dell'azienda (come prima)."""
    if tenant_verifier is None:
        raise HTTPException(503, "Archivio documenti non configurato")
    if spazio not in ("azienda", "privato"):
        raise HTTPException(400, "Spazio non valido")
    verified = tenant_verifier(tenant, key)
    if spazio not in spazi_ammessi(verified):
        raise HTTPException(403, "Questo spazio non e' abilitato per il cliente "
                                 f"(modalita' {modalita(verified)}).")
    if spazio == "privato":
        return verified, cliente_privato(verified)
    return verified, client_for_tenant(verified)



def _public(item: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: item.get(k) for k in ("id", "name", "titolo", "tipo", "size", "uploaded_at", "esterno",
                                     "sections", "stato", "data_documento", "valido_dal",
                                     "valido_fino", "sostituisce", "sostituito_da", "riservato",
                                     "note", "versione", "proposte", "storia",
                                     "cestinato_il", "motivo_rimozione", "nota_rimozione")}
    out["situazione"] = situazione(item)
    out["significato"] = bool(item.get("extracted_path")) and os.path.isfile(vettori_path(item))
    out["etichetta"] = etichetta_fonte(item)
    out["collegati"] = list(item.get("collegati") or [])
    out["media"] = ({"mime": item.get("media_mime"), "nome": item.get("media_nome"),
                     "bytes": item.get("media_bytes"), "trigger": item.get("trigger")}
                    if item.get("media_path") else None)
    out["audio"] = ({"impronta": item.get("audio_impronta"), "bytes": item.get("audio_bytes"),
                     "mime": item.get("audio_mime")} if item.get("audio_path") else None)
    if item.get("audio_stato"):
        out["audio_stato"] = item["audio_stato"]
    return out


@router.get("/{tenant}/spazi")
async def documents_spazi(tenant: str, x_applaudi_tenant_key: Optional[str] = Header(
        None, alias="X-Applaudi-Tenant-Key")):
    """Quali spazi ha questo cliente: lo decide il gestore della piattaforma."""
    if tenant_verifier is None:
        raise HTTPException(503, "Archivio documenti non configurato")
    verified = tenant_verifier(tenant, x_applaudi_tenant_key)
    spazi = list(spazi_ammessi(verified))
    catalogo = None
    if "azienda" in spazi:
        try:
            catalogo = bool(e_catalogo(client_for_tenant(verified)))
        except HTTPException:
            catalogo = None
    return {"tenant": verified, "modalita": modalita(verified), "spazi": spazi,
            "catalogo": catalogo}


@router.get("/{tenant}")
async def documents_list(tenant: str, storico: bool = Query(False), cestino: bool = Query(False),
                         spazio: str = Query("azienda", pattern="^(azienda|privato)$"),
                         x_applaudi_tenant_key: Optional[str] = Header(
                             None, alias="X-Applaudi-Tenant-Key")):
    verified, cliente = _verified_client(tenant, x_applaudi_tenant_key, spazio)
    items = [_public(i) for i in read_manifest(cliente)]
    if cestino:
        visibili = [i for i in items if i["stato"] == "cestinato"]
    elif storico:
        visibili = [i for i in items if i["stato"] != "cestinato"]
    else:
        visibili = [i for i in items if i["stato"] in ("da_confermare", "attivo")]
    return {"tenant": verified, "knowledge_client": cliente, "spazio": spazio, "documents": visibili,
            "count": len(visibili), "totale_archivio": len(items),
            "oggi": oggi().isoformat(), "tipi": TIPI, "motivi": MOTIVI,
            "nel_cestino": sum(1 for i in items if i["stato"] == "cestinato"),
            "cestino_giorni": cestino_giorni(),
            "persistent_root_configured": bool(os.getenv("DOCUMENT_UPLOAD_ROOT", "").strip()),
            "allowed_extensions": sorted(ALLOWED_EXTENSIONS), "max_mb": max_bytes() // 1048576}


@router.post("/{tenant}/upload")
async def documents_upload(tenant: str, file: UploadFile = File(...),
                           spazio: str = Query("azienda", pattern="^(azienda|privato)$"),
                           x_applaudi_tenant_key: Optional[str] = Header(
                               None, alias="X-Applaudi-Tenant-Key")):
    """Carica e legge il file. Il documento resta DA CONFERMARE: non entra in nessuna
    risposta finche' la scheda non viene confermata."""
    verified, cliente = _verified_client(tenant, x_applaudi_tenant_key, spazio)
    filename = safe_name(file.filename or "documento")
    extension = os.path.splitext(filename)[1].lower()
    if extension not in ALLOWED_EXTENSIONS and extension in MEDIA_EXT:
        raise HTTPException(415, "Foto e video non si caricano qui: usa il riquadro «Foto, video o "
                                 "disegno con il suo trigger», qui sopra, e scrivi il trigger.")
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, "Formato non supportato. Usa PDF, DOCX, XLSX, CSV, TXT, MD, "
                                 "JSON o EML (mail).")
    document_id = uuid.uuid4().hex
    folder = os.path.join(client_dir(cliente), "files", document_id)
    os.makedirs(folder, exist_ok=False)
    source_path = os.path.join(folder, filename)
    size, limit, digest = 0, max_bytes(), hashlib.sha256()
    try:
        with open(source_path, "wb") as stream:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"Il file supera il limite di {limit // 1048576} MB.")
                digest.update(chunk)
                stream.write(chunk)
        impronta = digest.hexdigest()
        doppio = next((i for i in read_manifest(cliente) if i.get("impronta") == impronta), None)
        if doppio:
            raise HTTPException(409, f"Questo file e' gia' nell'archivio: «{doppio.get('titolo')}».")
        try:
            chunks, meta = await run_in_threadpool(extract, source_path, extension)
        except Exception as error:
            raise HTTPException(422, f"Impossibile leggere il documento ({type(error).__name__}).")
        if not chunks:
            raise HTTPException(422, "Il documento non contiene testo estraibile "
                                     "(per esempio un PDF fatto solo di immagini).")
        extracted_path = os.path.join(folder, "extracted.json")
        _write_json_atomic(extracted_path, chunks)
        testo = "\n".join(c["text"] for c in chunks)
        date = proponi_date(testo)
        proposte = {"tipo": "mail" if extension == ".eml" else proponi_tipo(filename, testo),
                    "titolo": meta.get("oggetto") or os.path.splitext(filename)[0],
                    "data_documento": meta.get("data") or date["data_documento"],
                    "valido_dal": date["valido_dal"], "valido_fino": date["valido_fino"]}
        item = _normalizza({
            "id": document_id, "name": filename, "size": size, "uploaded_at": int(time.time()),
            "sections": len(chunks), "stato": "da_confermare", "impronta": impronta,
            "source_path": source_path, "extracted_path": extracted_path,
            "proposte": proposte, "dati_certi": meta or None,
            "titolo": proposte["titolo"], "tipo": proposte["tipo"],
            "storia": [{"quando": int(time.time()), "chi": verified, "cosa": "caricato"}]})
        with _WRITE_LOCK:
            manifest = read_manifest(cliente)
            manifest.append(item)
            _write_json_atomic(manifest_path(cliente), manifest)
        print(f"[DOCUMENTI] caricato cliente={cliente} tipo_proposto={proposte['tipo']} "
              f"sezioni={len(chunks)} (da confermare)")
        return {"ok": True, "document": _public(item), "knowledge_client": cliente}
    except HTTPException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except Exception as error:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(500, f"Caricamento non riuscito ({type(error).__name__}).")
    finally:
        await file.close()


@router.post("/{tenant}/media")
async def documents_media(tenant: str, file: UploadFile = File(...), trigger: str = Form(""),
                          collegati: str = Form(""), titolo: str = Form(""),
                          spazio: str = Query("privato", pattern="^(azienda|privato)$"),
                          x_applaudi_tenant_key: Optional[str] = Header(
                              None, alias="X-Applaudi-Tenant-Key")):
    """Foto, video o disegno CON il suo trigger. Senza trigger non entra: il file da solo
    non si puo' cercare. Entra attivo (il trigger scritto e' la conferma)."""
    verified, cliente = _verified_client(tenant, x_applaudi_tenant_key, spazio)
    if spazio != "privato":
        raise HTTPException(400, "Per ora foto, video e disegni vanno nello spazio privato: "
                                 "nell'archivio aziendale arriveranno con la certificazione.")
    trigger = _valida_trigger(trigger)
    filename = safe_name(file.filename or "file")
    extension = os.path.splitext(filename)[1].lower()
    if extension not in MEDIA_EXT:
        raise HTTPException(415, "Formato non supportato. Usa foto JPG o PNG, video MP4 o "
                                 "disegni in PDF.")
    mime, tipo = MEDIA_EXT[extension]
    collegati_ok = _valida_collegati(collegati, read_manifest(cliente))
    document_id = uuid.uuid4().hex
    folder = os.path.join(client_dir(cliente), "files", document_id)
    os.makedirs(folder, exist_ok=False)
    media_path = os.path.join(folder, "media" + extension)
    size, limit, digest, head = 0, max_bytes(), hashlib.sha256(), b""
    try:
        with open(media_path, "wb") as stream:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                if not head:
                    head = chunk[:16]
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"Il file supera il limite di {limit // 1048576} MB.")
                digest.update(chunk)
                stream.write(chunk)
        if not firma_media(head, mime):
            raise HTTPException(415, "Il contenuto del file non corrisponde al suo formato.")
        impronta = digest.hexdigest()
        doppio = next((i for i in read_manifest(cliente) if i.get("impronta") == impronta), None)
        if doppio:
            raise HTTPException(409, f"Questo file e' gia' nell'archivio: «{doppio.get('titolo')}».")
        extracted_path = os.path.join(folder, "extracted.json")
        _write_json_atomic(extracted_path, _testo_media(tipo, trigger))
        item = _normalizza({
            "id": document_id, "name": filename, "size": size, "uploaded_at": int(time.time()),
            "sections": 1, "stato": "attivo", "impronta": impronta, "tipo": tipo,
            "titolo": (re.sub(r"\s+", " ", titolo).strip()[:160] or titolo_breve(trigger)),
            "trigger": trigger, "collegati": collegati_ok, "extracted_path": extracted_path,
            "media_path": media_path, "media_mime": mime, "media_nome": filename,
            "media_bytes": size,
            "storia": [{"quando": int(time.time()), "chi": verified,
                        "cosa": f"aggiunto con trigger ({TIPI[tipo].lower()})"}]})
        with _WRITE_LOCK:
            manifest = read_manifest(cliente)
            manifest.append(item)
            _write_json_atomic(manifest_path(cliente), manifest)
    except HTTPException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except Exception as error:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(500, f"Caricamento non riuscito ({type(error).__name__}).")
    finally:
        await file.close()
    await run_in_threadpool(calcola_significato, dict(item))
    if on_change:
        on_change(cliente)
    print(f"[DOCUMENTI] {tipo} con trigger cliente={cliente} byte={size} collegati={len(collegati_ok)}")
    return {"ok": True, "document": _public(item), "knowledge_client": cliente}


@router.get("/{tenant}/{document_id}/media")
async def documents_media_file(tenant: str, document_id: str,
                               spazio: str = Query("azienda", pattern="^(azienda|privato)$"),
                               x_applaudi_tenant_key: Optional[str] = Header(
                                   None, alias="X-Applaudi-Tenant-Key")):
    """Il file di una foto/video/disegno, solo con la chiave del suo archivio."""
    _, cliente = _verified_client(tenant, x_applaudi_tenant_key, spazio)
    if not re.fullmatch(r"[0-9a-f]{32}", document_id or ""):
        raise HTTPException(400, "Documento non valido")
    item = next((i for i in read_manifest(cliente) if i.get("id") == document_id), None)
    if item and item.get("stato") == "cestinato":
        raise HTTPException(404, "Il documento e' nel cestino")
    path = (item or {}).get("media_path") or ""
    radice = os.path.join(client_dir(cliente), "files", document_id)
    if not path or not os.path.isfile(path) or os.path.dirname(os.path.abspath(path)) != os.path.abspath(radice):
        raise HTTPException(404, "File non trovato")
    return FileResponse(path, media_type=item.get("media_mime") or "application/octet-stream",
                        filename=item.get("media_nome") or os.path.basename(path),
                        content_disposition_type="inline")


def aggiorna_campi(cliente: str, document_id: str, campi: Dict[str, Any], storia: str = "",
                   chi: str = "") -> Optional[Dict[str, Any]]:
    """Aggiunge campi tecnici a una scheda (es. l'audio originale scaricato dopo)."""
    with _WRITE_LOCK:
        manifest = read_manifest(cliente)
        item = next((i for i in manifest if i.get("id") == document_id), None)
        if not item:
            return None
        item.update(campi)
        if storia:
            item["storia"].append({"quando": int(time.time()), "chi": chi, "cosa": storia})
        _write_json_atomic(manifest_path(cliente), manifest)
    return item


def _valida_trigger(valore: Any) -> str:
    trigger = re.sub(r"\s+", " ", str(valore or "")).strip()[:2000]
    if len(trigger) < TRIGGER_MIN:
        raise HTTPException(400, "Scrivi il trigger: cosa o chi e', e il perche' (almeno "
                                 f"{TRIGGER_MIN} caratteri). Senza trigger il file non entra.")
    return trigger


def _valida_collegati(valore: Any, manifest: List[Dict[str, Any]], proprio: str = "") -> List[str]:
    if isinstance(valore, str):
        valore = [v for v in re.split(r"[\s,;]+", valore) if v]
    if not isinstance(valore, list):
        raise HTTPException(400, "Collegamenti non validi")
    ids = {i.get("id") for i in manifest}
    out: List[str] = []
    for v in valore[:20]:
        v = str(v)
        if not re.fullmatch(r"[0-9a-f]{32}", v) or v not in ids or v == proprio:
            raise HTTPException(400, "Si puo' collegare solo un documento di questo archivio")
        if v not in out:
            out.append(v)
    return out


def _testo_media(tipo: str, trigger: str) -> List[Dict[str, str]]:
    return [{"label": "trigger", "text": f"{TIPI.get(tipo, 'File').upper()}: {trigger}"}]


def _valida_scheda(dati: Dict[str, Any], item: Dict[str, Any],
                   manifest: List[Dict[str, Any]]) -> Dict[str, Any]:
    nuovi: Dict[str, Any] = {}
    if "tipo" in dati:
        if dati["tipo"] not in TIPI:
            raise HTTPException(400, "Tipo di documento non valido")
        if bool(item.get("media_path")) != (dati["tipo"] in TIPI_MEDIA):
            raise HTTPException(400, "Foto, video e disegni restano tali: tipo non compatibile")
        nuovi["tipo"] = dati["tipo"]
    if "titolo" in dati:
        titolo = re.sub(r"\s+", " ", str(dati["titolo"] or "")).strip()[:160]
        if not titolo:
            raise HTTPException(400, "Il titolo non puo' essere vuoto")
        nuovi["titolo"] = titolo
    for key in ("data_documento", "valido_dal", "valido_fino"):
        if key in dati:
            raw = dati[key]
            if raw in (None, ""):
                nuovi[key] = None
            else:
                value = parse_date(raw)
                if not value:
                    raise HTTPException(400, f"Data non valida: {key}")
                nuovi[key] = value.isoformat()
    if "riservato" in dati:
        nuovi["riservato"] = bool(dati["riservato"])
    if "note" in dati:
        nuovi["note"] = (str(dati["note"] or "").strip()[:1000]) or None
    if "collegati" in dati:
        nuovi["collegati"] = _valida_collegati(dati["collegati"], manifest, item["id"])
    if "trigger" in dati:
        if not item.get("media_path"):
            raise HTTPException(400, "Il trigger vale solo per foto, video e disegni")
        nuovi["trigger"] = _valida_trigger(dati["trigger"])
    if "stato" in dati:
        if dati["stato"] not in ("attivo", "archiviato"):
            raise HTTPException(400, "Stato non modificabile a mano")
        nuovi["stato"] = dati["stato"]
    if "sostituisce" in dati and dati["sostituisce"]:
        target = next((i for i in manifest if i.get("id") == dati["sostituisce"]), None)
        if not target or target["id"] == item["id"]:
            raise HTTPException(400, "Il documento da sostituire non esiste")
        if target.get("stato") != "attivo":
            raise HTTPException(400, "Si puo' sostituire solo un documento attivo")
        nuovi["sostituisce"] = target["id"]
    merged = dict(item, **nuovi)
    inizio, fine = parse_date(merged.get("valido_dal")), parse_date(merged.get("valido_fino"))
    if inizio and fine and fine < inizio:
        raise HTTPException(400, "La data di fine validita' e' prima di quella di inizio")
    if merged.get("tipo") == "promozione" and not fine:
        raise HTTPException(400, "Per una promozione la data di fine validita' e' obbligatoria")
    return nuovi


@router.post("/{tenant}/{document_id}/scheda")
async def documents_scheda(tenant: str, document_id: str, dati: Dict[str, Any] = Body(...),
                           spazio: str = Query("azienda", pattern="^(azienda|privato)$"),
                           x_applaudi_tenant_key: Optional[str] = Header(
                               None, alias="X-Applaudi-Tenant-Key")):
    """Conferma (prima volta) o modifica la scheda. Ogni cambiamento resta nella storia."""
    verified, cliente = _verified_client(tenant, x_applaudi_tenant_key, spazio)
    if not re.fullmatch(r"[0-9a-f]{32}", document_id or ""):
        raise HTTPException(400, "Documento non valido")
    with _WRITE_LOCK:
        manifest = read_manifest(cliente)
        item = next((i for i in manifest if i.get("id") == document_id), None)
        if not item:
            raise HTTPException(404, "Documento non trovato")
        if item.get("stato") == "sostituito":
            raise HTTPException(409, "Documento sostituito: resta nello storico come era")
        nuovi = _valida_scheda(dati, item, manifest)
        conferma = item.get("stato") == "da_confermare"
        if conferma:
            nuovi.setdefault("stato", "attivo")
        cambi = {k: v for k, v in nuovi.items() if item.get(k) != v}
        if not cambi and not conferma:
            return {"ok": True, "document": _public(item)}
        item.update(nuovi)
        if "trigger" in cambi or ("tipo" in cambi and item.get("media_path")):
            _write_json_atomic(item["extracted_path"], _testo_media(item["tipo"], item["trigger"]))
        item["versione"] = int(item.get("versione") or 1) + (0 if conferma else 1)
        item["storia"].append({"quando": int(time.time()), "chi": verified,
                               "cosa": "confermato" if conferma else "modificato", "cambi": cambi})
        if nuovi.get("sostituisce"):
            old = next(i for i in manifest if i.get("id") == nuovi["sostituisce"])
            old.update({"stato": "sostituito", "sostituito_da": item["id"]})
            old["storia"].append({"quando": int(time.time()), "chi": verified,
                                  "cosa": f"sostituito da «{item.get('titolo')}»"})
        _write_json_atomic(manifest_path(cliente), manifest)
    # significato: al momento della conferma, o se cambia la riservatezza, o se mancava
    if item.get("stato") == "attivo" and (conferma or "riservato" in cambi or "trigger" in cambi
                                          or not os.path.isfile(vettori_path(item))):
        await run_in_threadpool(calcola_significato, dict(item))
    elif item.get("riservato") and os.path.isfile(vettori_path(item)):
        os.remove(vettori_path(item))
    if on_change:
        on_change(cliente)
    print(f"[DOCUMENTI] scheda {'confermata' if conferma else 'modificata'} cliente={cliente} "
          f"tipo={item.get('tipo')} stato={item.get('stato')}")
    return {"ok": True, "document": _public(item)}


# ------------------------------------------------------------------ uscita tutelata
def _ora_iso() -> str:
    return (dt.datetime.now(_ROMA) if _ROMA else dt.datetime.now()).isoformat(timespec="seconds")


def annota(cliente: str, azione: str, item: Optional[Dict[str, Any]] = None,
           motivo: str = "", nota: str = "", chi: str = "", senza_titolo: bool = False) -> None:
    """Registro delle operazioni: si aggiunge soltanto. Nessuna rotta lo cancella."""
    riga = {"quando": _ora_iso(), "ts": int(time.time()), "azione": azione, "chi": chi,
            "id": (item or {}).get("id"),
            "titolo": None if senza_titolo else (item or {}).get("titolo"),
            "motivo": motivo or None, "nota": (nota or "")[:500] or None}
    path = operazioni_path(cliente)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(riga, ensure_ascii=False) + "\n")


def leggi_operazioni(cliente: str, limite: int = 300) -> List[Dict[str, Any]]:
    try:
        with open(operazioni_path(cliente), encoding="utf-8") as stream:
            righe = [json.loads(r) for r in stream if r.strip()]
    except (OSError, ValueError):
        return []
    return list(reversed(righe))[:limite]


def _rimozioni_ultima_ora(cliente: str) -> int:
    limite = time.time() - 3600
    return sum(1 for r in leggi_operazioni(cliente, 10000)
               if r.get("azione") == "togli" and int(r.get("ts") or 0) >= limite)


def _trova(manifest: List[Dict[str, Any]], document_id: str) -> Dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{32}", document_id or ""):
        raise HTTPException(400, "Documento non valido")
    item = next((i for i in manifest if i.get("id") == document_id), None)
    if item is None:
        raise HTTPException(404, "Documento non trovato")
    return item


@router.post("/{tenant}/{document_id}/togli")
async def documents_togli(tenant: str, document_id: str, dati: Dict[str, Any] = Body(...),
                          spazio: str = Query("azienda", pattern="^(azienda|privato)$"),
                          x_applaudi_tenant_key: Optional[str] = Header(
                              None, alias="X-Applaudi-Tenant-Key")):
    """Toglie dalle risposte SUBITO e mette nel cestino. Il file resta: si puo' ripristinare."""
    verified, cliente = _verified_client(tenant, x_applaudi_tenant_key, spazio)
    motivo = str(dati.get("motivo") or "")
    nota = str(dati.get("nota") or "").strip()
    if motivo not in MOTIVI:
        raise HTTPException(400, "Indica il motivo della rimozione")
    if motivo == "altro" and len(nota) < 5:
        raise HTTPException(400, "Con il motivo «Altro» serve una nota che spieghi perché")
    with _WRITE_LOCK:
        # freno: nello spazio aziendale troppe rimozioni in un'ora si bloccano
        if not e_privato(cliente) and _rimozioni_ultima_ora(cliente) >= rimozioni_ora():
            annota(cliente, "rimozione_bloccata", None, motivo, nota, verified)
            print(f"[DOCUMENTI] rimozioni bloccate cliente={cliente}")
            raise HTTPException(429, f"Troppe rimozioni nell'ultima ora (limite {rimozioni_ora()}): "
                                     "per sicurezza le rimozioni sono sospese per un'ora. "
                                     "Il blocco resta nel registro delle operazioni.")
        manifest = read_manifest(cliente)
        item = _trova(manifest, document_id)
        if item.get("stato") == "cestinato":
            raise HTTPException(409, "Il documento e' gia' nel cestino")
        item.update({"stato_precedente": item.get("stato") or "attivo", "stato": "cestinato",
                     "cestinato_il": int(time.time()), "motivo_rimozione": motivo,
                     "nota_rimozione": nota or None})
        # un caricamento sbagliato che ne sostituiva un altro: il vecchio torna valido
        if motivo == "errore_caricamento":
            for other in manifest:
                if other.get("sostituito_da") == document_id:
                    other.update({"stato": "attivo", "sostituito_da": None})
        _write_json_atomic(manifest_path(cliente), manifest)
        annota(cliente, "togli", item, motivo, nota, verified)
    if on_change:
        on_change(cliente)
    print(f"[DOCUMENTI] tolto (nel cestino) cliente={cliente} motivo={motivo}")
    return {"ok": True, "document": _public(item)}


@router.post("/{tenant}/{document_id}/ripristina")
async def documents_ripristina(tenant: str, document_id: str,
                               spazio: str = Query("azienda", pattern="^(azienda|privato)$"),
                               x_applaudi_tenant_key: Optional[str] = Header(
                                   None, alias="X-Applaudi-Tenant-Key")):
    verified, cliente = _verified_client(tenant, x_applaudi_tenant_key, spazio)
    with _WRITE_LOCK:
        manifest = read_manifest(cliente)
        item = _trova(manifest, document_id)
        if item.get("stato") != "cestinato":
            raise HTTPException(409, "Il documento non e' nel cestino")
        item["stato"] = item.pop("stato_precedente", None) or "attivo"
        for k in ("cestinato_il", "motivo_rimozione", "nota_rimozione"):
            item.pop(k, None)
        if item["stato"] == "attivo" and item.get("sostituisce"):
            for other in manifest:       # torna a sostituire il documento che sostituiva
                if other.get("id") == item["sostituisce"] and other.get("stato") == "attivo":
                    other.update({"stato": "sostituito", "sostituito_da": item["id"]})
        _write_json_atomic(manifest_path(cliente), manifest)
        annota(cliente, "ripristina", item, chi=verified)
    if on_change:
        on_change(cliente)
    return {"ok": True, "document": _public(item)}


@router.post("/{tenant}/{document_id}/distruggi")
async def documents_distruggi(tenant: str, document_id: str, dati: Dict[str, Any] = Body(...),
                              spazio: str = Query("azienda", pattern="^(azienda|privato)$"),
                              x_applaudi_tenant_key: Optional[str] = Header(
                                  None, alias="X-Applaudi-Tenant-Key")):
    """Distrugge davvero (file e scheda). Solo dal cestino e solo con il PIN del titolare.
    Nello spazio aziendale solo dopo CESTINO_GIORNI, salvo richiesta privacy."""
    verified, cliente = _verified_client(tenant, x_applaudi_tenant_key, spazio)
    if verifica_pin is None:
        raise HTTPException(503, "Verifica del PIN non configurata")
    with _WRITE_LOCK:
        item = _trova(read_manifest(cliente), document_id)
        if item.get("stato") != "cestinato":
            raise HTTPException(409, "Si distrugge solo dal cestino: prima togli il documento")
        privacy = item.get("motivo_rimozione") == "privacy"
        giorni = (time.time() - int(item.get("cestinato_il") or 0)) / 86400
        if not e_privato(cliente) and not privacy and giorni < cestino_giorni():
            raise HTTPException(409, f"Nel cestino da {int(giorni)} giorni: nello spazio aziendale "
                                     f"si distrugge dopo {cestino_giorni()} giorni (subito solo "
                                     "per una richiesta privacy).")
    if not await run_in_threadpool(verifica_pin, verified, str(dati.get("pin") or "")):
        raise HTTPException(403, "PIN non corretto")
    with _WRITE_LOCK:
        manifest = read_manifest(cliente)
        item = _trova(manifest, document_id)
        if item.get("stato") != "cestinato":
            raise HTTPException(409, "Il documento non e' piu' nel cestino")
        _write_json_atomic(manifest_path(cliente), [i for i in manifest if i.get("id") != document_id])
        shutil.rmtree(os.path.join(client_dir(cliente), "files", document_id), ignore_errors=True)
        if not e_privato(cliente):     # nello spazio privato non resta traccia
            annota(cliente, "distruggi", item, item.get("motivo_rimozione") or "", chi=verified,
                   senza_titolo=item.get("motivo_rimozione") == "privacy")
    if on_change:
        on_change(cliente)
    print(f"[DOCUMENTI] distrutto cliente={cliente}")
    return {"ok": True}


def distruggi_tutto(cliente: str, conserva: Iterable[str] = ()) -> int:
    """Svuota un intero archivio (spazio privato): documenti, file, indici, registri.
    Restano solo i file in `conserva` (es. sicurezza.json). Restituisce i documenti tolti."""
    radice = client_dir(cliente)
    with _WRITE_LOCK:
        quanti = len(read_manifest(cliente))
        try:
            nomi = os.listdir(radice)
        except OSError:
            nomi = []
        for nome in nomi:
            if nome in set(conserva):
                continue
            percorso = os.path.join(radice, nome)
            if os.path.isdir(percorso) and not os.path.islink(percorso):
                shutil.rmtree(percorso, ignore_errors=True)
            else:
                try:
                    os.remove(percorso)
                except OSError:
                    pass
    if on_change:
        on_change(cliente)
    return quanti


@router.get("/{tenant}/registro/operazioni")
async def documents_operazioni(tenant: str,
                               spazio: str = Query("azienda", pattern="^(azienda|privato)$"),
                               x_applaudi_tenant_key: Optional[str] = Header(
                                   None, alias="X-Applaudi-Tenant-Key")):
    """Chi ha tolto, ripristinato o distrutto cosa, quando e perche'. Solo lettura."""
    _, cliente = _verified_client(tenant, x_applaudi_tenant_key, spazio)
    righe = leggi_operazioni(cliente)
    ora = time.time()
    return {"knowledge_client": cliente, "righe": righe, "motivi": MOTIVI,
            "rimozioni_ultima_ora": _rimozioni_ultima_ora(cliente), "limite_ora": rimozioni_ora(),
            "cestino_giorni": cestino_giorni(),
            "blocchi_24h": sum(1 for r in righe if r.get("azione") == "rimozione_bloccata"
                               and ora - int(r.get("ts") or 0) < 86400)}


@router.get("/{tenant}/registro/risposte")
async def documents_registro(tenant: str, testo: str = Query("", max_length=200),
                             spazio: str = Query("azienda", pattern="^(azienda|privato)$"),
                             x_applaudi_tenant_key: Optional[str] = Header(
                                 None, alias="X-Applaudi-Tenant-Key")):
    """Le risposte date, con le fonti usate: per le contestazioni."""
    _, cliente = _verified_client(tenant, x_applaudi_tenant_key, spazio)
    righe = leggi_registro(cliente, testo)
    return {"knowledge_client": cliente, "righe": righe, "count": len(righe),
            "conservazione_giorni": registro_giorni()}
