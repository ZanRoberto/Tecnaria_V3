"""Connettore multi-cliente per il Remote MCP ufficiale di PLAUD.

Ogni tenant autorizza il proprio account tramite OAuth. Le credenziali PLAUD non
passano mai dall'applicazione. I token OAuth sono cifrati prima della persistenza.
Il modulo e' indipendente dal motore Narratore/Risponditore.
"""

from __future__ import annotations

import base64
import html
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
    # Il Remote MCP ufficiale di PLAUD espone l'identificativo come ``file_id``.
    # ``id`` viene rifiutato dallo schema MCP con errore -32602, anche quando
    # contiene un identificativo valido restituito da list_files.
    arguments = {"file_id": recording_id}
    transcript = await _mcp_call(tenant, "tools/call", {"name": "get_transcript", "arguments": arguments})
    note = await _mcp_call(tenant, "tools/call", {"name": "get_note", "arguments": arguments})
    detail = await _mcp_call(tenant, "tools/call", {"name": "get_file", "arguments": arguments})
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
    page = """<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A'PLAUD'I · __TENANT__</title>
<style>
:root{--bg:#070b12;--panel:#111722;--panel2:#151d2a;--line:#263246;--text:#f3f6fa;--muted:#91a0b6;--cyan:#26d7e8;--green:#35e49a;--gold:#d8b277;--danger:#ff7b82}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 82% 0,#102a37 0,transparent 28%),var(--bg);color:var(--text);font:15px Inter,Segoe UI,system-ui,sans-serif}
.app{min-height:100vh;display:grid;grid-template-columns:245px 1fr}.side{background:#0a0f17;border-right:1px solid var(--line);padding:27px 20px;position:sticky;top:0;height:100vh}
.logo{font-size:27px;font-weight:850;letter-spacing:1.5px;margin:0 10px 36px}.logo i{font-style:normal;color:var(--cyan)}.nav{display:grid;gap:7px}.nav div{padding:12px 14px;border-radius:10px;color:#aeb9ca}.nav .active{background:#122532;color:white;border:1px solid #1f4d5b}.nav b{display:inline-block;width:25px;color:var(--cyan)}
.identity{position:absolute;bottom:24px;left:25px;color:var(--muted);font-size:12px}.identity strong{display:block;color:white;font-size:14px;margin-bottom:4px}.main{padding:32px 38px 60px;max-width:1500px;width:100%}
.top{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:26px}.top h1{font-size:29px;margin:0 0 5px}.muted{color:var(--muted)}.status{padding:9px 13px;border-radius:22px;background:#0d2a22;border:1px solid #1f664e;color:var(--green);font-weight:700}
.access{background:#111722;border:1px solid var(--line);border-radius:15px;padding:17px 20px;margin-bottom:20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}.access input{flex:1;min-width:260px}
button,input{font:inherit;border-radius:9px;padding:11px 14px}input{background:#090e16;color:white;border:1px solid #3a4659}button{border:0;background:var(--cyan);color:#031014;font-weight:800;cursor:pointer}button.secondary{background:#202b3b;color:white;border:1px solid #36445a}button:disabled{opacity:.5;cursor:not-allowed}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:15px;margin-bottom:20px}.kpi,.card{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:16px}.kpi{padding:17px}.kpi small{display:block;color:var(--muted);margin-bottom:8px}.kpi strong{font-size:19px}.ok{color:var(--green)}.warn{color:#ffc96a}
.grid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(330px,.8fr);gap:18px}.card{padding:21px}.card h2{font-size:18px;margin:0}.head{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}.head button{padding:8px 11px;font-size:13px}
.record{display:grid;grid-template-columns:minmax(0,1fr) 140px 100px 105px;align-items:center;gap:10px;padding:13px 4px;border-top:1px solid #263246}.record:first-child{border-top:0}.record .name{font-weight:700}.record .meta{font-size:12px;color:var(--muted);margin-top:4px}.record button{font-size:12px;padding:8px}.empty{padding:30px 8px;color:var(--muted);text-align:center}
.analysis-title{font-size:17px;font-weight:800;margin:4px 0 13px}.section{border-top:1px solid var(--line);padding-top:13px;margin-top:13px}.section h3{font-size:12px;letter-spacing:.9px;text-transform:uppercase;color:var(--cyan);margin:0 0 8px}.section ul{margin:0;padding-left:19px}.section li{margin:7px 0;line-height:1.4}.pill{display:inline-block;border:1px solid #365067;border-radius:20px;padding:5px 9px;color:#b8c6d9;font-size:11px}.loading{color:var(--cyan)}.error{color:var(--danger)}
.bottom{display:grid;grid-template-columns:repeat(3,1fr);gap:15px;margin-top:18px}.mini{min-height:125px}.mini p{color:var(--muted);line-height:1.45}.debug{white-space:pre-wrap;overflow-wrap:anywhere;font:12px ui-monospace;background:#080c12;padding:12px;border-radius:9px;max-height:260px;overflow:auto}
@media(max-width:980px){.app{grid-template-columns:1fr}.side{display:none}.main{padding:22px}.kpis{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}.bottom{grid-template-columns:1fr}.record{grid-template-columns:1fr auto}.record .hide-small{display:none}}
</style></head><body><div class="app">
<aside class="side"><div class="logo">A'PLAUD'<i>I</i></div><div class="nav">
<div class="active"><b>◈</b> Panoramica</div><div><b>◉</b> Registrazioni PLAUD</div><div><b>✦</b> Narratore</div><div><b>▤</b> Documenti aziendali</div><div><b>◇</b> Proposte</div><div><b>⚙</b> Motori</div><div><b>⌁</b> Supervisione</div><div><b>·</b> Impostazioni</div></div>
<div class="identity"><strong>Roberto Zannoni</strong>Supervisore · __TENANT_UPPER__</div></aside>
<main class="main"><div class="top"><div><h1>Buongiorno, Roberto</h1><div class="muted">Intelligenza aziendale certificata · controllo documentale e operativo</div></div><div id="topStatus" class="status">● Verifica collegamento</div></div>
<section class="access"><strong>Accesso protetto</strong><input id="key" type="password" placeholder="Chiave personale del cliente"><button onclick="saveKey()">Entra</button><button class="secondary" onclick="connectPlaud()">Collega PLAUD</button><span id="state" class="warn">Inserisci la chiave personale.</span></section>
<section class="kpis"><div class="kpi"><small>PLAUD</small><strong id="plaudKpi">Da collegare</strong></div><div class="kpi"><small>Registrazioni disponibili</small><strong id="recordingCount">—</strong></div><div class="kpi"><small>Documenti indicizzati</small><strong>Patrimonio aziendale</strong></div><div class="kpi"><small>Motore risposte</small><strong class="ok">Narratore attivo</strong></div></section>
<section class="grid"><div class="card"><div class="head"><div><h2>Registrazioni recenti</h2><span class="muted">Seleziona una conversazione da analizzare</span></div><button onclick="loadRecordings()">Aggiorna</button></div><div id="recordings"><div class="empty">Premi Aggiorna per caricare le registrazioni.</div></div></div>
<div class="card"><div class="head"><div><h2>Narratore</h2><span class="muted">Analisi della conversazione</span></div><span id="reviewPill" class="pill">In attesa</span></div><div id="result"><div class="empty">Seleziona una registrazione. Qui appariranno fatti, lacune e prossime azioni.</div></div></div></section>
<section class="bottom"><div class="card mini"><h2>Documenti aziendali</h2><p>Cataloghi, listini, manuali e procedure indicizzati separatamente per azienda.</p><span class="pill">Archivio protetto</span></div><div class="card mini"><h2>Proposte</h2><p>Le proposte nascono soltanto da prodotti e condizioni confermati.</p><span class="pill">Revisione richiesta</span></div><div class="card mini"><h2>Supervisione</h2><p>Controllo centralizzato dei clienti, dei motori e delle verifiche aperte.</p><span class="pill">Solo supervisore</span></div></section>
</main></div><script>
const tenant=__SAFE_TENANT__;const storageKey='applaudi-key-'+tenant;const keyEl=document.getElementById('key');keyEl.value=sessionStorage.getItem(storageKey)||'';
function headers(){return {'X-Applaudi-Tenant-Key':keyEl.value}}
function escapeHtml(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function request(url,options={}){options.headers=Object.assign({},options.headers||{},headers());const r=await fetch(url,options);const data=await r.json().catch(()=>({detail:'Risposta non leggibile'}));if(!r.ok)throw new Error(data.detail||('Errore '+r.status));return data}
function saveKey(){sessionStorage.setItem(storageKey,keyEl.value);checkStatus()}
async function checkStatus(){const s=document.getElementById('state');try{const x=await request('/api/plaud/cloud/status/'+tenant);s.textContent=x.connected?'Account PLAUD collegato':'Chiave valida · account PLAUD non ancora collegato';s.className=x.connected?'ok':'warn';document.getElementById('plaudKpi').textContent=x.connected?'Collegato':'Da collegare';document.getElementById('plaudKpi').className=x.connected?'ok':'warn';document.getElementById('topStatus').textContent=x.connected?'● Sistema operativo':'● Collegamento richiesto';if(x.connected)loadRecordings()}catch(e){s.textContent=e.message;s.className='error';document.getElementById('topStatus').textContent='● Accesso richiesto'}}
async function connectPlaud(){try{const x=await request('/api/plaud/cloud/connect/'+tenant,{method:'POST'});location.href=x.authorization_url}catch(e){alert(e.message)}}
function parseMaybeJson(s){try{return JSON.parse(s)}catch(e){return null}}
function collectRecords(value,out=[]){if(typeof value==='string'){const parsed=parseMaybeJson(value.trim());if(parsed)collectRecords(parsed,out);return out}if(Array.isArray(value)){value.forEach(v=>collectRecords(v,out));return out}if(value&&typeof value==='object'){const id=value.id||value.file_id;if(typeof id==='string'&&id.startsWith('of_'))out.push({id,name:value.name||'',created_at:value.start_at||value.created_at||'',duration:value.duration});Object.values(value).forEach(v=>collectRecords(v,out))}return out}
function fallbackIds(x){return [...new Set((JSON.stringify(x).match(/of_[a-fA-F0-9]{16,64}/g)||[]))].map(id=>({id,name:'',created_at:'',duration:null}))}
function formatDate(x){if(!x)return 'Data non disponibile';const d=new Date(x);return isNaN(d)?x:d.toLocaleString('it-IT',{dateStyle:'short',timeStyle:'short'})}
function formatDuration(ms){if(ms===null||ms===undefined)return '—';let sec=Number(ms)>10000?Math.round(Number(ms)/1000):Math.round(Number(ms));if(!isFinite(sec))return '—';return Math.floor(sec/60)+'m '+String(sec%60).padStart(2,'0')+'s'}
async function loadRecordings(){const box=document.getElementById('recordings');box.innerHTML='<div class="empty loading">Caricamento registrazioni…</div>';try{const x=await request('/api/plaud/cloud/recordings/'+tenant);let records=collectRecords(x);if(!records.length)records=fallbackIds(x);const unique=[...new Map(records.map(r=>[r.id,r])).values()];document.getElementById('recordingCount').textContent=unique.length;box.innerHTML='';if(!unique.length){box.innerHTML='<div class="empty">Nessuna registrazione disponibile.</div>';return}unique.forEach(r=>{const row=document.createElement('div');row.className='record';row.innerHTML='<div><div class="name">'+escapeHtml(r.name||'Registrazione senza titolo')+'</div><div class="meta">'+escapeHtml(r.id)+'</div></div><div class="hide-small">'+escapeHtml(formatDate(r.created_at))+'</div><div class="hide-small">'+escapeHtml(formatDuration(r.duration))+'</div><button>Analizza</button>';row.querySelector('button').onclick=()=>analyze(r.id,r.name);box.appendChild(row)})}catch(e){box.innerHTML='<div class="empty error">'+escapeHtml(e.message)+'</div>'}}
function list(items){return '<ul>'+((items||[]).map(x=>'<li>'+escapeHtml(x)+'</li>').join('')||'<li>Nessun elemento.</li>')+'</ul>'}
async function analyze(id,label){const out=document.getElementById('result');out.innerHTML='<div class="empty loading">Il Narratore sta analizzando la registrazione…</div>';document.getElementById('reviewPill').textContent='Analisi in corso';try{const x=await request('/api/plaud/cloud/analyze/'+tenant+'/'+id,{method:'POST'});const a=x.analysis||{};let catalog='';if(x.catalog_answer&&x.catalog_answer.answer){catalog='<div class="section"><h3>Risposta del Super-Risponditore</h3><div style="white-space:pre-wrap;line-height:1.55">'+escapeHtml(x.catalog_answer.answer)+'</div></div>'}else if(a.rilevanza_catalogo===true){catalog='<div class="section"><h3>Risposta del Super-Risponditore</h3><div class="warn">Richiesta documentale riconosciuta, ma risposta catalogo non prodotta: verifica tecnica necessaria.</div></div>'}out.innerHTML='<div class="analysis-title">'+escapeHtml(x.recording_name||label||'Registrazione')+'</div><div class="section"><h3>Richiesta identificata</h3><div>'+escapeHtml(a.richiesta_cliente||'Non identificabile')+'</div></div><div class="section"><h3>Fatti espliciti</h3>'+list(a.fatti_espliciti)+'</div><div class="section"><h3>Ambiguità e lacune</h3>'+list(a.ambiguita_e_lacune)+'</div><div class="section"><h3>Domande da chiarire</h3>'+list(a.domande_da_chiarire)+'</div>'+catalog;document.getElementById('reviewPill').textContent=x.manual_review_required?'Revisione richiesta':'Analisi completata'}catch(e){out.innerHTML='<div class="empty error">'+escapeHtml(e.message)+'</div>';document.getElementById('reviewPill').textContent='Errore'}}
if(keyEl.value)checkStatus();
</script></body></html>"""
    return HTMLResponse(
        page.replace("__SAFE_TENANT__", json.dumps(tenant))
            .replace("__TENANT_UPPER__", html.escape(tenant.upper()))
            .replace("__TENANT__", html.escape(tenant))
    )
