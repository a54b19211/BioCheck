from app import claims as C
from app.core import references
from app.unit_rules import ReportedUnit, check_sentence, coverage_view

QUOTE = "ORR was 32%"
EVIDENCE = [{"id": "E1", "title": "", "source_kind": "research", "verified": True, "published_at": "2020-01-01",
             "text": "ORR was 32% in one arm. No deaths were reported. The dose was 3 mg. The dose was 3 mg again. "
             "The death count was 0."}]
ANALYSIS = [{"id": "A1", "method": "count", "output": {"deaths": 0, "cases": [4, 5]}}]
REFS = references({"evidence": EVIDENCE, "analysis_results": ANALYSIS})


def unit(segment, fragment, kind="fact", verdict="supported", quote=QUOTE, **extra):
    quotes = [{"ref": "E1", "quote": quote}] if quote else []
    return ReportedUnit(segment=segment, fragment=fragment, kind=kind, verdict=verdict, quotes=quotes, **extra)


def check(text, *units, **options):
    return check_sentence(C.sentences(text)[0], list(units), REFS, **options)


def codes(result):
    return sorted(p.code for p in result.form_check.problems)


def test_one_quote_does_not_carry_a_compound_sentence():
    text = "ORR为32%，且死亡率为5%[E1]。"
    bare = check(text, unit("S1", "ORR为32%"), unit("S2", "且死亡率为5%", quote=None))
    assert bare.verdict == "insufficient"  # the second unit has no quote of its own
    assert [u.verdict for u in bare.units] == ["supported", "insufficient"] and "missing_quote" in bare.units[1].reason
    assert check(text, unit("S1", "ORR为32%"), unit("S2", "且死亡率为5%", quote="No deaths")).verdict == "supported"
    # a quote that is not in the cited text does not count either
    invented = check(text, unit("S1", "ORR为32%"), unit("S2", "且死亡率为5%", quote="mortality 5%"))
    assert invented.units[1].reason.startswith("quote_not_found") and invented.verdict == "insufficient"


def test_a_segment_nobody_handled_keeps_the_sentence_pending():
    result = check("ORR为32%，且死亡率为5%[E1]。", unit("S1", "ORR为32%"))
    assert (result.verdict, codes(result)) == ("insufficient", ["segment_unhandled"])
    assert result.form_check.status == "failed" and result.form_check.handled == {"S1": ["U1"], "S2": []}
    assert check("ORR为32%[E1]。").form_check.problems[0].code == "empty_result"
    lead_in = unit("S1", "综上所述", kind="no_claim", verdict="no_claim", quote=None, reason="过渡")
    # a segment that states nothing is handled by saying so
    assert check("综上所述，ORR为32%[E1]。", lead_in, unit("S2", "ORR为32%")).verdict == "supported"
    assert check("综上所述[E1]。", lead_in).verdict == "no_claim"
    assert check("综上所述[E1]。", unit("S1", "综上所述", kind="no_claim", verdict="no_claim", quote=None)).units[
        0].void_reason.startswith("no_claim_without_reason")
    number = unit("S1", "ORR为32%", kind="no_claim", verdict="no_claim", quote=None, reason="过渡")
    assert check("ORR为32%[E1]。", number).units[0].reason.startswith("no_claim_with_numbers")  # a number is checkable


def test_a_fragment_that_cannot_be_located_voids_the_unit():
    text = "共3例，其中3例死亡，另3例3例[E1]。"
    result = check(text, unit("S1", "共3例"), unit("S2", "其中4例死亡"), unit("S3", "3例", quote=None),
                   unit("S9", "共3例"), unit("S2", "其中3例死亡"))
    assert [(u.id, u.void_reason.split(":")[0] if u.void_reason else None) for u in result.units] == [
        ("U1", None), ("U2", "fragment_not_in_segment"), ("U3", "fragment_not_unique_in_segment"),
        ("U4", "unknown_segment"), ("U5", None)]
    assert codes(result) == ["segment_unhandled"] and result.form_check.void_units == 3  # S3 has no unit left
    assert text[result.units[4].start:result.units[4].end] == "其中3例死亡"


def test_a_negation_dropped_between_segment_and_unit_fails_the_form_check():
    kept = check("本研究未进行随机化[E1]。", unit("S1", "未进行随机化", kind="negated_fact", quote="No deaths"))
    assert kept.form_check.status == "passed" and kept.verdict == "supported"
    dropped = check("本研究未进行随机化[E1]。", unit("S1", "进行随机化", quote="No deaths"))
    assert codes(dropped) == ["negation_unreached"] and dropped.verdict == "insufficient"


def test_a_negation_stands_on_a_located_quote_or_a_checked_field_and_the_models_reading():
    text = "本次未发生死亡[A1]。"

    def negated(**extra):
        return check(text, ReportedUnit(segment="S1", fragment="未发生死亡", kind="negated_fact", verdict="supported",
                                        **extra))
    field = {"ref": "A1", "path": "output.deaths", "value": 0}
    assert negated(field_basis=field).verdict == "supported"
    assert negated(field_basis=dict(field, path="output.fatalities")).units[0].reason.startswith("field_basis_invalid")
    assert negated(field_basis=dict(field, value=2)).verdict == "insufficient"
    sentence = "本次未发生死亡[E1]。"
    for quote in ("No deaths were reported.", "The death count was 0"):  # the second holds no negation word
        assert check(sentence, unit("S1", "未发生死亡", kind="negated_fact", quote=quote)).verdict == "supported"
    missing = check(sentence, unit("S1", "未发生死亡", kind="negated_fact", quote=None))
    assert missing.units[0].reason.startswith("missing_quote") and missing.verdict == "insufficient"
    elsewhere = check(sentence, unit("S1", "未发生死亡", kind="negated_fact", quote="The death count was 2"))
    assert elsewhere.units[0].reason.startswith("quote_not_found") and elsewhere.verdict == "insufficient"
    unsure = unit("S1", "未发生死亡", kind="negated_fact", verdict="insufficient", quote="The death count was 0")
    assert check(sentence, unsure).verdict == "insufficient"  # the program does not turn the model's doubt into support


def test_a_repeated_quote_is_recorded_everywhere_and_never_taken_at_the_first():
    text = "剂量为3 mg[E1]。"
    unresolved = check(text, unit("S1", "剂量为3 mg", quote="The dose was 3 mg"))
    only = unresolved.units[0].quotes[0]
    assert (only["status"], only["adopted"], len(only["positions"])) == ("ambiguous", None, 2)
    assert unresolved.verdict == "insufficient"
    chosen = check(text, ReportedUnit(segment="S1", fragment="剂量为3 mg", kind="fact", verdict="supported",
                                      quotes=[{"ref": "E1", "quote": "The dose was 3 mg", "occurrence": 2}]))
    assert chosen.units[0].quotes[0]["adopted"] == 2 and chosen.verdict == "supported"
    start, end = chosen.units[0].quotes[0]["positions"][1]
    assert REFS["E1"]["text"][start:end] == "The dose was 3 mg"


def test_governed_by_maps_a_segment_to_unit_numbers_and_refuses_what_it_cannot_map():
    text = "如果ORR为32%，则死亡率为5%[E1]。"
    condition = unit("S1", "如果ORR为32%", kind="condition", verdict="no_claim", quote=None)
    result = check(text, condition, unit("S2", "则死亡率为5%", verdict="supported", quote=None, governed_by=["S1"]))
    assert [(u.id, u.governed_by, u.conditional) for u in result.units] == [("U1", [], True), ("U2", ["U1"], True)]
    assert result.verdict == "no_claim" and coverage_view(result) == {
        "index": 1, "stated": [], "conditional": ["如果ORR为32%", "则死亡率为5%"]}
    nowhere = check(text, condition, unit("S2", "则死亡率为5%", quote=None, governed_by=["S7"]))
    assert codes(nowhere) == ["governed_by_unmapped"] and nowhere.verdict == "insufficient"
    preassigned = check(text, condition, unit("S2", "则死亡率为5%", quote=None, governed_by=["H1"]), known_ids={"H1"})
    assert preassigned.units[1].governed_by == ["H1"] and preassigned.form_check.status == "passed"


def test_a_fact_inside_a_conditional_sentence_is_checked_as_usual():
    text = "如果ORR为32%，则获批，且死亡率为5%[E1]。"
    condition = unit("S1", "如果ORR为32%", kind="condition", verdict="no_claim", quote=None)
    consequent = unit("S2", "则获批", kind="condition", verdict="no_claim", quote=None, governed_by=["S1"])
    assert check(text, condition, consequent, unit("S3", "且死亡率为5%", quote="No deaths")).verdict == "supported"
    unsupported = check(text, condition, consequent, unit("S3", "且死亡率为5%", quote=None))
    assert unsupported.verdict == "insufficient" and coverage_view(unsupported)["stated"] == ["且死亡率为5%"]


def test_overlapping_units_must_be_declared_and_a_contradiction_outlives_a_failed_form_check():
    text = "ORR为32%，死亡率为5%[E1]。"
    twice = check(text, unit("S1", "ORR为32%"), unit("S1", "ORR"), unit("S2", "死亡率为5%", quote="No deaths"))
    assert codes(twice) == ["undeclared_overlap"] and twice.verdict == "insufficient"
    declared = check(text, unit("S1", "ORR为32%"), unit("S1", "ORR", governed_by=["S1"]),
                     unit("S2", "死亡率为5%", quote="No deaths"))
    assert declared.form_check.status == "passed"
    contradicted = check(text, unit("S1", "ORR为32%", verdict="contradicted"))  # S2 never handled
    assert contradicted.form_check.status == "failed" and contradicted.verdict == "contradicted"
