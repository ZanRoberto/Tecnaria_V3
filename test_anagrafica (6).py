"""Collaudo della CONSOLE DEL GESTORE (anagrafica.py) sul CORE VERO.

Cosa deve restare vero:
- senza APPLAUDI_GESTORE_KEY la console non si apre; con la chiave sbagliata nemmeno;
- la chiave di un cliente nuovo si vede UNA volta e sul disco resta solo l'impronta;
- un cliente nuovo entra con la sua chiave, un sospeso no, una chiave rigenerata
  spegne la vecchia;
- due aziende non condividono mai lo stesso archivio;
- i clienti e i numeri di Render vincono e non si toccano dalla console;
- il numero WhatsApp di una persona riceve le risposte della SUA azienda, mai di altre;
- il titolare aggiunge responsabili e membri, ma non nomina un titolare.

Esecuzione:  python -m pytest -q test_anagrafica.py
"""
import asyncio
import json
import types

import pytest

from test_spazio_privato import env as env_privato, H, KEYS  # noqa: F401
from test_whatsapp_core import core, payload, post_webhook  # noqa: F401

GESTORE = 'chiave-del-gestore-di-prova'
NUOVO_TEL = '393471112233'


@pytest.fixture
def env(env_privato, monkeypatch):  # noqa: F811
    import plaud_remote
    # covolo NON e' su Render: il suo catalogo e' libero per un cliente della console
    monkeypatch.setattr(plaud_remote, 'TENANT_KEYS', {'roberto': KEYS['roberto']})
    monkeypatch.setenv('APPLAUDI_GESTORE_KEY', GESTORE)
    monkeypatch.setenv('WHATSAPP_PHONE_BINDINGS_JSON', json.dumps({'393330000001': 'lago'}))
    return env_privato


def G(chiave=GESTORE):
    return {'X-Applaudi-Gestore-Key': chiave}


def crea(env, **campi):
    corpo = {'nome': 'Covolo Bagni', 'tipo': 'azienda', 'modalita': 'aziendale', 'archivio': 'covolo'}
    corpo.update(campi)
    return env.http.post('/api/gestore/clienti', json=corpo, headers=G())


def wa(env, telefono, testo):
    env.meta.sent.clear()
    r = asyncio.run(post_webhook(env.core, payload(telefono, testo)))
    assert r.status_code == 200, r.text
    return '\n'.join(m['text']['body'] for m in env.meta.sent if m.get('type') == 'text')


def test_console_chiusa_senza_chiave_del_gestore(env, monkeypatch):
    assert env.http.get('/api/gestore/stato', headers=G('sbagliata-ma-lunga')).status_code == 401
    monkeypatch.delenv('APPLAUDI_GESTORE_KEY')
    assert env.http.get('/api/gestore/stato', headers=G()).status_code == 503
    assert 'Console del gestore' in env.http.get('/gestore').text


def test_nuovo_cliente_con_la_sua_chiave(env):
    r = crea(env)
    assert r.status_code == 200, r.text
    tenant, chiave = r.json()['cliente']['id'], r.json()['chiave']
    assert tenant == 'covolo-bagni' and len(chiave) >= 30
    # sul disco solo l'impronta
    file = env.root.parent / 'anagrafica.json'
    assert file.exists() and chiave not in file.read_text()
    assert chiave not in (env.root.parent / 'anagrafica_registro.jsonl').read_text()
    # entra con la sua chiave, nel SUO archivio
    lista = env.http.get(f'/api/documents/{tenant}', headers={'X-Applaudi-Tenant-Key': chiave})
    assert lista.status_code == 200 and lista.json()['knowledge_client'] == 'covolo'
    assert env.http.get(f'/api/documents/{tenant}', headers={'X-Applaudi-Tenant-Key': 'altra'}).status_code == 401
    # compare nella dashboard
    assert '"covolo-bagni"' in env.http.get('/plaud/roberto').text
    # nessuna chiave nello stato della console
    assert chiave not in env.http.get('/api/gestore/stato', headers=G()).text


def test_due_aziende_non_condividono_un_archivio(env):
    assert crea(env, nome='Altra LAGO', archivio='lago').status_code == 409    # e' di roberto
    assert crea(env).status_code == 200
    assert crea(env, nome='Seconda Covolo').status_code == 409                 # ora e' preso
    assert crea(env, nome='Senza catalogo', archivio='').json()['cliente']['solo_documenti']   # archivio suo
    assert crea(env, nome='Catalogo inventato', archivio='nonesiste').status_code == 400


def test_chiave_rigenerata_e_sospensione(env):
    x = crea(env).json()
    tenant, vecchia = x['cliente']['id'], x['chiave']
    nuova = env.http.post(f'/api/gestore/clienti/{tenant}/chiave', headers=G()).json()['chiave']
    url = f'/api/documents/{tenant}'
    assert env.http.get(url, headers={'X-Applaudi-Tenant-Key': vecchia}).status_code == 401
    assert env.http.get(url, headers={'X-Applaudi-Tenant-Key': nuova}).status_code == 200
    env.http.post(f'/api/gestore/clienti/{tenant}', json={'sospeso': True}, headers=G())
    assert env.http.get(url, headers={'X-Applaudi-Tenant-Key': nuova}).status_code == 403
    env.http.post(f'/api/gestore/clienti/{tenant}', json={'sospeso': False}, headers=G())
    assert env.http.get(url, headers={'X-Applaudi-Tenant-Key': nuova}).status_code == 200
    azioni = [r['azione'] for r in env.http.get('/api/gestore/registro', headers=G()).json()['righe']]
    assert azioni[:4] == ['cliente_modificato', 'cliente_modificato', 'chiave_rigenerata', 'cliente_creato']


def test_cliente_privato_ha_solo_lo_spazio_privato(env):
    x = env.http.post('/api/gestore/clienti', json={'nome': 'Mario Rossi', 'tipo': 'privato'}, headers=G()).json()
    tenant, chiave = x['cliente']['id'], x['chiave']
    spazi = env.http.get(f'/api/documents/{tenant}/spazi', headers={'X-Applaudi-Tenant-Key': chiave}).json()
    assert spazi['spazi'] == ['privato'] and spazi['modalita'] == 'privato'
    assert env.http.get(f'/api/documents/{tenant}?spazio=azienda',
                        headers={'X-Applaudi-Tenant-Key': chiave}).status_code == 403
    assert env.http.get(f'/api/documents/{tenant}?spazio=privato',
                        headers={'X-Applaudi-Tenant-Key': chiave}).status_code == 200


def test_render_vince_e_non_si_tocca(env):
    assert crea(env, nome='Roberto', id='roberto').status_code == 409
    assert env.http.post('/api/gestore/clienti/roberto', json={'sospeso': True}, headers=G()).status_code == 409
    stato = env.http.get('/api/gestore/stato', headers=G()).json()
    roberto = next(c for c in stato['clienti'] if c['id'] == 'roberto')
    assert roberto['origine'] == 'render' and roberto['archivio'] == 'lago'


def test_persone_e_numeri_whatsapp(env):
    tenant = crea(env).json()['cliente']['id']
    url = f'/api/gestore/clienti/{tenant}/persone'
    r = env.http.post(url, json={'nome': 'Anna Covolo', 'ruolo': 'titolare', 'telefono': '347 111 2233'}, headers=G())
    assert r.status_code == 200 and r.json()['persona']['telefono'] == NUOVO_TEL      # prefisso 39 aggiunto
    assert env.http.post(url, json={'nome': 'Bis', 'ruolo': 'titolare'}, headers=G()).status_code == 409
    assert env.http.post(url, json={'nome': 'Doppio', 'telefono': NUOVO_TEL}, headers=G()).status_code == 409
    assert env.http.post(url, json={'nome': 'Su Render', 'telefono': '393330000001'}, headers=G()).status_code == 409
    assert env.http.post(url, json={'nome': 'Numero corto', 'telefono': '123'}, headers=G()).status_code == 400
    # il numero di Anna riceve le risposte di COVOLO (la sua azienda), non di LAGO
    env.llm.calls.clear()
    risposta = wa(env, NUOVO_TEL, 'Quanto costa il miscelatore?')
    assert risposta and env.llm.calls and env.llm.calls[-1]['cliente'] == 'covolo'
    # sospesa: il numero torna sconosciuto, nessuna risposta
    pid = r.json()['persona']['id']
    env.http.post(f'/api/gestore/persone/{pid}', json={'sospeso': True}, headers=G())
    assert wa(env, NUOVO_TEL, 'Quanto costa il miscelatore?') == ''


def test_il_titolare_gestisce_le_sue_persone(env):
    x = crea(env).json()
    tenant, chiave = x['cliente']['id'], x['chiave']
    TK = {'X-Applaudi-Tenant-Key': chiave}
    env.http.post(f'/api/gestore/clienti/{tenant}/persone', json={'nome': 'Anna', 'ruolo': 'titolare'}, headers=G())
    assert env.http.post(f'/api/persone/{tenant}', json={'nome': 'Bruno', 'ruolo': 'titolare'},
                         headers=TK).status_code == 400                      # il titolare no
    r = env.http.post(f'/api/persone/{tenant}', json={'nome': 'Bruno', 'ruolo': 'responsabile'}, headers=TK)
    assert r.status_code == 200
    persone = env.http.get(f'/api/persone/{tenant}', headers=TK).json()['persone']
    assert [p['nome'] for p in persone] == ['Anna', 'Bruno']                 # titolare per primo
    anna = persone[0]['id']
    assert env.http.post(f'/api/persone/{tenant}/{anna}', json={'sospeso': True}, headers=TK).status_code == 403
    bruno = r.json()['persona']['id']
    assert env.http.post(f'/api/persone/{tenant}/{bruno}', json={'sospeso': True}, headers=TK).status_code == 200
    # un altro cliente non vede e non tocca le persone di questo
    RK = H('roberto')
    assert all(p['nome'] not in ('Anna', 'Bruno') for p in env.http.get('/api/persone/roberto', headers=RK).json()['persone'])
    assert env.http.post(f'/api/persone/roberto/{bruno}', json={'sospeso': False}, headers=RK).status_code == 404


def test_un_cliente_di_render_puo_avere_persone(env):
    r = env.http.post('/api/persone/roberto', json={'nome': 'Mirco', 'ruolo': 'membro', 'telefono': '393479990000'},
                      headers=H('roberto'))
    assert r.status_code == 200
    env.llm.calls.clear()
    assert wa(env, '393479990000', 'Quanto costa il 211605?')
    assert env.llm.calls[-1]['cliente'] == 'lago'


def test_sospeso_non_entra_da_nessuna_porta(env):
    x = crea(env).json()
    tenant, chiave = x['cliente']['id'], x['chiave']
    env.http.post(f'/api/gestore/clienti/{tenant}', json={'sospeso': True}, headers=G())
    TK = {'X-Applaudi-Tenant-Key': chiave}
    for url in (f'/api/documents/{tenant}/spazi', f'/api/persone/{tenant}',
                f'/api/plaud/cloud/status/{tenant}'):
        assert env.http.get(url, headers=TK).status_code == 403, url


def test_render_vince_anche_se_cambia_dopo(env, monkeypatch):
    """Render cambiato DOPO la console: vince Render, senza chiedere."""
    import anagrafica
    dati = anagrafica.leggi()
    dati['clienti']['roberto'] = {'nome': 'Doppione', 'tipo': 'azienda', 'archivio': 'covolo',
                                  'modalita': 'aziendale', 'chiave_impronta': anagrafica._impronta('falsa')}
    dati['persone']['p1'] = {'tenant': 'roberto', 'nome': 'X', 'ruolo': 'membro', 'telefono': '393330000001'}
    dati['persone']['p2'] = {'tenant': 'roberto', 'nome': 'Y', 'ruolo': 'membro', 'telefono': '393475550000'}
    anagrafica._salva(dati)
    assert env.http.get('/api/documents/roberto/spazi', headers={'X-Applaudi-Tenant-Key': 'falsa'}).status_code == 401
    assert env.http.get('/api/documents/roberto', headers=H('roberto')).json()['knowledge_client'] == 'lago'
    # stesso numero su Render (-> lago) e nella console: vince Render
    monkeypatch.setenv('WHATSAPP_PHONE_BINDINGS_JSON', json.dumps({'393330000001': 'covolo'}))
    env.llm.calls.clear()
    assert wa(env, '393330000001', 'Quanto costa il miscelatore?')
    assert env.llm.calls[-1]['cliente'] == 'covolo'


# ---------------------------------------------------------------- aziende senza catalogo
def carica_doc(env, tenant, chiave, testo, nome='nota.txt'):
    import io
    TK = {'X-Applaudi-Tenant-Key': chiave}
    r = env.http.post(f'/api/documents/{tenant}/upload?spazio=azienda',
                      files={'file': (nome, io.BytesIO(testo.encode()), 'text/plain')}, headers=TK)
    assert r.status_code == 200, r.text
    r = env.http.post(f"/api/documents/{tenant}/{r.json()['document']['id']}/scheda?spazio=azienda",
                      json={'tipo': 'altro'}, headers=TK)
    assert r.status_code == 200, r.text


def test_azienda_solo_documenti(env):
    x = env.http.post('/api/gestore/clienti', json={'nome': 'Thermogen', 'tipo': 'azienda', 'modalita': 'aziendale',
                                                   'archivio': ''}, headers=G()).json()
    tenant, chiave = x['cliente']['id'], x['chiave']
    assert x['cliente']['solo_documenti'] and x['cliente']['archivio'] == tenant
    TK = {'X-Applaudi-Tenant-Key': chiave}
    assert env.http.get(f'/api/documents/{tenant}/spazi', headers=TK).json()['catalogo'] is False
    assert env.http.get(f'/api/documents/roberto/spazi', headers=H('roberto')).json()['catalogo'] is True
    carica_doc(env, tenant, chiave, 'Manutenzione biennale caldaia: tariffa 110 euro, libretto incluso.')
    env.llm.calls.clear()
    r = env.http.post(f'/api/spazio/{tenant}/chiedi_azienda', json={'domanda': 'quanto costa la manutenzione caldaia?'},
                      headers=TK)
    assert r.status_code == 200 and r.json()['fonti']
    testi = '\n'.join(c['text'] for c in env.llm.calls)
    assert '110 euro' in testi and 'Thermogen'.upper() in testi.upper()
    assert 'N.O.W.' not in testi                                   # niente catalogo LAGO
    # chi ha un catalogo non usa questa strada
    assert env.http.post('/api/spazio/roberto/chiedi_azienda', json={'domanda': 'x'},
                         headers=H('roberto')).status_code == 409
    # su WhatsApp la persona di Thermogen riceve le risposte del SUO archivio
    env.http.post(f'/api/gestore/clienti/{tenant}/persone', json={'nome': 'Luca', 'telefono': '393478880000'},
                  headers=G())
    env.llm.calls.clear()
    assert wa(env, '393478880000', 'quanto costa la manutenzione caldaia?')
    assert '110 euro' in env.llm.calls[-1]['text']
    # e il suo archivio non e' quello di nessun altro
    assert env.http.get('/api/documents/roberto', headers=H('roberto')).json()['knowledge_client'] == 'lago'


# ---------------------------------------------------------------- consumi
class ConUso:
    def __init__(self, core):
        self.calls = []
        fake = self

        class _C:
            @staticmethod
            def create(**kw):
                fake.calls.append({'cliente': core.CLIENTE, 'text': '\n'.join(m['content'] for m in kw['messages'])})
                msg = types.SimpleNamespace(content='Risposta di prova.')
                uso = types.SimpleNamespace(prompt_tokens=1000, completion_tokens=200)
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], usage=uso)
        self.chat = types.SimpleNamespace(completions=_C())


def test_consumi_per_cliente_e_costo(env, monkeypatch):
    monkeypatch.setattr(env.core, 'client', ConUso(env.core))
    x = env.http.post('/api/gestore/clienti', json={'nome': 'Thermogen', 'tipo': 'azienda', 'archivio': ''},
                      headers=G()).json()
    tenant, chiave = x['cliente']['id'], x['chiave']
    carica_doc(env, tenant, chiave, 'Manutenzione biennale caldaia: tariffa 110 euro.')
    env.http.post(f'/api/spazio/{tenant}/chiedi_azienda', json={'domanda': 'manutenzione caldaia?'},
                  headers={'X-Applaudi-Tenant-Key': chiave})
    env.core.ANSWER_CACHE.clear()
    env.http.post('/api/ask', json={'question': '/catalogo Quanto costa il 211605?', 'cliente': 'lago'})
    assert env.http.post('/api/gestore/tariffe', json={'ingresso_eur_milione': '0,25', 'uscita_eur_milione': '1'},
                         headers=G()).status_code == 200
    stato = env.http.get('/api/gestore/stato', headers=G()).json()
    th = next(c for c in stato['clienti'] if c['id'] == tenant)['consumi'][0]
    assert th['domande_web'] == 1 and th['token_ingresso'] == 1000 and th['token_uscita'] == 200
    assert th['costo_eur'] == round((1000 * 0.25 + 200 * 1) / 1e6, 4)
    ro = next(c for c in stato['clienti'] if c['id'] == 'roberto')['consumi'][0]
    assert ro['domande_web'] == 1 and ro['token_ingresso'] >= 1000          # il catalogo LAGO va a roberto
    assert env.http.post('/api/gestore/tariffe', json={'ingresso_eur_milione': 'tanto'},
                         headers=G()).status_code == 400


def test_analisi_plaud_di_azienda_senza_catalogo_non_usa_lago(env, monkeypatch):
    from test_whatsapp_core import FakeLLM
    x = env.http.post('/api/gestore/clienti', json={'nome': 'Thermogen', 'tipo': 'azienda', 'archivio': ''},
                      headers=G()).json()
    tenant, chiave = x['cliente']['id'], x['chiave']
    analisi = json.dumps({'tipo_comunicazione': 'Telefonata', 'richiesta_cliente': 'prezzo mobile',
                          'instradamento': 'DOCUMENTI_AZIENDALI', 'rilevanza_catalogo': True,
                          'domanda_catalogo': 'Quanto costa il 211605?'})
    llm = FakeLLM(env.core, answer=analisi)
    monkeypatch.setattr(env.core, 'client', llm)

    async def bundle(slot, rid):
        return {'transcript': 'Quanto costa il mobile 211605?', 'note': '', 'detail': {'name': 'Tel'}}
    monkeypatch.setattr(env.core, 'get_recording_bundle', bundle)
    monkeypatch.setattr(env.core, 'plaud_slot', lambda t, f: t)
    r = env.http.post(f'/api/plaud/cloud/analyze/{tenant}/abc', headers={'X-Applaudi-Tenant-Key': chiave})
    assert r.status_code == 200, r.text
    assert r.json()['catalog_answer'] is None
    assert not any('RICHIESTA ORIGINALE' in c['text'] for c in llm.calls)    # LAGO mai interrogato
    consumi = next(c for c in env.http.get('/api/gestore/stato', headers=G()).json()['clienti']
                   if c['id'] == tenant)['consumi']
    assert consumi[0]['domande_plaud'] == 1


# ---------------------------------------------------------------- credito
def test_credito_scala_si_vede_e_si_ferma_a_zero(env, monkeypatch):
    monkeypatch.setattr(env.core, 'client', ConUso(env.core))                 # 1000 + 200 token a chiamata
    x = env.http.post('/api/gestore/clienti', json={'nome': 'Thermogen', 'tipo': 'azienda', 'archivio': ''},
                      headers=G()).json()
    tenant, chiave = x['cliente']['id'], x['chiave']
    TK = {'X-Applaudi-Tenant-Key': chiave}
    carica_doc(env, tenant, chiave, 'Manutenzione biennale caldaia: tariffa 110 euro.')
    domanda = lambda: env.http.post(f'/api/spazio/{tenant}/chiedi_azienda',
                                    json={'domanda': 'manutenzione caldaia?'}, headers=TK).json()
    # tariffe: 1 €/milione in ingresso, 5 in uscita, ricarico 2 -> (1000*1 + 200*5)/1e6*2 = 0,004 € a domanda
    env.http.post('/api/gestore/tariffe', json={'ingresso_eur_milione': '1', 'uscita_eur_milione': '5',
                                                'ricarico': '2'}, headers=G())
    # senza credito impostato: nessun limite, e nessun credito nasce da solo
    assert domanda()['fonti'] and domanda()['fonti']
    assert env.http.get(f'/api/credito/{tenant}', headers=TK).json()['credito'] is None
    assert env.http.post(f'/api/gestore/clienti/{tenant}/ricarica', json={'importo_eur': '0,01'},
                         headers=G()).status_code == 200
    c = env.http.get(f'/api/credito/{tenant}', headers=TK).json()['credito']
    assert c['percentuale'] == 100 and c['residuo_eur'] == 0.01
    domanda()
    c = env.http.get(f'/api/credito/{tenant}', headers=TK).json()['credito']
    assert c['residuo_eur'] == round(0.01 - 0.004, 4) and c['percentuale'] == 60
    domanda(); domanda()                                                      # va sotto zero
    env.llm_calls = len(env.core.client.calls)
    r = domanda()
    assert 'credito' in r['risposta'].lower() and r['fonti'] == []
    assert len(env.core.client.calls) == env.llm_calls                         # il modello non viene pagato
    # su WhatsApp lo stesso
    env.http.post(f'/api/gestore/clienti/{tenant}/persone', json={'nome': 'Luca', 'telefono': '393478880000'},
                  headers=G())
    assert 'credito' in wa(env, '393478880000', 'manutenzione caldaia?').lower()
    # blocco spento: si risponde anche a credito finito
    env.http.post(f'/api/gestore/clienti/{tenant}/blocco', json={'blocca_a_zero': False}, headers=G())
    assert domanda()['fonti']
    # ricarica: l'anello riparte da 100%
    env.http.post(f'/api/gestore/clienti/{tenant}/ricarica', json={'importo_eur': '5'}, headers=G())
    assert env.http.get(f'/api/credito/{tenant}', headers=TK).json()['credito']['percentuale'] == 100
    righe = env.http.get('/api/gestore/registro', headers=G()).json()['righe']
    assert [r['azione'] for r in righe if r['azione'] == 'ricarica'] == ['ricarica', 'ricarica']


def test_credito_del_catalogo_ferma_anche_il_sito(env, monkeypatch):
    monkeypatch.setattr(env.core, 'client', ConUso(env.core))
    env.http.post('/api/gestore/tariffe', json={'ingresso_eur_milione': '1000', 'uscita_eur_milione': '1000'},
                  headers=G())
    env.http.post('/api/gestore/clienti/roberto/ricarica', json={'importo_eur': '0,001'}, headers=G())
    env.core.ANSWER_CACHE.clear()
    env.http.post('/api/ask', json={'question': '/catalogo Quanto costa il 211605?', 'cliente': 'lago'})
    env.core.ANSWER_CACHE.clear()
    r = env.http.post('/api/ask', json={'question': '/catalogo Quanto costa il 211606?', 'cliente': 'lago'}).json()
    assert r['source'] == 'credito_esaurito'
    # il credito di un cliente non tocca gli altri
    x = crea(env).json()
    assert env.http.get(f"/api/credito/{x['cliente']['id']}",
                        headers={'X-Applaudi-Tenant-Key': x['chiave']}).json()['credito'] is None


# ---------------------------------------------------------------- invio dell'intervento al tecnico
def tecnico(env):
    r = env.http.post('/api/persone/roberto', json={'nome': 'Luca Tecnico', 'ruolo': 'membro',
                                                    'telefono': '393475551234'}, headers=H('roberto'))
    return r.json()['persona']['id']


INTERVENTO = {'cliente': 'Studio Ferri Architetti', 'quando': 'mercoledì 22/10 ore 16:00',
              'indirizzo': 'Via Roma 12, Bassano del Grappa', 'note': 'puntualità tassativa'}


def test_invio_intervento_con_navigatore(env):
    pid = tecnico(env)
    env.meta.sent.clear()
    r = env.http.post(f'/api/persone/roberto/{pid}/invia', json=INTERVENTO, headers=H('roberto'))
    assert r.status_code == 200 and r.json()['modo'] == 'testo'
    inviato = env.meta.sent[-1]
    assert inviato['to'] == '393475551234' and inviato['type'] == 'text'
    corpo = inviato['text']['body']
    assert 'Via Roma 12, Bassano del Grappa' in corpo and 'Studio Ferri Architetti' in corpo
    assert 'https://www.google.com/maps/search/?api=1&query=Via+Roma+12%2C+Bassano+del+Grappa' in corpo
    righe = env.http.get('/api/gestore/registro', headers=G()).json()['righe']
    assert righe[0]['azione'] == 'messaggio_inviato' and righe[0]['nome'] == 'Luca Tecnico'
    ro = next(c for c in env.http.get('/api/gestore/stato', headers=G()).json()['clienti'] if c['id'] == 'roberto')
    assert ro['consumi'][0]['messaggi_testo'] == 1
    # controlli
    assert env.http.post(f'/api/persone/roberto/{pid}/invia', json=dict(INTERVENTO, indirizzo='via'),
                         headers=H('roberto')).status_code == 400
    altro = crea(env).json()
    assert env.http.post(f'/api/persone/{altro["cliente"]["id"]}/{pid}/invia', json=INTERVENTO,
                         headers={'X-Applaudi-Tenant-Key': altro['chiave']}).status_code == 404
    env.http.post(f'/api/persone/roberto/{pid}', json={'sospeso': True}, headers=H('roberto'))
    assert env.http.post(f'/api/persone/roberto/{pid}/invia', json=INTERVENTO,
                         headers=H('roberto')).status_code == 409


def test_oltre_24_ore_serve_il_modello(env, monkeypatch):
    import httpx
    pid = tecnico(env)
    inviati = []

    def meta(request):
        body = json.loads(request.content)
        inviati.append(body)
        if body.get('type') == 'text':
            return httpx.Response(400, json={'error': {'code': 131047, 'message': 'Re-engagement message'}})
        return httpx.Response(200, json={'messages': [{'id': 'wamid.t'}]})
    real = httpx.AsyncClient
    monkeypatch.setattr(env.gw, 'http_client_factory', lambda **kw: real(transport=httpx.MockTransport(meta), **kw))
    r = env.http.post(f'/api/persone/roberto/{pid}/invia', json=INTERVENTO, headers=H('roberto'))
    assert r.status_code == 409 and 'modello' in r.json()['detail']
    monkeypatch.setenv('WHATSAPP_TEMPLATE_INTERVENTO', 'intervento_tecnico')
    r = env.http.post(f'/api/persone/roberto/{pid}/invia', json=INTERVENTO, headers=H('roberto'))
    assert r.status_code == 200 and r.json()['modo'] == 'modello'
    t = inviati[-1]
    assert t['type'] == 'template' and t['template']['name'] == 'intervento_tecnico'
    assert t['template']['language']['code'] == 'it'
    par = [p['text'] for p in t['template']['components'][0]['parameters']]
    assert par[0] == 'mercoledì 22/10 ore 16:00' and par[2] == 'Via Roma 12, Bassano del Grappa'
    assert par[3].startswith('https://www.google.com/maps/') and len(par) == 5


def test_errore_meta_diverso_non_usa_il_modello(env, monkeypatch):
    import httpx
    pid = tecnico(env)
    monkeypatch.setenv('WHATSAPP_TEMPLATE_INTERVENTO', 'intervento_tecnico')
    inviati = []

    def meta(request):
        inviati.append(json.loads(request.content))
        return httpx.Response(400, json={'error': {'code': 100, 'message': 'parametro non valido'}})
    real = httpx.AsyncClient
    monkeypatch.setattr(env.gw, 'http_client_factory', lambda **kw: real(transport=httpx.MockTransport(meta), **kw))
    r = env.http.post(f'/api/persone/roberto/{pid}/invia', json=INTERVENTO, headers=H('roberto'))
    assert r.status_code == 502 and [m['type'] for m in inviati] == ['text']      # nessun modello a pagamento


def test_confronta_si_attiva_dal_gestore(env):
    TK = {'X-Applaudi-Tenant-Key': KEYS['roberto']}
    assert env.http.get('/api/confronta/roberto/profili', headers=TK).json()['profili'] == []
    stato = env.http.get('/api/gestore/stato', headers=G()).json()
    assert 'vita_tcm' in [p['nome'] for p in stato['profili_confronta']]
    url = '/api/gestore/clienti/roberto/confronta'          # vale anche per un cliente di Render
    assert env.http.post(url, json={'profili': ['inventato']}, headers=G()).status_code == 400
    assert env.http.post(url, json={'profili': ['vita_tcm']}, headers=G('sbagliata-ma-lunga')).status_code == 401
    assert env.http.post(url, json={'profili': ['vita_tcm']}, headers=G()).json()['confronta'] == ['vita_tcm']
    profili = env.http.get('/api/confronta/roberto/profili', headers=TK).json()['profili']
    assert [p['nome'] for p in profili] == ['vita_tcm']
    roberto = next(c for c in env.http.get('/api/gestore/stato', headers=G()).json()['clienti'] if c['id'] == 'roberto')
    assert roberto['confronta'] == ['vita_tcm']
    assert env.http.post(url, json={'profili': []}, headers=G()).json()['confronta'] == []
    assert env.http.get('/api/confronta/roberto/profili', headers=TK).json()['profili'] == []
