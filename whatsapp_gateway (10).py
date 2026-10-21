"""A'PLAUD'I - gateway WhatsApp Cloud API (Meta) per il CORE Narratore-Risponditore.

Regole (tutte "fail closed"):
- Spento se WHATSAPP_GATEWAY_ENABLED != true: ogni rotta risponde 404 e il CORE
  lavora esattamente come prima. Importare questo modulo non ha effetti collaterali.
- Ogni POST deve avere la firma Meta X-Hub-Signature-256 corretta, calcolata sul
  corpo grezzo con WHATSAPP_APP_SECRET. Senza firma valida: 403, nessuna lettura.
- Il GET /webhook serve SOLO alla verifica Meta (hub.challenge): non autorizza nulla.
- L'azienda si decide SOLO lato server: numero mittente -> azienda, da
  WHATSAPP_PHONE_BINDINGS_JSON. Testo, query string e corpo non possono cambiarla.
  Numeri non censiti: nessuna risposta, nessun accesso.
- Ogni messaggio Meta (wamid) viene lavorato una volta sola (idempotenza su SQLite),
  anche se Meta lo ritrasmette. Messaggi piu' vecchi di 24 ore non si lavorano.
- Limite di messaggi per numero all'ora, per contenere i costi del modello.
- Nel registro non si salvano ne' il numero in chiaro ne' il testo: solo un'impronta
  HMAC del numero, l'azienda, gli stati e i tempi (audit).
- Al mittente non arrivano mai messaggi di errore interni.
"""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

router = APIRouter(prefix='/api/whatsapp', tags=['WhatsApp'])

# Impostati da app.py. Nessuno dei due accetta un'azienda scelta dall'esterno.
document_responder: Optional[Callable[[str, str], Awaitable[str]]] = None
tenant_validator: Optional[Callable[[str], bool]] = None
# numeri delle persone registrate nella console del gestore: telefono -> azienda.
# Quelli di WHATSAPP_PHONE_BINDINGS_JSON vincono sempre.
bindings_extra: Optional[Callable[[], Dict[str, str]]] = None
# Spazio privato del titolare (spazio_privato.py). Se non impostati, tutto come prima.
# conversation_handler(mittente, azienda o None, testo) -> ("risposta", testo da inviare)
#   oppure ("azienda", intestazione): la domanda segue il percorso aziendale di sempre.
# private_access(mittente, testo) -> True se un numero NON associato a un'azienda puo'
#   scrivere (collegato a uno spazio privato, o sta mandando il codice di collegamento).
conversation_handler: Optional[Callable[[str, Optional[str], str], Awaitable[tuple]]] = None
private_access: Optional[Callable[[str, str], bool]] = None
PRIVATE_ONLY = '·privato'      # nel registro: numero senza azienda, solo spazio privato


# Client HTTP verso Meta; sostituibile nei test con un trasporto finto.
http_client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient


class ServiceBusy(Exception):
    """Il motore e' occupato (es. collaudo in corso): si risponde 'riprova'."""


MAX_BODY_BYTES = 1024 * 1024
MAX_QUESTION_CHARS = 3000
WA_PART_CHARS = 3900          # limite Meta 4096 caratteri per messaggio di testo
WA_MAX_PARTS = 3
MAX_MESSAGE_AGE_S = 24 * 3600  # oltre la finestra di 24 ore Meta non accetta testo libero

MSG_ACK = "A’PLAUD’I: messaggio di prova ricevuto. La consultazione documentale non è attiva."
MSG_BUSY = "A’PLAUD’I: il servizio è momentaneamente occupato. Riprova fra qualche minuto."
MSG_ERROR = "A’PLAUD’I: in questo momento non riesco a rispondere. Riprova più tardi."
MSG_RATE = "A’PLAUD’I: hai raggiunto il numero massimo di domande per quest'ora. Riprova più tardi."
MSG_ONLY_TEXT = "A’PLAUD’I: posso leggere solo messaggi di testo."

_PHONE = re.compile(r'\d{7,16}')
_TENANT = re.compile(r'[a-z0-9_-]{1,40}')
_WAMID = re.compile(r'[a-zA-Z0-9_.:=+/-]{1,300}')   # i wamid Meta sono base64: possono contenere + e /
_PID = re.compile(r'\d{8,30}')


# ------------------------------------------------------------------ diagnostica

def _mask(phone: str) -> str:
    """Nel log il numero non compare mai intero: solo le ultime 3 cifre."""
    return '…' + str(phone)[-3:] if phone else '?'


def log(event: str, **fields) -> None:
    extra = ' '.join(f'{k}={v}' for k, v in fields.items())
    print(f'[WHATSAPP] {event} {extra}'.rstrip(), flush=True)


def _meta_error(response: 'httpx.Response') -> str:
    """Codice e testo d'errore di Meta (non contengono segreti), per capire un rifiuto."""
    try:
        err = response.json().get('error') or {}
        return f"http={response.status_code} code={err.get('code')} msg={str(err.get('message'))[:160]!r}"
    except Exception:
        return f'http={response.status_code}'


class MetaSendError(RuntimeError):
    pass


# ------------------------------------------------------------------ configurazione

def enabled() -> bool:
    return os.getenv('WHATSAPP_GATEWAY_ENABLED', 'false').strip().lower() == 'true'


def settings() -> Tuple[Dict[str, str], Dict[str, str]]:
    if not enabled():
        raise HTTPException(404, 'WhatsApp gateway disabled')
    try:
        bindings = json.loads(os.getenv('WHATSAPP_PHONE_BINDINGS_JSON', '{}') or '{}')
    except ValueError:
        raise HTTPException(503, 'Invalid binding configuration')
    if not isinstance(bindings, dict):
        raise HTTPException(503, 'Invalid binding configuration')
    clean: Dict[str, str] = {}
    for phone, tenant in bindings.items():
        phone, tenant = str(phone).strip(), str(tenant).strip()
        if not _PHONE.fullmatch(phone) or not _TENANT.fullmatch(tenant):
            raise HTTPException(503, 'Invalid binding configuration')
        # Un'azienda che il CORE non conosce non viene "ripiegata" su quella
        # predefinita: tutta la configurazione e' rifiutata.
        if tenant_validator is not None and not tenant_validator(tenant):
            raise HTTPException(503, 'Binding to unknown tenant')
        clean[phone] = tenant
    if bindings_extra is not None:
        try:
            extra = bindings_extra() or {}
        except Exception as error:          # l'anagrafica non deve fermare WhatsApp
            print(f'[WHATSAPP] numeri della console non letti: {type(error).__name__}')
            extra = {}
        for phone, tenant in extra.items():
            phone, tenant = str(phone).strip(), str(tenant).strip()
            if (phone not in clean and _PHONE.fullmatch(phone) and _TENANT.fullmatch(tenant)
                    and (tenant_validator is None or tenant_validator(tenant))):
                clean[phone] = tenant
    cfg = {k: os.getenv(k, '').strip() for k in (
        'WHATSAPP_VERIFY_TOKEN', 'WHATSAPP_APP_SECRET', 'WHATSAPP_ACCESS_TOKEN',
        'WHATSAPP_PHONE_NUMBER_ID', 'WHATSAPP_INBOX_DB')}
    if not cfg['WHATSAPP_VERIFY_TOKEN'] or not cfg['WHATSAPP_APP_SECRET']:
        raise HTTPException(503, 'Missing Meta verification credentials')
    return cfg, clean


def reply_mode() -> str:
    mode = os.getenv('WHATSAPP_REPLY_MODE', 'ack').strip().lower()
    if mode not in ('ack', 'documents'):
        raise RuntimeError('Unsupported reply mode')
    return mode


def max_per_hour() -> int:
    try:
        return max(1, min(int(os.getenv('WHATSAPP_MAX_MSG_PER_HOUR', '30')), 1000))
    except ValueError:
        return 30


# ------------------------------------------------------------------ registro (audit)

def connection(path: str) -> sqlite3.Connection:
    # SQLite: adatto a UNA istanza con disco persistente. Su Render Free il disco
    # si azzera al riavvio: l'idempotenza vale finche' il servizio resta acceso.
    conn = sqlite3.connect(path, timeout=10)
    conn.execute(
        'CREATE TABLE IF NOT EXISTS wa_inbox ('
        ' id TEXT PRIMARY KEY, tenant TEXT NOT NULL, sender_hash TEXT NOT NULL,'
        ' received_at INTEGER NOT NULL, message_ts INTEGER, status TEXT NOT NULL,'
        ' updated_at INTEGER NOT NULL, answer_chars INTEGER, parts INTEGER, duration_ms INTEGER)')
    conn.execute('CREATE INDEX IF NOT EXISTS wa_inbox_sender ON wa_inbox(sender_hash, received_at)')
    conn.commit()
    return conn


def sender_hash(secret: str, sender: str) -> str:
    return hmac.new(secret.encode(), ('wa:' + sender).encode(), hashlib.sha256).hexdigest()[:24]


def _set_status(db: str, mid: str, status: str, **fields) -> None:
    cols = ['status=?', 'updated_at=?']
    vals: List[object] = [status, int(time.time())]
    for k in ('answer_chars', 'parts', 'duration_ms'):
        if k in fields:
            cols.append(f'{k}=?')
            vals.append(fields[k])
    vals.append(mid)
    with closing(connection(db)) as conn:
        conn.execute(f'UPDATE wa_inbox SET {", ".join(cols)} WHERE id=?', vals)
        conn.commit()


# ------------------------------------------------------------------ testo in uscita

def to_whatsapp_text(text: str) -> str:
    """Testo semplice: niente HTML, righe vuote compattate."""
    text = re.sub(r'<br\s*/?>', '\n', text or '', flags=re.I)
    text = re.sub(r'<[^>]{1,200}>', '', text)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def split_parts(text: str, size: int = WA_PART_CHARS, max_parts: int = WA_MAX_PARTS) -> List[str]:
    text = text.strip()
    parts: List[str] = []
    while text and len(parts) < max_parts:
        if len(text) <= size:
            parts.append(text)
            text = ''
            break
        cut = text.rfind('\n', 0, size)
        if cut < size // 2:
            cut = text.rfind(' ', 0, size)
        if cut < size // 2:
            cut = size
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text and parts:
        tail = '\n[…risposta troncata: chiedi un dettaglio più preciso]'
        parts[-1] = parts[-1][:size - len(tail)].rstrip() + tail
    return parts


async def send_reply(sender: str, cfg: Dict[str, str], body: str) -> int:
    access, pid = cfg['WHATSAPP_ACCESS_TOKEN'], cfg['WHATSAPP_PHONE_NUMBER_ID']
    if not access or not _PID.fullmatch(pid):
        raise RuntimeError('Missing WhatsApp outbound credentials')
    version = os.getenv('WHATSAPP_GRAPH_VERSION', 'v23.0').strip()
    if not re.fullmatch(r'v\d+\.\d+', version):
        raise RuntimeError('Invalid Graph version')
    parts = split_parts(to_whatsapp_text(body)) or [MSG_ERROR]
    async with http_client_factory(timeout=15) as client:
        for part in parts:
            payload = {'messaging_product': 'whatsapp', 'recipient_type': 'individual',
                       'to': sender, 'type': 'text', 'text': {'preview_url': False, 'body': part}}
            response = await client.post(
                f'https://graph.facebook.com/{version}/{pid}/messages',
                json=payload, headers={'Authorization': f'Bearer {access}'})
            if response.status_code >= 400:
                raise MetaSendError(_meta_error(response))
    return len(parts)


async def send_template(to: str, cfg: Dict[str, str], nome: str, lingua: str,
                        parametri: List[str]) -> None:
    """Un messaggio da MODELLO approvato da Meta: l'unico che un'azienda puo' mandare per
    prima, fuori dalla finestra di 24 ore dall'ultimo messaggio della persona."""
    access, pid = cfg['WHATSAPP_ACCESS_TOKEN'], cfg['WHATSAPP_PHONE_NUMBER_ID']
    version = os.getenv('WHATSAPP_GRAPH_VERSION', 'v23.0').strip()
    if not access or not _PID.fullmatch(pid) or not re.fullmatch(r'v\d+\.\d+', version):
        raise RuntimeError('Missing WhatsApp outbound credentials')
    if not re.fullmatch(r'[a-z0-9_]{1,512}', nome or '') or not re.fullmatch(r'[a-z]{2}(_[A-Z]{2})?', lingua or ''):
        raise RuntimeError('Invalid template configuration')
    payload = {'messaging_product': 'whatsapp', 'to': to, 'type': 'template',
               'template': {'name': nome, 'language': {'code': lingua},
                            'components': [{'type': 'body', 'parameters': [
                                {'type': 'text', 'text': (p or '—')[:1000]} for p in parametri]}]}}
    async with http_client_factory(timeout=15) as client:
        response = await client.post(f'https://graph.facebook.com/{version}/{pid}/messages',
                                     json=payload, headers={'Authorization': f'Bearer {access}'})
        if response.status_code >= 400:
            raise MetaSendError(_meta_error(response))


def fuori_finestra(errore: Exception) -> bool:
    """Meta rifiuta un testo libero oltre le 24 ore dall'ultimo messaggio (codice 131047)."""
    return 'code=131047' in str(errore)


async def send_typing(mid: str, cfg: Dict[str, str]) -> bool:
    """Spunte blu + «sta scrivendo…» sul telefono di chi ha scritto.

    Meta mostra l'indicatore finche' non arriva la risposta (massimo ~25 secondi).
    Va inviato PRIMA di calcolare la risposta: il calcolo occupa il servizio e
    durante quel tempo non partirebbe nulla. Un errore qui non ferma mai la risposta.
    """
    try:
        access, pid = cfg['WHATSAPP_ACCESS_TOKEN'], cfg['WHATSAPP_PHONE_NUMBER_ID']
        version = os.getenv('WHATSAPP_GRAPH_VERSION', 'v23.0').strip()
        if not access or not _PID.fullmatch(pid) or not re.fullmatch(r'v\d+\.\d+', version):
            return False
        payload = {'messaging_product': 'whatsapp', 'status': 'read', 'message_id': mid,
                   'typing_indicator': {'type': 'text'}}
        async with http_client_factory(timeout=5) as client:
            response = await client.post(
                f'https://graph.facebook.com/{version}/{pid}/messages',
                json=payload, headers={'Authorization': f'Bearer {access}'})
        if response.status_code >= 400:
            log('indicatore non accettato', dettaglio=_meta_error(response))
            return False
        return True
    except Exception as error:
        log('indicatore non inviato', errore=type(error).__name__)
        return False


# ------------------------------------------------------------------ allegati (foto, video, PDF)
# Limiti di Meta per tipo (Cloud API): immagini JPEG/PNG 5 MB, video MP4/3GP 16 MB,
# documenti 100 MB. Oltre, il file non si allega e si avvisa nel testo.
MEDIA_LIMITS = {'image': 5 * 1024 * 1024, 'video': 16 * 1024 * 1024, 'document': 100 * 1024 * 1024}


def media_kind(mime: str) -> str:
    if mime in ('image/jpeg', 'image/png'):
        return 'image'
    if mime in ('video/mp4', 'video/3gpp'):
        return 'video'
    return 'document'


def sendable(allegato: dict) -> bool:
    try:
        size = os.path.getsize(allegato['path'])
    except (OSError, KeyError, TypeError):
        return False
    return 0 < size <= MEDIA_LIMITS[media_kind(str(allegato.get('mime') or ''))]


async def send_media(sender: str, cfg: Dict[str, str], allegato: dict) -> None:
    """Carica il file su Meta (/media) e lo invia con il suo titolo come didascalia."""
    access, pid = cfg['WHATSAPP_ACCESS_TOKEN'], cfg['WHATSAPP_PHONE_NUMBER_ID']
    version = os.getenv('WHATSAPP_GRAPH_VERSION', 'v23.0').strip()
    if not access or not _PID.fullmatch(pid) or not re.fullmatch(r'v\d+\.\d+', version):
        raise RuntimeError('Missing WhatsApp outbound credentials')
    mime = str(allegato.get('mime') or 'application/octet-stream')
    kind = media_kind(mime)
    nome = str(allegato.get('nome') or 'file')
    with open(allegato['path'], 'rb') as stream:
        data = stream.read()
    headers = {'Authorization': f'Bearer {access}'}
    async with http_client_factory(timeout=60) as client:
        up = await client.post(f'https://graph.facebook.com/{version}/{pid}/media', headers=headers,
                               data={'messaging_product': 'whatsapp', 'type': mime},
                               files={'file': (nome, data, mime)})
        if up.status_code >= 400:
            raise MetaSendError(_meta_error(up))
        media_id = str((up.json() or {}).get('id') or '')
        if not media_id:
            raise MetaSendError('upload senza id')
        body: Dict[str, object] = {'id': media_id, 'caption': str(allegato.get('titolo') or '')[:1000]}
        if kind == 'document':
            body['filename'] = nome
        payload = {'messaging_product': 'whatsapp', 'recipient_type': 'individual', 'to': sender,
                   'type': kind, kind: body}
        sent = await client.post(f'https://graph.facebook.com/{version}/{pid}/messages',
                                 json=payload, headers=headers)
        if sent.status_code >= 400:
            raise MetaSendError(_meta_error(sent))


# ------------------------------------------------------------------ elaborazione

async def process_message(mid: str, sender: str, tenant: str, question: str,
                          cfg: Dict[str, str], kind: str = 'answer') -> str:
    """Calcola la risposta e la invia. Non solleva mai: lo stato finisce nel registro."""
    db = cfg['WHATSAPP_INBOX_DB']
    started = time.perf_counter()
    state, body = 'answered', ''
    files: list = []
    if kind == 'rate_limited':
        state, body = 'rate_limited', MSG_RATE
    elif kind == 'not_text':
        state, body = 'not_text', MSG_ONLY_TEXT
    else:
        if reply_mode() == 'documents':
            await send_typing(mid, cfg)
        try:
            if reply_mode() == 'documents':
                azienda = None if tenant == PRIVATE_ONLY else tenant
                tipo, intestazione = 'azienda', ''
                if conversation_handler is not None:
                    esito = await conversation_handler(sender, azienda, question)
                    tipo, intestazione = esito[0], esito[1]
                    files = list(esito[2]) if len(esito) > 2 and esito[2] else []
                if tipo == 'risposta':
                    body = intestazione
                elif azienda is None:
                    raise RuntimeError('No company for this sender')
                else:
                    if document_responder is None:
                        raise RuntimeError('Document responder not configured')
                    body = await document_responder(azienda, question)
                    if isinstance(body, str) and body.strip() and intestazione:
                        body = intestazione + body
                if not isinstance(body, str) or not body.strip():
                    raise RuntimeError('Empty answer')
            else:
                state, body = 'ack', MSG_ACK
        except ServiceBusy:
            state, body, files = 'busy', MSG_BUSY, []
        except Exception as error:   # niente dettagli interni al mittente
            log('risposta non calcolata', tenant=tenant, errore=type(error).__name__)
            state, body, files = 'answer_failed', MSG_ERROR, []
    troppo = [f for f in files if not sendable(f)]
    if troppo:
        body = body.rstrip() + '\n\n' + '\n'.join(
            f"📎 «{f.get('titolo') or 'file'}»: troppo grande per WhatsApp, aprilo dalla dashboard."
            for f in troppo)
    try:
        parts = await send_reply(sender, cfg, body)
        final = state if state != 'answered' else 'sent'
        for allegato in [f for f in files if sendable(f)]:
            try:
                await send_media(sender, cfg, allegato)
                parts += 1
            except Exception as error:     # un allegato non inviato non annulla la risposta
                log('allegato non inviato', a=_mask(sender), errore=type(error).__name__,
                    dettaglio=str(error)[:200] if isinstance(error, MetaSendError) else '-')
        log('risposta inviata', tenant=tenant, a=_mask(sender), stato=final, parti=parts)
    except MetaSendError as error:
        log('invio rifiutato da Meta', tenant=tenant, a=_mask(sender), dettaglio=str(error))
        parts, final = 0, 'send_failed'
    except Exception as error:
        log('invio fallito', tenant=tenant, a=_mask(sender), errore=type(error).__name__)
        parts, final = 0, 'send_failed'
    try:
        _set_status(db, mid, final, answer_chars=len(body), parts=parts,
                    duration_ms=int((time.perf_counter() - started) * 1000))
    except Exception as error:
        print(f'[WHATSAPP] registro non aggiornato: {type(error).__name__}')
    return final


# ------------------------------------------------------------------ informativa privacy
# Pagina pubblica richiesta da Meta per pubblicare l'app. Visibile solo con il
# gateway acceso: con WHATSAPP_GATEWAY_ENABLED spento risponde 404 come il resto.
PRIVACY_HTML = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>A’PLAUD’I — Informativa privacy</title>
<style>body{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;line-height:1.55;color:#222;background:#fff}h1{font-size:1.5rem}h2{font-size:1.1rem;margin-top:1.6rem}</style>
</head><body>
<h1>A’PLAUD’I — Informativa sulla privacy (servizio WhatsApp)</h1>
<p>Ultimo aggiornamento: 18 ottobre 2026</p>
<h2>Titolare</h2>
<p>Roberto Zannoni — Tecnaria, Bassano del Grappa (VI), Italia. Contatto: zroberto59@gmail.com</p>
<h2>Che cosa fa il servizio</h2>
<p>A’PLAUD’I risponde via WhatsApp alle domande di utenti autorizzati, consultando i documenti
(cataloghi, listini) dell'azienda a cui l'utente è associato. I messaggi provenienti da numeri non
abilitati vengono ignorati.</p>
<h2>Quali dati trattiamo</h2>
<ul>
<li><b>Numero di telefono del mittente</b>: usato solo per riconoscere l'utente abilitato e l'azienda
associata. Nel registro tecnico non è salvato in chiaro, ma come impronta crittografica.</li>
<li><b>Spazio privato</b>: il titolare può collegare il proprio numero a un archivio personale
(documenti e registrazioni che sceglie lui), protetto da un PIN. Il titolare è responsabile dei
contenuti che vi inserisce.</li>
<li><b>Testo del messaggio</b>: usato solo per elaborare la risposta; non viene conservato nel registro.</li>
<li><b>Dati tecnici</b>: identificativo del messaggio fornito da Meta, orari, esito della risposta.</li>
</ul>
<h2>Con chi vengono condivisi</h2>
<ul>
<li>Meta Platforms (WhatsApp Business Platform), che trasporta i messaggi.</li>
<li>Render, che ospita il servizio.</li>
<li>Il fornitore del modello linguistico usato per formulare la risposta (es. DeepSeek), che riceve
la domanda e gli estratti dei documenti pertinenti, non il numero di telefono.</li>
</ul>
<p>I dati non vengono venduti né usati per pubblicità.</p>
<h2>Conservazione</h2>
<p>Il registro tecnico serve a evitare risposte doppie e a limitare il numero di messaggi; viene
cancellato periodicamente e, nell'ambiente di prova, a ogni riavvio del servizio.</p>
<p>Le risposte ricevute su WhatsApp restano nel dispositivo dell'utente, che ne è responsabile:
la loro custodia e cancellazione spettano a lui.</p>
<h2 id="cancellazione">Diritti ed eliminazione dei dati</h2>
<p>Puoi chiedere accesso, rettifica o cancellazione dei tuoi dati, e la disattivazione del tuo numero,
scrivendo a <b>zroberto59@gmail.com</b> con oggetto "A’PLAUD’I — cancellazione dati" e indicando il
numero di telefono interessato. La richiesta viene evasa entro 30 giorni. Hai inoltre diritto di
proporre reclamo al Garante per la protezione dei dati personali.</p>
</body></html>"""


@router.get('/privacy', response_class=HTMLResponse)
async def privacy():
    if not enabled():
        raise HTTPException(404, 'WhatsApp gateway disabled')
    return HTMLResponse(PRIVACY_HTML)


# ------------------------------------------------------------------ rotte

@router.get('/webhook', response_class=PlainTextResponse)
async def verify(hub_mode: Optional[str] = Query(None, alias='hub.mode'),
                 hub_token: Optional[str] = Query(None, alias='hub.verify_token'),
                 hub_challenge: Optional[str] = Query(None, alias='hub.challenge')):
    cfg, _ = settings()
    if hub_mode != 'subscribe' or not hub_token or not hmac.compare_digest(
            hub_token.encode(), cfg['WHATSAPP_VERIFY_TOKEN'].encode()):
        raise HTTPException(403, 'Verification rejected')
    if not hub_challenge or not hub_challenge.isdigit() or len(hub_challenge) > 64:
        raise HTTPException(400, 'Invalid challenge')
    return hub_challenge


@router.post('/webhook')
async def inbound(request: Request, background: BackgroundTasks):
    cfg, bindings = settings()
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(413, 'Payload too large')
    expected = 'sha256=' + hmac.new(cfg['WHATSAPP_APP_SECRET'].encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get('X-Hub-Signature-256', '').encode(), expected.encode()):
        raise HTTPException(403, 'Invalid signature')
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(400, 'Invalid JSON')
    if not isinstance(payload, dict) or payload.get('object') != 'whatsapp_business_account':
        raise HTTPException(400, 'Invalid webhook object')
    db = cfg['WHATSAPP_INBOX_DB']
    expected_pid = cfg['WHATSAPP_PHONE_NUMBER_ID']
    if not db or not _PID.fullmatch(expected_pid):
        raise HTTPException(503, 'Missing inbox path or phone number id')
    now = int(time.time())
    accepted = 0
    for entry in payload.get('entry') or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get('changes') or []:
            val = change.get('value') if isinstance(change, dict) else None
            if not isinstance(val, dict):
                continue
            # Esiti dei messaggi inviati da noi (sent/delivered/read/failed): solo log.
            for st in val.get('statuses') or []:
                if isinstance(st, dict):
                    errs = st.get('errors') or []
                    log('stato messaggio', stato=st.get('status'), a=_mask(str(st.get('recipient_id', ''))),
                        errore=(f"{errs[0].get('code')}:{str(errs[0].get('title') or errs[0].get('message'))[:120]!r}"
                                if errs and isinstance(errs[0], dict) else '-'))
            # Solo messaggi arrivati al NOSTRO numero (test o produzione configurato).
            got_pid = str((val.get('metadata') or {}).get('phone_number_id', ''))
            if got_pid != expected_pid:
                if val.get('messages'):
                    log('scartato: phone_number_id diverso', ricevuto=got_pid, atteso=expected_pid)
                continue
            for msg in val.get('messages') or []:
                if not isinstance(msg, dict):
                    continue
                sender, mid = msg.get('from', ''), msg.get('id', '')
                if not isinstance(sender, str) or not _PHONE.fullmatch(sender):
                    log('scartato: mittente non valido')
                    continue
                if not isinstance(mid, str) or not _WAMID.fullmatch(mid):
                    log('scartato: id messaggio non valido', lunghezza=len(str(mid)))
                    continue
                tenant = bindings.get(sender)
                if not tenant and private_access is not None and msg.get('type') == 'text':
                    testo = (msg.get('text') or {}).get('body', '')
                    try:
                        if isinstance(testo, str) and private_access(sender, testo):
                            tenant = PRIVATE_ONLY
                    except Exception as error:
                        log('controllo spazio privato fallito', errore=type(error).__name__)
                if not tenant:
                    log('scartato: numero non abilitato', da=_mask(sender), cifre=len(sender))
                    continue            # numero sconosciuto: silenzio, nessun accesso
                try:
                    message_ts = int(msg.get('timestamp') or 0)
                except (TypeError, ValueError):
                    message_ts = 0
                if message_ts and now - message_ts > MAX_MESSAGE_AGE_S:
                    status, kind = 'expired', None
                else:
                    status, kind = 'queued', 'answer'
                question = ''
                if kind and msg.get('type') == 'text':
                    question = (msg.get('text') or {}).get('body', '')
                    if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_CHARS:
                        status, kind = 'invalid_question', None
                elif kind:
                    kind = 'not_text'
                shash = sender_hash(cfg['WHATSAPP_APP_SECRET'], sender)
                with closing(connection(db)) as conn:
                    inserted = conn.execute(
                        'INSERT OR IGNORE INTO wa_inbox (id, tenant, sender_hash, received_at, message_ts,'
                        ' status, updated_at) VALUES (?,?,?,?,?,?,?)',
                        (mid, tenant, shash, now, message_ts or None, status, now)).rowcount
                    if inserted and kind:
                        recent = conn.execute(
                            'SELECT COUNT(*) FROM wa_inbox WHERE sender_hash=? AND received_at>? AND id<>?'
                            " AND status NOT IN ('expired','invalid_question')",
                            (shash, now - 3600, mid)).fetchone()[0]
                        if recent >= max_per_hour():
                            kind = 'rate_limited'
                            # un solo avviso per ora: oltre, silenzio
                            warned = conn.execute(
                                "SELECT COUNT(*) FROM wa_inbox WHERE sender_hash=? AND received_at>?"
                                " AND status='rate_limited'", (shash, now - 3600)).fetchone()[0]
                            if warned:
                                kind = None
                                conn.execute("UPDATE wa_inbox SET status='rate_limited_silent' WHERE id=?", (mid,))
                    conn.commit()
                if not inserted or not kind:
                    log('scartato', motivo=('duplicato' if not inserted else status), tenant=tenant)
                    continue            # duplicato Meta, scaduto o non valido
                accepted += 1
                log('messaggio accettato', tenant=tenant, da=_mask(sender), tipo=kind)
                background.add_task(process_message, mid, sender, tenant, question.strip(), cfg, kind)
    # 200 subito: Meta non deve aspettare il modello (altrimenti ritrasmette).
    return {'ok': True, 'accepted': accepted}
