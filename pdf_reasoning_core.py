"""Document-independent constraint and attribution helpers. No catalog codes."""
import re

MONEY_NUMBER = r'(?:\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)'
MONEY_CUE = r'(?:budget|spesa|prezzo|costo|price|cost|premium|premio)'
MONEY_BOUND = r'(?:massim\w*|max(?:imum)?\.?|non\s+superior\w*\s+a|non\s+oltre|non\s+pi[uù]\s+di|non\s+superare|entro|fino\s+a|al\s+massimo|at\s+most|no\s+more\s+than|not\s+more\s+than|up\s+to|[≤]|<=)'


def european_number(raw):
    raw = re.sub(r'\s|€|eur(?:o)?', '', str(raw), flags=re.I)
    if not re.fullmatch(MONEY_NUMBER, raw):
        return None
    if re.fullmatch(r'\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?', raw):
        return float(raw.replace('.', '').replace(',', '.'))
    return float(raw.replace(',', '.'))


def request_constraints_text(question):
    return question.split("Risposta precedente:", 1)[0]


def budget_matches(question):
    question = request_constraints_text(question)
    patterns = [
        rf'\b(?:budget|spesa)\b\s*(?:(?:per\s+(?:ciascun\w*|ogni)\s+\w+)\s*)?(?:(?:massim\w*|max\.?|di|a|disponibile|complessiv\w*|totale)\s*)*({MONEY_NUMBER})\s*(?:euro|eur|€)?',
        rf'\b{MONEY_CUE}\b\s*{MONEY_BOUND}\s*({MONEY_NUMBER})\s*(?:euro|eur|€)?',
        rf'\b{MONEY_BOUND}\s*({MONEY_NUMBER})\s*(?:euro|eur|€)(?:\s*(?:di\s+)?{MONEY_CUE}\b)?',
        # "massimo 800 euro", "non piu' di 1.200 euro": il limite e' dichiarato
        # senza nominare il prezzo. Prima serviva per forza una parola come
        # "budget" o "spesa" accanto alla cifra, e un tetto scritto cosi' veniva
        # ignorato: la risposta proponeva prodotti fuori portata.
        rf'\b(?:ho|ha|hanno|dispone\s+di|con|per)\s+({MONEY_NUMBER})\s*(?:euro|eur|€)\b',
        # Il tetto dichiarato come SUFFICIENZA o come DISPONIBILITA'.
        #
        # Misurato il 7ott2026 su una prova dal vivo. La domanda finiva con "...
        # mi bastano 3.500 euro?" e requested_budget restituiva None: nessun
        # tetto riconosciuto. Il modello, senza un budget dichiarato, ha preso
        # dal documento un importo qualunque - 3.532, che e' un prezzo vero di
        # pagina 51 - e ci ha confrontato i totali, concludendo che il cliente
        # non poteva permettersi una spesa di 3.288 euro contro un budget di
        # 3.500. Una vendita persa per un verbo non previsto.
        #
        # Non e' un elenco di sinonimi di comodo: sono i modi in cui in italiano
        # si dichiara di avere una cifra o di chiedere se basta. Classe chiusa.
        rf'\b(?:bast\w+|avanz\w+|a\s+disposizione(?:\s+di)?|dispongo\s+di|'
        rf'spender\w+|rientr\w+\s+in|tetto\s+di|limite\s+di|arriv\w+\s+a)\s*'
        rf'(?:di\s+)?({MONEY_NUMBER})\s*(?:euro|eur|€)',
    ]
    matches = []
    for pattern in patterns:
        for match in re.finditer(pattern, question, re.I):
            value = european_number(match.group(1))
            if value is not None:
                matches.append((match.start(), match.end(), value))
    return sorted(set(matches))


def budget_limit(question):
    matches = budget_matches(question)
    return min((v for _, _, v in matches), default=None)


def merged_spans(spans):
    """Unisce gli intervalli che si sovrappongono.

    Serve perche' piu' modi di scrivere lo stesso tetto si riconoscono insieme:
    "premio fino a 2.500 euro" produce sia (0,24) sia (7,24), lo stesso importo
    visto da due pattern. Togliendoli uno dopo l'altro da una stringa che nel
    frattempo si e' accorciata, il secondo taglio cade nel posto sbagliato e si
    porta via il resto della frase: "premio fino a 2.500 euro per la RC
    professionale" diventava " ssionale". Vale per qualsiasi documento: anche
    "prezzo fino a 1.200 euro per la madia" ha questa forma.
    """
    unione = []
    for start, end in sorted((s, e) for s, e, _ in spans):
        if unione and start <= unione[-1][1]:
            unione[-1][1] = max(unione[-1][1], end)
        else:
            unione.append([start, end])
    return unione


def without_old_budget(question):
    for start, end in reversed(merged_spans(budget_matches(question))):
        question = question[:start] + ' ' + question[end:]
    return question


def bound_operator(text):
    if re.search(r'non.{0,15}super|massim|max\b|entro|fino\s+a|[≤]|<=|at most|maximum', text, re.I):
        return 'max'
    if re.search(r'minim|min\b|almeno|[≥]|>=|at least|minimum', text, re.I):
        return 'min'
    return None


def remove_replaced_dimensions(previous, current_limits, aliases):
    """Remove only the replaced bound: a new minimum must preserve the old maximum."""
    replaced = {(axis, op) for axis, op, _ in current_limits}
    union = '|'.join(aliases.values())
    spans = []
    for axis, alias in aliases.items():
        for match in re.finditer(rf'\b(?:{alias})\b', previous, re.I):
            following = previous[match.end():]
            stop = re.search(rf'[;!\n]|\b(?:{union})\b', following, re.I)
            segment = following[:stop.start()] if stop else following
            value = re.search(r'\d+(?:[.,]\d+)?\s*(?:cm|mm|m)\b', segment, re.I)
            if not value:
                continue
            prefix = segment[:value.start()]
            op = bound_operator(prefix)
            if (axis, op) in replaced:
                spans.append((match.start(), match.end()+value.end()))
    for start, end in sorted(set(spans), reverse=True):
        previous = previous[:start] + ' ' + previous[end:]
    return previous


def respective_prices(sentence, mentions, thousands_pattern, suffix_pattern):
    """Explicit 'respectively' links ordered codes to ordered prices; never use unions."""
    if len(mentions) < 2 or not re.search(r'rispettivamente|respectively', sentence, re.I):
        return None
    marker = re.search(r'rispettivamente|respectively', sentence, re.I)
    tail = sentence[marker.end():]
    matches = list(thousands_pattern.finditer(tail)) + list(suffix_pattern.finditer(tail))
    by_position = {}
    for match in matches:
        raw = match.group(1) if match.lastindex else match.group(0)
        by_position.setdefault(match.start(), raw)
    prices = [raw for _, raw in sorted(by_position.items())]
    codes = list(dict.fromkeys(c for _, c in mentions))
    if len(prices) != len(codes):
        return None
    return dict(zip(codes, prices))
