import copy
import unittest

from app.core import quality_feedback, revise, run, verify
from evaluation.replay_research_loop import examples

PACKET = {"question": "说明药物机制", "evidence": [
    {"id": "E1", "version": "v1", "text": "药物A作用于靶点T。"}]}


def reply(draft, verdict="supported", *, kind="fact", evidence=True):
    claim = {"span": draft, "kind": kind, "verdict": verdict, "reason": "离线接口测试",
             "evidence": [{"evidence_id": "E1", "segment_id": "S1"}] if evidence else []}
    return {"reviews": [{"unit_id": "U1", "verdict": verdict, "claims": [claim],
                         "reason": "离线接口测试", "issues": [], "numeric_resolutions": []}],
            "coverage": {"verdict": "complete", "reason": "接口测试"}}


class FeedbackTests(unittest.TestCase):
    def no_call(self, *args):
        self.fail("此问题不应触发自动修订或新增模型调用")

    def test_plain_evidence_gap_stops_after_write_and_verify(self):
        draft = "该药物没有专利限制[E1]。"
        raw = reply(draft, "insufficient", evidence=False)
        stages = []
        def call(stage, instruction, payload):
            stages.append(stage)
            if stage == "write_direct":
                return {"draft": draft}
            if stage == "verify":
                return copy.deepcopy(raw)
            return self.no_call()
        result = run(PACKET, call, method="direct")
        self.assertEqual(stages, ["write_direct", "verify"])
        self.assertIsNone(result["revision"])
        self.assertEqual(result["feedback"]["evidence_gaps"][0]["kind"], "missing_support")
        self.assertEqual(result["final_text"], draft)

    def test_direct_revise_blocks_invalid_verification_even_with_failure(self):
        draft = "该药物作用于另一靶点[E1]。"
        raw = reply(draft, "contradicted")
        raw["reviews"][0]["claims"][0]["evidence"][0]["segment_id"] = "S999"
        review = verify(PACKET, draft, lambda *args: raw)
        self.assertTrue(review["verification_errors"])
        result = revise(PACKET, review, self.no_call)
        self.assertEqual(result["draft"], draft)
        self.assertTrue(result["feedback"]["evidence_gaps"][0]["read_requests"])
        self.assertTrue(result["feedback"]["review_required"])

    def test_anchored_false_absence_is_repairable(self):
        draft = "本材料没有说明药物靶点[E1]。"
        raw = reply(draft, "contradicted", kind="source_absence")
        raw["reviews"][0]["claims"][0]["missing_information"] = "药物靶点"
        review = verify(PACKET, draft, lambda *args: raw)
        self.assertEqual(quality_feedback(review)["repair_units"], ["U1"])
        fixed = revise(PACKET, review, lambda *args: {
            "replacements": [{"unit_id": "U1", "text": "药物A作用于靶点T[E1]。"}]})
        self.assertEqual(len(fixed["changed_units"]), 1)

    def test_safe_repair_is_followed_by_full_verification_once(self):
        wrong, correct = "药物A作用于另一靶点[E1]。", "药物A作用于靶点T[E1]。"
        stages = []
        def call(stage, instruction, payload):
            stages.append(stage)
            if stage == "write_direct":
                return {"draft": wrong}
            if stage == "revise":
                return {"replacements": [{"unit_id": "U1", "text": correct}]}
            if stage == "verify":
                return reply(payload["draft"], "contradicted" if payload["draft"] == wrong else "supported")
            return self.no_call()
        result = run(PACKET, call, method="direct")
        self.assertEqual(stages, ["write_direct", "verify", "revise", "verify"])
        self.assertEqual(result["final_text"], correct)
        self.assertEqual(result["initial_feedback"]["repair_units"], ["U1"])

    def test_source_failure_or_missing_review_requires_check_not_edit(self):
        draft = "药物A作用于另一靶点[E1]。"
        review = verify(PACKET, draft, lambda *args: reply(draft, "contradicted"))
        result = revise(PACKET, review, self.no_call, source_errors=[{"code": "source_version_unavailable"}])
        self.assertEqual(result["changed_units"], [])
        self.assertEqual(result["feedback"]["review_required"][0]["kind"], "source_failure")
        self.assertTrue(quality_feedback(None)["review_required"])

    def test_unit_with_real_gap_and_error_is_not_automatically_rewritten(self):
        draft = "药物A作用于另一靶点；药物A没有专利限制[E1]。"
        raw = reply(draft, "contradicted")
        raw["reviews"][0]["claims"] = [
            reply("药物A作用于另一靶点", "contradicted")["reviews"][0]["claims"][0],
            reply("药物A没有专利限制", "insufficient", evidence=False)["reviews"][0]["claims"][0]]
        review = verify(PACKET, draft, lambda *args: raw)
        feedback = quality_feedback(review)
        self.assertEqual(feedback["repair_units"], [])
        self.assertEqual(feedback["evidence_gaps"][0]["kind"], "missing_support")
        self.assertEqual(review["status"], "FAIL")
        self.assertFalse(revise(PACKET, review, self.no_call)["changed_units"])

    def test_coverage_gap_does_not_claim_all_research_has_been_done(self):
        draft = "药物A作用于靶点T[E1]。"
        raw = reply(draft)
        raw["coverage"] = {"verdict": "incomplete", "reason": "未查竞争项目和专利范围"}
        feedback = quality_feedback(verify(PACKET, draft, lambda *args: raw))
        self.assertEqual(feedback["evidence_gaps"][0]["kind"], "coverage")
        self.assertIn("专利", feedback["evidence_gaps"][0]["reason"])
        self.assertEqual(feedback["repair_units"], [])

    def test_real_selected_support_failure_returns_gap_not_author_error(self):
        record = examples()["selected_support_gap"]
        before = copy.deepcopy(record["review"])
        feedback = quality_feedback(record["review"], record["support_audit"])
        selection = [g for g in feedback["evidence_gaps"] if g["kind"] == "evidence_selection"]
        self.assertEqual([(x["unit_id"], x["claim_index"]) for x in selection], [("U1", 0)])
        self.assertEqual(selection[0]["action"], "reselect_or_retrieve")
        self.assertEqual(feedback["machine_status"], "PASS")
        self.assertEqual(feedback["repair_units"], [])
        self.assertFalse(feedback["review_required"])
        self.assertEqual(record["review"], before)

    def test_audit_failure_and_stale_audit_are_not_missing_source_facts(self):
        record = examples()["selected_support_gap"]
        for mode in ("error", "stale", "uncertain"):
            with self.subTest(mode=mode):
                audit = copy.deepcopy(record["support_audit"])
                if mode == "error":
                    audit["verification_errors"] = [{"reason": "摘录不在片段中"}]
                elif mode == "stale":
                    audit["checks"][0]["context"] = "另一版本稿件"
                else:
                    audit["checks"][0]["verdict"] = "uncertain"
                feedback = quality_feedback(record["review"], audit)
                self.assertTrue(feedback["review_required"])
                self.assertEqual(feedback["repair_units"], [])
                self.assertFalse([g for g in feedback["evidence_gaps"] if g["kind"] == "evidence_selection"])

    def test_new_version_and_comparison_are_verified_without_overwriting_old(self):
        record = examples()["report_versions"]
        old, new = record["reports"]
        self.assertEqual(new["parent_report_id"], old["report_id"])
        self.assertNotEqual(old["result"]["final_text"], new["result"]["final_text"])
        self.assertIn("未提供", old["result"]["final_text"])
        self.assertIn("BCMA-CD3", new["result"]["final_text"])
        self.assertEqual(new["calls"][-1]["draft"], new["result"]["final_text"])
        self.assertIn("上一版", new["result"]["final_review"]["units"][1]["text"])
        self.assertTrue(old["result"]["feedback"]["evidence_gaps"])
        self.assertTrue(any(e.get("text_basis") == "prior_report" for e in new["packet"]["evidence"]))
        self.assertEqual(old["packet"]["assessment"]["research_scope"],
                         new["packet"]["assessment"]["research_scope"])
        self.assertTrue(all(x["status"] == "not_checked" for x in
                            new["packet"]["assessment"]["research_scope"] if x["dimension"] != "support"))
        self.assertEqual([x["stage"] for x in new["calls"]], ["write_direct", "verify"])

    def test_missing_comparison_unit_cannot_pass_final_verification(self):
        new = examples()["report_versions"]["reports"][1]
        def call(stage, instruction, payload):
            # Simulate a verifier that silently skipped the comparison paragraph.
            return {"reviews": [copy.deepcopy(u) for u in new["result"]["final_review"]["units"]
                                if u["unit_id"] != "U2"],
                    "coverage": {"verdict": "complete", "reason": "漏检示例"}}
        with self.assertRaises(ValueError):
            verify(new["packet"], new["result"]["final_text"], call)


if __name__ == "__main__":
    unittest.main()
