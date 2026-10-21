"""Dalla telefonata all'intervento: il messaggio per il tecnico preparato da solo.

Il Narratore dice CHE COSA e' stato fissato (quando, cosa, chi ha chiamato). Questo modulo
trova IL CLIENTE nell'anagrafica clienti dell'azienda (un Excel o CSV caricato nei
documenti) e ne prende i dati veri: indirizzo, note di accesso, tecnico di zona.

Regole (20ott2026):
1. L'indirizzo NON si prende mai dalla trascrizione (un civico sentito male manda il
   tecnico nel posto sbagliato): solo dalla riga dell'anagrafica.
2. Il cliente si riconosce senza modello, con regole dichiarate, e si dice QUANTO e' sicuro:
   - "certo": il telefono combacia, oppure nome e cognome (o ragione sociale) interi
     combaciano con una sola riga;
   - "da_scegliere": piu' righe possibili, si mostrano tutte;
   - "non_trovato": nessuna riga; l'indirizzo resta da scrivere.
3. Il tecnico e' quello di zona scritto nell'anagrafica, ma il messaggio parte solo verso
   una PERSONA registrata dell'azienda (numero verificato), mai verso un numero letto da
   un documento.
4. Niente parte da solo: il riquadro arriva compilato e serve un tocco su «Invia».
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Tuple

# le colonne si riconoscono dal nome (forme finite), non dalla posizione
COLONNE = {
    "codice": ("codice cliente", "codice", "cod. cliente", "id cliente"),
    "nome": ("cliente", "ragione sociale", "denominazione", "nominativo", "nome cliente", "nome"),
    "referente": ("referente", "contatto", "persona di riferimento"),
    "indirizzo": ("indirizzo", "via", "indirizzo di intervento", "sede"),
    "cap": ("cap",),
    "comune": ("comune", "citta", "città", "localita", "località"),
    "provincia": ("prov.", "prov", "provincia"),
    "telefono": ("telefono", "tel", "tel.", "fisso"),
    "whatsapp": ("whatsapp", "cellulare", "cell", "cell.", "mobile"),
    "note": ("accesso e note", "note", "note di accesso", "accesso"),
    "tecnico": ("tecnico di zona", "tecnico", "manutentore"),
    "impianto": ("impianto", "apparecchio", "modello"),
}
_PAROLE_VUOTE = {"srl", "snc", "spa", "sas", "srls", "dott", "dottssa", "dott.ssa", "sig", "sigra", "ing",
                 "arch", "studio", "il", "la", "di", "de", "del", "della", "e"}


def _piano(testo: str) -> str:
    testo = unicodedata.normalize("NFD", str(testo or "").lower())
    testo = "".join(c for c in testo if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", testo)


def _parole(testo: str) -> set:
    return {p for p in _piano(testo).split() if len(p) > 1 and p not in _PAROLE_VUOTE}


def _cifre(testo: str) -> str:
    return re.sub(r"\D", "", str(testo or ""))[-9:]


def _colonne(intestazione: List[str]) -> Dict[str, int]:
    trovate: Dict[str, int] = {}
    for i, nome in enumerate(intestazione):
        pulito = _piano(nome).strip()
        for chiave, sinonimi in COLONNE.items():
            if chiave not in trovate and pulito in {_piano(s).strip() for s in sinonimi}:
                trovate[chiave] = i
                break
    return trovate


def righe_anagrafica(pagine: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Le righe di ogni tabella che ha almeno un nome e un indirizzo, con la fonte."""
    out: List[Dict[str, Any]] = []
    visti = set()
    for pagina in pagine:
        righe = str(pagina.get("text") or "").split("\n")
        if len(righe) < 2 or "\t" not in righe[0]:
            continue
        col = _colonne(righe[0].split("\t"))
        if "nome" not in col or "indirizzo" not in col:
            continue
        for riga in righe[1:]:
            celle = riga.split("\t")
            voce = {k: (celle[i].strip() if i < len(celle) else "") for k, i in col.items()}
            if not voce.get("nome") or not voce.get("indirizzo"):
                continue
            chiave = (voce.get("codice"), voce["nome"], voce["indirizzo"])
            if chiave in visti:
                continue
            visti.add(chiave)
            voce["fonte"] = (pagina.get("scheda") or {}).get("titolo") or ""
            out.append(voce)
    return out


def indirizzo_completo(voce: Dict[str, Any]) -> str:
    parti = [voce.get("indirizzo", "")]
    luogo = " ".join(x for x in (voce.get("cap", ""), voce.get("comune", "")) if x)
    if luogo:
        parti.append(luogo)
    if voce.get("provincia"):
        parti[-1] = parti[-1] + f" ({voce['provincia']})"
    return ", ".join(p for p in parti if p)


def trova_cliente(righe: List[Dict[str, Any]], nome: str = "", telefono: str = "",
                  indirizzo: str = "") -> Dict[str, Any]:
    """{"stato": certo|da_scegliere|non_trovato, "motivo", "candidati": [...]}"""
    tel = _cifre(telefono)
    if len(tel) >= 6:
        per_tel = [r for r in righe if tel in (_cifre(r.get("telefono")), _cifre(r.get("whatsapp")))]
        if len(per_tel) == 1:
            return {"stato": "certo", "motivo": "telefono", "candidati": per_tel}
        if per_tel:
            return {"stato": "da_scegliere", "motivo": "telefono di più clienti", "candidati": per_tel}
    cercate = _parole(nome)
    via = _parole(indirizzo)
    if not cercate and not via:
        return {"stato": "non_trovato", "motivo": "nessun nome nella telefonata", "candidati": []}
    punteggi: List[Tuple[int, Dict[str, Any]]] = []
    for r in righe:
        punti = 0
        for campo in ("nome", "referente"):
            parole = _parole(r.get(campo, ""))
            if cercate and parole and cercate == parole:
                punti = max(punti, 3)                     # nome intero, uguale
            elif cercate and parole and cercate <= parole and len(cercate) >= 2:
                punti = max(punti, 2)                     # tutte le parole dette ci sono
            elif cercate and parole and cercate & parole:
                punti = max(punti, 1)                     # solo una parte (es. il cognome)
        if via and via <= _parole(r.get("indirizzo", "") + " " + r.get("comune", "")):
            punti += 2                                    # l'indirizzo detto conferma
        if punti:
            punteggi.append((punti, r))
    if not punteggi:
        return {"stato": "non_trovato", "motivo": "non c'è nell'anagrafica", "candidati": []}
    migliore = max(p for p, _ in punteggi)
    primi = [r for p, r in punteggi if p == migliore]
    if migliore >= 3 and len(primi) == 1:
        return {"stato": "certo", "motivo": "nome", "candidati": primi}
    candidati = [r for _, r in sorted(punteggi, key=lambda x: -x[0])][:5]
    return {"stato": "da_scegliere", "motivo": "nome simile a più clienti" if len(primi) > 1
            else "nome non completo", "candidati": candidati}


def tecnico_tra_le_persone(nome_tecnico: str, persone: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    cercate = _parole(nome_tecnico)
    if not cercate:
        return None
    for p in persone:
        if not p.get("sospeso") and p.get("telefono") and _parole(p.get("nome", "")) == cercate:
            return p
    return None


def prepara(intervento: Dict[str, Any], righe: List[Dict[str, Any]],
            persone: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Il riquadro pronto: cliente trovato (o da scegliere), indirizzo vero, tecnico, note."""
    esito = trova_cliente(righe, intervento.get("cliente_nome", ""),
                          intervento.get("cliente_telefono", ""), intervento.get("indirizzo_detto", ""))
    note = [n for n in (intervento.get("note") or "",) if n]
    proposta: Dict[str, Any] = {"quando": intervento.get("quando", ""),
                                "quando_da_confermare": bool(intervento.get("quando_relativo")),
                                "attivita": intervento.get("attivita", ""),
                                "esito_cliente": esito["stato"], "motivo": esito["motivo"],
                                "candidati": [dict(c, indirizzo_completo=indirizzo_completo(c))
                                              for c in esito["candidati"]],
                                "cliente": "", "indirizzo": "", "tecnico": None, "tecnico_zona": "",
                                "note": ""}
    if esito["stato"] == "certo":
        c = esito["candidati"][0]
        proposta["cliente"] = c.get("nome", "")
        proposta["indirizzo"] = indirizzo_completo(c)
        proposta["tecnico_zona"] = c.get("tecnico", "")
        if c.get("note"):
            note.append(c["note"])
        trovato = tecnico_tra_le_persone(c.get("tecnico", ""), persone)
        if trovato:
            proposta["tecnico"] = {"id": trovato["id"], "nome": trovato["nome"]}
    uniche: Dict[str, str] = {}
    for n in note:                       # «puntualità tassativa» detta e scritta: una volta sola
        n = n.strip()
        if n and not any(_piano(n).strip() in _piano(u) or _piano(u).strip() in _piano(n) for u in uniche.values()):
            uniche[_piano(n)] = n
    proposta["note"] = " — ".join(uniche.values())[:500]
    return proposta
