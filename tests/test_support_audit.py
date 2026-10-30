"""Offline replay checks structure and isolation, not a model's semantic accuracy."""
import copy
import json
from pathlib import Path
import unittest

from app.core import audit_support, verify
from app.evidence import locate_support

CASE = json.loads((Path(__file__).parents[1] / "evaluation/selected_support_case.json").read_text())


class SupportAuditTests(unittest.TestCase):
    def setUp(self):
        self.packet = copy.deepcopy(CASE["packet"])
        self.review = verify(self.packet, CASE["draft"], lambda *args: copy.deepcopy(CASE["raw_output"]))
        self.before = copy.deepcopy(self.review)

    def answer(self, payload):
        return {"checks": [
            {"unit_id": c["unit_id"], "claim_index": c["claim_index"],
             "verdict": "not_supported", "reason": "离线测试回执；不代表模型语义评价", "support": []}
            for c in payload["claims"]]}

    def test_original_machine_pass_and_selected_wrong_segment_remain_replayable(self):
        self.assertEqual(self.review["status"], "PASS")
        selected = self.review["units"][0]["claims"][0]["evidence"]
        self.assertEqual([(s["segment_id"], s["start"], s["end"]) for s in selected], [("S1", 0, 1200)])
        text = self.packet["evidence"][0]["text"]
        self.assertNotIn("BCMA-CD3 bispecific antibody", selected[0]["quote"])
        self.assertEqual(text.index("BCMA-CD3 bispecific antibody"), 1844)

    def test_auditor_sees_only_each_selected_union_not_old_verdict_or_other_source_text(self):
        def call(stage, prompt, payload):
            self.assertEqual(stage, "support_audit")
            self.assertEqual(set(payload), {"claims"})
            c0, c2 = payload["claims"]
            self.assertEqual((c0["unit_id"], c0["claim_index"]), ("U1", 0))
            self.assertNotIn("verdict", c0)
            self.assertNotIn("reason", c0)
            self.assertEqual([e["segment_id"] for e in c0["evidence"]], ["S1"])
            self.assertNotIn("BCMA-CD3 bispecific antibody", c0["evidence"][0]["quote"])
            self.assertIn("NCT05020236", c0["evidence"][0]["source_identity"]["record_id"])
            self.assertEqual([e["segment_id"] for e in c2["evidence"]], ["S1", "S13"])
            out = self.answer(payload)
            support = c2["evidence"][1]
            out["checks"][1].update(verdict="supported", support=[{
                "evidence_id": support["evidence_id"], "segment_id": support["segment_id"],
                "quote": "a humanized bispecific antibody"}])
            return out
        audited = audit_support(self.packet, self.review, call, targets=[("U1", 0), ("U1", 2)])
        self.assertEqual(audited["status"], "REVIEW")
        self.assertEqual([c["verdict"] for c in audited["checks"]], ["not_supported", "supported"])
        self.assertEqual(self.review, self.before)
        self.assertEqual(audited["original_status"], "PASS")

    def test_manually_corrected_selection_uses_exact_s2_and_preserves_original_review(self):
        corrected = copy.deepcopy(self.review)
        lookup = {e["id"]: e for e in self.packet["evidence"]}
        corrected["units"][0]["claims"][0]["evidence"] = locate_support(
            [{"evidence_id": "qef6ac51e879c", "segment_id": "S2"}], lookup)
        def call(stage, prompt, payload):
            c = payload["claims"][0]
            self.assertEqual(c["evidence"][0]["start"], 1200)
            self.assertEqual(c["evidence"][0]["end"], 2157)
            return {"checks": [{"unit_id": "U1", "claim_index": 0, "verdict": "supported",
                "reason": "离线正确片段对照", "support": [{"evidence_id": "qef6ac51e879c",
                "segment_id": "S2", "quote": "BCMA-CD3 bispecific antibody elranatamab"}]}]}
        audited = audit_support(self.packet, corrected, call, targets=[("U1", 0)])
        self.assertEqual(audited["status"], "PASS")
        self.assertEqual(self.review, self.before)
        self.assertEqual(corrected["draft"], self.review["draft"])

    def test_hallucinated_quote_or_unselected_source_cannot_pass(self):
        for support in [
            {"evidence_id": "qef6ac51e879c", "segment_id": "S1", "quote": "BCMA-CD3 bispecific antibody"},
            {"evidence_id": "qef6ac51e879c", "segment_id": "S2", "quote": "BCMA-CD3 bispecific antibody"}]:
            with self.subTest(support=support):
                def call(stage, prompt, payload):
                    out = self.answer(payload)
                    out["checks"][0].update(verdict="supported", support=[support])
                    return out
                result = audit_support(self.packet, self.review, call, targets=[("U1", 0)])
                self.assertEqual(result["status"], "REVIEW")
                self.assertTrue(result["verification_errors"])

    def test_missing_duplicate_and_unknown_target_receipts_remain_review(self):
        for changes in ("missing", "duplicate", "unknown"):
            def call(stage, prompt, payload):
                out = self.answer(payload)
                if changes == "missing":
                    out["checks"].pop()
                elif changes == "duplicate":
                    out["checks"][1] = out["checks"][0]
                else:
                    out["checks"][1]["claim_index"] = 99
                return out
            result = audit_support(self.packet, self.review, call, targets=[("U1", 0), ("U1", 2)])
            self.assertEqual(result["status"], "REVIEW")
            self.assertTrue(result["verification_errors"])

    def test_absence_empty_targets_and_non_supported_claims_do_not_call(self):
        def no_call(*args):
            self.fail("不应调用模型")
        for targets in ([], [("U1", 3)]):
            result = audit_support(self.packet, self.review, no_call, targets=targets)
            self.assertEqual(result["status"], "NOT_RUN")
        self.review["units"][0]["claims"][0]["verdict"] = "insufficient"
        self.assertEqual(audit_support(self.packet, self.review, no_call, targets=[("U1", 0)])["status"], "NOT_RUN")

    def test_source_version_or_saved_quote_change_stops_before_call(self):
        def no_call(*args):
            self.fail("固定原文不一致，不应调用模型")
        for field, value in (("version", "other"), ("quote", "invented"), ("start", 1)):
            review = copy.deepcopy(self.review)
            review["units"][0]["claims"][0]["evidence"][0][field] = value
            result = audit_support(self.packet, review, no_call, targets=[("U1", 0)])
            self.assertEqual(result["status"], "REVIEW")
            self.assertTrue(result["verification_errors"])

    def test_old_verification_errors_do_not_disappear_or_trigger_call(self):
        self.review["verification_errors"] = [{"unit_id": "U1", "errors": ["定位不唯一"]}]
        result = audit_support(self.packet, self.review, lambda *args: self.fail("不得调用"))
        self.assertEqual(result["status"], "REVIEW")
        self.assertEqual(result["verification_errors"], self.review["verification_errors"])

    def test_unknown_requested_claim_is_caller_error(self):
        with self.assertRaises(ValueError):
            audit_support(self.packet, self.review, lambda *args: self.fail("不得调用"), targets=[("U1", 99)])


    def test_saved_real_comparisons_replay_without_api_and_leave_draft_unchanged(self):
        saved = json.loads((Path(__file__).parents[1] / "evaluation/selected_support_results.json").read_text())
        for row in saved["cases"]:
            with self.subTest(case=row["name"]):
                review = copy.deepcopy(self.review)
                if row["name"] == "manual_s2_control":
                    lookup = {e["id"]: e for e in self.packet["evidence"]}
                    review["units"][0]["claims"][0]["evidence"] = locate_support(
                        [{"evidence_id": "qef6ac51e879c", "segment_id": "S2"}], lookup)
                before = copy.deepcopy(review)
                audit = audit_support(self.packet, review, lambda *args: copy.deepcopy(row["raw_output"]),
                                      targets=[tuple(x) for x in row["targets"]])
                self.assertEqual(audit.pop("raw_response"), row["raw_output"])
                self.assertEqual(audit, row["audit"])
                self.assertEqual(review, before)


if __name__ == "__main__":
    unittest.main()
