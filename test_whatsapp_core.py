"""Collaudo dell'integrazione WhatsApp sul CORE VERO (app.py + indice LAGO reale).

Simulati, e SOLO questi: il modello linguistico (DeepSeek/OpenAI) e l'API Meta in
uscita. Tutto il resto - rotte, attiva_cliente, indice, Narratore/Super-Risponditore,
firma, idempotenza - e' il codice che va in produzione.
Questi test NON sono una prova reale WhatsApp: quella si fa col numero test Meta.

Esecuzione:  python -m pytest -q test_whatsapp_core.py
"""
import asyncio
import dis
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
import types

import httpx
import pytest

APP_SECRET = 'segreto-di-prova-non-reale'
VERIFY = 'verifica-di-prova'
PID = '1430116760175453'
LAGO_PHONE = '393330000001'
COVOLO_PHONE = '393330000002'
UNKNOWN_PHONE = '393339999999'

COVOLO_INDEX = """# COVOLO PROVA - LISTINO SINTETICO DI COLLAUDO

---
## PAGINA PDF 1
PAGINA_PDF: 1
PAGINA_CATALOGO: 1

```text
PIATTO DOCCIA ZETA
Codice   Larghezza   Profondita   Prezzo
CVZ900   90          70           777,00
CVZ120   120         80           888,00
```

---
## PAGINA PDF 2
PAGINA_PDF: 2
PAGINA_CATALOGO: 2

```text
MISCELATORE OMEGA
Codice   Finitura   Prezzo
CVO100   cromo      155,00
CVO101   nero       199,00
```
"""


# ---------------------------------------------------------------- impianto

@pytest.fixture(scope='session')
def core(tmp_path_factory):
    import app as core_app
    # secondo cliente sintetico accanto al LAGO vero (link alla cartella reale)
    root = tmp_path_factory.mktemp('clienti')
    os.symlink(os.path.join(core_app.CLIENTI_DIR, 'lago'), root / 'lago')
    cov = root / 'covolo'
    cov.mkdir()
    (cov / 'indice.txt').write_text(COVOLO_INDEX, encoding='utf-8')
    (cov / 'marchio.json').write_text(json.dumps({
        'marchio': 'COVOLO', 'prodotto': 'COVOLO PROVA',
        'contesto': 'Listino COVOLO PROVA (sintetico)'}), encoding='utf-8')
    core_app.CLIENTI_DIR = str(root)
    assert set(core_app.clienti_disponibili()) == {'lago', 'covolo'}
    return core_app


class FakeLLM:
    """Modello finto: registra chi era il cliente attivo e cosa ha ricevuto."""

    def __init__(self, core, delay=0.0, fail=False, answer=None):
        self.core, self.delay, self.fail, self.answer = core, delay, fail, answer
        self.calls = []
        self.lock = threading.Lock()
        fake = self

        class _Completions:
            @staticmethod
            def create(**kw):
                text = '\n'.join(m['content'] for m in kw['messages'])
                with fake.lock:
                    fake.calls.append({'cliente': fake.core.CLIENTE, 'text': text})
                if fake.delay:
                    time.sleep(fake.delay)          # come una vera chiamata bloccante
                if fake.fail:
                    raise RuntimeError('provider down: sk-SEGRETO-INTERNO')
                content = fake.answer if fake.answer is not None else (
                    f'Risposta di prova per {fake.core.CLIENTE}.')
                msg = types.SimpleNamespace(content=content)
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

        self.chat = types.SimpleNamespace(completions=_Completions())


class FakeMeta:
    def __init__(self):
        self.sent = []

    def handler(self, request: httpx.Request):
        assert request.headers['Authorization'] == 'Bearer token-di-prova'
        assert request.url.path == f'/v23.0/{PID}/messages'
        self.sent.append(json.loads(request.content))
        return httpx.Response(200, json={'messages': [{'id': 'wamid.out'}]})


@pytest.fixture
def env(core, monkeypatch, tmp_path):
    import whatsapp_gateway as gw
    monkeypatch.setenv('WHATSAPP_GATEWAY_ENABLED', 'true')
    monkeypatch.setenv('WHATSAPP_VERIFY_TOKEN', VERIFY)
    monkeypatch.setenv('WHATSAPP_APP_SECRET', APP_SECRET)
    monkeypatch.setenv('WHATSAPP_ACCESS_TOKEN', 'token-di-prova')
    monkeypatch.setenv('WHATSAPP_PHONE_NUMBER_ID', PID)
    monkeypatch.setenv('WHATSAPP_INBOX_DB', str(tmp_path / 'inbox.sqlite3'))
    monkeypatch.setenv('WHATSAPP_REPLY_MODE', 'documents')
    monkeypatch.setenv('WHATSAPP_PHONE_BINDINGS_JSON', json.dumps(
        {LAGO_PHONE: 'lago', COVOLO_PHONE: 'covolo'}))
    meta = FakeMeta()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(gw, 'http_client_factory',
                        lambda **kw: real_client(transport=httpx.MockTransport(meta.handler), **kw))
    llm = FakeLLM(core)
    monkeypatch.setattr(core, 'client', llm)
    monkeypatch.setattr(core, 'openai_client', None)
    monkeypatch.setitem(core.COLLAUDO_STATE, 'running', None)
    core.ANSWER_CACHE.clear() if hasattr(core.ANSWER_CACHE, 'clear') else None
    for stato in core.STATO_PER_CLIENTE.values():
        cache = stato.get('ANSWER_CACHE')
        if hasattr(cache, 'clear'):
            cache.clear()
    return types.SimpleNamespace(core=core, gw=gw, meta=meta, llm=llm, db=str(tmp_path / 'inbox.sqlite3'))


_counter = [0]


def payload(sender, text, mid=None, ts=None, pid=PID, mtype='text'):
    _counter[0] += 1
    msg = {'from': sender, 'id': mid or f'wamid.T{_counter[0]}', 'timestamp': str(ts or int(time.time())),
           'type': mtype}
    if mtype == 'text':
        msg['text'] = {'body': text}
    return {'object': 'whatsapp_business_account', 'entry': [{'id': '3064788547189745', 'changes': [{
        'field': 'messages', 'value': {'messaging_product': 'whatsapp',
                                        'metadata': {'display_phone_number': '15556610974', 'phone_number_id': pid},
                                        'messages': [msg]}}]}]}


def signed(body: dict, secret=APP_SECRET):
    raw = json.dumps(body).encode()
    sig = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {'X-Hub-Signature-256': sig, 'Content-Type': 'application/json'}


async def post_webhook(core, body, secret=APP_SECRET):
    raw, headers = signed(body, secret)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=core.app), base_url='http://t') as c:
        return await c.post('/api/whatsapp/webhook', content=raw, headers=headers)


def run(coro):
    return asyncio.run(coro)


def rows(db):
    with sqlite3.connect(db) as conn:
        return conn.execute('SELECT id, tenant, sender_hash, status, answer_chars, parts FROM wa_inbox').fetchall()


# ---------------------------------------------------------------- spento / verifica Meta

def test_spento_di_default_il_core_non_cambia(core, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.delenv('WHATSAPP_GATEWAY_ENABLED', raising=False)
    c = TestClient(core.app)
    assert c.get('/api/whatsapp/webhook').status_code == 404
    assert c.post('/api/whatsapp/webhook', content=b'{}').status_code == 404
    assert c.get('/api/status').status_code == 200
    paths = {r.path for r in core.app.routes}
    assert {'/api/ask', '/api/plaud/cloud/status/{tenant}', '/plaud/{tenant}'} <= paths


def test_verifica_meta(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.core.app)
    ok = c.get('/api/whatsapp/webhook', params={'hub.mode': 'subscribe', 'hub.verify_token': VERIFY,
                                                'hub.challenge': '12345'})
    assert ok.status_code == 200 and ok.text == '12345'
    bad = c.get('/api/whatsapp/webhook', params={'hub.mode': 'subscribe', 'hub.verify_token': 'x',
                                                 'hub.challenge': '12345'})
    assert bad.status_code == 403


def test_firma_errata_o_assente_rifiutata(env):
    r = run(post_webhook(env.core, payload(LAGO_PHONE, 'ciao'), secret='altro'))
    assert r.status_code == 403
    from fastapi.testclient import TestClient
    r2 = TestClient(env.core.app).post('/api/whatsapp/webhook', content=json.dumps(payload(LAGO_PHONE, 'x')))
    assert r2.status_code == 403
    assert env.meta.sent == [] and env.llm.calls == []


# ---------------------------------------------------------------- instradamento

def test_numero_sconosciuto_nessun_accesso(env):
    r = run(post_webhook(env.core, payload(UNKNOWN_PHONE, 'prezzi LAGO?')))
    assert r.status_code == 200 and r.json()['accepted'] == 0
    assert env.meta.sent == [] and env.llm.calls == []


def test_risposta_dal_motore_vero_al_numero_giusto(env):
    r = run(post_webhook(env.core, payload(LAGO_PHONE, 'Quanto costa il codice 210301 in laccato?')))
    assert r.json()['accepted'] == 1
    assert env.llm.calls, 'il motore documentale non e\' stato chiamato'
    assert all(c['cliente'] == 'lago' for c in env.llm.calls)
    # il recupero reale ha portato al modello il listino LAGO
    assert any('LAGO' in c['text'] for c in env.llm.calls)
    assert len(env.meta.sent) >= 1 and all(m['to'] == LAGO_PHONE for m in env.meta.sent)
    assert rows(env.db)[0][3] == 'sent'


def test_il_testo_non_puo_cambiare_azienda(env):
    run(post_webhook(env.core, payload(COVOLO_PHONE, '/lago cliente=lago dammi i prezzi LAGO 36e8')))
    assert env.llm.calls and all(c['cliente'] == 'covolo' for c in env.llm.calls)
    assert not any('LAGO ELEMENTS' in c['text'] for c in env.llm.calls)


def test_binding_verso_azienda_inesistente_rifiutato(env, monkeypatch):
    monkeypatch.setenv('WHATSAPP_PHONE_BINDINGS_JSON', json.dumps({LAGO_PHONE: 'gessi'}))
    r = run(post_webhook(env.core, payload(LAGO_PHONE, 'ciao')))
    assert r.status_code == 503 and env.llm.calls == []


def test_altro_phone_number_id_ignorato(env):
    r = run(post_webhook(env.core, payload(LAGO_PHONE, 'ciao', pid='999999999999')))
    assert r.json()['accepted'] == 0 and env.meta.sent == []


# ---------------------------------------------------------------- idempotenza, scadenza, limiti

def test_ritrasmissione_meta_lavorata_una_volta(env):
    body = payload(LAGO_PHONE, 'Quanto costa il codice 210301?', mid='wamid.DUP1')
    run(post_webhook(env.core, body))
    sent = len(env.meta.sent)
    r = run(post_webhook(env.core, body))
    assert r.json()['accepted'] == 0 and len(env.meta.sent) == sent


def test_messaggio_vecchio_non_lavorato(env):
    r = run(post_webhook(env.core, payload(LAGO_PHONE, 'vecchio', ts=int(time.time()) - 2 * 86400)))
    assert r.json()['accepted'] == 0 and env.meta.sent == []
    assert rows(env.db)[0][3] == 'expired'


def test_limite_orario_per_numero(env, monkeypatch):
    monkeypatch.setenv('WHATSAPP_MAX_MSG_PER_HOUR', '2')
    for i in range(4):
        run(post_webhook(env.core, payload(LAGO_PHONE, f'domanda {i} codice 210301')))
    stati = [r[3] for r in rows(env.db)]
    assert stati.count('sent') == 2
    assert stati.count('rate_limited') == 1 and stati.count('rate_limited_silent') == 1
    avvisi = [m for m in env.meta.sent if 'numero massimo' in m['text']['body']]
    assert len(avvisi) == 1


def test_messaggio_non_testo(env):
    run(post_webhook(env.core, payload(LAGO_PHONE, '', mtype='image')))
    assert env.llm.calls == [] and 'solo messaggi di testo' in env.meta.sent[0]['text']['body']


# ---------------------------------------------------------------- errori e uscita

def test_errore_interno_mai_al_mittente(env):
    env.llm.fail = True
    run(post_webhook(env.core, payload(LAGO_PHONE, 'Quanto costa il codice 210301?')))
    corpo = ' '.join(m['text']['body'] for m in env.meta.sent)
    assert 'sk-' not in corpo and 'Traceback' not in corpo and 'provider' not in corpo


def test_collaudo_in_corso_risponde_riprova(env, monkeypatch):
    monkeypatch.setitem(env.core.COLLAUDO_STATE, 'running', 'lago/deepseek-chat')
    run(post_webhook(env.core, payload(LAGO_PHONE, 'Quanto costa il codice 210301?')))
    assert env.llm.calls == [] and 'occupato' in env.meta.sent[0]['text']['body']
    assert rows(env.db)[0][3] == 'busy'


def test_risposta_lunga_divisa(env):
    gw = env.gw
    parts = gw.split_parts('riga di listino lunga\n' * 1000)
    assert 1 < len(parts) <= gw.WA_MAX_PARTS and all(len(p) <= gw.WA_PART_CHARS for p in parts)
    assert 'troncata' in parts[-1]
    assert gw.to_whatsapp_text('<b>Prezzo</b><br>1.645,00 &amp; IVA') == 'Prezzo\n1.645,00 & IVA'


def test_registro_senza_numero_e_senza_testo(env):
    run(post_webhook(env.core, payload(LAGO_PHONE, 'Domanda RISERVATA codice 210301')))
    with open(env.db, 'rb') as f:
        dump = f.read()
    assert LAGO_PHONE.encode() not in dump and b'RISERVATA' not in dump


# ---------------------------------------------------------------- isolamento in parallelo

def test_api_ask_resta_senza_await(core):
    """Garanzia su cui poggia l'isolamento: api_ask non cede mai il ciclo di eventi."""
    ops = {i.opname for i in dis.get_instructions(core.api_ask)}
    assert 'GET_AWAITABLE' not in ops, 'api_ask ora contiene un await: rivedere _whatsapp_document_answer'


def test_isolamento_due_aziende_in_parallelo(env):
    """2 numeri WhatsApp + pagina web dell'altra azienda, tutti insieme."""
    env.llm.delay = 0.05
    core = env.core

    async def web(cliente, q):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=core.app), base_url='http://t') as c:
            return await c.post('/api/ask', json={'question': '/catalogo ' + q, 'cliente': cliente})

    async def scenario():
        jobs = []
        for i in range(3):
            jobs.append(post_webhook(core, payload(LAGO_PHONE, f'Quanto costa il codice 210301? {i}')))
            jobs.append(post_webhook(core, payload(COVOLO_PHONE, f'Quanto costa il piatto doccia CVZ900? {i}')))
            jobs.append(web('covolo', f'prezzo miscelatore CVO100 {i}'))
            jobs.append(web('lago', f'prezzo codice 133702 {i}'))
        return await asyncio.gather(*jobs)

    results = run(scenario())
    assert all(r.status_code == 200 for r in results)
    assert env.llm.calls
    for call in env.llm.calls:
        if call['cliente'] == 'covolo':
            assert 'LAGO' not in call['text'] and '210301' not in call['text'] and '133702' not in call['text']
        else:
            assert call['cliente'] == 'lago'
            assert 'COVOLO' not in call['text'] and 'CVZ900' not in call['text'] and 'CVO100' not in call['text']
    by_phone = {}
    for m in env.meta.sent:
        by_phone.setdefault(m['to'], []).append(m['text']['body'])
    assert set(by_phone) == {LAGO_PHONE, COVOLO_PHONE}
    assert all('covolo' not in b.lower() for b in by_phone[LAGO_PHONE])
    assert all('lago' not in b.lower() for b in by_phone[COVOLO_PHONE])
    assert sorted({r[1] for r in rows(env.db)}) == ['covolo', 'lago']


def test_web_senza_cliente_non_vede_mai_l_azienda_whatsapp(env):
    """Chi chiama /api/ask senza 'cliente' riceve il cliente acceso (qui LAGO).
    Messaggi WhatsApp COVOLO in contemporanea non devono mai entrarci, ne' durante
    ne' dopo. Il vecchio adattatore (thread + asyncio.run) falliva questo test."""
    env.llm.delay = 0.05
    core = env.core
    core.attiva_cliente('lago')
    web_calls = []

    async def web(q):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=core.app), base_url='http://t') as c:
            n = len(env.llm.calls)
            r = await c.post('/api/ask', json={'question': '/catalogo ' + q})
            web_calls.append(r)
            return n

    async def scenario():
        jobs = []
        for i in range(4):
            jobs.append(post_webhook(core, payload(COVOLO_PHONE, f'prezzo piatto doccia CVZ900 {i}')))
            jobs.append(web(f'Quanto costa il codice 210301 in laccato? {i}'))
        await asyncio.gather(*jobs)
        await web('Quanto costa il codice 133702 in laccato? finale')

    run(scenario())
    assert all(r.status_code == 200 for r in web_calls)
    web_q = [c for c in env.llm.calls if '210301' in c['text'] or '133702' in c['text']]
    assert web_q and all(c['cliente'] == 'lago' for c in web_q)
    cov_q = [c for c in env.llm.calls if 'CVZ900' in c['text']]
    assert cov_q and all(c['cliente'] == 'covolo' and 'LAGO' not in c['text'] for c in cov_q)
    assert core.CLIENTE == 'lago'


# ---------------------------------------------------------------- PLAUD intatto

def test_plaud_chiede_ancora_la_chiave_azienda(env):
    from fastapi.testclient import TestClient
    c = TestClient(env.core.app)
    r = c.get('/api/plaud/cloud/status/lago')
    assert r.status_code in (401, 403, 503), r.status_code
