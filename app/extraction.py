"""Extraction consistency (v3 6.2): does a processing product correspond to the passage it was extracted from?
Rules only, no model calls. It checks correspondence - locator, short quote, identifier, number, unit,
denominator, numerator, period, population - never whether a fact is true. A field the rules cannot decide is
"unsupported", which makes the item "unchecked", never "consistent"."""
import re

from .claims import (
    DATE,
    SOURCE_NUMBER,
    TIMESTAMP,
    _value,
    numeric_hints,
    numeric_tokens,
)

CONTRACT_VERSION = "extraction_consistency.v1"
CHECKER_VERSION = "biocheck.extraction_rules.v1"  # bump whenever a rule or the unit table below changes

# Units the rules know, canonical -> spellings. Longest spelling wins at a position, so "mg/kg" is never
# read as "mg". A unit not listed here is "unsupported", not guessed.
UNITS = {
    "%": ("%", "％", "percent"),
    "mg/kg": ("mg/kg", "毫克/千克"),
    "mg/m2": ("mg/m2", "mg/m²", "mg/㎡"),
    "mg": ("mg", "毫克"),
    "g": ("g", "克"),
    "kg": ("kg", "千克", "公斤"),
    "μg": ("μg", "µg", "ug", "mcg", "微克"),
    "ml": ("ml", "mL", "毫升"),
    "l": ("L", "升"),
    "mmol/l": ("mmol/L", "mmol/l"),
    "μmol/l": ("μmol/L", "µmol/L", "umol/L"),
    "iu": ("IU", "国际单位"),
    "例": ("例",),
    "人": ("人", "名"),
    "次": ("次",),
    "天": ("天", "日", "days", "day", "d"),
    "周": ("周", "weeks", "week", "wk"),
    "月": ("个月", "月", "months", "month", "mo"),
    "年": ("年", "years", "year", "yr"),
    "小时": ("小时", "hours", "hour", "h"),
    "分钟": ("分钟", "minutes", "min"),
}
SPELLINGS = sorted(((s, unit) for unit, spells in UNITS.items() for s in spells), key=lambda x: -len(x[0]))
CANONICAL = {s.casefold(): unit for s, unit in SPELLINGS}
# Explicit denominator statements: (numerator, denominator) groups.
RATIO = re.compile(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)")
OF_N = re.compile(r"(\d[\d,]*)\s*(?:例|人|名)?\s*(?:of|out of)\s+(\d[\d,]*)", re.IGNORECASE)
IN_N = re.compile(r"(\d[\d,]*)\s*(?:例|人|名)(?:患者|受试者|病例)?中[^0-9]{0,12}?(\d[\d,]*)")
TOTAL_N = re.compile(r"(?:\b[Nn]\s*=\s*|共\s*|总计\s*|合计\s*)(\d[\d,]*)")
SENTENCE_STOP = "。！？!?\n"
FIELDS = {
    "identifier": {"identifier"},
    "measurement": {"value", "unit", "denominator", "numerator", "period", "population"},
    "claim": {"value", "unit", "denominator", "numerator", "period", "population", "label"},
    "annotation": set(),  # a profile annotation: only its locator and short quote are checked
}


class ContractError(ValueError):
    """The request breaks the contract in a way pydantic cannot see (fields not allowed for a kind)."""


def squash(text):
    return " ".join((text or "").split())


def result(kind, reason="", span=None):
    out = {"result": kind}
    if reason:
        out["reason"] = reason
    if span is not None:
        out["span"] = list(span)
    return out


def sentence_around(text, a, b):
    """The passage-relative sentence holding [a, b): from after the previous stop to the next one."""
    start = max(text.rfind(c, 0, a) for c in SENTENCE_STOP) + 1
    ends = [i for i in (text.find(c, b) for c in SENTENCE_STOP) if i >= 0]
    return start, (min(ends) + 1 if ends else len(text))


def number_spans(text):
    return [(m.start(), m.end(), _value(m.group())) for m in SOURCE_NUMBER.finditer(text)]


def unit_at(text, pos):
    """The longest known unit spelling starting at pos (after spaces), as (canonical, start, end), or None."""
    while pos < len(text) and text[pos] == " ":
        pos += 1
    for spelling, unit in SPELLINGS:
        if text.startswith(spelling, pos) or (spelling.isascii() and text[pos:pos + len(spelling)].casefold()
                                              == spelling.casefold()):
            end = pos + len(spelling)
            if spelling.isascii() and spelling[-1].isalpha() and end < len(text) and text[end].isalpha():
                continue  # "d" is not the start of "dose"
            return unit, pos, end
    return None


def check_number(name, value, quote, offset):
    """A number field: its value must be a number of the quote (rounded to the digits shown, as /v1/verify)."""
    tokens = numeric_tokens(value)
    if len(tokens) != 1:
        return result("unsupported", f"{name}_not_a_single_number")
    if numeric_hints(tokens[0], [quote]):
        return result("not_found", f"{name}_not_in_quote")
    wanted = _value(tokens[0])
    for a, b, v in number_spans(quote):
        if v is not None and wanted is not None and abs(v - wanted) < 1:
            return result("match", span=(offset + a, offset + b))
    return result("match")


def stated_ratios(text):
    """Explicit (numerator, denominator) statements and bare totals of a text, as Decimals. Dates and
    timestamps are taken out first, so 2015/3/1 is no ratio; "120例中…32例" states the total first."""
    text = DATE.sub(" ", TIMESTAMP.sub(" ", text))
    pairs = [(_value(m.group(1)), _value(m.group(2))) for p in (RATIO, OF_N) for m in p.finditer(text)]
    pairs += [(_value(m.group(2)), _value(m.group(1))) for m in IN_N.finditer(text)]
    totals = [_value(m.group(1)) for m in TOTAL_N.finditer(text)]
    return pairs, totals


def check_item(item, passage):
    text, p_start = passage["text"], passage["start"]
    allowed = FIELDS[item["kind"]]
    given = {k: v for k, v in (item.get("fields") or {}).items() if v is not None}
    extra = set(given) - allowed
    if extra:
        raise ContractError(f"items[{item['id']}].fields: {', '.join(sorted(extra))} not allowed for {item['kind']}")
    if item["kind"] == "identifier" and "identifier" not in given:
        raise ContractError(f"items[{item['id']}].fields.identifier is required for an identifier")
    if item["kind"] == "measurement" and "value" not in given:
        raise ContractError(f"items[{item['id']}].fields.value is required for a measurement")
    fields = {}
    a, b = item["locator"]["start"] - p_start, item["locator"]["end"] - p_start
    if a < 0 or b > len(text) or a >= b:
        fields["locator"] = result("mismatch", "locator_outside_passage")
        fields["quote"] = result("not_found" if squash(item["quote"]) not in squash(text) else "mismatch",
                                 "locator_outside_passage")
        for name in given:
            fields[name] = result("unsupported", "locator_outside_passage")
        return fields
    located = text[a:b]
    quote = item["quote"]
    if squash(located) == squash(quote):
        fields["locator"] = result("match", span=(p_start + a, p_start + b))
        fields["quote"] = result("match", span=(p_start + a, p_start + b))
    else:
        where = squash(text).find(squash(quote)) if squash(quote) else -1
        fields["locator"] = result("mismatch", "located_text_differs_from_quote")
        fields["quote"] = result("mismatch", "quote_elsewhere_in_passage") if where >= 0 else result(
            "not_found", "quote_not_in_passage")
    # the fields are read from the located text when the locator holds; otherwise nothing else can be decided
    if fields["locator"]["result"] != "match":
        for name in given:
            fields[name] = result("unsupported", "no_valid_location")
        return fields
    s_start, s_end = sentence_around(text, a, b)
    sentence = text[s_start:s_end]
    for name, value in given.items():
        if name == "identifier":
            raw, normalized = value["raw"], value["value"]
            if squash(raw) != squash(located):
                fields[name] = result("mismatch", "identifier_raw_differs_from_source")
            elif re.sub(r"[\W_]", "", raw).casefold() != re.sub(r"[\W_]", "", normalized).casefold():
                fields[name] = result("mismatch", "identifier_value_differs_from_source")
            else:
                fields[name] = result("match", span=(p_start + a, p_start + b))
        elif name == "value":
            fields[name] = check_number("value", value, located, p_start + a)
        elif name == "unit":
            canonical = CANONICAL.get(value.strip().casefold()) or (value.strip() if value.strip() in UNITS else None)
            if canonical is None:
                fields[name] = result("unsupported", "unit_not_in_table")
                continue
            number = numeric_tokens(given.get("value") or "")
            spots = [s for s in number_spans(located) if number and s[2] == _value(number[0].rstrip("%"))]
            if not spots:
                fields[name] = result("unsupported", "unit_without_located_value")
                continue
            # a source number token carries its own "%" (26.7%): that is the unit written at the value
            found = [("%", end - 1, end) if located[end - 1] == "%" else unit_at(located, end)
                     for _, end, _ in spots]
            if any(f and f[0] == canonical for f in found):
                f = next(f for f in found if f and f[0] == canonical)
                fields[name] = result("match", span=(p_start + a + f[1], p_start + a + f[2]))
            elif any(found):
                fields[name] = result("mismatch", "another_unit_at_value:" + next(f for f in found if f)[0])
            else:
                fields[name] = result("not_found", "no_unit_at_value")
        elif name in ("denominator", "numerator"):
            pairs, totals = stated_ratios(sentence)
            if name == "denominator" and value.strip().casefold() == "unknown":
                fields[name] = (result("mismatch", "denominator_stated_in_source") if pairs or totals
                                else result("match", "no_denominator_stated"))
                continue
            tokens = numeric_tokens(value)
            if len(tokens) != 1:
                fields[name] = result("unsupported", f"{name}_not_a_single_number")
                continue
            wanted = _value(tokens[0])
            side = [d for _, d in pairs] + totals if name == "denominator" else [n for n, _ in pairs]
            if wanted in side:
                fields[name] = result("match")
            elif side:
                fields[name] = result("mismatch", f"source_states_another_{name}")
            elif any(v == wanted for _, _, v in number_spans(sentence)):
                fields[name] = result("unsupported", f"{name}_relation_not_rule_checkable")
            else:
                fields[name] = result("not_found", f"{name}_not_in_sentence")
        elif name == "period":
            if squash(value).casefold().replace(" ", "") in squash(sentence).casefold().replace(" ", ""):
                fields[name] = result("match")
            elif DATE.search(value) and DATE.search(sentence):
                fields[name] = result("mismatch", "source_states_another_period")
            else:
                fields[name] = result("not_found", "period_not_in_sentence")
        elif name == "population":
            fields[name] = (result("match") if squash(value).casefold() in squash(sentence).casefold()
                            else result("not_found", "population_not_in_sentence"))
        elif name == "label":
            fields[name] = result("unsupported", "label_is_semantic")
    return fields


def status(fields):
    results = {f["result"] for f in fields.values()}
    if results <= {"match"}:
        return "consistent"
    if results & {"mismatch", "not_found"}:
        return "inconsistent"
    return "unchecked"


def check(request):
    """One request: every item checked against the one passage. Raises ContractError on a contract break."""
    passage = request["passage"]
    if passage["end"] - passage["start"] != len(passage["text"]):
        raise ContractError("passage: end - start must equal the length of passage.text")
    if len({i["id"] for i in request["items"]}) != len(request["items"]):
        raise ContractError("items: ids must be unique")
    items = []
    for item in request["items"]:
        fields = check_item(item, passage)
        items.append({"id": item["id"], "kind": item["kind"], "status": status(fields), "fields": fields})
    counts = {s: sum(1 for i in items if i["status"] == s) for s in ("consistent", "inconsistent", "unchecked")}
    return {"contract_version": CONTRACT_VERSION, "checker_version": CHECKER_VERSION,
            "subject": request["subject"], "items": items, "counts": counts, "llm_calls": []}
