"""Meta WhatsApp test gateway. No enterprise document access; safe acknowledgement only.
Enabled only with WHATSAPP_GATEWAY_ENABLED=true; incoming senders must be explicitly
provisioned; verify Meta signature; bind tenant server-side; prevent duplicate replies.
"""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from typing import Callable, Awaitable
from contextlib import closing
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
import httpx

router = APIRouter(prefix='/api/whatsapp', tags=['WhatsApp'])
# Supplied only by app.py; no direct untrusted tenant selector.
document_responder: Callable[[str, str], Awaitable[str]] | None = None



def settings():
    if os.getenv('WHATSAPP_GATEWAY_ENABLED','false').lower() != 'true':
        raise HTTPException(404, 'WhatsApp gateway disabled')
    try:
        bindings = json.loads(os.getenv('WHATSAPP_PHONE_BINDINGS_JSON','{}'))
    except ValueError:
        raise HTTPException(503, 'Invalid binding configuration')
    if not isinstance(bindings,dict) or any(not re.fullmatch(r'\d{7,16}',str(k)) or not re.fullmatch(r'[a-z0-9_-]{1,40}',str(v)) for k,v in bindings.items()):
        raise HTTPException(503, 'Invalid binding configuration')
    values = {k:os.getenv(k,'') for k in ('WHATSAPP_VERIFY_TOKEN','WHATSAPP_APP_SECRET','WHATSAPP_ACCESS_TOKEN','WHATSAPP_PHONE_NUMBER_ID','WHATSAPP_INBOX_DB')}
    if not values['WHATSAPP_VERIFY_TOKEN'] or not values['WHATSAPP_APP_SECRET']:
        raise HTTPException(503, 'Missing Meta verification credentials')
    return values, bindings


def connection(path):
    # SQLite is ONLY for limited single-instance pilot with persistent disk.
    conn=sqlite3.connect(path,timeout=10)
    conn.execute('CREATE TABLE IF NOT EXISTS wa_messages (id TEXT PRIMARY KEY,tenant TEXT NOT NULL,received_at INTEGER NOT NULL,status TEXT NOT NULL)')
    conn.commit()
    return conn


@router.get('/webhook',response_class=PlainTextResponse)
async def verify(hub_mode: str | None=Query(None,alias='hub.mode'), hub_token: str | None=Query(None,alias='hub.verify_token'), hub_challenge: str | None=Query(None,alias='hub.challenge')):
    cfg,_=settings()
    if hub_mode!='subscribe' or not hub_token or not hmac.compare_digest(hub_token,cfg['WHATSAPP_VERIFY_TOKEN']):
        raise HTTPException(403,'Verification rejected')
    if not hub_challenge or not hub_challenge.isdigit():
        raise HTTPException(400,'Invalid challenge')
    return hub_challenge


async def send_reply(sender: str, tenant: str, cfg: dict, body: str):
    access=cfg['WHATSAPP_ACCESS_TOKEN']; pid=cfg['WHATSAPP_PHONE_NUMBER_ID']
    if not access or not re.fullmatch(r'\d{8,30}',pid):
        raise RuntimeError('Missing WhatsApp outbound credentials')
    version=os.getenv('WHATSAPP_GRAPH_VERSION','v23.0')
    if not re.fullmatch(r'v\d+\.\d+',version):
        raise RuntimeError('Invalid Graph version')
    # No customer-specific information is sent until document access is implemented.
    payload={'messaging_product':'whatsapp','to':sender,'type':'text','text':{'body':body[:4000]}}
    async with httpx.AsyncClient(timeout=15) as client:
        response=await client.post(f'https://graph.facebook.com/{version}/{pid}/messages',json=payload,headers={'Authorization':f'Bearer {access}'})
    response.raise_for_status()


@router.post('/webhook')
async def inbound(request: Request):
    cfg,bindings=settings()
    raw=await request.body()
    if len(raw)>1024*1024: raise HTTPException(413,'Payload too large')
    expected='sha256='+hmac.new(cfg['WHATSAPP_APP_SECRET'].encode(),raw,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get('X-Hub-Signature-256',''),expected):
        raise HTTPException(403,'Invalid signature')
    try: payload=json.loads(raw)
    except ValueError: raise HTTPException(400,'Invalid JSON')
    if not isinstance(payload,dict) or payload.get('object')!='whatsapp_business_account':
        raise HTTPException(400,'Invalid webhook object')
    db=cfg['WHATSAPP_INBOX_DB']
    if not db: raise HTTPException(503,'Missing persistent inbox path')
    accepted=0
    for entry in payload.get('entry',[]):
        for change in entry.get('changes',[]):
            val=change.get('value',{})
            for msg in val.get('messages',[]):
                sender,mid=msg.get('from',''),msg.get('id','')
                if not isinstance(sender,str) or not re.fullmatch(r'\d{7,16}',sender):continue
                if not isinstance(mid,str) or not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,200}',mid):continue
                if msg.get('type')!='text':continue
                tenant=bindings.get(sender)
                if not tenant:continue
                # Explicitly reject messages routed via a different test/production Meta number.
                expected_pid=cfg['WHATSAPP_PHONE_NUMBER_ID']
                actual_pid=str(val.get('metadata',{}).get('phone_number_id',''))
                if not expected_pid or actual_pid!=expected_pid:continue
                with closing(connection(db)) as conn:
                    inserted=conn.execute('INSERT OR IGNORE INTO wa_messages VALUES(?,?,?,?)',(mid,tenant,int(time.time()),'received')).rowcount
                    conn.commit()
                if not inserted:continue
                accepted+=1
                try:
                    mode=os.getenv('WHATSAPP_REPLY_MODE','ack').strip().lower()
                    if mode=='documents':
                        if document_responder is None:
                            raise RuntimeError('Document responder not configured')
                        question=msg.get('text',{}).get('body','')
                        if not isinstance(question,str) or not question.strip() or len(question)>3000:
                            raise ValueError('Invalid question')
                        body=await document_responder(tenant, question.strip())
                    elif mode=='ack':
                        body='A’PLAUD’I: messaggio di prova ricevuto. La consultazione documentale non è ancora attiva.'
                    else:
                        raise RuntimeError('Unsupported reply mode')
                    await send_reply(sender,tenant,cfg,body)
                    state='sent'
                except Exception:
                    state='send_failed'
                with closing(connection(db)) as conn:
                    conn.execute('UPDATE wa_messages SET status=? WHERE id=?',(state,mid));conn.commit()
    return {'ok':True,'accepted':accepted}
