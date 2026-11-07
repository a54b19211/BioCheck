"""/v2/verify with a fake model: the program's side of the unit check (cutting, numbering, location, form check,
batches, the sentence verdict). Whether a real model splits and judges well is not tested here."""
import pytest
from conftest import FakeLLM, request
from pydantic import ValidationError

from app import claims as C
from app import unit_verify
from app.llm import shape
from app.segment_rules import RULES_VERSION, split_segments

QUOTE_ORR, QUOTE_PFS = "ORR was 32%", "median PFS was 2.8 months"
COMPOUND = "2.5 mg/kg组ORR为32%，且中位PFS为2.8个月[E1]。"


def unit(sentence, segment, fragment, kind="fact", verdict="supported", quote=None, **extra):
    quotes = [{"ref": extra.pop("ref", "E1"), "quote": quote}] if quote else []
    return dict(sentence=sentence, segment=segment, fragment=fragment, kind=kind, verdict=verdict, quotes=quotes,
                **extra)


def answers(*units, split="complete"):
    """The model's answer for any batch: one group per sentence the units name, with a split report each."""
    def answer(payload):
        groups = {}
        for u in units:
            groups.setdefault(u["sentence"], []).append({k: v for k, v in u.items() if k != "sentence"})
        return {"claims": [{"sentence": i, "units": grouped, "split": {"assessment": split}}
                           for i, grouped in groups.items()]}
    return answer


def run(draft, llm, **extra):
    return unit_verify.verify(dict(request(**extra), draft=draft), llm)


def compound(fragment="且中位PFS为2.8个月", verdict="supported", quote=QUOTE_PFS, split="complete"):
    return answers(unit(1, "S1", "2.5 mg/kg组ORR为32%", quote=QUOTE_ORR),
                   unit(1, "S2", fragment, verdict=verdict, quote=quote), split=split)


def test_a_compound_sentence_is_cut_numbered_and_judged_unit_by_unit():
    llm = FakeLLM(verify_claims=compound())
    result = run(COMPOUND, llm)
    sent = next(p for stage, p, _ in llm.payloads if stage == "verify_claims")["claims"][0]
    assert [s["id"] for s in sent["segments"]] == ["S1", "S2"]  # the program numbers the segments, the model only cites
    claim = result["claims"][0]
    assert (result["status"], claim["verdict"]) == ("PASS", "supported")
    assert [(u["id"], u["segment"], u["verdict"]) for u in claim["units"]] == [("U1", "S1", "supported"),
                                                                              ("U2", "S2", "supported")]
    assert claim["form_check"]["status"] == "passed" and [s["status"] for s in claim["segments"]] == ["handled"] * 2
    assert claim["semantic_split"] == {"source": "model_self_assessment", "assessment": "complete", "reason": ""}
    assert COMPOUND[claim["units"][1]["start"]:claim["units"][1]["end"]] == "且中位PFS为2.8个月"
    assert claim["units"][0]["quotes"][0]["positions"] and claim["units"][0]["quotes"][0]["adopted"] == 1
    assert (result["checker_version"], result["rules_version"]) == (unit_verify.CHECKER_VERSION, RULES_VERSION)
    # one unit contradicted: the sentence is, and the other unit keeps its own verdict
    contradicting = compound("且中位PFS为3.5个月", "contradicted")
    wrong = run(COMPOUND.replace("2.8个月", "3.5个月"), FakeLLM(verify_claims=contradicting))
    assert (wrong["status"], [u["verdict"] for u in wrong["claims"][0]["units"]]) == (
        "FAIL", ["supported", "contradicted"])
    # one quote does not carry both: the second unit without its own is pending, the first still counts
    bare = run(COMPOUND, FakeLLM(verify_claims=compound(quote=None)))["claims"][0]
    assert bare["verdict"] == "insufficient" and [u["verdict"] for u in bare["units"]] == ["supported", "insufficient"]
    # a split report that does not say complete holds the support back, and is recorded apart from the form check
    unsure = run(COMPOUND, FakeLLM(verify_claims=compound(split="uncertain")))["claims"][0]
    assert unsure["verdict"] == "insufficient" and unsure["reason"].startswith("semantic_split_uncertain")
    assert unsure["form_check"]["status"] == "passed" and unsure["semantic_split"]["assessment"] == "uncertain"


def test_a_conditional_sentence_states_nothing_and_the_coverage_check_is_told_so():
    draft = "如果ORR为32%，则获批[E1]。"
    condition = unit(1, "S1", "如果ORR为32%", kind="condition", verdict="no_claim")
    consequent = unit(1, "S2", "则获批", verdict="supported", governed_by=["S1"])  # taken as a fact, but governed
    llm = FakeLLM(verify_claims=answers(condition, consequent))
    result = run(draft, llm)
    claim = result["claims"][0]
    assert (result["status"], claim["verdict"], claim["form_check"]["status"]) == ("PASS", "no_claim", "passed")
    assert [(u["verdict"], u["conditional"], u["governed_by"]) for u in claim["units"]] == [
        ("no_claim", True, []), ("no_claim", True, ["U1"])]
    coverage = next(p for stage, p, _ in llm.payloads if stage == "verify_coverage")["sentences"][0]
    assert (coverage["stated"], coverage["conditional"]) == ([], ["如果ORR为32%", "则获批"])


def test_the_condition_travels_with_its_consequence_and_the_models_doubt_is_not_covered_up():
    draft = "如果ORR为32%，或死亡数为0，则获批；如果PFS为2.8个月，则暂停[E1]。"
    conditions = [unit(1, segment, fragment, kind="condition", verdict="no_claim")
                  for segment, fragment in (("S1", "如果ORR为32%"), ("S2", "或死亡数为0"), ("S4", "如果PFS为2.8个月"))]

    def consequences(first="no_claim", second="no_claim", **second_extra):
        return [unit(1, "S3", "则获批", kind="inference", verdict=first, governed_by=["S1", "S2"]),
                unit(1, "S5", "则暂停", kind="inference", verdict=second, governed_by=["S4"], **second_extra)]

    def judged(*units):
        return run(draft, FakeLLM(verify_claims=answers(*conditions, *units)))["claims"][0]["verdict"]
    llm = FakeLLM(verify_claims=answers(*conditions, *consequences()))
    claim = run(draft, llm)["claims"][0]
    sent = next(p for stage, p, _ in llm.payloads if stage == "verify_claims")["claims"][0]
    # a consequence is sent with the segments of the condition it answers (one condition may take several)
    assert {s["id"]: s["consequent_of"] for s in sent["segments"] if "consequent_of" in s} == {
        "S3": ["S1", "S2"], "S5": ["S4"]}
    assert (claim["verdict"], [u["conditional"] for u in claim["units"]]) == ("no_claim", [True] * 5)
    # the program reads the model's verdict on a consequence and does not replace it: doubt and contradiction stand
    assert judged(*consequences("insufficient")) == "insufficient"
    assert judged(*consequences("no_claim", "contradicted", quote=QUOTE_ORR)) == "contradicted"


def test_an_incomplete_split_does_not_pass_a_sentence_as_no_claim_either():
    draft = "如果ORR为32%，则获批[E1]。"
    condition = unit(1, "S1", "如果ORR为32%", kind="condition", verdict="no_claim")
    consequent = unit(1, "S2", "则获批", verdict="supported", governed_by=["S1"])
    result = run(draft, FakeLLM(verify_claims=answers(condition, consequent, split="incomplete")))
    claim = result["claims"][0]
    assert (result["status"], claim["verdict"]) == ("REVIEW", "insufficient")
    assert claim["reason"].startswith("semantic_split_incomplete")
    assert [u["verdict"] for u in claim["units"]] == ["no_claim", "no_claim"]  # the units themselves are untouched


INFERRED = "2.5 mg/kg组ORR为32%，说明该剂量有效[E1]。"


def test_an_inference_is_supported_in_a_judgment_gaps_scenarios_or_recommendation_section_not_in_evidence():
    def inferring(sentence):
        return [unit(sentence, "S1", "2.5 mg/kg组ORR为32%", quote=QUOTE_ORR),
                unit(sentence, "S2", "说明该剂量有效", kind="inference", quote=QUOTE_ORR)]

    draft, width = INFERRED + "\n\n" + INFERRED, len(INFERRED)  # two paragraphs: sentence 1, then sentence 2

    def checked(*kinds):
        sections = [{"kind": k, "start": n * (width + 2), "end": n * (width + 2) + width} for n, k in enumerate(kinds)]
        return run(draft, FakeLLM(verify_claims=answers(*inferring(1), *inferring(2))), sections=sections)["claims"]

    assert [c["verdict"] for c in checked("judgment", "evidence")] == ["supported", "insufficient"]
    assert [c["verdict"] for c in checked("gaps", "evidence")] == ["supported", "insufficient"]
    assert [c["verdict"] for c in checked("scenarios", "recommendation")] == ["supported", "supported"]
    # gaps is no free pass: an inference the model did not find supported stays insufficient there
    doubtful = [unit(1, "S1", "2.5 mg/kg组ORR为32%", quote=QUOTE_ORR),
                unit(1, "S2", "说明该剂量有效", kind="inference", verdict="insufficient", quote=QUOTE_ORR)]
    gaps = [{"kind": "gaps", "start": 0, "end": width}]
    in_gaps = run(INFERRED, FakeLLM(verify_claims=answers(*doubtful)), sections=gaps)
    assert in_gaps["claims"][0]["verdict"] == "insufficient"
    fact = checked("judgment", "evidence")[1]
    assert fact["units"][1]["reason"].startswith("inference_in_fact_section")
    assert fact["units"][0]["verdict"] == "supported"  # only the inference is held back
    # no sections, or a sentence that lies in none of the given ones: nothing is known of where it stands
    assert [c["verdict"] for c in checked()] == ["supported", "supported"]
    outside = [{"kind": "evidence", "start": 0, "end": 5}]
    unplaced = run(draft, FakeLLM(verify_claims=answers(*inferring(1), *inferring(2))), sections=outside)
    assert [c["verdict"] for c in unplaced["claims"]] == ["supported", "supported"]


def test_each_sentence_names_the_call_that_carried_it():
    # two paragraphs are two batches: the sentence of the second is in the second verify_claims call
    both = answers(unit(1, "S1", "2.5 mg/kg组ORR为32%", quote=QUOTE_ORR), unit(2, "S1", "2.5 mg/kg组ORR为32%",
                                                                              quote=QUOTE_ORR))
    result = run("2.5 mg/kg组ORR为32%[E1]。\n\n2.5 mg/kg组ORR为32%[E1]。", FakeLLM(verify_claims=both))
    assert [c["call"] for c in result["claims"]] == [0, 1]
    assert [result["llm_calls"][c["call"]]["stage"] for c in result["claims"]] == ["verify_claims"] * 2
    # a sentence unknown to the references is never sent: no call
    unknown = run("2.5 mg/kg组ORR为32%[E9]。", FakeLLM(verify_claims=both))["claims"][0]
    assert unknown["call"] is None


def test_each_unit_goes_to_the_sentence_its_group_names_and_a_wrong_or_missing_number_is_not_repaired():
    draft = "2.5 mg/kg组ORR为32%[E1]。3.4 mg/kg组ORR为35%[E1]。"  # one paragraph, so one batch
    grouped = answers(unit(1, "S1", "2.5 mg/kg组ORR为32%", quote=QUOTE_ORR),
                      unit(2, "S1", "3.4 mg/kg组ORR为35%", quote="ORR was 35%"))({})
    first, second = grouped["claims"]
    # in any order, and with a group for a sentence nobody asked about: the number says whose units they are
    reordered = {"claims": [second, dict(first, sentence=9), first]}
    one, two = run(draft, FakeLLM(verify_claims=lambda p: reordered))["claims"]
    assert (one["verdict"], two["verdict"]) == ("supported", "supported")
    assert [u["fragment"] for u in two["units"]] == ["3.4 mg/kg组ORR为35%"]
    # a wrong number leaves its sentence without units, which the form check refuses; nothing is filled in by position
    wrong = {"claims": [first, dict(second, sentence=3)]}
    one, two = run(draft, FakeLLM(verify_claims=lambda p: wrong))["claims"]
    assert one["verdict"] == "supported" and two["verdict"] == "insufficient" and two["units"] == []
    assert [p["code"] for p in two["form_check"]["problems"]][:1] == ["empty_result"]
    assert two["semantic_split"]["assessment"] == "not_reported"
    # a group without a number is not an answer at all (the batch fails, as any invalid output does)
    with pytest.raises(ValidationError):
        unit_verify.UnitBatch.model_validate({"claims": [{"units": first["units"]}]})


def plan(draft):
    pending = C.sentences(draft)
    sizes = {c["index"]: len(split_segments(c["text"])) for c in pending}
    batches, too_big = unit_verify.plan_batches(pending, sizes)
    return [[c["index"] for c in b] for b in batches], [c["index"] for c in too_big]


def test_batches_are_four_sentences_24_segments_one_paragraph_and_a_long_sentence_goes_alone():
    short = "ORR为32%[E1]。"
    long_nine = "，".join(f"第{i}组ORR为32%" for i in range(1, 10)) + "[E1]。"
    assert plan(short * 5) == ([[1, 2, 3, 4], [5]], [])
    assert plan(short + "\n\n" + short) == ([[1], [2]], [])  # never across paragraphs
    assert plan(short + long_nine + short) == ([[1], [2], [3]], [])
    wide = "，".join(f"第{i}组ORR为32%" for i in range(1, 8)) + "[E1]。"  # 7 segments each: 24 holds three
    assert plan(wide * 4) == ([[1, 2, 3], [4]], [])


def test_a_sentence_with_more_segments_than_a_batch_holds_is_pending_and_never_sent():
    huge = "，".join(f"第{i}组ORR为32%" for i in range(1, 26)) + "[E1]。"
    llm = FakeLLM(verify_claims=answers(unit(2, "S1", "2.5 mg/kg组ORR为32%", quote=QUOTE_ORR)))
    result = run(huge + "2.5 mg/kg组ORR为32%[E1]。", llm)
    first, second = result["claims"]
    assert first["verdict"] == "insufficient" and first["reason"].startswith("batch_limit")
    assert (first["units"], first["form_check"]) == ([], None)
    assert {s["status"] for s in first["segments"]} == {"unchecked"}
    assert second["verdict"] == "supported" and result["status"] == "REVIEW"
    sent = [p for stage, p, _ in llm.payloads if stage == "verify_claims"]
    assert [[c["index"] for c in p["claims"]] for p in sent] == [[2]]


class Truncating(FakeLLM):
    """A model whose output is cut off whenever a batch holds more than one sentence, or sentence 2."""

    def __call__(self, stage, system, payload, model_cls, **options):
        indexes = [c["index"] for c in payload.get("claims", [])]
        if stage == "verify_claims" and (len(indexes) > 1 or indexes == [2]):
            self.payloads.append((stage, payload, options))
            return None, {"stage": stage, "model": "fake", "finish_reason": "length", "usage": {}, "seconds": 0.0,
                          "error": "invalid_output"}
        return super().__call__(stage, system, payload, model_cls, **options)


def test_a_cut_off_answer_splits_the_batch_down_to_one_sentence_and_the_calls_are_bounded():
    draft = "2.5 mg/kg组ORR为32%[E1]。3.4 mg/kg组ORR为35%[E1]。"
    one = unit(1, "S1", "2.5 mg/kg组ORR为32%", quote=QUOTE_ORR)
    llm = Truncating(verify_claims=answers(one))
    first, second = run(draft, llm)["claims"]
    sent = [[c["index"] for c in p["claims"]] for stage, p, _ in llm.payloads if stage == "verify_claims"]
    assert sent == [[1, 2], [1], [2]]
    assert first["verdict"] == "supported"
    assert second["verdict"] == "insufficient" and second["reason"].startswith("output_truncated")
    assert (first["call"], second["call"]) == (1, 2)  # the last call that carried each sentence, not the cut-off pair
    # the request's own call limit bounds the splitting: no call left means unchecked, not another try
    capped = Truncating(verify_claims=answers(one))
    first, second = run(draft, capped, max_llm_calls=2)["claims"]
    assert first["verdict"] == "supported" and second["reason"].startswith("unchecked")
    assert (first["call"], second["call"]) == (1, None)
    assert len([1 for stage, _, _ in capped.payloads if stage == "verify_claims"]) == 2


def test_a_negation_stands_on_a_structured_field_or_a_located_quote_without_a_negation_word():
    draft = "本次未发生死亡[A1]。"
    extra = dict(analysis_results=[{"id": "A1", "method": "count", "output": {"deaths": 0}}],
                 evidence=request()["evidence"] + [{"id": "E3", "title": "", "source": "x", "published_at": None,
                                                    "source_kind": "research", "verified": True,
                                                    "text": "The death count was 0 in this cohort."}])
    field = {"ref": "A1", "path": "output.deaths", "value": 0}
    on_field = unit(1, "S1", "未发生死亡", kind="negated_fact", field_basis=field)
    result = run(draft, FakeLLM(verify_claims=answers(on_field)), **extra)
    assert result["status"] == "PASS" and result["claims"][0]["units"][0]["field_basis"]["holds"] is True
    wrong_path = unit(1, "S1", "未发生死亡", kind="negated_fact", field_basis=dict(field, path="output.fatalities"))
    assert run(draft, FakeLLM(verify_claims=answers(wrong_path)), **extra)["claims"][0]["verdict"] == "insufficient"
    on_text = unit(1, "S1", "未发生死亡", kind="negated_fact", quote="The death count was 0", ref="E3")
    result = run("本次未发生死亡[E3]。", FakeLLM(verify_claims=answers(on_text)), **extra)
    assert result["status"] == "PASS" and result["claims"][0]["units"][0]["quotes"][0]["status"] == "found"


def test_the_time_facts_reach_the_model_and_a_later_date_is_only_a_clue():
    source = {"id": "E1", "title": "WHO", "source": "who", "published_at": "2020-05-07", "source_kind": "official",
              "verified": True, "text": "Published 7 May 2020. As of 19 May 2020, 3462 cases were reported.",
              "first_seen_at": "2026-11-06T03:10:00Z", "proof_available_at": "2026-11-06T03:12:00Z",
              "replay_mode": "current"}
    draft = "根据2020年5月7日发布的WHO通报，3462例已报告[E1]。"
    llm = FakeLLM(verify_claims=answers(
        unit(1, "S1", "根据2020年5月7日发布的WHO通报", kind="time", verdict="insufficient"),
        unit(1, "S2", "3462例已报告", quote="3462 cases were reported")))
    claim = run(draft, llm, evidence=[source])["claims"][0]
    sent = next(p for stage, p, _ in llm.payloads if stage == "verify_claims")
    reference = sent["references"][0]
    assert [reference[k] for k in ("first_seen_at", "proof_available_at", "replay_mode")] == [
        "2026-11-06T03:10:00Z", "2026-11-06T03:12:00Z", "current"]
    (clue,) = sent["claims"][0]["time_clues"]
    assert (clue["stated"], clue["later_dates"], clue["replay_mode"]) == ("2020-05-07", ["2020-05-19"], "current")
    assert [u["verdict"] for u in claim["units"]] == ["insufficient", "supported"]
    assert claim["verdict"] == "insufficient"


class Reading(FakeLLM):
    """Keeps the system prompt of each call as well."""

    def __init__(self, **answers):
        super().__init__(**answers)
        self.systems = []

    def __call__(self, stage, system, payload, model_cls, **options):
        self.systems.append((stage, system))
        return super().__call__(stage, system, payload, model_cls, **options)


def test_the_unit_prompt_is_named_by_the_checker_version_and_its_wording_reaches_the_model():
    llm = Reading(verify_claims=compound())
    result = run(COMPOUND, llm)
    system = next(text for stage, text in llm.systems if stage == "verify_claims")
    assert result["checker_version"] == "biocheck.unit_verify.v3"
    for clause in ("“原文未提及/未出现X”", "条件后果", "consequent_of", "一组只写这一次"):
        assert clause in system
    # the offline stand-in model tells this form from the claim check's by how the format line opens
    assert shape(unit_verify.UnitBatch).startswith('{"claims":[{"sentence":int,"units":[{"segment":')
