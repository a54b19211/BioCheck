"""HTTP tests use authored model outputs; no commercial model is called."""
import copy
import json
import threading
import unittest
import urllib.error
import urllib.request

from app import core
from app.rpc import PROVIDER_VERSION, SCHEMA_VERSION, execute
from app.service import MAX_REQUEST_BYTES, make_server

TOKEN = "test-only-service-token-32-characters"
PACKET = {"question": "概括这份材料", "evidence": [
    {"id": "E1", "version": "v1", "text": "药物A作用于靶点T。"}]}
DRAFT = "药物A作用于靶点T[E1]。"
PLAN = {"points": [{"intent": "说明材料中的作用", "evidence_ids": ["E1"], "conditions": [],
                    "binding": {"study": "本次材料"},
                    "support": [{"evidence_id": "E1", "segment_id": "S1"}]}]}
VERIFY = {"reviews": [{"unit_id": "U1", "verdict": "supported", "reason": "离线接口例",
                       "claims": [{"span": DRAFT, "kind": "fact", "verdict": "supported",
                                   "reason": "固定材料支持", "evidence": [{"evidence_id": "E1", "segment_id": "S1"}]}],
                       "issues": [], "numeric_resolutions": []}],
          "coverage": {"verdict": "complete", "reason": "本例覆盖"}}


def request(operation, arguments, *, operation_id="http-example"):
    return {"schema_version": SCHEMA_VERSION, "operation_id": operation_id,
            "operation": operation, "provider_version": None,
            "arguments": copy.deepcopy(arguments), "model_receipts": []}


def receipt(step, output):
    call = step["model_call"]
    return {"call_id": call["call_id"], "request_hash": call["request_hash"],
            "output": copy.deepcopy(output)}


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.start_server()

    def start_server(self):
        self.server = make_server(("127.0.0.1", 0), TOKEN)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def tearDown(self):
        self.stop_server()

    def http(self, path, body=None, *, token=TOKEN, raw=None, headers=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = {"Content-Type": "application/json"}
        if token is not None:
            h["Authorization"] = "Bearer " + token
        h.update(headers or {})
        req = urllib.request.Request(self.url + path, data=data, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def step(self, body):
        status, result = self.http("/v1/execute", body)
        self.assertEqual(status, 200)
        return result

    def test_health_capabilities_and_authorization(self):
        for path in ("/health", "/v1/capabilities"):
            self.assertEqual(self.http(path, token=None)[0], 401)
            code, result = self.http(path)
            self.assertEqual(code, 200)
            self.assertEqual(result["provider"], "biocheck")
            self.assertEqual(result["provider_version"], PROVIDER_VERSION)
        operations = self.http("/v1/capabilities")[1]["operations"]
        self.assertEqual(set(operations), {"plan", "write", "generate", "verify", "audit_support",
                                           "quality_feedback", "revise"})
        self.assertEqual(self.http("/v1/execute", {}, token="incorrect")[0], 401)

    def test_generate_two_callbacks_and_restart_replay_match_local(self):
        body = request("generate", {"packet": PACKET, "method": "bound"})
        first = self.step(body)
        self.assertEqual(first["status"], "model_required")
        self.assertEqual(first["model_call"]["stage"], "plan_bound")
        self.assertEqual(self.step(body), first)  # Lost HTTP response can be requested again.
        body["provider_version"] = first["provider_version"]
        body["model_receipts"].append(receipt(first, PLAN))
        self.stop_server()
        self.start_server()
        second = self.step(body)
        self.assertEqual(second["model_call"]["stage"], "write_bound")
        self.assertNotEqual(second["model_call"]["call_id"], first["model_call"]["call_id"])
        body["model_receipts"].append(receipt(second, {"draft": DRAFT}))
        completed = self.step(body)
        self.assertEqual(completed["status"], "completed")
        local_calls = []
        def local_call(stage, instruction, payload):
            local_calls.append({"stage": stage, "instruction": instruction, "payload": payload})
            return copy.deepcopy(PLAN if stage == "plan_bound" else {"draft": DRAFT})
        self.assertEqual(completed["result"], core.generate(PACKET, local_call))
        for observed, expected in zip((first, second), local_calls):
            self.assertEqual({k: observed["model_call"][k] for k in expected}, expected)
        self.assertEqual(self.step(body), completed)

    def test_paragraph_receipts_cross_http_restart_and_only_retry_length(self):
        from test_paragraph_verify import request as paragraph_request, accept, answer, coverage
        body = paragraph_request()
        first = self.step(body)
        accept(body, first, answer(first["model_call"]["payload"]))
        second = self.step(body)
        accept(body, second, "truncated public JSON", "length")
        self.stop_server()
        self.start_server()
        retry = self.step(body)
        self.assertEqual(retry["model_call"]["stage"], "verify_claim_retry")
        self.assertEqual(self.step(body), retry)
        accept(body, retry, answer(retry["model_call"]["payload"]))
        final_check = self.step(body)
        self.assertEqual(final_check["model_call"]["max_output_tokens"], 8192)
        accept(body, final_check, coverage(final_check["model_call"]["payload"]))
        done = self.step(body)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["result"]["status"], "PASS")
        self.assertEqual(self.step(body), done)

    def test_plan_and_write_can_be_checkpointed_independently(self):
        body = request("plan", {"packet": PACKET})
        planned = self.step(body)
        body.update(provider_version=planned["provider_version"], model_receipts=[receipt(planned, PLAN)])
        saved = self.step(body)
        self.assertEqual(saved["result"], PLAN)
        written = request("write", {"packet": PACKET, "method": "bound", "plan": saved["result"]},
                          operation_id="separate-write")
        step = self.step(written)
        self.assertEqual(step["model_call"]["stage"], "write_bound")
        written.update(provider_version=step["provider_version"], model_receipts=[receipt(step, {"draft": DRAFT})])
        self.assertEqual(self.step(written)["result"]["draft"], DRAFT)
        self.assertEqual(saved["result"], PLAN)

    def test_verify_audit_feedback_and_noop_revision_are_separate(self):
        body = request("verify", {"packet": PACKET, "draft": DRAFT})
        step = self.step(body)
        self.assertEqual(step["model_call"]["stage"], "verify")
        body.update(provider_version=step["provider_version"], model_receipts=[receipt(step, VERIFY)])
        reviewed = self.step(body)["result"]
        self.assertEqual(reviewed, core.verify(PACKET, DRAFT, lambda *args: copy.deepcopy(VERIFY)))
        audit_body = request("audit_support", {"packet": PACKET, "review": reviewed, "targets": [["U1", 0]]})
        audit_step = self.step(audit_body)
        answer = {"checks": [{"unit_id": "U1", "claim_index": 0, "verdict": "supported",
                             "reason": "离线接口例", "support": [{"evidence_id": "E1", "segment_id": "S1",
                                                               "quote": "药物A作用于靶点T。"}]}]}
        audit_body.update(provider_version=audit_step["provider_version"],
                          model_receipts=[receipt(audit_step, answer)])
        audit = self.step(audit_body)["result"]
        self.assertEqual(audit, core.audit_support(PACKET, reviewed, lambda *args: copy.deepcopy(answer),
                                                  targets=[("U1", 0)]))
        feedback = self.step(request("quality_feedback", {"review": reviewed, "audit": audit}))
        self.assertEqual(feedback["status"], "completed")
        self.assertEqual(feedback["result"], core.quality_feedback(reviewed, audit))
        unchanged = self.step(request("revise", {"packet": PACKET, "review": reviewed, "audit": audit}))
        self.assertEqual(unchanged["status"], "completed")
        self.assertEqual(unchanged["result"]["changed_units"], [])
        self.assertEqual(unchanged["result"]["draft"], DRAFT)

    def test_revision_produces_unverified_candidate_without_overwriting_review(self):
        wrong = "药物A作用于靶点Z[E1]。"
        raw = copy.deepcopy(VERIFY)
        raw["reviews"][0].update(verdict="contradicted")
        raw["reviews"][0]["claims"][0].update(span=wrong, verdict="contradicted")
        review = core.verify(PACKET, wrong, lambda *args: raw)
        before = copy.deepcopy(review)
        body = request("revise", {"packet": PACKET, "review": review})
        step = self.step(body)
        self.assertEqual(step["model_call"]["stage"], "revise")
        output = {"replacements": [{"unit_id": "U1", "text": DRAFT}]}
        body.update(provider_version=step["provider_version"], model_receipts=[receipt(step, output)])
        result = self.step(body)
        self.assertEqual(result["result"]["draft"], DRAFT)
        self.assertEqual(review, before)
        self.assertNotIn("final_review", result["result"])
        self.assertNotIn("verification", result["result"])

    def test_source_correction_read_recheck_revision_uses_separate_http_receipts(self):
        from test_source_context import PACKET as corrected_packet, answer
        packet = copy.deepcopy(corrected_packet)
        old = "上半年订单8000万元，共40套[qOld]。"
        fixed = "更正后上半年订单6400万元，共32套[qUpdate]。"
        def finish(operation, arguments, output, suffix):
            body = request(operation, arguments, operation_id="correction-" + suffix)
            step = self.step(body)
            self.assertEqual(step["status"], "model_required")
            body.update(provider_version=step["provider_version"], model_receipts=[receipt(step, output)])
            result = self.step(body)
            self.assertEqual(result["status"], "completed")
            return result["result"]
        pending = finish("verify", {"packet": packet, "draft": old}, answer(old), "initial")
        self.assertEqual(pending["status"], "REVIEW")
        feedback = self.step(request("quality_feedback", {"review": pending}))["result"]
        self.assertEqual(feedback["evidence_gaps"][0]["read_requests"][0]["version"], "v2")
        refused = self.step(request("revise", {"packet": packet, "review": pending}))["result"]
        self.assertFalse(refused["changed_units"])
        # After the host has read the correction, a fresh model check can identify an actual contradiction.
        raw = answer(old, [{"evidence_id": "qUpdate", "segment_id": "S1"}], verdict="contradicted")
        checked = finish("verify", {"packet": packet, "draft": old}, raw, "after-read")
        revised = finish("revise", {"packet": packet, "review": checked},
                         {"replacements": [{"unit_id": "U1", "text": fixed}]}, "revision")
        self.assertTrue(revised["requires_verification"])
        self.assertNotIn("status", revised)
        final = finish("verify", {"packet": packet, "draft": revised["draft"]},
                       answer(fixed, [{"evidence_id": "qUpdate", "segment_id": "S1"}]), "final")
        self.assertEqual(final["status"], "PASS")
        self.assertEqual(pending["draft"], old)
        self.assertEqual(checked["draft"], old)
        features = self.http("/v1/capabilities")[1]["features"]
        self.assertIn("source_updates", features)

    def test_plan_repair_budget_pause_restart_and_exact_receipt_resume(self):
        from test_plan_retry import bad_plan
        body = request("generate", {"packet": PACKET, "max_plan_retries": 1}, operation_id="http-plan-repair")
        initial = self.step(body)
        body.update(provider_version=initial["provider_version"], model_receipts=[receipt(initial, bad_plan())])
        repair = self.step(body)
        self.assertEqual(repair["model_call"]["stage"], "replan_bound")
        # A host without budget keeps this request pending and sends no model call.
        self.assertEqual(self.step(body), repair)
        self.stop_server()
        self.start_server()
        self.assertEqual(self.step(body), repair)
        body["model_receipts"].append(receipt(repair, PLAN))
        write = self.step(body)
        self.assertEqual(write["model_call"]["stage"], "write_bound")
        self.assertEqual(self.step(body), write)
        body["model_receipts"].append(receipt(write, {"draft": DRAFT}))
        result = self.step(body)
        self.assertEqual(result["result"]["draft"], DRAFT)
        self.assertEqual(self.step(body), result)

    def test_verify_repair_preserves_original_receipt_across_restart(self):
        from test_verify_retry import incomplete
        body = request("verify", {"packet": PACKET, "draft": DRAFT, "max_verify_retries": 1},
                       operation_id="http-verify-repair")
        first = self.step(body)
        body.update(provider_version=first["provider_version"], model_receipts=[receipt(first, incomplete())])
        repair = self.step(body)
        self.assertEqual(repair["model_call"]["stage"], "verify_repair")
        self.assertEqual(self.step(body), repair)
        self.stop_server()
        self.start_server()
        self.assertEqual(self.step(body), repair)
        unknown = copy.deepcopy(body)
        unknown["model_receipts"].append(dict(receipt(repair, VERIFY), status="unknown"))
        self.assertEqual(self.step(unknown)["error_code"], "invalid_receipt")
        body["model_receipts"].append(receipt(repair, VERIFY))
        result = self.step(body)
        self.assertEqual(result["result"]["status"], "PASS")
        self.assertEqual(body["model_receipts"][0]["output"], incomplete())
        self.assertEqual(self.step(body), result)

    def test_analysis_only_json_result_matches_local_with_fixed_calculation_basis(self):
        basis = {"analysis_unit": "project", "unit": "项目", "period": "2026-10-30截点",
                 "population": "本窗口项目", "denominator": 4}
        packet = {"question": "本窗口有多少活跃项目？", "evidence": [], "assessment": {"analysis_results": [
            {"id": "A1", "dataset_ref": {"id": "fixture-window", "version": "v1"},
             "method_version": "count.v1", "parameters": {"status": "active"}, "output": {"count": 2},
             "input_refs": [{"source_record_id": "example:p1", "version": "v1"},
                            {"source_record_id": "example:p2", "version": "v1"}],
             "calculation_basis": basis, "limitations": ["离线接口例；局部窗口，不代表全行业"]}]}}
        draft = "本窗口有2个活跃项目[A1]。"
        output = {"reviews": [{"unit_id": "U1", "verdict": "supported", "reason": "接口例",
            "claims": [{"span": draft, "kind": "analysis_result", "verdict": "supported",
                        "reason": "输出值和口径对应", "binding": basis,
                        "analysis_refs": [{"analysis_id": "A1", "output_path": "/count"}], "evidence": []}],
            "issues": [], "numeric_resolutions": []}],
            "coverage": {"verdict": "complete", "reason": "仅本窗口"}}
        body = request("verify", {"packet": packet, "draft": draft}, operation_id="analysis-http")
        step = self.step(body)
        self.assertEqual(step["model_call"]["payload"]["assessment"], packet["assessment"])
        body.update(provider_version=step["provider_version"], model_receipts=[receipt(step, output)])
        result = self.step(body)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"], core.verify(packet, draft, lambda *args: copy.deepcopy(output)))
        claim = result["result"]["units"][0]["claims"][0]
        self.assertEqual(claim["analysis_refs"][0]["value"], 2)
        self.assertEqual(claim["analysis_refs"][0]["calculation_basis"], basis)

    def test_surrogate_operation_id_returns_json_failure_without_disconnect(self):
        body = request("quality_feedback", {"review": {}}, operation_id="\ud800")
        result = self.step(body)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "method_input_invalid")

    def test_transport_errors_are_explicit_and_no_payload_is_echoed(self):
        self.assertEqual(self.http("/missing")[0], 404)
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'broken'):
            code, result = self.http("/v1/execute", raw=raw)
            self.assertEqual(code, 400)
            self.assertEqual(result["error_code"], "invalid_json")
        self.assertEqual(self.http("/v1/execute", raw=b"{}", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.http("/v1/execute", raw=b"{}",
                                   headers={"Content-Length": str(MAX_REQUEST_BYTES + 1)})[0], 413)


class ReceiptTests(unittest.TestCase):
    def first(self):
        body = request("generate", {"packet": PACKET, "method": "bound"})
        step = execute(body)
        body.update(provider_version=step["provider_version"], model_receipts=[receipt(step, PLAN)])
        return body, step

    def test_changed_operation_identity_input_and_method_version_do_not_reuse_receipts(self):
        body, _ = self.first()
        for field, value in (("operation_id", "another-operation"), ("operation", "plan"),
                             ("provider_version", "different-version")):
            modified = copy.deepcopy(body);modified[field] = value
            self.assertEqual(execute(modified)["status"], "failed")
        modified = copy.deepcopy(body)
        modified["arguments"]["packet"]["evidence"][0]["version"] = "v2"
        self.assertEqual(execute(modified)["error_code"], "receipt_conflict")
        modified = copy.deepcopy(body);modified["provider_version"] = None
        self.assertEqual(execute(modified)["error_code"], "provider_version_required")

    def test_duplicate_out_of_order_extra_and_unknown_receipts_are_rejected(self):
        body, _ = self.first()
        second = execute(body)
        body["model_receipts"].append(receipt(second, {"draft": DRAFT}))
        self.assertEqual(execute(body)["status"], "completed")
        for receipts in (body["model_receipts"][::-1],
                         body["model_receipts"] + [body["model_receipts"][0]],
                         [body["model_receipts"][1]]):
            changed = copy.deepcopy(body);changed["model_receipts"] = receipts
            self.assertEqual(execute(changed)["status"], "failed")
        extra = request("quality_feedback", {"review": core.verify(PACKET, DRAFT, lambda *args: VERIFY)})
        extra.update(provider_version=PROVIDER_VERSION, model_receipts=body["model_receipts"][:1])
        self.assertEqual(execute(extra)["error_code"], "extra_receipts")
        for state in ("unknown", "failed", "not_sent"):
            changed, _ = self.first()
            changed["model_receipts"][0]["status"] = state
            self.assertEqual(execute(changed)["error_code"], "invalid_receipt")

    def test_hash_conflict_invalid_model_output_and_unknown_operations_fail(self):
        body, _ = self.first()
        body["model_receipts"][0]["request_hash"] = "0" * 64
        self.assertEqual(execute(body)["error_code"], "receipt_conflict")
        body, _ = self.first()
        body["model_receipts"][0]["output"] = {}
        self.assertEqual(execute(body)["error_code"], "method_input_invalid")
        self.assertEqual(execute(request("run", {"packet": PACKET}))["error_code"], "unsupported_operation")
        body = request("verify", {"packet": PACKET, "draft": DRAFT, "hidden_retry": True})
        self.assertEqual(execute(body)["error_code"], "invalid_arguments")


if __name__ == "__main__":
    unittest.main()
