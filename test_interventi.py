"""Collaudo dell'INTERVENTO PREPARATO DALLA TELEFONATA (interventi.py + app.py).

Cosa deve restare vero:
- il cliente si trova nell'anagrafica con regole dichiarate e si dice quanto e' sicuro;
- l'indirizzo viene SEMPRE dall'anagrafica, mai dalla trascrizione;
- con un dubbio il sistema chiede di scegliere, non indovina;
- il tecnico e' una Persona registrata; il messaggio non parte da solo.

Esecuzione:  python -m pytest -q test_interventi.py
"""
import io
import json
import pytest

import interventi
from test_anagrafica import env as env_anagrafica, G  # noqa: F401
from test_spazio_privato import H, env as env_privato  # noqa: F401
from test_whatsapp_core import FakeLLM, core  # noqa: F401

# anagrafica di prova (stessa forma dell'Excel «Anagrafica clienti Thermogen»): dati inventati
ANAGRAFICA = '''Codice cliente;Cliente;Referente;Tipo;Indirizzo;CAP;Comune;Prov.;Telefono;WhatsApp;Accesso e note;Tecnico di zona
CL-0001;Studio Ferri Architetti;Marta Ferri;Azienda;Via Roma 12;36061;Bassano del Grappa;VI;0424 000 101;39 340 000 0101;Terzo piano senza ascensore, scale strette;Luca Bonato
CL-0002;Rossi Mario;Mario Rossi;Privato;Via Jacopo da Ponte 8;36061;Bassano del Grappa;VI;0424 000 102;39 340 000 0102;PUNTUALITÀ TASSATIVA;Luca Bonato
CL-0003;Panificio Dal Molin snc;Giorgio Dal Molin;Azienda;Viale delle Fosse 21;36061;Bassano del Grappa;VI;0424 000 103;39 340 000 0103;Accesso dal retro;Luca Bonato
CL-0005;Hotel Al Ponte srl;Paolo Zanon;Azienda;Via Vittorelli 30;36061;Bassano del Grappa;VI;0424 000 105;39 340 000 0105;Chiedere della reception;Sara Fabris
CL-0007;Zen Giulia;Giulia Zen;Privato;Via Marinali 17;36061;Bassano del Grappa;VI;0424 000 107;39 340 000 0107;Manutenzione scaduta;Sara Fabris
CL-0013;Fontana Luisa;Luisa Fontana;Privato;Via Roma 45;36022;Cassola;VI;0424 000 113;39 340 000 0113;Secondo piano;Matteo Gheno
'''


@pytest.fixture
def righe(tmp_path):
    import documenti_aziendali as docs
    f = tmp_path / 'anagrafica.csv'
    f.write_text(ANAGRAFICA, encoding='utf-8')
    parti, _ = docs.extract(str(f), '.csv')
    return interventi.righe_anagrafica(parti)


def test_righe_lette_con_le_colonne_giuste(righe):
    assert len(righe) == 6
    rossi = next(r for r in righe if r['codice'] == 'CL-0002')
    assert rossi['indirizzo'] == 'Via Jacopo da Ponte 8' and rossi['tecnico'] == 'Luca Bonato'
    assert interventi.indirizzo_completo(rossi) == 'Via Jacopo da Ponte 8, 36061 Bassano del Grappa (VI)'


@pytest.mark.parametrize('nome,telefono,indirizzo,stato,codice', [
    ('Mario Rossi', '', '', 'certo', 'CL-0002'),
    ('Rossi Mario', '', '', 'certo', 'CL-0002'),                    # ordine diverso
    ('', '+39 340 000 0105', '', 'certo', 'CL-0005'),                # per telefono
    ('Rossi', '', '', 'da_scegliere', None),                         # solo il cognome: si chiede
    ('Ferri', '', 'via Roma 12', 'certo', 'CL-0001'),                # cognome + via detta confermano
    ('Giuseppe Verdi', '', '', 'non_trovato', None),
    ('', '', '', 'non_trovato', None),
])
def test_riconoscimento_del_cliente(righe, nome, telefono, indirizzo, stato, codice):
    esito = interventi.trova_cliente(righe, nome, telefono, indirizzo)
    assert esito['stato'] == stato, esito
    if codice:
        assert esito['candidati'][0]['codice'] == codice


def test_telefono_di_due_clienti_si_sceglie(righe):
    doppio = righe + [dict(righe[0], codice='CL-9998', nome='Altro Studio')]
    esito = interventi.trova_cliente(doppio, '', '0424 000 101')
    assert esito['stato'] == 'da_scegliere' and len(esito['candidati']) == 2


def test_omonimi_si_sceglie(righe):
    doppio = righe + [dict(righe[1], codice='CL-9999', indirizzo='Via Altra 1')]
    esito = interventi.trova_cliente(doppio, 'Mario Rossi')
    assert esito['stato'] == 'da_scegliere' and len(esito['candidati']) == 2


# ---------------------------------------------------------------- dalla telefonata, sul core vero
@pytest.fixture
def env(env_anagrafica):  # noqa: F811
    return env_anagrafica


def analizza(env, monkeypatch, intervento):
    analisi = json.dumps({'tipo_comunicazione': 'Telefonata', 'richiesta_cliente': 'manutenzione',
                          'instradamento': 'CONVERSAZIONE', 'rilevanza_catalogo': False,
                          'intervento': intervento})
    monkeypatch.setattr(env.core, 'client', FakeLLM(env.core, answer=analisi))

    async def bundle(slot, rid):
        return {'transcript': 'Telefonata con il cliente.', 'note': '', 'detail': {'name': 'Tel'}}
    monkeypatch.setattr(env.core, 'get_recording_bundle', bundle)
    monkeypatch.setattr(env.core, 'plaud_slot', lambda t, f: t)
    r = env.http.post('/api/plaud/cloud/analyze/roberto/abc?data=2026-10-17T17:13:00', headers=H('roberto'))
    assert r.status_code == 200, r.text
    return r.json()


def carica_anagrafica(env):
    r = env.http.post('/api/documents/roberto/upload?spazio=azienda',
                      files={'file': ('Anagrafica clienti.csv', io.BytesIO(ANAGRAFICA.encode()), 'text/csv')},
                      headers=H('roberto'))
    assert r.status_code == 200, r.text
    assert env.http.post(f"/api/documents/roberto/{r.json()['document']['id']}/scheda?spazio=azienda",
                         json={'tipo': 'altro'}, headers=H('roberto')).status_code == 200


INTERVENTO = {'fissato': True, 'quando': 'mercoledì prossimo ore 16:00 (22/10/2026)', 'quando_relativo': True,
              'attivita': 'manutenzione biennale e libretto', 'cliente_nome': 'Mario Rossi',
              'cliente_telefono': '', 'indirizzo_detto': 'Via Sbagliata 99', 'note': 'puntualità tassativa'}


def test_intervento_pronto_con_indirizzo_dall_anagrafica(env, monkeypatch):
    carica_anagrafica(env)
    luca = env.http.post('/api/persone/roberto', json={'nome': 'Luca Bonato', 'telefono': '393475550201'},
                         headers=H('roberto')).json()['persona']['id']
    env.meta.sent.clear()
    iv = analizza(env, monkeypatch, INTERVENTO)['intervento']
    assert iv['esito_cliente'] == 'certo' and iv['cliente'] == 'Rossi Mario'
    assert iv['indirizzo'] == 'Via Jacopo da Ponte 8, 36061 Bassano del Grappa (VI)'   # mai «Via Sbagliata»
    assert iv['tecnico'] == {'id': luca, 'nome': 'Luca Bonato'}
    assert iv['note'].lower().count('puntualità tassativa') == 1               # detta e scritta: una volta
    assert iv['quando_da_confermare'] is True
    assert env.meta.sent == []                                   # niente parte da solo


def test_tecnico_non_registrato_e_cliente_dubbio(env, monkeypatch):
    carica_anagrafica(env)
    iv = analizza(env, monkeypatch, dict(INTERVENTO, cliente_nome='Rossi'))['intervento']
    assert iv['esito_cliente'] == 'da_scegliere' and iv['indirizzo'] == '' and iv['candidati']
    env.http.post('/api/persone/roberto', json={'nome': 'Sara Fabris', 'telefono': '393475550202'},
                  headers=H('roberto'))
    iv = analizza(env, monkeypatch, INTERVENTO)['intervento']
    assert iv['tecnico'] is None and iv['tecnico_zona'] == 'Luca Bonato'      # va aggiunto alle Persone
    assert [p['nome'] for p in iv['persone']] == ['Sara Fabris']             # si puo' scegliere un altro


def test_senza_intervento_o_senza_anagrafica(env, monkeypatch):
    assert analizza(env, monkeypatch, {'fissato': False})['intervento'] is None
    iv = analizza(env, monkeypatch, INTERVENTO)['intervento']
    assert iv['anagrafica_trovata'] is False and iv['esito_cliente'] == 'non_trovato'
