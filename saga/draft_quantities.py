"""Conservative local conversion of spelled quantities to existing fact references.

The caller must validate the original narrative, evidence, preparation and fact
allowlist first, and validate the returned narrative again for all size limits.
This module does not validate arbitrary factual prose or compute new facts.
"""

import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation

_SMALL = dict(zip(
    ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"], range(20), strict=True))
_TENS = dict(zip(["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"],
                 range(20, 100, 10), strict=True))
_SCALES = {"thousand": 1000, "million": 1_000_000}
# Unsupported number words are consumed too: never repair only a supported suffix.
_WORDS = set(_SMALL) | set(_TENS) | set(_SCALES) | {
    "hundred", "billion", "trillion", "point", "minus", "negative", "half",
    "halves", "quarter", "quarters", "dozen", "couple", "few", "several", "twice",
    "plus", "times", "to", "through", "percent",
}
_WORD_PATTERN = "(?:" + "|".join(sorted(_WORDS, key=len, reverse=True)) + ")"
_START_PATTERN = "(?:" + "|".join(sorted(
    _WORDS - {"plus", "times", "to", "through", "percent"}, key=len, reverse=True)) + ")"
_OPERATOR_START = r"(?:plus|times|to|through|percent)(?=[\s-]+" + _START_PATTERN + r"\b)"
_QUANTITY = re.compile(r"(?<![\w-])(?:" + _START_PATTERN + "|" + _OPERATOR_START + ")" +
                       r"(?:[\s,&/-]+(?:" + _WORD_PATTERN + r"|and|a|of|or))*\b", re.IGNORECASE)
_ONE_ON_ONE = re.compile(r"\bone-on-one\b", re.IGNORECASE)
_RESIDUAL_NUMBER = re.compile(r"\b(?:" + "|".join(
    sorted(_WORDS - {"plus", "times", "to", "through", "percent"}, key=len, reverse=True))
    + r")\b", re.IGNORECASE)
_ALIASES = {"accomplishment", "accomplishments", "completion", "completions"}
_QUALIFIED = re.compile(
    r"\b(?:category|categories|role|roles|month|monthly|week|weekly|day|daily|"
    r"year|yearly|january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec|"
    r"flagged|eligible|review|reviews|measured|unmeasured|each|per|average|"
    r"record|records|task|tasks|selected|project|projects|shift|shifts|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"about|approximately|roughly|nearly|almost|over|under|between|"
    r"minimum|maximum|more|less|fewer|apiece|exceeding|exceeded)\b|"
    r"\b(?:at\s+least|at\s+most|up\s+to|as\s+many\s+as|and\s+a)\b|[+~±<>=≤≥≈/]",
    re.IGNORECASE)
_REPORT_SCOPE = re.compile(r"\b(?:overall|total|all|report|entire|combined|cumulative|"
                           r"aggregate|altogether)\b", re.IGNORECASE)


def _decimal(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        number = Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _under_hundred(words):
    if len(words) == 1:
        return _SMALL.get(words[0], _TENS.get(words[0]))
    if (len(words) == 2 and words[0] in _TENS and words[1] in _SMALL
            and 1 <= _SMALL[words[1]] <= 9):
        return _TENS[words[0]] + _SMALL[words[1]]
    return None


def _under_thousand(words):
    if len(words) >= 2 and words[0] in _SMALL and words[1] == "hundred":
        if not 1 <= _SMALL[words[0]] <= 9:
            return None
        rest = words[2:]
        if rest and rest[0] == "and":
            rest = rest[1:]
            if not rest:
                return None
        tail = _under_hundred(rest) if rest else 0
        if tail is not None and (tail > 0 or not rest):
            return _SMALL[words[0]] * 100 + tail
        return None
    return _under_hundred(words)


def _integer(words):
    total, start, previous_scale = 0, 0, 1_000_000_000
    for position, word in enumerate(words):
        if word not in _SCALES:
            continue
        scale = _SCALES[word]
        part = _under_thousand(words[start:position])
        if part is None or part == 0 or scale >= previous_scale:
            return None
        total += part * scale
        previous_scale, start = scale, position + 1
    rest = words[start:]
    if start and rest and rest[0] == "and":
        tail = _under_hundred(rest[1:])
    else:
        tail = _under_thousand(rest) if rest else (0 if start else None)
    if tail is None or (start and rest and tail == 0):
        return None
    return total + tail


def _parse_quantity(text):
    words = re.split(r"[\s-]+", text.casefold())
    if len(words) > 32:
        return None
    negative = words[0] in {"minus", "negative"}
    if negative:
        words = words[1:]
    if words.count("point") > 1:
        return None
    if "point" in words:
        position = words.index("point")
        fraction = words[position + 1:]
        if not fraction or len(fraction) > 12 or any(
                word not in _SMALL or _SMALL[word] > 9 for word in fraction):
            return None
        integer = _integer(words[:position])
        if integer is None:
            return None
        value = Decimal(str(integer) + "." + "".join(str(_SMALL[word]) for word in fraction))
    else:
        integer = _integer(words)
        if integer is None:
            return None
        value = Decimal(integer)
    return -value if negative else value


def _unit_key(unit):
    return " ".join(unit.split())


def _candidates(evidence, preparation, allowed_fact_ids):
    """Return candidate lists without guessing totals from grouped facts."""
    candidates = {}
    selected = preparation is not None and preparation["report_format"] != "general"

    def add(unit, fact_id, fact):
        if fact_id not in allowed_fact_ids:
            return
        value = _decimal(fact.get("value"))
        if value is not None:
            candidates.setdefault(_unit_key(unit), {})[fact_id] = value

    if selected:
        for entry in preparation["selected_records"]:
            measurement = entry["measurement"]
            if measurement is None:
                continue
            fact_id = measurement["fact_id"]
            fact = preparation["facts"][fact_id]
            if (fact.get("dimension") == "selected_record"
                    and fact.get("source_record_ids") == [measurement["source_record_id"]]
                    and measurement["source_record_id"] == entry["source_record"]["id"]
                    and fact.get("measure") == measurement["unit"]
                    and _decimal(fact.get("value")) == _decimal(measurement["value"])):
                add(measurement["unit"], fact_id, fact)
    else:
        for measurement in evidence.get("measurements", []):
            fact_id = measurement["fact_id"]
            fact = evidence["facts"].get(fact_id, {})
            if (measurement.get("occasions", 0) > 0
                    and fact.get("dimension") == "total"
                    and fact.get("source_record_ids")
                    and fact.get("measure") == measurement["measure"]
                    and _decimal(fact.get("value")) == _decimal(measurement["total"])):
                add(measurement["measure"], fact_id, fact)
        for fact_id, fact in evidence["facts"].items():
            if fact.get("dimension") == "summary" and fact.get("group") == "completions":
                for unit in _ALIASES:
                    add(unit, fact_id, fact)
    return candidates, selected


def _scope_terms(evidence):
    terms = set()
    for fact in evidence["facts"].values():
        if (fact.get("dimension") in {"category", "role", "month"}
                and isinstance(fact.get("group"), str) and fact["group"].strip()):
            terms.add(fact["group"])
    for row in evidence.get("records", []):
        for field in ("category", "duty"):
            if isinstance(row.get(field), str) and row[field].strip():
                terms.add(row[field])
    return [re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE) for term in terms]


def repair_quantities(narrative, evidence, preparation, allowed_fact_ids):
    """Copy validated narrative and replace only unique, exact, unqualified claims.

    Recognized unsafe claims raise a fixed ValueError with no source text. The
    caller must still run its ordinary content checks on unrecognized wording.
    General references replace just the numeral; selected references replace
    numeral plus unit because selected fact displays already include the unit.
    """
    candidates, selected = _candidates(evidence, preparation, allowed_fact_ids)
    units = set(_ALIASES) | set(candidates)
    units.update(_unit_key(fact["measure"]) for fact in evidence["facts"].values()
                 if fact.get("measure"))
    units.update(_unit_key(item["measure"]) for item in evidence.get("measurements", []))
    unit_pattern = re.compile(r"\s+(?P<unit>" + "|".join(
        r"\s+".join(re.escape(word) for word in unit.split())
        for unit in sorted(units, key=len, reverse=True)) + r")(?![\w-])")
    folded_unit_pattern = re.compile(unit_pattern.pattern, re.IGNORECASE)
    # Unrecognized intervening words are not permission to match a numeral's
    # suffix or to reinterpret an exact unit (e.g. "twenty first hours").
    modifier_pattern = re.compile(
        r"(?:\s+(?:recorded|completed|assisted|volunteer|service|worked|documented|"
        r"flagged|eligible|measured|unmeasured|reviewed|"
        r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)){1,2}"
        + unit_pattern.pattern, re.IGNORECASE)
    scope_patterns = _scope_terms(evidence)

    def claims(text):
        ignored = list(_ONE_ON_ONE.finditer(text))
        result = []
        for number in _QUANTITY.finditer(text):
            if any(match.start() <= number.start() < match.end() for match in ignored):
                continue
            unit = unit_pattern.match(text, number.end())
            if unit is None:
                if (folded_unit_pattern.match(text, number.end())
                        or modifier_pattern.match(text, number.end())):
                    raise ValueError("unverified quantity")
                continue
            result.append((number, unit))
        return result

    result = deepcopy(narrative)
    for section in result["sections"]:
        if claims(section["heading"]):
            raise ValueError("unverified quantity")
        for index, paragraph in enumerate(section["paragraphs"]):
            # A fact interrupts text: a quantity cannot cross an existing reference.
            runs = []
            original_runs = []
            for segment in paragraph:
                if "text" in segment and runs and isinstance(runs[-1], str):
                    runs[-1] += segment["text"]
                    original_runs[-1].append(segment)
                else:
                    runs.append(segment.get("text", segment))
                    original_runs.append([segment])
            detected = [claims(run) if isinstance(run, str) else [] for run in runs]
            context = section["heading"] + " "
            for run, matches in zip(runs, detected, strict=True):
                if isinstance(run, str):
                    cursor = 0
                    for number, unit in matches:
                        context += run[cursor:number.start()] + " "
                        cursor = unit.end()
                    context += run[cursor:] + " "
            # A second, unsupported number may extend a recognized quantity, as
            # in "two hours and a half". Never repair just its integer prefix.
            scoped = (_QUALIFIED.search(context)
                      or (selected and _REPORT_SCOPE.search(context))
                      or _RESIDUAL_NUMBER.search(_ONE_ON_ONE.sub("", context))
                      or any(p.search(context) for p in scope_patterns))
            replaced = []
            for run, matches, original in zip(runs, detected, original_runs, strict=True):
                if not isinstance(run, str):
                    replaced.append(run)
                    continue
                if not matches:
                    replaced.extend(original)
                    continue
                cursor = 0
                for number, unit in matches:
                    options = candidates.get(_unit_key(unit.group("unit")), {})
                    value = _parse_quantity(number.group())
                    if scoped or value is None or len(options) != 1:
                        raise ValueError("unverified quantity")
                    fact_id, verified = next(iter(options.items()))
                    if value != verified:
                        raise ValueError("unverified quantity")
                    if number.start() > cursor:
                        replaced.extend({"text": run[start:min(start + 2000, number.start())]}
                                        for start in range(cursor, number.start(), 2000))
                    replaced.append({"fact": fact_id})
                    cursor = unit.end() if selected else number.end()
                if cursor < len(run):
                    replaced.extend({"text": run[start:start + 2000]}
                                    for start in range(cursor, len(run), 2000))
            section["paragraphs"][index] = replaced
    return result
