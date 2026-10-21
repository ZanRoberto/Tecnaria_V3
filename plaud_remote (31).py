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
import re
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urljoin, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import Column, Integer, LargeBinary, String, Text, create_engine, select
from sqlalchemy.orm import Session, declarative_base


PLAUD_MCP_URL = os.getenv("PLAUD_MCP_URL", "https://mcp.plaud.ai/mcp").rstrip("/")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")


def public_base_url() -> str:
    """L'indirizzo pubblico del servizio. Se PUBLIC_BASE_URL non e' impostata si usa
    quello che Render da' da solo a ogni servizio web (RENDER_EXTERNAL_URL): cosi' un
    servizio nuovo o di prova si collega a PLAUD senza un'impostazione a mano in piu'."""
    for nome in ("PUBLIC_BASE_URL", "RENDER_EXTERNAL_URL"):
        valore = os.getenv(nome, "").strip().rstrip("/")
        if valore.startswith("https://"):
            return valore
    return ""
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


def _fernet_key() -> str:
    """La chiave che cifra l'accesso PLAUD salvato. Se PLAUD_TOKEN_ENCRYPTION_KEY non c'e',
    se ne ricava una stabile da un segreto che il servizio ha gia' (mai mostrata, mai
    salvata): resta uguale a ogni riavvio. Dove la chiave e' impostata (LIVE) vale quella."""
    esplicita = os.getenv("PLAUD_TOKEN_ENCRYPTION_KEY", "").strip()
    if esplicita:
        return esplicita
    for nome in ("WHATSAPP_APP_SECRET", "APPLAUDI_TENANT_KEYS_JSON"):
        segreto = os.getenv(nome, "").strip()
        if len(segreto) >= 16:
            digest = hashlib.sha256(("applaudi-plaud-token:" + segreto).encode()).digest()
            return base64.urlsafe_b64encode(digest).decode("ascii")
    return ""


def _cipher() -> Fernet:
    chiave = _fernet_key()
    if not chiave:
        raise HTTPException(503, "PLAUD_TOKEN_ENCRYPTION_KEY non configurata")
    try:
        return Fernet(chiave.encode("ascii"))
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


# Due PLAUD distinti, mai uno che va in due direzioni: il PLAUD dell'AZIENDA e (solo per il
# titolare, se il gestore lo abilita) il PLAUD PRIVATO. Ognuno ha il suo collegamento OAuth;
# le registrazioni vanno dove dice il PLAUD da cui arrivano, non una scelta caso per caso.
FONTI = ("azienda", "privato")
# Impostato da app.py: quali PLAUD ha il cliente (dalla modalita' decisa dal gestore).
fonti_ammesse: Callable[[str], Tuple[str, ...]] = lambda tenant: ("azienda",)


def slot(tenant: str, fonte: str = "azienda") -> str:
    """Il collegamento OAuth di quel PLAUD del cliente (tenant gia' verificato)."""
    if fonte not in FONTI:
        raise HTTPException(400, "PLAUD non valido")
    if fonte not in fonti_ammesse(tenant):
        raise HTTPException(403, f"Il PLAUD {fonte} non e' abilitato per questo cliente")
    return tenant if fonte == "azienda" else tenant + "#privato"


def _base(valore: str) -> str:
    return (valore or "").split("#", 1)[0]


# Clienti creati dalla console del gestore (anagrafica.py), impostati da app.py.
# Quelli di APPLAUDI_TENANT_KEYS_JSON vincono sempre.
chiave_extra: Optional[Callable[[str, Optional[str]], Optional[bool]]] = None
tenant_extra: Callable[[], List[str]] = lambda: []


def tutti_i_tenant() -> List[str]:
    return sorted(set(TENANT_KEYS) | set(tenant_extra()))


def verify_tenant_key(tenant: str, supplied: Optional[str]) -> str:
    tenant = _tenant(tenant)
    expected = str(TENANT_KEYS.get(tenant) or "")
    if not expected and chiave_extra is not None:
        esito = chiave_extra(tenant, supplied)
        if esito is True:
            return tenant
        if esito is False:
            raise HTTPException(401, "Chiave cliente non valida")
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
    base = public_base_url()
    if not base.startswith("https://"):
        raise HTTPException(503, "PUBLIC_BASE_URL HTTPS non configurata")
    return base + "/api/plaud/cloud/callback"


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
async def connect(tenant: str, fonte: str = Query("azienda"),
                  x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    tenant = slot(verify_tenant_key(tenant, x_applaudi_tenant_key), fonte)
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
    return RedirectResponse(public_base_url() + f"/plaud/{_base(tenant)}?connected=1", status_code=303)


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
    fonti: Dict[str, bool] = {}
    for fonte in fonti_ammesse(tenant):
        try:
            _tokens(slot(tenant, fonte))
            fonti[fonte] = True
        except HTTPException:
            fonti[fonte] = False
    return {"tenant": tenant, "connected": any(fonti.values()), "fonti": fonti,
            "provider": "PLAUD Remote MCP"}


@router.get("/recordings/{tenant}")
async def recordings(tenant: str, query: Optional[str] = None, date_from: Optional[str] = None,
                     date_to: Optional[str] = None, fonte: str = Query("azienda"),
                     x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    tenant = slot(verify_tenant_key(tenant, x_applaudi_tenant_key), fonte)
    arguments = {k: v for k, v in {"query": query, "date_from": date_from, "date_to": date_to}.items() if v}
    raw = await _mcp_call(tenant, "tools/call", {"name": "list_files", "arguments": arguments})
    found: Dict[str, Dict[str, Any]] = {}

    def decode_jsonish(text: str) -> Any:
        value = (text or "").strip()
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I | re.S).strip()
        candidates = [value]
        for left, right in (("{", "}"), ("[", "]")):
            start, end = value.find(left), value.rfind(right)
            if start >= 0 and end > start:
                candidates.append(value[start:end + 1])
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    def visit(value: Any) -> None:
        if isinstance(value, str):
            decoded = decode_jsonish(value)
            if decoded is not None and decoded != value:
                visit(decoded)
            return
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        recording_id = value.get("id") or value.get("file_id")
        if isinstance(recording_id, str) and recording_id.startswith("of_"):
            current = found.get(recording_id, {})
            title = value.get("name") or value.get("title") or current.get("name") or ""
            started = value.get("start_at") or value.get("created_at") or current.get("created_at") or ""
            duration = value.get("duration") if value.get("duration") is not None else current.get("duration")
            found[recording_id] = {
                "id": recording_id,
                "name": str(title).strip(),
                "created_at": started,
                "duration": duration,
            }
        for nested in value.values():
            visit(nested)

    visit(raw)
    records = list(found.values())
    records.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return {"recordings": records, "count": len(records)}


TRANSCRIPT_PAGE = 500          # massimo consentito da PLAUD per pagina (predefinito: 50)
TRANSCRIPT_MAX_PAGES = 200     # 100.000 frasi: oltre, si dichiara incompleta


def _decode_jsonish(text: Any) -> Any:
    value = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip(), flags=re.I | re.S).strip()
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _mcp_payloads(result: Any) -> List[Any]:
    """I dati veri di una risposta MCP: structuredContent e i testi JSON del content."""
    out: List[Any] = []
    if isinstance(result, dict):
        if result.get("structuredContent") is not None:
            out.append(result["structuredContent"])
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                decoded = _decode_jsonish(item.get("text"))
                if decoded is not None:
                    out.append(decoded)
    return out


def _find_key(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if value.get(key) not in (None, ""):
            return value[key]
        for nested in value.values():
            found = _find_key(nested, key)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_key(nested, key)
            if found not in (None, ""):
                return found
    return None


_UTTERANCE_TEXT = ("content", "text", "sentence", "transcript")
_UTTERANCE_SPEAKER = ("speaker", "speaker_name", "speakerName", "original_speaker")


def _utterances(value: Any) -> List[str]:
    """Le frasi della trascrizione come «Chi: testo». Si cerca la lista di oggetti con un
    testo; chi parla si riporta solo se PLAUD lo indica. Nessuna frase inventata."""
    if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
        righe = []
        for item in value:
            testo = next((item[k] for k in _UTTERANCE_TEXT if isinstance(item.get(k), str)), "")
            if not testo.strip():
                continue
            chi = next((item[k] for k in _UTTERANCE_SPEAKER if isinstance(item.get(k), str)
                        and item[k].strip()), "")
            righe.append((chi.strip() + ": " if chi else "") + testo.strip())
        if righe:
            return righe
    if isinstance(value, dict):
        for nested in value.values():
            righe = _utterances(nested)
            if righe:
                return righe
    elif isinstance(value, list):
        for nested in value:
            righe = _utterances(nested)
            if righe:
                return righe
    return []


async def get_transcript_complete(tenant: str, recording_id: str) -> Dict[str, Any]:
    """TUTTE le pagine della trascrizione. PLAUD ne da' 50 frasi per volta: prima si
    leggeva solo la prima pagina e una riunione lunga entrava tagliata senza avviso."""
    pagine: List[Any] = []
    cursor: Optional[str] = None
    visti = set()
    complete = False
    for _ in range(TRANSCRIPT_MAX_PAGES):
        arguments: Dict[str, Any] = {"file_id": recording_id, "limit": TRANSCRIPT_PAGE}
        if cursor:
            arguments["cursor"] = cursor
        result = await _mcp_call(tenant, "tools/call", {"name": "get_transcript", "arguments": arguments})
        pagine.append(result)
        prossimo, frasi = None, 0
        for payload in _mcp_payloads(result):
            frasi = frasi or len(_utterances(payload))
            for nome in ("next_cursor", "nextCursor"):
                prossimo = prossimo or _find_key(payload, nome)
        if not isinstance(prossimo, str) or not prossimo or prossimo in visti:
            # cursore ripetuto, o pagina piena senza cursore riconoscibile: si ferma e
            # lo dichiara, invece di far passare per intera una trascrizione tagliata
            complete = not prossimo and frasi < TRANSCRIPT_PAGE
            break
        visti.add(prossimo)
        cursor = prossimo
    righe: List[str] = []
    for result in pagine:
        for payload in _mcp_payloads(result):
            trovate = _utterances(payload)
            if trovate:
                righe.extend(trovate)
                break
    if righe:
        testo = "\n".join(righe)
    else:                                    # formato non riconosciuto: il testo com'e'
        testo = "\n".join(str(item.get("text") or "") for result in pagine if isinstance(result, dict)
                          for item in result.get("content") or [] if isinstance(item, dict))
    return {"text": testo.strip(), "pages": len(pagine), "complete": complete}


# ------------------------------------------------------------------ audio originale
# get_file di PLAUD restituisce un link temporaneo (24 ore) per scaricare l'audio. Si
# scarica una volta e si conserva con la sua impronta: da quel momento l'originale non
# dipende piu' dall'account PLAUD. Si cercano solo chiavi che nominano l'audio o un
# download: mai un link qualsiasi preso a caso dalla risposta.
audio_client_factory = httpx.AsyncClient
AUDIO_MAX_BYTES = 500 * 1024 * 1024
_AUDIO_KEYS = ("presigned_url", "audio_url", "audio_download_url", "download_url", "temp_url",
               "file_url")
_AUDIO_EXT = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
              "audio/aac": ".aac", "audio/ogg": ".ogg", "audio/opus": ".opus", "audio/wav": ".wav",
              "audio/x-wav": ".wav", "audio/webm": ".webm"}


def url_audio(detail: Any) -> Optional[str]:
    for payload in _mcp_payloads(detail) + [detail]:
        for key in _AUDIO_KEYS:
            value = _find_key(payload, key)
            if isinstance(value, str) and value.startswith("https://"):
                return value
    chiavi = sorted({k for p in _mcp_payloads(detail) if isinstance(p, dict) for k in p})
    print(f"[PLAUD] link audio non trovato; chiavi della risposta: {chiavi[:30]}")
    return None


async def scarica_audio(url: str, cartella: str) -> Dict[str, Any]:
    """Scarica l'audio in streaming nella cartella del documento. Restituisce percorso,
    impronta sha256, byte e tipo. Oltre AUDIO_MAX_BYTES si ferma e non lascia file a meta'."""
    if not url.startswith("https://"):
        raise ValueError("link audio non sicuro")
    os.makedirs(cartella, exist_ok=True)
    tmp = os.path.join(cartella, "audio.download")
    digest, size = hashlib.sha256(), 0
    try:
        async with audio_client_factory(timeout=180, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise RuntimeError(f"download audio rifiutato ({response.status_code})")
                mime = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
                with open(tmp, "wb") as stream:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        size += len(chunk)
                        if size > AUDIO_MAX_BYTES:
                            raise RuntimeError("audio oltre il limite")
                        digest.update(chunk)
                        stream.write(chunk)
        if not size:
            raise RuntimeError("audio vuoto")
        ext = _AUDIO_EXT.get(mime) or os.path.splitext(urlparse(url).path)[1].lower()
        if not re.fullmatch(r"\.[a-z0-9]{2,5}", ext or ""):
            ext = ".audio"
        finale = os.path.join(cartella, "audio_originale" + ext)
        os.replace(tmp, finale)
        return {"audio_path": finale, "audio_impronta": digest.hexdigest(), "audio_bytes": size,
                "audio_mime": mime or None, "audio_scaricato_il": int(time.time())}
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


async def get_recording_bundle(tenant: str, recording_id: str) -> Dict[str, Any]:
    # Il Remote MCP ufficiale di PLAUD espone l'identificativo come ``file_id``.
    # ``id`` viene rifiutato dallo schema MCP con errore -32602, anche quando
    # contiene un identificativo valido restituito da list_files.
    arguments = {"file_id": recording_id}
    transcript = await get_transcript_complete(tenant, recording_id)
    note = await _mcp_call(tenant, "tools/call", {"name": "get_note", "arguments": arguments})
    detail = await _mcp_call(tenant, "tools/call", {"name": "get_file", "arguments": arguments})
    return {"detail": detail, "transcript": transcript["text"], "note": note,
            "transcript_pages": transcript["pages"], "transcript_complete": transcript["complete"]}


@router.get("/recording/{tenant}/{recording_id}")
async def recording(tenant: str, recording_id: str, fonte: str = Query("azienda"),
                    x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    tenant = slot(verify_tenant_key(tenant, x_applaudi_tenant_key), fonte)
    return await get_recording_bundle(tenant, recording_id)


@router.delete("/disconnect/{tenant}")
async def disconnect(tenant: str, fonte: str = Query("azienda"),
                     x_applaudi_tenant_key: Optional[str] = Header(None, alias="X-Applaudi-Tenant-Key")):
    tenant = slot(verify_tenant_key(tenant, x_applaudi_tenant_key), fonte)
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
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:radial-gradient(circle at 82% 0,#102a37 0,transparent 28%),var(--bg);color:var(--text);font:15px Inter,Segoe UI,system-ui,sans-serif}
.app{min-height:100vh;display:grid;grid-template-columns:245px 1fr}.side{background:#0a0f17;border-right:1px solid var(--line);padding:27px 20px;position:sticky;top:0;height:100vh}
.logo{font-size:27px;font-weight:850;letter-spacing:1.5px;margin:0 10px 36px}.logo i{font-style:normal;color:var(--cyan)}.nav{display:grid;gap:7px}.nav button{padding:12px 14px;border-radius:10px;color:#aeb9ca;background:transparent;border:1px solid transparent;text-align:left;font-weight:600}.nav button:hover{background:#101a27;color:white}.nav .active{background:#122532;color:white;border-color:#1f4d5b}.nav b{display:inline-block;width:25px;color:var(--cyan)}
.identity{position:absolute;bottom:24px;left:25px;color:var(--muted);font-size:12px}.identity strong{display:block;color:white;font-size:14px;margin-bottom:4px}.main{padding:32px 38px 60px;max-width:1500px;width:100%}
.top{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:26px}.top h1{font-size:29px;margin:0 0 5px}.muted{color:var(--muted)}.status{padding:9px 13px;border-radius:22px;background:#0d2a22;border:1px solid #1f664e;color:var(--green);font-weight:700}
.access{background:#111722;border:1px solid var(--line);border-radius:15px;padding:17px 20px;margin-bottom:20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}.access input{flex:1;min-width:260px}
button,input,select{font:inherit;border-radius:9px;padding:11px 14px}select{background:#090e16;color:white;border:1px solid #3a4659;min-width:200px}input{background:#090e16;color:white;border:1px solid #3a4659}button{border:0;background:var(--cyan);color:#031014;font-weight:800;cursor:pointer}button.secondary{background:#202b3b;color:white;border:1px solid #36445a}button:disabled{opacity:.5;cursor:not-allowed}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:15px;margin-bottom:20px}.kpi,.card{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:16px}.kpi{padding:17px}.kpi small{display:block;color:var(--muted);margin-bottom:8px}.kpi strong{font-size:19px}.ok{color:var(--green)}.warn{color:#ffc96a}
.grid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(330px,.8fr);gap:18px}.card{padding:21px}.card h2{font-size:18px;margin:0}.head{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}.head button{padding:8px 11px;font-size:13px}
.record{display:grid;grid-template-columns:42px minmax(0,1fr) 125px 72px auto;align-items:center;gap:10px;padding:13px 4px;border-top:1px solid #263246}.record .index{width:30px;height:30px;border-radius:50%;display:grid;place-items:center;background:#102632;color:var(--cyan);font-weight:800}.record .name{font-weight:700}.record .meta{font-size:12px;color:var(--muted);margin-top:4px}.record button{font-size:12px;padding:8px}.record-head{display:grid;grid-template-columns:42px minmax(0,1fr) 125px 72px auto;gap:10px;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.7px;padding:0 4px 9px}.empty{padding:30px 8px;color:var(--muted);text-align:center}
.analysis-title{font-size:17px;font-weight:800;margin:4px 0 13px}.section{border-top:1px solid var(--line);padding-top:13px;margin-top:13px}.section h3{font-size:12px;letter-spacing:.9px;text-transform:uppercase;color:var(--cyan);margin:0 0 8px}.section ul{margin:0;padding-left:19px}.section li{margin:7px 0;line-height:1.4}.pill{display:inline-block;border:1px solid #365067;border-radius:20px;padding:5px 9px;color:#b8c6d9;font-size:11px}.loading{color:var(--cyan)}.error{color:var(--danger)}
.bottom{display:grid;grid-template-columns:repeat(3,1fr);gap:15px;margin-top:18px}.mini{min-height:125px}.mini p{color:var(--muted);line-height:1.45}.debug{white-space:pre-wrap;overflow-wrap:anywhere;font:12px ui-monospace;background:#080c12;padding:12px;border-radius:9px;max-height:260px;overflow:auto}
.upload-box{max-width:980px}.upload-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:18px 0}.upload-row input{flex:1;min-width:240px}.doc-row{display:grid;grid-template-columns:minmax(0,1fr) auto auto auto auto;gap:10px;align-items:center;padding:13px 0;border-top:1px solid var(--line)}.doc-row strong{display:block;overflow-wrap:anywhere}.doc-row small{color:var(--muted);display:block;margin-top:3px}.doc-row button{font-size:12px;padding:7px 10px}.doc-row.spento{opacity:.55}.badge{font-size:11px;font-weight:800;letter-spacing:.4px;padding:5px 9px;border-radius:20px;white-space:nowrap}.b-vigente{background:#0d2a22;color:var(--green);border:1px solid #1f664e}.b-da_confermare{background:#33280f;color:#ffc96a;border:1px solid #7a5b1c}.b-in_arrivo{background:#102632;color:var(--cyan);border:1px solid #1f4d5b}button.danger{background:#b4232f;color:#fff;border-color:#b4232f}.b-scaduto,.b-sostituito,.b-archiviato,.b-cestinato{background:#1a212d;color:#91a0b6;border:1px solid #36445a}.scheda{margin:14px 0;padding:18px;border:1px solid #7a5b1c;border-radius:14px;background:#141a24}.scheda h3{margin:0 0 4px}.scheda-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:12px}.scheda-grid label{display:flex;flex-direction:column;gap:6px;font-size:12px;color:var(--muted)}.scheda-grid .wide{grid-column:span 3}.scheda-grid input,.scheda-grid select{width:100%;box-sizing:border-box;color-scheme:dark}.check{display:flex!important;flex-direction:row!important;align-items:center;gap:8px;font-size:13px;color:var(--text)}.check input{width:auto}.scheda-actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}.proposta{outline:2px solid #7a5b1c}.doc-tools{margin-top:8px}.reg-row{border-top:1px solid var(--line);padding:12px 0}.reg-row small{color:var(--muted)}.reg-row .q{font-weight:700;margin:4px 0}.reg-row .f{font-size:12px;color:#b8c6d9}@media(max-width:760px){.scheda-grid{grid-template-columns:1fr}.scheda-grid .wide{grid-column:auto}.doc-row{grid-template-columns:1fr auto}}
.page{display:none}.page.active{display:block}.module-state{max-width:760px;padding:28px}.module-state h2{margin:0 0 10px}.module-state p{color:var(--muted);line-height:1.55}.module-state .pill{margin-top:8px}.action-link{color:var(--cyan);cursor:pointer;font-weight:700}.mini{cursor:pointer}.mini:hover{border-color:#3a536f}
.record.in-kb{border-left:3px solid var(--green);background:linear-gradient(90deg,#0d2a22 0,transparent 65%)}.kb-badge{display:inline-block;margin-left:8px;padding:2px 8px;border-radius:12px;background:#0d2a22;border:1px solid #1f664e;color:var(--green);font-size:11px;font-weight:800;letter-spacing:.4px;vertical-align:middle}.kb-badge.off{background:#1a2230;border-color:#36445a;color:var(--muted)}.rec-actions{display:flex;gap:6px;justify-content:flex-end;flex-wrap:wrap}.rec-actions .done{background:#0d2a22;color:var(--green);border:1px solid #1f664e}.space-switch{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 14px}.space-switch button{background:#202b3b;color:white;border:1px solid #36445a}.space-switch button.on{background:var(--cyan);color:#031014;border-color:var(--cyan)}.iv-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-top:10px}.iv-f{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--muted);min-width:0}.iv-f input,.iv-f select{width:100%;min-width:0}.section.intervento{border:1px solid #1f664e;border-radius:12px;padding:12px;background:#0b1a17}.top-right{display:flex;gap:14px;align-items:center;flex-wrap:wrap}.credit{display:flex;gap:10px;align-items:center;background:#0c1220;border:1px solid var(--line);border-radius:14px;padding:8px 14px}.credit .ring{--p:0;--c:var(--cyan);width:54px;height:54px;border-radius:50%;background:conic-gradient(var(--c) calc(var(--p)*1%),#243044 0);display:grid;place-items:center;flex:0 0 54px}.credit .ring span{width:42px;height:42px;border-radius:50%;background:#0c1220;display:grid;place-items:center;font-weight:800;font-size:13px}.credit .muted{font-size:12px}.answer-box{white-space:pre-wrap;line-height:1.55;background:#080c12;border:1px solid var(--line);border-radius:10px;padding:14px;margin-top:10px}.ask-priv{border:1px solid #1f664e;border-radius:12px;padding:4px 14px 12px;margin-bottom:16px;background:#0b1a17}textarea{font:inherit;width:100%;background:#090e16;color:white;border:1px solid #3a4659;border-radius:9px;padding:11px 14px;resize:vertical}.media-box{border-color:#365067;background:#0c1622}.media-box p{margin:6px 0 10px}.media-box label{display:flex;gap:8px;align-items:center;flex:1;min-width:0;flex-wrap:wrap}.media-box select{min-width:0;flex:1;max-width:100%}.thumbs{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px}.thumbs figure{margin:0;max-width:220px}.thumbs img,.thumbs video{max-width:220px;max-height:220px;border-radius:9px;border:1px solid var(--line);display:block}.thumbs figcaption{font-size:12px;color:var(--muted);margin-top:4px}.media-view{margin-top:12px}.media-view img,.media-view video{max-width:100%;max-height:70vh;border-radius:10px;border:1px solid var(--line)}.sec-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin-top:16px}.code-big{font:800 30px ui-monospace,monospace;letter-spacing:6px;color:var(--cyan);margin:6px 0}.error{color:var(--danger)}
@media(max-width:980px){.app{grid-template-columns:minmax(0,1fr)}.side{min-width:0;position:static;height:auto;padding:14px 16px;border-right:0;border-bottom:1px solid var(--line)}.side .logo{margin:0 0 10px;font-size:22px}.nav{display:flex;overflow-x:auto;gap:6px;padding-bottom:4px}.nav button{white-space:nowrap;padding:9px 12px}.identity{display:none}.main{padding:22px}.kpis{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}.bottom{grid-template-columns:1fr}.record{grid-template-columns:1fr}.record-head{display:none}.rec-actions{justify-content:flex-start}.record .hide-small{display:none}}
.cf-names{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:10px}.cf-names label{display:flex;flex-direction:column;gap:4px;color:var(--muted);font-size:13px;flex:1;min-width:200px}.cf-wrap{overflow-x:auto}.cf-table{width:100%;border-collapse:collapse;font-size:13px}.cf-table th,.cf-table td{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:top}.cf-table th{color:var(--muted);font-weight:700}.cf-table td div+div{margin-top:6px}.cf-g{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:800;white-space:nowrap}.cf-g.a{background:#0d2a22;color:var(--green)}.cf-g.b{background:#2a1215;color:var(--danger)}.cf-g.d{background:#2a2412;color:var(--gold)}.cf-g.u{background:#1a2230;color:var(--muted)}.cf-src{color:var(--muted);font-size:11px;margin-top:2px}.cf-tag{color:var(--gold);font-size:11px;font-weight:800}.cf-list li{margin:6px 0;line-height:1.5}#compare h3{margin:18px 0 6px}.cf-verdetto{background:#080c12;border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px}.cf-vtit{display:flex;gap:18px;flex-wrap:wrap;font-size:15px}.cf-vtit b{font-size:22px}.cf-vtit .a b{color:var(--green)}.cf-vtit .b b{color:var(--danger)}.cf-bar{display:flex;height:14px;border-radius:7px;overflow:hidden;margin-top:12px;background:#1a2230}.cf-bar span{display:block;height:100%}.cf-bar .a{background:var(--green)}.cf-bar .p{background:#3a4659}.cf-bar .b{background:var(--danger)}.cf-carte{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;margin-bottom:6px}.cf-carta{background:#080c12;border:1px solid var(--line);border-radius:12px;padding:12px 14px}.cf-ct{color:var(--muted);font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.5px}.cf-cv{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:8px 0 6px}.cf-lato{border:1px solid #2a3547;border-radius:9px;padding:7px 9px;font-size:12px;color:var(--muted)}.cf-lato b{display:block;font-size:17px;color:var(--text);margin-top:2px}.cf-lato.win.a{border-color:var(--green);background:#0d2a22}.cf-lato.win.b{border-color:var(--danger);background:#2a1215}.cf-cn{font-size:11px;color:var(--muted);line-height:1.4}</style></head><body><div class="app">
<aside class="side"><div class="logo">A'PLAUD'<i>I</i></div><div class="nav">
<button class="active" data-page="overview"><b>◈</b> Panoramica</button><button data-page="search"><b>⌕</b> Ricerca</button><button data-page="compare"><b>⚖</b> Confronta</button><button data-page="workspace"><b>◉</b> Registrazioni PLAUD</button><button data-page="workspace"><b>✦</b> Narratore</button><button data-page="documents"><b>▤</b> Documenti aziendali</button><button data-page="proposals"><b>◇</b> Proposte</button><button data-page="engines"><b>⚙</b> Motori</button><button data-page="supervision"><b>⌁</b> Supervisione</button><button data-page="people"><b>☺</b> Persone</button><button data-page="security"><b>⚿</b> Sicurezza</button><button data-page="settings"><b>·</b> Impostazioni</button></div>
<div class="identity"><strong>__TENANT_UPPER__</strong>Area riservata del cliente</div></aside>
<main class="main"><div class="top"><div><h1>Buongiorno</h1><div class="muted">Intelligenza aziendale certificata · controllo documentale e operativo</div></div><div class="top-right"><div id="creditRing" class="credit" hidden title="Credito residuo"><div class="ring" id="ringFill"><span id="ringPct">—</span></div><div><strong>Credito</strong><div class="muted" id="ringTxt"></div></div></div><div id="topStatus" class="status">● Verifica collegamento</div></div></div>
<section class="access"><label for="companySelect"><strong>Seleziona azienda</strong></label><select id="companySelect" aria-label="Seleziona azienda" onchange="changeCompany(this.value)"></select><span class="muted" id="companyHint">Ogni azienda richiede la propria chiave di accesso.</span></section>
<section class="access"><strong>Accesso protetto</strong><input id="key" type="password" placeholder="Chiave personale del cliente"><button onclick="saveKey()">Entra</button><button class="secondary" id="connectBtn" onclick="connectPlaud()">Collega PLAUD</button><span id="state" class="warn">Inserisci la chiave personale.</span></section>
<div id="overview" class="page active"><section class="kpis"><div class="kpi"><small>PLAUD</small><strong id="plaudKpi">Da collegare</strong></div><div class="kpi"><small>Registrazioni disponibili</small><strong id="recordingCount">—</strong></div><div class="kpi"><small>Documenti caricati</small><strong id="documentCount">—</strong></div><div class="kpi"><small>Motore risposte</small><strong class="ok">Narratore attivo</strong></div></section>
<section class="bottom"><div class="card mini" onclick="switchPage('workspace')"><h2>Registrazioni e Narratore</h2><p>Recupera una conversazione PLAUD, separa fatti e lacune e interroga il Super-Risponditore quando emerge una richiesta documentale.</p><span class="pill ok">Operativo</span></div><div class="card mini" onclick="switchPage('documents')"><h2>Documenti aziendali</h2><p>Carica cataloghi, listini, manuali, procedure e tabelle nell'archivio separato dell'azienda.</p><span class="pill ok">Operativo</span></div><div class="card mini" onclick="switchPage('proposals')"><h2>Proposte</h2><p>Le proposte nascono soltanto da prodotti e condizioni confermati.</p><span class="pill warn">Da attivare</span></div></section></div>
<div id="people" class="page"><section class="card upload-box"><div class="head"><div><h2>Persone dell’azienda</h2><span class="muted">Chi lavora con A’PLAUD’I: ruolo e numero WhatsApp. Un numero registrato qui riceve le risposte dell’azienda, mai lo spazio privato del titolare.</span></div></div><div id="peopleList"><div class="empty">Accedi per vedere le persone.</div></div><div id="inviaBox" class="card" hidden style="margin-top:12px"><h2 id="inviaTitle">Invia intervento</h2><p class="muted">Parte su WhatsApp con il link che apre il navigatore. Resta nel registro.</p><div class="upload-row"><input id="iCliente" maxlength="120" placeholder="Cliente (es. Studio Ferri Architetti)"><input id="iQuando" maxlength="60" placeholder="Quando (es. mercoledì 22/10 ore 16:00)"></div><div class="upload-row"><input id="iIndirizzo" maxlength="200" placeholder="Indirizzo completo, con il comune"></div><div class="upload-row"><input id="iNote" maxlength="500" placeholder="Note (es. puntualità tassativa, terzo piano senza ascensore)"></div><div class="rec-actions" style="justify-content:flex-start"><button onclick="sendInvia()">Invia su WhatsApp</button><button class="secondary" onclick="document.getElementById('inviaBox').hidden=true">Annulla</button></div><div id="inviaState" class="muted"></div></div><div class="upload-row"><input id="pNome" maxlength="80" placeholder="Nome e cognome"><select id="pRuolo" style="min-width:0"><option value="membro">Membro</option><option value="responsabile">Responsabile</option></select><input id="pTel" maxlength="20" placeholder="WhatsApp con prefisso, es. 393401234567"><button onclick="addPerson()">Aggiungi</button></div><div id="peopleState" class="muted"></div><p class="muted">Il titolare lo nomina il gestore della piattaforma. Sospendere non cancella: la persona smette solo di essere riconosciuta.</p></section></div>
<div id="search" class="page"><section class="card search-card"><div class="head"><div><h2>Ricerca</h2><span class="muted">Una domanda, una risposta: solo con le fonti dello spazio che scegli, sempre con la fonte accanto.</span></div></div><div class="space-switch"><button id="cPriv" onclick="setCerca('privato')">🔒 Spazio privato</button><button id="cAz" onclick="setCerca('azienda')">🏢 Azienda</button></div><div id="cercaHint" class="muted" style="margin-bottom:8px"></div><textarea id="cercaQ" rows="3" maxlength="3000" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();cerca('rapido')}"></textarea><div class="rec-actions" style="justify-content:flex-start;margin-top:10px"><button onclick="cerca('rapido')">Cerca</button><button class="secondary" id="cercaTutto" onclick="cerca('globale')">Confronta tutte le soluzioni</button></div><div id="cercaOut" style="margin-top:14px"></div></section></div>
<div id="compare" class="page"><section class="card"><div class="head"><div><h2>Confronta polizze</h2><span class="muted">Scegli i documenti della polizza A e della polizza B: condizioni, appendici, deroghe. Ogni dato del confronto è verificato sulla citazione del documento.</span></div></div><div class="space-switch"><button id="cfPriv" onclick="cfSetSpazio('privato')">🔒 Spazio privato</button><button id="cfAz" onclick="cfSetSpazio('azienda')">🏢 Azienda</button></div><div class="cf-names"><label>Nome della polizza A<input id="cfNomeA" maxlength="80" placeholder="es. la nostra"></label><label>Nome della polizza B<input id="cfNomeB" maxlength="80" placeholder="es. la concorrente"></label></div><div id="cfDocs"></div><div class="rec-actions" style="justify-content:flex-start;margin-top:10px"><button onclick="confrontaPolizze()">Confronta</button></div><div id="cfOut" style="margin-top:14px"></div></section></div>
<div id="workspace" class="page"><section class="grid"><div class="card"><div class="head"><div><h2>Registrazioni PLAUD</h2><span class="muted">Analizzale, oppure aggiungile alla conoscenza: entrano intere e diventano cercabili</span></div><div class="rec-actions"><button class="secondary" id="addAllBtn" onclick="addAllToKb()">Aggiungi tutte</button><button onclick="loadRecordings()">Aggiorna</button></div></div><div class="space-switch"><button id="fPriv" onclick="setFonte('privato')">🔒 PLAUD privato</button><button id="fAz" onclick="setFonte('azienda')">🏢 PLAUD aziendale</button></div><div id="fonteHint" class="muted" style="margin-bottom:8px"></div><div class="upload-row" style="margin:4px 0 10px"><input id="recSearch" placeholder="Cerca per titolo o data (es. riunione, 17/10)" oninput="filterRecordings()"><select id="recFilter" onchange="filterRecordings()" style="min-width:0"><option value="tutte">Tutte</option><option value="da">Da aggiungere</option><option value="kb">Nella conoscenza</option></select></div><div id="kbState" class="muted" style="margin-bottom:8px"></div><div id="recordings"><div class="empty">Premi Aggiorna per caricare le registrazioni.</div></div></div>
<div class="card"><div class="head"><div><h2>Narratore</h2><span class="muted">Analisi della conversazione</span></div><span id="reviewPill" class="pill">In attesa</span></div><div id="result"><div class="empty">Seleziona una registrazione. Qui appariranno fatti, lacune e prossime azioni.</div></div></div></section>
<section class="bottom"><div class="card mini" onclick="switchPage('documents')"><h2>Documenti aziendali</h2><p>Consulta e amplia il patrimonio documentale collegato a questa azienda.</p><span class="pill ok">Operativo</span></div><div class="card mini" onclick="switchPage('proposals')"><h2>Proposte</h2><p>Prepara una proposta soltanto dopo la scelta esplicita del prodotto.</p><span class="pill warn">Da attivare</span></div><div class="card mini" onclick="switchPage('supervision')"><h2>Supervisione</h2><p>Controllo centralizzato dei clienti e delle verifiche aperte.</p><span class="pill warn">Da attivare</span></div></section></div>
<div id="documents" class="page"><section class="card upload-box"><div class="head"><div><h2 id="docTitle">Documenti</h2><span class="muted" id="docSubtitle">PDF, Word, Excel, CSV, TXT, Markdown, JSON e mail (.eml)</span></div><span class="pill ok">Operativo</span></div><div class="space-switch"><button id="spPriv" onclick="setSpazio('privato')">🔒 Spazio privato</button><button id="spAz" onclick="setSpazio('azienda')">🏢 Azienda</button></div><div id="mediaBox" class="ask-priv media-box" hidden><strong>Foto, video o disegno con il suo trigger</strong><p class="muted">Il trigger dice cosa o chi è, e perché: è quello che si cerca, il file arriva allegato alla risposta. Senza trigger il file non entra. JPG o PNG, video MP4, disegni in PDF.</p><div class="upload-row"><input id="mediaFile" type="file" accept=".jpg,.jpeg,.png,.mp4,.3gp,.pdf"></div><textarea id="mediaTrigger" rows="2" maxlength="2000" placeholder="Es.: Antonio, quello con cui ho parlato il 15/10 della fornitura · Fiat targata JK345PT che sono andato a vedere"></textarea><div class="upload-row"><label>Collegata a <select id="mediaLink"><option value="">— nessun documento —</option></select></label><button id="mediaButton" onclick="uploadMedia()">Aggiungi con trigger</button></div><div id="mediaState" class="muted"></div><div id="mediaView" class="media-view"></div></div><p class="muted">Carichi il file, controlli la scheda proposta e confermi. Prima della conferma il documento non entra in nessuna risposta. Il catalogo non viene mai modificato: le risposte mostrano a parte che cosa aggiunge ogni documento, con la sua data.</p><div class="upload-row"><input id="documentFile" type="file" accept=".pdf,.docx,.xlsx,.csv,.txt,.md,.json,.eml"><button id="uploadButton" onclick="uploadDocument()">Carica e leggi</button></div><div id="documentState" class="muted">Seleziona un documento.</div><form id="schedaForm" class="scheda" hidden onsubmit="event.preventDefault();saveScheda()"><h3 id="schedaTitle">Scheda della fonte</h3><p class="muted" id="schedaHint"></p><div class="scheda-grid"><label>Tipo<select id="fTipo" onchange="schedaRules()"></select></label><label class="wide">Titolo<input id="fTitolo" maxlength="160" required></label><label>Data del documento<input id="fData" type="date"></label><label>Valido dal<input id="fDal" type="date"></label><label>Valido fino al<input id="fFino" type="date"></label><label class="wide">Sostituisce<select id="fSost"><option value="">— nessuno —</option></select></label><label class="check"><input id="fRis" type="checkbox"> Riservato: mai su WhatsApp</label><label class="wide">Note<input id="fNote" maxlength="1000"></label></div><div class="scheda-actions"><button type="submit" id="schedaSave">Conferma e inserisci</button><button type="button" class="secondary" id="schedaArchive" onclick="archiveScheda()" hidden>Archivia</button><button type="button" class="secondary" onclick="closeScheda()">Chiudi</button></div><div id="schedaState" class="muted"></div></form><div class="doc-tools"><label class="check"><input id="showStorico" type="checkbox" onchange="loadDocuments()"> Mostra storico (sostituiti e archiviati)</label> <label class="check"><input id="showCestino" type="checkbox" onchange="loadDocuments()"> Mostra cestino <span id="cestinoCount"></span></label></div><div id="togliForm" class="card" hidden style="margin-top:12px"><h2 id="togliTitle">Togli</h2><p class="muted">Esce subito dalle risposte e va nel cestino: si può ripristinare. Il motivo resta nel registro delle operazioni.</p><div class="upload-row"><select id="togliMotivo"></select><input id="togliNota" maxlength="500" placeholder="Nota (obbligatoria con «Altro»)"></div><div class="rec-actions" style="justify-content:flex-start"><button onclick="confirmTogli()">Togli</button><button class="secondary" onclick="closeTogli()">Annulla</button></div><div id="togliState" class="muted"></div></div><div id="distruggiBox" class="card" hidden style="margin-top:12px"><h2 id="distrTitle">Distruggi</h2><p class="muted">Distruzione definitiva: file e scheda spariscono e non si recuperano. Serve il PIN intero del titolare. Nello spazio aziendale si può solo dopo i giorni di cestino, salvo richiesta privacy.</p><div class="upload-row"><input id="distrPin" type="password" inputmode="numeric" maxlength="6" autocomplete="off" placeholder="PIN intero"></div><div class="rec-actions" style="justify-content:flex-start"><button class="danger" onclick="confirmDistruggi()">Distruggi definitivamente</button><button class="secondary" onclick="closeDistruggi()">Annulla</button></div><div id="distrState" class="muted"></div></div><div id="documentList" style="margin-top:10px"><div class="empty">Accedi per vedere i documenti.</div></div></section><section class="card upload-box" style="margin-top:18px"><div class="head"><div><h2>Registro delle risposte</h2><span class="muted">Ogni risposta con le fonti usate: pagine del catalogo e documenti con la loro versione</span></div></div><div class="upload-row"><input id="registroCerca" placeholder="Cerca nel registro (es. 211605, promo, nome cliente)"><button class="secondary" onclick="loadRegistro()">Cerca</button></div><div id="registroList"><div class="empty">Premi Cerca per vedere le ultime risposte.</div></div></section></div>
<div id="proposals" class="page"><section class="card module-state"><h2>Proposte</h2><p>Il modulo sarà abilitato dopo che il Super-Risponditore avrà restituito prodotti documentati e l'utente ne avrà selezionato esplicitamente uno. Nessuna proposta viene creata automaticamente.</p><span class="pill warn">Non operativo</span></section></div>
<div id="engines" class="page"><section class="card module-state"><h2>Motori</h2><p>La configurazione del motore resta gestita dal server. Qui verranno mostrati motore attivo, disponibilità e diagnostica senza esporre chiavi o segreti.</p><span class="pill warn">Non operativo</span></section></div>
<div id="supervision" class="page"><section class="card module-state"><h2>Supervisione</h2><p>Questa area richiederà autenticazione separata da supervisore e mostrerà clienti, stato dei collegamenti e verifiche aperte senza mescolare i relativi dati.</p><span class="pill warn">Non operativo</span></section></div>
<div id="security" class="page"><section class="card upload-box"><div class="head"><div><h2>Sicurezza dello spazio privato</h2><span class="muted">PIN per WhatsApp e collegamento del tuo telefono</span></div><span id="secPill" class="pill">—</span></div><div id="secState" class="muted">Accedi per vedere lo stato.</div><div class="sec-grid"><div class="card"><h2>PIN</h2><p class="muted">6 cifre. Su WhatsApp ti verranno chieste solo 3 cifre in posizioni a caso: nella chat non resta mai il PIN intero. Dopo 3 errori lo spazio si blocca e si sblocca solo da qui. Vale 30 minuti.</p><div class="upload-row"><input id="pin1" type="password" inputmode="numeric" maxlength="6" autocomplete="new-password" placeholder="Nuovo PIN"><input id="pin2" type="password" inputmode="numeric" maxlength="6" autocomplete="new-password" placeholder="Ripeti il PIN"></div><label class="check" style="display:block;margin:0 0 10px"><input type="checkbox" onchange="['pin1','pin2'].forEach(i=>document.getElementById(i).type=this.checked?'text':'password')"> Mostra le cifre mentre le scrivi</label><div class="rec-actions" style="justify-content:flex-start"><button onclick="savePin()">Salva PIN</button><button class="secondary" id="unlockBtn" onclick="unlockSpace()" hidden>Sblocca</button></div><div id="pinState" class="muted" style="margin-top:8px"></div></div><div class="card"><h2>Telefono</h2><p class="muted">Genera un codice e scrivilo da WhatsApp al numero di A&#39;PLAUD&#39;I: la parola collega seguita dal codice. Vale 10 minuti. Così il servizio sa che quel telefono è tuo.</p><div class="rec-actions" style="justify-content:flex-start"><button onclick="linkPhone()">Genera codice</button><button class="secondary" onclick="unlinkPhone()">Scollega i telefoni</button></div><div id="linkOut" style="margin-top:12px"></div></div><div class="card"><h2>Distruggi tutto</h2><p class="muted">Svuota all&#39;istante TUTTO lo spazio privato: documenti, foto, video, registrazioni e risposte salvate. Non si recupera e sul server non resta traccia. Restano solo PIN e telefoni collegati. Da WhatsApp: scrivi distruggi, poi il PIN intero.</p><div class="upload-row"><input id="killPin" type="password" inputmode="numeric" maxlength="6" autocomplete="off" placeholder="PIN intero"><input id="killWord" autocomplete="off" placeholder="Scrivi DISTRUGGI"></div><div class="rec-actions" style="justify-content:flex-start"><button class="danger" onclick="destroySpace()">Distruggi tutto</button></div><div id="killState" class="muted" style="margin-top:8px"></div></div></div><p class="muted" style="margin-top:16px">Su WhatsApp: scrivi <strong>privato</strong> per il tuo spazio personale, il nome dell&#39;azienda per l&#39;azienda, <strong>chiudi</strong> per richiudere lo spazio privato. Dopo 8 ore di silenzio si riparte dal privato.</p></section></div><div id="settings" class="page"><section class="card module-state"><h2>Impostazioni</h2><p>Le impostazioni per cliente saranno disponibili dopo l'introduzione dei profili persistenti e dei ruoli.</p><span class="pill warn">Non operativo</span></section></div>
</main></div><script>
const companyList=__COMPANY_LIST__;const tenant=__SAFE_TENANT__;const storageKey='applaudi-key-'+tenant;const keyEl=document.getElementById('key');keyEl.value=sessionStorage.getItem(storageKey)||'';
function changeCompany(next){if(!companyList.includes(next)||next===tenant)return;window.location.assign('/plaud/'+encodeURIComponent(next))}
const companySelect=document.getElementById('companySelect');
[...new Set([tenant,...companyList])].forEach(name=>{const option=document.createElement('option');option.value=name;option.textContent=name.toUpperCase();option.selected=name===tenant;companySelect.appendChild(option)});
if(companySelect.options.length===1){document.getElementById('companyHint').textContent='Solo questa azienda è configurata: gli altri clienti si creano dalla console del gestore.'}
function headers(){return {'X-Applaudi-Tenant-Key':keyEl.value}}
function escapeHtml(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function request(url,options={}){options.headers=Object.assign({},options.headers||{},headers());const r=await fetch(url,options);const data=await r.json().catch(()=>({detail:'Risposta non leggibile'}));if(!r.ok)throw new Error(data.detail||('Errore '+r.status));return data}
function saveKey(){sessionStorage.setItem(storageKey,keyEl.value);checkStatus()}
function switchPage(page){document.querySelectorAll('.page').forEach(x=>x.classList.toggle('active',x.id===page));document.querySelectorAll('.nav button').forEach(x=>x.classList.toggle('active',x.dataset.page===page));if(page==='workspace'&&keyEl.value)loadRecordings();if(page==='documents'&&keyEl.value)loadDocuments();if(page==='security'&&keyEl.value)loadSecurity();if(page==='people'&&keyEl.value)loadPeople();if(page==='compare'&&keyEl.value)loadConfronta();if(page==='search'){paintCerca();document.getElementById('cercaQ').focus()}}
document.querySelectorAll('.nav button').forEach(x=>x.addEventListener('click',()=>switchPage(x.dataset.page)))
async function loadCredit(){const box=document.getElementById('creditRing');try{const x=await request('/api/credito/'+tenant);const c=x.credito;if(!c){box.hidden=true;return}const ring=document.getElementById('ringFill');ring.style.setProperty('--p',c.percentuale);ring.style.setProperty('--c',c.esaurito?'var(--danger)':(c.percentuale<20?'#fbbf24':'var(--cyan)'));document.getElementById('ringPct').textContent=c.percentuale+'%';document.getElementById('ringTxt').textContent=c.esaurito?'Esaurito: chiedi la ricarica':(Number(c.residuo_eur).toLocaleString('it-IT',{style:'currency',currency:'EUR'})+' di '+Number(c.base_eur).toLocaleString('it-IT',{style:'currency',currency:'EUR'}));box.hidden=false}catch(e){box.hidden=true}}
async function checkStatus(){const s=document.getElementById('state');try{await loadSpazi();loadCredit();const x=await request('/api/plaud/cloud/status/'+tenant);plaudFonti=x.fonti||{};const nomi={azienda:'PLAUD aziendale',privato:'PLAUD privato'};s.textContent='Chiave valida · '+Object.keys(plaudFonti).map(f=>nomi[f]+(plaudFonti[f]?' collegato':' da collegare')).join(' · ');const ok=!!plaudFonti[recFonte];s.className=ok?'ok':'warn';document.getElementById('plaudKpi').textContent=ok?'Collegato':'Da collegare';document.getElementById('plaudKpi').className=ok?'ok':'warn';document.getElementById('topStatus').textContent=ok?'● Sistema operativo':'● Collegamento richiesto';paintFonte();if(ok)loadRecordings();loadDocuments()}catch(e){s.textContent=e.message;s.className='error';document.getElementById('topStatus').textContent='● Accesso richiesto'}}
async function connectPlaud(){try{const x=await request('/api/plaud/cloud/connect/'+tenant+'?fonte='+recFonte,{method:'POST'});location.href=x.authorization_url}catch(e){alert(e.message)}}
let docState={tipi:{},docs:[],editing:null};
const SIT={vigente:'VIGENTE',da_confermare:'DA CONFERMARE',in_arrivo:'IN ARRIVO',scaduto:'SCADUTO',sostituito:'SOSTITUITO',archiviato:'ARCHIVIATO',cestinato:'NEL CESTINO'};
function fileSize(n){n=Number(n)||0;if(n<1024)return n+' B';if(n<1048576)return (n/1024).toFixed(1)+' KB';return (n/1048576).toFixed(1)+' MB'}
function itDate(v){if(!v)return '';const p=String(v).slice(0,10).split('-');return p.length===3?p[2]+'/'+p[1]+'/'+p[0]:v}
async function loadDocuments(){const box=document.getElementById('documentList');const state=document.getElementById('documentState');const storico=document.getElementById('showStorico').checked;const cestino=document.getElementById('showCestino').checked;box.innerHTML='<div class="empty loading">Caricamento documenti…</div>';try{const x=await request('/api/documents/'+tenant+'?spazio='+docSpazio+(cestino?'&cestino=true':(storico?'&storico=true':'')));docState.tipi=x.tipi||{};docState.motivi=x.motivi||{};docState.docs=Array.isArray(x.documents)?x.documents:[];document.getElementById('cestinoCount').textContent=x.nel_cestino?'('+x.nel_cestino+')':'';document.getElementById('documentCount').textContent=docState.docs.filter(d=>d.situazione==='vigente').length;box.innerHTML=docState.docs.length?'':(cestino?'<div class="empty">Il cestino è vuoto.</div>':'<div class="empty">Nessun documento nell’archivio.</div>');docState.docs.slice().reverse().forEach(d=>{const row=document.createElement('div');const off=['scaduto','sostituito','archiviato','cestinato'].includes(d.situazione);row.className='doc-row'+(off?' spento':'');row.innerHTML='<div><strong>'+escapeHtml(d.titolo||d.name||'Documento')+'</strong><small>'+escapeHtml(d.etichetta||'')+'</small><small>'+escapeHtml(d.name||'')+' · '+escapeHtml(fileSize(d.size))+' · '+escapeHtml(String(d.sections||0))+' sezioni · versione '+escapeHtml(String(d.versione||1))+(d.significato?' · ricerca per significato attiva':(d.riservato?' · riservato: solo ricerca per parole':''))+'</small>'+(d.media?'<small>Trigger: '+escapeHtml(d.media.trigger||'')+(d.collegati&&d.collegati.length?' · collegata a '+d.collegati.length+' documento/i':'')+'</small>':'')+(d.audio?'<small class="ok">Audio originale conservato · impronta '+escapeHtml(String(d.audio.impronta||'').slice(0,12))+'… · '+escapeHtml(fileSize(d.audio.bytes))+'</small>':(d.audio_stato?'<small class="warn">'+escapeHtml(d.audio_stato)+'</small>':''))+(d.stato==='cestinato'?'<small class="warn">Nel cestino dal '+escapeHtml(new Date((d.cestinato_il||0)*1000).toLocaleDateString('it-IT'))+' · '+escapeHtml((docState.motivi||{})[d.motivo_rimozione]||d.motivo_rimozione||'')+(d.nota_rimozione?' · '+escapeHtml(d.nota_rimozione):'')+'</small>':'')+'</div><span class="badge b-'+escapeHtml(d.situazione)+'">'+escapeHtml(SIT[d.situazione]||d.situazione)+'</span>'+(d.media?'<button class="secondary" data-a="v">Vedi</button>':'<span></span>')+(d.stato==='cestinato'?'<button class="secondary" data-a="r">Ripristina</button><button class="secondary" data-a="x">Distruggi</button>':'<button class="secondary" data-a="s">'+(d.stato==='da_confermare'?'Conferma':'Scheda')+'</button><button class="secondary" data-a="e">Togli</button>');if(d.stato==='cestinato'){row.querySelector('[data-a=r]').onclick=()=>restoreDocument(d);row.querySelector('[data-a=x]').onclick=()=>openDistruggi(d)}else{row.querySelector('[data-a=s]').onclick=()=>openScheda(d);row.querySelector('[data-a=e]').onclick=()=>openTogli(d)}if(d.media)row.querySelector('[data-a=v]').onclick=()=>showMedia(d.id,d.titolo,d.media.mime,'mediaView');if(d.stato==='sostituito')row.querySelector('[data-a=s]').disabled=true;box.appendChild(row)});if(!x.persistent_root_configured){state.textContent='Attenzione: disco persistente non configurato (DOCUMENT_UPLOAD_ROOT). I documenti spariscono al prossimo riavvio.';state.className='warn'}else{state.textContent='Archivio persistente · '+(docSpazio==='privato'?'spazio privato di '+String(x.tenant||'').toUpperCase():'azienda: '+String(x.knowledge_client||'').toUpperCase())+' · oggi '+itDate(x.oggi);state.className='ok'}if(docSpazio==='azienda'&&!cestino){request('/api/documents/'+tenant+'/registro/operazioni?spazio=azienda').then(o=>{if(o.blocchi_24h){state.textContent='Attenzione: nelle ultime 24 ore le rimozioni sono state bloccate '+o.blocchi_24h+' volta/e per troppe rimozioni in un’ora.';state.className='error'}}).catch(()=>{})}}catch(e){document.getElementById('documentCount').textContent='—';box.innerHTML='<div class="empty error">'+escapeHtml(e.message)+'</div>'}}
async function uploadDocument(){const input=document.getElementById('documentFile');const state=document.getElementById('documentState');const button=document.getElementById('uploadButton');if(!input.files.length){state.textContent='Seleziona prima un documento.';state.className='warn';return}const form=new FormData();form.append('file',input.files[0]);state.textContent='Caricamento e lettura in corso…';state.className='loading';button.disabled=true;try{const x=await request('/api/documents/'+tenant+'/upload?spazio='+docSpazio,{method:'POST',body:form});input.value='';await loadDocuments();openScheda(x.document,true);state.textContent='Letto ('+x.document.sections+' sezioni). Controlla la scheda e conferma: fino ad allora il documento non viene usato.';state.className='warn'}catch(e){state.textContent=e.message;state.className='error'}finally{button.disabled=false}}
function openScheda(d,nuovo){docState.editing=d;const p=d.proposte||{};const conferma=d.stato==='da_confermare';const tipo=document.getElementById('fTipo');tipo.innerHTML='';Object.entries(docState.tipi).forEach(([k,v])=>{const o=document.createElement('option');o.value=k;o.textContent=v;tipo.appendChild(o)});const val=(k)=>conferma?(d[k]||p[k]||''):(d[k]||'');tipo.value=val('tipo')||'altro';document.getElementById('fTitolo').value=val('titolo');document.getElementById('fData').value=val('data_documento');document.getElementById('fDal').value=val('valido_dal');document.getElementById('fFino').value=val('valido_fino');document.getElementById('fRis').checked=!!d.riservato;document.getElementById('fNote').value=d.note||'';const sost=document.getElementById('fSost');sost.innerHTML='<option value="">— nessuno —</option>';docState.docs.filter(o=>o.id!==d.id&&o.stato==='attivo').forEach(o=>{const opt=document.createElement('option');opt.value=o.id;opt.textContent=(o.titolo||o.name)+' · '+(docState.tipi[o.tipo]||o.tipo);sost.appendChild(opt)});sost.value=d.sostituisce||'';sost.disabled=!conferma&&!!d.sostituisce;['fTipo','fTitolo','fData','fDal','fFino'].forEach(id=>document.getElementById(id).classList.remove('proposta'));if(conferma){[['tipo','fTipo'],['titolo','fTitolo'],['data_documento','fData'],['valido_dal','fDal'],['valido_fino','fFino']].forEach(([k,id])=>{if(p[k])document.getElementById(id).classList.add('proposta')})}document.getElementById('schedaTitle').textContent=(conferma?'Conferma la scheda: ':'Scheda: ')+(d.titolo||d.name);document.getElementById('schedaHint').textContent=conferma?'I campi bordati di giallo sono proposte lette dal documento: controllale. Per una promozione la data di fine validità è obbligatoria.':'Ogni modifica resta nella storia del documento, con data e autore.';document.getElementById('schedaSave').textContent=conferma?'Conferma e inserisci':'Salva modifiche';document.getElementById('schedaArchive').hidden=conferma||d.stato!=='attivo';document.getElementById('schedaState').textContent='';schedaRules();const f=document.getElementById('schedaForm');f.hidden=false;f.scrollIntoView({behavior:'smooth',block:'center'})}
function schedaRules(){const promo=document.getElementById('fTipo').value==='promozione';document.getElementById('fFino').required=promo}
function closeScheda(){document.getElementById('schedaForm').hidden=true;docState.editing=null}
async function saveScheda(extra){const d=docState.editing;if(!d)return;const st=document.getElementById('schedaState');const body=Object.assign({tipo:document.getElementById('fTipo').value,titolo:document.getElementById('fTitolo').value,data_documento:document.getElementById('fData').value||null,valido_dal:document.getElementById('fDal').value||null,valido_fino:document.getElementById('fFino').value||null,riservato:document.getElementById('fRis').checked,note:document.getElementById('fNote').value||null},extra||{});const s=document.getElementById('fSost').value;if(s&&!d.sostituisce)body.sostituisce=s;st.textContent='Salvataggio…';st.className='loading';try{const x=await request('/api/documents/'+tenant+'/'+d.id+'/scheda?spazio='+docSpazio,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});closeScheda();const state=document.getElementById('documentState');state.textContent='«'+(x.document.titolo||'')+'» · '+(SIT[x.document.situazione]||x.document.situazione);state.className='ok';await loadDocuments()}catch(e){st.textContent=e.message;st.className='error'}}
function archiveScheda(){if(confirm('Archiviare il documento? Non entrerà più nelle risposte ma resterà nello storico.'))saveScheda({stato:'archiviato'})}
function openTogli(d){docState.togli=d;const m=document.getElementById('togliMotivo');m.innerHTML='<option value="">— scegli il motivo —</option>';Object.entries(docState.motivi||{}).forEach(([k,v])=>{const o=document.createElement('option');o.value=k;o.textContent=v;m.appendChild(o)});document.getElementById('togliNota').value='';document.getElementById('togliTitle').textContent='Togli «'+(d.titolo||d.name||'documento')+'»';document.getElementById('togliState').textContent='';document.getElementById('distruggiBox').hidden=true;const f=document.getElementById('togliForm');f.hidden=false;f.scrollIntoView({behavior:'smooth',block:'center'})}
function closeTogli(){document.getElementById('togliForm').hidden=true;docState.togli=null}
async function confirmTogli(){const d=docState.togli;if(!d)return;const st=document.getElementById('togliState');st.textContent='…';st.className='loading';try{await request('/api/documents/'+tenant+'/'+encodeURIComponent(d.id)+'/togli?spazio='+docSpazio,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({motivo:document.getElementById('togliMotivo').value,nota:document.getElementById('togliNota').value})});closeTogli();closeScheda();const state=document.getElementById('documentState');state.textContent='«'+(d.titolo||d.name||'Documento')+'» tolto dalle risposte e messo nel cestino.';state.className='ok';await loadDocuments()}catch(e){st.textContent=e.message;st.className='error'}}
async function restoreDocument(d){const state=document.getElementById('documentState');try{await request('/api/documents/'+tenant+'/'+encodeURIComponent(d.id)+'/ripristina?spazio='+docSpazio,{method:'POST'});state.textContent='«'+(d.titolo||d.name||'Documento')+'» ripristinato.';state.className='ok';await loadDocuments()}catch(e){state.textContent=e.message;state.className='error'}}
function openDistruggi(d){docState.distr=d;document.getElementById('togliForm').hidden=true;document.getElementById('distrTitle').textContent='Distruggi «'+(d.titolo||d.name||'documento')+'»';document.getElementById('distrPin').value='';document.getElementById('distrState').textContent='';const b=document.getElementById('distruggiBox');b.hidden=false;b.scrollIntoView({behavior:'smooth',block:'center'})}
function closeDistruggi(){document.getElementById('distruggiBox').hidden=true;docState.distr=null}
async function confirmDistruggi(){const d=docState.distr;if(!d)return;const st=document.getElementById('distrState');st.textContent='…';st.className='loading';try{await request('/api/documents/'+tenant+'/'+encodeURIComponent(d.id)+'/distruggi?spazio='+docSpazio,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pin:document.getElementById('distrPin').value})});document.getElementById('distrPin').value='';closeDistruggi();const state=document.getElementById('documentState');state.textContent='Distrutto definitivamente.';state.className='ok';await loadDocuments()}catch(e){st.textContent=e.message;st.className='error'}}
async function destroySpace(){const st=document.getElementById('killState');st.textContent='Distruzione in corso…';st.className='loading';try{const x=await request('/api/spazio/'+tenant+'/distruggi',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pin:document.getElementById('killPin').value,conferma:document.getElementById('killWord').value.trim()})});document.getElementById('killPin').value='';document.getElementById('killWord').value='';st.textContent='Spazio privato distrutto: '+x.documenti_distrutti+' contenuti eliminati, nessuna traccia sul server. Restano la chat WhatsApp sul telefono e gli originali nell’app PLAUD.';st.className='ok'}catch(e){st.textContent=e.message;st.className='error'}}
async function loadRegistro(){const box=document.getElementById('registroList');const q=document.getElementById('registroCerca').value||'';box.innerHTML='<div class="empty loading">Ricerca…</div>';try{const x=await request('/api/documents/'+tenant+'/registro/risposte?spazio='+docSpazio+'&testo='+encodeURIComponent(q));const r=Array.isArray(x.righe)?x.righe:[];box.innerHTML=r.length?'':'<div class="empty">Nessuna risposta registrata.</div>';r.forEach(g=>{const row=document.createElement('div');row.className='reg-row';const fonti=[];if((g.pagine_catalogo||[]).length)fonti.push('catalogo pag. '+g.pagine_catalogo.join(', '));(g.documenti||[]).forEach(d=>fonti.push('«'+(d.titolo||'')+'» v'+(d.versione||1)));row.innerHTML='<small>'+escapeHtml(formatDate(g.quando))+' · '+escapeHtml(String(g.canale||'').toUpperCase())+(g.da_memoria?' · da memoria':'')+'</small><div class="q">'+escapeHtml(g.domanda||'')+'</div><div class="f">Fonti: '+escapeHtml(fonti.join(' · ')||'—')+'</div>';box.appendChild(row)})}catch(e){box.innerHTML='<div class="empty error">'+escapeHtml(e.message)+'</div>'}}
function toDate(x){if(x===null||x===undefined||x==='')return null;if(typeof x==='number')return new Date(x<1e12?x*1000:x);let s=String(x).trim();const tz=s.endsWith('Z')||s.slice(-6,-5)==='+'||(s.length>19&&s.slice(-6,-5)==='-');if(s.length>=16&&s[4]==='-'&&s[7]==='-'&&!tz)s=s.replace(' ','T')+'Z';const d=new Date(s);return isNaN(d)?null:d}
function formatDate(x){if(!x)return 'Data non disponibile';const d=toDate(x);return d?d.toLocaleString('it-IT',{dateStyle:'short',timeStyle:'short'}):String(x)}
function formatDuration(ms){if(ms===null||ms===undefined)return '—';let sec=Number(ms)>10000?Math.round(Number(ms)/1000):Math.round(Number(ms));if(!isFinite(sec))return '—';return Math.floor(sec/60)+'m '+String(sec%60).padStart(2,'0')+'s'}
async function loadRecordings(){const box=document.getElementById('recordings');box.innerHTML='<div class="empty loading">Caricamento registrazioni…</div>';try{const both=await Promise.all([request('/api/plaud/cloud/recordings/'+tenant+'?fonte='+recFonte),loadKb()]);const x=both[0];const records=Array.isArray(x.recordings)?x.recordings:[];document.getElementById('recordingCount').textContent=records.length;box.innerHTML='';recordState=[];if(!records.length){box.innerHTML='<div class="empty error">PLAUD non ha restituito titoli e metadati leggibili. Nessun codice tecnico viene mostrato: aggiorna l’elenco o verifica il collegamento.</div>';return}box.innerHTML='<div class="record-head"><span>N.</span><span>Registrazione</span><span class="hide-small">Data e ora</span><span class="hide-small">Durata</span><span>Azioni</span></div>';records.forEach((r,i)=>{const shownDate=formatDate(r.created_at);const title=(r.name||'').trim()||('Registrazione del '+shownDate);const row=document.createElement('div');row.className='record';row.title='Identificativo tecnico disponibile solo al sistema';row.innerHTML='<div class="index">'+(i+1)+'</div><div><div class="name">'+escapeHtml(title)+'</div><div class="meta">Registrazione PLAUD<span class="kb-slot"></span></div></div><div class="hide-small">'+escapeHtml(shownDate)+'</div><div class="hide-small">'+escapeHtml(formatDuration(r.duration))+'</div><div class="rec-actions"><button data-a="an">Analizza</button><button data-a="kb" class="secondary">Aggiungi</button></div>';const item={r:r,title:title,row:row};row.querySelector('[data-a=an]').onclick=()=>analyze(r.id,title,r.created_at);row.querySelector('[data-a=kb]').onclick=()=>addToKb(item);recordState.push(item);markRow(item);box.appendChild(row)});const n=recordState.filter(x=>kbMap['plaud:'+x.r.id]&&kbMap['plaud:'+x.r.id].stato==='attivo').length;const st=document.getElementById('kbState');st.textContent=n+' di '+records.length+(kbSpazio()==='privato'?' registrazioni sono nel tuo spazio privato (in verde).':' registrazioni sono nella conoscenza dell’azienda (in verde).');st.className='muted';filterRecordings()}catch(e){box.innerHTML='<div class="empty error">'+escapeHtml(e.message)+'</div>'}}
function list(items){const values=Array.isArray(items)?items:[];return '<ul>'+(values.map(x=>'<li>'+escapeHtml(x)+'</li>').join('')||'<li>Nessun elemento.</li>')+'</ul>'}
function kpiList(items){if(!Array.isArray(items)||!items.length)return '<div class="muted">Nessun KPI esplicito rilevato.</div>';return '<ul>'+items.map(x=>'<li><strong>'+escapeHtml(x.voce||'Valore')+': '+escapeHtml(x.valore||'')+(x.unita?' '+escapeHtml(x.unita):'')+'</strong><br><span class="muted">'+escapeHtml(x.evidenza||'')+' · '+escapeHtml(x.stato||'')+'</span></li>').join('')+'</ul>'}
function actionList(items){if(!Array.isArray(items)||!items.length)return '<div class="muted">Nessuna azione esplicita.</div>';return '<ul>'+items.map(x=>'<li><strong>'+escapeHtml(x.azione||'')+'</strong><br><span class="muted">Responsabile: '+escapeHtml(x.responsabile||'da definire')+' · Scadenza: '+escapeHtml(x.scadenza||'da definire')+' · '+escapeHtml(x.stato||'')+'</span></li>').join('')+'</ul>'}
async function analyze(id,label,quando){switchPage('workspace');const out=document.getElementById('result');out.innerHTML='<div class="empty loading">Il Narratore sta analizzando la registrazione…</div>';document.getElementById('reviewPill').textContent='Analisi in corso';try{const x=await request('/api/plaud/cloud/analyze/'+tenant+'/'+id+'?fonte='+recFonte+(quando?'&data='+encodeURIComponent(String(quando)):''),{method:'POST'});const a=x.analysis||{};let catalog='';if(x.catalog_answer&&x.catalog_answer.answer){catalog='<div class="section"><h3>Risposta del Super-Risponditore</h3><div style="white-space:pre-wrap;line-height:1.55">'+escapeHtml(x.catalog_answer.answer)+'</div></div>'}else if(a.rilevanza_catalogo===true){catalog='<div class="section"><h3>Risposta del Super-Risponditore</h3><div class="warn">Richiesta documentale riconosciuta, ma risposta documentale non prodotta: verifica tecnica necessaria.</div></div>'}out.innerHTML='<div class="analysis-title">'+escapeHtml(x.recording_name||label||'Registrazione')+'</div><div class="pill">'+escapeHtml(a.tipo_comunicazione||'Comunicazione')+' · '+escapeHtml(a.instradamento||'CHIARIMENTO_NECESSARIO')+'</div><div class="section"><h3>Richiesta identificata</h3><div>'+escapeHtml(a.richiesta_cliente||'Non identificabile')+'</div><div class="muted" style="margin-top:7px">'+escapeHtml(a.sintesi_operativa||'')+'</div></div><div class="section"><h3>KPI e valori espliciti</h3>'+kpiList(a.kpi_e_valori)+'</div><div class="section"><h3>Fatti espliciti</h3>'+list(a.fatti_espliciti)+'</div><div class="section"><h3>Vincoli</h3>'+list(a.vincoli)+'</div><div class="section"><h3>Decisioni</h3>'+list(a.decisioni)+'</div><div class="section"><h3>Azioni</h3>'+actionList(a.azioni)+'</div><div class="section"><h3>Rischi e opportunità</h3>'+list(a.rischi_e_opportunita)+'</div><div class="section"><h3>Ambiguità e lacune</h3>'+list(a.ambiguita_e_lacune)+'</div><div class="section"><h3>Domande da chiarire</h3>'+list(a.domande_da_chiarire)+'</div>'+catalog+(x.intervento?'<div class="section intervento" id="ivBox"></div>':'');if(x.intervento)drawIntervento(x.intervento);document.getElementById('reviewPill').textContent=x.manual_review_required?'Revisione richiesta':'Analisi completata'}catch(e){out.innerHTML='<div class="empty error">'+escapeHtml(e.message)+'</div>';document.getElementById('reviewPill').textContent='Errore'}}
let docSpazio='privato';try{docSpazio=sessionStorage.getItem('applaudi-spazio-'+tenant)==='azienda'?'azienda':'privato'}catch(e){}
let spaziAmmessi=['privato','azienda'];let recFonte='privato';let plaudFonti={};try{recFonte=sessionStorage.getItem('applaudi-fonte-'+tenant)||'privato'}catch(e){}
function kbSpazio(){return recFonte}
function paintFonte(){const bp=document.getElementById('fPriv'),ba=document.getElementById('fAz');if(!bp)return;bp.hidden=!spaziAmmessi.includes('privato');ba.hidden=!spaziAmmessi.includes('azienda');bp.classList.toggle('on',recFonte==='privato');ba.classList.toggle('on',recFonte==='azienda');document.getElementById('fonteHint').textContent=recFonte==='privato'?'Il tuo PLAUD privato: le registrazioni aggiunte vanno nel tuo spazio privato.':'Il PLAUD dell’azienda: le registrazioni proposte vanno nell’archivio aziendale, da confermare.';document.getElementById('connectBtn').textContent=spaziAmmessi.length>1?(recFonte==='privato'?'Collega PLAUD privato':'Collega PLAUD aziendale'):'Collega PLAUD'}
function setFonte(f){if(!spaziAmmessi.includes(f))return;recFonte=f;try{sessionStorage.setItem('applaudi-fonte-'+tenant,f)}catch(e){}paintFonte();const ok=!!plaudFonti[recFonte];document.getElementById('plaudKpi').textContent=ok?'Collegato':'Da collegare';if(ok)loadRecordings();else{document.getElementById('recordings').innerHTML='<div class="empty">Questo PLAUD non è ancora collegato: premi «'+document.getElementById('connectBtn').textContent+'» in alto.</div>';document.getElementById('kbState').textContent='';recordState=[]}}
async function loadSpazi(){try{const x=await request('/api/documents/'+tenant+'/spazi');spaziAmmessi=Array.isArray(x.spazi)&&x.spazi.length?x.spazi:['azienda'];spaziCatalogo=x.catalogo!==false}catch(e){spaziAmmessi=['azienda']}if(!spaziAmmessi.includes(docSpazio))docSpazio=spaziAmmessi[0];if(!spaziAmmessi.includes(recFonte))recFonte=spaziAmmessi[0];const navSec=document.querySelector('.nav button[data-page=security]');if(navSec)navSec.hidden=!spaziAmmessi.includes('privato');paintSpazio();paintCerca()}
function paintSpazio(){const priv=docSpazio==='privato';document.getElementById('spPriv').hidden=!spaziAmmessi.includes('privato');document.getElementById('spAz').hidden=!spaziAmmessi.includes('azienda');document.getElementById('spPriv').classList.toggle('on',priv);document.getElementById('spAz').classList.toggle('on',!priv);document.getElementById('mediaBox').hidden=!priv;document.getElementById('docTitle').textContent=priv?'Il tuo spazio privato':'Documenti aziendali';document.getElementById('docSubtitle').textContent=(priv?'Solo tuo: registrazioni PLAUD e documenti personali, protetti dal PIN su WhatsApp':'Archivio dell’azienda, accanto al catalogo')+' · PDF, Word, Excel, CSV, TXT, Markdown, JSON e mail (.eml)'}
function setSpazio(s){docSpazio=s==='azienda'?'azienda':'privato';if(!spaziAmmessi.includes(docSpazio))return;try{sessionStorage.setItem('applaudi-spazio-'+tenant,docSpazio)}catch(e){}paintSpazio();closeScheda();document.getElementById('registroList').innerHTML='<div class="empty">Premi Cerca per vedere le ultime risposte.</div>';if(keyEl.value)loadDocuments()}
paintSpazio();
const peopleState={invia:null};const RUOLI_IT={titolare:'Titolare',responsabile:'Responsabile',membro:'Membro'};
async function loadPeople(){const box=document.getElementById('peopleList');box.innerHTML='<div class="empty loading">Caricamento…</div>';try{const x=await request('/api/persone/'+tenant);const pp=x.persone||[];box.innerHTML=pp.length?'':'<div class="empty">Nessuna persona registrata.</div>';pp.forEach(p=>{const row=document.createElement('div');row.className='doc-row'+(p.sospeso?' spento':'');row.innerHTML='<div><strong>'+escapeHtml(p.nome)+'</strong><small>'+escapeHtml(RUOLI_IT[p.ruolo]||p.ruolo)+' · '+escapeHtml(p.telefono||'nessun numero')+(p.sospeso?' · SOSPESA':'')+'</small></div><span></span>'+(p.telefono&&!p.sospeso?'<button data-a="i">Invia</button>':'<span></span>')+(p.ruolo==='titolare'?'<span class="muted">gestore</span>':'<button class="secondary" data-a="s">'+(p.sospeso?'Riattiva':'Sospendi')+'</button>');const bi=row.querySelector('[data-a=i]');if(bi)bi.onclick=()=>openInvia(p);const b=row.querySelector('[data-a=s]');if(b)b.onclick=async()=>{try{await request('/api/persone/'+tenant+'/'+p.id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sospeso:!p.sospeso})});await loadPeople()}catch(e){document.getElementById('peopleState').textContent=e.message;document.getElementById('peopleState').className='error'}};box.appendChild(row)})}catch(e){box.innerHTML='<div class="empty error">'+escapeHtml(e.message)+'</div>'}}
function openInvia(p){peopleState.invia=p;document.getElementById('inviaTitle').textContent='Invia intervento a '+p.nome;document.getElementById('inviaState').textContent='';const b=document.getElementById('inviaBox');b.hidden=false;b.scrollIntoView({behavior:'smooth',block:'center'})}
async function sendInvia(){const p=peopleState.invia;if(!p)return;const st=document.getElementById('inviaState');st.textContent='Invio…';st.className='loading';try{const x=await request('/api/persone/'+tenant+'/'+p.id+'/invia',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cliente:document.getElementById('iCliente').value,quando:document.getElementById('iQuando').value,indirizzo:document.getElementById('iIndirizzo').value,note:document.getElementById('iNote').value})});st.innerHTML='Inviato a '+escapeHtml(p.nome)+(x.modo==='modello'?' con il modello approvato (a pagamento Meta).':'.')+'<div class="answer-box">'+escapeHtml(x.testo)+'</div>';st.className='ok'}catch(e){st.textContent=e.message;st.className='error'}}
async function addPerson(){const st=document.getElementById('peopleState');try{await request('/api/persone/'+tenant,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({nome:document.getElementById('pNome').value,ruolo:document.getElementById('pRuolo').value,telefono:document.getElementById('pTel').value})});document.getElementById('pNome').value='';document.getElementById('pTel').value='';st.textContent='Persona aggiunta.';st.className='ok';await loadPeople()}catch(e){st.textContent=e.message;st.className='error'}}
let cercaSpazio=docSpazio;let aziendaCliente='';let spaziCatalogo=true;
function paintCerca(){if(!spaziAmmessi.includes(cercaSpazio))cercaSpazio=spaziAmmessi[0];const priv=cercaSpazio==='privato';const bp=document.getElementById('cPriv'),ba=document.getElementById('cAz');bp.hidden=!spaziAmmessi.includes('privato');ba.hidden=!spaziAmmessi.includes('azienda');bp.classList.toggle('on',priv);ba.classList.toggle('on',!priv);document.getElementById('cercaTutto').hidden=priv||!spaziCatalogo;document.getElementById('cercaHint').textContent=priv?'Cerca nelle tue registrazioni PLAUD, nei tuoi documenti e nei trigger di foto e video: i file arrivano allegati.':(spaziCatalogo?'Cerca nel catalogo e nei documenti confermati dell’azienda: le stesse risposte del sito e di WhatsApp.':'Cerca nei documenti confermati dell’azienda: le stesse risposte di WhatsApp.');document.getElementById('cercaQ').placeholder=priv?'Es.: cosa mi ha detto il cliente della caldaia? · mostrami il video del sopralluogo':'Es.: quanto costa il 211605 in vetro opaco? · che promozioni ci sono?'}
function setCerca(s){if(!spaziAmmessi.includes(s))return;cercaSpazio=s;paintCerca();document.getElementById('cercaOut').innerHTML='';document.getElementById('cercaQ').focus()}
function linkify(t){let out='';let i=0;while(true){const a=t.indexOf('[',i);if(a<0)break;const b=t.indexOf('](',a);const c=b<0?-1:t.indexOf(')',b);if(b<0||c<0)break;const label=t.slice(a+1,b);const url=t.slice(b+2,c);if(label.length>40||label.indexOf('[')>=0||url.indexOf('http')!==0||url.indexOf(' ')>=0){out+=escapeHtml(t.slice(i,a+1));i=a+1;continue}out+=escapeHtml(t.slice(i,a))+'<a class="action-link" href="'+escapeHtml(url)+'" target="_blank" rel="noopener">'+escapeHtml(label)+'</a>';i=c+1}return out+escapeHtml(t.slice(i))}
async function cerca(modo){const q=document.getElementById('cercaQ').value.trim();const out=document.getElementById('cercaOut');if(!q)return;if(!keyEl.value){out.innerHTML='<div class="empty error">Prima inserisci la chiave e premi Entra.</div>';return}out.innerHTML='<div class="empty loading">'+(cercaSpazio==='privato'?'Cerco nel tuo spazio privato…':'Cerco nel catalogo e nei documenti dell’azienda…')+'</div>';try{if(cercaSpazio==='privato'){const x=await request('/api/spazio/'+tenant+'/chiedi',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({domanda:q})});const f=(x.fonti||[]).map(d=>'«'+(d.titolo||'')+'»'+(d.data?' '+itDate(d.data):'')).join(' · ');out.innerHTML='<div class="answer-box">'+escapeHtml(x.risposta)+'</div>'+(f?'<div class="muted" style="margin-top:6px">Fonti: '+escapeHtml(f)+'</div>':'')+'<div class="thumbs" id="cercaThumbs"></div>';(x.allegati||[]).forEach(a=>addThumb(a,'cercaThumbs'))}else if(!spaziCatalogo){const x=await request('/api/spazio/'+tenant+'/chiedi_azienda',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({domanda:q})});const f=(x.fonti||[]).map(d=>'«'+(d.titolo||'')+'»'+(d.data?' '+itDate(d.data):'')).join(' · ');out.innerHTML='<div class="answer-box">'+escapeHtml(x.risposta)+'</div>'+(f?'<div class="muted" style="margin-top:6px">Fonti: '+escapeHtml(f)+'</div>':'')}else{if(!aziendaCliente){const y=await request('/api/documents/'+tenant+'?spazio=azienda');aziendaCliente=y.knowledge_client||''}const x=await request('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:(modo==='globale'?'/globale ':'/catalogo ')+q,cliente:aziendaCliente})});out.innerHTML='<div class="answer-box">'+linkify(String(x.answer||''))+'</div>'}}catch(e){out.innerHTML='<div class="empty error">'+escapeHtml(e.message)+'</div>'}}
async function mediaUrl(id){const r=await fetch('/api/documents/'+tenant+'/'+encodeURIComponent(id)+'/media?spazio=privato',{headers:headers()});if(!r.ok)throw new Error('File non disponibile ('+r.status+')');return URL.createObjectURL(await r.blob())}
function mediaTag(url,mime,title){if(String(mime).indexOf('image/')===0)return '<img src="'+url+'" alt="'+escapeHtml(title||'')+'">';if(String(mime).indexOf('video/')===0)return '<video src="'+url+'" controls></video>';return '<a class="action-link" href="'+url+'" target="_blank" rel="noopener">📎 Apri il PDF</a>'}
async function addThumb(a,boxId){const box=document.getElementById(boxId);if(!box)return;const fig=document.createElement('figure');fig.innerHTML='<div class="muted">Carico…</div>';box.appendChild(fig);try{const url=await mediaUrl(a.id);fig.innerHTML=mediaTag(url,a.mime,a.titolo)+'<figcaption>'+escapeHtml(a.titolo||'')+'</figcaption>'}catch(e){fig.innerHTML='<figcaption class="error">'+escapeHtml(e.message)+'</figcaption>'}}
async function showMedia(id,title,mime,boxId){const box=document.getElementById(boxId);box.innerHTML='<div class="muted">Carico…</div>';box.scrollIntoView({behavior:'smooth',block:'nearest'});try{const url=await mediaUrl(id);box.innerHTML='<div class="muted" style="margin-bottom:6px">'+escapeHtml(title||'')+'</div>'+mediaTag(url,mime,title)}catch(e){box.innerHTML='<div class="error">'+escapeHtml(e.message)+'</div>'}}
function fillLinks(){const sel=document.getElementById('mediaLink');if(!sel)return;const keep=sel.value;sel.innerHTML='<option value="">— nessun documento —</option>';docState.docs.filter(d=>d.stato==='attivo').slice().reverse().forEach(d=>{const o=document.createElement('option');o.value=d.id;o.textContent=(d.titolo||d.name||'Documento').slice(0,70);sel.appendChild(o)});sel.value=keep}
const _loadDocumentsBase=loadDocuments;loadDocuments=async function(){await _loadDocumentsBase();if(docSpazio==='privato')fillLinks()};
async function uploadMedia(){const input=document.getElementById('mediaFile');const st=document.getElementById('mediaState');const btn=document.getElementById('mediaButton');const trig=document.getElementById('mediaTrigger').value.trim();if(!input.files.length){st.textContent='Scegli prima la foto, il video o il disegno.';st.className='warn';return}if(trig.length<8){st.textContent='Scrivi il trigger: cosa o chi è, e perché. Senza trigger il file non entra.';st.className='warn';return}const form=new FormData();form.append('file',input.files[0]);form.append('trigger',trig);form.append('collegati',document.getElementById('mediaLink').value||'');st.textContent='Caricamento…';st.className='loading';btn.disabled=true;try{const x=await request('/api/documents/'+tenant+'/media?spazio=privato',{method:'POST',body:form});input.value='';document.getElementById('mediaTrigger').value='';st.textContent='«'+(x.document.titolo||'')+'» è nel tuo spazio privato: si trova con il suo trigger.';st.className='ok';await loadDocuments()}catch(e){st.textContent=e.message;st.className='error'}finally{btn.disabled=false}}
let kbMap={};let recordState=[];
function ivField(id,label,val,warn){return '<label class="iv-f">'+label+'<input id="'+id+'" value="'+escapeHtml(val||'')+'"'+(warn?' class="proposta"':'')+'></label>'}
function drawIntervento(iv){const box=document.getElementById('ivBox');if(!box)return;const certo=iv.esito_cliente==='certo';let h='<h3>Intervento da inviare</h3>';if(!iv.anagrafica_trovata){h+='<div class="warn">Nessuna anagrafica clienti tra i documenti: caricala in Documenti (Excel o CSV con cliente e indirizzo) e l’indirizzo arriva da solo.</div>'}else if(certo){h+='<div class="ok">Cliente trovato nell’anagrafica ('+(iv.motivo==='telefono'?'per telefono':'per nome')+'): l’indirizzo è quello registrato, non quello sentito nella telefonata.</div>'}else if(iv.esito_cliente==='da_scegliere'){h+='<div class="warn">Più clienti possibili: scegli quello giusto.</div><select id="ivScelta" style="width:100%;margin:8px 0"><option value="">— scegli il cliente —</option>'+iv.candidati.map((c,i)=>'<option value="'+i+'">'+escapeHtml(c.nome+' · '+c.indirizzo_completo)+'</option>').join('')+'</select>'}else{h+='<div class="warn">Cliente non trovato nell’anagrafica: scrivi tu l’indirizzo.</div>'}h+='<div class="iv-grid">'+ivField('ivQuando','Quando'+(iv.quando_da_confermare?' (data calcolata: conferma)':''),iv.quando+(iv.attivita?' — '+iv.attivita:''),iv.quando_da_confermare)+ivField('ivCliente','Cliente',iv.cliente)+ivField('ivIndirizzo','Indirizzo',iv.indirizzo)+ivField('ivNote','Note',iv.note)+'<label class="iv-f">Tecnico<select id="ivTecnico"><option value="">— scegli —</option>'+(iv.persone||[]).map(p=>'<option value="'+escapeHtml(p.id)+'"'+(iv.tecnico&&iv.tecnico.id===p.id?' selected':'')+'>'+escapeHtml(p.nome)+'</option>').join('')+'</select></label></div>'+(iv.tecnico_zona&&!iv.tecnico?'<div class="warn">Il tecnico di zona è '+escapeHtml(iv.tecnico_zona)+', ma non è tra le Persone con WhatsApp: aggiungilo nella pagina Persone.</div>':'')+'<div class="rec-actions" style="justify-content:flex-start;margin-top:10px"><button onclick="sendIntervento()">Invia al tecnico</button></div><div id="ivState" class="muted"></div>';box.innerHTML=h;ivState.dati=iv;const sc=document.getElementById('ivScelta');if(sc)sc.onchange=()=>{const c=iv.candidati[Number(sc.value)];if(!c)return;document.getElementById('ivCliente').value=c.nome||'';document.getElementById('ivIndirizzo').value=c.indirizzo_completo||'';if(c.note){const n=document.getElementById('ivNote');n.value=[n.value,c.note].filter(Boolean).join(' — ')}const t=(iv.persone||[]).find(p=>(p.nome||'').toLowerCase()===String(c.tecnico||'').toLowerCase());if(t)document.getElementById('ivTecnico').value=t.id}}
const ivState={dati:null};
async function sendIntervento(){const st=document.getElementById('ivState');const pid=document.getElementById('ivTecnico').value;if(!pid){st.textContent='Scegli il tecnico.';st.className='error';return}st.textContent='Invio…';st.className='loading';try{const x=await request('/api/persone/'+tenant+'/'+pid+'/invia',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({quando:document.getElementById('ivQuando').value,cliente:document.getElementById('ivCliente').value,indirizzo:document.getElementById('ivIndirizzo').value,note:document.getElementById('ivNote').value})});st.innerHTML='Inviato'+(x.modo==='modello'?' con il modello approvato (a pagamento Meta).':'.')+'<div class="answer-box">'+escapeHtml(x.testo)+'</div>';st.className='ok'}catch(e){st.textContent=e.message;st.className='error'}}
async function loadKb(){try{const x=await request('/api/documents/'+tenant+'?spazio='+kbSpazio()+'&storico=true');kbMap={};(Array.isArray(x.documents)?x.documents:[]).forEach(d=>{if(d.esterno)kbMap[d.esterno]=d})}catch(e){kbMap={}}}
function isoDay(v){const d=toDate(v);if(!d)return null;return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')}
function markRow(item){const d=kbMap['plaud:'+item.r.id];const btn=item.row.querySelector('[data-a=kb]');const badge=item.row.querySelector('.kb-slot');if(!d){item.row.classList.remove('in-kb');badge.innerHTML='';btn.disabled=false;btn.className='secondary';btn.textContent=kbSpazio()==='privato'?'Aggiungi':'Proponi';return}const attiva=d.stato==='attivo';const attesa=d.stato==='da_confermare';item.row.classList.toggle('in-kb',attiva);badge.innerHTML='<span class="kb-badge'+(attiva?'':' off')+'">'+(attiva?'NELLA CONOSCENZA':(attesa?'DA CONFERMARE':'ARCHIVIATA'))+'</span>';btn.disabled=true;btn.className='done';btn.textContent=attiva?'✓ Aggiunta':(attesa?'Proposta':'Archiviata')}
async function addToKb(item){const btn=item.row.querySelector('[data-a=kb]');btn.disabled=true;btn.textContent='Aggiungo…';try{const x=await request('/api/plaud/cloud/archivia/'+tenant+'/'+encodeURIComponent(item.r.id),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({nome:item.title,data:isoDay(item.r.created_at),fonte:recFonte})});kbMap['plaud:'+item.r.id]=x.document;markRow(item);filterRecordings();return true}catch(e){btn.disabled=false;btn.textContent='Riprova';btn.title=e.message;document.getElementById('kbState').textContent='«'+item.title+'»: '+e.message;document.getElementById('kbState').className='error';return false}}
function filterRecordings(){const q=(document.getElementById('recSearch').value||'').trim().toLowerCase();const f=document.getElementById('recFilter').value;let n=0;recordState.forEach(x=>{const testo=(x.title+' '+formatDate(x.r.created_at)+' '+(isoDay(x.r.created_at)||'')).toLowerCase();const dentro=!!kbMap['plaud:'+x.r.id];const ok=(!q||q.split(' ').filter(Boolean).every(p=>testo.indexOf(p)>=0))&&(f==='tutte'||(f==='kb'?dentro:!dentro));x.row.hidden=!ok;if(ok)n++});const st=document.getElementById('kbState');if(q||f!=='tutte'){st.textContent=n+' di '+recordState.length+' registrazioni mostrate. «Aggiungi tutte» aggiunge solo quelle mostrate.';st.className='muted'}else{const k=recordState.filter(x=>kbMap['plaud:'+x.r.id]&&kbMap['plaud:'+x.r.id].stato==='attivo').length;st.textContent=k+' di '+recordState.length+(kbSpazio()==='privato'?' registrazioni sono nel tuo spazio privato (in verde).':' registrazioni sono nella conoscenza dell’azienda (in verde).');st.className='muted'}}
async function addAllToKb(){const todo=recordState.filter(x=>!x.row.hidden&&!kbMap['plaud:'+x.r.id]);const st=document.getElementById('kbState');const all=document.getElementById('addAllBtn');if(!todo.length){st.textContent='Tutte le registrazioni sono già nella conoscenza.';st.className='ok';return}all.disabled=true;let ok=0;for(let i=0;i<todo.length;i++){st.textContent='Aggiungo '+(i+1)+' di '+todo.length+'…';st.className='loading';if(await addToKb(todo[i]))ok++}all.disabled=false;st.textContent=ok+' di '+todo.length+(kbSpazio()==='privato'?' aggiunte al tuo spazio privato.':' proposte all’azienda: confermale in Documenti aziendali.');st.className=ok===todo.length?'ok':'warn'}
async function loadSecurity(){const st=document.getElementById('secState');const pill=document.getElementById('secPill');try{const x=await request('/api/spazio/'+tenant+'/sicurezza');const parti=[x.pin_impostato?'PIN impostato':'PIN non impostato',x.telefoni_collegati===1?'1 telefono collegato':x.telefoni_collegati+' telefoni collegati'];if(x.sessioni_aperte)parti.push('spazio aperto su WhatsApp');if(x.bloccato)parti.push('BLOCCATO dopo 3 errori');if(!x.persistente)parti.push('attenzione: senza disco persistente PIN e collegamento si perdono al riavvio');st.textContent=parti.join(' · ');st.className=x.bloccato?'error':(x.pin_impostato&&x.telefoni_collegati?'ok':'warn');pill.textContent=x.bloccato?'Bloccato':(x.pin_impostato&&x.telefoni_collegati?'Protetto':'Da configurare');pill.className='pill '+(x.bloccato||!(x.pin_impostato&&x.telefoni_collegati)?'warn':'ok');document.getElementById('unlockBtn').hidden=!x.bloccato}catch(e){st.textContent=e.message;st.className='error'}}
async function savePin(){const a=document.getElementById('pin1').value,b=document.getElementById('pin2').value;const s=document.getElementById('pinState');s.textContent='Salvataggio…';s.className='loading';try{await request('/api/spazio/'+tenant+'/pin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pin:a,conferma:b})});document.getElementById('pin1').value='';document.getElementById('pin2').value='';s.textContent='PIN salvato. Le sessioni aperte su WhatsApp sono state chiuse.';s.className='ok';loadSecurity()}catch(e){s.textContent=e.message;s.className='error'}}
async function unlockSpace(){try{await request('/api/spazio/'+tenant+'/sblocca',{method:'POST'});document.getElementById('pinState').textContent='Spazio sbloccato.';document.getElementById('pinState').className='ok';loadSecurity()}catch(e){alert(e.message)}}
async function linkPhone(){const out=document.getElementById('linkOut');try{const x=await request('/api/spazio/'+tenant+'/collega',{method:'POST'});out.innerHTML='<div class="code-big">'+escapeHtml(x.codice)+'</div><div class="muted">Scrivi su WhatsApp: <strong>collega '+escapeHtml(x.codice)+'</strong> · vale '+escapeHtml(String(x.minuti))+' minuti</div>';loadSecurity()}catch(e){out.innerHTML='<div class="error">'+escapeHtml(e.message)+'</div>'}}
async function unlinkPhone(){if(!confirm('Scollegare tutti i telefoni dallo spazio privato?'))return;try{await request('/api/spazio/'+tenant+'/scollega',{method:'POST'});document.getElementById('linkOut').innerHTML='';loadSecurity()}catch(e){alert(e.message)}}
if(keyEl.value)checkStatus();
let cfSpazio='azienda';let cfUltimo=null;
function cfPaint(){if(!spaziAmmessi.includes(cfSpazio))cfSpazio=spaziAmmessi[0];const bp=document.getElementById('cfPriv'),ba=document.getElementById('cfAz');bp.hidden=!spaziAmmessi.includes('privato');ba.hidden=!spaziAmmessi.includes('azienda');bp.classList.toggle('on',cfSpazio==='privato');ba.classList.toggle('on',cfSpazio==='azienda')}
function cfSetSpazio(s){if(!spaziAmmessi.includes(s))return;cfSpazio=s;document.getElementById('cfOut').innerHTML='';loadConfronta()}
async function loadConfronta(){cfPaint();const box=document.getElementById('cfDocs');box.innerHTML='<div class="empty loading">Carico i documenti…</div>';try{const x=await request('/api/documents/'+tenant+'?spazio='+cfSpazio);const d=(x.documents||[]).filter(i=>!i.media);if(!d.length){box.innerHTML='<div class="empty">Nessun documento in questo spazio: carica le polizze in Documenti.</div>';return}box.innerHTML='<div class="cf-wrap"><table class="cf-table"><thead><tr><th>A</th><th>B</th><th>Documento</th><th>Tipo</th><th>Stato</th></tr></thead><tbody>'+d.map(i=>'<tr><td><input type="checkbox" class="cfA" value="'+escapeHtml(i.id)+'"></td><td><input type="checkbox" class="cfB" value="'+escapeHtml(i.id)+'"></td><td>'+escapeHtml(i.titolo||i.name||'')+'</td><td>'+escapeHtml((x.tipi||{})[i.tipo]||i.tipo||'')+'</td><td>'+escapeHtml(i.stato==='da_confermare'?'da confermare':(i.situazione||''))+'</td></tr>').join('')+'</tbody></table></div>'}catch(e){box.innerHTML='<div class="empty error">'+escapeHtml(e.message)+'</div>'}}
function cfScelti(c){return Array.from(document.querySelectorAll('.'+c+':checked')).map(x=>x.value)}
async function confrontaPolizze(){const out=document.getElementById('cfOut');if(!keyEl.value){out.innerHTML='<div class="empty error">Prima inserisci la chiave e premi Entra.</div>';return}const a=cfScelti('cfA'),b=cfScelti('cfB');if(!a.length||!b.length){out.innerHTML='<div class="empty error">Scegli almeno un documento per la polizza A e uno per la polizza B.</div>';return}if(a.some(i=>b.includes(i))){out.innerHTML='<div class="empty error">Lo stesso documento non può stare in tutte e due le polizze.</div>';return}out.innerHTML='<div class="empty loading">Leggo le due polizze pagina per pagina e verifico ogni dato: può servire un minuto.</div>';try{const x=await request('/api/confronta/'+tenant,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({spazio:cfSpazio,a:a,b:b,nome_a:document.getElementById('cfNomeA').value,nome_b:document.getElementById('cfNomeB').value})});cfUltimo=x;cfRender(x)}catch(e){out.innerHTML='<div class="empty error">'+escapeHtml(e.message)+'</div>'}}
const CF_G={meglio_a:['Meglio A','a'],meglio_b:['Meglio B','b'],solo_a:['Solo A','a'],solo_b:['Solo B','b'],uguale:['Uguale','u'],diverso:['Diverso','d'],non_confrontata:['Non confrontata','u']};
function cfNum(v){return Number(v).toLocaleString('it-IT',{minimumFractionDigits:2,maximumFractionDigits:2})}
function cfVoci(ids,voci){return ids.map(i=>{const v=voci[i];if(!v)return '';let t='';if(v.deroga)t=v.condizionata?' <span class="cf-tag">DEROGA, a condizione</span>':' <span class="cf-tag">DEROGA</span>';else if(v.superata)t=' <span class="cf-tag">superato dalla deroga</span>';else if((v.modificata_da||[]).length)t=' <span class="cf-tag">modificato dalla deroga</span>';return '<div><b>'+escapeHtml(v.nome)+'</b>: '+escapeHtml(v.valore)+t+'<div class="cf-src">'+escapeHtml(v.documento+' · '+v.label)+' · «'+escapeHtml(v.citazione)+'»</div></div>'}).join('')}
function cfBase(n,b){return b.calcolabile?escapeHtml(n)+': <b>'+cfNum(b.per_mille)+' €</b> ogni 1.000 € di capitale medio ('+escapeHtml(b.come||'')+')':escapeHtml(n)+': non calcolabile, '+escapeHtml(b.motivo||'')}
function cfQuadro(x){const q=x.quadro;if(!q)return '';const tot=Math.max(1,q.vince_a+q.vince_b+q.pari);const pc=n=>(100*n/tot).toFixed(1)+'%';const carte=q.carte.map(c=>{const wa=c.meglio==='a'?' win a':'',wb=c.meglio==='b'?' win b':'';return '<div class="cf-carta"><div class="cf-ct">'+escapeHtml(c.voce)+'</div><div class="cf-cv"><div class="cf-lato'+wa+'">A · '+escapeHtml(x.a.nome)+'<b>'+escapeHtml(c.a)+'</b></div><div class="cf-lato'+wb+'">B · '+escapeHtml(x.b.nome)+'<b>'+escapeHtml(c.b)+'</b></div></div><div class="cf-cn">'+escapeHtml(c.meglio==='pari'?'Pari. ':c.meglio==='nd'?'Non confrontabile. ':'')+escapeHtml(c.nota||'')+'</div></div>'}).join('');return '<div class="cf-verdetto"><div class="cf-vtit"><span class="a">A · '+escapeHtml(x.a.nome)+' <b>'+q.vince_a+'</b> voci a favore</span><span class="b">B · '+escapeHtml(x.b.nome)+' <b>'+q.vince_b+'</b> voci a favore</span><span class="muted">pari o diverse <b>'+q.pari+'</b></span></div><div class="cf-bar"><span class="a" style="width:'+pc(q.vince_a)+'"></span><span class="p" style="width:'+pc(q.pari)+'"></span><span class="b" style="width:'+pc(q.vince_b)+'"></span></div></div><div class="cf-carte">'+carte+'</div>'}
function cfRender(x){const voci={};x.a.voci.concat(x.b.voci).forEach(v=>{voci[v.id]=v});const rows=x.righe.map(r=>{const g0=CF_G[r.giudizio]||[r.giudizio,'u'];const g=[g0[0],r.vantaggio==='a'?'a':r.vantaggio==='b'?'b':(g0[1]==='a'||g0[1]==='b'?'u':g0[1])];return '<tr><td><b>'+escapeHtml(r.voce)+'</b></td><td>'+cfVoci(r.a,voci)+'</td><td>'+cfVoci(r.b,voci)+'</td><td><span class="cf-g '+g[1]+'">'+g[0]+'</span><div class="cf-src">'+escapeHtml(r.perche||'')+'</div></td></tr>'}).join('');const nt=x.a.non_trovate.map(n=>'<li>A · '+escapeHtml(n.voce)+': '+escapeHtml(n.testo)+'</li>').concat(x.b.non_trovate.map(n=>'<li>B · '+escapeHtml(n.voce)+': '+escapeHtml(n.testo)+'</li>')).join('');const li=l=>l.map(t=>'<li>'+escapeHtml(t.testo)+'</li>').join('');const scart=(x.controlli.scartate||[]).length;const st=(x.a.senza_testo||[]).concat(x.b.senza_testo||[]);document.getElementById('cfOut').innerHTML=cfQuadro(x)+(x.sintesi?'<div class="answer-box">'+escapeHtml(x.sintesi)+'</div>':'')+'<h3>Premio sulla stessa base</h3><div class="muted" style="line-height:1.7">'+cfBase('A · '+x.a.nome,x.a.base_premio)+'<br>'+cfBase('B · '+x.b.nome,x.b.base_premio)+'</div>'+(x.trappole.length?'<h3>Trappole per l’assicurato</h3><ul class="cf-list">'+li(x.trappole)+'</ul>':'')+(x.azioni.length?'<h3>Azioni da fare</h3><ul class="cf-list">'+li(x.azioni)+'</ul>':'')+'<h3>Voce per voce</h3><div class="cf-wrap"><table class="cf-table"><thead><tr><th>Voce</th><th>A · '+escapeHtml(x.a.nome)+'</th><th>B · '+escapeHtml(x.b.nome)+'</th><th>Giudizio</th></tr></thead><tbody>'+rows+'</tbody></table></div>'+(nt?'<h3>Non trovato</h3><ul class="cf-list">'+nt+'</ul>':'')+(st.length?'<div class="empty error" style="margin-top:10px">Pagine senza testo leggibile (forse scansioni): '+escapeHtml(st.join(', '))+'</div>':'')+'<div class="muted" style="margin-top:10px">Dati verificati: '+x.controlli.verificate+'. Tolti perché non tornavano con il testo: '+scart+' (elenco nel foglio Controlli dell’Excel).</div><div class="rec-actions" style="justify-content:flex-start;margin-top:10px"><button onclick="cfExcel()">Scarica Excel</button></div><div id="cfExcelMsg" class="muted"></div>'}
async function cfExcel(){if(!cfUltimo)return;const msg=document.getElementById('cfExcelMsg');try{const r=await fetch('/api/confronta/'+tenant+'/'+encodeURIComponent(cfUltimo.id)+'/excel?spazio='+cfSpazio,{headers:headers()});if(!r.ok)throw new Error('Excel non disponibile ('+r.status+')');const url=URL.createObjectURL(await r.blob());const l=document.createElement('a');l.href=url;l.download='Confronto '+cfUltimo.a.nome+' - '+cfUltimo.b.nome+'.xlsx';document.body.appendChild(l);l.click();l.remove();setTimeout(()=>URL.revokeObjectURL(url),5000)}catch(e){msg.textContent=e.message}}
</script></body></html>"""
    return HTMLResponse(
        page.replace("__COMPANY_LIST__", json.dumps(tutti_i_tenant()))
            .replace("__SAFE_TENANT__", json.dumps(tenant))
            .replace("__TENANT_UPPER__", html.escape(tenant.upper()))
            .replace("__TENANT__", html.escape(tenant))
    )
