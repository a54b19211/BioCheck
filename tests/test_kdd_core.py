"""Offline/stub protocol regressions; these are not new model or business results."""
import copy
import unittest

from app.core import generate, plan, write, verify, quality_feedback, revise, audit_support


PACKET = {"question": "说明本次材料的事实及范围", "evidence": [
    {"id": "E1", "version": "v1", "text": "来源称药物桥接两个靶点。"},
    {"id": "E2", "version": "v2", "text": "本次另有一份登记。"}]}
BASIS = {"analysis_unit": "project", "unit": "项目", "period": "2026-10-30截点",
         "population": "本窗口合格登记项目", "denominator": 4}
ANALYSIS = {"question": "本窗口多少项目？", "evidence": [], "assessment": {
    "analysis_results": [{"id": "A1", "dataset_ref": {"id": "snapshot-1", "version": "v1"},
        "method_version": "pipeline_count.v1", "parameters": {"status": "active"},
        "output": {"count": 2, "unrelated_count": 7},
        "input_refs": [{"source_record_id": "registry:p1", "version": "r1"},
                       {"source_record_id": "registry:p2", "version": "r1"}],
        "calculation_basis": BASIS, "limitations": ["局部窗口，不代表全行业"]}],
    "research_scope": [{"dimension": "patents", "status": "not_checked"}]}}


def reply(text, *, kind="fact", evidence=None, **extra):
    claim = {"span": text, "kind": kind, "verdict": "supported", "reason": "离线stub回执",
             "evidence": [{"evidence_id": "E1", "segment_id": "S1"}] if evidence is None else evidence}
    claim.update(extra)
    return {"reviews": [{"unit_id": "U1", "verdict": "supported", "claims": [claim],
                         "issues": [], "numeric_resolutions": [], "reason": "离线stub回执"}],
            "coverage": {"verdict": "complete", "reason": "只验证离线协议"}}


def analysis_reply(text):
    return reply(text, kind="analysis_result", evidence=[], binding=copy.deepcopy(BASIS),
                 analysis_refs=[{"analysis_id": "A1", "output_path": "/count"}])


class KddCoreTests(unittest.TestCase):
    def no_call(self, *args):
        self.fail("无效计划、故障或缺口不得继续调用模型")

    def test_plan_and_write_are_independent_and_generate_stays_compatible(self):
        planned = {"points": [{"intent": "来源事实", "evidence_ids": ["E1"], "binding": {},
                               "support": [{"evidence_id": "E1", "segment_id": "S1"}]}]}
        calls = []
        def call(stage, prompt, payload):
            calls.append((stage, payload))
            return copy.deepcopy(planned) if stage == "plan_bound" else {"draft": "来源称药物桥接两个靶点[E1]。"}
        selected = plan(PACKET, call)
        self.assertEqual([c[0] for c in calls], ["plan_bound"])
        written = write(PACKET, call, plan=selected)
        self.assertEqual([c[0] for c in calls], ["plan_bound", "write_bound"])
        self.assertNotIn("support", calls[-1][1]["plan"]["points"][0])
        self.assertEqual(written, generate(PACKET, call))
        self.assertIsNone(plan(PACKET, self.no_call, "direct"))

    def test_write_cannot_skip_a_required_plan_or_use_invalid_bindings(self):
        for method in ("plain", "bound"):
            with self.subTest(method=method), self.assertRaises(ValueError):
                write(PACKET, self.no_call, method)
        with self.assertRaises(ValueError):
            write(PACKET, self.no_call, "bound", plan={"points": [{"evidence_ids": ["E1"],
                "binding": {}, "support": [{"evidence_id": "E1", "segment_id": "S999"}]}]})

    def test_analysis_only_plan_write_and_verify_keep_supplied_provenance(self):
        text = "本窗口按给定方法计算有2个项目，属于局部探索[A1]。"
        planned = {"points": [{"intent": "本窗口计数", "evidence_ids": [], "analysis_ids": ["A1"],
            "binding": BASIS, "analysis_refs": [{"analysis_id": "A1", "output_path": "/count"}]}]}
        def call(stage, prompt, payload):
            self.assertEqual(payload["evidence"], [])
            self.assertEqual(payload["assessment"], ANALYSIS["assessment"])
            if stage == "plan_bound": return copy.deepcopy(planned)
            if stage == "write_bound": return {"draft": text}
            return analysis_reply(text)
        generated = generate(ANALYSIS, call)
        checked = verify(ANALYSIS, generated["draft"], call)
        self.assertEqual(checked["status"], "PASS")
        selected = checked["units"][0]["claims"][0]["analysis_refs"][0]
        self.assertEqual(selected["value"], 2)
        for key in ("dataset_ref", "method_version", "parameters", "input_refs", "limitations", "calculation_basis"):
            self.assertEqual(selected[key], ANALYSIS["assessment"]["analysis_results"][0][key])
        self.assertIn("复算及总体构建由Radar负责", checked["scope"])

    def test_analysis_number_needs_the_selected_output_not_an_unrelated_number(self):
        text = "本窗口有7个项目[A1]。"
        checked = verify(ANALYSIS, text, lambda *args: analysis_reply(text))
        self.assertEqual(checked["status"], "REVIEW")
        self.assertIn("7", checked["units"][0]["numeric_hints"])

    def test_analysis_unit_period_population_and_denominator_cannot_be_swapped(self):
        text = "本窗口有2个项目[A1]。"
        for field in BASIS:
            raw = analysis_reply(text)
            raw["reviews"][0]["claims"][0]["binding"][field] = "其它口径"
            with self.subTest(field=field):
                checked = verify(ANALYSIS, text, lambda *args: raw)
                self.assertEqual(checked["status"], "REVIEW")
                self.assertTrue(checked["verification_errors"])
                self.assertEqual(checked["raw_response"], raw)

    def test_analysis_output_path_and_fact_kind_are_checked(self):
        text = "本窗口有2个项目[A1]。"
        for change in ("bad_path", "fact"):
            raw = analysis_reply(text)
            claim = raw["reviews"][0]["claims"][0]
            if change == "bad_path": claim["analysis_refs"][0]["output_path"] = "/missing"
            else: claim["kind"] = "fact"
            with self.subTest(change=change):
                checked = verify(ANALYSIS, text, lambda *args: raw)
                self.assertEqual(checked["status"], "REVIEW")
                self.assertTrue(checked["verification_errors"])

    def test_incomplete_analysis_provenance_is_an_input_error(self):
        for field in ("dataset_ref", "method_version", "parameters", "output", "input_refs", "limitations"):
            packet = copy.deepcopy(ANALYSIS)
            del packet["assessment"]["analysis_results"][0][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                generate(packet, self.no_call, "direct")

    def test_output_conversion_needs_an_explicit_calculation(self):
        text = "本窗口项目占比为50%[A1]。"
        raw = analysis_reply(text)
        resolution = {"token": "50%", "reason": "离线stub：2/4*100=50%", "evidence": [],
                      "analysis_refs": [{"analysis_id": "A1", "output_path": "/count"}]}
        raw["reviews"][0]["numeric_resolutions"] = [resolution]
        self.assertEqual(verify(ANALYSIS, text, lambda *args: copy.deepcopy(raw))["status"], "REVIEW")
        resolution["calculation"] = {"expression": "2/4*100", "unit": "%", "period": BASIS["period"],
                                     "population": BASIS["population"], "denominator": 4,
                                     "analysis_unit": BASIS["analysis_unit"]}
        checked = verify(ANALYSIS, text, lambda *args: copy.deepcopy(raw))
        self.assertEqual(checked["status"], "PASS")
        self.assertEqual(checked["units"][0]["numeric_resolutions"][0]["calculation"]["unit"], "%")
        self.assertNotEqual(BASIS["unit"], "%")

    def test_conversion_rejects_wrong_scope_even_with_correct_claim_binding(self):
        text = "本窗口项目占比为50%[A1]。"
        wrong = {"period": "2020其它期间", "population": "不同总体", "denominator": 400,
                 "analysis_unit": "patient"}
        for field in wrong:
            raw = analysis_reply(text)
            calculation = dict(BASIS, expression="2/4*100", unit="%")
            calculation[field] = wrong[field]
            raw["reviews"][0]["numeric_resolutions"] = [{"token": "50%", "reason": "离线stub换算回执",
                "evidence": [], "analysis_refs": [{"analysis_id": "A1", "output_path": "/count"}],
                "calculation": calculation}]
            with self.subTest(field=field):
                checked = verify(ANALYSIS, text, lambda *args: copy.deepcopy(raw))
                self.assertEqual(checked["units"][0]["claims"][0]["binding"], BASIS)
                self.assertEqual(checked["status"], "REVIEW")
                self.assertTrue(checked["verification_errors"])
                self.assertEqual(checked["units"][0]["numeric_resolutions"], [])
                self.assertEqual(checked["raw_response"], raw)

    def test_mixed_scope_keeps_valid_segment_and_returns_a_separate_gap(self):
        text = "来源称药物桥接两个靶点，但本次材料未提供直接实验[E1]。"
        raw = reply(text, evidence=[{"evidence_id": "E1", "segment_id": "S1"},
                                    {"evidence_id": "E1", "scope": "whole_source"}],
                    missing_information="本次材料未提供直接实验")
        before = copy.deepcopy(raw)
        checked = verify(PACKET, text, lambda *args: raw)
        claim = checked["units"][0]["claims"][0]
        self.assertEqual(claim["evidence"][0]["segment_id"], "S1")
        self.assertEqual(claim["scope_evidence"][0]["scope"], "whole_source")
        self.assertFalse(checked["verification_errors"])
        feedback = quality_feedback(checked)
        self.assertEqual(feedback["evidence_gaps"][0]["kind"], "material_absence")
        self.assertEqual(feedback["repair_units"], [])
        self.assertEqual(raw, before)
        self.assertEqual(checked["raw_response"], before)
        raw["reviews"].clear()
        self.assertEqual(checked["raw_response"], before)
        self.assertEqual(audit_support(PACKET, checked, self.no_call)["status"], "NOT_RUN")

    def test_malformed_one_of_several_supports_does_not_erase_valid_passage(self):
        text = "来源称药物桥接两个靶点[E1]。"
        raw = reply(text, evidence=[{"evidence_id": "E1", "segment_id": "S1"},
                                    {"evidence_id": "E2", "segment_id": "S999"}])
        checked = verify(PACKET, text, lambda *args: raw)
        self.assertEqual(checked["status"], "REVIEW")
        self.assertEqual(checked["units"][0]["claims"][0]["evidence"][0]["segment_id"], "S1")
        self.assertTrue(checked["verification_errors"])
        self.assertEqual(revise(PACKET, checked, self.no_call)["changed_units"], [])

    def test_wrong_citation_is_a_failed_reference_without_medical_contradiction(self):
        text = "来源称药物桥接两个靶点[E2]。"
        checked = verify(PACKET, text, lambda *args: reply(text))
        claim = checked["units"][0]["claims"][0]
        self.assertEqual(checked["status"], "FAIL")
        self.assertEqual(claim["verdict"], "supported")
        self.assertEqual(claim["citation_status"], "invalid")
        self.assertNotEqual(checked["units"][0]["verdict"], "contradicted")
        self.assertEqual(quality_feedback(checked)["repair_units"], ["U1"])
        feedback = quality_feedback(checked, source_errors=[{"code": "source_unavailable"}])
        self.assertEqual(feedback["repair_units"], [])
        self.assertEqual(feedback["review_required"][0]["kind"], "source_failure")

    def test_package_absence_does_not_require_every_inspected_source_to_be_cited(self):
        text = "本次材料包未提供旧版报告，无法逐字比较[E1]。"
        raw = reply(text, kind="source_absence", missing_information="本次未提供旧版报告",
                    evidence=[{"evidence_id": "E1", "scope": "whole_source"},
                              {"evidence_id": "E2", "scope": "whole_source"}])
        checked = verify(PACKET, text, lambda *args: raw)
        self.assertEqual(checked["status"], "PASS")
        self.assertFalse(checked["units"][0]["issues"])
        self.assertEqual(quality_feedback(checked)["evidence_gaps"][0]["kind"], "material_absence")

    def test_absence_still_requires_a_citation_to_an_inspected_source(self):
        text = "本次材料未提供旧版报告[E2]。"
        raw = reply(text, kind="source_absence", missing_information="旧版报告",
                    evidence=[{"evidence_id": "E1", "scope": "whole_source"}])
        checked = verify(PACKET, text, lambda *args: raw)
        self.assertEqual(checked["status"], "FAIL")
        self.assertEqual(checked["units"][0]["claims"][0]["verdict"], "supported")
        self.assertEqual(checked["units"][0]["citation_status"], "invalid")

    def test_hypothesis_is_kept_separate_from_observed_fact(self):
        text = "桥接可能带来功能作用，仍是待验证假设[E1]。"
        checked = verify(PACKET, text, lambda *args: reply(text, kind="hypothesis"))
        self.assertEqual(checked["units"][0]["claims"][0]["kind"], "hypothesis")
        self.assertEqual(audit_support(PACKET, checked, self.no_call)["status"], "NOT_RUN")


if __name__ == "__main__":
    unittest.main()
