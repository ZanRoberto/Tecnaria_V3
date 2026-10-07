# -*- coding: utf-8 -*-
"""IL LESSICO DEL DOCUMENTO — un posto solo dove si decide se due parole si corrispondono.

PERCHE' ESISTE QUESTO FILE
==========================
Il 7 ottobre 2026, misurando il motore su LAGO ELEMENTS, sono emersi sette
difetti. Sei hanno la stessa forma:

    "55,2 x 36,8" letto come larghezza x profondita'   (era larghezza x altezza)
    "optionals" che premia un accessorio               (parola troppo generica)
    "laccata" che non trova "Laccato Lacquered"        (genere dell'aggettivo)
    "mi bastano 3.500 euro" non riconosciuto           (forma della dichiarazione)
    "tondo" che non trova "rotondo"                    (il catalogo usa un'altra parola)
    "madia" che non trova "sideboard"                  (il catalogo e' in inglese)

Nessuno e' un errore di logica. Sono tutti lo stesso errore: una parola della
domanda confrontata con una parola del documento, e il confronto fallisce.

La causa non e' in nessuno di quei sei punti: e' che il motore NON AVEVA un
componente che legge la domanda. Ne aveva sette che la rileggevano ognuno per
conto suo - plan_request, requested_price_labels, exact_dimension_requests,
requested_budget, derived_title_tokens, verified_dimension_candidates,
vincoli_senza_riscontro - ognuno con la sua normalizzazione privata. Per questo
ogni correzione ne sistemava uno solo e gli altri restavano rotti.

Qui dentro ci sono TUTTE le regole di corrispondenza, e una volta sola.

COSA NON C'E' QUI DENTRO, E NON CI DEVE ENTRARE
===============================================
Nessuna parola di questo catalogo. Nessun elenco di finiture, di tipi di
prodotto, di sinonimi commerciali. Il lessico si COSTRUISCE dal documento che
gli viene dato: un documento nuovo produce un lessico nuovo, senza che nessuno
scriva una riga. E' l'unico modo perche' Gessi, i cataloghi cucine e bagni e un
contratto assicurativo funzionino senza rincorrere ogni volta.

Le uniche regole scritte a mano sono regole della LINGUA, non del prodotto:
che in italiano l'aggettivo cambia la vocale finale, e che una parola puo'
essere pezzo di un'altra. Valgono su qualsiasi documento italiano.

COME SI MISURA
==============
    python3 lessico.py            misura il lessico sul catalogo caricato

Ogni soglia qui dentro e' misurata, non scelta: il file stesso stampa i numeri
su cui e' stata decisa.
"""
from collections import Counter, defaultdict
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

# ============================================================
# REGOLE DELLA LINGUA — non del prodotto
# ============================================================
# Queste due sono le sole cose scritte a mano in tutto il file, e non parlano di
# mobili: parlano di italiano. Restano valide su un catalogo di rubinetti o su
# un contratto di assicurazione.

RADICE_LUNGHEZZA_MINIMA = 5      # sotto, togliere una vocale fa danni: "aria" -> "ari"
VOCALI_FINALI = "aeio"


def radice(parola: str) -> str:
    """Toglie la vocale finale alle parole lunghe: e' li' che l'italiano segna
    genere e numero.

    laccato / laccata / laccate -> laccat
    opaco   / opaca             -> opac
    vetro   / vetri             -> vetr

    Misurato il 7ott2026 sui 65 casi della batteria LAGO. Senza questo, "una
    madia laccata" non agganciava la colonna "Laccato Lacquered": zero etichette
    invece di tre.

        casi che nominano una finitura .......... 31 / 65
        con la forma scritta diversa dall'etichetta .. 5
        di questi, falliti per questa ragione ........ 1   (D04)

    Non e' un difetto che bruciava il motore: in quattro casi su cinque la
    risposta restava giusta, perche' la finitura e' scritta anche nel testo
    della pagina e il modello la legge da li'. Ha morso dove serviva un conto -
    il confronto col budget - che non si arrangia leggendo.

    Si tiene perche' toglie una classe intera a costo zero, non perche' quella
    classe fosse grande. La misura sopra e' il motivo per cui NON va raccontata
    come sistematica.
    """
    parola = (parola or "").strip().lower()
    if len(parola) >= RADICE_LUNGHEZZA_MINIMA and parola[-1:] in VOCALI_FINALI:
        return parola[:-1]
    return parola


SOTTOSTRINGA_LUNGHEZZA_MINIMA = 5   # "tondo" si', "base" no
SOTTOSTRINGA_MARGINE = 3            # "tondo" dentro "rotondo" si', dentro "rotondamente" no


def _e_pezzo_di(parola: str, altra: str) -> bool:
    """La parola e' un pezzo riconoscibile di un'altra parola del documento."""
    return (len(parola) >= SOTTOSTRINGA_LUNGHEZZA_MINIMA
            and parola != altra
            and parola in altra
            and len(altra) <= len(parola) + SOTTOSTRINGA_MARGINE)


# ============================================================
# IL LESSICO
# ============================================================

class Lessico:
    """Tutte le parole di un documento, con tutto quello che serve per sapere se
    una parola della domanda corrisponde a una del documento.

    Si costruisce una volta per cliente, al caricamento dell'indice. Costruirlo
    costa un giro sulle schede; interrogarlo non costa niente.

    Argomenti:
      parole_di_scheda  funzione scheda -> insieme di parole (titolo, colonne,
                        descrizione). La fornisce il motore: il lessico non sa
                        com'e' fatta una scheda.
      schede            tutte le schede del documento
      parole_di_pagina  funzione pagina -> insieme di parole
      pagine            tutte le pagine del documento
      rese              funzione parola -> insieme di rese nell'altra lingua del
                        documento, ricavate dalle righe bilingui. Facoltativa.
    """

    def __init__(self,
                 schede: Iterable,
                 parole_di_scheda: Callable,
                 pagine: Iterable = (),
                 parole_di_pagina: Optional[Callable] = None,
                 rese: Optional[Callable] = None):
        self._rese_grezze = rese or (lambda _: set())

        self.frequenza: Counter = Counter()
        self.schede_totali = 0
        for scheda in schede:
            self.schede_totali += 1
            self.frequenza.update(set(parole_di_scheda(scheda) or ()))

        self.nel_documento: Set[str] = set()
        if parole_di_pagina is not None:
            for pagina in pagine:
                self.nel_documento |= set(parole_di_pagina(pagina) or ())
        self.nel_documento |= set(self.frequenza)

        # indice per radice: laccat -> {laccato, laccata, laccate}
        self.per_radice: Dict[str, Set[str]] = defaultdict(set)
        for parola in self.frequenza:
            self.per_radice[radice(parola)].add(parola)

    # --- quanto una parola distingue -------------------------------------

    def quota(self, parola: str) -> float:
        """Su quale frazione delle schede compare. Zero se non compare."""
        if not self.schede_totali:
            return 0.0
        return self.frequenza.get(parola, 0) / self.schede_totali

    QUOTA_GENERICA = 0.10

    def generica(self, parola: str) -> bool:
        """Una parola che sta su un decimo del catalogo non distingue niente.

        Misurato il 7ott2026: la domanda "qual e' la finitura piu' economica"
        produceva, attraverso le righe bilingui, la resa "optionals" (14,4% delle
        schede) e "optional" (25,5%). Quel bonus metteva un set di vetri da 92
        euro davanti al comodino da 777. Una soglia sulla parola di PARTENZA non
        serviva - "finitura" sta sul 6,4% e "tavolo" sul 4,2%, non si distinguono
        - la soglia giusta e' sull'ARRIVO.

        La soglia si misura sul documento: 'generiche()' le elenca tutte, e su
        LAGO ne vengono fuori poche decine, tutte parole di servizio.
        """
        return self.quota(parola) > self.QUOTA_GENERICA

    def generiche(self) -> List[Tuple[str, float]]:
        """Le parole che il documento usa cosi' tanto da non distinguere niente."""
        return sorted(((p, self.quota(p)) for p in self.frequenza
                       if self.quota(p) > self.QUOTA_GENERICA),
                      key=lambda x: -x[1])

    # --- corrispondenze ---------------------------------------------------

    def varianti(self, parola: str) -> Set[str]:
        """Le forme della stessa parola che il documento usa davvero.
        laccata -> {laccato, laccate, laccati}"""
        return set(self.per_radice.get(radice(parola), ()))

    def contenenti(self, parola: str) -> Set[str]:
        """Parole del documento di cui questa e' un pezzo. tondo -> {rotondo}

        Serve quando il cliente usa una parola che il catalogo non scrive mai:
        "tondo" non compare in nessuna scheda di LAGO, "rotondo" in 47. Non e'
        un sinonimo scritto a mano: e' una relazione fra due parole, e una delle
        due sta nel documento.
        """
        if len(parola) < SOTTOSTRINGA_LUNGHEZZA_MINIMA:
            return set()
        return {w for w in self.frequenza if _e_pezzo_di(parola, w)}

    def rese(self, parola: str) -> Set[str]:
        """Le rese nell'altra lingua del documento, senza quelle generiche.

        Il filtro sulla genericita' sta QUI e non da nessun'altra parte: chi
        chiede le rese le riceve gia' pulite, e non puo' dimenticarsi di
        filtrarle - che e' esattamente l'errore misurato su "optionals".
        """
        return {r for r in (self._rese_grezze(parola) or set())
                if r != parola and not self.generica(r)}

    def corrispondenze(self, parola: str) -> Set[str]:
        """TUTTE le parole del documento che valgono come questa.

        E' l'unica funzione che il resto del motore deve chiamare. Dentro ci
        sono, in ordine di forza: la parola stessa, le sue forme flesse, le
        parole di cui e' un pezzo, le rese nell'altra lingua.
        """
        trovate = {parola} if parola in self.frequenza else set()
        trovate |= self.varianti(parola)
        trovate |= self.contenenti(parola)
        trovate |= self.rese(parola)
        return trovate

    def copre(self, parola: str, insieme: Iterable[str]) -> bool:
        """La parola trova riscontro in questo insieme di parole?

        E' il test che prima stava scritto in sette posti diversi, ognuno con la
        sua versione incompleta.
        """
        insieme = set(insieme or ())
        if parola in insieme:
            return True
        return bool(self.corrispondenze(parola) & insieme)

    # --- assi delle misure -------------------------------------------------

    def legge_la_coppia(self, primo: float, secondo: float,
                        valori_per_asse: Callable[[str], Iterable[Tuple[float, float]]],
                        assi: Tuple[str, ...] = ("depth", "height"),
                        tolleranza: float = 0.051) -> Optional[List[Tuple[str, float]]]:
        """Due numeri senza parole d'asse: "55,2 x 36,8". Decide il documento.

        Il catalogo stampa le colonne come larghezza, profondita', altezza, e
        leggere la coppia in quell'ordine sembra ovvio. Non lo e': un
        rivenditore che scrive "comodino 55,2 x 36,8" intende larghezza per
        ALTEZZA, perche' quel comodino e' profondo 40,6. Misurato il 7ott2026 su
        cinque coppie prese dalla batteria: in tutte e cinque una lettura esiste
        nel documento e l'altra e' vuota.

            55,2  x 36,8    profondita':  0 schede    altezza: 17 schede
            184   x 40,6    profondita': 92 schede    altezza:  0 schede
            110,4 x 56      profondita': 12 schede    altezza:  0 schede
            220,8 x 78,7    profondita':  0 schede    altezza:  5 schede
            147,2 x 75,3    profondita':  0 schede    altezza: 54 schede

        Se il documento conferma entrambe le letture e' davvero ambiguo e si
        restituisce None: decide il chiamante, che conosce l'ordine stampato.
        Se non ne conferma nessuna, None: una misura non riconosciuta non deve
        far sparire il vincolo.
        """
        conferme = {}
        for asse in assi:
            conferme[asse] = sum(
                1 for larghezze, altre in valori_per_asse(asse)
                if any(abs(v - primo) <= tolleranza for v in larghezze)
                and any(abs(v - secondo) <= tolleranza for v in altre))
        vive = [a for a in assi if conferme[a]]
        if len(vive) != 1:
            return None
        return [("width", primo), (vive[0], secondo)]

    # --- riepilogo ---------------------------------------------------------

    def riepilogo(self) -> Dict[str, object]:
        gen = self.generiche()
        return {
            "schede": self.schede_totali,
            "parole distinte sulle schede": len(self.frequenza),
            "parole nel documento": len(self.nel_documento),
            "radici distinte": len(self.per_radice),
            "parole generiche (oltre il 10%)": len(gen),
            "le piu' generiche": [f"{p} {q:.0%}" for p, q in gen[:8]],
        }


# ============================================================
# MISURA — si lancia da sola
# ============================================================

def _misura() -> None:
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    os.environ.setdefault("DEEPSEEK_API_KEY", "misura")
    import app                                              # noqa: E402

    lessico = Lessico(
        schede=[c for g in app.PRODUCT_CARDS.values() for c in g],
        parole_di_scheda=app._parole_di_scheda,
        pagine=app.DOCUMENT_PAGES,
        parole_di_pagina=lambda p: p.get("token_set") or set(),
        rese=app.synonyms_for,
    )
    print("\n--- LESSICO DEL DOCUMENTO CARICATO ---------------------------")
    for chiave, valore in lessico.riepilogo().items():
        print(f"  {chiave:34} {valore}")

    print("\n--- CORRISPONDENZE: la domanda contro il catalogo ------------")
    for parola in ("laccata", "tondo", "madia", "comodino", "opaca", "gambe",
                   "legno", "marmo", "finitura", "scrivania"):
        c = sorted(lessico.corrispondenze(parola))
        print(f"  {parola:12} -> {c[:6]}{' ...' if len(c) > 6 else ''}")

    print("\n--- LETTURA DELLE COPPIE DI MISURE ---------------------------")
    def valori(asse):
        for gruppi in app.PRODUCT_CARDS.values():
            for card in gruppi:
                yield (app.card_dimension_values(card, 'width') or [],
                       app.card_dimension_values(card, asse) or [])
    for a, b in ((55.2, 36.8), (184.0, 40.6), (110.4, 56.0), (220.8, 78.7), (147.2, 75.3)):
        print(f"  {a} x {b:<6} -> {lessico.legge_la_coppia(a, b, valori)}")
    print()


if __name__ == "__main__":
    _misura()
