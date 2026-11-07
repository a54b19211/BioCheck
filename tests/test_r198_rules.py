"""The r198 sentences of 06 section 5.3 against the rule layer, with fixed model answers and no model call. This
tests the program boundary only: that the named defects show up where the program can see them and that the
three control sentences are left alone. Whether a real model would then answer well is not tested here."""
import json
from pathlib import Path

from app import claims as C
from app.core import references
from app.segment_rules import VisibleSource, split_segments, time_clues
from app.unit_rules import ReportedUnit, check_sentence, coverage_view

SEALED = json.loads((Path(__file__).parent / "fixtures" / "r198_sealed.json").read_text(encoding="utf-8"))
E1 = SEALED["evidence"]
SENTENCES = SEALED["sentences"]
# the lines the sealed review quoted from E1
RISK = ("On 14 April 2020, WHO revised the risk assessment for this event from High to Moderate at the national "
        "and regional levels, while the risk level remained Low at the global level.")
CASES = ("As of 19 May 2020, a total of 3462 EVD cases, including 3317 confirmed and 145 probable cases have been "
         "reported. Of these, 2279 people died (overall case fatality ratio 66%) and 1171 survived.")
DELAY = ("Data in recent weeks are subject to delays in case confirmation and reporting, as well as ongoing data "
         "cleaning.")


def refs(extra=""):
    return references({"evidence": [{"id": "E1", "title": "", "source_kind": "official", "verified": True,
                                     "published_at": E1["published_at"], "text": E1["text"] + extra}]})


def sentence(text):
    (claim,) = C.sentences(text)
    return claim


def answer(text, *units, extra="", **options):
    return check_sentence(sentence(text), list(units), refs(extra), **options)


def unit(segment, fragment, quote=None, kind="fact", verdict="supported", **extra):
    quotes = [{"ref": "E1", "quote": quote}] if quote else []
    return ReportedUnit(segment=segment, fragment=fragment, kind=kind, verdict=verdict, quotes=quotes, **extra)


def whole(text, quote, kind="fact", verdict="supported", **extra):
    """One unit per segment, its fragment the segment without the citation tail, all on one quote."""
    return [unit(s.id, s.text.removesuffix("[E1]。"), quote, kind, verdict, **extra) for s in split_segments(text)]


def test_the_fixture_quotes_are_in_the_source_once():
    for quote in (RISK, CASES, DELAY):
        assert len(C.quote_spans(E1["text"], quote)) == 1


def test_c1_the_negation_in_judgment_1_is_a_segment_that_needs_a_unit_and_a_basis():
    text = SEALED["judgment_1"].rstrip("。") + "[E1]。"
    segments = split_segments(text)
    negated = [s for s in segments if "虽未宣布为PHEIC" in s.text]
    assert [s.text for s in negated] == ["虽未宣布为PHEIC"] and [m.text for m in negated[0].markers] == ["未"]
    s1, s2, s3, s4 = (s.id for s in segments)
    risk = [unit(s1, "该疫情被WHO评估为具有中等国家/区域风险和低全球风险", RISK),
            unit(s3, "但因存在复发风险和监测需求", RISK),
            unit(s4, "仍构成需要持续核查和监测的公共卫生威胁", RISK, kind="inference")]
    # the old answer: the risk quote carried the whole sentence. Without a unit for the negation the form check fails.
    skipped = answer(text, *risk)
    assert (skipped.verdict, skipped.form_check.status) == ("insufficient", "failed")
    assert [(p.code, p.segment) for p in skipped.form_check.problems] == [("segment_unhandled", s2)]
    # with one, the program asks for a located quote and nothing about what it says: whether RISK is about the
    # negation is the semantic check's (C1 is closed by the real answer: a quote that holds the negation)
    unsure = answer(text, *risk, unit(s2, "虽未宣布为PHEIC", RISK, kind="negated_fact", verdict="insufficient"))
    assert (unsure.verdict, unsure.form_check.status) == ("insufficient", "passed")
    bare = answer(text, *risk, unit(s2, "虽未宣布为PHEIC", None, kind="negated_fact"))
    assert bare.verdict == "insufficient" and bare.units[3].reason.startswith("missing_quote")
    extra = " WHO has not declared this event a PHEIC."
    stated = answer(text, *risk, unit(s2, "虽未宣布为PHEIC", extra.strip(), kind="negated_fact"), extra=extra)
    assert (stated.verdict, stated.form_check.status) == ("supported", "passed")


def test_c2_the_negated_judgment_of_sentence_19_is_its_own_unit_and_not_a_supported_fact():
    text = SENTENCES["19"]
    segments = split_segments(text)
    assert [s.text for s in segments][-1] == "未触发高级别核查机制[E1]。" and segments[-1].markers[0].text == "未"
    units = whole(text, RISK, kind="inference")
    assert answer(text, *units).verdict == "supported"  # nothing says it is out of place ...
    in_facts = answer(text, *units, fact_section=True)  # ... until the report section does
    assert in_facts.verdict == "insufficient" and in_facts.units[-1].reason.startswith("inference_in_fact_section")
    assert answer(text, *units[:-1]).form_check.problems[0].code == "segment_unhandled"  # or the unit is left out


def conditions(text):
    """A model's reading of sentences 13 and 21: a segment with an if-marker is a condition, every other segment is
    governed by the condition before it."""
    units, governor = [], None
    for s in split_segments(text):
        fragment = s.text.removesuffix("[E1]。")
        if s.markers and s.markers[0].kind == "condition" and not s.text.startswith("则"):
            governor = s.id
            units.append(unit(s.id, fragment, kind="condition", verdict="no_claim"))
        else:
            units.append(unit(s.id, fragment, kind="inference", governed_by=[governor]))
    return units


def test_c3_conditions_in_sentences_13_and_21_are_not_stated_facts_and_are_handled_alike():
    results = {n: answer(SENTENCES[n], *conditions(SENTENCES[n])) for n in ("13", "21")}
    for result in results.values():
        assert (result.verdict, result.form_check.status) == ("no_claim", "passed")
        view = coverage_view(result)
        assert view["stated"] == [] and view["conditional"]  # nothing the coverage check could call a missing fact
    assert [u.conditional for u in results["21"].units] == [True] * len(results["21"].units)


def test_c4_the_first_sentence_gets_a_time_clue_and_the_controls_none():
    source = VisibleSource(ref="E1", text=E1["text"], published_at=E1["published_at"],
                           first_seen_at="2026-11-06T03:10:00Z", replay_mode="current")
    (clue,) = time_clues(SENTENCES["1"], [source])
    assert (clue["stated"], clue["later_dates"]) == ("2020-05-07", ["2020-05-14", "2020-05-19"])
    assert clue["first_seen_at"] == source.first_seen_at and clue["replay_mode"] == "current"
    assert time_clues(SENTENCES["1"].replace("WHO通报", "WHO通报的现存版本"), [source]) == []
    assert [time_clues(SENTENCES[n], [source]) for n in ("2", "6", "10")] == [[], [], []]


def test_the_controls_2_6_10_are_not_hurt():
    two, six, ten = SENTENCES["2"], SENTENCES["6"], SENTENCES["10"]
    # 10: the lead-in 首先 states nothing; 不完全准确 holds a 不, which the unit's fragment reaches
    lead_in = unit("S1", "首先", kind="no_claim", verdict="no_claim", reason="序数")
    limit = unit("S4", "这意味着当时的病例数和死亡数可能不完全准确", DELAY, kind="inference")
    results = [answer(two, *whole(two, RISK)), answer(six, *whole(six, CASES)),
               answer(ten, lead_in, *whole(ten, DELAY)[1:3], limit)]
    assert [(r.verdict, r.form_check.status) for r in results] == [("supported", "passed")] * 3
    assert [len(r.segments) for r in results] == [2, 4, 4]
