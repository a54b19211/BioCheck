"""Small authored contracts for structured citations and material-scope repairs."""
import copy
import unittest
from app import core, claims
from tests.test_claim_verify import Calls, packet

BASIS = {"analysis_unit": "item", "unit": "items", "period": "fixed",
         "population": "adopted", "denominator": 4}


def analysis_packet(output=None):
    output = output if output is not None else {"left": {"count": 2}, "right": {"count": 2}}
    return {"question": "Report the fixed result.", "evidence": [], "assessment": {"analysis_results": [
        {"id": "A1", "dataset_ref": "fixed-v1", "method_version": "count.v1", "parameters": {},
         "input_refs": [{"source_record_id": "record", "version": "v1"}], "limitations": ["Bounded sample."],
         "calculation_basis": copy.deepcopy(BASIS), "output": output}]}}


def analysis_calls(path="/left", quote='"count":2', change=None):
    def answer(out, payload):
        row = out["checks"][0]
        row["verdict"].update(kind="analysis_result", binding=copy.deepcopy(BASIS))
        row["quotes"] = [{"reference": "A1", "output_path": path, "quote": quote}]
        if change:
            change(row)
    return Calls(answer)


def codes(result):
    return [code for e in result["verification_errors"] for code in e.get("error_codes", [])]


class ClaimContractTests(unittest.TestCase):
    def test_illegal_kind_and_source_use_are_claim_errors_without_aliases(self):
        for field, value, code in (("kind", "analysis", "claim_kind_invalid"),
                                   ("source_use", "analysis_result", "claim_source_use_invalid")):
            def bad(out, payload): out["checks"][0]["verdict"][field] = value
            result = core.verify(packet(), "Alpha is present[E1:S1].", Calls(bad), mode="paragraph")
            self.assertIn(code, codes(result))
            self.assertTrue(result["verification_errors"][0].get("claim_id"))
            self.assertEqual(result["claim_checks"][0]["answer"]["verdict"][field], value)

    def test_explicit_path_locates_repeated_value_without_global_first_match(self):
        result = core.verify(analysis_packet(), "Left count is 2[A1].", analysis_calls(), mode="paragraph")
        self.assertEqual(result["status"], "PASS")
        claim = result["units"][0]["claims"][0]
        self.assertEqual(claim["analysis_refs"][0]["output_path"], "/left")
        quote = claim["short_quotes"][0]
        self.assertEqual(quote["output_path"], "/left")
        self.assertEqual(quote["offset_basis"], "selected_value_json")
        self.assertNotIn("start", quote)
        self.assertEqual('{"count":2}'[quote["value_start"]:quote["value_end"]], '"count":2')

    def test_missing_unknown_and_ambiguous_root_paths_remain_review(self):
        for path, expected in ((None, "quote_analysis_path_required"),
                               ("/missing", "quote_analysis_path_invalid"), ("", "quote_ambiguous")):
            def change(row):
                if path is None: row["quotes"][0].pop("output_path")
            result = core.verify(analysis_packet(), "Left count is 2[A1].",
                                 analysis_calls(path, change=change), mode="paragraph")
            self.assertIn(expected, codes(result))
            self.assertEqual(core.revise(analysis_packet(), result, lambda *a: self.fail("No model call."))["changed_claims"], [])

    def test_paths_never_apply_to_source_quotes_or_hide_wrong_version(self):
        def wrong_path(out, payload): out["checks"][0]["quotes"][0]["output_path"] = ""
        review = core.verify(packet(), "Alpha is present[E1:S1].", Calls(wrong_path), mode="paragraph")
        self.assertIn("quote_path_not_allowed", codes(review))
        def wrong_version(row): row["quotes"][0]["version"] = "wrong"
        review = core.verify(analysis_packet(), "Left count is 2[A1].",
                             analysis_calls(change=wrong_version), mode="paragraph")
        self.assertIn("quote_version_mismatch", codes(review))

    def test_json_pointer_prefix_and_escapes_keep_existing_meaning(self):
        for output, path, expected, quote in (
            ({"left": {"count": 2}}, "/output/left", "/left", '"count":2'),
            ({"output": {"count": 2}, "count": 9}, "/output/count", "/output/count", '2'),
            ({"a/b": {"x~y": 2}}, "/a~1b/x~0y", "/a~1b/x~0y", '2')):
            r = core.verify(analysis_packet(output), "Count is 2[A1].", analysis_calls(path, quote), mode="paragraph")
            self.assertEqual(r["status"], "PASS")
            self.assertEqual(r["units"][0]["claims"][0]["analysis_refs"][0]["output_path"], expected)

    def test_binding_layout_must_match_contract_and_values(self):
        for bad in ({"references": ["A1"], "calculation_basis": BASIS},
                    dict(BASIS, denominator=True)):
            result = core.verify(analysis_packet(), "Count is 2[A1].",
                analysis_calls(change=lambda row: row["verdict"].update(binding=bad)), mode="paragraph")
            self.assertIn("claim_analysis_binding_invalid", codes(result))
        good = core.verify(analysis_packet(), "Count is 2[A1].",
            analysis_calls(change=lambda row: row["verdict"].update(binding={"A1": BASIS})), mode="paragraph")
        self.assertEqual(good["status"], "PASS")

    def test_numeric_conversion_cannot_borrow_sibling_or_prefix_neighbour(self):
        for output, selected, resolution, expected in (
            ({"left": {"count": 2}, "right": {"count": 2}}, "/left", "/left/count", "PASS"),
            ({"left": {"count": 2}, "right": {"count": 2}}, "/left", "/right/count", "REVIEW"),
            ({"a": {"count": 2}, "ab": {"count": 2}}, "/a", "/ab/count", "REVIEW")):
            def convert(row):
                row["verdict"]["numeric_resolutions"] = [{"token": "2000", "reason": "2*1000", "evidence": [],
                    "analysis_refs": [{"analysis_id": "A1", "output_path": resolution}],
                    "calculation": dict(BASIS, expression="2*1000", unit="milli-items")}]
            result = core.verify(analysis_packet(output), "Count is 2000 milli-items[A1].",
                                 analysis_calls(selected, change=convert), mode="paragraph")
            self.assertEqual(result["status"], expected)

    def test_conversion_requires_quoted_field_not_only_equal_value(self):
        for output, selected, quote, resolution in (
            ({"left": {"count": 2, "other": 2}}, "/left", '\"other\":2', "/left/count"),
            ({"left": {"count": 2}, "right": {"count": 2}}, "", '\"left\":{\"count\":2}', "/right/count")):
            def convert(row):
                row["verdict"]["numeric_resolutions"] = [{"token": "2000", "reason": "2*1000",
                    "analysis_refs": [{"analysis_id": "A1", "output_path": resolution}],
                    "calculation": dict(BASIS, expression="2*1000", unit="milli-items")}]
            result = core.verify(analysis_packet(output), "Count is 2000 milli-items[A1].",
                                 analysis_calls(selected, quote, convert), mode="paragraph")
            self.assertEqual(result["status"], "REVIEW")

    def test_json_string_digit_does_not_support_numeric_conversion(self):
        def convert(row):
            row["verdict"]["numeric_resolutions"] = [{"token": "2000", "reason": "2*1000",
                "analysis_refs": [{"analysis_id": "A1", "output_path": "/left/count"}],
                "calculation": dict(BASIS, expression="2*1000", unit="milli-items")}]
        result = core.verify(analysis_packet({"left": {"label": "2", "count": 2}}),
            "Count is 2000 milli-items[A1].", analysis_calls("/left", '\"label\":\"2\"', convert), mode="paragraph")
        self.assertEqual(result["status"], "REVIEW")
        self.assertTrue(result["verification_errors"])


class MaterialScopeRepairTests(unittest.TestCase):
    draft = "已观察到Alpha；该实验未做对照[E1:S1]。"
    unsupported = "该实验未做对照"

    def review(self, *, explicit=True, status="insufficient", bad_quote=False):
        def check(out, payload):
            row = out["checks"][0]
            row["verdict"].update(status=status, labels=["STR"] if status=="insufficient" else [])
            if explicit:
                row["verdict"].update(repair_kind="unreported_as_absent", repair_span=self.unsupported)
            if bad_quote:
                row["quotes"][0]["quote"] = "Invented quote."
            row["reason"] = "Materials report the observation but do not state whether a control was used."
        return core.verify(packet(), self.draft, Calls(check), mode="paragraph")

    def test_valid_explicit_scope_repair_preserves_all_other_characters(self):
        review = self.review()
        self.assertEqual(len(review["repair_claims"]), 1)
        expected = self.draft.replace(self.unsupported, "本次引用材料不足以确认“"+self.unsupported+"”这一说法")
        def revise(stage, instruction, payload):
            self.assertEqual(stage, "revise_claims")
            claim = payload["claims"][0]
            self.assertEqual(claim["scope_revision"], expected)
            return {"finish_reason": "stop", "output": {"replacements": [{"claim_id":claim["claim_id"], "text":expected}]}}
        result = core.revise(packet(), review, revise)
        self.assertEqual(result["draft"], expected)
        self.assertTrue(result["requires_verification"])
        self.assertEqual(review["draft"], self.draft)
        self.assertEqual(review["claim_checks"][0]["answer"]["verdict"]["status"], "insufficient")

    def test_ordinary_insufficient_and_invalid_verification_do_not_rewrite(self):
        for review in (self.review(explicit=False), self.review(bad_quote=True)):
            self.assertEqual(review["repair_claims"], [])
            result = core.revise(packet(), review, lambda *a:self.fail("No rewrite callback."))
            self.assertEqual(result["draft"], self.draft)

    def test_supported_or_contradicted_negative_facts_cannot_claim_scope_permission(self):
        for status in ("supported", "contradicted"):
            result = self.review(status=status)
            self.assertIn("claim_scope_repair_invalid", codes(result))
            self.assertEqual(result["repair_claims"], [])

    def test_scope_permission_cannot_be_added_after_original_model_receipt(self):
        review = self.review(explicit=False)
        review["units"][0]["claims"][0].update(
            repair_kind="unreported_as_absent", repair_span=self.unsupported)
        result = core.revise(packet(), review, lambda *a: self.fail("No post-hoc permission."))
        self.assertEqual(result["changed_claims"], [])
        self.assertEqual(result["draft"], self.draft)

    def test_correct_negative_fact_stays_unchanged(self):
        p = packet()
        p["evidence"][0]["text"] = "No effect on pain was found."
        draft = "No effect on pain was found[E1:S1]."
        review = core.verify(p, draft, Calls(), mode="paragraph")
        self.assertEqual(review["status"], "PASS")
        self.assertEqual(review["repair_claims"], [])
        result = core.revise(p, review, lambda *a: self.fail("No repair of a correct negative fact."))
        self.assertEqual(result["draft"], draft)

    def test_scope_edit_rechecks_changed_sentence_and_both_neighbours(self):
        draft = ("Alpha is present[E1:S1]. Beta is present[E1:S2]. "
                 "No control experiment was performed[E1:S3]. Delta is present[E1:S4]. Epsilon is present[E1:S5].")
        def check(out, payload):
            for row, claim in zip(out["checks"], payload["claims"]):
                if claim["text"].startswith("No control"):
                    row["verdict"].update(status="insufficient", labels=["STR"],
                        repair_kind="unreported_as_absent", repair_span="No control experiment was performed")
        old = core.verify(packet(), draft, Calls(check), mode="paragraph")
        def repair(stage, instruction, payload):
            return {"finish_reason": "stop", "output": {"replacements": [
                {"claim_id": c["claim_id"], "text": c["scope_revision"]} for c in payload["claims"]]}}
        revised = core.revise(packet(), old, repair)
        self.assertTrue(revised["requires_verification"])
        calls = Calls()
        checked = core.verify(packet(), revised["draft"], calls, mode="paragraph", previous_review=old)
        self.assertEqual(checked["status"], "PASS")
        requested = [c["text"] for stage, p in calls.calls if stage=="verify_claim" for c in p["claims"]]
        self.assertEqual(requested, [c["text"] for c in claims.sentences(revised["draft"])][1:4])
        self.assertEqual(len(checked["reused_claims"]), 2)
        self.assertEqual(sum(stage=="verify_coverage" for stage, p in calls.calls), 1)

    def test_posthoc_scope_fields_cannot_weaken_a_real_contradiction(self):
        p = packet()
        p["evidence"][0]["text"] = "该实验已做对照。"
        draft = "该实验未做对照[E1:S1]。"
        def check(out, payload):
            out["checks"][0]["verdict"].update(status="contradicted", labels=["STR"])
        review = core.verify(p, draft, Calls(check), mode="paragraph")
        review["units"][0]["claims"][0].update(
            repair_kind="unreported_as_absent", repair_span="该实验未做对照")
        def revise(stage, instruction, payload):
            claim = payload["claims"][0]
            self.assertNotIn("scope_revision", claim)
            self.assertNotIn("repair_kind", claim["finding"])
            return {"finish_reason":"stop", "output":{"replacements":[{
                "claim_id": claim["claim_id"], "text": "该实验已做对照[E1:S1]。"}]}}
        revised = core.revise(p, review, revise)
        self.assertEqual(revised["draft"], "该实验已做对照[E1:S1]。")

    def test_scope_repair_cannot_introduce_opposite_fact_or_delete_content(self):
        review = self.review()
        for replacement in ("该实验已做对照[E1:S1]。", "无法判断[E1:S1]。"):
            def revise(stage, instruction, payload):
                return {"finish_reason":"stop", "output":{"replacements":[{
                    "claim_id":payload["claims"][0]["claim_id"],"text":replacement}]}}
            with self.assertRaises(ValueError): core.revise(packet(), review, revise)


if __name__ == "__main__":
    unittest.main()
