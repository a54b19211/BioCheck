"""Authored regression cases derived from the retained A/B/C audit findings.

The original three-scenario private packets were deleted before this work.
These cases exercise code contracts; they are not fresh model measurements.
"""
import copy
import unittest

from app.core import audit_support, quality_feedback, revise, verify, run
from app.evidence import model_input


PACKET = {"question": "当前订单情况如何？", "evidence": [
    {"id": "qOld", "record_id": "archive:order", "version": "v1",
     "text": "上半年订单8000万元，共40套。"},
    {"id": "qUpdate", "record_id": "archive:correction", "version": "v2",
     "text": "更正上半年订单为6400万元，共32套。"}],
    "assessment": {"source_updates": [{"kind": "correction",
        "target": {"record_id": "archive:order", "version": "v1"},
        "replacement": {"record_id": "archive:correction", "version": "v2"},
        "reason": "原披露存在重复计数"}]}}


def answer(text, refs=None, **fields):
    claim = {"span": text, "verdict": "supported", "kind": "fact", "reason": "离线回执",
             "evidence": refs if refs is not None else [{"evidence_id": "qOld", "segment_id": "S1"}]}
    claim.update(fields)
    return {"reviews": [{"unit_id": "U1", "verdict": "supported", "reason": "离线回执",
                         "claims": [claim], "issues": [], "numeric_resolutions": []}],
            "coverage": {"verdict": "complete", "reason": "离线覆盖"}}


def checked(packet, text, refs=None, **fields):
    raw = answer(text, refs, **fields)
    return verify(packet, text, lambda *args: copy.deepcopy(raw))


class SourceContextTests(unittest.TestCase):
    def no_call(self, *args):
        self.fail("待核时不得自动调用改稿")

    def test_old_current_numbers_are_pending_even_if_model_says_supported(self):
        text = "上半年订单8000万元，共40套[qOld]。"
        raw = answer(text)
        result = verify(PACKET, text, lambda *args: raw)
        self.assertEqual(result["status"], "REVIEW")
        self.assertEqual(result["raw_response"], raw)
        self.assertEqual(result["draft"], text)
        gap = quality_feedback(result)["evidence_gaps"][0]
        self.assertEqual(gap["kind"], "source_updated")
        self.assertEqual(gap["read_requests"][0]["record_id"], "archive:correction")
        self.assertEqual(gap["read_requests"][0]["version"], "v2")
        self.assertEqual(revise(PACKET, result, self.no_call)["changed_units"], [])

    def test_missing_correction_body_still_returns_specific_read_request(self):
        packet = copy.deepcopy(PACKET)
        packet["evidence"].pop()
        result = checked(packet, "上半年订单8000万元，共40套[qOld]。")
        self.assertEqual(result["status"], "REVIEW")
        request = result["read_requests"][0]
        self.assertEqual((request["record_id"], request["version"]), ("archive:correction", "v2"))
        self.assertIsNone(request["evidence_id"])
        self.assertFalse(result["verification_errors"])

    def test_historical_account_requires_real_anchors_and_read_correction(self):
        text = "当时披露订单8000万元、40套，后更正为6400万元、32套[qOld][qUpdate]。"
        refs = [{"evidence_id": key, "segment_id": "S1"} for key in ("qOld", "qUpdate")]
        fields = {"source_use": "historical", "history_context": {"then_span": "当时披露", "update_span": "后更正"}}
        result = checked(PACKET, text, refs, **fields)
        self.assertEqual(result["status"], "PASS")
        # Merely adding a citation without selecting the correction passage is not enough.
        self.assertEqual(checked(PACKET, text, refs[:1], **fields)["status"], "REVIEW")
        self.assertEqual(checked(PACKET, text, refs[1:], **fields)["status"], "REVIEW")
        current = "当前订单8000万元，共40套[qOld]。"
        self.assertEqual(checked(PACKET, current, **fields)["status"], "REVIEW")

    def test_loaded_correction_can_support_revision_after_a_fresh_valid_check(self):
        text = "上半年订单8000万元，共40套[qOld]。"
        raw = answer(text, [{"evidence_id": "qUpdate", "segment_id": "S1"}], verdict="contradicted")
        review = verify(PACKET, text, lambda *args: raw)
        self.assertEqual(review["status"], "FAIL")
        self.assertEqual(quality_feedback(review)["repair_units"], ["U1"])
        fixed = "更正后上半年订单6400万元，共32套[qUpdate]。"
        revision = revise(PACKET, review, lambda *args: {"replacements": [{"unit_id": "U1", "text": fixed}]})
        self.assertTrue(revision["requires_verification"])
        fresh = checked(PACKET, revision["draft"], [{"evidence_id": "qUpdate", "segment_id": "S1"}])
        self.assertEqual(fresh["status"], "PASS")
        self.assertEqual(review["draft"], text)

    def test_new_values_and_other_versions_are_not_marked_obsolete(self):
        text = "更正后上半年订单6400万元，共32套[qUpdate]。"
        result = checked(PACKET, text, [{"evidence_id": "qUpdate", "segment_id": "S1"}])
        self.assertEqual(result["status"], "PASS")
        packet = copy.deepcopy(PACKET)
        packet["assessment"]["source_updates"][0]["target"]["version"] = "other-version"
        result = checked(packet, "上半年订单8000万元，共40套[qOld]。")
        self.assertFalse(result["units"][0].get("source_gaps"))

    def test_retraction_is_not_new_positive_evidence(self):
        packet = copy.deepcopy(PACKET)
        packet["assessment"]["source_updates"][0]["kind"] = "retraction"
        packet["evidence"][1]["text"] = "已撤回此前的订单数字，没有提供新数字。"
        result = checked(packet, "上半年订单8000万元，共40套[qOld]。")
        self.assertEqual(result["status"], "REVIEW")
        self.assertEqual(result["draft"], "上半年订单8000万元，共40套[qOld]。")

    def test_analysis_cannot_launder_an_obsolete_source(self):
        packet = copy.deepcopy(PACKET)
        basis = {"analysis_unit": "order", "unit": "万元", "period": "H1", "population": "本公司", "denominator": 1}
        packet["assessment"]["analysis_results"] = [{"id": "A1", "dataset_ref": "d1",
            "method_version": "sum.v1", "parameters": {}, "output": {"amount": 8000},
            "input_refs": [{"source_record_id": "archive:order", "version": "v1"}],
            "calculation_basis": basis, "limitations": []}]
        result = checked(packet, "本次数据集订单8000万元[A1]。", [], kind="analysis_result",
                         analysis_refs=[{"analysis_id": "A1", "output_path": "/amount"}], binding=basis)
        self.assertEqual(result["status"], "REVIEW")
        self.assertEqual(result["units"][0]["source_gaps"][0]["kind"], "source_updated")

    def test_missing_evidence_id_is_not_inferred_but_a_read_target_is_available(self):
        packet = dict(PACKET, assessment={})
        result = checked(packet, "上半年订单8000万元，共40套[qOld]。", [{"segment_id": "S1"}])
        self.assertEqual(result["status"], "REVIEW")
        self.assertTrue(result["verification_errors"])
        self.assertEqual(result["units"][0]["claims"][0]["evidence"], [])
        requests = quality_feedback(result)["evidence_gaps"][0]["read_requests"]
        self.assertTrue(any((x["record_id"], x["version"]) == ("archive:order", "v1") for x in requests))
        self.assertEqual(revise(packet, result, self.no_call)["changed_units"], [])

    def test_wrong_segment_retains_requested_location_without_making_it_support(self):
        packet = dict(PACKET, assessment={})
        result = checked(packet, "上半年订单8000万元，共40套[qOld]。",
                         [{"evidence_id": "qOld", "segment_id": "S999", "version": "v0"}])
        request = result["read_requests"][0]
        self.assertEqual((request["version"], request["requested_version"]), ("v1", "v0"))
        self.assertEqual(request["requested_segment_id"], "S999")
        self.assertEqual(result["units"][0]["claims"][0]["evidence"], [])

    def test_social_accurate_attribution_is_separate_from_confirmation(self):
        packet = {"question": "发生了什么", "evidence": [{"id": "E1", "record_id": "post:1", "version": "p1",
            "text": "有多人出现症状。", "source_kind": "social_media", "verification_status": "unverified",
            "source_context": {"original_post_id": "p1", "repost_of": None, "independent_source_group": "g1"}}]}
        refs = [{"evidence_id": "E1", "segment_id": "S1"}]
        text = "网传多人出现症状，尚未证实[E1]。"
        valid = checked(packet, text, refs, source_use="attributed_report",
                        attribution_span="网传", uncertainty_span="尚未证实")
        self.assertEqual(valid["status"], "PASS")
        self.assertEqual(valid["units"][0]["claims"][0]["support_scope"], "attribution_only")
        false_fact = checked(packet, "已确认多人出现症状[E1]。", refs,
                             source_use="attributed_report", attribution_span="网传", uncertainty_span="尚未证实")
        self.assertEqual(false_fact["status"], "REVIEW")
        self.assertEqual(model_input(packet)["evidence"][0]["source_context"], packet["evidence"][0]["source_context"])

    def test_information_environment_alone_does_not_establish_disease_or_raise_risk(self):
        packet = {"question": "如何理解通报", "evidence": [{"id": "E1", "record_id": "event:1", "version": "v1",
            "text": "该帖子被删除。", "source_context": {"information_environment_event": "deleted"}}]}
        refs = [{"evidence_id": "E1", "segment_id": "S1"}]
        event = checked(packet, "该帖子被删除[E1]。", refs, source_use="information_environment", attribution_span="删除")
        self.assertEqual(event["status"], "PASS")
        risk = checked(packet, "疑似疫情风险上升[E1]。", refs, kind="hypothesis",
                       source_use="hypothesis", uncertainty_span="疑似")
        self.assertEqual(risk["status"], "REVIEW")

    def test_old_audit_does_not_survive_other_paragraph_or_source_status_change(self):
        packet = dict(PACKET, assessment={})
        review = checked(packet, "上半年订单8000万元，共40套[qOld]。")
        def call(stage, prompt, payload):
            return {"checks": [{"unit_id": "U1", "claim_index": 0, "verdict": "supported", "reason": "离线",
                "support": [{"evidence_id": "qOld", "segment_id": "S1", "quote": "订单8000万元"}]}]}
        audit = audit_support(packet, review, call)
        changed = copy.deepcopy(review)
        changed["draft"] += "\n\n新增判断。"
        self.assertTrue(quality_feedback(changed, audit)["review_required"])
        changed_packet = copy.deepcopy(packet)
        changed_packet["evidence"][0]["verification_status"] = "unverified"
        self.assertEqual(audit_support(changed_packet, review, self.no_call)["status"], "REVIEW")
        self.assertEqual(revise(changed_packet, review, self.no_call)["changed_units"], [])

    def test_changed_analysis_cannot_reuse_old_review_for_revision_or_audit(self):
        from test_kdd_core import ANALYSIS as analysis_packet, analysis_reply
        packet = copy.deepcopy(analysis_packet)
        raw = analysis_reply("本次3个项目[A1]。")
        raw["reviews"][0]["claims"][0]["verdict"] = "contradicted"
        review = verify(packet, "本次3个项目[A1]。", lambda *args: raw)
        self.assertEqual(review["status"], "FAIL")
        changed = copy.deepcopy(packet)
        changed["assessment"]["analysis_results"][0]["output"]["count"] = 3
        changed["assessment"]["analysis_results"][0]["dataset_ref"]["version"] = "v2"
        self.assertEqual(revise(changed, review, self.no_call)["changed_units"], [])
        self.assertEqual(audit_support(changed, review, self.no_call)["status"], "REVIEW")

    def test_top_level_requests_include_ordinary_insufficient_claims(self):
        packet = dict(PACKET, assessment={})
        result = checked(packet, "订单数据还需更多说明[qOld]。", [], verdict="insufficient")
        self.assertFalse(result["verification_errors"])
        self.assertTrue(result["read_requests"])
        self.assertEqual(result["read_requests"][0]["record_id"], "archive:order")

    def test_unconfirmed_deletion_cannot_use_environment_exception(self):
        packet = {"question": "发生了什么", "evidence": [{"id": "E1", "record_id": "post:1", "version": "v1",
            "text": "据称相关帖子已被删除。", "source_kind": "social_media", "verification_status": "unverified"}]}
        refs = [{"evidence_id": "E1", "segment_id": "S1"}]
        for metadata in ({}, {"information_environment_event": "deleted"}):
            packet["evidence"][0]["source_context"] = metadata
            direct = checked(packet, "相关帖子已被删除[E1]。", refs,
                             source_use="information_environment", attribution_span="删除")
            self.assertEqual(direct["status"], "REVIEW")
            attributed = checked(packet, "网传相关帖子已被删除，尚未证实[E1]。", refs,
                                 source_use="attributed_report", attribution_span="网传", uncertainty_span="尚未证实")
            self.assertEqual(attributed["status"], "PASS")
            self.assertEqual(attributed["units"][0]["claims"][0]["support_scope"], "attribution_only")

    def test_actual_revision_is_checked_again_and_failure_is_not_relabelled(self):
        packet = {"question": "订单", "evidence": [PACKET["evidence"][1]]}
        wrong, repaired = "订单8000万元[qUpdate]。", "订单6400万元[qUpdate]。"
        stages = []
        def call(stage, prompt, payload):
            stages.append(stage)
            if stage == "write_direct":
                return {"draft": wrong}
            if stage == "revise":
                return {"replacements": [{"unit_id": "U1", "text": repaired}]}
            text = payload["draft"]
            raw = answer(text, [{"evidence_id": "qUpdate", "segment_id": "S1"}], verdict="contradicted")
            raw["reviews"][0]["verdict"] = "contradicted"
            return raw
        result = run(packet, call, "direct")
        self.assertEqual(stages, ["write_direct", "verify", "revise", "verify"])
        self.assertEqual(result["initial_review"]["draft"], wrong)
        self.assertEqual(result["final_review"]["draft"], repaired)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["revision"]["requires_verification"])


if __name__ == "__main__":
    unittest.main()
