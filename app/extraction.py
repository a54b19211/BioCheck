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
CHECKER_VERSION = "biocheck.extraction_rules.v2"  # v2: whole numbers, same table, groups, rate evidence

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
    elif not P.whole_number(vspan):
        fields["value"] = outcome("mismatch", "value_span_not_a_whole_number")  # "0" of 100%, "10" of -10%
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
    # the value's own context: in a table row, the row (one record); in prose, the value's group, and only if
    # that group names no other quantity (numbers other than the value and the partners of its ratio)
    row = P.row(vspan)
    g_start, g_end = row if row else P.group(vspan)
    partners = {(vspan["start"], vspan["end"])}
    for r in mine_ratios(ratios, vspan, f.get("denominator")):
        partners |= {(r[0], r[1]), (r[3], r[4])}

    def own_context(field_span):
        if field_span["passage"] != pid or not (g_start <= field_span["start"] and field_span["end"] <= g_end):
            return "outside"
        if row:
            return "row"
        text = passage["text"][g_start - passage["start"]:g_end - passage["start"]]
        blanked = DATE.sub(lambda m: " " * len(m.group()), TIMESTAMP.sub(lambda m: " " * len(m.group()), text))
        others = [m for m in NUMBER.finditer(blanked.translate(FULLWIDTH))
                  if (g_start + m.start(), g_start + m.end()) not in partners
                  and not (field_span["start"] <= g_start + m.start() and g_start + m.end() <= field_span["end"])]
        return "several" if others else "group"

    # period and scope: in the value's own context
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
        where = own_context(field["span"]) if text is not None else "outside"
        if text is None or squash(text).casefold() != squash(field["value"]).casefold():
            fields[name] = outcome("mismatch", f"{name}_differs_from_span")
        elif where in ("row", "group"):
            fields[name] = outcome("match", span=field["span"])
        elif where == "several":
            fields[name] = outcome("unsupported", "several_quantities_in_value_group")
        else:
            fields[name] = outcome("unsupported", f"{name}_not_in_value_group")
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
        where = own_context(field["span"])
        if where in ("row", "group"):
            fields[name] = outcome("match", span=field["span"])
            continue
        if where == "several":
            fields[name] = outcome("unsupported", "several_quantities_in_value_group")
            continue
        column = header_column(P, field["span"], vspan)
        if column is True:
            fields[name] = outcome("match", "header_of_value_column", field["span"])
        elif column in ("another_column", "another_table"):
            fields[name] = outcome("mismatch", "header_of_" + column)
        else:
            fields[name] = outcome("unsupported", "header_table_not_provable" if column == "unprovable"
                                   else f"{name}_not_in_value_group")
    kind = item["fields"]["measure_kind"]
    # a rate needs positive evidence: a percentage written at the value, or a ratio actually stated for it -
    # "no denominator in the clause" proves nothing (Codex r106)
    ratio_shown = fields["denominator"]["result"] == "match" and fields["denominator"].get("reason") in (
        None, "percentage_of_stated_ratio")
    if kind == "rate" and fields["value"]["result"] == "match" and (percent_written or ratio_shown):
        fields["measure_kind"] = outcome("match", "rate_with_percentage_or_ratio")
    else:
        fields["measure_kind"] = outcome("unsupported", "measure_kind_not_rule_checkable")


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
