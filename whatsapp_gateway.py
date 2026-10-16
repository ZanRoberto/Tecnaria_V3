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
from fastapi.responses import PlainTextResponse

router = APIRouter(prefix='/api/whatsapp', tags=['WhatsApp'])

# Impostati da app.py. Nessuno dei due accetta un'azienda scelta dall'esterno.
document_responder: Optional[Callable[[str, str], Awaitable[str]]] = None
tenant_validator: Optional[Callable[[str], bool]] = None


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
_WAMID = re.compile(r'[a-zA-Z0-9_.:=-]{1,200}')
_PID = re.compile(r'\d{8,30}')


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
            response.raise_for_status()
    return len(parts)


# ------------------------------------------------------------------ elaborazione

async def process_message(mid: str, sender: str, tenant: str, question: str,
                          cfg: Dict[str, str], kind: str = 'answer') -> str:
    """Calcola la risposta e la invia. Non solleva mai: lo stato finisce nel registro."""
    db = cfg['WHATSAPP_INBOX_DB']
    started = time.perf_counter()
    state, body = 'answered', ''
    if kind == 'rate_limited':
        state, body = 'rate_limited', MSG_RATE
    elif kind == 'not_text':
        state, body = 'not_text', MSG_ONLY_TEXT
    else:
        try:
            if reply_mode() == 'documents':
                if document_responder is None:
                    raise RuntimeError('Document responder not configured')
                body = await document_responder(tenant, question)
                if not isinstance(body, str) or not body.strip():
                    raise RuntimeError('Empty answer')
            else:
                state, body = 'ack', MSG_ACK
        except ServiceBusy:
            state, body = 'busy', MSG_BUSY
        except Exception as error:   # niente dettagli interni al mittente
            print(f'[WHATSAPP] risposta non calcolata ({tenant}): {type(error).__name__}')
            state, body = 'answer_failed', MSG_ERROR
    try:
        parts = await send_reply(sender, cfg, body)
        final = state if state != 'answered' else 'sent'
    except Exception as error:
        print(f'[WHATSAPP] invio fallito ({tenant}): {type(error).__name__}')
        parts, final = 0, 'send_failed'
    try:
        _set_status(db, mid, final, answer_chars=len(body), parts=parts,
                    duration_ms=int((time.perf_counter() - started) * 1000))
    except Exception as error:
        print(f'[WHATSAPP] registro non aggiornato: {type(error).__name__}')
    return final


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
            # Solo messaggi arrivati al NOSTRO numero (test o produzione configurato).
            if str((val.get('metadata') or {}).get('phone_number_id', '')) != expected_pid:
                continue
            for msg in val.get('messages') or []:
                if not isinstance(msg, dict):
                    continue
                sender, mid = msg.get('from', ''), msg.get('id', '')
                if not isinstance(sender, str) or not _PHONE.fullmatch(sender):
                    continue
                if not isinstance(mid, str) or not _WAMID.fullmatch(mid):
                    continue
                tenant = bindings.get(sender)
                if not tenant:
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
                    continue            # duplicato Meta, scaduto o non valido
                accepted += 1
                background.add_task(process_message, mid, sender, tenant, question.strip(), cfg, kind)
    # 200 subito: Meta non deve aspettare il modello (altrimenti ritrasmette).
    return {'ok': True, 'accepted': accepted}
