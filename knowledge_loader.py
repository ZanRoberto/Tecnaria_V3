# -*- coding: utf-8 -*-
"""
knowledge_loader.py
-------------------
Carica i dati interni da `static/data/tecnaria_connettori_dati.json` e
fornisce utility per:
- cercare un connettore/prodotto per nome o query,
- costruire una "nota tecnica" pronta da appendere sotto la risposta di ChatGPT,
- arricchire automaticamente una risposta con le note tecniche quando disponibili.

Dipendenze: solo libreria standard.
Posizionare questo file nella root del progetto `Tecnaria_V3/` oppure in un package importabile.
"""

from __future__ import annotations
from pathlib import Path
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

# Percorso di default del JSON (relativo alla posizione di questo file)
_BASE_DIR = Path(__file__).resolve().parent
_DEFAULT_JSON_PATH = _BASE_DIR / "static" / "data" / "tecnaria_connettori_dati.json"

# Cache semplice con controllo su mtime per ricaricare se il file cambia
_CACHE: Dict[str, Any] = {"data": None, "path": None, "mtime": None}


def _normalize(s: str) -> str:
    """Normalizza stringhe per il matching: minuscole, rimozione spazi/punteggiatura."""
    s = s.lower()
    s = s.replace("ø", "o")
    s = s.replace("Ø", "o")
    # Unifica separatori tipici (spazi, slash)
    s = s.replace("/", "")
    s = re.sub(r"[\W_]+", "", s, flags=re.UNICODE)
    return s


def _tokenize(s: str) -> List[str]:
    """Tokenizza in parole utili al matching (senza caratteri speciali)."""
    s = s.lower()
    # Mantieni slash per distinguere es. 12/40 durante tokenizzazione, poi li separo
    s = re.sub(r"[^\w/]+", " ", s, flags=re.UNICODE)
    parts = []
    for tok in s.split():
        if "/" in tok:
            parts.extend(tok.split("/"))
        else:
            parts.append(tok)
    # pulizia
    parts = [re.sub(r"\W+", "", p) for p in parts if p]
    return parts


def load_connettori_data(json_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Carica e restituisce il dict del JSON dei connettori.
    Usa una cache con invalidazione su mtime del file.
    """
    path = Path(json_path) if json_path else _DEFAULT_JSON_PATH
    if not path.exists():
        raise FileNotFoundError(f"File JSON non trovato: {path}")

    mtime = path.stat().st_mtime
    if _CACHE["data"] is None or _CACHE["path"] != str(path) or _CACHE["mtime"] != mtime:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        _CACHE.update({"data": data, "path": str(path), "mtime": mtime})
    return _CACHE["data"]


def _score_candidate(query_tokens: List[str], name: str) -> float:
    """
    Scoring semplice per il matching:
    - overlap di token,
    - bonus per match esatto normalizzato.
    """
    name_tokens = _tokenize(name)
    overlap = len(set(query_tokens) & set(name_tokens))
    exact_bonus = 1.0 if _normalize(" ".join(query_tokens)) == _normalize(name) else 0.0
    # Bonus per coppie "sigla + numero" tipiche: CTF, CTL, GTS e altezze/diametri
    siglas = {"ctf", "ctl", "gts", "vcem", "vceme", "ctcem", "minicem", "nanoceme", "diapason", "omega"}
    bonus = 0.0
    if any(s in query_tokens for s in siglas) and any(t.isdigit() for t in query_tokens):
        bonus += 0.5
    return overlap + exact_bonus + bonus


def find_connettore(query_or_name: str, data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Trova il connettore più pertinente rispetto a una query o un nome.
    Restituisce il dict del connettore oppure None.
    """
    if not query_or_name:
        return None
    data = data or load_connettori_data()
    items = data.get("connettori", [])
    q_tokens = _tokenize(query_or_name)

    # 1) Match esatto su normalizzato
    target_norm = _normalize(query_or_name)
    for c in items:
        if _normalize(c.get("name", "")) == target_norm:
            return c

    # 2) Best score su overlap token
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for c in items:
        score = _score_candidate(q_tokens, c.get("name", ""))
        # leggero boost se la sigla coincide (es. "ctf" in query e "CTF 12/40" nel nome)
        if any(sig in q_tokens for sig in ["ctf", "ctl", "gts", "vcem", "vceme", "ctcem"]) and \
           c.get("name", "").lower().startswith(tuple(["ctf", "ctl", "gts", "v cem", "v cem-e", "ct cem"])):
            score += 0.25
        scored.append((score, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1] if scored and scored[0][0] > 0 else None
    return best


def build_nota_tecnica(c: Dict[str, Any]) -> str:
    """
    Costruisce una nota tecnica leggibile in italiano a partire dal dict del connettore.
    Solo i campi presenti vengono mostrati.
    """
    if not c:
        return ""
    lines: List[str] = []
    nome = c.get("name", "—")
    cat = c.get("category")
    sub = c.get("substrate")
    if cat or sub:
        lines.append(f"• Ambito: {cat or '—'}" + (f" · Supporto: {sub}" if sub else ""))

    # Incidenze / velocità
    if c.get("incidenza_pz_m2") is not None:
        lines.append(f"• Incidenza media: {c['incidenza_pz_m2']} pz/m²")
    if c.get("velocita_pz_giorno_1_persona") is not None:
        lines.append(f"• Velocità di posa: {c['velocita_pz_giorno_1_persona']} pz/giorno (1 persona)")
    if c.get("velocita_giunzioni_ora_2_persone") is not None:
        lines.append(f"• Produttività: {c['velocita_giunzioni_ora_2_persone']} giunzioni/ora (2 persone)")

    # Prezzi
    if c.get("price_eur_listino") is not None:
        lines.append(f"• Prezzo di listino indicativo: {c['price_eur_listino']} €/cad" +
                     (f" ({c.get('price_notes')})" if c.get('price_notes') else ""))

    # Noleggi / accessori (gestiti a livello dataset generale, ma aggiungo nota specifica per CTF/Diapason)
    if "ctf" in nome.lower() or "diapason" in nome.lower():
        lines.append("• Noleggio chiodatrice: 100 € (prima settimana) / 50 € (settimane successive)")

    # Installazione / attrezzature
    if c.get("install_notes"):
        lines.append(f"• Posa: {c['install_notes']}")
    if c.get("equipment"):
        lines.append(f"• Attrezzatura: {c['equipment']}")

    # Link capitolato
    if c.get("capitolato_url"):
        lines.append(f"• Voce di capitolato: {c['capitolato_url']}")

    return "\n".join(lines)


def enrich_response_with_internal_notes(answer: str,
                                        user_query: str,
                                        product_hint: Optional[str] = None,
                                        json_path: Optional[Path] = None) -> str:
    """
    Ritorna la risposta originale arricchita con una "Nota tecnica (fonte interna)"
    se viene trovato un connettore coerente con la query o con il product_hint.

    Parametri:
        - answer: testo della risposta generata da ChatGPT
        - user_query: domanda/contesto dell'utente (serve per il matching)
        - product_hint: (opzionale) nome esplicito del connettore/prodotto
        - json_path: (opzionale) percorso alternativo del JSON

    Uso tipico:
        enriched = enrich_response_with_internal_notes(risposta_chatgpt, query_utente)
    """
    try:
        data = load_connettori_data(json_path=json_path)
    except FileNotFoundError:
        # Se il file non esiste, restituisco la risposta originale senza errori
        return answer

    # 1) Se c'è un hint esplicito, uso quello; altrimenti provo dalla query
    target = product_hint or user_query
    connettore = find_connettore(target, data=data)
    if not connettore:
        return answer

    nota = build_nota_tecnica(connettore)
    if not nota.strip():
        return answer

    enriched = f"{answer}\n\n📌 Nota tecnica (fonte interna):\n{nota}"
    return enriched


# Funzione di utilità per debug rapido
def demo(query: str) -> str:
    """
    Esempio rapido:
        print(demo("Qual è la velocità di posa del CTF 12/40?"))
    """
    base = "Risposta ChatGPT (esempio): Il connettore richiesto è idoneo per l'impiego indicato."
    return enrich_response_with_internal_notes(base, query)


if __name__ == "__main__":
    # Piccolo smoke-test da riga di comando
    q = "Qual è la velocità di posa del CTF 12/40?"
    print(demo(q))
