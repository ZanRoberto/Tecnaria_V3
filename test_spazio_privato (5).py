"""Collaudo dello SPAZIO PRIVATO del titolare sul CORE VERO (app.py + indice LAGO reale).

Simulati SOLO: il modello linguistico, l'API Meta in uscita e le risposte di PLAUD.
Rotte, chiavi, archivio su disco, PIN, collegamento del telefono, gateway: codice vero.

Cosa deve restare vero:
- lo spazio privato e l'archivio dell'azienda non si vedono mai a vicenda;
- un numero si collega solo con il codice della dashboard;
- su WhatsApp il PIN non si chiede mai intero; 3 errori bloccano; si sblocca dalla dashboard;
- un numero non collegato riceve esattamente le risposte di prima;
- una registrazione PLAUD entra intera (tutte le pagine) o non entra.

Esecuzione:  python -m pytest -q test_spazio_privato.py
"""
import asyncio
import io
import json
import re
import time
import types

import httpx
import pytest
from fastapi.testclient import TestClient

from test_whatsapp_core import (APP_SECRET, COVOLO_PHONE, LAGO_PHONE, PID, UNKNOWN_PHONE,  # noqa: F401
                                FakeLLM, FakeMeta, core, payload, post_webhook)

KEYS = {'roberto': 'chiave-roberto', 'covolo': 'chiave-covolo'}
PIN = '482913'
SEGRETO = 'Telefonata con Mario Bianchi: per entrare in quella societa serve parlare con Luisa entro novembre.'
PREZZO = 'Mario Bianchi ha detto che il prezzo concordato per la fornitura e 18.400 euro.'


@pytest.fixture
def env(core, monkeypatch, tmp_path):  # noqa: F811
    import plaud_remote
    import documenti_aziendali as docs
    import spazio_privato as sp
    import whatsapp_gateway as gw
    monkeypatch.setenv('DOCUMENT_UPLOAD_ROOT', str(tmp_path / 'archivio'))
    monkeypatch.setenv('APPLAUDI_TENANT_CLIENT_MAP', json.dumps({'roberto': 'lago'}))
    monkeypatch.setenv('APPLAUDI_MODALITA_JSON', json.dumps({'roberto': 'mix', 'covolo': 'mix'}))
    monkeypatch.setattr(plaud_remote, 'TENANT_KEYS', dict(KEYS))
    monkeypatch.setenv('WHATSAPP_GATEWAY_ENABLED', 'true')
    monkeypatch.setenv('WHATSAPP_VERIFY_TOKEN', 'verifica')
    monkeypatch.setenv('WHATSAPP_APP_SECRET', APP_SECRET)
    monkeypatch.setenv('WHATSAPP_ACCESS_TOKEN', 'token-di-prova')
    monkeypatch.setenv('WHATSAPP_PHONE_NUMBER_ID', PID)
    monkeypatch.setenv('WHATSAPP_INBOX_DB', str(tmp_path / 'inbox.sqlite3'))
    monkeypatch.setenv('WHATSAPP_REPLY_MODE', 'documents')
    monkeypatch.setenv('WHATSAPP_MAX_MSG_PER_HOUR', '500')
    monkeypatch.setenv('WHATSAPP_PHONE_BINDINGS_JSON', json.dumps(
        {LAGO_PHONE: 'lago', COVOLO_PHONE: 'covolo'}))
    meta = FakeMeta()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(gw, 'http_client_factory',
                        lambda **kw: real_client(transport=httpx.MockTransport(meta.handler), **kw))
    llm = FakeLLM(core, answer='Risposta privata di prova.')
    monkeypatch.setattr(core, 'client', llm)
    monkeypatch.setattr(core, 'openai_client', None)
    monkeypatch.setattr(docs, 'embedder', None)
    monkeypatch.setitem(core.COLLAUDO_STATE, 'running', None)
    sp._PAGINE.clear()
    for cliente in ('lago', 'covolo'):
        core._documenti_cambiati(cliente)
    for stato in core.STATO_PER_CLIENTE.values():
        stato['ANSWER_CACHE'].clear()
    core.ANSWER_CACHE.clear()
    return types.SimpleNamespace(core=core, docs=docs, sp=sp, gw=gw, meta=meta, llm=llm,
                                 http=TestClient(core.app), root=tmp_path / 'archivio')


def H(tenant='roberto'):
    return {'X-Applaudi-Tenant-Key': KEYS[tenant]}


def carica(env, testo, spazio='privato', tenant='roberto', nome='nota.txt', **campi):
    r = env.http.post(f'/api/documents/{tenant}/upload?spazio={spazio}',
                      files={'file': (nome, io.BytesIO(testo.encode()), 'text/plain')}, headers=H(tenant))
    assert r.status_code == 200, r.text
    doc = r.json()['document']
    campi.setdefault('tipo', 'altro')
    r = env.http.post(f'/api/documents/{tenant}/{doc["id"]}/scheda?spazio={spazio}', json=campi,
                      headers=H(tenant))
    assert r.status_code == 200, r.text
    return r.json()['document']


def chiedi(env, domanda, tenant='roberto'):
    env.llm.calls.clear()
    r = env.http.post(f'/api/spazio/{tenant}/chiedi', json={'domanda': domanda}, headers=H(tenant))
    assert r.status_code == 200, r.text
    return r.json()


def pin(env, valore=PIN, conferma=None, tenant='roberto'):
    return env.http.post(f'/api/spazio/{tenant}/pin', json={'pin': valore, 'conferma': conferma or valore},
                         headers=H(tenant))


def collega(env, telefono=LAGO_PHONE, tenant='roberto'):
    codice = env.http.post(f'/api/spazio/{tenant}/collega', headers=H(tenant)).json()['codice']
    return wa(env, telefono, f'collega {codice}')


def wa(env, telefono, testo):
    """Un messaggio WhatsApp vero (firmato) e il testo della risposta inviata."""
    env.meta.sent.clear()
    env.llm.calls.clear()
    r = asyncio.run(post_webhook(env.core, payload(telefono, testo)))
    assert r.status_code == 200, r.text
    return '\n'.join(m['text']['body'] for m in env.meta.sent if m.get('type') == 'text')


def cifre_richieste(messaggio, valore=PIN):
    posizioni = [int(n) - 1 for n in re.findall(r'la (\d)ª', messaggio)]
    assert len(posizioni) == 3, messaggio
    return ''.join(valore[p] for p in posizioni)


def sblocca_whatsapp(env, telefono=LAGO_PHONE, domanda='Cosa mi ha detto Mario Bianchi su Luisa?'):
    sfida = wa(env, telefono, domanda)
    assert 'cifra del tuo PIN' in sfida and PIN not in sfida
    return wa(env, telefono, cifre_richieste(sfida))


# ---------------------------------------------------------------- separazione degli archivi

def test_spazio_privato_e_azienda_non_si_vedono(env):
    priv = carica(env, SEGRETO)
    lista_priv = env.http.get('/api/documents/roberto?spazio=privato', headers=H()).json()
    lista_az = env.http.get('/api/documents/roberto', headers=H()).json()
    assert lista_priv['knowledge_client'] == 'privato-roberto' and lista_az['knowledge_client'] == 'lago'
    assert [d['id'] for d in lista_priv['documents']] == [priv['id']]
    assert priv['id'] not in [d['id'] for d in lista_az['documents']]
    # una domanda al catalogo LAGO non vede MAI lo spazio privato
    env.llm.calls.clear()
    r = env.http.post('/api/ask', json={'question': '/catalogo Cosa ha detto Mario Bianchi su Luisa?',
                                        'cliente': 'lago'})
    assert r.status_code == 200
    assert all('serve parlare' not in c['text'] for c in env.llm.calls)


def test_lo_spazio_privato_risponde_con_le_sue_fonti(env):
    carica(env, SEGRETO, titolo='Telefonata Mario')
    out = chiedi(env, 'Cosa mi ha detto Mario Bianchi su Luisa?')
    assert out['risposta'] == 'Risposta privata di prova.'
    assert [f['titolo'] for f in out['fonti']] == ['Telefonata Mario']
    prompt = env.llm.calls[0]['text']
    assert 'serve parlare' in prompt and 'archivio personale e PRIVATO' in prompt
    assert 'RICHIESTA ORIGINALE' not in prompt                 # non passa dal motore del catalogo
    righe = env.docs.leggi_registro('privato-roberto')
    assert righe and righe[0]['documenti'][0]['titolo'] == 'Telefonata Mario'
    assert not any('Luisa' in r.get('domanda', '') for r in env.docs.leggi_registro('lago'))


def test_domanda_di_prezzo_trova_la_telefonata(env):
    # nello spazio privato la regola "prezzi solo da listini" non vale
    carica(env, PREZZO, titolo='Telefonata fornitura')
    out = chiedi(env, 'Qual e il prezzo concordato con Mario Bianchi?')
    assert out['fonti'] and '18.400' in env.llm.calls[0]['text']


def test_niente_fonti_niente_modello(env):
    carica(env, SEGRETO)
    out = chiedi(env, 'Quanto costa un biglietto per Tokyo?')
    assert out['risposta'] == env.sp.MSG_NON_TROVATO and env.llm.calls == []


def test_spazio_vuoto(env):
    out = chiedi(env, 'Cosa ho registrato ieri?')
    assert out['risposta'] == env.sp.MSG_VUOTO and env.llm.calls == []


def test_chiave_di_un_altro_non_apre(env):
    carica(env, SEGRETO)
    r = env.http.get('/api/documents/roberto?spazio=privato', headers=H('covolo'))
    assert r.status_code in (401, 403)
    r = env.http.post('/api/spazio/roberto/chiedi', json={'domanda': 'Luisa?'}, headers=H('covolo'))
    assert r.status_code in (401, 403)
    # con la sua chiave Covolo vede il SUO spazio privato, vuoto
    lista = env.http.get('/api/documents/covolo?spazio=privato', headers=H('covolo')).json()
    assert lista['knowledge_client'] == 'privato-covolo' and lista['documents'] == []
    r = env.http.get('/api/documents/roberto?spazio=segreto', headers=H())
    assert r.status_code == 422


def test_nessuna_mappa_punta_a_uno_spazio_privato(env, monkeypatch):
    monkeypatch.setenv('APPLAUDI_TENANT_CLIENT_MAP', json.dumps({'covolo': 'privato-roberto'}))
    assert env.docs.client_for_tenant('covolo') == 'covolo'       # la mappa verso il privato e' ignorata
    with pytest.raises(Exception):
        env.docs.client_for_tenant('privato-roberto')


def test_documento_riservato_privato_non_esce_su_whatsapp(env):
    carica(env, SEGRETO, titolo='Telefonata Mario', riservato=True)
    assert chiedi(env, 'Cosa mi ha detto Mario Bianchi su Luisa?')['fonti']   # dashboard: si
    pin(env)
    collega(env)
    risposta = sblocca_whatsapp(env)
    assert risposta.endswith(env.sp.MSG_NON_TROVATO)                          # WhatsApp: no
    assert env.llm.calls == []


# ---------------------------------------------------------------- PIN

def test_pin_regole_e_niente_in_chiaro(env):
    assert pin(env, '111111').status_code == 400
    assert pin(env, '123456').status_code == 400
    assert pin(env, '12345').status_code == 400
    assert pin(env, PIN, '482914').status_code == 400
    r = pin(env)
    assert r.status_code == 200 and r.json()['pin_impostato'] is True
    collega(env)
    testo = (env.root / 'privato-roberto' / 'sicurezza.json').read_text()
    assert PIN not in testo and LAGO_PHONE not in testo
    assert pin(env, tenant='covolo').status_code == 200        # ognuno il suo
    assert env.http.post('/api/spazio/roberto/pin', json={'pin': PIN, 'conferma': PIN},
                         headers=H('covolo')).status_code in (401, 403)


# ---------------------------------------------------------------- collegamento del telefono

def test_collegamento_solo_con_il_codice_giusto(env):
    assert 'non valido' in wa(env, LAGO_PHONE, 'collega 000000')
    env.http.post('/api/spazio/roberto/collega', headers=H())
    stato = env.http.get('/api/spazio/roberto/sicurezza', headers=H()).json()
    assert stato['telefoni_collegati'] == 0 and stato['codice_in_attesa']
    assert 'collegato al tuo spazio privato' in collega(env)
    assert env.http.get('/api/spazio/roberto/sicurezza', headers=H()).json()['telefoni_collegati'] == 1


def test_codice_muore_dopo_troppi_tentativi(env):
    codice = env.http.post('/api/spazio/roberto/collega', headers=H()).json()['codice']
    sbagliato = f'{(int(codice) + 1) % 1000000:06d}'
    for _ in range(env.sp.CODICE_MAX_TENTATIVI):
        wa(env, UNKNOWN_PHONE, f'collega {sbagliato}')
    assert 'non valido' in wa(env, UNKNOWN_PHONE, f'collega {codice}')


def test_numero_sconosciuto_resta_ignorato(env):
    assert wa(env, UNKNOWN_PHONE, 'Cosa mi ha detto Mario?') == ''


def test_numero_non_collegato_risponde_come_prima(env):
    carica(env, SEGRETO)
    risposta = wa(env, COVOLO_PHONE, 'Quanto costa CVZ900?')
    assert risposta and 'Privato' not in risposta and '🏢' not in risposta
    assert all('serve parlare' not in c['text'] for c in env.llm.calls)


def test_comandi_di_spazio_da_numero_senza_privato_non_sono_domande(env):
    """23ott2026: «azienda» da un numero senza spazio privato e' finito al motore come domanda."""
    for parola in ('azienda', 'Azienda!', 'aiuto'):
        assert wa(env, COVOLO_PHONE, parola) == env.sp.MSG_SOLO_AZIENDA
    assert wa(env, COVOLO_PHONE, 'privato') == env.sp.MSG_SENZA_PRIVATO
    assert env.llm.calls == []
    assert wa(env, COVOLO_PHONE, 'Quanto costa CVZ900?')          # una domanda vera va al motore
    assert env.llm.calls


# ---------------------------------------------------------------- conversazione WhatsApp

def test_senza_pin_lo_spazio_privato_non_si_apre(env):
    carica(env, SEGRETO)
    collega(env)
    assert wa(env, LAGO_PHONE, 'Cosa mi ha detto Mario Bianchi su Luisa?') == env.sp.MSG_SENZA_PIN
    assert env.llm.calls == []


def test_pin_a_cifre_poi_risposta_privata(env):
    carica(env, SEGRETO, titolo='Telefonata Mario')
    pin(env)
    collega(env)
    sfida = wa(env, LAGO_PHONE, 'Cosa mi ha detto Mario Bianchi su Luisa?')
    assert env.llm.calls == []                                   # niente risposta prima del PIN
    risposta = wa(env, LAGO_PHONE, cifre_richieste(sfida))
    assert risposta.startswith('🔓 🔒 *Privato*') and 'Risposta privata di prova.' in risposta
    assert 'serve parlare' in env.llm.calls[0]['text']
    # nei 30 minuti seguenti nessun PIN
    risposta = wa(env, LAGO_PHONE, 'E Mario Bianchi cosa ha detto di novembre?')
    assert risposta.startswith('🔒 *Privato*') and 'cifra' not in risposta


def test_pin_intero_non_accettato(env):
    carica(env, SEGRETO)
    pin(env)
    collega(env)
    wa(env, LAGO_PHONE, 'Cosa mi ha detto Mario Bianchi su Luisa?')
    assert env.sp.MSG_PIN_INTERO in wa(env, LAGO_PHONE, PIN)
    assert env.http.get('/api/spazio/roberto/sicurezza', headers=H()).json()['errori'] == 0


def test_tre_errori_bloccano_e_si_sblocca_solo_dalla_dashboard(env):
    carica(env, SEGRETO)
    pin(env)
    collega(env)
    sfida = wa(env, LAGO_PHONE, 'Cosa mi ha detto Mario Bianchi su Luisa?')
    giuste = cifre_richieste(sfida)
    sbagliate = ''.join(str((int(c) + 1) % 10) for c in giuste)
    assert 'Tentativi rimasti: 2' in wa(env, LAGO_PHONE, sbagliate)
    assert 'Tentativi rimasti: 1' in wa(env, LAGO_PHONE, sbagliate)
    assert wa(env, LAGO_PHONE, sbagliate) == env.sp.MSG_BLOCCATO
    assert wa(env, LAGO_PHONE, giuste) == env.sp.MSG_BLOCCATO   # anche con le cifre giuste
    assert env.llm.calls == []
    assert env.http.get('/api/spazio/roberto/sicurezza', headers=H()).json()['bloccato'] is True
    env.http.post('/api/spazio/roberto/sblocca', headers=H())
    assert 'Risposta privata di prova.' in sblocca_whatsapp(env)


def test_cambio_spazio_lago_e_ritorno(env):
    carica(env, SEGRETO)
    pin(env)
    collega(env)
    assert 'LAGO' in wa(env, LAGO_PHONE, 'lago')
    risposta = wa(env, LAGO_PHONE, 'Quanto costa il mobile TV 211605?')
    assert risposta.startswith('🏢 *LAGO*')
    assert any('RICHIESTA ORIGINALE' in c['text'] for c in env.llm.calls)   # motore del catalogo
    assert all('serve parlare' not in c['text'] for c in env.llm.calls)
    assert 'spazio privato' in wa(env, LAGO_PHONE, 'privato')
    assert 'cifra del tuo PIN' in wa(env, LAGO_PHONE, 'Cosa mi ha detto Mario Bianchi su Luisa?')


def test_dopo_otto_ore_si_torna_al_privato(env):
    pin(env)
    collega(env)
    wa(env, LAGO_PHONE, 'lago')
    percorso = env.root / 'privato-roberto' / 'sicurezza.json'
    dati = json.loads(percorso.read_text())
    for sessione in dati['sessioni'].values():
        sessione['ultimo'] = int(time.time()) - env.sp.RITORNO_PRIVATO_S - 10
    percorso.write_text(json.dumps(dati))
    assert 'cifra del tuo PIN' in wa(env, LAGO_PHONE, 'Quanto costa il mobile TV 211605?')


def test_sblocco_scade_e_chiudi(env):
    carica(env, SEGRETO)
    pin(env)
    collega(env)
    sblocca_whatsapp(env)
    assert wa(env, LAGO_PHONE, 'chiudi') == env.sp.MSG_CHIUSO
    assert 'cifra del tuo PIN' in wa(env, LAGO_PHONE, 'Cosa mi ha detto Mario Bianchi su Luisa?')
    sblocca_whatsapp(env)
    percorso = env.root / 'privato-roberto' / 'sicurezza.json'
    dati = json.loads(percorso.read_text())
    for sessione in dati['sessioni'].values():
        sessione['sbloccato_fino'] = int(time.time()) - 1
    percorso.write_text(json.dumps(dati))
    assert 'cifra del tuo PIN' in wa(env, LAGO_PHONE, 'Cosa mi ha detto Mario Bianchi su Luisa?')


def test_nuovo_pin_chiude_le_sessioni(env):
    carica(env, SEGRETO)
    pin(env)
    collega(env)
    sblocca_whatsapp(env)
    pin(env, '739104')
    assert 'cifra del tuo PIN' in wa(env, LAGO_PHONE, 'Cosa mi ha detto Mario Bianchi su Luisa?')


def test_numero_solo_privato(env):
    carica(env, SEGRETO)
    pin(env)
    collega(env, UNKNOWN_PHONE)
    risposta = sblocca_whatsapp(env, UNKNOWN_PHONE)
    assert 'Risposta privata di prova.' in risposta
    # "lago" per un numero senza azienda e' una domanda come un'altra, nello spazio privato
    assert wa(env, UNKNOWN_PHONE, 'lago').startswith('🔒 *Privato*')


def test_scollega(env):
    pin(env)
    collega(env)
    env.http.post('/api/spazio/roberto/scollega', headers=H())
    risposta = wa(env, LAGO_PHONE, 'Quanto costa il mobile TV 211605?')
    assert risposta and '🏢' not in risposta and 'cifra' not in risposta     # di nuovo come prima


# ---------------------------------------------------------------- PLAUD

def bundle(testo='Mario Bianchi: serve parlare con Luisa.', completa=True):
    async def finto(tenant, recording_id):
        return {'detail': {}, 'transcript': testo, 'note': 'Sintesi: Luisa.',
                'transcript_pages': 3, 'transcript_complete': completa}
    return finto


def test_plaud_entra_nello_spazio_privato_una_volta(env, monkeypatch):
    monkeypatch.setattr(env.core, 'get_recording_bundle', bundle())
    url = '/api/plaud/cloud/archivia/roberto/of_abc123'
    r = env.http.post(url, json={'nome': 'Telefonata Mario', 'data': '2026-10-15', 'fonte': 'privato'},
                      headers=H())
    assert r.status_code == 200, r.text
    doc = r.json()['document']
    assert r.json()['knowledge_client'] == 'privato-roberto'
    assert doc['esterno'] == 'plaud:of_abc123' and doc['riservato'] is False
    assert doc['stato'] == 'attivo' and doc['tipo'] == 'registrazione'
    assert env.http.post(url, json={'fonte': 'privato'}, headers=H()).json()['gia_presente'] is True
    assert env.http.get('/api/documents/roberto', headers=H()).json()['documents'] == []   # LAGO
    assert chiedi(env, 'Cosa ha detto Mario Bianchi su Luisa?')['fonti'][0]['titolo'] == 'Telefonata Mario'


def test_plaud_trascrizione_incompleta_non_entra(env, monkeypatch):
    monkeypatch.setattr(env.core, 'get_recording_bundle', bundle(completa=False))
    r = env.http.post('/api/plaud/cloud/archivia/roberto/of_abc124', json={'fonte': 'privato'}, headers=H())
    assert r.status_code == 502
    assert env.http.get('/api/documents/roberto?spazio=privato', headers=H()).json()['documents'] == []


def _pagina(frasi, cursore=None):
    dati = {'utterances': [{'speaker': f'Persona {i % 2 + 1}', 'content': f'frase {n}'}
                           for i, n in enumerate(frasi)]}
    if cursore:
        dati['next_cursor'] = cursore
    return {'content': [{'type': 'text', 'text': json.dumps(dati)}]}


def test_trascrizione_letta_tutta(env, monkeypatch):
    import plaud_remote
    chiamate = []
    pagine = {None: _pagina(range(0, 500), 'c1'), 'c1': _pagina(range(500, 1000), 'c2'),
              'c2': _pagina(range(1000, 1010))}

    async def finto(tenant, method, params):
        chiamate.append(params['arguments'])
        return pagine[params['arguments'].get('cursor')]
    monkeypatch.setattr(plaud_remote, '_mcp_call', finto)
    out = asyncio.run(plaud_remote.get_transcript_complete('roberto', 'of_x'))
    assert out['complete'] is True and out['pages'] == 3
    assert [c.get('cursor') for c in chiamate] == [None, 'c1', 'c2']
    assert all(c['limit'] == 500 for c in chiamate)
    righe = out['text'].splitlines()
    assert len(righe) == 1010 and righe[0] == 'Persona 1: frase 0' and righe[-1].endswith('frase 1009')


def test_trascrizione_dubbia_dichiarata_incompleta(env, monkeypatch):
    import plaud_remote

    async def ripetuto(tenant, method, params):            # PLAUD ridà sempre lo stesso cursore
        return _pagina(range(10), 'sempre-uguale')
    monkeypatch.setattr(plaud_remote, '_mcp_call', ripetuto)
    assert asyncio.run(plaud_remote.get_transcript_complete('roberto', 'of_x'))['complete'] is False

    async def piena_senza_cursore(tenant, method, params):  # 500 frasi e nessun cursore leggibile
        return _pagina(range(500))
    monkeypatch.setattr(plaud_remote, '_mcp_call', piena_senza_cursore)
    assert asyncio.run(plaud_remote.get_transcript_complete('roberto', 'of_x'))['complete'] is False


def test_dashboard_ha_i_nuovi_comandi(env):
    pagina = env.http.get('/plaud/roberto').text
    for pezzo in ('Aggiungi tutte', 'NELLA CONOSCENZA', 'data-page="security"', "setSpazio('privato')",
                  '/api/spazio/', 'cerca(', 'data-page="search"'):
        assert pezzo in pagina, pezzo


# ---------------------------------------------------------------- foto, video, disegni con trigger

JPG = b'\xff\xd8\xff\xe0' + b'J' * 200
PNG = b'\x89PNG\r\n\x1a\n' + b'P' * 200
MP4 = b'\x00\x00\x00\x18ftypmp42' + b'V' * 200


def media(env, dati, nome, trigger, collegati='', spazio='privato', tenant='roberto'):
    return env.http.post(f'/api/documents/{tenant}/media?spazio={spazio}',
                         files={'file': (nome, io.BytesIO(dati), 'application/octet-stream')},
                         data={'trigger': trigger, 'collegati': collegati}, headers=H(tenant))


class MetaConMedia(FakeMeta):
    def __init__(self):
        super().__init__()
        self.uploads = []

    def handler(self, request):
        if request.url.path == f'/v23.0/{PID}/media':
            assert request.headers['Authorization'] == 'Bearer token-di-prova'
            self.uploads.append(request.content)
            return httpx.Response(200, json={'id': f'media-{len(self.uploads)}'})
        return super().handler(request)


def test_senza_trigger_non_entra(env):
    r = media(env, JPG, 'auto.jpg', '')
    assert r.status_code == 400 and 'trigger' in r.json()['detail']
    assert media(env, JPG, 'auto.jpg', 'corto').status_code == 400
    assert media(env, b'MZ eseguibile', 'virus.exe', 'Fiat targata JK345PT').status_code == 415
    assert media(env, PNG, 'finta.jpg', 'Fiat targata JK345PT').status_code == 415     # firma
    assert media(env, JPG, 'auto.jpg', 'Fiat targata JK345PT', spazio='azienda').status_code == 400
    assert env.http.get('/api/documents/roberto?spazio=privato', headers=H()).json()['documents'] == []


def test_foto_si_trova_con_il_trigger_e_arriva_allegata(env):
    r = media(env, JPG, 'auto.jpg', "Fiat targata JK345PT, l'auto che sono andato a vedere il 12 ottobre")
    assert r.status_code == 200, r.text
    doc = r.json()['document']
    assert doc['stato'] == 'attivo' and doc['tipo'] == 'immagine' and doc['media']['mime'] == 'image/jpeg'
    out = chiedi(env, 'Com era la JK345PT?')
    assert [a['id'] for a in out['allegati']] == [doc['id']]
    assert 'IMMAGINE: Fiat targata JK345PT' in env.llm.calls[0]['text']
    f = env.http.get(f'/api/documents/roberto/{doc["id"]}/media?spazio=privato', headers=H())
    assert f.status_code == 200 and f.content == JPG and f.headers['content-type'] == 'image/jpeg'
    assert env.http.get(f'/api/documents/roberto/{doc["id"]}/media?spazio=privato',
                        headers=H('covolo')).status_code in (401, 403)
    assert env.http.get(f'/api/documents/covolo/{doc["id"]}/media?spazio=privato',
                        headers=H('covolo')).status_code == 404


def test_foto_collegata_arriva_con_la_telefonata(env):
    tel = carica(env, SEGRETO, titolo='Telefonata Mario')
    foto = media(env, PNG, 'mario.png', 'Ritratto della persona incontrata in fiera', collegati=tel['id'])
    assert foto.status_code == 200, foto.text
    out = chiedi(env, 'Cosa mi ha detto Mario Bianchi su Luisa?')
    assert [f['titolo'] for f in out['fonti']] == ['Telefonata Mario']
    assert [a['id'] for a in out['allegati']] == [foto.json()['document']['id']]
    # collegamento verso un documento che non esiste: rifiutato
    assert media(env, MP4, 'v.mp4', 'Video della fiera di Verona', collegati='f' * 32).status_code == 400


def test_al_massimo_tre_allegati(env):
    for n in range(5):
        dati = JPG + bytes([n])
        assert media(env, dati, f'f{n}.jpg', f'Foto numero {n} del cantiere Rossi').status_code == 200
    out = chiedi(env, 'Le foto del cantiere Rossi')
    assert len(out['allegati']) == 3


def test_whatsapp_manda_la_foto(env, monkeypatch):
    meta = MetaConMedia()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(env.gw, 'http_client_factory',
                        lambda **kw: real_client(transport=httpx.MockTransport(meta.handler), **kw))
    env.meta = meta
    doc = media(env, JPG, 'auto.jpg', 'Fiat targata JK345PT vista a Bassano').json()['document']
    pin(env)
    collega(env)
    risposta = sblocca_whatsapp(env, domanda='Com era la JK345PT?')
    assert 'Risposta privata di prova.' in risposta
    assert len(meta.uploads) == 1 and JPG in meta.uploads[0]
    immagini = [m for m in meta.sent if m.get('type') == 'image']
    assert immagini and immagini[0]['image']['id'] == 'media-1'
    assert immagini[0]['image']['caption'] == doc['titolo']


def test_whatsapp_file_troppo_grande_avvisa(env, monkeypatch):
    meta = MetaConMedia()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(env.gw, 'http_client_factory',
                        lambda **kw: real_client(transport=httpx.MockTransport(meta.handler), **kw))
    monkeypatch.setitem(env.gw.MEDIA_LIMITS, 'image', 10)
    env.meta = meta
    media(env, JPG, 'auto.jpg', 'Fiat targata JK345PT vista a Bassano')
    pin(env)
    collega(env)
    risposta = sblocca_whatsapp(env, domanda='Com era la JK345PT?')
    assert 'troppo grande per WhatsApp' in risposta and meta.uploads == []


def test_foto_riservata_non_esce_su_whatsapp(env, monkeypatch):
    meta = MetaConMedia()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(env.gw, 'http_client_factory',
                        lambda **kw: real_client(transport=httpx.MockTransport(meta.handler), **kw))
    env.meta = meta
    doc = media(env, JPG, 'auto.jpg', 'Fiat targata JK345PT vista a Bassano').json()['document']
    env.http.post(f'/api/documents/roberto/{doc["id"]}/scheda?spazio=privato', json={'riservato': True},
                  headers=H())
    assert chiedi(env, 'Com era la JK345PT?')['allegati']                     # dashboard: si
    pin(env)
    collega(env)
    sblocca_whatsapp(env, domanda='Com era la JK345PT?')
    assert meta.uploads == []                                                  # WhatsApp: no


def test_trigger_modificabile_e_tipo_bloccato(env):
    doc = media(env, JPG, 'auto.jpg', 'Fiat targata JK345PT vista a Bassano').json()['document']
    url = f'/api/documents/roberto/{doc["id"]}/scheda?spazio=privato'
    assert env.http.post(url, json={'tipo': 'listino'}, headers=H()).status_code == 400
    assert env.http.post(url, json={'trigger': 'Alfa Romeo targata ZZ999ZZ'}, headers=H()).status_code == 200
    assert chiedi(env, 'Com era la ZZ999ZZ?')['allegati']
    assert chiedi(env, 'Com era la JK345PT?')['allegati'] == []
    testo = carica(env, 'Nota qualsiasi sul cantiere.')
    r = env.http.post(f'/api/documents/roberto/{testo["id"]}/scheda?spazio=privato',
                      json={'trigger': 'non vale'}, headers=H())
    assert r.status_code == 400


def test_togliere_poi_distruggere_con_il_pin(env):
    assert pin(env).status_code == 200
    doc = media(env, JPG, 'auto.jpg', 'Fiat targata JK345PT vista a Bassano').json()['document']
    cartella = env.root / 'privato-roberto' / 'files' / doc['id']
    assert (cartella / 'media.jpg').read_bytes() == JPG
    r = env.http.post(f'/api/documents/roberto/{doc["id"]}/togli?spazio=privato',
                      json={'motivo': 'altro', 'nota': 'non mi serve piu'}, headers=H())
    assert r.status_code == 200 and cartella.exists()
    assert chiedi(env, 'Com era la JK345PT?')['allegati'] == []           # fuori dalle risposte
    assert env.http.get(f'/api/documents/roberto/{doc["id"]}/media?spazio=privato',
                        headers=H()).status_code == 404                     # e il file non si serve
    url = f'/api/documents/roberto/{doc["id"]}/distruggi?spazio=privato'
    assert env.http.post(url, json={'pin': '111111'}, headers=H()).status_code == 403
    assert env.http.post(url, json={'pin': PIN}, headers=H()).status_code == 200   # privato: subito
    assert not cartella.exists()
    assert not (env.root / 'privato-roberto' / 'operazioni.jsonl').exists() or \
        'distruggi' not in (env.root / 'privato-roberto' / 'operazioni.jsonl').read_text()


def test_distruggi_tutto_dalla_dashboard(env):
    assert pin(env).status_code == 200
    carica(env, SEGRETO)
    media(env, JPG, 'auto.jpg', 'Fiat targata JK345PT vista a Bassano')
    chiedi(env, 'Cosa ha detto Mario Bianchi su Luisa?')                   # scrive il registro
    radice = env.root / 'privato-roberto'
    url = '/api/spazio/roberto/distruggi'
    assert env.http.post(url, json={'pin': PIN, 'conferma': 'distruggi'}, headers=H()).status_code == 400
    assert env.http.post(url, json={'pin': '111111', 'conferma': 'DISTRUGGI'}, headers=H()).status_code == 403
    # serve il PIN INTERO: le prime 3 cifre giuste non bastano
    assert env.http.post(url, json={'pin': PIN[:3] + '000', 'conferma': 'DISTRUGGI'}, headers=H()).status_code == 403
    env.http.post('/api/spazio/roberto/sblocca', headers=H())
    assert len(env.http.get('/api/documents/roberto?spazio=privato', headers=H()).json()['documents']) == 2
    r = env.http.post(url, json={'pin': PIN, 'conferma': 'DISTRUGGI'}, headers=H())
    assert r.status_code == 200 and r.json()['documenti_distrutti'] == 2
    assert sorted(p.name for p in radice.iterdir()) == ['sicurezza.json']     # resta solo il PIN
    assert env.http.get('/api/documents/roberto?spazio=privato', headers=H()).json()['documents'] == []
    assert chiedi(env, 'Cosa ha detto Mario Bianchi su Luisa?')['fonti'] == []
    assert env.http.get('/api/spazio/roberto/sicurezza', headers=H()).json()['pin_impostato']
    # l'azienda non e' stata toccata
    assert env.http.get('/api/documents/roberto', headers=H()).status_code == 200


def test_distruggi_tutto_da_whatsapp(env):
    assert pin(env).status_code == 200
    collega(env)
    carica(env, SEGRETO)
    sblocca_whatsapp(env)
    chiede = wa(env, LAGO_PHONE, 'distruggi')
    assert 'PIN *intero*' in chiede
    assert 'annullata' in wa(env, LAGO_PHONE, 'annulla')
    assert len(env.http.get('/api/documents/roberto?spazio=privato', headers=H()).json()['documents']) == 1
    wa(env, LAGO_PHONE, 'distruggi')
    sbagliato = wa(env, LAGO_PHONE, '111111')
    assert 'non è stato distrutto nulla' in sbagliato
    assert len(env.http.get('/api/documents/roberto?spazio=privato', headers=H()).json()['documents']) == 1
    wa(env, LAGO_PHONE, 'distruggi')
    assert 'distrutto' in wa(env, LAGO_PHONE, PIN)
    assert env.http.get('/api/documents/roberto?spazio=privato', headers=H()).json()['documents'] == []
    # la sessione si e' chiusa: la prossima domanda richiede di nuovo il PIN
    assert 'cifra del tuo PIN' in wa(env, LAGO_PHONE, 'Cosa ha detto Mario Bianchi?')


def test_distruggi_pin_sbagliato_tre_volte_blocca(env):
    assert pin(env).status_code == 200
    carica(env, SEGRETO)
    url = '/api/spazio/roberto/distruggi'
    for _ in range(3):
        env.http.post(url, json={'pin': '111111', 'conferma': 'DISTRUGGI'}, headers=H())
    r = env.http.post(url, json={'pin': PIN, 'conferma': 'DISTRUGGI'}, headers=H())
    assert r.status_code == 423
    assert len(env.http.get('/api/documents/roberto?spazio=privato', headers=H()).json()['documents']) == 1


def test_log_del_server_senza_titoli_privati(env, capsys):
    carica(env, SEGRETO, titolo='Telefonata segretissima con Luisa')
    capsys.readouterr()
    chiedi(env, 'Cosa ha detto Mario Bianchi su Luisa?')
    assert 'segretissima' not in capsys.readouterr().out


# ---------------------------------------------------------------- audio originale PLAUD

AUDIO = b'ID3' + b'A' * 5000


def detail_con_audio(url='https://audio.plaud.example/of_abc.mp3?firma=x'):
    return {'content': [{'type': 'text', 'text': json.dumps(
        {'data': {'name': 'Telefonata', 'presigned_url': url, 'share_url': 'https://altro.example/x'}})}]}


def bundle_audio(detail):
    async def finto(tenant, recording_id):
        return {'detail': detail, 'transcript': 'Mario Bianchi: serve parlare con Luisa.',
                'note': '', 'transcript_pages': 1, 'transcript_complete': True}
    return finto


def audio_server(monkeypatch, status=200, body=AUDIO):
    import plaud_remote
    viste = []

    def handler(request):
        viste.append(str(request.url))
        return httpx.Response(status, content=body, headers={'content-type': 'audio/mpeg'})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(plaud_remote, 'audio_client_factory',
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    return viste


def test_audio_originale_scaricato_con_impronta(env, monkeypatch):
    import hashlib
    viste = audio_server(monkeypatch)
    monkeypatch.setattr(env.core, 'get_recording_bundle', bundle_audio(detail_con_audio()))
    r = env.http.post('/api/plaud/cloud/archivia/roberto/of_abc', json={'nome': 'Telefonata', 'fonte': 'privato'},
                      headers=H())
    assert r.status_code == 200, r.text
    doc = r.json()['document']
    assert viste == ['https://audio.plaud.example/of_abc.mp3?firma=x']        # il link dell'audio, non un altro
    assert doc['audio']['impronta'] == hashlib.sha256(AUDIO).hexdigest()
    assert doc['audio']['bytes'] == len(AUDIO)
    salvato = env.root / 'privato-roberto' / 'files' / doc['id'] / 'audio_originale.mp3'
    assert salvato.read_bytes() == AUDIO
    assert any('audio originale scaricato' in s['cosa'] for s in doc['storia'])


def test_audio_non_scaricato_lo_dice_ma_la_registrazione_entra(env, monkeypatch):
    audio_server(monkeypatch, status=403)
    monkeypatch.setattr(env.core, 'get_recording_bundle', bundle_audio(detail_con_audio()))
    doc = env.http.post('/api/plaud/cloud/archivia/roberto/of_abd', json={'fonte': 'privato'},
                        headers=H()).json()['document']
    assert doc['stato'] == 'attivo' and doc['audio'] is None and 'audio non scaricato' in doc['audio_stato']
    assert not list((env.root / 'privato-roberto' / 'files' / doc['id']).glob('audio*'))


def test_audio_senza_disco_permanente_non_si_scarica(env, monkeypatch):
    viste = audio_server(monkeypatch)
    monkeypatch.setattr(env.docs, 'disco_persistente', lambda: False)
    monkeypatch.setattr(env.core, 'get_recording_bundle', bundle_audio(detail_con_audio()))
    doc = env.http.post('/api/plaud/cloud/archivia/roberto/of_abe', json={'fonte': 'privato'},
                        headers=H()).json()['document']
    assert viste == [] and 'disco permanente' in doc['audio_stato']


def test_link_audio_solo_https_e_solo_chiavi_audio(env):
    import plaud_remote
    assert plaud_remote.url_audio(detail_con_audio('http://audio.example/x.mp3')) is None
    altro = {'content': [{'type': 'text', 'text': json.dumps({'url': 'https://sito.example/pagina'})}]}
    assert plaud_remote.url_audio(altro) is None


def test_audio_oltre_il_limite_non_lascia_file(env, monkeypatch, tmp_path):
    import plaud_remote
    audio_server(monkeypatch)
    monkeypatch.setattr(plaud_remote, 'AUDIO_MAX_BYTES', 100)
    with pytest.raises(RuntimeError):
        asyncio.run(plaud_remote.scarica_audio('https://audio.example/a.mp3', str(tmp_path / 'c')))
    assert list((tmp_path / 'c').iterdir()) == []


def test_foto_riservata_collegata_non_esce_su_whatsapp(env, monkeypatch):
    meta = MetaConMedia()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(env.gw, 'http_client_factory',
                        lambda **kw: real_client(transport=httpx.MockTransport(meta.handler), **kw))
    env.meta = meta
    tel = carica(env, SEGRETO, titolo='Telefonata Mario')
    foto = media(env, PNG, 'mario.png', 'Ritratto della persona incontrata in fiera',
                 collegati=tel['id']).json()['document']
    env.http.post(f'/api/documents/roberto/{foto["id"]}/scheda?spazio=privato', json={'riservato': True},
                  headers=H())
    assert chiedi(env, 'Cosa mi ha detto Mario Bianchi su Luisa?')['allegati']    # dashboard: si
    pin(env)
    collega(env)
    assert 'Risposta privata di prova.' in sblocca_whatsapp(env)
    assert meta.uploads == []                                                       # WhatsApp: no


# ---------------------------------------------------------------- collegamento PLAUD su un servizio nuovo

def test_indirizzo_pubblico_da_render_se_manca(monkeypatch):
    import plaud_remote
    monkeypatch.delenv('PUBLIC_BASE_URL', raising=False)
    monkeypatch.setenv('RENDER_EXTERNAL_URL', 'https://applaudi-whatsapp-test.onrender.com')
    assert plaud_remote._redirect_uri() == 'https://applaudi-whatsapp-test.onrender.com/api/plaud/cloud/callback'
    monkeypatch.setenv('PUBLIC_BASE_URL', 'applaudi-whatsapp-test.onrender.com')     # scritta senza https
    assert plaud_remote.public_base_url() == 'https://applaudi-whatsapp-test.onrender.com'
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://tecnaria-v3.onrender.com/')        # LIVE: vale la sua
    assert plaud_remote.public_base_url() == 'https://tecnaria-v3.onrender.com'
    monkeypatch.delenv('PUBLIC_BASE_URL')
    monkeypatch.delenv('RENDER_EXTERNAL_URL')
    with pytest.raises(Exception):
        plaud_remote._redirect_uri()


def test_chiave_plaud_ricavata_e_stabile(monkeypatch):
    import plaud_remote
    monkeypatch.delenv('PLAUD_TOKEN_ENCRYPTION_KEY', raising=False)
    monkeypatch.setenv('WHATSAPP_APP_SECRET', 'segreto-lungo-di-prova-1234')
    sigillo = plaud_remote._seal({'token': 'x'})
    assert plaud_remote._open(sigillo) == {'token': 'x'}          # stessa chiave a ogni chiamata
    from cryptography.fernet import Fernet
    esplicita = Fernet.generate_key().decode()
    monkeypatch.setenv('PLAUD_TOKEN_ENCRYPTION_KEY', esplicita)
    assert plaud_remote._fernet_key() == esplicita                 # dove c'e' (LIVE) vale quella


# ---------------------------------------------------------------- correzioni dopo il collaudo del 18-19 ottobre

def test_sfida_mostra_lo_schema_del_pin(env):
    msg = env.sp.msg_sfida([0, 2, 5])
    assert '⬜⬛⬜⬛⬛⬜' in msg and 'la 1ª, la 3ª e la 6ª' in msg


def test_niente_asterischi_doppi(env):
    testo = 'Il mobile è il **Mobile TV 211605** in vetro.\n## Nota\nfine'
    assert env.sp.pulisci(testo, 'web') == 'Il mobile è il Mobile TV 211605 in vetro.\nNota\nfine'
    assert env.sp.pulisci(testo, 'whatsapp').startswith('Il mobile è il *Mobile TV 211605* in vetro.')


def test_risposta_web_senza_markdown(env, monkeypatch):
    carica(env, SEGRETO, titolo='Telefonata Mario')
    monkeypatch.setattr(env.llm, 'answer', 'Serve **parlare con Luisa** entro novembre.')
    assert chiedi(env, 'Cosa mi ha detto Mario Bianchi su Luisa?')['risposta'] == \
        'Serve parlare con Luisa entro novembre.'


def test_registrazione_non_scade_e_nessuna_validita_inventata(env, monkeypatch):
    monkeypatch.setattr(env.core, 'get_recording_bundle', bundle())
    doc = env.http.post('/api/plaud/cloud/archivia/roberto/of_abc777', json={'nome': 'Telefonata Mario',
                        'data': '2026-10-15', 'fonte': 'privato'}, headers=H()).json()['document']
    assert 'valido' not in doc['etichetta']
    import datetime as dt
    monkeypatch.setattr(env.docs, 'oggi', lambda: dt.date(2031, 1, 1))     # anni dopo
    env.sp._PAGINE.clear()
    assert chiedi(env, 'Cosa ha detto Mario Bianchi su Luisa?')['fonti']


def test_titolo_dal_trigger_tagliato_a_fine_parola(env):
    trigger = 'Mobile TV N.O.W. codice 211605 in vetro opaco, scelto dall\'architetto Ferri per via Roma 12 a Bassano'
    doc = media(env, JPG, 'auto.jpg', trigger).json()['document']
    assert doc['titolo'].endswith('…') and len(doc['titolo']) <= 81
    assert doc['titolo'].startswith('Mobile TV N.O.W. codice 211605')
    assert ' ' not in doc['titolo'][-2:]


def test_dashboard_mostra_cifre_pin(env):
    assert 'Mostra le cifre mentre le scrivi' in env.http.get('/plaud/roberto').text


# ---------------------------------------------------------------- modalita' decisa dal gestore

def test_modalita_aziendale_niente_spazio_privato(env, monkeypatch):
    monkeypatch.setenv('APPLAUDI_MODALITA_JSON', json.dumps({'roberto': 'aziendale'}))
    assert env.http.get('/api/documents/roberto/spazi', headers=H()).json()['spazi'] == ['azienda']
    assert env.http.get('/api/documents/roberto?spazio=privato', headers=H()).status_code == 403
    assert env.http.get('/api/spazio/roberto/sicurezza', headers=H()).status_code == 403
    assert env.http.get('/api/documents/roberto', headers=H()).status_code == 200
    # la registrazione va all'azienda, DA CONFERMARE: non entra nelle risposte da sola
    monkeypatch.setattr(env.core, 'get_recording_bundle', bundle())
    r = env.http.post('/api/plaud/cloud/archivia/roberto/of_az1', json={'nome': 'Riunione'}, headers=H())
    assert r.status_code == 200 and r.json()['spazio'] == 'azienda'
    assert r.json()['knowledge_client'] == 'lago' and r.json()['document']['stato'] == 'da_confermare'


def test_modalita_aziendale_whatsapp_resta_azienda(env, monkeypatch):
    pin(env)
    collega(env)                                          # collegato quando era mix
    monkeypatch.setenv('APPLAUDI_MODALITA_JSON', json.dumps({'roberto': 'aziendale'}))
    risposta = wa(env, LAGO_PHONE, 'Quanto costa il mobile TV 211605?')
    assert risposta and 'cifra' not in risposta and 'Privato' not in risposta


def test_modalita_privato_niente_azienda_e_niente_cambio(env, monkeypatch):
    monkeypatch.setenv('APPLAUDI_MODALITA_JSON', json.dumps({'roberto': 'privato'}))
    assert env.http.get('/api/documents/roberto/spazi', headers=H()).json()['spazi'] == ['privato']
    assert env.http.get('/api/documents/roberto', headers=H()).status_code == 403
    pin(env)
    collega(env)
    assert wa(env, LAGO_PHONE, 'lago').startswith('🔒')        # "lago" non porta all'azienda


def test_modalita_predefinita_e_aziendale(env, monkeypatch):
    monkeypatch.delenv('APPLAUDI_MODALITA_JSON', raising=False)
    assert env.docs.modalita('roberto') == 'aziendale'
    monkeypatch.setenv('APPLAUDI_MODALITA_JSON', json.dumps({'roberto': 'mix'}))
    assert env.docs.spazi_ammessi('roberto') == ('privato', 'azienda')


# ---------------------------------------------------------------- un PLAUD per direzione

def test_due_plaud_due_direzioni(env, monkeypatch):
    import plaud_remote
    assert plaud_remote.slot('roberto', 'azienda') == 'roberto'
    assert plaud_remote.slot('roberto', 'privato') == 'roberto#privato'
    monkeypatch.setattr(env.core, 'get_recording_bundle', bundle())
    url = '/api/plaud/cloud/archivia/roberto/'
    priv = env.http.post(url + 'of_p1', json={'fonte': 'privato'}, headers=H()).json()
    az = env.http.post(url + 'of_a1', json={'fonte': 'azienda'}, headers=H()).json()
    assert priv['knowledge_client'] == 'privato-roberto' and priv['document']['stato'] == 'attivo'
    assert az['knowledge_client'] == 'lago' and az['document']['stato'] == 'da_confermare'
    assert env.http.post(url + 'of_x', json={'fonte': 'altro'}, headers=H()).status_code == 400


def test_plaud_privato_non_esiste_se_non_abilitato(env, monkeypatch):
    import plaud_remote
    monkeypatch.setenv('APPLAUDI_MODALITA_JSON', json.dumps({'roberto': 'aziendale'}))
    with pytest.raises(Exception):
        plaud_remote.slot('roberto', 'privato')
    monkeypatch.setattr(env.core, 'get_recording_bundle', bundle())
    r = env.http.post('/api/plaud/cloud/archivia/roberto/of_p2', json={'fonte': 'privato'}, headers=H())
    assert r.status_code == 403
    assert env.http.post('/api/plaud/cloud/connect/roberto?fonte=privato', headers=H()).status_code == 403
    stato = env.http.get('/api/plaud/cloud/status/roberto', headers=H()).json()
    assert list(stato['fonti']) == ['azienda']


def test_stato_dei_due_plaud(env):
    stato = env.http.get('/api/plaud/cloud/status/roberto', headers=H()).json()
    assert set(stato['fonti']) == {'privato', 'azienda'}


def test_accenti_non_contano_nella_ricerca(env):
    media(env, JPG, 'visita.jpg', 'Foto per incontro caldaia di mercoledi con il cliente')
    assert chiedi(env, 'mi dai le notizie sulla visita di mercoledì?')['allegati']
