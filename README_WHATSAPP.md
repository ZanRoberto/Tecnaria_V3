# A’PLAUD’I — WhatsApp sul CORE (ramo `whatsapp-test`)

## Cosa c'è in questo ramo
È `main` (il CORE LIVE) più tre aggiunte, **nessuna riga del CORE tolta o cambiata**:

| File | Cosa fa |
|---|---|
| `app.py` | Il CORE di `main` + 3 blocchi: import del gateway, `include_router`, adattatore `_whatsapp_document_answer` |
| `whatsapp_gateway.py` | Webhook Meta: verifica, firma, numero→azienda, idempotenza, limiti, invio risposta |
| `test_whatsapp_core.py` | 20 test sul CORE vero (indice LAGO reale + un secondo cliente sintetico) |

Il gateway è **spento** finché `WHATSAPP_GATEWAY_ENABLED` non vale `true`: senza quella variabile il servizio si comporta come `main`. Unirlo un giorno a `main` senza variabili WhatsApp non cambia nulla nel LIVE.

## Il percorso di un messaggio
numero mittente → azienda (`WHATSAPP_PHONE_BINDINGS_JSON`, solo lato server) → `attiva_cliente(azienda)` → `api_ask('/catalogo …')`, cioè Narratore + Super-Risponditore originali → risposta al mittente → cliente precedente rimesso acceso.

- Numero non censito: nessuna risposta, nessun accesso.
- Il testo non può cambiare azienda: `/lago`, `cliente=…` e simili vengono tolti, la modalità documentale la impone il server.
- Il messaggio di un'azienda che il CORE non conosce rende non valida **tutta** la configurazione (503): niente ripiego silenzioso su LAGO.
- Se il motore è fermo (credito AI finito, ecc.) il CORE sul web scrive "MOTORE FERMO — causa". Su WhatsApp arriva solo "riprova più tardi"; la causa resta nel log.
- Durante un collaudo (`/collaudo`) WhatsApp risponde "servizio occupato".

## Variabili d'ambiente (servizio di STAGING)
Valori da inserire **solo nel pannello Render**, mai su GitHub né in chat.

| Variabile | Valore |
|---|---|
| `WHATSAPP_GATEWAY_ENABLED` | `true` |
| `WHATSAPP_REPLY_MODE` | `ack` per la prima prova (risposta fissa), poi `documents` |
| `WHATSAPP_VERIFY_TOKEN` | una parola lunga inventata da te; la stessa va scritta in Meta |
| `WHATSAPP_APP_SECRET` | Meta → app `aplaudi` → Impostazioni app → Di base → Chiave segreta |
| `WHATSAPP_ACCESS_TOKEN` | token WhatsApp (vedi nota sotto) |
| `WHATSAPP_PHONE_NUMBER_ID` | `1430116760175453` (numero test) |
| `WHATSAPP_INBOX_DB` | `/tmp/wa_inbox.sqlite3` |
| `WHATSAPP_PHONE_BINDINGS_JSON` | `{"39XXXXXXXXXX":"lago"}`: numero completo con prefisso, senza `+` né spazi |
| `WHATSAPP_MAX_MSG_PER_HOUR` | facoltativa, predefinito `30` |
| `WHATSAPP_GRAPH_VERSION` | facoltativa, predefinito `v23.0` |
| `DEEPSEEK_API_KEY` (e le altre del CORE) | le stesse del servizio LIVE, se si vuole `documents` |

Note:
- **Token**: quello generato in "API Setup" scade dopo circa 24 ore. Per una prova di un giorno va bene; per tenere acceso lo staging serve un token di un *System User* del portafoglio business.
- **Render Free**: il servizio si addormenta dopo un po' di inattività e al primo messaggio impiega decine di secondi a ripartire. Meta ritrasmette e il doppione viene scartato. Il disco si azzera a ogni riavvio: il registro in `/tmp` è temporaneo.
- In modalità sviluppo Meta consegna messaggi solo ai numeri destinatari verificati nella pagina "API Setup".

## Configurazione Meta
URL di callback: `https://<servizio-staging>.onrender.com/api/whatsapp/webhook`
Token di verifica: lo stesso di `WHATSAPP_VERIFY_TOKEN`. Campo da sottoscrivere: `messages`.

## Test
```
python -m pytest -q test_whatsapp_core.py
```
Esito di questa consegna: **20 passed** (Python 3.13, fastapi 0.115.0, starlette 0.38.6).
Simulati: **solo** il modello AI e l'API Meta in uscita. Non è una prova reale WhatsApp.
Il test d'isolamento `test_web_senza_cliente_non_vede_mai_l_azienda_whatsapp` è stato fatto girare anche contro il vecchio adattatore (thread + `asyncio.run`): **fallisce** (una domanda LAGO dal web calcolata con COVOLO acceso). Con l'adattatore nuovo passa.

## Rollback
Lo staging è un servizio separato dal ramo `whatsapp-test`: per tornare indietro si mette `WHATSAPP_GATEWAY_ENABLED=false`, oppure si cancella il servizio di staging. Il LIVE (`main`) non viene toccato.

## Cosa NON fa ancora (bloccanti dichiarati)
1. Nessuna prova reale col numero test Meta: va fatta sullo staging.
2. COVOLO non esiste come cliente nel repository (c'è solo `static/clienti/lago`). L'isolamento è dimostrato con un cliente sintetico. Per il COVOLO reale i documenti vanno indicizzati con la pipeline del CORE (`static/clienti/covolo/…`).
3. Supervisore ROBERTO su WhatsApp non implementato, per scelta: un numero di telefono non è un'autenticazione forte. Il numero di Roberto si lega a un'azienda come gli altri.
4. Ogni messaggio è a sé: niente "e in nero?" riferito alla domanda precedente.
5. Registro su SQLite: una sola istanza. Con più istanze serve Postgres.
