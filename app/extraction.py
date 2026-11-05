"""Extraction consistency (v3 6.2): does a processing product correspond to the fixed text it was extracted
from? Rules only, no model calls. It checks correspondence, never whether a fact is true.

Each field of an item names its own span in one of the request's passages (bounded slices of one fixed
artifact, each with its own offsets - a table's header and a data row are two passages, never one invented
continuous range), or says explicitly that the source states none ("unknown" / "not_applicable"). The value is
the anchor: unit, denominator, period and scope must belong to that same quantity (the unit written right at
the number, the denominator in an explicit ratio of that number within its clause, period and scope in that
clause), and a table metric's header must head the value's column. Whatever the rules cannot prove is
"unsupported", which makes the item "unchecked", never "consistent"."""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .claims import DATE, TIMESTAMP

CONTRACT_VERSION = "extraction_consistency.v1"
# Bump whenever a rule, the unit table, the number conversions or the clause rule below changes.
CHECKER_VERSION = "biocheck.extraction_rules.v1"

# Units the rules know, canonical -> spellings. The longest spelling wins at a position ("mg/kg" is never "mg",
# "mg/day" is no ratio). A unit not listed here is "unsupported", never guessed.
UNITS = {
    "%": ("%", "％", "percent"),
    "mg/kg": ("mg/kg", "毫克/千克"),
    "mg/m2": ("mg/m2", "mg/m²", "mg/㎡"),
    "mg/day": ("mg/day", "mg/d", "毫克/天", "毫克/日"),
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
    "天": ("天", "days", "day"),
    "周": ("周", "weeks", "week"),
    "月": ("个月", "months", "month"),
    "年": ("years", "year"),
    "小时": ("小时", "hours", "hour"),
    "分钟": ("分钟", "minutes", "min"),
}
SPELLINGS = sorted(((s, unit) for unit, spells in UNITS.items() for s in spells), key=lambda x: -len(x[0]))
CANONICAL = {s.casefold(): unit for s, unit in SPELLINGS}
NUMBER = re.compile(r"(?<![\d.])[-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d])")
FULLWIDTH = str.maketrans("０１２３４５６７８９．，－", "0123456789.,-")
# Explicit ratios inside one clause, as (numerator span, denominator span) group numbers.
RATIOS = (
    (re.compile(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)"), 1, 2),  # 32/120 (dates are blanked first; mg/day has no digits)
    (re.compile(r"(\d[\d,]*)\s*(?:例|人|名)?\s*(?:of|out of)\s+(\d[\d,]*)", re.IGNORECASE), 1, 2),
    (re.compile(r"(\d[\d,]*)\s*(?:例|人|名)(?:患者|受试者|病例)?中[^0-9；;。]{0,12}?(\d[\d,]*)"), 2, 1),
)
TOTAL = re.compile(r"(?:\b[Nn]\s*=\s*|共\s*|总计\s*|合计\s*)(\d[\d,]*)")
CLAUSE_STOP = "；;。！？!?\n"
TABLE_RULE = re.compile(r"^\|?\s*:?-{3,}")

KIND_FIELDS = {
    "measurement": ("object", "metric", "value", "unit", "period", "scope", "denominator"),
    "identifier": ("identifier",),
    "claim": ("statement",),
    "annotation": (),
}


class ContractError(ValueError):
    """The request breaks the contract in a way the HTTP schema cannot see."""


def squash(text):
    return " ".join((text or "").split())


def outcome(kind, reason="", span=None):
    out = {"result": kind}
    if reason:
        out["reason"] = reason
    if span is not None:
        out["span"] = span
    return out


def number(text):
    """A written number as a Decimal: only full-width digits and thousands separators are converted
    (versioned with CHECKER_VERSION); anything else is not a number here."""
    t = squash(text).translate(FULLWIDTH).replace("−", "-")
    if not re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", t):
        return None
    try:
        return Decimal(t.replace(",", ""))
    except InvalidOperation:
        return None


class Passages:
    """The request's passages of one fixed artifact, each in its own artifact offsets."""

    def __init__(self, passages):
        self.by_id = {p["id"]: p for p in passages}

    def text(self, span):
        p = self.by_id.get(span["passage"])
        if p is None:
            raise ContractError(f"span names passage {span['passage']!r}, which is not in the request")
        a, b = span["start"] - p["start"], span["end"] - p["start"]
        if a < 0 or b > len(p["text"]) or a >= b:
            return None
        return p["text"][a:b]

    def clause(self, span):
        """(passage id, clause start, clause end) in artifact offsets: the clause around the span, cut at
        "；;。！？!?" and line ends; a markdown table row is one line, so a row is one clause."""
        p = self.by_id[span["passage"]]
        text, a, b = p["text"], span["start"] - p["start"], span["end"] - p["start"]
        start = max(text.rfind(c, 0, a) for c in CLAUSE_STOP) + 1
        ends = [i for i in (text.find(c, b) for c in CLAUSE_STOP) if i >= 0]
        end = min(ends) if ends else len(text)
        return p["id"], p["start"] + start, p["start"] + end, text[start:end]

    def inside(self, inner, outer):
        return inner["passage"] == outer["passage"] and outer["start"] <= inner["start"] and inner["end"] <= outer["end"]


def unit_at(text, pos):
    """(canonical unit, start, end) of the longest known spelling at pos (after spaces), or None."""
    while pos < len(text) and text[pos] == " ":
        pos += 1
    for spelling, unit in SPELLINGS:
        piece = text[pos:pos + len(spelling)]
        if piece == spelling or (spelling.isascii() and piece.casefold() == spelling.casefold()):
            end = pos + len(spelling)
            if spelling[-1].isascii() and spelling[-1].isalpha() and end < len(text) and text[end].isalpha():
                continue  # "day" is not the start of "days later"; "min" is not "minor"
            return unit, pos, end
    return None


def ratios_in(clause_text, offset):
    """Explicit (numerator, denominator) statements in a clause: [(num_start, num_end, num, den_start, den_end, den)]
    in artifact offsets. Dates and timestamps are blanked first (2015/3/1 is no ratio)."""
    text = DATE.sub(lambda m: " " * len(m.group()), TIMESTAMP.sub(lambda m: " " * len(m.group()), clause_text))
    out = []
    for pattern, n, d in RATIOS:
        for m in pattern.finditer(text):
            out.append((offset + m.start(n), offset + m.end(n), number(m.group(n)),
                        offset + m.start(d), offset + m.end(d), number(m.group(d))))
    return out


def stated(field):
    return field is not None and field.get("state", "stated") == "stated"


def check_measurement(item, P, fields):
    f = item["fields"]
    value_field = f["value"]
    if not stated(value_field):
        raise ContractError(f"items[{item['id']}].fields.value: a measurement states its value")
    vspan = value_field["span"]
    vtext = P.text(vspan)
    if vtext is None:
        fields["value"] = outcome("mismatch", "span_outside_passage")
        for name in KIND_FIELDS["measurement"] + ("measure_kind",):
            fields.setdefault(name, outcome("unsupported", "no_valid_value_span"))
        return
    if not P.inside(vspan, item["locator"]):
        fields["value"] = outcome("mismatch", "value_outside_quote")
    elif number(vtext) is None or number(value_field["value"]) is None:
        fields["value"] = outcome("unsupported", "value_not_a_plain_number")
    elif number(vtext) != number(value_field["value"]):
        fields["value"] = outcome("mismatch", "value_differs_from_span")
    else:
        fields["value"] = outcome("match", span=vspan)
    pid, c_start, c_end, clause_text = P.clause(vspan)
    passage = P.by_id[pid]
    after = vspan["end"] - passage["start"]
    at_value = unit_at(passage["text"], after)
    percent_written = passage["text"][after:after + 1] in ("%", "％")
    # unit: written right at the number
    unit = f["unit"]
    if stated(unit):
        canonical = CANONICAL.get(squash(unit["value"]).casefold())
        utext = P.text(unit["span"])
        if canonical is None:
            fields["unit"] = outcome("unsupported", "unit_not_in_table")
        elif utext is None or CANONICAL.get(squash(utext).casefold()) != canonical:
            fields["unit"] = outcome("mismatch", "unit_differs_from_span")
        elif at_value and (unit["span"]["passage"], unit["span"]["start"]) == (pid, passage["start"] + at_value[1]) \
                and at_value[0] == canonical:
            fields["unit"] = outcome("match", span=unit["span"])
        elif at_value and at_value[0] != canonical:
            fields["unit"] = outcome("mismatch", "another_unit_at_value:" + at_value[0])
        else:
            fields["unit"] = outcome("mismatch", "unit_not_at_value")
    else:
        fields["unit"] = (outcome("mismatch", "unit_written_at_value:" + at_value[0]) if at_value
                          else outcome("match", "no_unit_at_value"))
    # denominator: the denominator of an explicit ratio of this very number, inside its clause
    ratios = ratios_in(clause_text, c_start)
    mine = [r for r in ratios if (r[0], r[1]) == (vspan["start"], vspan["end"])]
    den = f["denominator"]
    if stated(den):
        dtext = P.text(den["span"])
        wanted = number(den["value"])
        if dtext is None or wanted is None or number(dtext) != wanted:
            fields["denominator"] = outcome("mismatch", "denominator_differs_from_span")
        elif any((r[3], r[4]) == (den["span"]["start"], den["span"]["end"]) for r in mine):
            fields["denominator"] = outcome("match", span=den["span"])
        elif mine:
            fields["denominator"] = outcome("mismatch", "the_value_has_another_denominator")
        elif percent_written and number(vtext) is not None:
            # a percentage of a ratio stated in the same clause: n/N*100 rounded to the digits shown
            shown = number(vtext)
            places = max(0, -shown.as_tuple().exponent)
            fits = [r for r in ratios if (r[3], r[4]) == (den["span"]["start"], den["span"]["end"]) and r[5]
                    and (r[2] / r[5] * 100).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP) == shown]
            fields["denominator"] = (outcome("match", "percentage_of_stated_ratio", den["span"]) if fits
                                     else outcome("mismatch", "percentage_does_not_follow_from_ratio")
                                     if any((r[3], r[4]) == (den["span"]["start"], den["span"]["end"]) for r in ratios)
                                     else outcome("unsupported", "denominator_relation_not_rule_checkable"))
        else:
            fields["denominator"] = outcome("unsupported", "denominator_relation_not_rule_checkable")
    else:
        # "unknown": wrong for certain only when this very number has a stated denominator; other ratios or
        # totals in the clause may belong to other quantities
        fields["denominator"] = (outcome("mismatch", "the_value_has_a_stated_denominator") if mine
                                 else outcome("unsupported", "other_ratio_or_total_in_value_clause")
                                 if ratios or TOTAL.search(clause_text)
                                 else outcome("match", "no_denominator_in_value_clause"))
    # period and scope: in the value's clause
    for name in ("period", "scope"):
        field = f[name]
        if not stated(field):
            if name == "period":
                blanked = TIMESTAMP.sub(" ", clause_text)
                # a date in the clause may be something else (a pharmacopoeia edition "2015年版"): not proof
                fields[name] = (outcome("unsupported", "date_in_value_clause") if DATE.search(blanked)
                                else outcome("match", "no_date_in_value_clause"))
            else:
                fields[name] = outcome("unsupported", "absence_of_scope_not_rule_checkable")
            continue
        text = P.text(field["span"])
        if text is None or squash(text).casefold() != squash(field["value"]).casefold():
            fields[name] = outcome("mismatch", f"{name}_differs_from_span")
        elif field["span"]["passage"] == pid and c_start <= field["span"]["start"] and field["span"]["end"] <= c_end:
            fields[name] = outcome("match", span=field["span"])
        else:
            fields[name] = outcome("unsupported", f"{name}_not_in_value_clause")
    # object and metric: in the value's clause or row, or (a table metric) the header of the value's column
    for name in ("object", "metric"):
        field = f[name]
        if not stated(field):
            fields[name] = outcome("unsupported", f"unknown_{name}_not_rule_checkable")
            continue
        text = P.text(field["span"])
        if text is None or squash(text).casefold() != squash(field["value"]).casefold():
            fields[name] = outcome("mismatch", f"{name}_differs_from_span")
            continue
        if field["span"]["passage"] == pid and c_start <= field["span"]["start"] and field["span"]["end"] <= c_end:
            fields[name] = outcome("match", span=field["span"])
            continue
        column = header_column(P, field["span"], vspan)
        fields[name] = (outcome("match", "header_of_value_column", field["span"]) if column is True
                        else outcome("mismatch", "header_of_another_column") if column is False
                        else outcome("unsupported", f"{name}_not_in_value_clause"))
    kind = item["fields"]["measure_kind"]
    if kind == "rate" and (percent_written or (fields["unit"]["result"] == "match" and
                                               CANONICAL.get(squash(f["unit"].get("value")).casefold()) == "%")
                           or fields["denominator"]["result"] == "match"):
        fields["measure_kind"] = outcome("match", "rate_with_percentage_or_ratio")
    else:
        fields["measure_kind"] = outcome("unsupported", "measure_kind_not_rule_checkable")


def cell_index(line, pos):
    """The 0-based cell number of a markdown table line at character pos, or None."""
    if not line.startswith("|") or pos >= len(line):
        return None
    return line[:pos].count("|") - 1


def header_column(P, header_span, value_span):
    """True / False when header_span is a cell of a table header line (a line followed by a separator row in
    its passage) and value_span sits in a table row line: same column or not. None when not that shape."""
    hp, vp = P.by_id[header_span["passage"]], P.by_id[value_span["passage"]]
    ht, ha = hp["text"], header_span["start"] - hp["start"]
    hl_start = ht.rfind("\n", 0, ha) + 1
    hl_end = ht.find("\n", ha)
    hl_end = len(ht) if hl_end < 0 else hl_end
    following = ht[hl_end + 1:].split("\n", 1)[0] if hl_end < len(ht) else ""
    if not (ht[hl_start:hl_end].startswith("|") and TABLE_RULE.match(following.strip())):
        return None
    vt, va = vp["text"], value_span["start"] - vp["start"]
    vl_start = vt.rfind("\n", 0, va) + 1
    vl_end = vt.find("\n", va)
    vl_end = len(vt) if vl_end < 0 else vl_end
    row = vt[vl_start:vl_end]
    if not row.startswith("|") or TABLE_RULE.match(row.strip()):
        return None
    hc, vc = cell_index(ht[hl_start:hl_end], ha - hl_start), cell_index(row, va - vl_start)
    return None if hc is None or vc is None else hc == vc


def check_item(item, P):
    fields = {}
    quote = P.text(item["locator"])
    if quote is None:
        fields["locator"] = outcome("mismatch", "locator_outside_passage")
    elif squash(quote) == squash(item["quote"]):
        fields["locator"] = outcome("match", span=item["locator"])
    else:
        anywhere = any(squash(item["quote"]) in squash(p["text"]) for p in P.by_id.values())
        fields["locator"] = outcome("mismatch", "quote_elsewhere_in_passages" if anywhere else "quote_not_in_passages")
    if fields["locator"]["result"] != "match":
        for name in KIND_FIELDS[item["kind"]] + (("measure_kind",) if item["kind"] == "measurement" else ()):
            fields[name] = outcome("unsupported", "no_valid_location")
        return fields
    f = item["fields"]
    if item["kind"] == "measurement":
        check_measurement(item, P, fields)
    elif item["kind"] == "identifier":
        field = f["identifier"]
        text = P.text(field["span"])
        if text is None or not P.inside(field["span"], item["locator"]):
            fields["identifier"] = outcome("mismatch", "identifier_outside_quote")
        elif squash(text) == squash(field["value"]):
            fields["identifier"] = outcome("match", span=field["span"])
        elif re.sub(r"[\W_]", "", text).casefold() == re.sub(r"[\W_]", "", field["value"]).casefold():
            fields["identifier"] = outcome("unsupported", "normalized_form_not_verifiable_in_v1")
        else:
            fields["identifier"] = outcome("mismatch", "identifier_differs_from_span")
    elif item["kind"] == "claim":
        field = f["statement"]
        text = P.text(field["span"])
        fields["statement"] = (outcome("match", span=field["span"]) if text is not None
                               and squash(text) == squash(field["value"]) else outcome("mismatch", "statement_differs_from_span"))
        fields["polarity"] = outcome("unsupported", "polarity_is_semantic")
        if f.get("qualifiers"):
            fields["qualifiers"] = outcome("unsupported", "qualifiers_are_semantic")
    elif item["kind"] == "annotation":
        fields["label"] = outcome("unsupported", "annotation_judgement_is_semantic")
    return fields


def status(fields):
    results = {f["result"] for f in fields.values()}
    if results <= {"match"}:
        return "consistent"
    if results & {"mismatch", "not_found"}:
        return "inconsistent"
    return "unchecked"


def check(request):
    """Every item against the request's passages. Raises ContractError on a contract break."""
    passages = request["passages"]
    if len({p["id"] for p in passages}) != len(passages):
        raise ContractError("passages: ids must be unique")
    for p in passages:
        if p["end"] - p["start"] != len(p["text"]):
            raise ContractError(f"passages[{p['id']}]: end - start must equal the length of its text")
    ordered = sorted(passages, key=lambda p: p["start"])
    if any(a["end"] > b["start"] for a, b in zip(ordered, ordered[1:])):
        raise ContractError("passages: ranges of one artifact must not overlap")
    if sum(len(p["text"]) for p in passages) > 20000:
        raise ContractError("passages: at most 20000 characters in all")
    if not any(p["role"] == "primary" for p in passages):
        raise ContractError("passages: one passage at least is the primary one")
    if len({i["id"] for i in request["items"]}) != len(request["items"]):
        raise ContractError("items: ids must be unique")
    P = Passages(passages)
    items = []
    for item in request["items"]:
        fields = check_item(item, P)
        items.append({"id": item["id"], "kind": item["kind"], "status": status(fields), "fields": fields})
    counts = {s: sum(1 for i in items if i["status"] == s) for s in ("consistent", "inconsistent", "unchecked")}
    return {"contract_version": CONTRACT_VERSION, "checker_version": CHECKER_VERSION,
            "subject": request["subject"], "items": items, "counts": counts, "llm_calls": []}
