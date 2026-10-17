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

Sicurezza: l'azienda la decide il server (chiave del tenant + mappa esplicita
APPLAUDI_TENANT_CLIENT_MAP), mai il browser; un tenant senza archivio dichiarato non
puo' caricare. Persistenza: DOCUMENT_UPLOAD_ROOT su disco persistente.
"""
from __future__ import annotations

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
import uuid
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Body, File, Header, HTTPException, Query, UploadFile
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
}
STATI = ("da_confermare", "attivo", "sostituito", "archiviato")
EVIDENCE_PAGES = 6            # sezioni di documenti mandate al modello
EVIDENCE_CHARS = 3500         # per sezione: il catalogo resta la parte principale
SECTION_HEADER = "DAI DOCUMENTI AZIENDALI"
_WRITE_LOCK = threading.Lock()

# Contesto della domanda in corso (una domanda = un thread, api_ask non ha await):
# canale ("web" o "whatsapp") e fonti effettivamente mandate al modello.
contesto = threading.local()

# ------------------------------------------------------------------ collegamenti
# Impostati da app.py all'avvio (stesso schema di whatsapp_gateway).
tenant_verifier: Optional[Callable[[str, Optional[str]], str]] = None
client_exists: Optional[Callable[[str], bool]] = None
default_client: Optional[Callable[[], str]] = None
on_change: Optional[Callable[[str], None]] = None


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


def max_bytes() -> int:
    return _int_env("DOCUMENT_MAX_MB", 30, 1, 100) * 1024 * 1024


def durata_mail_giorni() -> int:
    """Mail e registrazioni valgono questo tempo dalla loro data (poi escono dalle risposte)."""
    return _int_env("DURATA_MAIL_GIORNI", 730, 1, 36500)


def registro_giorni() -> int:
    return _int_env("REGISTRO_GIORNI", 730, 1, 36500)


def _clean_id(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "", (value or "").strip().lower())[:40]


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
    if mapped:
        return mapped
    if clean and ((client_exists and client_exists(clean))
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
    if fine is None and item.get("tipo") in ("mail", "registrazione"):
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
    inizio, fine = periodo(item)
    if inizio or fine:
        parti.append("valido" + (f" dal {inizio.strftime('%d/%m/%Y')}" if inizio else "")
                     + (f" al {fine.strftime('%d/%m/%Y')}" if fine else ""))
    return " · ".join(parti)


# ------------------------------------------------------------------ pagine e evidenze
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
        scheda = {k: item.get(k) for k in ("id", "name", "titolo", "tipo", "data_documento",
                                           "valido_dal", "valido_fino", "riservato", "versione",
                                           "stato", "uploaded_at")}
        for chunk in chunks if isinstance(chunks, list) else []:
            text = str(chunk.get("text") or "").strip()
            if not text:
                continue
            tokens = list(tokenizer(text))
            counts: Dict[str, int] = {}
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
            pages.append({"scheda": scheda, "label": str(chunk.get("label") or ""),
                          "text": text, "token_counts": counts, "token_set": set(tokens)})
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


def evidence(question: str, pages: List[Dict[str, Any]],
             tokenizer: Callable[[str], Iterable[str]], max_pages: int = EVIDENCE_PAGES
             ) -> Tuple[str, List[Dict[str, Any]]]:
    """(testo per il modello, schede usate) delle sezioni piu' pertinenti e valide oggi."""
    candidates = utilizzabili(pages)
    query = set(tokenizer(question))
    if not candidates or not query:
        return "", []
    ranked = []
    for page, stato in candidates:
        overlap = query & page["token_set"]
        if not overlap:
            continue
        frequency = sum(page["token_counts"].get(t, 0) for t in overlap)
        ranked.append((len(overlap) * 5 + min(frequency, 30)
                       + sum(2 for t in overlap if len(t) >= 7), page, stato))
    ranked.sort(key=lambda r: r[0], reverse=True)
    parts, used = [], {}
    for _, page, stato in ranked[:max_pages]:
        scheda = page["scheda"]
        avviso = ""
        if stato == "in_arrivo":
            inizio, _ = periodo(scheda)
            avviso = (f"\nNON ANCORA IN VIGORE: vale dal {inizio.strftime('%d/%m/%Y')}. "
                      "Citalo solo come cosa futura, mai come condizione di oggi.")
        parts.append(f"===== DOCUMENTO: {etichetta_fonte(scheda)} · {page['label']} ====={avviso}\n"
                     f"{page['text'][:EVIDENCE_CHARS]}")
        used[scheda["id"]] = scheda
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
        "1. La parte principale della risposta resta basata sul catalogo, con le sue pagine. "
        "Non mettere nella parte principale nessun dato preso da questi documenti.\n"
        f"2. Se e solo se questi documenti aggiungono, cambiano o confermano qualcosa di "
        f"pertinente alla domanda, chiudi la risposta con una riga che contiene soltanto "
        f"{SECTION_HEADER} e sotto un punto per ogni contributo, in questa forma:\n"
        "- [NUOVO] cosa aggiunge rispetto al catalogo — fonte: «titolo» (data; validita' se "
        "indicata), posizione\n"
        "- [DIVERSO DAL CATALOGO] cosa dice il documento e cosa dice il catalogo (pagina) — "
        "fonte: «titolo» (data; validita'), posizione\n"
        "- [CONFERMA] cosa conferma — fonte: «titolo» (data), posizione\n"
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


def join_section(answer: str, section: str) -> str:
    return answer if not section else (answer or "").rstrip() + "\n\n" + section


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


# ------------------------------------------------------------------ API
router = APIRouter(prefix="/api/documents", tags=["Documenti aziendali"])


def _verified_client(tenant: str, key: Optional[str]) -> Tuple[str, str]:
    if tenant_verifier is None:
        raise HTTPException(503, "Archivio documenti non configurato")
    verified = tenant_verifier(tenant, key)
    return verified, client_for_tenant(verified)


def _public(item: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: item.get(k) for k in ("id", "name", "titolo", "tipo", "size", "uploaded_at",
                                     "sections", "stato", "data_documento", "valido_dal",
                                     "valido_fino", "sostituisce", "sostituito_da", "riservato",
                                     "note", "versione", "proposte", "storia")}
    out["situazione"] = situazione(item)
    out["etichetta"] = etichetta_fonte(item)
    return out


@router.get("/{tenant}")
async def documents_list(tenant: str, storico: bool = Query(False),
                         x_applaudi_tenant_key: Optional[str] = Header(
                             None, alias="X-Applaudi-Tenant-Key")):
    verified, cliente = _verified_client(tenant, x_applaudi_tenant_key)
    items = [_public(i) for i in read_manifest(cliente)]
    visibili = items if storico else [i for i in items if i["stato"] in ("da_confermare", "attivo")]
    return {"tenant": verified, "knowledge_client": cliente, "documents": visibili,
            "count": len(visibili), "totale_archivio": len(items),
            "oggi": oggi().isoformat(), "tipi": TIPI,
            "persistent_root_configured": bool(os.getenv("DOCUMENT_UPLOAD_ROOT", "").strip()),
            "allowed_extensions": sorted(ALLOWED_EXTENSIONS), "max_mb": max_bytes() // 1048576}


@router.post("/{tenant}/upload")
async def documents_upload(tenant: str, file: UploadFile = File(...),
                           x_applaudi_tenant_key: Optional[str] = Header(
                               None, alias="X-Applaudi-Tenant-Key")):
    """Carica e legge il file. Il documento resta DA CONFERMARE: non entra in nessuna
    risposta finche' la scheda non viene confermata."""
    verified, cliente = _verified_client(tenant, x_applaudi_tenant_key)
    filename = safe_name(file.filename or "documento")
    extension = os.path.splitext(filename)[1].lower()
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


def _valida_scheda(dati: Dict[str, Any], item: Dict[str, Any],
                   manifest: List[Dict[str, Any]]) -> Dict[str, Any]:
    nuovi: Dict[str, Any] = {}
    if "tipo" in dati:
        if dati["tipo"] not in TIPI:
            raise HTTPException(400, "Tipo di documento non valido")
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
                           x_applaudi_tenant_key: Optional[str] = Header(
                               None, alias="X-Applaudi-Tenant-Key")):
    """Conferma (prima volta) o modifica la scheda. Ogni cambiamento resta nella storia."""
    verified, cliente = _verified_client(tenant, x_applaudi_tenant_key)
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
        item["versione"] = int(item.get("versione") or 1) + (0 if conferma else 1)
        item["storia"].append({"quando": int(time.time()), "chi": verified,
                               "cosa": "confermato" if conferma else "modificato", "cambi": cambi})
        if nuovi.get("sostituisce"):
            old = next(i for i in manifest if i.get("id") == nuovi["sostituisce"])
            old.update({"stato": "sostituito", "sostituito_da": item["id"]})
            old["storia"].append({"quando": int(time.time()), "chi": verified,
                                  "cosa": f"sostituito da «{item.get('titolo')}»"})
        _write_json_atomic(manifest_path(cliente), manifest)
    if on_change:
        on_change(cliente)
    print(f"[DOCUMENTI] scheda {'confermata' if conferma else 'modificata'} cliente={cliente} "
          f"tipo={item.get('tipo')} stato={item.get('stato')}")
    return {"ok": True, "document": _public(item)}


@router.delete("/{tenant}/{document_id}")
async def documents_delete(tenant: str, document_id: str,
                           x_applaudi_tenant_key: Optional[str] = Header(
                               None, alias="X-Applaudi-Tenant-Key")):
    """Solo per errori di caricamento. Per un documento superato: sostituire o archiviare."""
    _, cliente = _verified_client(tenant, x_applaudi_tenant_key)
    if not re.fullmatch(r"[0-9a-f]{32}", document_id or ""):
        raise HTTPException(400, "Documento non valido")
    with _WRITE_LOCK:
        manifest = read_manifest(cliente)
        kept = [item for item in manifest if item.get("id") != document_id]
        if len(kept) == len(manifest):
            raise HTTPException(404, "Documento non trovato")
        for item in kept:     # chi era sostituito da questo torna attivo
            if item.get("sostituito_da") == document_id:
                item.update({"stato": "attivo", "sostituito_da": None})
        _write_json_atomic(manifest_path(cliente), kept)
    shutil.rmtree(os.path.join(client_dir(cliente), "files", document_id), ignore_errors=True)
    if on_change:
        on_change(cliente)
    print(f"[DOCUMENTI] eliminato cliente={cliente}")
    return {"ok": True}


@router.get("/{tenant}/registro/risposte")
async def documents_registro(tenant: str, testo: str = Query("", max_length=200),
                             x_applaudi_tenant_key: Optional[str] = Header(
                                 None, alias="X-Applaudi-Tenant-Key")):
    """Le risposte date, con le fonti usate: per le contestazioni."""
    _, cliente = _verified_client(tenant, x_applaudi_tenant_key)
    righe = leggi_registro(cliente, testo)
    return {"knowledge_client": cliente, "righe": righe, "count": len(righe),
            "conservazione_giorni": registro_giorni()}
