"""Extraction consistency (v3 6.2): does a processing product correspond to the fixed text it was extracted
from? Rules only, no model calls. It checks correspondence, never whether a fact is true.

Each field of an item names its own span in one of the request's passages (bounded slices of one fixed
artifact, each with its own offsets - a table's header and a data row are two passages, never one invented
continuous range), or says explicitly that the source states none ("unknown" / "not_applicable"). The value is
the anchor: unit, denominator, period and scope must belong to that same quantity (the unit written right at
the number, the denominator in an explicit ratio of that number within its clause, period and scope in that
clause), and a table metric's header must head the value's column. Whatever the rules cannot prove is
"unsupported", which makes the item "unchecked", never "consistent"."""

import dataclasses
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

from .claims import DATE, TIMESTAMP

CONTRACT_VERSION = "extraction_consistency.v1"
# Bump whenever a rule, the unit table, the number conversions or the clause rule below changes.
CHECKER_VERSION = "biocheck.extraction_rules.v3"  # v3: numbers bounded to MAX_DIGITS (Codex r107)

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
GROUP_STOP = CLAUSE_STOP + "，,、"  # a clause can hold several groups: "成人10人中2人有效，儿童100人中80人有效"
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


MAX_DIGITS = 18  # numbers with more significant digits are beyond these rules: unsupported, never approximated


def number(text):
    """A written number as a Decimal: only full-width digits and thousands separators are converted
    (versioned with CHECKER_VERSION); anything else, or more than MAX_DIGITS digits, is not a number here."""
    t = squash(text).translate(FULLWIDTH).replace("−", "-")
    if not re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", t):
        return None
    if sum(c.isdigit() for c in t) > MAX_DIGITS:
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

    def group(self, span):
        """Like clause(), also cut at "，,、": the smallest stretch naming one quantity's own context."""
        p = self.by_id[span["passage"]]
        text, a, b = p["text"], span["start"] - p["start"], span["end"] - p["start"]
        start = max(text.rfind(c, 0, a) for c in GROUP_STOP) + 1
        ends = [i for i in (text.find(c, b) for c in GROUP_STOP) if i >= 0]
        return p["start"] + start, p["start"] + (min(ends) if ends else len(text))

    def row(self, span):
        """(start, end) in artifact offsets of the markdown table row line holding the span, or None."""
        p = self.by_id[span["passage"]]
        text, a = p["text"], span["start"] - p["start"]
        start, end = text.rfind("\n", 0, a) + 1, text.find("\n", a)
        line = text[start:len(text) if end < 0 else end]
        if not line.startswith("|") or TABLE_RULE.match(line.strip()):
            return None
        return p["start"] + start, p["start"] + start + len(line)

    def whole_number(self, span):
        """The span is exactly one written number of its passage (sign, decimals, thousands included)."""
        p = self.by_id[span["passage"]]
        text = p["text"].translate(FULLWIDTH)
        a, b = span["start"] - p["start"], span["end"] - p["start"]
        return any(m.span() == (a, b) for m in NUMBER.finditer(text))

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


def mine_ratios(ratios, vspan, den):
    """The ratios this value takes part in: as numerator, or as the ratio its stated denominator belongs to."""
    out = [r for r in ratios if (r[0], r[1]) == (vspan["start"], vspan["end"])]
    if stated(den):
        out += [r for r in ratios if (r[3], r[4]) == (den["span"]["start"], den["span"]["end"])]
    return out


def stated(field):
    return field is not None and field.get("state", "stated") == "stated"


@dataclasses.dataclass
class ValueClause:
    """The clause around a measurement's value in its passage, and the unit written right at the number."""

    pid: str
    start: int  # artifact offset of the clause
    text: str
    passage: dict
    after: int  # passage offset right after the value
    at_value: tuple | None

    @property
    def percent_written(self):
        return self.passage["text"][self.after:self.after + 1] in ("%", "％")


def value_clause(P, vspan):
    pid, c_start, _c_end, clause_text = P.clause(vspan)
    passage = P.by_id[pid]
    after = vspan["end"] - passage["start"]
    return ValueClause(pid, c_start, clause_text, passage, after, unit_at(passage["text"], after))


def _ends(span):
    return span["start"], span["end"]


def _value_result(item, P, vtext):
    value_field = item["fields"]["value"]
    vspan = value_field["span"]
    if not P.inside(vspan, item["locator"]):
        return outcome("mismatch", "value_outside_quote")
    if not P.whole_number(vspan):
        return outcome("mismatch", "value_span_not_a_whole_number")  # "0" of 100%, "10" of -10%
    if number(vtext) is None or number(value_field["value"]) is None:
        return outcome("unsupported", "value_not_a_plain_number")
    if number(vtext) != number(value_field["value"]):
        return outcome("mismatch", "value_differs_from_span")
    return outcome("match", span=vspan)


def _unit_result(unit, P, vc):
    """The unit is written right at the number: the unit table's longest spelling there."""
    at_value = vc.at_value
    if not stated(unit):
        return (outcome("mismatch", "unit_written_at_value:" + at_value[0]) if at_value
                else outcome("match", "no_unit_at_value"))
    canonical = CANONICAL.get(squash(unit["value"]).casefold())
    utext = P.text(unit["span"])
    if canonical is None:
        return outcome("unsupported", "unit_not_in_table")
    if utext is None or CANONICAL.get(squash(utext).casefold()) != canonical:
        return outcome("mismatch", "unit_differs_from_span")
    if at_value and (unit["span"]["passage"], unit["span"]["start"]) == (vc.pid, vc.passage["start"] + at_value[1]) \
            and at_value[0] == canonical:
        return outcome("match", span=unit["span"])
    if at_value and at_value[0] != canonical:
        return outcome("mismatch", "another_unit_at_value:" + at_value[0])
    return outcome("mismatch", "unit_not_at_value")


def _percentage_of_ratio(den, shown, ratios):
    """A percentage of a ratio stated in the same clause: n/N*100 rounded to the digits shown."""
    places = max(0, -shown.as_tuple().exponent)
    of_den = [r for r in ratios if (r[3], r[4]) == _ends(den["span"])]
    # numbers are bounded by MAX_DIGITS, so a 60-digit context computes n/N*100 exactly enough
    with localcontext() as ctx:
        ctx.prec = 60
        fits = [r for r in of_den if r[2] is not None and r[5]
                and (r[2] / r[5] * 100).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP) == shown]
    if fits:
        return outcome("match", "percentage_of_stated_ratio", den["span"])
    if of_den:
        return outcome("mismatch", "percentage_does_not_follow_from_ratio")
    return outcome("unsupported", "denominator_relation_not_rule_checkable")


def _denominator_result(den, P, vc, ratios, mine, vtext):
    """The denominator of an explicit ratio of this very number, inside its clause. "unknown" is wrong for
    certain only when this very number has a stated denominator; other ratios or totals in the clause may
    belong to other quantities."""
    if not stated(den):
        if mine:
            return outcome("mismatch", "the_value_has_a_stated_denominator")
        if ratios or TOTAL.search(vc.text):
            return outcome("unsupported", "other_ratio_or_total_in_value_clause")
        return outcome("match", "no_denominator_in_value_clause")
    dtext = P.text(den["span"])
    wanted = number(den["value"])
    if dtext is not None and (wanted is None or number(dtext) is None):
        return outcome("unsupported", "denominator_not_a_supported_number")
    if dtext is None or number(dtext) != wanted:
        return outcome("mismatch", "denominator_differs_from_span")
    if any((r[3], r[4]) == _ends(den["span"]) for r in mine):
        return outcome("match", span=den["span"])
    if mine:
        return outcome("mismatch", "the_value_has_another_denominator")
    if vc.percent_written and number(vtext) is not None:
        return _percentage_of_ratio(den, number(vtext), ratios)
    return outcome("unsupported", "denominator_relation_not_rule_checkable")


def _own_context(P, vspan, vc, ratios, den):
    """where(field_span) -> "row" | "group" | "several" | "outside": the value's own context is, in a table row,
    the row (one record); in prose, the value's group, and only if that group names no other quantity (numbers
    other than the value and the partners of its ratio)."""
    row = P.row(vspan)
    g_start, g_end = row if row else P.group(vspan)
    partners = {_ends(vspan)}
    for r in mine_ratios(ratios, vspan, den):
        partners |= {(r[0], r[1]), (r[3], r[4])}
    passage = vc.passage

    def where(field_span):
        if field_span["passage"] != vc.pid or not (g_start <= field_span["start"] and field_span["end"] <= g_end):
            return "outside"
        if row:
            return "row"
        text = passage["text"][g_start - passage["start"]:g_end - passage["start"]]
        blanked = DATE.sub(lambda m: " " * len(m.group()), TIMESTAMP.sub(lambda m: " " * len(m.group()), text))
        others = [m for m in NUMBER.finditer(blanked.translate(FULLWIDTH))
                  if (g_start + m.start(), g_start + m.end()) not in partners
                  and not (field_span["start"] <= g_start + m.start() and g_start + m.end() <= field_span["end"])]
        return "several" if others else "group"

    return where


def _period_or_scope_result(name, field, P, vc, where):
    """Period and scope stand in the value's own context."""
    if not stated(field):
        if name == "period":
            # a date in the clause may be something else (a pharmacopoeia edition "2015年版"): not proof
            return (outcome("unsupported", "date_in_value_clause") if DATE.search(TIMESTAMP.sub(" ", vc.text))
                    else outcome("match", "no_date_in_value_clause"))
        return outcome("unsupported", "absence_of_scope_not_rule_checkable")
    text = P.text(field["span"])
    place = where(field["span"]) if text is not None else "outside"
    if text is None or squash(text).casefold() != squash(field["value"]).casefold():
        return outcome("mismatch", f"{name}_differs_from_span")
    if place in ("row", "group"):
        return outcome("match", span=field["span"])
    if place == "several":
        return outcome("unsupported", "several_quantities_in_value_group")
    return outcome("unsupported", f"{name}_not_in_value_group")


def _object_or_metric_result(name, field, P, vspan, where):
    """Object and metric stand in the value's clause or row, or (a table metric) head the value's column."""
    if not stated(field):
        return outcome("unsupported", f"unknown_{name}_not_rule_checkable")
    text = P.text(field["span"])
    if text is None or squash(text).casefold() != squash(field["value"]).casefold():
        return outcome("mismatch", f"{name}_differs_from_span")
    place = where(field["span"])
    if place in ("row", "group"):
        return outcome("match", span=field["span"])
    if place == "several":
        return outcome("unsupported", "several_quantities_in_value_group")
    column = header_column(P, field["span"], vspan)
    if column is True:
        return outcome("match", "header_of_value_column", field["span"])
    if column in ("another_column", "another_table"):
        return outcome("mismatch", "header_of_" + column)
    return outcome("unsupported", "header_table_not_provable" if column == "unprovable"
                   else f"{name}_not_in_value_group")


def _kind_result(kind, fields, vc):
    """A rate needs positive evidence: a percentage written at the value, or a ratio actually stated for it -
    "no denominator in the clause" proves nothing."""
    ratio_shown = fields["denominator"]["result"] == "match" and fields["denominator"].get("reason") in (
        None, "percentage_of_stated_ratio")
    if kind == "rate" and fields["value"]["result"] == "match" and (vc.percent_written or ratio_shown):
        return outcome("match", "rate_with_percentage_or_ratio")
    return outcome("unsupported", "measure_kind_not_rule_checkable")


def check_measurement(item, P, fields):
    """Field results of a measurement item, anchored on its value, into `fields`."""
    f = item["fields"]
    if not stated(f["value"]):
        raise ContractError(f"items[{item['id']}].fields.value: a measurement states its value")
    vspan = f["value"]["span"]
    vtext = P.text(vspan)
    if vtext is None:
        fields["value"] = outcome("mismatch", "span_outside_passage")
        for name in KIND_FIELDS["measurement"] + ("measure_kind",):
            fields.setdefault(name, outcome("unsupported", "no_valid_value_span"))
        return
    fields["value"] = _value_result(item, P, vtext)
    vc = value_clause(P, vspan)
    fields["unit"] = _unit_result(f["unit"], P, vc)
    ratios = ratios_in(vc.text, vc.start)
    mine = [r for r in ratios if (r[0], r[1]) == _ends(vspan)]
    fields["denominator"] = _denominator_result(f["denominator"], P, vc, ratios, mine, vtext)
    where = _own_context(P, vspan, vc, ratios, f.get("denominator"))
    for name in ("period", "scope"):
        fields[name] = _period_or_scope_result(name, f[name], P, vc, where)
    for name in ("object", "metric"):
        fields[name] = _object_or_metric_result(name, f[name], P, vspan, where)
    fields["measure_kind"] = _kind_result(item["fields"]["measure_kind"], fields, vc)


def cell_index(line, pos):
    """The 0-based cell number of a markdown table line at character pos, or None."""
    if not line.startswith("|") or pos >= len(line):
        return None
    return line[:pos].count("|") - 1


def header_column(P, header_span, value_span):
    """The table metric check: True when header_span is a cell of the header of the table the value's row
    belongs to, in the same column; "another_column" / "another_table" when it is a header cell of the wrong
    column or of another table; "unprovable" when header and row are in different passages (non-contiguous
    slices cannot prove they are one table); None when the shapes are not a table header and row."""
    if header_span["passage"] != value_span["passage"]:
        return "unprovable"
    p = P.by_id[value_span["passage"]]
    lines, pos = [], 0
    for line in p["text"].split("\n"):
        lines.append((pos, pos + len(line), line))
        pos += len(line) + 1

    def at(offset):
        return next((i for i, (s, e, _) in enumerate(lines) if s <= offset <= e), None)

    def heads(i):
        return (i + 1 < len(lines) and lines[i][2].startswith("|") and lines[i + 1][2].startswith("|")
                and bool(TABLE_RULE.match(lines[i + 1][2].strip())))

    hi, vi = at(header_span["start"] - p["start"]), at(value_span["start"] - p["start"])
    if hi is None or vi is None or not lines[vi][2].startswith("|") or not heads(hi):
        return None
    own = None  # the header of the value row's table: the nearest header above it inside its "|" block
    for i in range(vi - 1, -1, -1):
        if not lines[i][2].startswith("|"):
            break
        if heads(i):
            own = i
            break
    if own is None:
        return "unprovable"
    if own != hi:
        return "another_table"
    hc = cell_index(lines[hi][2], header_span["start"] - p["start"] - lines[hi][0])
    vc = cell_index(lines[vi][2], value_span["start"] - p["start"] - lines[vi][0])
    if hc is None or vc is None:
        return None
    return True if hc == vc else "another_column"


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
