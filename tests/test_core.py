import copy
import json
from pathlib import Path
import unittest
from app.core import generate, verify, revise, run, split_units, numeric_tokens, locate_span
from app.evidence import segments, model_input

PACKET = {"question": "概括研究A", "evidence": [
    {"id": "E1", "version": "v1", "text": "研究A有50人，其中20人缓解，缓解率40%。"},
    {"id": "E2", "version": "v2", "text": "研究B有100人，其中60人缓解，缓解率60%。"}]}


def response(draft="研究A缓解率40%[E1]。", verdict="supported"):
    return {"reviews": [{"unit_id": "U1", "verdict": verdict, "reason": "核对原文",
        "claims": [{"span": draft, "verdict": verdict, "reason": "原文",
                    "evidence": [{"evidence_id": "E1", "segment_id": "S1"}]}],
        "issues": [], "numeric_resolutions": []}],
        "coverage": {"verdict": "complete", "reason": "回答了所问"}}


class QualityTests(unittest.TestCase):
    def test_paragraphs_preserve_trailing_newline_and_offsets(self):
        text = " 第一段[E1]。\n\n第二段有40%。\n"
        units = split_units(text)
        self.assertEqual(len(units), 2)
        for u in units:
            self.assertEqual(text[u["start"]:u["end"]], u["text"])

    def test_chinese_numbers_are_not_skipped(self):
        self.assertEqual(numeric_tokens("缓解率40%，人数30，p<.01，NCT03525678[E1]"), ["40%", "30", ".01"])

    def test_no_unknown_reference_pass(self):
        draft = "研究A缓解率40%[E3]。"
        result = verify(PACKET, draft, lambda *args: response(draft))
        self.assertEqual(result["status"], "FAIL")

    def test_wrong_source_citation_is_not_saved_by_other_source(self):
        draft = "研究A缓解率40%[E2]。"
        result = verify(PACKET, draft, lambda *args: response(draft))
        self.assertEqual(result["status"], "FAIL")

    def test_one_correct_number_does_not_pass_extra_wrong_number(self):
        draft = "研究A缓解率40%，共30人缓解[E1]。"
        result = verify(PACKET, draft, lambda *args: response(draft))
        self.assertEqual(result["status"], "REVIEW")
        self.assertIn("30", result["units"][0]["numeric_hints"])

    def test_quote_must_be_in_exact_source(self):
        bad = response()
        bad["reviews"][0]["claims"][0]["evidence"][0]["segment_id"] = "S999"
        result = verify(PACKET, "研究A缓解率40%[E1]。", lambda *args: bad)
        self.assertEqual(result["status"], "REVIEW")
        self.assertTrue(result["verification_errors"])

    def test_missing_paragraph_is_a_runtime_failure(self):
        with self.assertRaises(ValueError):
            verify(PACKET, "第一段[E1]。\n\n第二段[E2]。", lambda *args: response("第一段[E1]。"))

    def test_empty_check_cannot_pass(self):
        with self.assertRaises(ValueError):
            verify(PACKET, "", lambda *args: response())

    def test_unsupported_subclaim_overrides_paragraph_pass(self):
        bad = response()
        bad["reviews"][0]["claims"][0]["verdict"] = "insufficient"
        result = verify(PACKET, "研究A缓解率40%[E1]。", lambda *args: bad)
        self.assertEqual(result["status"], "REVIEW")

    def test_evaluation_labels_do_not_reach_model(self):
        packet = dict(PACKET, expected="error", expected_labels=["NUM"])
        seen = []
        def call(stage, prompt, data):
            seen.append(data)
            return response()
        verify(packet, "研究A缓解率40%[E1]。", call)
        self.assertNotIn("expected", seen[0])
        self.assertNotIn("expected_labels", seen[0])

    def test_plan_invented_quote_stops_generation(self):
        def call(*args):
            return {"points": [{"evidence_ids": ["E1"], "binding": {},
                                "support": [{"evidence_id": "E1", "segment_id": "S999"}]}]}
        with self.assertRaises(ValueError):
            generate(PACKET, call)

    def test_segments_keep_all_original_characters(self):
        text = "First 2.8 months. \n下一句。 " + "x" * 2500
        parts = segments(text)
        self.assertEqual("".join(p["text"] for p in parts), text)
        for part in parts:
            self.assertEqual(text[part["start"]:part["end"]], part["text"])

    def test_model_sees_text_once_and_not_duplicate_abstract(self):
        packet = copy.deepcopy(PACKET)
        packet["evidence"][0]["facts"] = {"abstract": "duplicate"}
        request = model_input(packet)
        self.assertNotIn("text", request["evidence"][0])
        self.assertNotIn("facts", request["evidence"][0])
        self.assertEqual(request["evidence"][0]["segments"][0]["text"], packet["evidence"][0]["text"])

    def test_swapped_citations_within_one_paragraph_are_detected(self):
        draft = "研究A缓解率40%[E2]，研究B缓解率60%[E1]。"
        ans = response(draft)
        ans["reviews"][0]["claims"] = [
            {"span": "研究A缓解率40%", "verdict": "supported",
             "evidence": [{"evidence_id": "E1", "segment_id": "S1"}]},
            {"span": "研究B缓解率60%", "verdict": "supported",
             "evidence": [{"evidence_id": "E2", "segment_id": "S1"}]}]
        result = verify(PACKET, draft, lambda *args: ans)
        self.assertEqual(result["status"], "FAIL")

    def test_correct_local_citations_are_retained(self):
        draft = "研究A缓解率40%[E1]，研究B缓解率60%[E2]。"
        ans = response(draft)
        ans["reviews"][0]["claims"] = [
            {"span": "研究A缓解率40%", "verdict": "supported",
             "evidence": [{"evidence_id": "E1", "segment_id": "S1"}]},
            {"span": "研究B缓解率60%", "verdict": "supported",
             "evidence": [{"evidence_id": "E2", "segment_id": "S1"}]}]
        self.assertEqual(verify(PACKET, draft, lambda *args: ans)["status"], "PASS")

    def test_affirmative_fact_cannot_use_whole_source_as_support(self):
        ans = response()
        ans["reviews"][0]["claims"][0]["evidence"] = [{"evidence_id": "E1", "scope": "whole_source"}]
        result = verify(PACKET, "研究A缓解率40%[E1]。", lambda *args: ans)
        self.assertEqual(result["status"], "REVIEW")
        self.assertTrue(result["verification_errors"])

    def test_limited_absence_has_explicit_source_and_missing_item(self):
        text = "本次材料未提供P值[E1]。"
        ans = response(text)
        claim = ans["reviews"][0]["claims"][0]
        claim.update(kind="source_absence", missing_information="E1中未给出P值",
                     evidence=[{"evidence_id": "E1", "scope": "whole_source"}])
        self.assertEqual(verify(PACKET, text, lambda *args: ans)["status"], "PASS")

    def test_invalid_check_does_not_erase_an_established_error(self):
        draft = "研究A缓解率60%，研究B缓解率60%[E1][E2]。"
        ans = response(draft)
        ans["reviews"][0]["claims"] = [
            {"span": "研究A缓解率60%", "verdict": "contradicted",
             "evidence": [{"evidence_id": "E1", "segment_id": "S1"}]},
            {"span": "研究B缓解率60%", "verdict": "supported",
             "evidence": [{"evidence_id": "E2", "segment_id": "S999"}]}]
        result = verify(PACKET, draft, lambda *args: ans)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["verification_errors"])

    def test_valid_segment_citation_is_accepted(self):
        draft = "研究A缓解率40%[E1:S1]。"
        self.assertEqual(verify(PACKET, draft, lambda *args: response(draft))["status"], "PASS")

    def test_segment_citation_must_match_used_segment(self):
        packet = copy.deepcopy(PACKET)
        packet["evidence"][0]["text"] = "研究A有50人。 \n缓解率40%。"
        draft = "研究A缓解率40%[E1:S1]。"
        ans = response(draft)
        ans["reviews"][0]["claims"][0]["evidence"][0]["segment_id"] = "S3"
        self.assertEqual(verify(packet, draft, lambda *args: ans)["status"], "FAIL")

    def test_nonexistent_segment_citation_cannot_pass(self):
        draft = "研究A缓解率40%[E1:S999]。"
        self.assertEqual(verify(PACKET, draft, lambda *args: response(draft))["status"], "FAIL")

    def test_writing_uses_original_text_without_internal_segment_ids(self):
        requests = []
        def call(stage, prompt, data):
            requests.append((stage, data))
            if stage.startswith("plan"):
                return {"points": [{"intent": "概括研究", "evidence_ids": ["E1"],
                                    "binding": {"study": "A"},
                                    "support": [{"evidence_id": "E1", "segment_id": "S1"}]}]}
            return {"draft": "研究A缓解率40%[E1]。"}
        result = generate(PACKET, call, "bound")
        self.assertIn("support", result["plan"]["points"][0])
        write = requests[-1][1]
        self.assertEqual(write["evidence"][0]["text"], PACKET["evidence"][0]["text"])
        self.assertNotIn("segments", write["evidence"][0])
        self.assertNotIn("support", write["plan"]["points"][0])

    def test_rephrased_claim_becomes_incomplete_check_not_author_error(self):
        ans = response(verdict="contradicted")
        ans["reviews"][0]["claims"][0]["span"] = "研究A的缓解率是40%"
        result = verify(PACKET, "研究A缓解率40%[E1]。", lambda *args: ans)
        self.assertEqual(result["status"], "REVIEW")
        self.assertTrue(result["verification_errors"])
        self.assertEqual(result["units"][0]["claims"], [])

    def test_rephrased_issue_is_not_used_to_rewrite_author(self):
        ans = response()
        ans["reviews"][0]["issues"] = [
            {"span": "不在原稿里的表述", "labels": ["STR"], "reason": "错误指控", "evidence": []}]
        result = verify(PACKET, "研究A缓解率40%[E1]。", lambda *args: ans)
        self.assertEqual(result["status"], "REVIEW")
        self.assertTrue(result["verification_errors"])
        self.assertEqual(result["units"][0]["issues"], [])

    def test_revision_preserves_other_paragraph_verbatim(self):
        draft = "原句[E1]。\n\n正确句 [E2]。 \n"
        units = split_units(draft)
        review = {"draft": draft, "units": [dict(u, unit_id=u["id"]) for u in units],
                  "problem_units": ["U1"]}
        fixed = revise(PACKET, review, lambda *args: {
            "replacements": [{"unit_id": "U1", "text": "修订句[E1]。"}]})
        self.assertEqual(fixed["draft"], "修订句[E1]。\n\n正确句 [E2]。 \n")

    def test_revision_rejects_unrequested_paragraph(self):
        draft = "原句[E1]。"
        u = split_units(draft)[0]
        review = {"draft": draft, "units": [dict(u, unit_id="U1")], "problem_units": ["U1"]}
        with self.assertRaises(ValueError):
            revise(PACKET, review, lambda *args: {"replacements": [{"unit_id": "U2", "text": "别的"}]})

    def test_citation_location_examples_match_the_consumer_receipts(self):
        fixture = json.loads((Path(__file__).parents[1] / "evaluation/citation_location_cases.json").read_text())
        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                result = verify(fixture["packet"], case["draft"],
                                lambda *args: copy.deepcopy(case["model_reply"]))
                self.assertEqual(result["status"], case["expected"]["status"])
                self.assertEqual(bool(result["verification_errors"]), case["expected"]["has_verification_errors"])
                for unit in result["units"]:
                    for claim in unit["claims"]:
                        self.assertEqual(unit["text"][claim["span_start"]:claim["span_end"]], claim["span"])
                if "verification_result" in case:
                    self.assertEqual(result, case["verification_result"])

    def test_ambiguous_or_repeated_position_never_triggers_automatic_revision(self):
        fixture = json.loads((Path(__file__).parents[1] / "evaluation/citation_location_cases.json").read_text())
        for case in fixture["cases"]:
            if not case["expected"]["has_verification_errors"]:
                continue
            with self.subTest(case=case["name"]):
                stages = []
                def call(stage, instruction, payload):
                    stages.append(stage)
                    if stage == "write_direct":
                        return {"draft": case["draft"]}
                    if stage == "verify":
                        return copy.deepcopy(case["model_reply"])
                    self.fail("无效核验回执不得触发自动修订")
                result = run(fixture["packet"], call, method="direct")
                self.assertEqual(stages, ["write_direct", "verify"])
                self.assertIsNone(result["revision"])
                self.assertEqual(result["final_text"], case["draft"])

    def test_invalid_explicit_location_never_falls_back_to_text_search(self):
        for offsets in (
            {"span_start": 0}, {"span_end": 3}, {"span_start": -1, "span_end": 3},
            {"span_start": 0, "span_end": 99}, {"span_start": True, "span_end": 3},
            {"span_start": 0.0, "span_end": 3}, {"span_start": 1, "span_end": 3},
        ):
            with self.subTest(offsets=offsets):
                with self.assertRaises(ValueError):
                    locate_span("abc", dict(span="abc", **offsets))

    def test_positions_are_relative_unicode_offsets_and_do_not_depend_on_claim_order(self):
        text = "🙂 Treatment reduced pain [E1]. Treatment reduced pain [E2]."
        span = "Treatment reduced pain"
        second = text.rfind(span)
        packet = {"question": "What happened?", "evidence": [
            {"id": "E1", "version": "v1", "text": "Treatment reduced pain."},
            {"id": "E2", "version": "v1", "text": "No effect on pain was found."}]}
        ans = response(text)
        ans["reviews"][0]["claims"] = [
            {"span": span, "span_start": offset, "span_end": offset + len(span),
             "verdict": "supported", "evidence": [{"evidence_id": "E1", "segment_id": "S1"}]}
            for offset in (second, text.index(span))]
        result = verify(packet, text, lambda *args: ans)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["verification_errors"])
        self.assertEqual(result["units"][0]["claims"][0]["span_start"], second)

    def test_claim_and_issue_can_share_a_valid_position(self):
        text = "研究A缓解率40%[E2]。"
        ans = response(text)
        ans["reviews"][0]["issues"] = [
            {"span": text, "labels": ["REF"], "reason": "引用不符",
             "evidence": [{"evidence_id": "E1", "segment_id": "S1"}]}]
        result = verify(PACKET, text, lambda *args: ans)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["verification_errors"])

    def test_ambiguous_issue_is_an_incomplete_check(self):
        text = "研究A缓解率40%[E1]。研究A缓解率40%[E1]。"
        ans = response(text)
        ans["reviews"][0]["issues"] = [
            {"span": "研究A缓解率40%", "labels": ["STR"], "reason": "位置不明", "evidence": []}]
        result = verify(PACKET, text, lambda *args: ans)
        self.assertEqual(result["status"], "REVIEW")
        self.assertTrue(result["verification_errors"])
        self.assertFalse(result["units"][0]["issues"])


if __name__ == "__main__":
    unittest.main()
