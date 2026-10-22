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


def _parte_pdf(client: Any, percorso: str, chiave: str) -> Dict[str, Any]:
    dati = open(percorso, "rb").read()
    if len(dati) <= MAX_INLINE_BYTE:
        return {"inline_data": {"mime_type": "application/pdf", "data": base64.b64encode(dati).decode()}}
    # file grande: caricamento in due passi (File API), poi si usa il suo indirizzo
    inizio = client.post(f"{API}/upload/v1beta/files", params={"key": chiave},
                         headers={"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                                  "X-Goog-Upload-Header-Content-Length": str(len(dati)),
                                  "X-Goog-Upload-Header-Content-Type": "application/pdf",
                                  "Content-Type": "application/json"},
                         json={"file": {"display_name": os.path.basename(percorso)[:100]}})
    inizio.raise_for_status()
    url = inizio.headers.get("x-goog-upload-url")
    if not url:
        raise RuntimeError("Gemini non ha dato l'indirizzo di caricamento")
    fatto = client.post(url, headers={"Content-Length": str(len(dati)), "X-Goog-Upload-Offset": "0",
                                      "X-Goog-Upload-Command": "upload, finalize"}, content=dati)
    fatto.raise_for_status()
    info = fatto.json().get("file") or {}
    for _ in range(30):
        if info.get("state") in (None, "ACTIVE"):
            break
        time.sleep(2)
        info = client.get(f"{API}/v1beta/{info['name']}", params={"key": chiave}).json()
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
        r = client.post(f"{API}/v1beta/models/{modello()}:generateContent", params={"key": chiave}, json=corpo)
        if r.status_code >= 400:
            raise RuntimeError(f"Gemini ha risposto {r.status_code}: {r.text[:200]}")
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
