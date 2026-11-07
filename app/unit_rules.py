"""Units of a sentence: what the model reports for it, numbered and located by the program, checked in form.

The model reads the whole sentence, its neighbours and the cited sources, and reports units (the smallest
statements that can be true or false on their own), each at a candidate segment (segment_rules.py). The program
settles only what a program can: where a fragment is, that every segment was handled, that no negation was
dropped on the way, that a quote or a structured field really exists. It cannot tell that the units are all the
assertions of the sentence (a compound without a connector, such as 获批进入医保, passes the form check), so a
passed form check is recorded as exactly that. A failed one keeps the sentence pending.
"""
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from . import claims as C
from .segment_rules import RULES_VERSION, Segment, split_segments

Kind = Literal["fact", "negated_fact", "condition", "inference", "time", "no_claim"]
Verdict = Literal["supported", "contradicted", "insufficient", "no_claim"]
BAD_QUOTE = {"outside": "quote_outside_citation: {ref}不是本句的引用",
             "not_found": "quote_not_found: 短引不在{ref}原文中"}


# ---- what the model reports (one unit; the sentence's answer is a list of them)

class UnitQuote(BaseModel):
    ref: str
    quote: str
    occurrence: int | None = None  # which place (1-based) when the quote is in the source more than once


class FieldBasis(BaseModel):
    """A negation grounded in a structured field of an analysis result: its dot path from the result body
    (output.deaths, list items by number) and the value found there."""

    ref: str
    path: str
    value: str | int | float | bool | None


class ReportedUnit(BaseModel):
    segment: str  # S-number of the candidate segment the fragment is in
    fragment: str  # verbatim text of the sentence, inside that segment
    kind: Kind
    verdict: Verdict
    quotes: list[UnitQuote] = []
    labels: list[str] = []  # OBJ NUM TIME COND STR REF, as in claims.LABELS
    governed_by: list[str] = []  # segment of this sentence, or an identifier the request pre-assigned
    field_basis: FieldBasis | None = None
    reason: str = ""


# ---- what the program makes of it

@dataclass
class Unit:
    id: str  # U1.. in the order reported, void units included
    segment: str
    fragment: str
    kind: str
    model_verdict: str
    verdict: str = "insufficient"  # after the rules
    reason: str = ""
    start: int | None = None  # offsets in the sentence
    end: int | None = None
    void_reason: str | None = None
    quotes: list[dict] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    field_basis: dict | None = None
    governed_by: list[str] = field(default_factory=list)  # unit numbers, or the request's identifiers
    conditional: bool = False  # a condition, or governed by one: not a stated fact

    @property
    def located(self):
        return self.void_reason is None


@dataclass
class Problem:
    code: str
    segment: str | None = None
    units: tuple[str, ...] = ()
    detail: str = ""


@dataclass
class FormCheck:
    """What the program could check about the shape of the answer. Passing says nothing about whether the units
    are all the assertions the sentence makes."""

    status: Literal["passed", "failed"]
    problems: list[Problem]
    handled: dict[str, list[str]]  # segment -> the located units at it; an empty list is a segment nobody handled
    units: int
    void_units: int
    rules_version: str = RULES_VERSION


@dataclass
class SentenceResult:
    index: int
    segments: list[Segment]
    units: list[Unit]
    form_check: FormCheck
    verdict: str
    reason: str


# ---- numbering and locating

def _occurrences(text, part):
    return len(re.findall("(?=" + re.escape(part) + ")", text))


def _void_reason(reported, fragment, segment):
    if segment is None:
        return "unknown_segment: 句段编号不存在"
    if reported.kind == "no_claim" and not reported.reason.strip():
        return "no_claim_without_reason: 非事实句要写明理由"
    seen = _occurrences(segment.text, fragment) if fragment else 0
    if seen == 0:
        return "fragment_not_in_segment: 片段不在所报句段的原文中"
    if seen > 1:
        return "fragment_not_unique_in_segment: 片段在该句段中出现不止一处"
    return None


def _place(reported, segments):
    """Number the reported units U1.. and locate each fragment in its segment; one that cannot be found exactly
    once there is void, with the reason."""
    by_id = {s.id: s for s in segments}
    placed = []
    for number, item in enumerate(reported, 1):
        fragment, segment = item.fragment.strip(), by_id.get(item.segment)
        unit = Unit(f"U{number}", item.segment, fragment, item.kind, item.verdict,
                    labels=[x for x in dict.fromkeys(item.labels) if x in C.LABELS])
        unit.void_reason = _void_reason(item, fragment, segment)
        if unit.void_reason:
            unit.reason = unit.void_reason
        else:
            unit.start = segment.start + segment.text.index(fragment)
            unit.end = unit.start + len(fragment)
        placed.append(unit)
    return placed


# ---- quotes, fields and the rules per kind

def _check_quote(quote, citations, refs):
    """Where the quote is in its source: every place, and the one the model adopted (the only place counts as
    adopted; with several the model must say which, the first is never assumed)."""
    spans = []
    if quote.ref in citations and quote.ref in refs:
        spans = C.quote_spans(refs[quote.ref]["text"], quote.quote)
    if quote.ref not in citations:
        status = "outside"
    elif not spans:
        status = "not_found"
    else:
        status = "found" if len(spans) == 1 else "ambiguous"
    adopted = 1 if status == "found" else None
    if status == "ambiguous" and quote.occurrence in range(1, len(spans) + 1):
        adopted = quote.occurrence
    return {"ref": quote.ref, "quote": quote.quote, "status": status, "positions": [list(s) for s in spans],
            "adopted": adopted}


def _same_value(found, expected):
    if isinstance(found, bool) or isinstance(expected, bool):
        return found is expected
    return found == expected


def _field_holds(basis, citations, refs):
    source = refs.get(basis.ref)
    if basis.ref not in citations or not source or source.get("kind") != "analysis_result":
        return False
    try:
        node = json.loads(source["text"])
    except ValueError:
        return False
    for part in basis.path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return False
    return _same_value(node, basis.value)


def _ground_problems(item, unit, usable):
    """A supported or contradicted unit needs a located quote (or, for a negation, a field that checks out).
    What the evidence is about, a negation's object and scope included, is the semantic check's: a death count of
    0 supports "no deaths" without a negation word in it, so none is asked for."""
    problems = [BAD_QUOTE[q["status"]].format(ref=q["ref"]) for q in unit.quotes if q["status"] in BAD_QUOTE]
    basis = unit.field_basis
    if basis and not basis["holds"]:
        problems.append("field_basis_invalid: 结构字段的路径不存在或值不符")
    field_grounds = bool(basis and basis["holds"] and item.kind == "negated_fact")
    if item.verdict in ("supported", "contradicted") and not usable and not field_grounds:
        problems.append("missing_quote: 判定缺少定位成功且已确定出现处的短引")
    return problems


def _kind_problems(item, unit, fact_section, known_numbers):
    problems = []
    if item.verdict == "supported" and item.kind == "inference" and fact_section:
        problems.append("inference_in_fact_section: 推断或判断写在事实部分，不能算已支持的事实")
    new_numbers = set(C.numeric_tokens(unit.fragment)) - known_numbers
    if item.verdict == "no_claim" and item.kind != "condition" and new_numbers:
        problems.append("no_claim_with_numbers: 含具体数字的句子是可核查陈述")
    return problems


def _number_problems(item, unit, citations, refs):
    """A supported fragment's numbers are in the cited texts, or its reason names each (a conversion, a rounding)."""
    if item.verdict != "supported":
        return []
    cited = C.cited_texts([r for r in citations if r in refs], refs)
    unnamed = C.unnamed_numbers(C.numeric_hints(unit.fragment, cited), item.reason)
    return ["numeric_unexplained: 数字" + "、".join(unnamed) + "不在所引原文中，也未说明换算"] if unnamed else []


def _judge_unit(unit, item, citations, refs, fact_section, known_numbers):
    """Apply the rules to one located unit. What a condition governs asserts nothing, so a model verdict of
    supported or no_claim on it means "nothing stated"; insufficient (cannot tell it is only a condition) and
    contradicted still count."""
    unit.quotes = [_check_quote(q, citations, refs) for q in item.quotes]
    unit.field_basis = (dict(item.field_basis.model_dump(), holds=_field_holds(item.field_basis, citations, refs))
                        if item.field_basis else None)
    if unit.conditional and item.verdict in ("supported", "no_claim"):
        unit.verdict, unit.reason = "no_claim", item.reason
        return
    usable = [q for q in unit.quotes if q["adopted"] is not None]
    numbers = _number_problems(item, unit, citations, refs)
    if numbers:
        unit.labels = list(dict.fromkeys([*unit.labels, "NUM"]))
    problems = _ground_problems(item, unit, usable) + _kind_problems(item, unit, fact_section, known_numbers) + numbers
    verdict = "no_claim" if item.kind == "no_claim" else item.verdict
    unit.verdict = "insufficient" if problems else verdict
    unit.reason = item.reason
    if problems:
        unit.reason = "；".join(problems) + ("｜模型理由：" + item.reason if item.reason else "")


# ---- scope, form check, verdict

def _map_governors(placed, reported, known_ids):
    """governed_by names a segment of this sentence (it becomes the numbers of the units located there) or an
    identifier the request pre-assigned; anything else cannot be mapped."""
    in_segment = defaultdict(list)
    for unit in placed:
        if unit.located:
            in_segment[unit.segment].append(unit.id)
    problems = []
    for unit, item in zip(placed, reported):
        for name in (item.governed_by if unit.located else ()):
            targets = [t for t in in_segment.get(name, []) if t != unit.id]
            if targets:
                unit.governed_by += targets
            elif name in known_ids:
                unit.governed_by.append(name)
            else:
                problems.append(Problem("governed_by_unmapped", unit.segment, (unit.id,), name))
    return problems


def _mark_conditional(placed):
    """A condition is not a stated fact, and neither is what it governs."""
    by_id = {u.id: u for u in placed}
    for unit in placed:
        unit.conditional = unit.located and unit.kind == "condition"
    changed = True
    while changed:
        changed = False
        for unit in placed:
            if unit.located and not unit.conditional and any(
                    by_id[g].conditional for g in unit.governed_by if g in by_id):
                unit.conditional = changed = True


def _dropped_negations(segment, here):
    """A negation word of the segment that no unit's fragment reaches: it was lost between segment and unit."""
    return [Problem("negation_unreached", segment.id, tuple(u.id for u in here), m.text)
            for m in segment.markers
            if m.kind == "negation" and not any(u.start < m.start + len(m.text) and m.start < u.end for u in here)]


def _undeclared_overlaps(located, named):
    """Two units over the same characters, unless one is declared as governed by the other's segment."""
    return [Problem("undeclared_overlap", None, (a.id, b.id))
            for i, a in enumerate(located) for b in located[i + 1:]
            if a.start < b.end and b.start < a.end and b.segment not in named[a.id] and a.segment not in named[b.id]]


def _form_check(segments, reported, placed, unmapped):
    problems = list(unmapped)
    if not reported:
        problems.append(Problem("empty_result"))
    located = [u for u in placed if u.located]
    handled = {}
    for segment in segments:
        here = [u for u in located if u.segment == segment.id]
        handled[segment.id] = [u.id for u in here]
        problems += _dropped_negations(segment, here) if here else [Problem("segment_unhandled", segment.id)]
    problems += _undeclared_overlaps(located, {u.id: set(i.governed_by) for u, i in zip(placed, reported)})
    return FormCheck("failed" if problems else "passed", problems, handled, len(placed), len(placed) - len(located))


def _sentence_verdict(form, units):
    """Sum of the located units; a condition has already been read as "nothing stated" unless it is a problem. A
    contradiction stands even when the form check failed (a real negative result is never turned into unknown)."""
    located = [u for u in units if u.located]
    verdicts = {u.verdict for u in located}
    if "contradicted" in verdicts:
        return "contradicted", ""
    if form.status == "failed":
        return "insufficient", "form_check_failed: " + "、".join(sorted({p.code for p in form.problems}))
    if "insufficient" in verdicts:
        return "insufficient", "unit_insufficient: " + "、".join(u.id for u in located if u.verdict == "insufficient")
    return ("supported" if "supported" in verdicts else "no_claim"), ""


def check_sentence(claim, reported, refs, *, known_ids=frozenset(), fact_section=False,
                   known_numbers=frozenset()):
    """claim: a sentence of claims.sentences; reported: the model's units for it; refs: core.references.
    known_ids: identifiers the request pre-assigned, which governed_by may name. fact_section: the sentence is in
    a part of the report where only facts belong, so a supported inference there is not accepted.
    known_numbers: numbers the draft already cites elsewhere; a unit that only repeats them may be no_claim."""
    segments = split_segments(claim["text"])
    placed = _place(reported, segments)
    unmapped = _map_governors(placed, reported, known_ids)
    _mark_conditional(placed)
    for unit, item in zip(placed, reported):
        if unit.located:
            _judge_unit(unit, item, claim["citations"], refs, fact_section, known_numbers)
    form = _form_check(segments, reported, placed, unmapped)
    verdict, reason = _sentence_verdict(form, placed)
    return SentenceResult(claim["index"], segments, placed, form, verdict, reason)


def coverage_view(result):
    """What the coverage check is told about one sentence: the units that state something, and the conditions
    (with what they govern), which are not stated facts and so never a gap in the draft."""
    located = [u for u in result.units if u.located]
    return {"index": result.index,
            "stated": [u.fragment for u in located if not u.conditional and u.verdict != "no_claim"],
            "conditional": [u.fragment for u in located if u.conditional]}
