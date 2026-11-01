"""The main output example must name the fields enforced for unconfirmed sources."""
import copy
import json
import unittest

from app.prompts import VERIFY
from app.rpc import execute, SCHEMA_VERSION


def main_example(instruction):
    start = instruction.index("返回：")
    start = instruction.index("{", start)
    return json.JSONDecoder().raw_decode(instruction[start:])[0]


class PromptContractTests(unittest.TestCase):
    def test_primary_output_example_lists_source_use_and_anchors(self):
        claim = main_example(VERIFY)["reviews"][0]["claims"][0]
        self.assertTrue({"source_use", "attribution_span", "uncertainty_span", "history_context"} <= claim.keys())
        self.assertIn("attributed_report", claim["source_use"])
        self.assertIn("hypothesis", claim["source_use"])

    def test_rpc_delivers_contract_without_filling_missing_model_fields(self):
        draft = "帖子称有人出现症状，尚未证实[E1]。"
        packet = {"question": "这条消息说明什么？", "evidence": [{"id": "E1", "record_id": "post:1",
            "version": "v1", "text": "有人出现症状。", "source_kind": "social_media",
            "verification_status": "unverified"}]}
        raw = {"reviews": [{"unit_id": "U1", "verdict": "supported", "reason": "人工协议例",
            "claims": [{"span": draft, "kind": "fact", "verdict": "supported", "reason": "转述",
                        "evidence": [{"evidence_id": "E1", "segment_id": "S1"}]}],
            "issues": [], "numeric_resolutions": []}],
            "coverage": {"verdict": "complete", "reason": "有限材料"}}
        for complete in (False, True):
            output = copy.deepcopy(raw)
            if complete:
                output["reviews"][0]["claims"][0].update(source_use="attributed_report",
                    attribution_span="帖子称", uncertainty_span="尚未证实", history_context=None)
            request = {"schema_version": SCHEMA_VERSION, "operation_id": "source-contract-" + str(complete),
                       "operation": "verify", "provider_version": None,
                       "arguments": {"packet": packet, "draft": draft}, "model_receipts": []}
            step = execute(request)
            call = step["model_call"]
            self.assertEqual(call["instruction"], VERIFY)
            self.assertIn("source_use", main_example(call["instruction"])["reviews"][0]["claims"][0])
            self.assertIsNone(call["schema"])
            request.update(provider_version=step["provider_version"], model_receipts=[{
                "call_id": call["call_id"], "request_hash": call["request_hash"], "output": output}])
            result = execute(request)["result"]
            self.assertEqual(result["raw_response"], output)
            self.assertEqual(result["draft"], draft)
            self.assertEqual(result["status"], "PASS" if complete else "REVIEW")
            if complete:
                self.assertEqual(result["units"][0]["claims"][0]["support_scope"], "attribution_only")
            else:
                self.assertNotIn("source_use", result["units"][0]["claims"][0])
                self.assertEqual(result["units"][0]["source_gaps"][0]["kind"], "unconfirmed_source")


if __name__ == "__main__":
    unittest.main()
