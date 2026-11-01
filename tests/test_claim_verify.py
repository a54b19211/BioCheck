"""Claim-local citations and selective rechecking; all outputs are authored test receipts."""
import copy
import hashlib
import json
import unittest

from app import core
from app.rpc import execute


def packet():
    text = "Alpha is present. Beta is present. Gamma is present. Delta is present. Epsilon is present."
    return {"question": "Describe the observations.", "evidence": [{
        "id": "E1", "record_id": "doc", "version": "v1", "text": text, "title": "Fixed observations",
        "source_context": {"adopted_text_level": "selected_passages", "source_window": {
            "start": 10, "end": 10 + len(text), "source_length": 1000,
            "adopted_text_length": len(text), "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "hash_scope": "adopted_packet_text"}}}]}


DRAFT = "Alpha is present[E1:S1]. Beta is present[E1:S2]. Gamma is present[E1:S3]. Delta is present[E1:S4]. Epsilon is present[E1:S5]."


class Calls:
    def __init__(self, change=None):
        self.calls = []
        self.change = change

    def __call__(self, stage, instruction, payload):
        self.calls.append((stage, copy.deepcopy(payload)))
        if stage == "verify_coverage":
            return {"finish_reason": "stop", "output": {
                "checked_unit_ids": payload["checked_unit_ids"],
                "coverage": {"verdict": "complete", "reason": "Authored task coverage."}}}
        refs = {r["reference"]: r for r in payload["references"]}
        out = {"checks": [{"claim_id": c["claim_id"],
            "verdict": {"status": "supported", "kind": "fact", "source_use": "current_fact", "labels": []},
            "quotes": [{"reference": key, "quote": refs[key]["text"].strip()}
                       for key in c["references"]], "reason": "Authored support."} for c in payload["claims"]]}
        if self.change:
            self.change(out, payload)
        return {"finish_reason": "stop", "output": out}


class ClaimVerifyTests(unittest.TestCase):
    def run_review(self, draft=DRAFT, **kwargs):
        caller = kwargs.pop("caller", Calls())
        return core.verify(packet(), draft, caller, mode="paragraph", **kwargs), caller

    def test_batch_only_contains_cited_ranges_not_other_sources(self):
        p = packet()
        p["evidence"].append({"id": "OTHER", "version": "v1", "text": "UNRELATED_SECRET_BODY"})
        calls = Calls()
        result = core.verify(p, DRAFT, calls, mode="paragraph")
        self.assertEqual(result["status"], "PASS")
        batches = [payload for stage, payload in calls.calls if stage == "verify_claim"]
        self.assertEqual([len(b["claims"]) for b in batches], [3, 2])
        self.assertNotIn("UNRELATED_SECRET_BODY", str(calls.calls))
        self.assertEqual([c["claim_index"] for c in result["units"][0]["claims"]], list(range(5)))

    def test_fake_quote_wrong_segment_and_wrong_version_stay_review(self):
        def fake(out, _):
            out["checks"][0]["quotes"][0]["quote"] = "Invented quotation."
        def other_segment(out, _):
            out["checks"][0]["quotes"][0]["quote"] = "Beta is present."
        def other_reference(out, _):
            out["checks"][0]["quotes"][0]["reference"] = "E1:S2"
        def wrong_version(out, _):
            out["checks"][0]["quotes"][0]["version"] = "v2"
        for change in (fake, other_segment, other_reference, wrong_version):
            with self.subTest(change=change.__name__):
                result, _ = self.run_review(caller=Calls(change))
                self.assertEqual(result["status"], "REVIEW")
                self.assertTrue(result["verification_errors"])
                self.assertEqual(core.revise(packet(), result, lambda *a: self.fail("No rewrite."))["changed_units"], [])

    def test_missing_sentence_citation_cannot_borrow_next_sentence(self):
        result, calls = self.run_review("Alpha is present. Beta is present[E1:S2].")
        self.assertEqual(result["status"], "REVIEW")
        sent = [c["text"] for stage, p in calls.calls if stage == "verify_claim" for c in p["claims"]]
        self.assertNotIn("Alpha is present.", sent)

    def test_same_complete_inputs_reuse_all_checks_without_any_callback(self):
        first, _ = self.run_review()
        before = copy.deepcopy(first)
        second = core.verify(packet(), DRAFT, lambda *a: self.fail("No model calls."),
                             mode="paragraph", previous_review=first)
        self.assertEqual(second["status"], "PASS")
        self.assertEqual(len(second["reused_claims"]), 5)
        self.assertEqual(first, before)

    def test_changed_sentence_invalidates_only_itself_and_adjacent_context(self):
        first, _ = self.run_review()
        revised = DRAFT.replace("Gamma is present", "Gamma remains present")
        second, calls = self.run_review(revised, previous_review=first)
        checked = [c["text"] for stage, p in calls.calls if stage == "verify_claim" for c in p["claims"]]
        self.assertEqual(len(checked), 3)
        self.assertTrue(all(any(word in c for word in ("Beta", "Gamma", "Delta")) for c in checked))
        self.assertEqual(len(second["reused_claims"]), 2)
        for c in second["units"][0]["claims"]:
            self.assertEqual(second["units"][0]["text"][c["span_start"]:c["span_end"]], c["span"])

    def test_task_scope_source_text_and_title_changes_invalidate_reuse(self):
        first, _ = self.run_review()
        for what in ("question", "scope", "source", "title"):
            with self.subTest(what=what):
                p = packet()
                if what == "question":
                    p["question"] += " Include patents."
                elif what == "scope":
                    p["assessment"] = {"research_scope": {"patents": "unread"}}
                elif what == "title":
                    p["evidence"][0]["title"] = "Different laboratory"
                else:
                    p["evidence"][0]["version"] = "v2"
                calls = Calls()
                result = core.verify(p, DRAFT, calls, mode="paragraph", previous_review=first)
                self.assertEqual(result["reused_claims"], [])
                self.assertTrue(any(s == "verify_coverage" for s, _ in calls.calls))

    def test_no_fulltext_fallback_for_unknown_segment_or_undeclared_window(self):
        for reference in ("E1:S999", "E1"):
            p = packet()
            p["evidence"][0].pop("source_context")
            calls = Calls()
            result = core.verify(p, "Alpha is present[" + reference + "].", calls, mode="paragraph")
            self.assertIn(result["status"], {"REVIEW", "FAIL"})
            self.assertFalse(any(s == "verify_claim" for s, _ in calls.calls))

    def test_record_citation_is_limited_to_declared_window_and_ambiguous_quote_is_review(self):
        result, _ = self.run_review("Alpha is present[E1].")
        self.assertEqual(result["status"], "PASS")
        p = packet()
        p["evidence"][0]["text"] = "Repeated. Repeated."
        p["evidence"][0]["source_context"]["source_window"].update(
            end=10+len(p["evidence"][0]["text"]), adopted_text_length=len(p["evidence"][0]["text"]),
            text_sha256=hashlib.sha256(p["evidence"][0]["text"].encode()).hexdigest())
        def repeat(out, _):
            out["checks"][0]["quotes"][0]["quote"] = "Repeated."
        result = core.verify(p, "Repeated[E1].", Calls(repeat), mode="paragraph")
        self.assertEqual(result["status"], "REVIEW")
        self.assertTrue(result["verification_errors"])

    def test_revise_only_failed_sentence_preserves_every_other_character(self):
        def wrong(out, payload):
            for row, claim in zip(out["checks"], payload["claims"]):
                if "Gamma" in claim["text"]:
                    row["verdict"]["status"] = "contradicted"
                    row["verdict"]["labels"] = ["STR"]
                    row["reason"] = "Authored failure."
        first, _ = self.run_review(caller=Calls(wrong))
        targets = first["repair_claims"]
        self.assertEqual(len(targets), 1)
        def edit(stage, instruction, payload):
            self.assertEqual(stage, "revise_claims")
            self.assertEqual([c["claim_id"] for c in payload["claims"]], targets)
            return {"finish_reason": "stop", "output": {
                "replacements": [{"claim_id": targets[0], "text": "Gamma remains present[E1:S3]."}]}}
        result = core.revise(packet(), first, edit)
        self.assertEqual(result["draft"], DRAFT.replace("Gamma is present[E1:S3].", "Gamma remains present[E1:S3]."))
        self.assertEqual(len(result["changed_claims"]), 1)
        self.assertEqual([u["unit_id"] for u in result["changed_units"]], ["U1"])
        self.assertEqual(first["draft"], DRAFT)

    def test_unknown_completion_never_triggers_retry(self):
        calls = []
        def unknown(stage, instruction, payload):
            calls.append(stage)
            return {"finish_reason": None, "output": {}}
        with self.assertRaises(ValueError):
            core.verify(packet(), DRAFT, unknown, mode="paragraph")
        self.assertEqual(calls, ["verify_claim"])

    def test_incomplete_or_duplicate_claim_receipts_cannot_pass(self):
        for change in ("missing", "duplicate", "unknown"):
            def mutate(out, _):
                if change == "missing":
                    out["checks"].pop()
                elif change == "duplicate":
                    out["checks"][1] = out["checks"][0]
                else:
                    out["checks"][0]["claim_id"] = "unknown"
            result, _ = self.run_review(caller=Calls(mutate))
            self.assertEqual(result["status"], "REVIEW")
            self.assertTrue(result["verification_errors"])


    def test_numeric_explanation_cannot_borrow_another_source_or_unquoted_segment(self):
        for wrong in ({"evidence_id": "E2", "segment_id": "S1"},
                      {"evidence_id": "E1", "segment_id": "S5"}):
            p = packet()
            p["evidence"].append({"id": "E2", "version": "v1", "text": "The count is nine."})
            def explain(out, _):
                out["checks"][0]["verdict"]["numeric_resolutions"] = [{
                    "token": "9", "reason": "Authored conversion from nine.", "evidence": [wrong]}]
            result = core.verify(p, "The count is 9[E1:S1].", Calls(explain), mode="paragraph")
            self.assertEqual(result["status"], "REVIEW")
            self.assertTrue(result["verification_errors"])
            self.assertEqual(core.revise(p, result, lambda *a: self.fail("No rewrite."))["changed_claims"], [])

    def test_absence_cannot_expand_a_partial_citation_to_whole_source(self):
        def absence(out, _):
            out["checks"][0]["verdict"].update(kind="source_absence", missing_information="Epsilon")
        result, _ = self.run_review("材料未提供Epsilon[E1:S1]。", caller=Calls(absence))
        self.assertEqual(result["status"], "REVIEW")
        self.assertTrue(result["verification_errors"])
        self.assertFalse(any(e.get("scope") == "whole_source" for u in result["units"]
                             for c in u["claims"] for e in c["evidence"]))
        def valid_absence(out, payload):
            absence(out, payload)
            out["checks"][0]["verdict"]["missing_information"] = "Zeta"
        result, _ = self.run_review("本次采用窗口未提供Zeta[E1]。", caller=Calls(valid_absence))
        self.assertEqual(result["status"], "PASS")

    def test_citations_after_full_stop_belong_to_preceding_sentence(self):
        from app.claims import prepare
        for draft, texts in (
            ("Alpha is present. [E1:S1] Beta is present. [E1:S2]",
             ["Alpha is present. [E1:S1]", "Beta is present. [E1:S2]"]),
            ("Alpha is present.[E1:S1] Beta is present.[E1:S2]",
             ["Alpha is present.[E1:S1]", "Beta is present.[E1:S2]"]),
            ("甲。[E1:S1]乙。[E1:S2]", ["甲。[E1:S1]", "乙。[E1:S2]"])):
            source, claims = prepare(packet(), draft)
            self.assertEqual([c["text"] for c in claims], texts)
            self.assertEqual([[r["reference"] for r in c["references"]] for c in claims],
                             [["E1:S1"], ["E1:S2"]])
            self.assertTrue(all(draft[c["start"]:c["end"]] == c["text"] for c in claims))

    def test_list_research_scope_reaches_coverage_without_crashing(self):
        p = packet()
        p["assessment"] = {"research_scope": [{"dimension": "patents", "status": "not_checked"}]}
        calls = Calls()
        result = core.verify(p, DRAFT, calls, mode="paragraph")
        self.assertEqual(result["status"], "PASS")
        coverage = next(value for stage, value in calls.calls if stage == "verify_coverage")
        self.assertEqual(coverage["task_scope"], p["assessment"]["research_scope"])

    def test_batch_byte_limit_includes_final_request_metadata(self):
        from app import claims
        from app.prompts import CLAIM_VERIFY
        p = packet()
        p["evidence"][0]["text"] = "a" * 100
        def resize(count):
            text = "a" * count
            p["evidence"][0]["text"] = text
            p["evidence"][0]["source_context"]["source_window"].update(
                end=10+count, source_length=count+10, adopted_text_length=count,
                text_sha256=hashlib.sha256(text.encode()).hexdigest())
            source, items = claims.prepare(p, "Observed[E1].")
            return len(json.dumps({"instruction": CLAIM_VERIFY,
                "payload": claims.batch_payload(source, items)}, ensure_ascii=False).encode())
        low, high = 100, claims.MAX_BATCH_BYTES
        while low < high:
            mid = (low + high + 1) // 2
            if resize(mid) <= claims.MAX_BATCH_BYTES:
                low = mid
            else:
                high = mid - 1
        resize(low)
        calls = Calls()
        result = core.verify(p, "Observed[E1].", calls, mode="paragraph")
        self.assertEqual(result["status"], "REVIEW")
        self.assertFalse(calls.calls)

    def test_quoted_source_number_conversion_keeps_correct_case(self):
        p = {"question": "Count.", "evidence": [{"id": "E1", "version": "v1", "text": "The count is nine."}]}
        def explain(out, _):
            out["checks"][0]["verdict"]["numeric_resolutions"] = [{"token": "9",
                "reason": "nine is 9", "evidence": [{"evidence_id": "E1", "segment_id": "S1"}]}]
        result = core.verify(p, "The count is 9[E1:S1].", Calls(explain), mode="paragraph")
        self.assertEqual(result["status"], "PASS")

    def test_analysis_conversion_is_limited_to_actually_cited_quoted_value(self):
        basis = {"analysis_unit": "item", "unit": "items", "period": "fixed-window",
                 "population": "fixed-set", "denominator": 4}
        p = {"question": "Count.", "evidence": [], "assessment": {"analysis_results": [
            {"id": key, "dataset_ref": key, "method_version": "count.v1", "parameters": {},
             "input_refs": [{"source_record_id": key, "version": "v1"}], "limitations": [],
             "calculation_basis": basis, "output": {"count": value}}
            for key, value in (("A1", 2), ("A2", 9))]}}
        for key, expected in (("A1", "PASS"), ("A2", "REVIEW")):
            def converted(out, _):
                row = out["checks"][0]
                row["quotes"] = [{"reference": "A1", "output_path": "", "quote": '{"count":2}'}]
                row["verdict"].update(kind="analysis_result", binding=basis,
                    numeric_resolutions=[{"token": "2000", "reason": "2*1000",
                        "evidence": [], "analysis_refs": [{"analysis_id": key, "output_path": "/count"}],
                        "calculation": dict(basis, expression="2*1000", unit="milli-items")}])
            result = core.verify(p, "Count is 2000 milli-items[A1].", Calls(converted), mode="paragraph")
            self.assertEqual(result["status"], expected)
        p["assessment"]["analysis_results"][0]["output"] = {"count": 1, "other": 10}
        def substring(out, payload):
            converted(out, payload)
            out["checks"][0]["quotes"] = [{"reference": "A1", "output_path": "", "quote": '"other":10'}]
            out["checks"][0]["verdict"]["numeric_resolutions"][0]["analysis_refs"][0]["analysis_id"] = "A1"
        # The short quote contains 10, not the selected value 1.
        result = core.verify(p, "Count is 2000 milli-items[A1].", Calls(substring), mode="paragraph")
        self.assertEqual(result["status"], "REVIEW")
        self.assertTrue(result["verification_errors"])
        p["assessment"]["analysis_results"][0]["output"] = {"count": 1, "negative": -1, "label": "x=1 y"}
        for quoted in ('"negative":-1', '"label":"x=1 y"'):
            def wrong_json_value(out, payload):
                substring(out, payload)
                out["checks"][0]["quotes"][0]["quote"] = quoted
            result = core.verify(p, "Count is 2000 milli-items[A1].", Calls(wrong_json_value), mode="paragraph")
            self.assertEqual(result["status"], "REVIEW")
            self.assertTrue(result["verification_errors"])

    def test_supported_claim_with_failed_citation_is_rechecked_not_cached(self):
        def issue(out, payload):
            for row, claim in zip(out["checks"], payload["claims"]):
                if "Alpha" in claim["text"]:
                    row["verdict"]["labels"] = ["REF"]
        first, _ = self.run_review(caller=Calls(issue))
        self.assertNotEqual(first["status"], "PASS")
        second, calls = self.run_review(previous_review=first)
        sent = [c["text"] for stage, payload in calls.calls if stage == "verify_claim" for c in payload["claims"]]
        self.assertEqual(sent, ["Alpha is present[E1:S1]."])
        self.assertEqual(len(second["reused_claims"]), 4)
        self.assertEqual(second["status"], "PASS")
        first, _ = self.run_review()
        first["status"] = "REVIEW"
        first["verification_errors"] = [{"unit_id": "U1", "errors": ["Authored unresolved unit check"]}]
        second, calls = self.run_review(previous_review=first)
        self.assertEqual(second["reused_claims"], [])

    def test_new_review_still_supports_separate_paid_audit(self):
        review, _ = self.run_review()
        before = copy.deepcopy(review)
        stages = []
        def audit(stage, instruction, payload):
            stages.append(stage)
            return {"checks": [{"unit_id": c["unit_id"], "claim_index": c["claim_index"],
                "verdict": "supported", "reason": "Authored independent receipt.",
                "support": [{"evidence_id": e["evidence_id"], "segment_id": e["segment_id"],
                             "quote": e["quote"]} for e in c["evidence"]]} for c in payload["claims"]]}
        result = core.audit_support(packet(), review, audit)
        self.assertEqual(stages, ["support_audit"])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(result["checks"]), 5)
        self.assertEqual(review, before)

    def test_revision_rpc_requires_completion_and_preserves_legacy_changed_units(self):
        from tests.test_service import request
        def wrong(out, payload):
            for row, c in zip(out["checks"], payload["claims"]):
                if "Gamma" in c["text"]:
                    row["verdict"].update(status="contradicted", labels=["STR"])
        review, _ = self.run_review(caller=Calls(wrong))
        body = request("revise", {"packet": packet(), "review": review}, operation_id="claim-revision")
        step = execute(body)
        self.assertEqual(step["status"], "model_required")
        model_call = step["model_call"]
        self.assertEqual(model_call["stage"], "revise_claims")
        target = model_call["payload"]["claims"][0]["claim_id"]
        receipt = {"call_id": model_call["call_id"], "request_hash": model_call["request_hash"],
                   "output": {"replacements": [{"claim_id": target, "text": "Gamma remains present[E1:S3]."}]}}
        body.update(provider_version=step["provider_version"], model_receipts=[receipt])
        self.assertEqual(execute(body)["status"], "failed")
        receipt["finish_reason"] = "stop"
        result = execute(body)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["changed_units"][0]["unit_id"], "U1")
        self.assertEqual(len(result["result"]["changed_claims"]), 1)
        previous = request("verify", {"packet": packet(), "draft": result["result"]["draft"],
            "mode": "paragraph", "previous_review": review}, operation_id="claim-recheck")
        next_step = execute(previous)
        self.assertEqual(next_step["status"], "model_required")
        self.assertEqual(next_step["model_call"]["stage"], "verify_claim")
        self.assertEqual(len(next_step["model_call"]["payload"]["claims"]), 3)

    def test_quote_diagnostics_preserve_message_and_add_stable_rule_code(self):
        def fake(out, _):
            out["checks"][0]["quotes"][0]["quote"] = "Invented quotation."
        result, _ = self.run_review(caller=Calls(fake))
        error = next(e for e in result["verification_errors"] if e.get("claim_id"))
        self.assertEqual(error["error_codes"], ["quote_not_found"])
        self.assertEqual(error["errors"], ["短引不是被引原段中唯一的连续原文"])
        check = next(c for c in result["claim_checks"] if c["claim_id"] == error["claim_id"])
        self.assertEqual(check["error_codes"], error["error_codes"])

    def test_reuse_reasons_only_report_provable_binding_changes(self):
        first, _ = self.run_review()
        self.assertTrue(all(c["reason_code"] == "no_prior_check" for c in first["reuse_decisions"]))
        changed, _ = self.run_review(DRAFT.replace("Gamma is present", "Gamma remains present"), previous_review=first)
        decisions = changed["reuse_decisions"]
        self.assertEqual([c["reason_code"] for c in decisions],
                         ["unchanged", "context_changed", "no_prior_check", "context_changed", "unchanged"])
        self.assertEqual(decisions[1]["changed_binding_fields"], ["context"])
        p = packet();p["evidence"][0]["version"] = "v2"
        source_change = core.verify(p, DRAFT, Calls(), mode="paragraph", previous_review=first)
        self.assertTrue(all(c["reason_code"] == "source_changed" and c["changed_binding_fields"] == ["references"]
                            for c in source_change["reuse_decisions"]))

if __name__ == "__main__":
    unittest.main()
