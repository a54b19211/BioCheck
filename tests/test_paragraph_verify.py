"""Transport regression for paragraph mode, now batching independently cited claims."""
import copy
import unittest

from app import core
from app.rpc import execute, capabilities

PACKET = {"question": "Compare A and B.", "evidence": [
    {"id": "E1", "version": "v1", "title": "Study identity",
     "text": "Source records A. Source records B."}]}
DRAFT = "Source records A[E1:S1].\n\nSource records B[E1:S2]."


def answer(payload, verdict="supported"):
    references = {r["reference"]: r for r in payload["references"]}
    return {"checks": [{"claim_id": c["claim_id"],
        "verdict": {"status": verdict, "kind": "fact", "source_use": "current_fact", "labels": []},
        "quotes": [{"reference": key, "quote": references[key]["text"].strip()} for key in c["references"]],
        "reason": "Authored transport example."} for c in payload["claims"]]}


def coverage(payload, verdict="complete"):
    return {"checked_unit_ids": payload["checked_unit_ids"],
            "coverage": {"verdict": verdict, "reason": "Authored full-draft coverage."}}


def request(draft=DRAFT):
    return {"schema_version": "bioradar.professional.v1", "operation": "verify",
            "operation_id": "paragraph-offline", "arguments": {
                "packet": copy.deepcopy(PACKET), "draft": draft, "mode": "paragraph"},
            "model_receipts": []}


def accept(body, step, output, finish_reason="stop"):
    call = step["model_call"]
    body["provider_version"] = step["provider_version"]
    body["model_receipts"].append({"call_id": call["call_id"], "request_hash": call["request_hash"],
                                   "finish_reason": finish_reason, "output": copy.deepcopy(output)})


class ParagraphVerifyTests(unittest.TestCase):
    def test_rpc_resumes_exact_step_and_binds_previous_receipts_and_whole_draft(self):
        body = request()
        first = execute(body)
        self.assertEqual(first["model_call"]["stage"], "verify_claim")
        self.assertEqual(first, execute(body))
        self.assertEqual(first["model_call"]["max_output_tokens"], 12288)
        accept(body, first, answer(first["model_call"]["payload"]))
        second = execute(body)
        accept(body, second, answer(second["model_call"]["payload"]))
        third = execute(body)
        self.assertEqual(third["model_call"]["stage"], "verify_coverage")
        changed = copy.deepcopy(body)
        changed["model_receipts"][0]["output"]["checks"][0]["verdict"]["status"] = "contradicted"
        self.assertEqual(execute(changed)["error_code"], "receipt_conflict")
        changed = copy.deepcopy(body)
        changed["arguments"]["draft"] += " "
        self.assertEqual(execute(changed)["error_code"], "receipt_conflict")
        accept(body, third, coverage(third["model_call"]["payload"]))
        done = execute(body)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["result"]["status"], "PASS")
        self.assertEqual(done, execute(body))

    def test_length_retries_current_batch_once_with_a_new_id(self):
        body = request()
        first = execute(body)
        accept(body, first, "truncated public output", "length")
        retry = execute(body)
        self.assertEqual(retry["model_call"]["stage"], "verify_claim_retry")
        self.assertNotEqual(first["model_call"]["call_id"], retry["model_call"]["call_id"])
        self.assertEqual(first["model_call"]["payload"]["claims"], retry["model_call"]["payload"]["claims"])
        self.assertEqual(retry["model_call"]["max_output_tokens"], 16384)
        self.assertEqual(retry, execute(body))
        accept(body, retry, None, "length")
        failed = execute(body)
        self.assertEqual(failed["error_code"], "model_output_truncated")
        self.assertEqual(failed["claim_ids"], [c["claim_id"] for c in first["model_call"]["payload"]["claims"]])

    def test_unknown_finish_and_bad_completed_objects_do_not_retry(self):
        for finish, output in ((None, {}), ("unknown", {}), ("stop", None), ("stop", [])):
            body = request()
            first = execute(body)
            accept(body, first, output, finish)
            self.assertEqual(execute(body)["error_code"], "invalid_receipt")
        body = request()
        first = execute(body)
        accept(body, first, answer(first["model_call"]["payload"]))
        del body["model_receipts"][0]["finish_reason"]
        self.assertEqual(execute(body)["error_code"], "invalid_receipt")

    def test_malformed_completed_batch_stays_review_without_format_retry(self):
        body = request()
        first = execute(body)
        accept(body, first, {"checks": []})
        second = execute(body)
        self.assertEqual(second["model_call"]["stage"], "verify_claim")
        accept(body, second, answer(second["model_call"]["payload"]))
        completed = execute(body)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["result"]["status"], "REVIEW")
        self.assertTrue(completed["result"]["verification_errors"])

    def test_more_than_eight_paragraphs_preserve_coverage_without_legacy_cap_change(self):
        body = request("\n\n".join(["Source records A[E1:S1]."] * 9))
        for _ in range(10):
            step = execute(body)
            self.assertEqual(step["status"], "model_required")
            payload = step["model_call"]["payload"]
            out = coverage(payload) if step["model_call"]["stage"] == "verify_coverage" else answer(payload)
            accept(body, step, out)
        self.assertEqual(execute(body)["result"]["status"], "PASS")
        self.assertEqual(capabilities()["max_model_calls_per_operation"], 8)

    def test_whole_coverage_omission_or_truncation_cannot_pass(self):
        for fault in ("missing", "length"):
            body = request()
            for _ in range(2):
                step = execute(body)
                accept(body, step, answer(step["model_call"]["payload"]))
            step = execute(body)
            out = coverage(step["model_call"]["payload"])
            out["checked_unit_ids"] = ["U1"]
            accept(body, step, out if fault == "missing" else None, "stop" if fault == "missing" else "length")
            failed = execute(body)
            self.assertEqual(failed["status"], "failed")

    def test_paragraph_mode_does_not_enable_whole_report_format_retry(self):
        with self.assertRaises(ValueError):
            core.verify(PACKET, DRAFT, lambda *a: self.fail("No model call."),
                        mode="paragraph", max_verify_retries=1)

    def test_diagnostic_sentence_boundaries_keep_chinese_english_and_tail(self):
        for text, count in [("甲。乙。", 2), ("A. B.", 2), ("A without punctuation", 1), ("甲。乙", 2)]:
            values = core._draft_sentences(text)
            self.assertEqual(len(values), count)
            self.assertEqual(values[-1]["end"], len(text))


class AuditIdentityContextTests(unittest.TestCase):
    def test_context_quote_cannot_replace_selected_passage(self):
        packet = {"question": "Describe A.", "evidence": [{
            "id": "E1", "version": "v1", "title": "Laboratory L cohort",
            "text": "Laboratory L, day T.\n\nSource records A. A separate observation concerns B.\n\nPopulation P."}]}
        draft = "Source records A[E1]."
        raw = {"reviews": [{"unit_id": "U1", "verdict": "supported", "reason": "Offline.",
               "claims": [{"span": draft, "kind": "fact", "verdict": "supported", "reason": "Offline.",
                           "evidence": [{"evidence_id": "E1", "segment_id": "S3"}]}],
               "issues": [], "numeric_resolutions": []}],
               "coverage": {"verdict": "complete", "reason": "Offline."}}
        review = core.verify(packet, draft, lambda *a: raw)
        def call(stage, instruction, payload):
            item = payload["claims"][0]
            context = item["source_context"][0]
            self.assertEqual(context["title"], "Laboratory L cohort")
            self.assertTrue(any(p["text"] == "Source records A. A separate observation concerns B."
                                for p in context["paragraphs"]))
            for p in context["paragraphs"]:
                self.assertEqual(packet["evidence"][0]["text"][p["start"]:p["end"]], p["text"])
            return {"checks": [{"unit_id": "U1", "claim_index": 0, "verdict": "supported",
                    "reason": "Context is not selected proof.", "support": [
                        {"evidence_id": "E1", "segment_id": "S3", "quote": "A separate observation concerns B."}]}]}
        result = core.audit_support(packet, review, call)
        self.assertEqual(result["status"], "REVIEW")
        self.assertTrue(result["verification_errors"])


if __name__ == "__main__":
    unittest.main()
