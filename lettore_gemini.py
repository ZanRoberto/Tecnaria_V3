"""Lettura dei documenti con Gemini: il modello VEDE le pagine (22ott2026).

Perche': un modello che legge solo il testo estratto dal PDF parte svantaggiato (colonne
mescolate, parole incollate, riquadri e titoli di sezione persi). Gemini riceve il PDF intero,
come lo riceve una persona, e restituisce la SCHEDA del documento per il profilo di mercato:
- le forme (tariffe) del prodotto,
- le garanzie accessorie / complementari (non sono il prodotto principale),
- se le esclusioni della copertura principale sono un ELENCO CHIUSO («coperto qualunque sia
  la causa» + elenco),
- i concetti del catalogo con valore, pagina e citazione.

Regole:
1. Si legge UNA volta per documento e per versione del profilo: la scheda resta salvata
   accanto al PDF (scheda_<profilo>_v<versione>.json) e non si rilegge piu'.
2. Il codice verifica ogni citazione sul testo del documento (confronto_catalogo._verifica):
   cio' che non torna si scarta, come sempre.
3. Senza GEMINI_API_KEY, o se Gemini non risponde, si torna alla lettura di prima.
Nessuna chiave nel codice: GEMINI_API_KEY e GEMINI_MODEL stanno su Render.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

VERSIONE_SCHEDA = 1
MODELLO_PREDEFINITO = "gemini-2.5-flash"
API = "https://generativelanguage.googleapis.com"
MAX_INLINE_BYTE = 8 * 1024 * 1024       # oltre si usa il caricamento dei file di Gemini (limite 20 MB a richiesta)
TEMPO_S = 240

# consumo: (cliente, token in ingresso, token in uscita); impostato da app.py. Il credito si
# scala con le tariffe del modello di tutti i giorni (DeepSeek): i token di Gemini si contano
# moltiplicati per il rapporto di prezzo (Gemini 2.5 Flash 0,30/2,50 $ contro DeepSeek
# 0,15/0,60 $ per milione, ottobre 2026), cambiabile su Render senza toccare il codice.
registra_consumo: Optional[Callable[[str, int, int], None]] = None


def _fattore(nome: str, predefinito: float) -> float:
    try:
        return float(os.getenv(nome, "") or predefinito)
    except ValueError:
        return predefinito


def attivo() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def modello() -> str:
    return os.getenv("GEMINI_MODEL", "").strip() or MODELLO_PREDEFINITO


ISTRUZIONI = """Sei {lettore}. Ti do un documento PDF ({documento}): leggilo TUTTO, pagina per pagina, guardando anche impaginazione, colonne, riquadri e titoli delle sezioni.
Restituisci SOLO questo JSON:
{{
 "forme": [{{"codice": "codice della forma o tariffa (es. 242), altrimenti un nome breve", "descrizione": "come la descrive il documento"}}],
 "garanzie_accessorie": [{{"nome": "nome della garanzia complementare/accessoria/facoltativa", "facoltativa": true, "pagine": [numeri]}}],
 "esclusioni_elenco_chiuso": {{"si_no": "si se la copertura principale vale qualunque sia la causa salvo un elenco di esclusioni, altrimenti no", "citazione": "frase copiata", "pagina": numero}},
 "concetti": [{{"id": "id del concetto", "valore": "risposta breve con numeri e unita' esatti", "numero": numero o null, "unita": "unita' del numero o null", "si_no": "si/no o null", "condizione": "variante a condizione, oppure vuoto", "forma": "codice della forma se vale solo per una forma, nome della garanzia accessoria se vale solo per quella, altrimenti vuoto", "citazione": "frase che lo prova COPIATA ESATTAMENTE dal documento (max 300 caratteri)", "pagina": numero della pagina del PDF (1 = prima pagina del file)}}]
}}
Regole:
1. Per ogni concetto rispondi alla DOMANDA, e solo a quella; leggi «non confondere con».
2. Rispondi per la copertura PRINCIPALE. Un valore che vale solo per una garanzia accessoria o facoltativa va con il nome di quella garanzia in «forma».
3. Se il prodotto ha piu' forme o tariffe con valori diversi, un oggetto per forma, con il codice in «forma».
4. Se le esclusioni della copertura principale sono un elenco chiuso, per un'esclusione del catalogo che NON e' nell'elenco rispondi «no» (non esclusa) citando la frase dell'elenco chiuso.
5. Un esempio numerico o una simulazione NON e' il valore del contratto: non riportarlo.
6. Se un concetto non c'e', non scriverlo. Non usare conoscenze generali.
7. Il «numero» e' nell'unita' del concetto; se il testo usa un'altra unita' converti il numero e lascia il «valore» come e' scritto.
{regole}
CONCETTI:
{concetti}"""


def _istruzioni(profilo: Dict[str, Any]) -> str:
    import confronto_catalogo as cc
    regole = "".join(f"{n}. {r}\n" for n, r in enumerate(profilo.get("regole_lettura") or [], 8))
    return ISTRUZIONI.format(lettore=cc._p(profilo, "lettore"), documento=cc._p(profilo, "documento"),
                             regole=regole, concetti=cc._elenco_concetti(profilo, False))


def _client():
    import httpx
    return httpx.Client(timeout=TEMPO_S)


MOTIVI = {
    400: "richiesta rifiutata da Gemini",
    401: "chiave GEMINI_API_KEY non valida",
    402: "credito Google esaurito o fatturazione non ancora attiva sul progetto",
    403: "chiave GEMINI_API_KEY senza permesso o API non abilitata sul progetto",
    404: "modello Gemini non trovato (GEMINI_MODEL)",
    413: "documento troppo grande per Gemini",
    429: "troppe richieste o limite di spesa raggiunto: riprovare piu' tardi",
}


class ErroreGemini(RuntimeError):
    """Errore di Gemini con un motivo leggibile. La chiave non compare mai: viaggia
    nell'intestazione x-goog-api-key, non nell'indirizzo (23ott2026: finiva nei log)."""

    def __init__(self, codice: int, dettaglio: str = ""):
        self.codice = codice
        self.motivo = MOTIVI.get(codice) or ("Gemini non disponibile" if codice >= 500 else "errore di Gemini")
        super().__init__(f"Gemini ha risposto {codice}: {self.motivo}" + (f" ({dettaglio})" if dettaglio else ""))


def _controlla(risposta: Any) -> None:
    if risposta.status_code < 400:
        return
    try:
        dettaglio = str(((risposta.json() or {}).get("error") or {}).get("message") or "")[:160]
    except ValueError:
        dettaglio = ""
    raise ErroreGemini(risposta.status_code, dettaglio)


def _parte_pdf(client: Any, percorso: str, chiave: str) -> Dict[str, Any]:
    dati = open(percorso, "rb").read()
    if len(dati) <= MAX_INLINE_BYTE:
        return {"inline_data": {"mime_type": "application/pdf", "data": base64.b64encode(dati).decode()}}
    # file grande: caricamento in due passi (File API), poi si usa il suo indirizzo
    inizio = client.post(f"{API}/upload/v1beta/files",
                         headers={"x-goog-api-key": chiave,
                                  "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                                  "X-Goog-Upload-Header-Content-Length": str(len(dati)),
                                  "X-Goog-Upload-Header-Content-Type": "application/pdf",
                                  "Content-Type": "application/json"},
                         json={"file": {"display_name": os.path.basename(percorso)[:100]}})
    _controlla(inizio)
    url = inizio.headers.get("x-goog-upload-url")
    if not url:
        raise RuntimeError("Gemini non ha dato l'indirizzo di caricamento")
    fatto = client.post(url, headers={"Content-Length": str(len(dati)), "X-Goog-Upload-Offset": "0",
                                      "X-Goog-Upload-Command": "upload, finalize"}, content=dati)
    _controlla(fatto)
    info = fatto.json().get("file") or {}
    for _ in range(30):
        if info.get("state") in (None, "ACTIVE"):
            break
        time.sleep(2)
        stato = client.get(f"{API}/v1beta/{info['name']}", headers={"x-goog-api-key": chiave})
        _controlla(stato)
        info = stato.json()
    return {"file_data": {"mime_type": "application/pdf", "file_uri": info["uri"]}}


def leggi_scheda(percorso: str, profilo: Dict[str, Any], cliente: str = "") -> Dict[str, Any]:
    """Una chiamata a Gemini: la scheda del documento (non ancora verificata)."""
    chiave = os.getenv("GEMINI_API_KEY", "").strip()
    if not chiave:
        raise RuntimeError("GEMINI_API_KEY non impostata")
    with _client() as client:
        corpo = {"contents": [{"role": "user", "parts": [_parte_pdf(client, percorso, chiave),
                                                         {"text": _istruzioni(profilo)}]}],
                 "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                      "maxOutputTokens": 32000}}
        r = client.post(f"{API}/v1beta/models/{modello()}:generateContent",
                        headers={"x-goog-api-key": chiave}, json=corpo)
        _controlla(r)
        dati = r.json()
    uso = dati.get("usageMetadata") or {}
    if registra_consumo and cliente:
        try:
            registra_consumo(cliente,
                             round(int(uso.get("promptTokenCount") or 0) * _fattore("GEMINI_FATTORE_INGRESSO", 2.0)),
                             round((int(uso.get("candidatesTokenCount") or 0) + int(uso.get("thoughtsTokenCount") or 0))
                                   * _fattore("GEMINI_FATTORE_USCITA", 4.2)))
        except Exception as error:                      # il conteggio non deve fermare la lettura
            print(f"[GEMINI] consumo non registrato: {error}")
    testo = "".join(p.get("text", "") for c in dati.get("candidates") or []
                    for p in (c.get("content") or {}).get("parts") or [])
    scheda = _json(testo)
    if not isinstance(scheda, dict):
        raise RuntimeError("Gemini non ha restituito una scheda leggibile")
    scheda["_modello"] = modello()
    scheda["_token"] = {"ingresso": uso.get("promptTokenCount"), "uscita": uso.get("candidatesTokenCount")}
    return scheda


def _json(testo: str) -> Any:
    testo = (testo or "").strip()
    if testo.startswith("```"):
        testo = testo.strip("`")
        testo = testo[testo.find("{"):]
    try:
        return json.loads(testo)
    except ValueError:
        a, b = testo.find("{"), testo.rfind("}")
        if a >= 0 and b > a:
            try:
                return json.loads(testo[a:b + 1])
            except ValueError:
                return None
    return None


def scheda_salvata(percorso: str, profilo: Dict[str, Any], cliente: str = "") -> Dict[str, Any]:
    """La scheda di un documento: dal disco se gia' letta con questo profilo, altrimenti da Gemini
    (una volta sola)."""
    cache = os.path.join(os.path.dirname(percorso),
                         f"scheda_{profilo['nome']}_v{profilo.get('versione', 1)}_{VERSIONE_SCHEDA}.json")
    if os.path.isfile(cache):
        try:
            with open(cache, encoding="utf-8") as stream:
                return json.load(stream)
        except (OSError, ValueError):
            pass
    scheda = leggi_scheda(percorso, profilo, cliente)
    tmp = cache + ".tmp"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(scheda, stream, ensure_ascii=False)
    os.replace(tmp, cache)
    return scheda


def lettura_da_scheda(scheda: Dict[str, Any], fasc: Dict[str, Any], documento_id: str) -> List[Dict[str, Any]]:
    """I concetti della scheda nel formato della lettura di sempre: la pagina del PDF diventa
    l'id della pagina del fascicolo (A1, B12...)."""
    titolo = next((p["documento"] for p in fasc["pagine"] if p.get("documento_id") == documento_id), None)
    per_numero: Dict[int, str] = {}
    for p in fasc["pagine"]:
        if titolo is not None and p["documento"] != titolo:
            continue
        try:
            numero = int(str(p["label"]).split()[1])
        except (IndexError, ValueError):
            continue
        per_numero.setdefault(numero, p["id"])
    out = []
    for c in scheda.get("concetti") or []:
        if not isinstance(c, dict):
            continue
        try:
            numero = int(str(c.get("pagina")).strip("[] p"))
        except (TypeError, ValueError):
            numero = 0
        out.append({**c, "pagina": per_numero.get(numero, "")})
    return out


# ------------------------------------------------------------------ confronto diretto (23ott2026)
# Prova del 23ott2026: una chat con i due PDF ha fatto meglio della lettura documento per
# documento (scelta della tariffa 242, garanzie facoltative, esempio da 300.000 riconosciuto).
# Il motivo: vede i due documenti INSIEME e sceglie da sola la forma confrontabile. Qui si fa
# lo stesso, in una chiamata sola; il codice poi verifica ogni citazione sulla pagina
# (confronto_catalogo._verifica), come sempre: cio' che non torna si scarta.
VERSIONE_CONFRONTO = 2

ISTRUZIONI_CONFRONTO = """Sei {lettore}. Ti do i documenti di due proposte da confrontare: A ({file_a}) e B ({file_b}). Leggili TUTTI, pagina per pagina, guardando anche impaginazione, tabelle, riquadri e appendici.
PASSO 1 - FORMA. Se un lato ha piu' forme o tariffe (es. capitale costante o decrescente, premio annuo o unico), scegli quella confrontabile con l'altro lato (stesso andamento del capitale e stesso tipo di premio) e dillo. Tutti i valori di quel lato sono poi quelli della forma scelta.
PASSO 2 - CONCETTI. Per ogni concetto dell'elenco, il valore di A e quello di B per la copertura PRINCIPALE della forma scelta.
Restituisci SOLO questo JSON:
{{
 "forma_a": {{"codice": "codice o nome breve", "descrizione": "come la descrive il documento", "ha_piu_forme": true/false, "perche": "perche' e' la forma confrontabile"}},
 "forma_b": {{"codice": "...", "descrizione": "...", "ha_piu_forme": true/false, "perche": "..."}},
 "concetti": [{{"id": "id del concetto",
   "A": {{"valore": "risposta breve con numeri e unita' come scritti, SENZA la variante", "numero": numero o null, "unita": "unita' del numero o null", "si_no": "si/no o null", "condizione": "variante a condizione, oppure vuoto", "solo_accessoria": false, "nome_accessoria": "", "compresa_nel_premio": true/false/null, "esempio": false, "citazione": "frase COPIATA ESATTAMENTE dal documento che prova il valore (max 300 caratteri)", "documento": "A1", "pagina": numero della pagina del PDF (1 = prima pagina del file)}} oppure null,
   "B": {{ ...stessi campi, "documento": "B1"... }} oppure null}}],
 "altre_differenze": [{{"voce": "nome breve della differenza", "perche": "perche' conta per {per_chi}, una frase",
   "A": {{"valore": "...", "citazione": "frase copiata", "documento": "A1", "pagina": numero}} oppure null,
   "B": {{"valore": "...", "citazione": "frase copiata", "documento": "B1", "pagina": numero}} oppure null}}],
 "trappole": [{{"testo": "una trappola per {per_chi} o un falso confronto, una o due frasi"}}],
 "azioni": [{{"testo": "cosa deve verificare o chiedere {utente}"}}],
 "sintesi": "tre o quattro frasi"
}}
Regole:
1. Rispondi alla DOMANDA del concetto, e solo a quella; leggi «non confondere con».
2. Un valore che vale solo per una garanzia complementare, accessoria o facoltativa NON e' della copertura principale: «solo_accessoria»: true e il suo nome in «nome_accessoria». Una garanzia facoltativa non e' «compresa nel premio».
3. Se le esclusioni della copertura principale sono un elenco chiuso («coperto qualunque sia la causa» salvo un elenco), per un'esclusione che NON e' nell'elenco rispondi «no» (non esclusa) citando la frase dell'elenco chiuso.
4. Un esempio numerico o una simulazione NON e' il valore del contratto: se l'unico numero e' un esempio, metti «esempio»: true. Campi non compilati (XXX) = valore assente: null.
5. Se un lato non dice nulla sul concetto, quel lato e' null. Non usare conoscenze generali.
6. Il «numero» e' nell'unita' del concetto; se il testo usa un'altra unita' converti il numero e lascia il «valore» come e' scritto.
7. Nelle trappole e nelle azioni usa SOLO numeri presenti nei valori; testo semplice, niente markdown.
8. Garanzie: «compresa_nel_premio» true se la garanzia e' compresa nel premio della proposta (anche se il documento la chiama complementare), false se e' facoltativa, opzionale o non attivata. Una garanzia compresa nel premio NON e' «solo_accessoria».
9. La variante va SOLO in «condizione», mai nel «valore». Ogni numero del «valore» deve essere scritto nella «citazione».
10. Se il valore e' un intervallo scelto dal cliente (es. durata da 2 a 30 anni), scrivi l'intervallo nel «valore» e metti «numero»: null. Se non c'e' una cifra (es. rimborso al netto della quota di rischio), «numero»: null.
11. Esclusione con una misura (es. immersione oltre X metri) che NON e' nell'elenco chiuso: «valore»: «non esclusa», «numero»: null, citando la frase dell'elenco chiuso.
12. «altre_differenze»: TUTTE le altre differenze importanti fra A e B che non stanno nei concetti (costi fissi, limiti, condizioni, servizi, obblighi), con la frase copiata per ogni lato. Non ripetere i concetti.
{regole}
CONCETTI:
{concetti}"""


def _impronta(percorsi: List[str], profilo: Dict[str, Any]) -> str:
    import hashlib
    h = hashlib.sha256()
    for p in percorsi:
        with open(p, "rb") as stream:
            h.update(hashlib.sha256(stream.read()).digest())
    h.update(f"{profilo['nome']}|{profilo.get('versione', 1)}|{VERSIONE_CONFRONTO}|{modello()}".encode())
    return h.hexdigest()[:24]


def confronto_diretto(file_a: List[str], file_b: List[str], profilo: Dict[str, Any],
                      cliente: str = "") -> Dict[str, Any]:
    """Una chiamata a Gemini con i PDF dei due lati insieme: forma scelta, valori di A e B
    per concetto, trappole, azioni, sintesi. Salvato accanto al primo PDF di A e riletto da
    li' se gli stessi file si confrontano di nuovo (stesso profilo, stesso modello)."""
    import confronto_catalogo as cc
    cache = os.path.join(os.path.dirname(file_a[0]), f"confronto_{_impronta(file_a + file_b, profilo)}.json")
    if os.path.isfile(cache):
        try:
            with open(cache, encoding="utf-8") as stream:
                return json.load(stream)
        except (OSError, ValueError):
            pass
    chiave = os.getenv("GEMINI_API_KEY", "").strip()
    if not chiave:
        raise RuntimeError("GEMINI_API_KEY non impostata")
    regole = "".join(f"{n}. {r}\n" for n, r in enumerate(profilo.get("regole_lettura") or [], 8))
    istruzioni = ISTRUZIONI_CONFRONTO.format(
        lettore=cc._p(profilo, "lettore"), per_chi=cc._p(profilo, "per_chi"), utente=cc._p(profilo, "utente"),
        file_a=", ".join(f"A{n}" for n in range(1, len(file_a) + 1)),
        file_b=", ".join(f"B{n}" for n in range(1, len(file_b) + 1)),
        regole=regole, concetti=cc._elenco_concetti(profilo, False))
    with _client() as client:
        parti: List[Dict[str, Any]] = []
        for lato, files in (("A", file_a), ("B", file_b)):
            for n, p in enumerate(files, 1):
                parti.append({"text": f"Documento {lato}{n}:"})
                parti.append(_parte_pdf(client, p, chiave))
        parti.append({"text": istruzioni})
        corpo = {"contents": [{"role": "user", "parts": parti}],
                 "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                      "maxOutputTokens": 60000}}
        r = client.post(f"{API}/v1beta/models/{modello()}:generateContent",
                        headers={"x-goog-api-key": chiave}, json=corpo)
        _controlla(r)
        dati = r.json()
    uso = dati.get("usageMetadata") or {}
    if registra_consumo and cliente:
        try:
            registra_consumo(cliente,
                             round(int(uso.get("promptTokenCount") or 0) * _fattore("GEMINI_FATTORE_INGRESSO", 2.0)),
                             round((int(uso.get("candidatesTokenCount") or 0) + int(uso.get("thoughtsTokenCount") or 0))
                                   * _fattore("GEMINI_FATTORE_USCITA", 4.2)))
        except Exception as error:                      # il conteggio non deve fermare la lettura
            print(f"[GEMINI] consumo non registrato: {error}")
    testo = "".join(p.get("text", "") for c in dati.get("candidates") or []
                    for p in (c.get("content") or {}).get("parts") or [])
    esito = _json(testo)
    if not isinstance(esito, dict) or not isinstance(esito.get("concetti"), list):
        motivo = ((dati.get("candidates") or [{}])[0] or {}).get("finishReason", "")
        raise RuntimeError(f"Gemini non ha restituito un confronto leggibile ({motivo or 'risposta vuota'})")
    esito["_modello"] = modello()
    esito["_token"] = {"ingresso": uso.get("promptTokenCount"), "uscita": uso.get("candidatesTokenCount")}
    tmp = cache + ".tmp"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(esito, stream, ensure_ascii=False)
    os.replace(tmp, cache)
    return esito


def lettura_da_confronto(esito: Dict[str, Any], lato: str, fasc: Dict[str, Any],
                         documenti: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """I valori di un lato nel formato della lettura di sempre: «A2» pagina 5 diventa l'id
    della pagina del fascicolo; un valore della sola garanzia accessoria porta il suo nome in
    «forma»; un esempio porta «(esempio)», cosi' la verifica lo scarta e lo mostra a parte."""
    pagine: Dict[Any, str] = {}
    for p in fasc["pagine"]:
        try:
            numero = int(str(p["label"]).split()[1])
        except (IndexError, ValueError):
            continue
        pagine.setdefault((p.get("documento_id"), numero), p["id"])
    out = []
    for c in esito.get("concetti") or []:
        if not isinstance(c, dict) or not isinstance(c.get(lato), dict):
            continue
        v = c[lato]
        try:
            indice = int(str(v.get("documento") or f"{lato}1").strip().upper().lstrip(lato) or 1) - 1
        except ValueError:
            indice = 0
        did = documenti[indice].get("id") if 0 <= indice < len(documenti) else (documenti[0].get("id") if documenti else None)
        try:
            numero = int(str(v.get("pagina")).strip("[] p"))
        except (TypeError, ValueError):
            numero = 0
        valore = str(v.get("valore") or "").strip()
        condizione = str(v.get("condizione") or "").strip()
        # «5 anni (esteso a 7 se ...)»: la parte fra parentesi e' la variante. Si sposta nella
        # condizione, cosi' la verifica controlla il valore (5) e non butta tutto per il 7
        # che sta in un'altra frase (prova Sara, 24ott2026)
        m = re.match(r"^(.*?\S)\s*\((.+)\)\s*$", valore, re.S)
        if m and not v.get("esempio"):
            valore = m.group(1).strip()
            condizione = (condizione + "; " if condizione else "") + m.group(2).strip()
        if v.get("esempio") and "esempio" not in valore.lower():
            valore += " (esempio)"
        # una garanzia compresa nel premio non e' accessoria anche se il documento la chiama
        # «complementare» (Versilia: invalidita' e doppio capitale nel premio base)
        accessoria = bool(v.get("solo_accessoria")) and v.get("compresa_nel_premio") is not True
        out.append({"id": c.get("id"), "valore": valore, "numero": v.get("numero"), "unita": v.get("unita"),
                    "si_no": v.get("si_no"), "condizione": condizione,
                    "forma": str(v.get("nome_accessoria") or "garanzia accessoria") if accessoria else "",
                    "citazione": v.get("citazione") or "", "pagina": pagine.get((did, numero), "")})
    return out


def accessorie_da_confronto(esito: Dict[str, Any], lato: str) -> List[str]:
    return sorted({str(c[lato].get("nome_accessoria") or "") for c in esito.get("concetti") or []
                   if isinstance(c, dict) and isinstance(c.get(lato), dict) and c[lato].get("solo_accessoria")
                   and c[lato].get("nome_accessoria")})
