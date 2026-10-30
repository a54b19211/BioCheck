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


if __name__ == "__main__":
    unittest.main()
