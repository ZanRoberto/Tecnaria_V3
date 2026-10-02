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
        rf'\b{MONEY_BOUND}\s*({MONEY_NUMBER})\s*(?:euro|eur|€)\s*(?:di\s+)?{MONEY_CUE}\b',
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


def without_old_budget(question):
    for start, end, _ in sorted(budget_matches(question), reverse=True):
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
