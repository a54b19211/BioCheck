"""Regression of recorded output truncation, using synthetic public receipts only."""
import copy
import json
import unittest

from app import core
from app.rpc import execute, capabilities

PACKET = {"question": "Compare A and B.", "evidence": [
    {"id": "E1", "version": "v1", "title": "Study identity",
     "text": "Source records A. Source records B."}]}
DRAFT = "Source records A[E1].\n\nSource records B[E1]."


def answer(payload, verdict="supported"):
    unit = payload["units"][0]
    part = "S1" if unit["id"] == "U1" else "S2"
    return {"reviews": [{"unit_id": unit["id"], "verdict": verdict, "reason": "Synthetic.",
             "claims": [{"span": unit["text"], "kind": "fact", "verdict": verdict,
                         "reason": "Synthetic.", "evidence": [{"evidence_id": "E1", "segment_id": part}]}],
             "issues": [], "numeric_resolutions": []}],
            "coverage": {"verdict": "complete", "reason": "All facts in this paragraph considered."}}


def coverage(payload, verdict="complete"):
    return {"checked_unit_ids": payload["checked_unit_ids"],
            "coverage": {"verdict": verdict, "reason": "Synthetic full-draft coverage."}}


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
    def test_each_unit_then_whole_coverage_and_original_positions(self):
        calls = []
        def call(stage, instruction, payload):
            calls.append((stage, copy.deepcopy(payload)))
            output = coverage(payload) if stage == "verify_coverage" else answer(payload)
            return {"finish_reason": "stop", "output": output}
        review = core.verify(PACKET, DRAFT, call, mode="paragraph")
        self.assertEqual([s for s, _ in calls], ["verify_paragraph", "verify_paragraph", "verify_coverage"])
        self.assertEqual([p["units"][0]["id"] for s, p in calls[:-1]], ["U1", "U2"])
        self.assertEqual(review["status"], "PASS")
        self.assertEqual(review["draft"], DRAFT)
        self.assertEqual(review["units"][1]["start"], DRAFT.index("Source records B"))
        self.assertEqual(review["units"][1]["claims"][0]["sentence_numbers"], [1])
        self.assertEqual(review["units"][1]["claims"][0]["claim_index"], 0)
        self.assertTrue(all(c["finish_reason"] == "stop" for c in review["verification_execution"]))

    def test_only_explicitly_truncated_unit_is_retried_once(self):
        calls = []
        def call(stage, instruction, payload):
            calls.append((stage, copy.deepcopy(payload)))
            if stage == "verify_paragraph" and payload["units"][0]["id"] == "U2":
                return {"finish_reason": "length", "output": '{"reviews": ['}
            return {"finish_reason": "stop", "output": coverage(payload) if stage == "verify_coverage" else answer(payload)}
        review = core.verify(PACKET, DRAFT, call, mode="paragraph")
        self.assertEqual([s for s, _ in calls], ["verify_paragraph", "verify_paragraph",
                                               "verify_paragraph_retry", "verify_coverage"])
        self.assertEqual(calls[1][1]["units"], calls[2][1]["units"])
        self.assertEqual(len(calls[2][1]["units"]), 1)
        self.assertEqual(review["status"], "PASS")
        self.assertEqual([c["finish_reason"] for c in review["verification_execution"]],
                         ["stop", "length", "stop", "stop"])
        self.assertIn('{"reviews": [', str(review["raw_response"]))

    def test_unknown_completion_or_bad_stop_output_never_retries(self):
        for result in ({}, {"finish_reason": None, "output": {}},
                       {"finish_reason": "content_filter", "output": {}},
                       {"finish_reason": "stop", "output": "not JSON"}):
            with self.subTest(result=result):
                calls = []
                def call(*args):
                    calls.append(args[0])
                    return copy.deepcopy(result)
                with self.assertRaises(ValueError):
                    core.verify(PACKET, DRAFT, call, mode="paragraph")
                self.assertEqual(calls, ["verify_paragraph"])

    def test_second_length_stops_without_other_paragraph_or_coverage_call(self):
        calls = []
        def call(stage, instruction, payload):
            calls.append(stage)
            return {"finish_reason": "length", "output": None}
        with self.assertRaises(ValueError):
            core.verify(PACKET, DRAFT, call, mode="paragraph")
        self.assertEqual(calls, ["verify_paragraph", "verify_paragraph_retry"])

    def test_missing_wrong_or_duplicate_units_and_global_coverage_cannot_pass(self):
        for fault in ("wrong_unit", "missing_unit", "duplicate_unit", "coverage_omission"):
            with self.subTest(fault=fault):
                def call(stage, instruction, payload):
                    output = coverage(payload) if stage == "verify_coverage" else answer(payload)
                    if stage == "verify_coverage" and fault == "coverage_omission":
                        output["checked_unit_ids"] = ["U1"]
                    elif stage != "verify_coverage":
                        if fault == "wrong_unit":
                            output["reviews"][0]["unit_id"] = "U99"
                        elif fault == "missing_unit":
                            output["reviews"] = []
                        elif fault == "duplicate_unit":
                            output["reviews"] *= 2
                    return {"finish_reason": "stop", "output": output}
                with self.assertRaises(ValueError):
                    core.verify(PACKET, DRAFT, call, mode="paragraph")

    def test_local_or_global_incomplete_and_negative_findings_are_not_overridden(self):
        for fault in ("local_coverage", "global_coverage", "negative"):
            with self.subTest(fault=fault):
                def call(stage, instruction, payload):
                    out = coverage(payload) if stage == "verify_coverage" else answer(payload)
                    if fault == "global_coverage" and stage == "verify_coverage":
                        out["coverage"]["verdict"] = "incomplete"
                    if fault == "local_coverage" and stage == "verify_paragraph":
                        out["coverage"]["verdict"] = "uncertain"
                    if fault == "negative" and stage == "verify_paragraph":
                        out = answer(payload, "contradicted")
                        out["reviews"][0]["issues"] = [{
                            "span": payload["units"][0]["text"], "labels": ["OBJ", "REF"],
                            "reason": "Synthetic contradiction.", "evidence": [{"evidence_id": "E1", "segment_id": "S1"}]}]
                    return {"finish_reason": "stop", "output": out}
                result = core.verify(PACKET, DRAFT, call, mode="paragraph")
                self.assertNotEqual(result["status"], "PASS")
                if fault == "negative":
                    issue = result["units"][0]["issues"][0]
                    self.assertEqual(issue["labels"], ["OBJ", "REF"])
                    self.assertEqual(issue["sentence_numbers"], [1])
                    self.assertEqual(issue["issue_index"], 0)

    def test_paragraph_mode_cannot_request_a_whole_draft_format_retry(self):
        with self.assertRaises(ValueError):
            core.verify(PACKET, DRAFT, lambda *a: self.fail("No call."),
                        mode="paragraph", max_verify_retries=1)

    def test_rpc_resumes_exact_step_and_preserves_prior_receipt_binding(self):
        body = request()
        first = execute(body)
        self.assertEqual(first["status"], "model_required")
        self.assertEqual(first, execute(body))
        self.assertEqual(first["model_call"]["max_output_tokens"], 12288)
        accept(body, first, answer(first["model_call"]["payload"]))
        second = execute(body)
        accept(body, second, answer(second["model_call"]["payload"]))
        third = execute(body)
        self.assertEqual(third["model_call"]["stage"], "verify_coverage")
        changed = copy.deepcopy(body)
        changed["model_receipts"][0]["output"]["reviews"][0]["verdict"] = "contradicted"
        self.assertEqual(execute(changed)["error_code"], "receipt_conflict")
        accept(body, third, coverage(third["model_call"]["payload"]))
        done = execute(body)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done, execute(body))

    def test_rpc_length_has_new_id_and_second_length_is_terminal(self):
        body = request()
        first = execute(body)
        accept(body, first, None, "length")
        retry = execute(body)
        self.assertEqual(retry["model_call"]["stage"], "verify_paragraph_retry")
        self.assertNotEqual(first["model_call"]["call_id"], retry["model_call"]["call_id"])
        self.assertEqual(retry["model_call"]["max_output_tokens"], 16384)
        self.assertEqual(retry, execute(body))
        accept(body, retry, None, "length")
        failed = execute(body)
        self.assertEqual(failed["error_code"], "model_output_truncated")
        self.assertEqual(failed["unit_id"], "U1")
        self.assertEqual(failed["finish_reason"], "length")

    def test_rpc_requires_finish_reason_for_new_mode_only(self):
        body = request()
        first = execute(body)
        accept(body, first, answer(first["model_call"]["payload"]))
        del body["model_receipts"][0]["finish_reason"]
        self.assertEqual(execute(body)["error_code"], "invalid_receipt")

    def test_chinese_sentences_without_spaces_have_distinct_diagnostic_numbers(self):
        packet = {"question": "检查两句", "evidence": [
            {"id": "E1", "version": "v1", "text": "甲项成立。乙项尚无结论。"}]}
        draft = "甲项成立[E1]。乙项成立[E1]。"
        spans = ["甲项成立[E1]。", "乙项成立[E1]。"]
        def call(stage, instruction, payload):
            if stage == "verify_coverage":
                return {"finish_reason": "stop", "output": coverage(payload)}
            self.assertEqual(len(payload["units"][0]["sentences"]), 2)
            claims = [{"span": span, "kind": "fact", "verdict": "supported" if i == 0 else "insufficient",
                       "reason": "合成定位例", "evidence": [{"evidence_id": "E1", "segment_id": "S1"}]}
                      for i, span in enumerate(spans)]
            return {"finish_reason": "stop", "output": {
                "reviews": [{"unit_id": "U1", "verdict": "insufficient", "reason": "第二句待核",
                             "claims": claims, "numeric_resolutions": [],
                             "issues": [{"span": spans[1], "labels": ["STR"], "reason": "合成例",
                                         "evidence": [{"evidence_id": "E1", "segment_id": "S1"}]}]}],
                "coverage": {"verdict": "complete", "reason": "两句均检查"}}}
        result = core.verify(packet, draft, call, mode="paragraph")
        self.assertEqual([c["sentence_numbers"] for c in result["units"][0]["claims"]], [[1], [2]])
        self.assertEqual(result["units"][0]["issues"][0]["sentence_numbers"], [2])

    def test_sentence_diagnostics_keep_english_and_unpunctuated_tails(self):
        for text, count in [("A. B.", 2), ("A without terminal punctuation", 1), ("甲。乙", 2)]:
            with self.subTest(text=text):
                sentences = core._draft_sentences(text)
                self.assertEqual(len(sentences), count)
                self.assertEqual(sentences[-1]["end"], len(text))

    def test_global_request_does_not_copy_sources_or_raw_receipts(self):
        packet = copy.deepcopy(PACKET)
        sentinel = "SYNTHETIC_SOURCE_BODY_NOT_FOR_COVERAGE"
        packet["evidence"].append({"id": "E2", "version": "v1", "text": sentinel * 3000})
        sizes = {}
        def call(stage, instruction, payload):
            encoded = json.dumps(payload)
            sizes[stage] = len(encoded.encode())
            if stage == "verify_coverage":
                self.assertNotIn("evidence", payload)
                self.assertNotIn("assessment", payload)
                self.assertNotIn("raw_response", payload)
                self.assertNotIn(sentinel, encoded)
                self.assertEqual(payload["verification_request"]["max_output_tokens"], 8192)
            return {"finish_reason": "stop",
                    "output": coverage(payload) if stage == "verify_coverage" else answer(payload)}
        core.verify(packet, DRAFT, call, mode="paragraph")
        self.assertLess(sizes["verify_coverage"], sizes["verify_paragraph"] / 10)

    def test_rpc_bad_structure_does_not_advertise_format_retry(self):
        body = request()
        first = execute(body)
        accept(body, first, {"reviews": [], "coverage": {"verdict": "complete"}})
        failed = execute(body)
        self.assertEqual(failed["error_code"], "verification_contract_invalid")
        self.assertFalse(failed["verify_retry"]["eligible"])
        self.assertEqual(failed["diagnostics"][0]["unit_id"], "U1")

    def test_more_than_eight_paragraphs_complete_without_relaxing_legacy_cap(self):
        draft = "\n\n".join(["Source records A[E1]."] * 9)
        body = request(draft)
        for _ in range(10):
            step = execute(body)
            self.assertEqual(step["status"], "model_required")
            payload = step["model_call"]["payload"]
            out = coverage(payload) if step["model_call"]["stage"] == "verify_coverage" else answer(payload)
            if "reviews" in out:
                out["reviews"][0]["claims"][0]["evidence"][0]["segment_id"] = "S1"
            accept(body, step, out)
        self.assertEqual(execute(body)["result"]["status"], "PASS")
        self.assertEqual(capabilities()["max_model_calls_per_operation"], 8)


class AuditIdentityContextTests(unittest.TestCase):
    def example(self):
        packet = {"question": "Describe A.", "evidence": [{
            "id": "E1", "version": "v1", "title": "Laboratory L cohort",
            "text": "Laboratory L, day T.\n\nSource records A. A separate observation concerns B.\n\nPopulation P."}]}
        draft = "Source records A[E1]."
        payload = {"units": [{"id": "U1", "text": draft}]}
        raw = answer(payload)
        raw["reviews"][0]["claims"][0]["evidence"][0]["segment_id"] = "S3"
        review = core.verify(packet, draft, lambda *a: raw)
        self.assertEqual(review["status"], "PASS")
        return packet, review

    def test_containing_paragraph_and_identity_are_visible_but_not_selected_as_support(self):
        packet, review = self.example()
        def call(stage, instruction, payload):
            item = payload["claims"][0]
            context = item["source_context"][0]
            self.assertEqual(context["title"], "Laboratory L cohort")
            paragraphs = context["paragraphs"]
            self.assertTrue(any(p["text"] == "Source records A. A separate observation concerns B." for p in paragraphs))
            for p in paragraphs:
                self.assertEqual(packet["evidence"][0]["text"][p["start"]:p["end"]], p["text"])
            self.assertEqual(item["evidence"][0]["quote"], "Source records A.")
            return {"checks": [{"unit_id": "U1", "claim_index": 0, "verdict": "supported",
                    "reason": "Context cannot stand in for selected original.",
                    "support": [{"evidence_id": "E1", "segment_id": "S3",
                                 "quote": "A separate observation concerns B."}]}]}
        audit = core.audit_support(packet, review, call)
        self.assertEqual(audit["status"], "REVIEW")
        self.assertTrue(audit["verification_errors"])


if __name__ == "__main__":
    unittest.main()
