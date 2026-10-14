"""Connettore multi-cliente per il Remote MCP ufficiale di PLAUD.

Ogni tenant autorizza il proprio account tramite OAuth. Le credenziali PLAUD non
passano mai dall'applicazione. I token OAuth sono cifrati prima della persistenza.
Il modulo e' indipendente dal motore Narratore/Risponditore.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode, urljoin, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import Column, Integer, LargeBinary, String, Text, create_engine, select
from sqlalchemy.orm import Session, declarative_base


PLAUD_MCP_URL = os.getenv("PLAUD_MCP_URL", "https://mcp.plaud.ai/mcp").rstrip("/")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./applaudi_oauth.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgres://"):]
elif DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgresql://"):]
TENANT_KEYS = json.loads(os.getenv("APPLAUDI_TENANT_KEYS_JSON", "{}") or "{}")
FERNET_KEY = os.getenv("PLAUD_TOKEN_ENCRYPTION_KEY", "").strip()

router = APIRouter(prefix="/api/plaud/cloud", tags=["PLAUD Cloud"])
dashboard_router = APIRouter(tags=["PLAUD Dashboard"])
Base = declarative_base()


class OAuthRecord(Base):
    __tablename__ = "plaud_oauth"
    id = Column(Integer, primary_key=True)
    tenant = Column(String(120), unique=True, nullable=False, index=True)
    client_info = Column(LargeBinary, nullable=True)
    tokens = Column(LargeBinary, nullable=True)
    updated_at = Column(Integer, nullable=False, default=lambda: int(time.time()))


class PendingOAuth(Base):
    __tablename__ = "plaud_oauth_pending"
    state = Column(String(160), primary_key=True)
    tenant = Column(String(120), nullable=False, index=True)
    verifier = Column(String(180), nullable=False)
    oauth_metadata = Column("metadata", Text, nullable=False)
    expires_at = Column(Integer, nullable=False)


_engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Base.metadata.create_all(_engine)


def _cipher() -> Fernet:
    if not FERNET_KEY:
        raise HTTPException(503, "PLAUD_TOKEN_ENCRYPTION_KEY non configurata")
    try:
        return Fernet(FERNET_KEY.encode("ascii"))
    except Exception as exc:
        raise HTTPException(503, "PLAUD_TOKEN_ENCRYPTION_KEY non valida") from exc


def _seal(value: Dict[str, Any]) -> bytes:
    return _cipher().encrypt(json.dumps(value, separators=(",", ":")).encode())


def _open(value: Optional[bytes]) -> Dict[str, Any]:
    if not value:
        return {}
    try:
        return json.loads(_cipher().decrypt(value).decode())
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(503, "Archivio OAuth PLAUD non leggibile") from exc


def _tenant(tenant: str) -> str:
    value = (tenant or "").strip().lower()
    if not value or len(value) > 120 or not all(c.isalnum() or c in "-_" for c in value):
        raise HTTPException(400, "Identificativo cliente non valido")
    return value


def verify_tenant_key(tenant: str, supplied: Optional[str]) -> str:
    tenant = _tenant(tenant)
    expected = str(TENANT_KEYS.get(tenant) or "")
    if not expected:
        raise HTTPException(403, "Cliente non abilitato al collegamento PLAUD")
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "Chiave cliente non valida")
    return tenant


async def _get_json(client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    response = await client.get(url, headers={"Accept": "application/json"})
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise HTTPException(502, "Metadati OAuth PLAUD non validi")
    return result


async def _discover() -> Dict[str, Any]:
    parsed = urlparse(PLAUD_MCP_URL)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [
        origin + "/.well-known/oauth-protected-resource" + parsed.path,
        origin + "/.well-known/oauth-protected-resource",
    ]
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        resource = None
        for url in candidates:
            try:
                resource = await _get_json(client, url)
                break
            except Exception:
                continue
        if not resource:
            raise HTTPException(502, "PLAUD non espone i metadati OAuth attesi")
        servers = resource.get("authorization_servers") or []
        issuer = str(servers[0] if servers else origin).rstrip("/")
        issuer_path = urlparse(issuer).path.rstrip("/")
        auth_meta_urls = [
            origin + "/.well-known/oauth-authorization-server" + issuer_path,
            issuer + "/.well-known/oauth-authorization-server",
        ]
        auth = None
        for url in auth_meta_urls:
            try:
                auth = await _get_json(client, url)
                break
            except Exception:
                continue
        if not auth:
            raise HTTPException(502, "Server OAuth PLAUD non rilevabile")
        return {"resource": resource, "auth": auth}


def _redirect_uri() -> str:
    if not PUBLIC_BASE_URL.startswith("https://"):
        raise HTTPException(503, "PUBLIC_BASE_URL HTTPS non configurata")
    return PUBLIC_BASE_URL + "/api/plaud/cloud/callback"


async def _ensure_client(tenant: str, auth: Dict[str, Any]) -> Dict[str, Any]:
    with Session(_engine) as db:
        row = db.scalar(select(OAuthRecord).where(OAuthRecord.tenant == tenant))
        saved = _open(row.client_info) if row and row.client_info else {}
    if saved.get("client_id"):
        return saved
    registration = auth.get("registration_endpoint")
    if not registration:
        raise HTTPException(502, "Registrazione dinamica OAuth non disponibile su PLAUD")
    payload = {
        "client_name": "A'PLAUD'I",
        "redirect_uris": [_redirect_uri()],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(registration, json=payload)
        response.raise_for_status()
        saved = response.json()
    with Session(_engine) as db:
        row = db.scalar(select(OAuthRecord).where(OAuthRecord.tenant == tenant)) or OAuthRecord(tenant=tenant)
        row.client_info = _seal(saved)
        row.updated_at = int(time.time())
        db.add(row)
        db.commit()
    return saved


@router.post("/connect/{tenant}")
async def connect(tenant: str, x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    tenant = verify_tenant_key(tenant, x_applaudi_tenant_key)
    metadata = await _discover()
    client_info = await _ensure_client(tenant, metadata["auth"])
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    scope = " ".join(metadata["auth"].get("scopes_supported") or [])
    params = {
        "response_type": "code",
        "client_id": client_info["client_id"],
        "redirect_uri": _redirect_uri(),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": PLAUD_MCP_URL,
    }
    if scope:
        params["scope"] = scope
    with Session(_engine) as db:
        db.query(PendingOAuth).filter(PendingOAuth.expires_at < int(time.time())).delete()
        db.add(PendingOAuth(state=state, tenant=tenant, verifier=verifier,
                            oauth_metadata=json.dumps(metadata), expires_at=int(time.time()) + 600))
        db.commit()
    return {"authorization_url": metadata["auth"]["authorization_endpoint"] + "?" + urlencode(params)}


@router.get("/callback")
async def callback(code: str = Query(...), state: str = Query(...)):
    with Session(_engine) as db:
        pending = db.get(PendingOAuth, state)
        if not pending or pending.expires_at < int(time.time()):
            raise HTTPException(400, "Autorizzazione PLAUD scaduta o non valida")
        tenant, verifier, metadata = pending.tenant, pending.verifier, json.loads(pending.oauth_metadata)
        row = db.scalar(select(OAuthRecord).where(OAuthRecord.tenant == tenant))
        client_info = _open(row.client_info if row else None)
    data = {
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": _redirect_uri(), "client_id": client_info["client_id"],
        "code_verifier": verifier,
    }
    if client_info.get("client_secret"):
        data["client_secret"] = client_info["client_secret"]
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(metadata["auth"]["token_endpoint"], data=data)
        response.raise_for_status()
        tokens = response.json()
    tokens["obtained_at"] = int(time.time())
    if tokens.get("expires_in"):
        tokens["expires_at"] = int(time.time()) + int(tokens["expires_in"])
    with Session(_engine) as db:
        row = db.scalar(select(OAuthRecord).where(OAuthRecord.tenant == tenant))
        row.tokens, row.updated_at = _seal(tokens), int(time.time())
        db.delete(db.get(PendingOAuth, state))
        db.commit()
    return RedirectResponse(PUBLIC_BASE_URL + f"/plaud/{tenant}?connected=1", status_code=303)


def _tokens(tenant: str) -> Dict[str, Any]:
    with Session(_engine) as db:
        row = db.scalar(select(OAuthRecord).where(OAuthRecord.tenant == tenant))
        value = _open(row.tokens) if row and row.tokens else {}
    if not value.get("access_token"):
        raise HTTPException(409, "Account PLAUD non collegato")
    return value


async def _access_token(tenant: str) -> str:
    tokens = _tokens(tenant)
    expires_at = int(tokens.get("expires_at") or 0)
    if not expires_at or expires_at > int(time.time()) + 60:
        return str(tokens["access_token"])
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise HTTPException(409, "Autorizzazione PLAUD scaduta: ricollegare l'account")
    metadata = await _discover()
    with Session(_engine) as db:
        row = db.scalar(select(OAuthRecord).where(OAuthRecord.tenant == tenant))
        client_info = _open(row.client_info if row else None)
    data = {
        "grant_type": "refresh_token", "refresh_token": refresh_token,
        "client_id": client_info["client_id"],
    }
    if client_info.get("client_secret"):
        data["client_secret"] = client_info["client_secret"]
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(metadata["auth"]["token_endpoint"], data=data)
        if response.status_code >= 400:
            raise HTTPException(409, "Autorizzazione PLAUD scaduta: ricollegare l'account")
        refreshed = response.json()
    refreshed.setdefault("refresh_token", refresh_token)
    refreshed["obtained_at"] = int(time.time())
    if refreshed.get("expires_in"):
        refreshed["expires_at"] = int(time.time()) + int(refreshed["expires_in"])
    with Session(_engine) as db:
        row = db.scalar(select(OAuthRecord).where(OAuthRecord.tenant == tenant))
        row.tokens, row.updated_at = _seal(refreshed), int(time.time())
        db.commit()
    return str(refreshed["access_token"])


def _decode_mcp(response: httpx.Response) -> Dict[str, Any]:
    if "text/event-stream" not in response.headers.get("content-type", ""):
        return response.json()
    for line in response.text.splitlines():
        if line.startswith("data:"):
            value = json.loads(line[5:].strip())
            if isinstance(value, dict) and ("result" in value or "error" in value):
                return value
    raise HTTPException(502, "Risposta MCP PLAUD non interpretabile")


async def _mcp_call(tenant: str, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    token = await _access_token(tenant)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
    async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
        init = await client.post(PLAUD_MCP_URL, headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "A'PLAUD'I", "version": "1.0"}},
        })
        if init.status_code == 401:
            raise HTTPException(409, "Autorizzazione PLAUD scaduta: ricollegare l'account")
        init.raise_for_status()
        session_id = init.headers.get("Mcp-Session-Id")
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        await client.post(PLAUD_MCP_URL, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        response = await client.post(PLAUD_MCP_URL, headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": method, "params": params,
        })
        response.raise_for_status()
        result = _decode_mcp(response)
        if result.get("error"):
            raise HTTPException(502, f"Errore PLAUD: {result['error'].get('message', 'chiamata MCP fallita')}")
        return result.get("result") or {}


@router.get("/status/{tenant}")
async def status(tenant: str, x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    tenant = verify_tenant_key(tenant, x_applaudi_tenant_key)
    try:
        _tokens(tenant)
        connected = True
    except HTTPException:
        connected = False
    return {"tenant": tenant, "connected": connected, "provider": "PLAUD Remote MCP"}


@router.get("/recordings/{tenant}")
async def recordings(tenant: str, query: Optional[str] = None, date_from: Optional[str] = None,
                     date_to: Optional[str] = None,
                     x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    tenant = verify_tenant_key(tenant, x_applaudi_tenant_key)
    arguments = {k: v for k, v in {"query": query, "date_from": date_from, "date_to": date_to}.items() if v}
    return await _mcp_call(tenant, "tools/call", {"name": "list_files", "arguments": arguments})


async def get_recording_bundle(tenant: str, recording_id: str) -> Dict[str, Any]:
    transcript = await _mcp_call(tenant, "tools/call", {"name": "get_transcript", "arguments": {"id": recording_id}})
    note = await _mcp_call(tenant, "tools/call", {"name": "get_note", "arguments": {"id": recording_id}})
    detail = await _mcp_call(tenant, "tools/call", {"name": "get_file", "arguments": {"id": recording_id}})
    return {"detail": detail, "transcript": transcript, "note": note}


@router.get("/recording/{tenant}/{recording_id}")
async def recording(tenant: str, recording_id: str,
                    x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    tenant = verify_tenant_key(tenant, x_applaudi_tenant_key)
    return await get_recording_bundle(tenant, recording_id)


@router.delete("/disconnect/{tenant}")
async def disconnect(tenant: str, x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    tenant = verify_tenant_key(tenant, x_applaudi_tenant_key)
    with Session(_engine) as db:
        row = db.scalar(select(OAuthRecord).where(OAuthRecord.tenant == tenant))
        if row:
            row.tokens, row.updated_at = None, int(time.time())
            db.commit()
    return {"tenant": tenant, "connected": False}


@dashboard_router.get("/plaud/{tenant}", response_class=HTMLResponse)
async def dashboard(tenant: str):
    tenant = _tenant(tenant)
    safe_tenant = json.dumps(tenant)
    return HTMLResponse(f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A'PLAUD'I · {tenant}</title>
<style>
body{{margin:0;background:#0d1117;color:#f4f6f8;font:16px system-ui}}main{{max-width:1050px;margin:38px auto;padding:0 20px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:18px;padding:22px;margin:16px 0}}h1{{margin:0 0 6px}}
button,input{{border-radius:10px;padding:12px 15px;font:inherit}}input{{min-width:320px;background:#0d1117;color:white;border:1px solid #555}}
button{{border:0;background:#d5b078;color:#111;font-weight:700;cursor:pointer;margin:5px}}button.secondary{{background:#30363d;color:white}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#090c10;padding:16px;border-radius:12px;max-height:420px;overflow:auto}}
.ok{{color:#4ee69b}}.warn{{color:#ffca68}}.row{{display:flex;gap:10px;flex-wrap:wrap;align-items:center}}
</style></head><body><main>
<h1>A'PLAUD'I</h1><div>Cruscotto PLAUD · cliente <b>{tenant}</b></div>
<section class="card"><h2>Accesso protetto</h2><div class="row"><input id="key" type="password" placeholder="Chiave personale del cliente">
<button onclick="saveKey()">Entra</button></div><p id="state" class="warn">Inserisci la chiave personale.</p></section>
<section class="card"><h2>Account PLAUD</h2><button onclick="connectPlaud()">Collega PLAUD</button>
<button class="secondary" onclick="loadRecordings()">Mostra registrazioni</button></section>
<section class="card"><h2>Registrazioni</h2><div id="recordings">Nessuna registrazione caricata.</div></section>
<section class="card"><h2>Narratore · risultato</h2><pre id="result">Seleziona una registrazione e premi Analizza.</pre></section>
</main><script>
const tenant={safe_tenant}; const storageKey='applaudi-key-'+tenant;
const keyEl=document.getElementById('key'); keyEl.value=sessionStorage.getItem(storageKey)||'';
function headers(){{return {{'X-Applaudi-Tenant-Key':keyEl.value}}}}
function saveKey(){{sessionStorage.setItem(storageKey,keyEl.value); checkStatus()}}
async function request(url,options={{}}){{options.headers=Object.assign({{}},options.headers||{{}},headers()); const r=await fetch(url,options);
 const data=await r.json().catch(()=>({{detail:'Risposta non leggibile'}})); if(!r.ok)throw new Error(data.detail||('Errore '+r.status)); return data}}
async function checkStatus(){{try{{const x=await request('/api/plaud/cloud/status/'+tenant); const s=document.getElementById('state');
 s.textContent=x.connected?'Account PLAUD collegato':'Chiave valida · account PLAUD non ancora collegato';s.className=x.connected?'ok':'warn'}}catch(e){{state.textContent=e.message;state.className='warn'}}}}
async function connectPlaud(){{try{{const x=await request('/api/plaud/cloud/connect/'+tenant,{{method:'POST'}});location.href=x.authorization_url}}catch(e){{alert(e.message)}}}}
function allIds(x){{const text=JSON.stringify(x);return [...new Set(text.match(/of_[a-fA-F0-9]{{16,64}}/g)||[])]}}
async function loadRecordings(){{const box=document.getElementById('recordings');box.textContent='Caricamento…';try{{const x=await request('/api/plaud/cloud/recordings/'+tenant);
 const ids=allIds(x);box.innerHTML='';if(!ids.length){{box.innerHTML='<pre>'+escapeHtml(JSON.stringify(x,null,2))+'</pre>';return}}
 ids.forEach(id=>{{const d=document.createElement('div');d.className='row';d.innerHTML='<code>'+id+'</code><button>Analizza con il Narratore</button>';
 d.querySelector('button').onclick=()=>analyze(id);box.appendChild(d)}})}}catch(e){{box.textContent=e.message}}}}
async function analyze(id){{const out=document.getElementById('result');out.textContent='Analisi in corso…';try{{const x=await request('/api/plaud/cloud/analyze/'+tenant+'/'+id,{{method:'POST'}});out.textContent=JSON.stringify(x,null,2)}}catch(e){{out.textContent=e.message}}}}
function escapeHtml(s){{return s.replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]))}}
if(keyEl.value)checkStatus();
</script></body></html>""")
