"""Confronto per CONCETTI: un catalogo chiuso per mercato (profilo_*.json).

Perche' esiste (22ott2026, prova dal vivo con il set informativo di Sara): quando il modello
sceglie da solo cosa confrontare con cosa, confonde concetti diversi (la non contestabilita'
di 24 mesi messa contro il termine di 3 mesi per annullare il contratto), legge al contrario
(«compreso deltaplano» in una tabella di esclusioni) e scambia una soglia di capitale per un
premio. Riconoscere e confondere non possono stare insieme.

Il motore e' UNICO per tutti i mercati: tutto cio' che e' di un mercato sta nel profilo
(polizze vita, preventivi di piastrelle, reti informatiche...): chi legge, cosa si legge, i
concetti, la misura che rende confrontabili i prezzi (premio per 1.000 EUR di capitale medio,
EUR al m2, EUR per punto rete), i vincoli («il premio si giudica solo a pari capitale medio»)
e le carte del quadro. Un mercato nuovo e' un file profilo_<nome>.json, non codice nuovo.
I soli calcoli che il testo non da' direttamente (il capitale medio di una polizza
decrescente) sono funzioni registrate in CALCOLI, scelte dal profilo per nome.

Qui i ruoli sono separati:
1. Il catalogo dice QUALI concetti esistono, cosa misurano, l'unita' e il verso del meglio per
   chi deve scegliere. E' un file di dati, certificato da chi conosce il mercato.
2. Il modello, per ogni concetto, trova la frase che lo definisce e il valore. Non confronta.
3. Ogni concetto si legge DUE volte, in due letture separate (ordine dei concetti e
   formulazione diversi). Se le letture non coincidono il concetto e' «da verificare» e non
   entra nel giudizio.
4. Il codice verifica la citazione e i numeri (come sempre) e confronta SOLO un concetto con lo
   stesso concetto, con il verso del catalogo. Mai due concetti diversi.
5. Cio' che non si trova si dichiara: «non l'ho trovato», mai «non c'e'».
"""
from __future__ import annotations

import concurrent.futures
import contextvars
import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import confronto_polizze as cp
import lettore_gemini

CARTELLA = os.path.dirname(os.path.abspath(__file__))
PROFILO_PREDEFINITO = "vita_tcm"
_PROFILI: Dict[str, Dict[str, Any]] = {}


def carica_profilo(nome: str = PROFILO_PREDEFINITO) -> Dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9_]{1,40}", nome or ""):
        raise ValueError("Nome di profilo non valido")
    if nome not in _PROFILI:
        with open(os.path.join(CARTELLA, f"profilo_{nome}.json"), encoding="utf-8") as stream:
            profilo = json.load(stream)
        _controlla(profilo)
        _PROFILI[nome] = profilo
    return _PROFILI[nome]


def _controlla(profilo: Dict[str, Any]) -> None:
    """Un profilo sbagliato si ferma al caricamento, non a meta' di un confronto."""
    ids = [c["id"] for c in profilo["concetti"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Concetti con lo stesso id nel profilo")
    concetti = set(ids)
    misure = profilo.get("misure") or {}
    for nome, m in misure.items():
        if m.get("calcolo") and m["calcolo"] not in CALCOLI:
            raise ValueError(f"Misura {nome}: calcolo sconosciuto {m['calcolo']}")
        if not m.get("calcolo") and m.get("concetto") not in concetti:
            raise ValueError(f"Misura {nome}: concetto sconosciuto")
    pu = profilo.get("prezzo_unitario")
    if pu:
        if pu.get("prezzo") not in concetti:
            raise ValueError("prezzo_unitario: concetto del prezzo sconosciuto")
        if pu.get("quantita") not in misure and pu.get("quantita") not in concetti:
            raise ValueError("prezzo_unitario: quantita' sconosciuta")
    for c in profilo["concetti"]:
        if c.get("solo_a_pari") and c["solo_a_pari"] not in misure and c["solo_a_pari"] not in concetti:
            raise ValueError(f"{c['id']}: solo_a_pari sconosciuto")
    for carta in profilo.get("quadro") or []:
        tipo = carta.get("tipo")
        if tipo == "concetto" and carta.get("concetto") not in concetti:
            raise ValueError("Carta del quadro su un concetto sconosciuto")
        if tipo == "misura" and carta.get("misura") not in misure:
            raise ValueError("Carta del quadro su una misura sconosciuta")
        if tipo == "prezzo_unitario" and not pu:
            raise ValueError("Carta del prezzo unitario senza prezzo_unitario nel profilo")
        if tipo not in ("concetto", "misura", "prezzo_unitario", "conteggio"):
            raise ValueError(f"Carta del quadro di tipo sconosciuto: {tipo}")


def profili_disponibili() -> List[Dict[str, Any]]:
    """I profili presenti accanto al codice (il gestore li attiva per cliente)."""
    out = []
    for nome in sorted(os.listdir(CARTELLA)):
        m = re.fullmatch(r"profilo_([a-z0-9_]{1,40})\.json", nome)
        if not m:
            continue
        try:
            p = carica_profilo(m.group(1))
        except (OSError, ValueError, KeyError) as error:
            print(f"[CONFRONTO] profilo {nome} non valido: {error}")
            continue
        out.append({"nome": p["nome"], "titolo": p["titolo"], "stato": p.get("stato") or ""})
    return out


def _p(profilo: Dict[str, Any], campo: str) -> str:
    """I testi del mercato, con valori neutri se il profilo non li dice."""
    neutri = {"lettore": "un esperto del settore", "documento": "documento", "documenti": "documenti",
              "per_chi": "il cliente", "utente": "chi confronta",
              "esempi_condizione": "una condizione particolare, una deroga, una variante"}
    return str(profilo.get(campo) or neutri[campo])


def testi(profilo: Dict[str, Any]) -> Dict[str, str]:
    t = {"titolo": "Confronta", "lato": "documento", "trappole": "Trappole per " + _p(profilo, "per_chi"),
         "sintesi": "Sintesi", "azioni": "Azioni da fare"}
    t.update({k: str(v) for k, v in (profilo.get("testi") or {}).items()})
    return t


# ------------------------------------------------------------------ lettura (modello)
SYSTEM_CONCETTI = """Sei {lettore}. Leggi il testo di un {documento} e, per OGNI concetto dell'elenco, cerca la frase che lo definisce.
Il testo viene da PDF: sotto «[RIGHE DELLE TABELLE]» le righe di tabella sono gia' ricostruite con il nome della colonna accanto a ogni valore.
Per ogni concetto TROVATO in questo testo scrivi un oggetto:
- "id": l'id del concetto
- "valore": la risposta in poche parole, con numeri e unita' esatti
- "numero": il numero che risponde alla domanda (solo per importo, durata, percentuale, misura), altrimenti null
- "unita": l'unita' del numero come scritta nel testo, altrimenti null
- "si_no": "si" o "no" per i concetti si/no, altrimenti null
- "condizione": se il valore cambia solo a una condizione ({esempi_condizione}) scrivi qui la condizione e il valore che ne deriva; il "numero" resta quello che vale per tutti
- "forma": se il valore vale solo per una forma, una variante o una tariffa, quale; altrimenti ""
- "citazione": la frase che lo prova, COPIATA ESATTAMENTE dal testo (massimo 300 caratteri)
- "pagina": l'id della pagina tra doppie parentesi quadre in cui sta la citazione
Regole:
1. Rispondi alla DOMANDA del concetto, e solo a quella. Leggi «non confondere con»: quelle cose NON sono il concetto.
2. Se nel testo il concetto non c'e', non scriverlo. Non dedurre, non usare conoscenze generali.
3. Un concetto puo' comparire piu' volte (es. una volta per ogni forma o variante): un oggetto per ciascuna.
{regole}Rispondi SOLO con JSON: {"concetti": [ ... ]}"""


def sistema_lettura(profilo: Dict[str, Any]) -> str:
    regole = "".join(f"{n}. {r}\n" for n, r in enumerate(profilo.get("regole_lettura") or [], 4))
    out = SYSTEM_CONCETTI
    for campo in ("lettore", "documento", "esempi_condizione"):
        out = out.replace("{" + campo + "}", _p(profilo, campo))
    return out.replace("{regole}", regole)


SECONDA_LETTURA = ("SECONDA LETTURA, INDIPENDENTE: rileggi il testo da capo come se fosse la prima volta, "
                   "senza dare per scontato nulla. ")


def _elenco_concetti(profilo: Dict[str, Any], rovescia: bool) -> str:
    concetti = list(reversed(profilo["concetti"])) if rovescia else profilo["concetti"]
    righe = []
    for c in concetti:
        tipo = {"si_no": "si/no", "importo": "importo in euro", "durata": f"durata ({c.get('unita')})",
                "percentuale": "percentuale", "misura": f"misura ({c.get('unita')})", "testo": "testo"}[c["tipo"]]
        riga = f"- {c['id']} [{tipo}]: {c['domanda']}"
        if c.get("non_confondere"):
            riga += " NON confondere con: " + "; ".join(c["non_confondere"]) + "."
        righe.append(riga)
    return "\n".join(righe)


def _chiedi(sistema: str, utente: str, temperatura: float) -> Dict[str, Any]:
    if cp.scrivi is None:
        raise RuntimeError("Modello non configurato")
    for tentativo in range(2):
        raw = cp.scrivi(sistema, utente if not tentativo else utente + "\n\nRispondi SOLO con il JSON.",
                        6000, temperatura)
        dati = cp._json(raw)
        if isinstance(dati, dict):
            return dati
    raise RuntimeError("Il modello non ha restituito dati leggibili")


def _leggi_lotto(lotto: List[Dict[str, Any]], profilo: Dict[str, Any], seconda: bool,
                 non_lette: List[str]) -> List[Dict[str, Any]]:
    utente = ((SECONDA_LETTURA if seconda else "") + "CONCETTI:\n" + _elenco_concetti(profilo, seconda)
              + "\n\nFASCICOLO:\n" + cp._testo_lotto(lotto))
    try:
        r = _chiedi(sistema_lettura(profilo), utente, 0.3 if seconda else 0.0)
        return [c for c in (r.get("concetti") or []) if isinstance(c, dict)]
    except RuntimeError:
        if len(lotto) == 1:
            non_lette.append(f"{lotto[0]['documento']} {lotto[0]['label']}")
            return []
        meta = len(lotto) // 2
        return (_leggi_lotto(lotto[:meta], profilo, seconda, non_lette)
                + _leggi_lotto(lotto[meta:], profilo, seconda, non_lette))


def leggi(fascicoli: List[Dict[str, Any]], profilo: Dict[str, Any],
          avanza: cp.Avanzamento = None) -> List[Dict[str, Any]]:
    """Per ogni lato: {"letture": [lettura1, lettura2], "non_lette": [...]}. Tutte le parti di
    tutti i lati, in tutte e due le letture, in parallelo."""
    lavori = [(n, k, lotto) for n, f in enumerate(fascicoli) for k in (0, 1) for lotto in cp._lotti(f["pagine"])]
    esiti: List[Dict[str, Any]] = [{"letture": [[], []], "non_lette": []} for _ in fascicoli]
    fatte = 0
    if avanza:
        avanza("lettura", 0, len(lavori))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(cp.MAX_PARALLELO, len(lavori)))) as pool:
        futuri = {pool.submit(contextvars.copy_context().run, _leggi_lotto, lotto, profilo, bool(k),
                              esiti[n]["non_lette"]): (n, k) for n, k, lotto in lavori}
        for futuro in concurrent.futures.as_completed(futuri):
            n, k = futuri[futuro]
            esiti[n]["letture"][k].extend(futuro.result())
            fatte += 1
            if avanza:
                avanza("lettura", fatte, len(lavori))
    for e in esiti:
        e["non_lette"] = sorted(set(e["non_lette"]))
    return esiti


# ------------------------------------------------------------------ verifica e valore (codice)
_IN_MESI = {"mesi": 1.0, "mese": 1.0, "anni": 12.0, "anno": 12.0, "giorni": 1 / 30, "giorno": 1 / 30, "gg": 1 / 30}
_IN_GIORNI = {"giorni": 1.0, "giorno": 1.0, "gg": 1.0, "mesi": 30.0, "mese": 30.0, "anni": 365.0, "anno": 365.0}
_IN_ANNI = {"anni": 1.0, "anno": 1.0, "mesi": 1 / 12, "mese": 1 / 12}
_LUNGHEZZE = {"metri": 1.0, "metro": 1.0, "m": 1.0, "mt": 1.0, "cm": 0.01, "mm": 0.001, "km": 1000.0}
_AREE = {"m2": 1.0, "mq": 1.0, "metriquadri": 1.0, "metriquadrati": 1.0, "cm2": 0.0001}
_MISURE = {"metri": _LUNGHEZZE, "m": _LUNGHEZZE, "m2": _AREE, "mq": _AREE,
           "cm": {k: v * 100 for k, v in _LUNGHEZZE.items()}, "mm": {k: v * 1000 for k, v in _LUNGHEZZE.items()}}


def _normalizza(concetto: Dict[str, Any], c: Dict[str, Any]) -> Optional[Any]:
    """Il valore confrontabile: un numero nell'unita' del catalogo, oppure "si"/"no"."""
    tipo = concetto["tipo"]
    if tipo == "testo":
        return None
    if tipo == "si_no":
        v = str(c.get("si_no") or "").strip().lower()
        return v if v in ("si", "no") else None
    try:
        numero = float(str(c.get("numero")).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if tipo == "misura" and concetto.get("unita") in _MISURE:
        unita = str(c.get("unita") or concetto["unita"]).strip().lower().replace("²", "2").replace(" ", "")
        tabella = _MISURE[concetto["unita"]]
        if unita not in tabella:
            return None          # unita' diversa e non convertibile: non si confronta (da verificare)
        numero = numero * tabella[unita]
    if tipo == "durata":
        unita = str(c.get("unita") or concetto["unita"]).strip().lower()
        tabella = {"mesi": _IN_MESI, "giorni": _IN_GIORNI, "anni": _IN_ANNI}[concetto["unita"]]
        if unita not in tabella:
            return None
        numero = numero * tabella[unita]
    return round(numero, 4)


_PAROLE_NUMERO = {"uno": 1, "un": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6,
                  "sette": 7, "otto": 8, "nove": 9, "dieci": 10, "undici": 11, "dodici": 12, "quindici": 15,
                  "diciotto": 18, "venti": 20, "ventiquattro": 24, "trenta": 30, "trentasei": 36,
                  "quaranta": 40, "quarantotto": 48, "sessanta": 60, "novanta": 90, "cento": 100}
_NUMERO_A_PAROLE = re.compile(r"\b(" + "|".join(sorted(_PAROLE_NUMERO, key=len, reverse=True)) + r")\s+"
                              r"(mesi|mese|anni|anno|giorni|giorno|settimane|metri|per\s*cento)\b", re.I)
# un numero dato in un'altra unita' («2 anni» -> 24 mesi) e' lo stesso valore
_FATTORI = (1.0, 12.0, 1 / 12, 30.0, 1 / 30, 365.0, 1 / 365, 7.0, 100.0, 0.01, 1000.0, 0.001)


def _numeri_citazione(citazione: str) -> Set[float]:
    out = {float(n) for n in cp.numeri(citazione)}
    for m in _NUMERO_A_PAROLE.finditer(cp._piano(citazione)):
        out.add(float(_PAROLE_NUMERO[m.group(1).lower()]))     # «entro i primi sei mesi»
    return out


def _numeri_senza_prova(valore: str, numero: Any, citazione: str, condizione: str) -> Set[str]:
    """I numeri del valore che la citazione non dimostra. Il «numero» puo' essere lo stesso
    valore in un'altra unita' (2 anni = 24 mesi). I numeri della sola variante (condizione)
    non si chiedono alla citazione: la variante si mostra, non si confronta."""
    nella = _numeri_citazione(citazione)
    piano_valore = valore
    if condizione:
        piano_valore = re.sub(r"\(\s*se\b.*?\)", " ", valore, flags=re.I | re.S)
    del_valore = {float(n) for n in cp.numeri(piano_valore)}
    if 0.0 in del_valore and _ZERO.search(citazione):
        del_valore.discard(0.0)       # «senza maggiorazione», «senza trattenute»: lo zero e' scritto a parole
    mancanti = {cp._forma(n) for n in del_valore if n not in nella}
    try:
        n = float(str(numero).replace(",", "."))
    except (TypeError, ValueError):
        return mancanti
    if n == 0 and _ZERO.search(citazione):
        return mancanti
    if not any(abs(n - b * f) < 1e-6 * max(1.0, abs(n)) for b in nella | del_valore for f in _FATTORI):
        mancanti.add(cp._forma(n))
    return mancanti


_ZERO = re.compile(r"\bsenza\b|\bnessun|\bnon\s+(?:e'|è|sono|prevede|previst|applica)|\bintegrale\b|\bzero\b", re.I)


def _verifica(lato: str, fasc: Dict[str, Any], profilo: Dict[str, Any], lettura: List[Dict[str, Any]],
              scartate: List[Dict[str, Any]], numero_lettura: int,
              accessorie: Optional[List[str]] = None) -> Dict[str, List[Dict[str, Any]]]:
    """I candidati di una lettura che passano la verifica, per concetto."""
    per_id = {p["id"]: p for p in fasc["pagine"]}
    concetti = {c["id"]: c for c in profilo["concetti"]}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for c in lettura:
        cid = str(c.get("id") or "").strip()
        if cid not in concetti:
            continue
        concetto = concetti[cid]
        voce = {"nome": concetto["etichetta"], "valore": str(c.get("valore") or "").strip()[:300],
                "citazione": str(c.get("citazione") or "").strip()[:600]}
        if not voce["valore"] or not voce["citazione"]:
            continue
        pagina, riga = cp._trova_prova(voce, per_id.get(str(c.get("pagina") or "").strip("[] ").upper()),
                                       fasc["pagine"])
        if pagina is None:
            scartate.append({**voce, "lato": lato, "concetto": cid, "lettura": numero_lettura,
                             "motivo": "citazione non trovata nel fascicolo"})
            continue
        if riga:
            voce["citazione"] = riga
        condizione = str(c.get("condizione") or "").strip()[:300]
        if concetto["tipo"] in ("importo", "percentuale", "misura", "durata") and cp._ESEMPIO.search(voce["valore"]):
            # «300.000 euro (esempio)»: il numero di un esempio non e' il contratto (Sara, 22ott2026)
            scartate.append({**voce, "lato": lato, "concetto": cid, "lettura": numero_lettura,
                             "motivo": "valore di un esempio, non del contratto"})
            continue
        mancanti = _numeri_senza_prova(voce["valore"], c.get("numero"), voce["citazione"], condizione)
        if mancanti:
            scartate.append({**voce, "lato": lato, "concetto": cid, "lettura": numero_lettura,
                             "motivo": "numeri non presenti nella citazione: " + ", ".join(sorted(mancanti))})
            continue
        deroga = bool(pagina["deroga"] and re.search(r"deroga|appendic", voce["citazione"] + " " + condizione, re.I)) \
            or bool(re.search(r"\bin\s+(?:parziale\s+)?deroga", voce["citazione"], re.I))
        out.setdefault(cid, []).append({
            **voce, "concetto": cid, "condizione": condizione, "forma": str(c.get("forma") or "").strip()[:60],
            "normale": _normalizza(concetto, c), "deroga": deroga,
            # la «condizione» descrive una variante, il «numero» resta quello che vale per tutti
            # (cosi' chiede la lettura): un valore base con una variante NON e' condizionato
            # (prova dal vivo 22ott2026: «6 mesi, 3 con referto» scartato = carenza «non leggibile»).
            # Condizionata e' solo una deroga che vale a una condizione.
            "condizionata": deroga and (bool(condizione) or bool(cp._CONDIZIONE.search(voce["citazione"]))),
            "con_condizione": bool(condizione),
            "accessoria": _accessoria(profilo, str(c.get("forma") or ""), accessorie),
            "pagina": pagina["id"], "documento": pagina["documento"], "label": pagina["label"]})
    return out


def _accessoria(profilo: Dict[str, Any], forma: str, nomi: Optional[List[str]] = None) -> bool:
    """Un valore che vale solo per una garanzia accessoria (la complementare infortuni di Sara)
    non e' il valore del prodotto: si mostra, non si confronta. Le accessorie sono quelle del
    profilo (parole) e quelle che la scheda del documento ha riconosciuto (nomi)."""
    if not forma:
        return False
    f = forma.lower()
    if any(re.search(r, forma, re.I) for r in profilo.get("garanzie_accessorie") or []):
        return True
    return any(n and len(n) >= 6 and (n.lower() in f or f in n.lower()) for n in nomi or [])


def _valori_base(candidati: List[Dict[str, Any]]) -> Set[Any]:
    """I valori che valgono per tutti: una deroga senza condizione sostituisce il valore base;
    una deroga o un valore a condizione no (la condizione si mostra, non si confronta)."""
    con_valore = [c for c in candidati if c["normale"] is not None and not c.get("accessoria")]
    sostituiscono = [c for c in con_valore if c["deroga"] and not c["condizionata"]]
    validi = [c for c in con_valore if not c["condizionata"]]
    # un valore scritto con la sua variante («3 mesi con referto») accanto a quello di tutti
    # («6 mesi») e' la variante; se c'e' solo quello con la condizione, il suo numero e' il
    # valore di tutti (la lettura mette la variante nella condizione)
    base = sostituiscono or [c for c in validi if not c.get("con_condizione")] or validi
    return {(c["forma"], c["normale"]) for c in base}


def valuta_concetto(concetto: Dict[str, Any], prima: List[Dict[str, Any]], seconda: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Lo stato del concetto per un lato: certo | da_verificare | piu_forme | non_trovato | testo."""
    if not prima and not seconda:
        return {"stato": "non_trovato", "valore": None}
    if concetto["tipo"] == "testo":
        return {"stato": "testo", "valore": None}
    v1, v2 = _valori_base(prima), _valori_base(seconda)
    if not v1 and not v2:
        if all(c.get("accessoria") for c in prima + seconda):
            return {"stato": "da_verificare", "valore": None,
                    "motivo": "trovato solo in una garanzia accessoria, non nella copertura principale"}
        if all(c.get("normale") is None for c in prima + seconda):
            return {"stato": "da_verificare", "valore": None, "motivo": "il testo non dà un valore confrontabile"}
        return {"stato": "da_verificare", "valore": None, "motivo": "vale solo a una condizione"}
    if v1 != v2:
        return {"stato": "da_verificare", "valore": None,
                "motivo": "le due letture non coincidono" if v1 and v2 else "trovato da una sola lettura"}
    forme = {f for f, _ in v1}
    if len(v1) > 1:
        return {"stato": "piu_forme" if len(forme) > 1 else "da_verificare", "valore": None,
                "motivo": "il documento ha piu' forme o tariffe con valori diversi" if len(forme) > 1
                else "nel documento ci sono valori diversi per lo stesso concetto"}
    return {"stato": "certo", "valore": next(iter(v1))[1]}


# ------------------------------------------------------------------ confronto (codice)
def _formatta(valore: Optional[float], unita: str) -> str:
    if valore is None:
        return ""
    if unita in ("EUR", "€"):
        return cp._euro_it(float(valore)) + " €"
    n = cp._forma(float(valore)).replace(".", ",")
    if unita == "%":
        return f"{n}%"
    if unita.startswith("EUR/") or unita.startswith("€/"):
        return cp._euro_it(float(valore)) + " €/" + unita.split("/", 1)[1]
    return f"{n} {unita}".strip()


def _testo_valore(concetto: Dict[str, Any], valore: Any) -> str:
    if concetto["tipo"] == "si_no":
        return "sì" if valore == "si" else "no"
    if concetto.get("se_non_escluso") is not None and float(valore) == float(concetto["se_non_escluso"]):
        return "non esclusa"
    unita = concetto.get("unita") or ""
    if concetto["tipo"] == "importo" and not unita:
        unita = "EUR"
    return _formatta(float(valore), unita)


def confronta_concetto(concetto: Dict[str, Any], sa: Dict[str, Any], sb: Dict[str, Any],
                       per_chi: str = "il cliente") -> Tuple[str, str]:
    """(giudizio, perche). Solo lo stesso concetto, solo valori certi, solo con il verso del catalogo."""
    parti = []
    for lato, st in (("A", sa), ("B", sb)):
        if st["stato"] == "non_trovato":
            parti.append(f"non l'ho trovato in {lato}")
        elif st["stato"] == "da_verificare":
            parti.append(f"in {lato} {st.get('motivo') or 'da verificare'}")
        elif st["stato"] == "piu_forme":
            parti.append(f"in {lato} ci sono più forme o varianti con valori diversi: va scelta la forma")
    if parti:
        testo = "; ".join(parti)
        testo = testo[0].upper() + testo[1:]
        solo_mancanti = all(st["stato"] in ("non_trovato", "certo", "testo") for st in (sa, sb))
        return "non_confrontata", testo + (": va verificato, non vuol dire che non ci sia." if solo_mancanti
                                           else ": da verificare.")
    if "testo" in (sa["stato"], sb["stato"]):
        return "diverso", ""
    va, vb = sa["valore"], sb["valore"]
    ta, tb = _testo_valore(concetto, va), _testo_valore(concetto, vb)
    if va == vb:
        return "uguale", f"Uguale: {ta}."
    meglio = concetto.get("meglio")
    if not meglio:
        return "diverso", f"A {ta}, B {tb}."
    if concetto["tipo"] == "si_no":
        vince = "a" if va == meglio else "b"
        return f"meglio_{vince}", f"A {ta}, B {tb}: per {per_chi} è meglio «{'sì' if meglio == 'si' else 'no'}»."
    basso = meglio == "basso"
    vince = "a" if (va < vb) == basso else "b"
    return f"meglio_{vince}", (f"A {ta}, B {tb}: per {per_chi} conta il valore più "
                               f"{'basso' if basso else 'alto'}.")


# ------------------------------------------------------------------ misure, prezzo unitario, vincoli
def _cm_polizza(voci: List[Dict[str, Any]], stati: Dict[str, Any]) -> Dict[str, Any]:
    r = cp.capitale_medio(voci)
    return {"calcolabile": r["calcolabile"], "valore": r.get("capitale_medio"),
            "nota": r.get("andamento") or "", "come": r.get("come") or r.get("motivo") or "",
            "motivo": r.get("motivo") or "", "capitale": r.get("capitale")}


# i soli calcoli che il testo non da' direttamente; il profilo li sceglie per nome
CALCOLI: Dict[str, Callable[[List[Dict[str, Any]], Dict[str, Any]], Dict[str, Any]]] = {
    "capitale_medio_polizza": _cm_polizza,
}

_STATI_TESTO = {"non_trovato": "non trovato", "piu_forme": "più forme", "testo": "vedi testo",
                "da_verificare": "da verificare"}


def _da_concetto(stati: Dict[str, Any], cid: str) -> Dict[str, Any]:
    st = stati.get(cid) or {"stato": "non_trovato"}
    if st.get("stato") == "certo" and isinstance(st.get("valore"), (int, float)):
        return {"calcolabile": True, "valore": float(st["valore"]), "nota": "", "come": "", "motivo": ""}
    motivo = _STATI_TESTO.get(st.get("stato"), "da verificare")
    return {"calcolabile": False, "valore": None, "nota": "", "come": "", "motivo": motivo}


def misura(profilo: Dict[str, Any], nome: str, voci: List[Dict[str, Any]], stati: Dict[str, Any]) -> Dict[str, Any]:
    spec = (profilo.get("misure") or {}).get(nome)
    if spec is None:                          # una misura puo' essere direttamente un concetto
        return _da_concetto(stati, nome)
    if spec.get("calcolo"):
        return CALCOLI[spec["calcolo"]](voci, stati)
    return _da_concetto(stati, spec["concetto"])


def _etichetta_misura(profilo: Dict[str, Any], nome: str) -> str:
    spec = (profilo.get("misure") or {}).get(nome)
    if spec:
        return spec.get("etichetta") or nome.replace("_", " ")
    c = next((c for c in profilo["concetti"] if c["id"] == nome), None)
    return c["etichetta"] if c else nome.replace("_", " ")


def _unita_misura(profilo: Dict[str, Any], nome: str) -> str:
    spec = (profilo.get("misure") or {}).get(nome)
    if spec:
        return spec.get("unita") or ""
    c = next((c for c in profilo["concetti"] if c["id"] == nome), None)
    return (c.get("unita") or ("EUR" if c["tipo"] == "importo" else "")) if c else ""


def prezzo_unitario(profilo: Dict[str, Any], voci: List[Dict[str, Any]], stati: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Il prezzo sulla stessa base: prezzo / (quantita' / per). Per una polizza vita il premio
    per 1.000 EUR di capitale medio; per le piastrelle gli EUR al m2."""
    spec = profilo.get("prezzo_unitario")
    if not spec:
        return None
    prezzo = _da_concetto(stati, spec["prezzo"])
    quantita = misura(profilo, spec["quantita"], voci, stati)
    out = {"calcolabile": False, "prezzo": prezzo["valore"], "quantita": quantita["valore"],
           "unitario": None, "nota": quantita.get("nota") or "", "come": quantita.get("come") or "",
           "motivo": ""}
    if not prezzo["calcolabile"]:
        out["motivo"] = f"{_etichetta_misura(profilo, spec['prezzo']).lower()}: {prezzo['motivo']}"
    elif not quantita["calcolabile"] or not quantita["valore"]:
        out["motivo"] = (quantita.get("motivo") or "non calcolabile")
    else:
        out["unitario"] = round(prezzo["valore"] / (quantita["valore"] / float(spec.get("per") or 1)), 2)
        out["calcolabile"] = True
    return out


def _vincolo(profilo: Dict[str, Any], concetto: Dict[str, Any], giudizio: str, perche: str,
             misure_a: Dict[str, Any], misure_b: Dict[str, Any]) -> Tuple[str, str]:
    """«solo_a_pari»: un concetto si giudica da solo solo se quella misura e' la stessa nei due
    documenti. Il premio di 348 EUR per 200.000 costanti contro 156 EUR per 200.000 decrescenti
    darebbe «meglio B» confrontando coperture diverse (prova del 22ott2026)."""
    nome = concetto.get("solo_a_pari")
    if not nome or not giudizio.startswith("meglio_"):
        return giudizio, perche
    ma, mb = misure_a.get(nome) or {}, misure_b.get(nome) or {}
    if ma.get("calcolabile") and mb.get("calcolabile") and abs(float(ma["valore"]) - float(mb["valore"])) < 0.005:
        return giudizio, perche
    etichetta = _etichetta_misura(profilo, nome).lower()
    motivo = (f"{etichetta} non è lo stesso" if ma.get("calcolabile") and mb.get("calcolabile")
              else f"{etichetta} non è calcolabile in tutti e due")
    pu = profilo.get("prezzo_unitario")
    conta = f"conta «{pu['etichetta']}»" if pu and pu.get("prezzo") == concetto["id"] else "va confrontato a parità"
    return "diverso", perche.split(":")[0] + f": non si giudica da solo perché {motivo}: {conta}."


def _misure_lato(profilo: Dict[str, Any], voci: List[Dict[str, Any]], stati: Dict[str, Any]) -> Dict[str, Any]:
    nomi = set((profilo.get("misure") or {}).keys())
    nomi |= {c["solo_a_pari"] for c in profilo["concetti"] if c.get("solo_a_pari")}
    pu = profilo.get("prezzo_unitario")
    if pu:
        nomi.add(pu["quantita"])
    return {n: misura(profilo, n, voci, stati) for n in sorted(nomi)}


def _giudica_righe(profilo: Dict[str, Any], conf: Dict[str, Any]) -> None:
    """Ricalcola i giudizi con le regole di oggi (anche su un confronto salvato)."""
    concetti = {c["id"]: c for c in profilo["concetti"]}
    sa_all, sb_all = conf["a"].get("concetti") or {}, conf["b"].get("concetti") or {}
    per_chi = _p(profilo, "per_chi")
    for r in conf["righe"]:
        c = concetti.get(r.get("concetto"))
        if not c:
            continue
        sa = sa_all.get(c["id"]) or {"stato": "non_trovato"}
        sb = sb_all.get(c["id"]) or {"stato": "non_trovato"}
        g, perche = confronta_concetto(c, sa, sb, per_chi)
        r["giudizio"], r["perche"] = _vincolo(profilo, c, g, perche, conf["a"]["misure"], conf["b"]["misure"])


def _base_compatibile(pu: Optional[Dict[str, Any]], misure: Dict[str, Any]) -> Dict[str, Any]:
    """Le chiavi di sempre (premio, capitale_medio, per_mille) per i numeri ammessi nella sintesi."""
    if not pu:
        return {"calcolabile": False, "motivo": "nessun prezzo unitario nel profilo"}
    out = {"calcolabile": pu["calcolabile"], "premio": pu["prezzo"], "capitale_medio": pu["quantita"],
           "per_mille": pu["unitario"], "come": pu["come"], "motivo": pu["motivo"]}
    for m in misure.values():
        if m.get("capitale"):
            out["capitale"] = m["capitale"]
    return out


def rivedi(conf: Dict[str, Any]) -> None:
    """Misure, prezzo unitario, giudizi e quadro dal profilo, sui valori gia' verificati."""
    profilo = carica_profilo(conf.get("profilo", {}).get("nome") or PROFILO_PREDEFINITO)
    conf["profilo"].update({"titolo": profilo["titolo"], "stato": profilo.get("stato"),
                            "testi": testi(profilo),
                            "prezzo_unitario": (profilo.get("prezzo_unitario") or {}).get("etichetta", "")})
    for lato in ("a", "b"):
        x = conf[lato]
        stati = x.get("concetti") or {}
        x["misure"] = _misure_lato(profilo, x["voci"], stati)
        x["prezzo_unitario"] = prezzo_unitario(profilo, x["voci"], stati)
        x["base_premio"] = _base_compatibile(x["prezzo_unitario"], x["misure"])
    _giudica_righe(profilo, conf)


def carte(conf: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Le carte del quadro, come le dichiara il profilo."""
    profilo = carica_profilo(conf.get("profilo", {}).get("nome") or PROFILO_PREDEFINITO)
    concetti = {c["id"]: c for c in profilo["concetti"]}
    A, B = conf["a"], conf["b"]
    sa, sb = A.get("concetti") or {}, B.get("concetti") or {}
    per_chi = _p(profilo, "per_chi")
    out: List[Dict[str, Any]] = []

    def stato_testo(cid: str, st: Dict[str, Any]) -> str:
        if st.get("stato") == "certo":
            return _testo_valore(concetti[cid], st["valore"])
        return _STATI_TESTO.get(st.get("stato"), "non trovato")

    for spec in profilo.get("quadro") or []:
        tipo = spec["tipo"]
        if tipo == "prezzo_unitario":
            pu = profilo["prezzo_unitario"]
            pa, pb = A.get("prezzo_unitario") or {}, B.get("prezzo_unitario") or {}
            unita = pu.get("unita") or "EUR"
            nome_prezzo = _etichetta_misura(profilo, pu["prezzo"]).lower()
            nota = (f"{nome_prezzo}: A {_formatta(pa['prezzo'], 'EUR')}, B {_formatta(pb['prezzo'], 'EUR')}"
                    if pa.get("prezzo") is not None and pb.get("prezzo") is not None
                    else (pa.get("motivo") or pb.get("motivo") or ""))
            out.append({"voce": spec.get("etichetta") or pu["etichetta"],
                        "a": _formatta(pa["unitario"], unita) if pa.get("calcolabile") else "non calcolabile",
                        "b": _formatta(pb["unitario"], unita) if pb.get("calcolabile") else "non calcolabile",
                        "meglio": cp._chi_vince(pa.get("unitario") if pa.get("calcolabile") else None,
                                                pb.get("unitario") if pb.get("calcolabile") else None, True),
                        "nota": nota})
        elif tipo == "misura":
            nome = spec["misura"]
            ma, mb = A["misure"].get(nome) or {}, B["misure"].get(nome) or {}
            unita = _unita_misura(profilo, nome)
            va = ma.get("valore") if ma.get("calcolabile") else None
            vb = mb.get("valore") if mb.get("calcolabile") else None
            meglio = spec.get("meglio")
            out.append({"voce": spec.get("etichetta") or _etichetta_misura(profilo, nome),
                        "a": _formatta(va, unita) if va is not None else (ma.get("motivo") or "non trovato"),
                        "b": _formatta(vb, unita) if vb is not None else (mb.get("motivo") or "non trovato"),
                        "meglio": cp._chi_vince(va, vb, meglio == "basso") if meglio in ("alto", "basso") else
                        ("pari" if va is not None and va == vb else "nd"),
                        "nota": " · ".join(x for x in (f"A {ma['nota']}" if ma.get("nota") else "",
                                                       f"B {mb['nota']}" if mb.get("nota") else "") if x)})
        elif tipo == "concetto":
            cid = spec["concetto"]
            a, b = sa.get(cid) or {"stato": "non_trovato"}, sb.get(cid) or {"stato": "non_trovato"}
            giudizio, perche = confronta_concetto(concetti[cid], a, b, per_chi)
            giudizio, perche = _vincolo(profilo, concetti[cid], giudizio, perche, A["misure"], B["misure"])
            meglio = {"meglio_a": "a", "meglio_b": "b", "uguale": "pari"}.get(giudizio, "nd")
            out.append({"voce": spec.get("etichetta") or concetti[cid]["etichetta"],
                        "a": stato_testo(cid, a), "b": stato_testo(cid, b), "meglio": meglio,
                        "nota": "" if meglio != "nd" else perche})
        elif tipo == "conteggio":
            # 24ott2026: «2 su 4» non diceva QUALI. Ora la carta nomina le voci: quelle che
            # contano (comprese / escluse), quelle che no, e quelle ancora da verificare.
            ids = spec.get("concetti") or [c["id"] for c in profilo["concetti"]
                                           if c.get("chiave") == spec.get("chiave") and c["tipo"] == "si_no"]
            conta = spec.get("conta") or "si"
            breve = lambda c: concetti[c].get("breve") or concetti[c]["etichetta"]       # noqa: E731

            def gruppi(st: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
                si = [c for c in ids if (st.get(c) or {}).get("valore") == conta]
                no = [c for c in ids if (st.get(c) or {}).get("stato") == "certo" and c not in si]
                dubbi = [c for c in ids if c not in si and c not in no
                         and (st.get(c) or {}).get("stato") not in (None, "non_trovato")]
                return si, no, dubbi
            ga, gb = gruppi(sa), gruppi(sb)

            def testo(g: Tuple[List[str], List[str], List[str]]) -> str:
                return (", ".join(breve(c) for c in g[0]) or "nessuna") + f" ({len(g[0])} su {len(ids)})"

            def dettaglio(lato: str, g: Tuple[List[str], List[str], List[str]]) -> str:
                parti = []
                if g[1]:
                    parti.append(("non " + spec.get("verbo_no", "comprese" if conta == "si" else "") + ": ").replace("non : ", "no: ")
                                 + ", ".join(breve(c) for c in g[1]))
                if g[2]:
                    parti.append("da verificare: " + ", ".join(breve(c) for c in g[2]))
                return f"{lato} " + "; ".join(parti) if parti else ""
            dubbie = len(ga[2]) + len(gb[2])
            nota = " · ".join(x for x in (spec.get("nota") or "", dettaglio("A", ga), dettaglio("B", gb)) if x)
            meglio = spec.get("meglio")
            out.append({"voce": spec["etichetta"], "a": testo(ga), "b": testo(gb),
                        "meglio": cp._chi_vince(len(ga[0]), len(gb[0]), meglio == "basso")
                        if meglio in ("alto", "basso") and not dubbie else "nd",
                        "nota": nota})
    return out


# ------------------------------------------------------------------ misura sulla chiave di correzione
def chiavi_disponibili(profilo_nome: str) -> List[str]:
    out = []
    for nome in sorted(os.listdir(CARTELLA)):
        m = re.fullmatch(r"chiave_([a-z0-9_]{1,40})\.json", nome)
        if m:
            try:
                with open(os.path.join(CARTELLA, nome), encoding="utf-8") as stream:
                    if json.load(stream).get("profilo") == profilo_nome:
                        out.append(m.group(1))
            except (OSError, ValueError):
                continue
    return out


def misura_su_chiave(conf: Dict[str, Any], nome_chiave: str) -> Dict[str, Any]:
    """Il confronto contro la chiave: giusti, sbagliati (un verdetto contrario o inventato) e
    mancati (il sistema non ha deciso dove la chiave decide). Gli sbagliati devono essere zero."""
    if not re.fullmatch(r"[a-z0-9_]{1,40}", nome_chiave or ""):
        raise ValueError("Nome di chiave non valido")
    with open(os.path.join(CARTELLA, f"chiave_{nome_chiave}.json"), encoding="utf-8") as stream:
        chiave = json.load(stream)
    giudizi = {r.get("concetto"): r for r in conf.get("righe") or [] if r.get("concetto")}
    conta = {"giusti": 0, "sbagliati": 0, "mancati": 0}
    righe = []
    for k in chiave["righe"]:
        r = giudizi.get(k["concetto"])
        g = (r or {}).get("giudizio") or "non_confrontata"
        atteso = k["atteso"]
        if atteso in ("meglio_a", "meglio_b", "uguale"):
            esito = "giusto" if g == atteso else ("sbagliato" if g in ("meglio_a", "meglio_b", "uguale") else "mancato")
        else:          # diverso o non confrontabile: un verdetto qui e' un errore
            esito = "sbagliato" if g in ("meglio_a", "meglio_b", "uguale") else "giusto"
        conta[{"giusto": "giusti", "sbagliato": "sbagliati", "mancato": "mancati"}[esito]] += 1
        righe.append({"concetto": k["concetto"], "atteso": atteso, "ottenuto": g, "esito": esito,
                      "perche": (r or {}).get("perche", ""), "nota": k.get("nota", "")})
    return {"chiave": nome_chiave, "titolo": chiave.get("titolo", ""), **conta, "totale": len(chiave["righe"]),
            "righe": righe}


# ------------------------------------------------------------------ lettura con Gemini e struttura
def leggi_con_gemini(lati: List[Any], profilo: Dict[str, Any], avanza: cp.Avanzamento = None):
    """Per ogni lato: la scheda di ogni documento (salvata, letta una volta sola). La stessa
    lettura vale come prima e seconda: il controllo e' quello del codice sulle citazioni."""
    lavori = [(n, fasc, d) for n, (fasc, docs) in enumerate(lati) for d in docs]
    esiti = [{"letture": [[], []], "non_lette": []} for _ in lati]
    struttura: Dict[str, Dict[str, Any]] = {"A": {}, "B": {}}
    cliente = cp.docs.consumo_cliente.get() if hasattr(cp.docs, "consumo_cliente") else ""
    if avanza:
        avanza("lettura", 0, len(lavori))
    fatte = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(4, len(lavori)))) as pool:
        futuri = {pool.submit(contextvars.copy_context().run, lettore_gemini.scheda_salvata, d["source_path"],
                              profilo, cliente or ""): (n, fasc, d) for n, fasc, d in lavori}
        for futuro in concurrent.futures.as_completed(futuri):
            n, fasc, d = futuri[futuro]
            scheda = futuro.result()
            lettura = lettore_gemini.lettura_da_scheda(scheda, fasc, d.get("id"))
            esiti[n]["letture"][0].extend(lettura)
            esiti[n]["letture"][1].extend(lettura)
            st = struttura["AB"[n]]
            for k in ("forme", "garanzie_accessorie"):
                st.setdefault(k, []).extend(x for x in scheda.get(k) or [] if isinstance(x, dict))
            ec = scheda.get("esclusioni_elenco_chiuso")
            if isinstance(ec, dict) and str(ec.get("si_no", "")).lower() == "si":
                st.setdefault("elenco_chiuso", []).append({**ec, "documento_id": d.get("id")})
            st["_modello"] = scheda.get("_modello", "")
            fatte += 1
            if avanza:
                avanza("lettura", fatte, len(lavori))
    return esiti, struttura


def _segni(profilo: Dict[str, Any], testo: str) -> frozenset:
    return frozenset(k for k, r in (profilo.get("segni_forma") or {}).items() if re.search(r, testo or "", re.I))


def scegli_forma(profilo: Dict[str, Any], struttura: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """Un lato con UNA forma e l'altro con piu' forme (Versilia contro le 4 tariffe di Sara):
    si confronta la forma dell'altro lato con gli stessi segni (capitale costante + premio
    annuo = tar. 242). Se non ce n'e' esattamente una, non si sceglie: resta «piu' forme»."""
    if not profilo.get("segni_forma"):
        return None
    for uno, molti in (("A", "B"), ("B", "A")):
        fu = [f for f in struttura[uno].get("forme") or [] if isinstance(f, dict)]
        fm = [f for f in struttura[molti].get("forme") or [] if isinstance(f, dict)]
        if len(fu) == 1 and len(fm) > 1:
            segno = _segni(profilo, f"{fu[0].get('descrizione', '')} {fu[0].get('codice', '')}")
            uguali = [f for f in fm if segno and _segni(profilo, f"{f.get('descrizione', '')} {f.get('codice', '')}") == segno]
            if len(uguali) == 1:
                return {"lato": molti, "codice": str(uguali[0].get("codice") or ""),
                        "descrizione": str(uguali[0].get("descrizione") or ""),
                        "perche": f"stessa forma di {uno}: {fu[0].get('descrizione', '')}"}
    return None


def _solo_forma(lettura: Dict[str, List[Dict[str, Any]]], codice: str, st: Dict[str, Any]) -> None:
    """Tiene i valori comuni a tutte le forme e quelli della forma scelta; toglie quelli delle
    altre forme (le garanzie accessorie restano: si mostrano)."""
    altri = [str(f.get("codice") or "") for f in st.get("forme") or [] if str(f.get("codice") or "") != codice]
    for cid, cand in lettura.items():
        tenuti = []
        for c in cand:
            forma = c.get("forma") or ""
            if not forma or c.get("accessoria") or codice in forma or not any(a and a in forma for a in altri):
                tenuti.append({**c, "forma": "" if codice in forma and not c.get("accessoria") else forma})
        lettura[cid] = tenuti


def _elenco_chiuso(lato: str, fasc: Dict[str, Any], profilo: Dict[str, Any], st: Dict[str, Any],
                   lettura: Dict[str, List[Dict[str, Any]]]) -> None:
    """«Coperto qualunque sia la causa» + un elenco di esclusioni: un'esclusione del catalogo che
    nell'elenco non c'e' NON e' un'esclusione della polizza. Vale solo se la frase dell'elenco
    chiuso e' verificata sul testo del documento."""
    per_id = {p["id"]: p for p in fasc["pagine"]}
    prova = None
    for ec in st.get("elenco_chiuso") or []:
        voce = {"nome": "elenco chiuso", "valore": "coperto qualunque sia la causa",
                "citazione": str(ec.get("citazione") or "")[:600]}
        numero = str(ec.get("pagina") or "")
        dichiarata = next((p for p in fasc["pagine"] if p.get("documento_id") == ec.get("documento_id")
                           and str(p["label"]).split()[-1] == numero), None)
        pagina, _ = cp._trova_prova(voce, dichiarata, fasc["pagine"])
        if pagina is not None and voce["citazione"]:
            prova = (voce["citazione"], pagina)
            break
    if not prova:
        return
    for c in profilo["concetti"]:
        if c.get("chiave") != "esclusione" or c["tipo"] not in ("si_no", "misura"):
            continue
        principali = [x for x in lettura.get(c["id"], []) if not x.get("accessoria")]
        if principali:
            continue
        if c["tipo"] == "misura" and "se_non_escluso" not in c:
            continue
        normale = "no" if c["tipo"] == "si_no" else float(c["se_non_escluso"])
        lettura.setdefault(c["id"], []).append({
            "nome": c["etichetta"], "valore": "non esclusa: non è nell'elenco chiuso delle esclusioni",
            "citazione": prova[0], "concetto": c["id"], "condizione": "", "forma": "", "normale": normale,
            "deroga": False, "condizionata": False, "con_condizione": False, "accessoria": False,
            "dedotto": True, "pagina": prova[1]["id"], "documento": prova[1]["documento"],
            "label": prova[1]["label"]})


SYSTEM_SINTESI = """Sei {lettore}, consulente di parte. Ti do il confronto GIA' FATTO fra due {documenti}, A ({nome_a}) e B ({nome_b}), concetto per concetto: valori verificati e giudizio deciso con regole dichiarate. Non cambiare i giudizi e non aggiungere confronti.
Scrivi solo:
{"trappole": [{"testo": "una trappola per {per_chi} o un falso confronto, una o due frasi", "voci": ["A-1","B-2"]}],
 "azioni": [{"testo": "cosa deve fare o chiedere {utente}", "voci": ["B-2"]}],
 "sintesi": "tre o quattro frasi"}
Regole: usa SOLO i numeri presenti nei valori; per i concetti «da verificare» o «non trovato» suggerisci di verificarli, non trarre conclusioni; testo semplice, niente markdown."""


def confronta(documenti_a: List[Dict[str, Any]], documenti_b: List[Dict[str, Any]], nome_a: str = "",
              nome_b: str = "", avanza: cp.Avanzamento = None, profilo_nome: str = PROFILO_PREDEFINITO) -> Dict[str, Any]:
    profilo = carica_profilo(profilo_nome)
    concetti = profilo["concetti"]
    fa, fb = cp.fascicolo("A", documenti_a), cp.fascicolo("B", documenti_b)
    for f, nome in ((fa, "A"), (fb, "B")):
        if not f["pagine"]:
            raise cp.HTTPException(422, f"I documenti di {nome} non hanno testo leggibile: forse sono scansioni. "
                                        "Serve il PDF con il testo (o passato da un OCR).")
    scartate: List[Dict[str, Any]] = []
    struttura = {"A": {}, "B": {}}
    avvisi: List[str] = []
    letto = None
    diretto: Optional[Dict[str, Any]] = None
    forma_scelta = None
    if lettore_gemini.attivo() and all(d.get("source_path") for d in documenti_a + documenti_b):
        # 23ott2026: i due lati letti INSIEME, in una chiamata (come una chat con i due PDF);
        # il modello sceglie la forma confrontabile, il codice verifica ogni citazione
        try:
            if avanza:
                avanza("lettura", 0, 1)
            diretto = lettore_gemini.confronto_diretto(
                [d["source_path"] for d in documenti_a], [d["source_path"] for d in documenti_b], profilo,
                cp.docs.consumo_cliente.get() if hasattr(cp.docs, "consumo_cliente") else "")
            if avanza:
                avanza("lettura", 1, 1)
            letto = []
            for lato, fasc, docs_lato in (("A", fa, documenti_a), ("B", fb, documenti_b)):
                lettura = lettore_gemini.lettura_da_confronto(diretto, lato, fasc, docs_lato)
                letto.append({"letture": [lettura, lettura], "non_lette": []})
                struttura[lato] = {"garanzie_accessorie": [{"nome": n} for n in
                                                           lettore_gemini.accessorie_da_confronto(diretto, lato)],
                                   "forme": [diretto[f"forma_{lato.lower()}"]]
                                   if isinstance(diretto.get(f"forma_{lato.lower()}"), dict) else [],
                                   "_modello": diretto.get("_modello", "")}
            for lato in ("A", "B"):
                f = diretto.get(f"forma_{lato.lower()}")
                if isinstance(f, dict) and f.get("ha_piu_forme") and f.get("codice"):
                    forma_scelta = {"lato": lato, "codice": str(f.get("codice"))[:60],
                                    "descrizione": str(f.get("descrizione") or "")[:200],
                                    "perche": str(f.get("perche") or "")[:300]}
                    break
        except Exception as error:
            print(f"[CONFRONTO] lettura con Gemini non riuscita ({type(error).__name__}: {error})")
            motivo = getattr(error, "motivo", "") or "errore imprevisto, dettagli nei log"
            avvisi.append(f"La lettura con Gemini non è riuscita ({motivo}): usata la lettura di riserva.")
            letto, diretto, forma_scelta = None, None, None
            struttura = {"A": {}, "B": {}}
    if letto is None:
        letto = leggi([fa, fb], profilo, avanza)
    lati: Dict[str, Dict[str, Any]] = {}
    for lato, fasc, esito in (("A", fa, letto[0]), ("B", fb, letto[1])):
        st = struttura[lato]
        acc = [g.get("nome", "") for g in st.get("garanzie_accessorie") or [] if isinstance(g, dict)]
        prima = _verifica(lato, fasc, profilo, esito["letture"][0], scartate, 1, acc)
        seconda = _verifica(lato, fasc, profilo, esito["letture"][1], scartate, 2, acc)
        if diretto is not None:
            for lettura in (prima, seconda):
                _non_esclusa_misura(profilo, lettura)
        if diretto is None:
            for lettura in (prima, seconda):
                _elenco_chiuso(lato, fasc, profilo, st, lettura)
                if forma_scelta and forma_scelta["lato"] == lato:
                    _solo_forma(lettura, forma_scelta["codice"], st)
        voci: List[Dict[str, Any]] = []
        stati: Dict[str, Dict[str, Any]] = {}
        ids_per_concetto: Dict[str, List[str]] = {}
        for c in concetti:
            cid = c["id"]
            stati[cid] = valuta_concetto(c, prima.get(cid, []), seconda.get(cid, []))
            mostrate = prima.get(cid) or seconda.get(cid) or []
            for cand in mostrate:
                vid = f"{lato}-{len(voci) + 1}"
                ids_per_concetto.setdefault(cid, []).append(vid)
                valore = cand["valore"] + (f" (se {cand['condizione']})" if cand["condizione"] and
                                           cand["condizione"].lower() not in cand["valore"].lower() else "")
                chiave = c.get("chiave") or "altro"
                if chiave == "esclusione" and c["tipo"] == "si_no" and cand["normale"] == "no":
                    chiave = "altro"          # non escluso: non conta fra le esclusioni del quadro
                voci.append({"id": vid, "chiave": chiave, "concetto": cid,
                             "nome": c["etichetta"] + (f" · {cand['forma']}" if cand["forma"] else ""),
                             "valore": valore[:300], "citazione": cand["citazione"], "deroga": cand["deroga"],
                             "condizionata": cand["condizionata"], "superata": False, "modificata_da": [],
                             "inclusa": (cand["normale"] == c.get("meglio")) if c["tipo"] == "si_no"
                             and c.get("meglio") == "si" else None,
                             "pagina": cand["pagina"], "documento": cand["documento"], "label": cand["label"],
                             "stato_concetto": stati[cid]["stato"]})
        lati[lato] = {"fasc": fasc, "voci": voci, "stati": stati, "ids": ids_per_concetto,
                      "non_lette": esito["non_lette"]}
    righe: List[Dict[str, Any]] = []
    for c in concetti:
        cid = c["id"]
        sa, sb = lati["A"]["stati"][cid], lati["B"]["stati"][cid]
        if sa["stato"] == "non_trovato" and sb["stato"] == "non_trovato":
            continue
        righe.append({"voce": c["etichetta"], "concetto": cid, "a": lati["A"]["ids"].get(cid, []),
                      "b": lati["B"]["ids"].get(cid, []), "giudizio": "", "perche": "",
                      "stato_a": sa["stato"], "stato_b": sb["stato"]})
    voci_a, voci_b = lati["A"]["voci"], lati["B"]["voci"]
    t = testi(profilo)
    nome_a = (nome_a or f"{t['lato'].capitalize()} A").strip()[:80]
    nome_b = (nome_b or f"{t['lato'].capitalize()} B").strip()[:80]
    # giudizi, misure e prezzo unitario: gli stessi calcoli che rivedi() rifa' alla lettura
    prova = {"profilo": {"nome": profilo["nome"]}, "righe": righe,
             "a": {"voci": voci_a, "concetti": lati["A"]["stati"]}, "b": {"voci": voci_b, "concetti": lati["B"]["stati"]}}
    rivedi(prova)
    base_a, base_b = prova["a"]["base_premio"], prova["b"]["base_premio"]
    ammessi = cp._numeri_ammessi(voci_a, voci_b, base_a, base_b)
    trappole, azioni, sintesi = [], [], ""
    if diretto is not None:
        # trappole e azioni scritte da chi ha letto i due documenti; i numeri passano lo stesso
        # filtro: un numero che non e' nelle voci verificate non arriva al cliente
        trappole, azioni, sintesi = _liste_filtrate(diretto, voci_a + voci_b, ammessi, scartate)
    if not trappole and not azioni:
        trappole, azioni, sintesi = _sintesi(profilo, righe, voci_a, voci_b, nome_a, nome_b, ammessi, scartate, avanza)
    for lato, fasc in (("A", fa), ("B", fb)):
        fasc["senza_testo"] = fasc["senza_testo"] + [p + " (non letta dal modello)" for p in lati[lato]["non_lette"]]

    def blocco(lato: str, nome: str, documenti: List[Dict[str, Any]], base: Dict[str, Any]) -> Dict[str, Any]:
        fasc, stati = lati[lato]["fasc"], lati[lato]["stati"]
        pagine = ", ".join(f"{p['documento']} {p['label']}" for p in fasc["pagine"]) or "nessuna pagina con testo"
        return {"nome": nome, "ramo": profilo["titolo"], "prodotto": "",
                "documenti": [{"id": d.get("id"), "titolo": d.get("titolo")} for d in documenti],
                "voci": lati[lato]["voci"], "base_premio": base,
                "non_trovate": [{"chiave": c["id"], "voce": c["etichetta"],
                                 "testo": f"Non l'ho trovato. Ho cercato in: {pagine}."}
                                for c in concetti if c.get("peso", 1) >= 3 and stati[c["id"]]["stato"] == "non_trovato"],
                "senza_testo": fasc["senza_testo"], "pagine": len(fasc["pagine"]),
                "pagine_deroga": [p["id"] for p in fasc["pagine"] if p["deroga"]],
                "concetti": {cid: s for cid, s in stati.items()}}

    risultato = {"versione": 3, "modo": "concetti", "profilo": {"nome": profilo["nome"], "titolo": profilo["titolo"],
                                                               "versione": profilo["versione"], "stato": profilo.get("stato")},
                 "creato": int(cp.time.time()),
                 "a": blocco("A", nome_a, documenti_a, base_a), "b": blocco("B", nome_b, documenti_b, base_b),
                 "righe": righe, "trappole": trappole, "azioni": azioni, "sintesi": sintesi,
                 "altre_differenze": _altre_differenze(diretto, fa, fb, documenti_a, documenti_b, scartate)
                 if diretto is not None else [],
                 "lettura": {"modo": "gemini" if struttura["A"] or struttura["B"] else "testo",
                             "modello": (struttura["A"] or struttura["B"]).get("_modello", ""),
                             "forma_scelta": forma_scelta, "avvisi": avvisi,
                             "forme": {k: [f for f in (v.get("forme") or []) if isinstance(f, dict)]
                                       for k, v in struttura.items()}},
                 "controlli": {"scartate": scartate, "verificate": len(voci_a) + len(voci_b),
                               "da_verificare": sum(1 for r in righe if r["giudizio"] == "non_confrontata")}}
    return cp.rivedi(risultato)


def _non_esclusa_misura(profilo: Dict[str, Any], lettura: Dict[str, List[Dict[str, Any]]]) -> None:
    """«Immersione: non esclusa» (fuori dall'elenco chiuso) vale come la misura massima del
    catalogo (se_non_escluso), come faceva la lettura per concetti."""
    for c in profilo["concetti"]:
        if c["tipo"] != "misura" or "se_non_escluso" not in c:
            continue
        for cand in lettura.get(c["id"], []):
            if cand.get("normale") is None and re.search(r"non\s+esclus", cand.get("valore") or "", re.I):
                cand["normale"] = float(c["se_non_escluso"])


def _altre_differenze(diretto: Dict[str, Any], fa: Dict[str, Any], fb: Dict[str, Any],
                      documenti_a: List[Dict[str, Any]], documenti_b: List[Dict[str, Any]],
                      scartate: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Le differenze che il modello ha trovato FUORI dalla lista dei concetti (come una chat),
    con la stessa verifica: ogni lato entra solo se la frase e' davvero su quella pagina e i
    numeri del valore stanno nella frase. Una differenza senza nessun lato verificato non entra."""
    out = []
    for n, d in enumerate(diretto.get("altre_differenze") or []):
        if not isinstance(d, dict) or not str(d.get("voce") or "").strip():
            continue
        voce = str(d["voce"]).strip()[:120]
        finta = {"concetti": [{"id": "x", "A": d.get("A"), "B": d.get("B")}]}
        lati: Dict[str, Optional[Dict[str, Any]]] = {"a": None, "b": None}
        for lato, fasc, docs_lato in (("A", fa, documenti_a), ("B", fb, documenti_b)):
            for v in lettore_gemini.lettura_da_confronto(finta, lato, fasc, docs_lato):
                prova = {"nome": voce, "valore": str(v["valore"])[:300], "citazione": str(v["citazione"])[:600]}
                if not prova["valore"] or not prova["citazione"]:
                    continue
                per_id = {p["id"]: p for p in fasc["pagine"]}
                pagina, riga = cp._trova_prova(prova, per_id.get(str(v.get("pagina") or "")), fasc["pagine"])
                mancanti = _numeri_senza_prova(prova["valore"], None, riga or prova["citazione"], v.get("condizione") or "")
                if pagina is None or mancanti:
                    scartate.append({**prova, "lato": lato, "concetto": "altra differenza",
                                     "motivo": "citazione non trovata nel fascicolo" if pagina is None else
                                     "numeri non presenti nella citazione: " + ", ".join(sorted(mancanti))})
                    continue
                cond = str(v.get("condizione") or "").strip()
                lati[lato.lower()] = {"valore": prova["valore"] + (f" ({cond})" if cond else ""),
                                      "citazione": riga or prova["citazione"],
                                      "documento": pagina["documento"], "label": pagina["label"]}
        if lati["a"] or lati["b"]:
            out.append({"voce": voce, "perche": str(d.get("perche") or "").strip()[:400], **lati})
    return out


def _liste_filtrate(grezzo: Dict[str, Any], voci: List[Dict[str, Any]], ammessi: Any,
                    scartate: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    liste: Dict[str, List[Dict[str, Any]]] = {"trappole": [], "azioni": []}
    for nome in liste:
        for t in grezzo.get(nome) or []:
            testo = str((t.get("testo") if isinstance(t, dict) else t) or "").strip()[:700]
            if not testo:
                continue
            if not ammessi.ok(testo):
                scartate.append({"dove": nome, "testo": testo,
                                 "motivo": "numeri non presenti nelle voci: " + ", ".join(ammessi.fuori(testo))})
                continue
            liste[nome].append({"testo": testo, "voci": []})
    sintesi = str(grezzo.get("sintesi") or "").strip()[:1500]
    if sintesi and not ammessi.ok(sintesi):
        scartate.append({"dove": "sintesi", "testo": sintesi,
                         "motivo": "numeri non presenti nelle voci: " + ", ".join(ammessi.fuori(sintesi))})
        sintesi = ""
    return liste["trappole"], liste["azioni"], sintesi


def _sintesi(profilo: Dict[str, Any], righe: List[Dict[str, Any]], voci_a: List[Dict[str, Any]],
             voci_b: List[Dict[str, Any]], nome_a: str, nome_b: str, ammessi: Any,
             scartate: List[Dict[str, Any]], avanza: cp.Avanzamento) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    voci = {v["id"]: v for v in voci_a + voci_b}
    tabella = [{"concetto": r["voce"], "giudizio": r["giudizio"], "perche": r["perche"],
                "A": [{"id": i, "valore": voci[i]["valore"]} for i in r["a"]],
                "B": [{"id": i, "valore": voci[i]["valore"]} for i in r["b"]]} for r in righe]
    if avanza:
        avanza("giudizio", 0, 1)
    sistema = SYSTEM_SINTESI.replace("{nome_a}", nome_a).replace("{nome_b}", nome_b)
    for campo in ("lettore", "documenti", "per_chi", "utente"):
        sistema = sistema.replace("{" + campo + "}", _p(profilo, campo))
    try:
        grezzo = _chiedi(sistema, json.dumps({"confronto": tabella}, ensure_ascii=False), 0.0)
    except RuntimeError:
        grezzo = {}
    if avanza:
        avanza("giudizio", 1, 1)
    liste: Dict[str, List[Dict[str, Any]]] = {"trappole": [], "azioni": []}
    for nome in liste:
        for t in grezzo.get(nome) or []:
            if not isinstance(t, dict):
                continue
            testo = str(t.get("testo") or "").strip()[:700]
            if not testo:
                continue
            if not ammessi.ok(testo):
                scartate.append({"dove": nome, "testo": testo,
                                 "motivo": "numeri non presenti nelle voci: " + ", ".join(ammessi.fuori(testo))})
                continue
            liste[nome].append({"testo": testo, "voci": [i for i in (t.get("voci") or []) if i in voci]})
    sintesi = str(grezzo.get("sintesi") or "").strip()[:1500]
    if sintesi and not ammessi.ok(sintesi):
        scartate.append({"dove": "sintesi", "testo": sintesi,
                         "motivo": "numeri non presenti nelle voci: " + ", ".join(ammessi.fuori(sintesi))})
        sintesi = ""
    return liste["trappole"], liste["azioni"], sintesi
