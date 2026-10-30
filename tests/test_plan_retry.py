"""A single opt-in replan uses the host's existing receipt sequence, never a local API."""
import copy
import json
import unittest

from app import core
from app.rpc import execute, capabilities
from test_service import PACKET, DRAFT, PLAN, VERIFY, request, receipt
from test_kdd_core import ANALYSIS


def bad_plan():
    result = copy.deepcopy(PLAN)
    result["points"][0]["support"][0]["segment_id"] = "S999"
    return result


def after_plan(output=None, *, retries=1, operation="generate", packet=PACKET):
    body = request(operation, {"packet": packet, "max_plan_retries": retries}, operation_id="bounded-plan")
    first = execute(body)
    body.update(provider_version=first["provider_version"],
                model_receipts=[receipt(first, bad_plan() if output is None else output)])
    return body, execute(body)


class PlanRetryTests(unittest.TestCase):
    def test_default_zero_keeps_original_plan_and_returns_typed_diagnostics(self):
        invalid = bad_plan()
        before = copy.deepcopy(invalid)
        body, failed = after_plan(invalid, retries=0)
        self.assertEqual(failed["error_code"], "plan_reference_invalid")
        self.assertEqual(failed["plan_retry"], {"eligible": True, "attempted": 0, "limit": 1})
        self.assertEqual(failed["diagnostics"][0]["code"], "source_segment_invalid")
        self.assertEqual(failed["diagnostics"][0]["point_index"], 0)
        self.assertEqual(body["model_receipts"][0]["output"], before)
        self.assertEqual(invalid, before)

    def test_generate_corrects_once_then_validates_before_writing(self):
        body, repair = after_plan()
        self.assertEqual(repair["status"], "model_required")
        self.assertEqual(repair["model_call"]["stage"], "replan_bound")
        context = repair["model_call"]["payload"]["plan_repair"]
        self.assertEqual(context["failed_plan"], bad_plan())
        self.assertEqual(context["attempt"], context["max_attempts"])
        body["model_receipts"].append(receipt(repair, PLAN))
        write = execute(body)
        self.assertEqual(write["model_call"]["stage"], "write_bound")
        body["model_receipts"].append(receipt(write, {"draft": DRAFT}))
        result = execute(body)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"], {"method": "bound", "plan": PLAN, "draft": DRAFT})
        self.assertEqual(execute(body), result)

    def test_second_invalid_plan_is_terminal_and_cannot_replan_again(self):
        body, repair = after_plan()
        body["model_receipts"].append(receipt(repair, bad_plan()))
        failed = execute(body)
        self.assertEqual(failed["error_code"], "plan_repair_exhausted")
        self.assertEqual(failed["plan_retry"], {"eligible": False, "attempted": 1, "limit": 1})
        self.assertNotIn("model_call", failed)
        self.assertEqual(execute(body), failed)

    def test_version_and_binding_errors_never_enter_plan_repair(self):
        version = bad_plan()
        version["points"][0]["support"][0]["version"] = "wrong-version"
        missing_binding = bad_plan()
        missing_binding["points"][0].pop("binding")
        # An earlier repairable error does not conceal a later hard version conflict.
        mixed = bad_plan()
        mixed["points"].append(copy.deepcopy(PLAN["points"][0]))
        mixed["points"][1]["support"][0]["version"] = "wrong-version"
        for invalid in (version, missing_binding, mixed, {"points": []}):
            with self.subTest(invalid=invalid):
                _, failed = after_plan(invalid)
                self.assertEqual(failed["error_code"], "method_input_invalid")
                self.assertNotIn("plan_retry", failed)

    def test_invalid_packet_is_rejected_before_any_model_call(self):
        body = request("generate", {"packet": {"question": "empty", "evidence": []}, "max_plan_retries": 1})
        self.assertEqual(execute(body)["error_code"], "method_input_invalid")

    def test_diagnostics_are_bounded_without_guessing_replacement_values(self):
        invalid = copy.deepcopy(PLAN)
        invalid["points"][0]["support"] = [{"evidence_id": "missing" + "x" * 1000, "segment_id": "S1"}] * 30
        _, step = after_plan(invalid)
        context = step["model_call"]["payload"]["plan_repair"]
        self.assertEqual(len(context["diagnostics"]), 8)
        self.assertGreater(context["diagnostics_omitted"], 0)
        self.assertLess(len(json.dumps(context["diagnostics"]).encode()), 6000)
        self.assertEqual(context["failed_plan"], invalid)
        self.assertTrue(all(set(d) <= {"code", "point_index", "field", "reference_index", "provided", "message"}
                            for d in context["diagnostics"]))

    def test_invalid_analysis_id_pointer_and_reference_structure_are_classified(self):
        planned = {"points": [{"analysis_ids": ["A1"], "binding": {},
                               "analysis_refs": [{"analysis_id": "A1", "output_path": "/missing/count"}]}]}
        _, pointer = after_plan(planned, packet=ANALYSIS)
        self.assertEqual(pointer["model_call"]["payload"]["plan_repair"]["diagnostics"][0]["code"], "analysis_path_invalid")
        planned["points"][0]["analysis_refs"][0]["analysis_id"] = "unknown"
        _, unknown = after_plan(planned, packet=ANALYSIS)
        self.assertEqual(unknown["model_call"]["payload"]["plan_repair"]["diagnostics"][0]["code"], "analysis_id_unknown")
        for support in (None, [None], [{"segment_id": "S1"}]):
            invalid = copy.deepcopy(PLAN)
            invalid["points"][0]["support"] = support
            self.assertEqual(after_plan(invalid)[1]["model_call"]["stage"], "replan_bound")

    def test_existing_operation_arguments_and_material_cannot_change_on_resume(self):
        body, repair = after_plan()
        for change in ("retries", "material", "id"):
            modified = copy.deepcopy(body)
            if change == "retries":
                modified["arguments"]["max_plan_retries"] = 0
            elif change == "material":
                modified["arguments"]["packet"]["evidence"][0]["text"] += " another source"
            else:
                modified["operation_id"] += "-escape"
            self.assertEqual(execute(modified)["error_code"], "receipt_conflict")
        unknown = copy.deepcopy(body)
        unknown["model_receipts"].append({"call_id": repair["model_call"]["call_id"],
            "request_hash": repair["model_call"]["request_hash"], "output": {}, "status": "unknown"})
        self.assertEqual(execute(unknown)["error_code"], "invalid_receipt")
        self.assertEqual(execute(body), repair)

    def test_verify_missing_claim_verdict_is_not_a_plan_retry(self):
        raw = copy.deepcopy(VERIFY)
        raw["reviews"][0]["claims"][0].pop("verdict")
        body = request("verify", {"packet": PACKET, "draft": DRAFT})
        step = execute(body)
        body.update(provider_version=step["provider_version"], model_receipts=[receipt(step, raw)])
        failed = execute(body)
        self.assertEqual(failed["error_code"], "verification_contract_invalid")
        self.assertNotIn("plan_retry", failed)

    def test_plain_plan_and_local_generate_use_same_single_retry_contract(self):
        stages = []
        def call(stage, instruction, payload):
            stages.append(stage)
            if stage == "plan_plain":
                return {"points": [{"evidence_ids": ["missing"]}]}
            if stage == "replan_plain":
                return {"points": [{"evidence_ids": ["E1"]}]}
            return {"draft": DRAFT}
        self.assertEqual(core.generate(PACKET, call, "plain", max_plan_retries=1)["draft"], DRAFT)
        self.assertEqual(stages, ["plan_plain", "replan_plain", "write_plain"])
        for value in (True, -1, 2, "1"):
            body = request("generate", {"packet": PACKET, "max_plan_retries": value})
            self.assertEqual(execute(body)["error_code"], "invalid_arguments")
        caps = capabilities()
        self.assertIn("bounded_plan_repair", caps["features"])
        self.assertEqual(caps["plan_retry_policy"]["maximum"], 1)


if __name__ == "__main__":
    unittest.main()
