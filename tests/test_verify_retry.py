"""Output-structure repair must preserve the original report and valid judgments."""
import copy
import unittest

from app import core
from app.rpc import execute, capabilities
from test_service import PACKET, DRAFT, VERIFY, request, receipt


def incomplete():
    result = copy.deepcopy(VERIFY)
    result["reviews"][0]["claims"][0].pop("verdict")
    return result


def after_verify(raw=None, *, retries=1, packet=PACKET):
    body = request("verify", {"packet": packet, "draft": DRAFT, "max_verify_retries": retries},
                   operation_id="bounded-verify")
    first = execute(body)
    body.update(provider_version=first["provider_version"],
                model_receipts=[receipt(first, incomplete() if raw is None else raw)])
    return body, execute(body)


class VerifyRetryTests(unittest.TestCase):
    def test_default_zero_returns_missing_field_path_without_filling_verdict(self):
        body, failed = after_verify(retries=0)
        self.assertEqual(failed["error_code"], "verification_contract_invalid")
        self.assertEqual(failed["diagnostics"][0]["path"], "/reviews/0/claims/0/verdict")
        self.assertTrue(failed["verify_retry"]["eligible"])
        self.assertNotIn("verdict", body["model_receipts"][0]["output"]["reviews"][0]["claims"][0])

    def test_one_explicit_repair_uses_same_draft_and_all_original_evidence(self):
        body, step = after_verify()
        self.assertEqual(step["model_call"]["stage"], "verify_repair")
        payload = step["model_call"]["payload"]
        self.assertEqual(payload["draft"], DRAFT)
        self.assertEqual(payload["verify_repair"]["failed_output"], incomplete())
        body["model_receipts"].append(receipt(step, VERIFY))
        result = execute(body)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["status"], "PASS")
        self.assertEqual(result["result"]["raw_response"], VERIFY)
        self.assertEqual(result["result"]["draft"], DRAFT)
        self.assertEqual(execute(body), result)

    def test_valid_non_support_and_coverage_gaps_are_not_retried(self):
        for where, value in (("claim", "insufficient"), ("claim", "contradicted"),
                             ("coverage", "incomplete"), ("coverage", "uncertain")):
            raw = copy.deepcopy(VERIFY)
            if where == "claim":
                raw["reviews"][0]["claims"][0]["verdict"] = value
            else:
                raw["coverage"]["verdict"] = value
            _, result = after_verify(raw)
            self.assertEqual(result["status"], "completed")
            self.assertIn(result["result"]["status"], {"REVIEW", "FAIL"})
            self.assertNotIn("model_call", result)

    def test_mixed_structure_error_and_existing_finding_block_repair(self):
        for mode in ("contradicted", "insufficient", "unsupported_alias", "issue", "coverage"):
            raw = incomplete()
            if mode in {"contradicted", "insufficient"}:
                raw["reviews"][0]["verdict"] = mode
            elif mode == "unsupported_alias":
                raw["reviews"][0]["claims"][0]["verdict"] = "unsupported"
            elif mode == "issue":
                raw["reviews"][0]["issues"] = [{"span": DRAFT, "labels": ["REF"], "reason": "已指出问题"}]
            else:
                raw["coverage"]["verdict"] = "incomplete"
            _, failed = after_verify(raw)
            self.assertEqual(failed["error_code"], "verification_contract_invalid")
            self.assertFalse(failed["verify_retry"]["eligible"])
            self.assertNotIn("model_call", failed)

    def test_negative_enum_spellings_and_containers_cannot_be_repaired_to_pass(self):
        for field, value in (("claim", "insufficient "), ("claim", " CONTRADICTED\n"),
                             ("coverage", "incomplete\n"), ("coverage", " UNCERTAIN "),
                             ("claim", ["insufficient"]), ("claim", {"value": "contradicted"}),
                             ("claim", "not-supported"), ("claim", {"unsupported": True})):
            raw = copy.deepcopy(VERIFY)
            if field == "claim":
                raw["reviews"][0]["claims"][0]["verdict"] = value
            else:
                raw["coverage"]["verdict"] = value
            _, result = after_verify(raw)
            self.assertEqual(result["error_code"], "verification_contract_invalid")
            self.assertFalse(result["verify_retry"]["eligible"])
            self.assertNotIn("model_call", result)

    def test_wrong_nonempty_containers_cannot_erase_existing_content_or_ids(self):
        for where in ("reviews", "claims", "claim", "coverage"):
            raw = copy.deepcopy(VERIFY)
            if where == "reviews":
                raw["reviews"] = raw["reviews"][0]
                raw["reviews"]["unit_id"] = "U999"
            elif where == "claims":
                raw["reviews"][0]["claims"] = raw["reviews"][0]["claims"][0]
            elif where == "claim":
                raw["reviews"][0]["claims"][0] = "a statement with existing content"
            else:
                raw["coverage"] = "a statement with existing scope"
            _, failed = after_verify(raw)
            self.assertEqual(failed["error_code"], "verification_contract_invalid")
            self.assertFalse(failed["verify_retry"]["eligible"])
            self.assertNotIn("model_call", failed)

    def test_source_use_and_position_review_never_become_format_retries(self):
        packet = copy.deepcopy(PACKET)
        packet["evidence"][0].update(source_kind="social_media", verification_status="unverified")
        _, unconfirmed = after_verify(VERIFY, packet=packet)
        self.assertEqual(unconfirmed["status"], "completed")
        self.assertEqual(unconfirmed["result"]["status"], "REVIEW")
        raw = copy.deepcopy(VERIFY)
        raw["reviews"][0]["claims"][0]["evidence"][0]["segment_id"] = "S999"
        _, invalid_position = after_verify(raw)
        self.assertEqual(invalid_position["status"], "completed")
        self.assertTrue(invalid_position["result"]["verification_errors"])
        self.assertNotIn("model_call", invalid_position)

    def test_second_bad_structure_is_terminal(self):
        body, step = after_verify()
        body["model_receipts"].append(receipt(step, incomplete()))
        failed = execute(body)
        self.assertEqual(failed["error_code"], "verification_repair_exhausted")
        self.assertEqual(failed["verify_retry"], {"eligible": False, "attempted": 1, "limit": 1})
        self.assertNotIn("model_call", failed)

    def test_structure_repair_cannot_rewrite_existing_claims_references_or_reasons(self):
        body, step = after_verify()
        for field in ("span", "reason", "evidence", "source_use"):
            changed = copy.deepcopy(VERIFY)
            claim = changed["reviews"][0]["claims"][0]
            claim[field] = [] if field == "evidence" else "changed"
            altered = copy.deepcopy(body)
            altered["model_receipts"].append(receipt(step, changed))
            self.assertEqual(execute(altered)["error_code"], "method_input_invalid")

    def test_filled_verdict_can_be_negative_and_is_not_promoted_to_pass(self):
        body, step = after_verify()
        corrected = copy.deepcopy(VERIFY)
        corrected["reviews"][0]["claims"][0]["verdict"] = "insufficient"
        body["model_receipts"].append(receipt(step, corrected))
        result = execute(body)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["status"], "REVIEW")

    def test_unmatched_units_and_packet_failures_do_not_qualify(self):
        raw = incomplete()
        raw["reviews"][0]["unit_id"] = "U999"
        _, failed = after_verify(raw)
        self.assertFalse(failed["verify_retry"]["eligible"])
        invalid = request("verify", {"packet": {"question": "x", "evidence": []},
                                     "draft": DRAFT, "max_verify_retries": 1})
        self.assertEqual(execute(invalid)["error_code"], "method_input_invalid")

    def test_diagnostics_are_bounded_and_all_missing_fields_remain_explicit(self):
        raw = incomplete()
        raw["reviews"][0]["claims"] *= 17
        body, step = after_verify(raw)
        repair = step["model_call"]["payload"]["verify_repair"]
        self.assertEqual(len(repair["diagnostics"]), 8)
        self.assertEqual(repair["diagnostics_omitted"], 9)
        self.assertTrue(all("verdict" not in c for c in repair["failed_output"]["reviews"][0]["claims"]))

    def test_same_operation_preserves_input_and_receipts_during_recovery(self):
        body, step = after_verify()
        self.assertEqual(execute(body), step)  # No new receipt means no new logical call.
        for field in ("draft", "max_verify_retries", "packet"):
            changed = copy.deepcopy(body)
            if field == "draft":
                changed["arguments"][field] += " amended"
            elif field == "max_verify_retries":
                changed["arguments"][field] = 0
            else:
                changed["arguments"][field]["evidence"][0]["version"] = "new"
            self.assertEqual(execute(changed)["error_code"], "receipt_conflict")

    def test_local_verify_and_capability_are_explicit(self):
        stages = []
        def call(stage, instruction, payload):
            stages.append(stage)
            return incomplete() if stage == "verify" else copy.deepcopy(VERIFY)
        self.assertEqual(core.verify(PACKET, DRAFT, call, max_verify_retries=1)["status"], "PASS")
        self.assertEqual(stages, ["verify", "verify_repair"])
        self.assertEqual(capabilities()["verify_retry_policy"]["maximum"], 1)
        for value in (True, 2, "1"):
            body = request("verify", {"packet": PACKET, "draft": DRAFT, "max_verify_retries": value})
            self.assertEqual(execute(body)["error_code"], "invalid_arguments")


if __name__ == "__main__":
    unittest.main()
