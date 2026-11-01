"""One compatibility case from a real plan; no model or source requests."""
import copy
import unittest

from app.core import plan
from app.evidence import locate_analysis


class AnalysisPathTests(unittest.TestCase):
    def setUp(self):
        self.analysis = {
            "id": "A1", "dataset_ref": {"id": "fixture", "version": "v1"},
            "method_version": "count.v1", "parameters": {}, "input_refs": [{"record_id": "r1", "version": "v1"}],
            "limitations": ["接口例"], "output": {"statistics": {"analysis_units": 7}},
        }
        self.results = {"A1": self.analysis}

    def test_record_root_spelling_preserves_original_and_resolves_same_fixed_value(self):
        before = copy.deepcopy(self.analysis)
        relative = locate_analysis([{"analysis_id": "A1", "output_path": "/statistics/analysis_units"}], self.results)[0]
        rooted = locate_analysis([{"analysis_id": "A1", "output_path": "/output/statistics/analysis_units"}], self.results)[0]
        self.assertEqual(rooted.pop("requested_output_path"), "/output/statistics/analysis_units")
        self.assertEqual(rooted, relative)
        self.assertEqual(rooted["value"], 7)
        self.assertEqual(self.analysis, before)

    def test_real_output_field_keeps_its_original_meaning_and_never_falls_back(self):
        self.analysis["output"] = {"output": {"count": 5}, "count": 2}
        result = locate_analysis([{"analysis_id": "A1", "output_path": "/output/count"}], self.results)[0]
        self.assertEqual(result["value"], 5)
        self.assertNotIn("requested_output_path", result)
        self.analysis["output"]["output"] = {}
        with self.assertRaises(ValueError):
            locate_analysis([{"analysis_id": "A1", "output_path": "/output/count"}], self.results)

    def test_nonexistent_field_and_repeated_container_prefix_are_not_guessed(self):
        for path in ("/output/missing", "/output/output/statistics/analysis_units",
                     "/result/statistics/analysis_units"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                locate_analysis([{"analysis_id": "A1", "output_path": path}], self.results)

    def test_plan_accepts_compatible_location_without_rewriting_model_reply(self):
        packet = {"question": "报告本窗口分析单元数", "evidence": [],
                  "assessment": {"analysis_results": [self.analysis]}}
        output = {"points": [{"intent": "报告固定窗口", "evidence_ids": [], "analysis_ids": ["A1"],
                              "binding": {"population": "固定窗口"}, "conditions": [], "support": [],
                              "analysis_refs": [{"analysis_id": "A1", "output_path": "/output/statistics/analysis_units"}]}]}
        original = copy.deepcopy(output)
        result = plan(packet, lambda *args: output)
        self.assertEqual(result, original)
        self.assertEqual(output, original)



class SharedAnalysisContextTests(unittest.TestCase):
    def packet(self):
        from copy import deepcopy
        item = {"id": "A1", "dataset_ref": "fixed", "method_version": "test",
                "parameters": {"mode": "fixed"}, "calculation_basis": {"unit": "items"},
                "limitations": ["synthetic"], "output": {"count": 2},
                "input_refs": [{"source_record_id": "record", "version": "v1"}]}
        return {"question": "count", "evidence": [], "assessment": {
            "analysis_results": [item, dict(deepcopy(item), id="A2")]}}

    def compact(self, packet, fields):
        from copy import deepcopy
        value = deepcopy(packet)
        rows = value["assessment"]["analysis_results"]
        value["assessment"]["analysis_result_context"] = {
            "projection_version": "shared-calculation-context-v1",
            "applies_to_result_ids": [r["id"] for r in rows],
            "fields": {k: deepcopy(rows[0][k]) for k in fields},
            "source_paths": {k: [f"$.analysis_results[{i}].{k}" for i in range(len(rows))] for k in fields}}
        for row in rows:
            for key in fields:
                row.pop(key)
        return value

    def test_shared_subset_restores_exact_values_without_mutating_packet(self):
        from copy import deepcopy
        from app.evidence import analysis_results, analysis_signature
        original = self.packet()
        for fields in (["parameters"], ["parameters", "calculation_basis", "limitations"]):
            compact = self.compact(original, fields)
            before = deepcopy(compact)
            self.assertEqual(analysis_results(compact), analysis_results(original))
            self.assertEqual(analysis_signature(compact), analysis_signature(original))
            self.assertEqual(compact, before)

    def test_conflict_unknown_target_and_wrong_origin_path_are_rejected(self):
        from copy import deepcopy
        from app.evidence import analysis_results
        original = self.compact(self.packet(), ["parameters"])
        for change in ("conflict", "unknown", "wrong_path"):
            value = deepcopy(original)
            if change == "conflict":
                value["assessment"]["analysis_results"][0]["parameters"] = {"mode": "different"}
            elif change == "unknown":
                value["assessment"]["analysis_result_context"]["applies_to_result_ids"][0] = "UNKNOWN"
            else:
                value["assessment"]["analysis_result_context"]["source_paths"]["parameters"][0] = "$.analysis_results[1].parameters"
            with self.assertRaises(ValueError):
                analysis_results(value)

    def test_shared_context_rejects_bool_integer_and_float_type_conflicts(self):
        from app.evidence import analysis_results
        for left, right in ((True, 1), (1, 1.0), ({"nested": [False]}, {"nested": [0]})):
            value = self.compact(self.packet(), ["parameters"])
            value["assessment"]["analysis_results"][0]["parameters"] = {"flag": left}
            value["assessment"]["analysis_result_context"]["fields"]["parameters"] = {"flag": right}
            before = copy.deepcopy(value)
            with self.assertRaises(ValueError):
                analysis_results(value)
            self.assertEqual(value, before)

if __name__ == "__main__":
    unittest.main()
