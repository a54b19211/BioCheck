"""Cited-claim checks and sentence edits. Model execution stays with the host."""
import hashlib
import json
import re
from collections import Counter
from copy import deepcopy

from . import core
from .contracts import VerificationCompletionError
from .evidence import segments, analysis_results, analysis_signature, locate_support, locate_analysis
from .source_context import SOURCE_FIELDS, evidence_catalog, source_updates
from .prompts import CLAIM_VERIFY, CLAIM_REVISE, VERIFY_COVERAGE

MAX_CLAIMS_PER_BATCH = 3
MAX_BATCH_BYTES = 120000


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def provider():
    from .rpc import PROVIDER_VERSION
    return PROVIDER_VERSION


def sentences(draft):
    result, occurrences = [], Counter()
    for unit in core.split_units(draft):
        cursor, index = 0, 0
        for span in core._draft_sentences(unit["text"]):
            start, end = max(cursor, span["start"]), span["end"]
            if start >= end:
                continue
            # Consume a trailing citation run before starting the next sentence.
            following = re.match(r"\s*(?:\[[A-Za-z][A-Za-z0-9_.:-]*\]\s*)+", unit["text"][end:])
            if following:
                end += following.end()
            cursor = end
            text = unit["text"][start:end]
            start += len(text) - len(text.lstrip())
            end -= len(text) - len(text.rstrip())
            text = unit["text"][start:end]
            if not text:
                continue
            identity = digest(text)[:20]
            occurrences[identity] += 1
            result.append({"claim_id": f"C{identity}-{occurrences[identity]}",
                "unit_id": unit["id"], "claim_index": index, "text": text,
                "span_start": start, "span_end": end, "start": unit["start"] + start,
                "end": unit["start"] + end})
            index += 1
    for i, claim in enumerate(result):
        claim["context"] = {
            "previous": result[i - 1]["text"] if i else None,
            "next": result[i + 1]["text"] if i + 1 < len(result) else None,
            "previous_same_paragraph": bool(i and result[i - 1]["unit_id"] == claim["unit_id"]),
            "next_same_paragraph": bool(i + 1 < len(result) and result[i + 1]["unit_id"] == claim["unit_id"])}
    return result


def _reference(reference, source, lookup, analyses):
    key, part = core.reference_target(reference, lookup)
    if key in lookup:
        item = lookup[key]
        if part:
            selected = next(p for p in segments(item["text"]) if p["id"] == part)
            start, end = selected["start"], selected["end"]
        else:
            context = item.get("source_context", {})
            window = context.get("source_window", {})
            text = item["text"]
            if (context.get("adopted_text_level") != "selected_passages"
                    or window.get("hash_scope") != "adopted_packet_text"
                    or window.get("adopted_text_length") != len(text)
                    or window.get("text_sha256") != hashlib.sha256(text.encode()).hexdigest()):
                raise ValueError("记录级引用缺少与原文相符的固定采用窗口")
            a, b, total = window.get("start"), window.get("end"), window.get("source_length")
            if a is not None or b is not None:
                if type(a) is not int or type(b) is not int or a < 0 or b - a != len(text):
                    raise ValueError("采用窗口位置与实际文本不一致")
            if total is not None and (type(total) is not int or total < len(text) or b is not None and b > total):
                raise ValueError("采用窗口越过原文长度")
            start, end = 0, len(text)
        backgrounds = core._audit_source_context(
            [{"evidence_id": key, "start": start, "end": end}], lookup)[0]["paragraphs"]
        return {"reference": reference, "kind": "source", "evidence_id": key,
                "version": item["version"], "start": start, "end": end,
                "text": item["text"][start:end],
                "identity": {k: deepcopy(item[k]) for k in SOURCE_FIELDS if k in item},
                "identity_context": [p for p in backgrounds if not start <= p["start"] <= p["end"] <= end]}
    if reference in analyses:
        value = analyses[reference]
        body = {k: value[k] for k in ("output", "calculation_basis", "limitations") if k in value}
        text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {"reference": reference, "kind": "analysis_result", "analysis_id": reference,
                "version": digest(value), "start": 0, "end": len(text), "text": text,
                "identity": {k: deepcopy(value[k]) for k in
                             ("dataset_ref", "method_version", "input_refs", "parameters") if k in value},
                "identity_context": []}
    raise ValueError("本句引用了未知来源或无效段号：" + reference)


def prepare(packet, draft):
    source = core.packet_input(packet)
    lookup = {e["id"]: e for e in source["evidence"]}
    analyses = analysis_results(source)
    task = digest({"question": source["question"], "assessment": source.get("assessment", {})})
    claims = sentences(draft)
    for claim in claims:
        claim["references"], claim["input_errors"] = [], []
        for reference in dict.fromkeys(core.CITATION.findall(claim["text"])):
            try:
                claim["references"].append(_reference(reference, source, lookup, analyses))
            except ValueError as error:
                claim["input_errors"].append(str(error))
        if not core.CITATION.findall(claim["text"]):
            claim["input_errors"].append("本句没有实际引用，不能借用相邻句或整段的引用")
        claim["binding"] = {"task": task, "statement": digest(claim["text"]),
                            "context": digest(claim["context"]), "references": digest(claim["references"]),
                            "method": provider()}
        claim["fingerprint"] = digest(claim["binding"])
    return source, claims


def batch_payload(source, claims):
    refs = {}
    for claim in claims:
        for ref in claim["references"]:
            refs[ref["reference"]] = ref
    return {"question": source["question"],
            "claims": [{"claim_id": c["claim_id"], "text": c["text"], "context": c["context"],
                        "references": [r["reference"] for r in c["references"]]} for c in claims],
            "references": list(refs.values()),
            "source_updates": source_updates(source)}


def request_payload(stage, payload, draft, attempt=0):
    limit = 16384 if attempt or stage == "revise_claims" else (8192 if stage == "verify_coverage" else 12288)
    return dict(payload, verification_request={
        "claim_ids": [c["claim_id"] for c in payload.get("claims", [])], "attempt": attempt,
        "draft_sha256": hashlib.sha256(draft.encode()).hexdigest(), "max_output_tokens": limit})


def request_bytes(instruction, payload):
    return len(json.dumps({"instruction": instruction, "payload": payload}, ensure_ascii=False).encode())


def batches(source, claims, draft):
    current = []
    for claim in claims:
        proposed = current + [claim]
        size = request_bytes(CLAIM_VERIFY, request_payload("verify_claim", batch_payload(source, proposed), draft))
        if current and (len(proposed) > MAX_CLAIMS_PER_BATCH or claim["unit_id"] != current[0]["unit_id"]
                        or size > MAX_BATCH_BYTES):
            yield current
            current = []
        current.append(claim)
    if current:
        yield current


def completed(call, stage, instruction, payload, draft, recorded, *, retry=False):
    ids = [c["claim_id"] for c in payload.get("claims", [])]
    for attempt in range(2 if retry else 1):
        actual_stage = stage + "_retry" if attempt else stage
        request = request_payload(stage, payload, draft, attempt)
        limit = request["verification_request"]["max_output_tokens"]
        if request_bytes(instruction, request) > MAX_BATCH_BYTES:
            raise ValueError("实际模型请求超过本地大小限制，未发起调用")
        receipt = call(actual_stage, instruction, request)
        finish = receipt.get("finish_reason") if isinstance(receipt, dict) else None
        output = receipt.get("output") if isinstance(receipt, dict) else None
        recorded.append({"stage": actual_stage, "claim_ids": ids, "attempt": attempt,
                         "finish_reason": finish, "max_output_tokens": limit, "output": deepcopy(output)})
        if finish == "stop" and isinstance(output, dict):
            return output
        if finish == "length" and retry and attempt == 0:
            continue
        error = VerificationCompletionError(
            "model_output_truncated" if finish == "length" else "model_completion_unconfirmed",
            None, finish, attempt)
        error.claim_ids = ids
        raise error


def quote_contains_value(text, value):
    """Accept a complete JSON value or member, never a number inside a string."""
    for candidate in (text, "{" + text + "}"):
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        pending = [parsed]
        while pending:
            item = pending.pop()
            if digest(item) == digest(value):
                return True
            if isinstance(item, dict):
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
    return False


def quote_error(code, message):
    error = ValueError(message)
    error.code = code
    return error


def parse_answer(claim, answer, source):
    if not isinstance(answer, dict) or set(answer) != {"claim_id", "verdict", "quotes", "reason"}:
        raise ValueError("主张回执须为claim_id/verdict/quotes/reason四个字段")
    if answer["claim_id"] != claim["claim_id"] or not isinstance(answer["reason"], str) or not answer["reason"].strip():
        raise ValueError("主张编号或理由无效")
    verdict = answer["verdict"]
    if not isinstance(verdict, dict) or verdict.get("status") not in {"supported", "contradicted", "insufficient"}:
        raise ValueError("主张缺少有效判定")
    status = verdict["status"]
    allowed = {r["reference"]: r for r in claim["references"]}
    quotes = answer["quotes"]
    if not isinstance(quotes, list) or status in {"supported", "contradicted"} and not quotes:
        raise quote_error("quote_required", "支持或矛盾判定须有原文短引")
    located, evidence, derived, used = [], [], [], set()
    lookup = {e["id"]: e for e in source["evidence"]}
    for quote in quotes:
        if not isinstance(quote, dict) or not isinstance(quote.get("reference"), str):
            raise quote_error("quote_reference_invalid", "短引须明确对应本句实际引用")
        ref = allowed.get(quote["reference"])
        text = quote.get("quote")
        if ref is None or not isinstance(text, str) or not text.strip():
            raise quote_error("quote_outside_citation", "短引来源不在本句实际引用范围中")
        if "version" in quote and quote["version"] != ref["version"]:
            raise quote_error("quote_version_mismatch", "短引与固定来源版本不一致")
        offset = ref["text"].find(text)
        if offset < 0 or ref["text"].find(text, offset + 1) >= 0:
            raise quote_error("quote_not_found" if offset < 0 else "quote_ambiguous",
                              "短引不是被引原段中唯一的连续原文")
        start, end = ref["start"] + offset, ref["start"] + offset + len(text)
        located.append({"reference": ref["reference"], "version": ref["version"],
                        "start": start, "end": end, "quote": text})
        used.add(ref["reference"])
        if ref["kind"] == "source":
            for part in segments(lookup[ref["evidence_id"]]["text"]):
                if start < part["end"] and end > part["start"]:
                    item = {"evidence_id": ref["evidence_id"], "segment_id": part["id"]}
                    if item not in evidence:
                        evidence.append(item)
        else:
            item = {"analysis_id": ref["analysis_id"], "output_path": ""}
            if item not in derived:
                derived.append(item)
    if status == "supported" and used != set(allowed):
        raise quote_error("quote_citation_missing", "支持判定未给出本句每个实际引用的有效短引")
    metadata = {k: deepcopy(verdict[k]) for k in (
        "kind", "source_use", "attribution_span", "uncertainty_span", "history_context",
        "binding", "missing_information") if k in verdict}
    if not all(k in metadata for k in ("kind", "source_use")):
        raise ValueError("判定须保留kind和source_use，不能绕过来源状态检查")
    if metadata["kind"] == "source_absence":
        for ref in claim["references"]:
            if ref["kind"] == "source":
                whole = _reference(ref["evidence_id"], source, lookup, {})
                if (ref["start"], ref["end"]) != (whole["start"], whole["end"]):
                    raise ValueError("部分引文不能扩展为整份采用材料的缺省判断")
                evidence.append({"evidence_id": ref["evidence_id"], "scope": "whole_source"})
    labels = verdict.get("labels", [])
    if not isinstance(labels, list) or any(not isinstance(x, str) or x not in core.LABELS for x in labels):
        raise ValueError("问题标签无效")
    normalized = dict(metadata, claim_id=claim["claim_id"], span=claim["text"],
        span_start=claim["span_start"], span_end=claim["span_end"],
        verdict=status, reason=answer["reason"], evidence=evidence, analysis_refs=derived,
        short_quotes=located)
    issues = []
    if status != "supported" or labels:
        issues = [{"span": claim["text"], "span_start": claim["span_start"], "span_end": claim["span_end"],
                   "claim_id": claim["claim_id"], "labels": labels or ["REF"],
                   "reason": answer["reason"], "evidence": evidence, "analysis_refs": derived}]
    resolutions = verdict.get("numeric_resolutions", [])
    if not isinstance(resolutions, list):
        raise ValueError("数值解释须为列表")
    quoted_parts = {(e["evidence_id"], e.get("segment_id")) for e in evidence if e.get("segment_id")}
    for resolution in resolutions:
        if not isinstance(resolution, dict) or resolution.get("token") not in core.numeric_tokens(claim["text"]):
            raise ValueError("数值解释须对应本句实际数字")
        for item in locate_support(resolution.get("evidence", []), lookup):
            if (item["evidence_id"], item["segment_id"]) not in quoted_parts:
                raise ValueError("数值解释越过了本句实际引用及已验证短引范围")
        for item in locate_analysis(resolution.get("analysis_refs", []), analysis_results(source)):
            if not any(q["reference"] == item["analysis_id"]
                       and quote_contains_value(q["quote"], item["value"]) for q in located):
                raise ValueError("数值解释的分析输出不在本句实际引用及已验证短引内")
    return normalized, issues, resolutions


def _previous(previous_review, source):
    if previous_review is None:
        return {}
    if (previous_review.get("verification_method") != "cited_claims"
            or previous_review.get("method_provider_version") != provider()
            or not core.review_locations_match(previous_review)
            or previous_review.get("draft_sha256") != hashlib.sha256(previous_review["draft"].encode()).hexdigest()
            or previous_review.get("status") not in {"PASS", "REVIEW", "FAIL"}
            or not isinstance(previous_review.get("claim_checks"), list)):
        raise ValueError("previous_review须为本方法同版本的完整已完成回执，不能采用未知或旧方法结果")
    old_claims = {c.get("claim_id"): c for u in previous_review["units"] for c in u.get("claims", [])}
    blocked = set()
    for error in previous_review.get("verification_errors", []):
        if error.get("claim_id"):
            blocked.add(error["claim_id"])
        elif error.get("unit_id"):
            blocked.update(c.get("claim_id") for u in previous_review["units"]
                           if u["unit_id"] == error["unit_id"] for c in u["claims"])
        else:
            return {}
    for unit in previous_review["units"]:
        for issue in unit.get("issues", []):
            try:
                start, end = core.locate_span(unit["text"], issue)
            except ValueError:
                blocked.update(c.get("claim_id") for c in unit["claims"])
            else:
                blocked.update(c.get("claim_id") for c in unit["claims"]
                               if start < c["span_end"] and end > c["span_start"])
        blocked.update(c.get("claim_id") for c in unit["claims"] if c.get("citation_status") == "invalid")
    counts = Counter(c.get("fingerprint") for c in previous_review["claim_checks"])
    return {c["fingerprint"]: c for c in previous_review["claim_checks"]
            if c.get("claim_id") not in blocked and counts[c.get("fingerprint")] == 1
            and c.get("finish_reason") == "stop"
            and c.get("effective_verdict") == "supported" and not c.get("errors")
            and old_claims.get(c.get("claim_id"), {}).get("verdict") == "supported"}


def reuse_diagnostics(claim, previous_review, reused, invalid_reply):
    old = [c for c in (previous_review or {}).get("claim_checks", []) if c.get("claim_id") == claim["claim_id"]]
    changed = sorted(k for k, v in claim["binding"].items()
                     if len(old) == 1 and old[0].get("binding", {}).get(k) != v)
    if reused:
        reason = "unchanged"
    elif invalid_reply:
        reason = "previous_reply_invalid"
    elif not old:
        reason = "no_prior_check"
    elif len(old) != 1:
        reason = "ambiguous_prior_check"
    elif changed:
        reason = {("context",): "context_changed", ("references",): "source_changed",
                  ("task",): "task_changed", ("statement",): "claim_changed"}.get(tuple(changed), "inputs_changed")
    elif old[0].get("effective_verdict") != "supported":
        reason = "previous_check_not_supported"
    else:
        reason = "previous_check_failed"
    return {"reason_code": reason, "changed_binding_fields": changed}


def verify(packet, draft, call, previous_review=None):
    source, claims = prepare(packet, draft)
    previous = _previous(previous_review, source)
    counts = Counter(c["fingerprint"] for c in claims)
    answers, errors, error_codes, recorded, reused, decisions = {}, {}, {}, [], [], []
    pending = []
    for claim in claims:
        key = claim["claim_id"]
        if claim["input_errors"]:
            errors[key] = list(claim["input_errors"])
            error_codes[key] = ["citation_binding_invalid"] * len(errors[key])
            decisions.append({"claim_id": key, "action": "review", "reason": "citation_binding_invalid",
                              "reason_code": "citation_binding_invalid", "changed_binding_fields": []})
            continue
        cached = previous.get(claim["fingerprint"]) if counts[claim["fingerprint"]] == 1 else None
        invalid_reply = False
        if cached:
            answer = deepcopy(cached["answer"])
            answer["claim_id"] = key
            try:
                parse_answer(claim, answer, source)
            except ValueError:
                cached = None
                invalid_reply = True
            else:
                answers[key] = answer
                reused.append(key)
        if not cached:
            pending.append(claim)
        decisions.append({"claim_id": key, "action": "reused" if cached else "check",
                          "binding": claim["binding"],
                          **reuse_diagnostics(claim, previous_review, bool(cached), invalid_reply)})
    for batch in batches(source, pending, draft):
        payload = batch_payload(source, batch)
        size = request_bytes(CLAIM_VERIFY, request_payload("verify_claim", payload, draft))
        if size > MAX_BATCH_BYTES:
            for claim in batch:
                errors[claim["claim_id"]] = ["单条主张的实际引用超过请求限制，需缩小已采用窗口后再核验"]
                error_codes[claim["claim_id"]] = ["claim_request_too_large"]
            continue
        output = completed(call, "verify_claim", CLAIM_VERIFY, payload, draft, recorded, retry=True)
        checks = output.get("checks")
        expected = {c["claim_id"] for c in batch}
        if (not isinstance(checks, list) or len(checks) != len(batch)
                or any(not isinstance(c, dict) or not isinstance(c.get("claim_id"), str) for c in checks)
                or {c["claim_id"] for c in checks} != expected):
            for claim in batch:
                errors[claim["claim_id"]] = ["本批回执遗漏、重复或包含未知主张编号"]
                error_codes[claim["claim_id"]] = ["claim_batch_identity_invalid"]
            continue
        answers.update({c["claim_id"]: deepcopy(c) for c in checks})
    normalized, issues, resolutions = {}, {}, {}
    for claim in claims:
        key = claim["claim_id"]
        if key not in errors:
            try:
                normalized[key], issues[key], resolutions[key] = parse_answer(claim, answers[key], source)
            except (ValueError, KeyError, TypeError) as error:
                errors[key] = [str(error)]
                error_codes[key] = [getattr(error, "code", "claim_reply_invalid")]
        if key in errors:
            normalized[key] = {"claim_id": key, "span": claim["text"], "span_start": claim["span_start"],
                "span_end": claim["span_end"], "kind": "fact", "source_use": "current_fact",
                "verdict": "insufficient", "reason": "；".join(errors[key]), "evidence": [], "short_quotes": []}
            issues[key], resolutions[key] = [], []
    reviews = []
    for unit in core.split_units(draft):
        ids = [c["claim_id"] for c in claims if c["unit_id"] == unit["id"]]
        members = [normalized[key] for key in ids]
        verdict = "contradicted" if any(c["verdict"] == "contradicted" for c in members) else (
            "insufficient" if any(c["verdict"] == "insufficient" for c in members) else "supported")
        reviews.append({"unit_id": unit["id"], "verdict": verdict, "reason": "逐条主张及实际引用核验",
                        "claims": members, "issues": [i for key in ids for i in issues[key]],
                        "numeric_resolutions": [i for key in ids for i in resolutions[key]]})
    combined = {"reviews": reviews, "coverage": {"verdict": "uncertain", "reason": "整体覆盖尚未检查"}}
    # Reuse the existing scientific/source-policy checks, without another model request.
    review = core.verify(source, draft, lambda *args: deepcopy(combined), mode="whole")
    for claim in claims:
        if claim["claim_id"] in errors:
            review["verification_errors"].append({"unit_id": claim["unit_id"], "claim_id": claim["claim_id"],
                                                   "errors": errors[claim["claim_id"]],
                                                   "error_codes": error_codes[claim["claim_id"]]})
    scope = source.get("assessment", {}).get("research_scope", {})
    if isinstance(scope, dict):
        scope = {k: v for k, v in scope.items()
                 if k in {"qualification", "stop_status", "coverage_checks", "coverage_issues"}}
    coverage_payload = {"question": source["question"], "draft": draft,
        "checked_unit_ids": [u["unit_id"] for u in review["units"]],
        "paragraph_findings": [{"unit_id": u["unit_id"], "verdict": u["verdict"],
             "issues": [{"labels": i["labels"], "reason": i["reason"]} for i in u["issues"]]}
            for u in review["units"]],
        "task_scope": scope}
    coverage_key = digest({"request": coverage_payload, "method": provider(),
                          "task": source.get("assessment", {}), "answers": answers,
                          "bindings": [c["binding"] for c in claims], "errors": review["verification_errors"]})
    old_coverage = (previous_review or {}).get("coverage_receipt", {})
    coverage_output = None
    if review["verification_errors"]:
        coverage_output = {"checked_unit_ids": coverage_payload["checked_unit_ids"],
                           "coverage": {"verdict": "uncertain", "reason": "存在未完成的主张核验，保留待核"}}
    elif old_coverage.get("request_hash") == coverage_key and old_coverage.get("finish_reason") == "stop":
        coverage_output = deepcopy(old_coverage["output"])
    else:
        coverage_output = completed(call, "verify_coverage", VERIFY_COVERAGE, coverage_payload, draft, recorded)
    coverage = coverage_output.get("coverage", {})
    if (coverage_output.get("checked_unit_ids") != coverage_payload["checked_unit_ids"]
            or not isinstance(coverage, dict) or coverage.get("verdict") not in {"complete", "incomplete", "uncertain"}):
        raise ValueError("整稿覆盖回执遗漏段落或缺少有效结论")
    review["coverage"] = deepcopy(coverage)
    review["raw_response"]["coverage"] = deepcopy(coverage)
    review["raw_response"]["claim_outputs"] = deepcopy(recorded)
    review["status"] = "FAIL" if any(u["verdict"] == "contradicted" or u.get("citation_status") == "invalid"
                                      for u in review["units"]) else (
        "REVIEW" if review["verification_errors"] or review["problem_units"] or coverage["verdict"] != "complete" else "PASS")
    effective = {c["claim_id"]: c for u in review["units"] for c in u["claims"]}
    review.update(verification_method="cited_claims", method_provider_version=provider(),
        draft_sha256=hashlib.sha256(draft.encode()).hexdigest(), reused_claims=reused, reuse_decisions=decisions,
        verification_execution=[{k: v for k, v in c.items() if k != "output"} for c in recorded],
        coverage_receipt={"request_hash": coverage_key, "finish_reason": "stop" if not review["verification_errors"] else None,
                          "output": deepcopy(coverage_output)},
        claim_checks=[{"claim_id": c["claim_id"], "fingerprint": c["fingerprint"], "binding": c["binding"],
                      "finish_reason": "stop" if c["claim_id"] in answers else None,
                      "answer": deepcopy(answers.get(c["claim_id"])), "errors": errors.get(c["claim_id"], []),
                      "error_codes": error_codes.get(c["claim_id"], []),
                      "effective_verdict": effective.get(c["claim_id"], {}).get("verdict")}
                     for c in claims])
    core._add_sentence_diagnostics(review)
    review["repair_claims"] = repair_candidates(review)
    return review


def repair_candidates(review):
    if review.get("verification_errors") or not core.review_locations_match(review):
        return []
    return [c["claim_id"] for u in review["units"] for c in u["claims"]
            if c["verdict"] == "contradicted" and c.get("short_quotes") and not c.get("source_gaps")]


def revise(packet, review, call, *, audit=None, source_errors=()):
    source, current = prepare(packet, review["draft"])
    feedback = core.quality_feedback(review, audit, source_errors=source_errors)
    allowed = set(repair_candidates(review)) if not feedback["review_required"] else set()
    saved = {c["claim_id"]: c for c in review.get("claim_checks", [])}
    targets = [c for c in current if c["claim_id"] in allowed]
    if (review.get("method_provider_version") != provider()
            or review.get("evidence_catalog") != evidence_catalog(source)
            or review.get("source_updates") != source_updates(source)
            or review.get("analysis_signature") != analysis_signature(source)
            or any(saved.get(c["claim_id"], {}).get("fingerprint") != c["fingerprint"] for c in targets)):
        feedback["review_required"].append({"kind": "source_changed", "reason": "任务、来源或语境已变，先重新核验"})
        targets = []
    if not targets:
        return {"draft": review["draft"], "changed_units": [], "changed_claims": [], "feedback": feedback,
                "requires_verification": False, "reason": "没有可按已核原文修订的失败句"}
    payload = batch_payload(source, targets)
    for claim in payload["claims"]:
        finding = next(c for u in review["units"] for c in u["claims"] if c["claim_id"] == claim["claim_id"])
        claim["finding"] = {"verdict": finding["verdict"], "reason": finding["reason"],
                            "short_quotes": finding["short_quotes"]}
    if request_bytes(CLAIM_REVISE, request_payload("revise_claims", payload, review["draft"])) > MAX_BATCH_BYTES:
        raise ValueError("失败句修订请求过大，未修改原稿")
    recorded = []
    result = completed(call, "revise_claims", CLAIM_REVISE, payload, review["draft"], recorded)
    replacements = result.get("replacements")
    if (not isinstance(replacements, list) or len(replacements) != len(targets)
            or any(not isinstance(p, dict) or not isinstance(p.get("claim_id"), str) for p in replacements)
            or {p["claim_id"] for p in replacements} != {c["claim_id"] for c in targets}):
        raise ValueError("修订须逐一对应允许修改的失败句")
    mapped = {p["claim_id"]: p.get("text") for p in replacements}
    lookup = {e["id"]: e for e in source["evidence"]}
    analyses = analysis_results(source)
    for claim in targets:
        text = mapped[claim["claim_id"]]
        if not isinstance(text, str) or not text.strip() or len(sentences(text)) != 1 or "\n" in text:
            raise ValueError("每个替换须为一个非空句子，不能删除或扩成整段")
        refs = core.CITATION.findall(text)
        if not refs:
            raise ValueError("修订句不能删除全部引用")
        for ref in refs:
            actual = _reference(ref, source, lookup, analyses)
            if not any(actual["kind"] == old["kind"] and actual.get("evidence_id", actual.get("analysis_id"))
                       == old.get("evidence_id", old.get("analysis_id")) and actual["version"] == old["version"]
                       and old["start"] <= actual["start"] <= actual["end"] <= old["end"]
                       for old in claim["references"]):
                raise ValueError("修订不能把未引用的邻段或新来源冒作依据")
    draft, changed = review["draft"], []
    for claim in sorted(targets, key=lambda c: c["start"], reverse=True):
        text = mapped[claim["claim_id"]]
        if text != claim["text"]:
            draft = draft[:claim["start"]] + text + draft[claim["end"]:]
            changed.append({"claim_id": claim["claim_id"], "unit_id": claim["unit_id"],
                            "start": claim["start"], "end": claim["end"], "before": claim["text"], "after": text})
    before = {u["unit_id"]: u["text"] for u in review["units"]}
    return {"draft": draft, "changed_claims": list(reversed(changed)),
            "changed_units": [{"unit_id": u["id"], "before": before[u["id"]], "after": u["text"]}
                              for u in core.split_units(draft) if u["text"] != before[u["id"]]],
            "requires_verification": bool(changed),
            "verification_execution": [{k: v for k, v in c.items() if k != "output"} for c in recorded]}
