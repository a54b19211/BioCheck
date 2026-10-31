"""Validate model output structure; do not infer or replace factual conclusions."""
import re
import json

from .evidence import locate_support, locate_analysis, analysis_results, ReferenceLocationError


PLAN_DIAGNOSTIC_LIMIT = 8
CLAIM_KINDS = ("fact", "source_absence", "analysis_result", "hypothesis")
SOURCE_USES = ("current_fact", "attributed_report", "hypothesis", "historical", "information_environment")


def analysis_binding_matches(binding, results):
    """Accept explicit original calculation context, never guess a nested alias."""
    required = {"analysis_unit", "unit", "period", "population", "denominator"}
    for result in results:
        basis = result.get("calculation_basis", {})
        selected = binding.get(result["analysis_id"], binding) if isinstance(binding, dict) else None
        if required - basis.keys() or not isinstance(selected, dict) or basis.keys() - selected.keys():
            return False
        try:
            if any(json.dumps(selected[k], sort_keys=True, allow_nan=False)
                   != json.dumps(v, sort_keys=True, allow_nan=False) for k, v in basis.items()):
                return False
        except (ValueError, TypeError):
            return False
    return True


class PlanReferenceError(ValueError):
    def __init__(self, diagnostics, omitted=0):
        super().__init__(diagnostics[0]["message"])
        self.diagnostics, self.omitted = diagnostics, omitted
        self.repair_attempted = 0


def check_plan(source, planned, method):
    if method not in {"direct", "plain", "bound"}:
        raise ValueError("method须为direct/plain/bound")
    if method == "direct":
        if planned is not None:
            raise ValueError("direct不使用计划")
        return
    if not isinstance(planned, dict) or not isinstance(planned.get("points"), list) or not planned["points"]:
        raise ValueError("生成计划为空")
    lookup = {e["id"]: e for e in source["evidence"]}
    analyses = analysis_results(source)
    diagnostics, count = [], 0

    def record(code, point_index, field, message, provided=None, reference_index=None):
        nonlocal count
        count += 1
        if len(diagnostics) < PLAN_DIAGNOSTIC_LIMIT:
            item = {"code": code, "point_index": point_index, "field": field,
                    "message": message[:240]}
            if provided is not None:
                item["provided"] = str(provided)[:160]
            if reference_index is not None:
                item["reference_index"] = reference_index
            diagnostics.append(item)

    for index, point in enumerate(planned["points"]):
        if not isinstance(point, dict):
            raise ValueError("计划要点须为对象")
        declared = {}
        for field, allowed, code in (("evidence_ids", lookup, "evidence_id_unknown"),
                                     ("analysis_ids", analyses, "analysis_id_unknown")):
            refs = point.get(field, [])
            if not isinstance(refs, list) or not all(isinstance(k, str) for k in refs):
                record("plan_reference_structure", index, field, "计划引用编号须为字符串列表")
                declared[field] = None
                continue
            declared[field] = set(refs)
            for position, key in enumerate(refs):
                if key not in allowed:
                    record(code, index, field, "计划引用了未提供的依据编号", key, position)
        if declared.get("evidence_ids") == declared.get("analysis_ids") == set():
            record("plan_reference_empty", index, "evidence_ids/analysis_ids", "计划缺少有效依据")
        if method == "bound":
            located = {}
            for field, locator, table in (("support", locate_support, lookup),
                                           ("analysis_refs", locate_analysis, analyses)):
                items = point.get(field, [])
                located[field] = []
                if not isinstance(items, list):
                    record("plan_reference_structure", index, field, "计划依据须为列表")
                    continue
                for position, item in enumerate(items):
                    try:
                        located[field].extend(locator([item], table))
                    except ReferenceLocationError as error:
                        record(error.code, index, field + "/" + error.field,
                               str(error), error.provided, position)
                    # Version conflicts and other input errors remain terminal.
            for field, refs, key in (("evidence_ids", located["support"], "evidence_id"),
                                     ("analysis_ids", located["analysis_refs"], "analysis_id")):
                if declared[field] is not None and declared[field] != {r[key] for r in refs}:
                    record("plan_reference_mismatch", index, field, "声明编号与实际定位的依据不一致")
            if not isinstance(point.get("binding"), dict):
                raise ValueError("绑定计划缺少binding")
    if diagnostics:
        raise PlanReferenceError(diagnostics, count - len(diagnostics))



class VerificationContractError(ValueError):
    def __init__(self, diagnostics, paths, *, semantic_findings=False, repairable=True):
        super().__init__(diagnostics[0]["message"])
        self.diagnostics = diagnostics[:8]
        self.omitted = max(0, len(diagnostics) - 8)
        self.invalid_paths = paths
        self.retry_eligible = repairable and not semantic_findings
        self.repair_attempted = 0


def _negative_verdict(value):
    if value is False:
        return True
    if isinstance(value, str):
        return re.search(r"(?<![a-z])(?:contradicted|insufficient|unsupported|not[\s_-]+supported|"
                         r"uncertain|incomplete|unknown|unverified|fail|failed|review|false)(?![a-z])",
                         value.strip(), re.I) is not None
    if isinstance(value, dict):
        return any(_negative_verdict(k) or _negative_verdict(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_negative_verdict(v) for v in value)
    return False


def _has_findings(value):
    """Malformed negative labels still block any attempt to re-score the output."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"verdict", "status"} and _negative_verdict(item):
                return True
            if key in {"issues", "source_gaps", "verification_errors"} and item:
                return True
            if _has_findings(item):
                return True
    elif isinstance(value, list):
        return any(_has_findings(item) for item in value)
    return False


def check_verification_contract(response, units):
    """Only fatal container/required-verdict faults qualify; normal REVIEW stays REVIEW."""
    diagnostics, paths = [], set()
    repairable = True

    def error(path, code, message):
        paths.add(path)
        diagnostics.append({"path": path, "code": code, "message": message})

    def verdict(value, path, allowed):
        if not isinstance(value, str) or value not in allowed:
            error(path, "verdict_required_or_enum", "必须提供允许枚举中的verdict，不能用上层结论代填")

    if not isinstance(response, dict):
        error("", "verification_structure", "核验回执须为JSON对象")
        repairable = False
    else:
        reviews = response.get("reviews")
        if not isinstance(reviews, list):
            error("/reviews", "reviews_required", "reviews须为段落核验列表")
            if reviews is not None:
                repairable = False
        else:
            valid_units = (len(reviews) == len(units) and all(isinstance(r, dict) for r in reviews)
                           and all(isinstance(r.get("unit_id"), str) for r in reviews)
                           and {r["unit_id"] for r in reviews} == {u["id"] for u in units})
            if not valid_units:
                error("/reviews", "unit_coverage_invalid", "段落编号缺失、重复或不匹配；不能猜测对应关系")
                repairable = False
            else:
                for i, review in enumerate(reviews):
                    path = f"/reviews/{i}"
                    verdict(review.get("verdict"), path + "/verdict", {"supported", "contradicted", "insufficient"})
                    claims = review.get("claims")
                    if not isinstance(claims, list) or not claims:
                        error(path + "/claims", "claims_required", "每段须提供非空claims列表")
                        if claims is not None and claims != []:
                            repairable = False
                        continue
                    for j, claim in enumerate(claims):
                        claim_path = path + f"/claims/{j}"
                        if not isinstance(claim, dict):
                            error(claim_path, "claim_structure", "子声明须为对象")
                            if claim is not None:
                                repairable = False
                            continue
                        verdict(claim.get("verdict"), claim_path + "/verdict",
                                {"supported", "contradicted", "insufficient"})
        coverage = response.get("coverage")
        if not isinstance(coverage, dict):
            error("/coverage", "coverage_required", "须提供本次材料内的任务覆盖评价")
            if coverage is not None:
                repairable = False
        else:
            verdict(coverage.get("verdict"), "/coverage/verdict", {"complete", "incomplete", "uncertain"})
    if diagnostics:
        raise VerificationContractError(diagnostics, paths, semantic_findings=_has_findings(response),
                                        repairable=repairable)


def same_outside_errors(before, after, allowed, path=""):
    """Require a format-only repair: existing valid content is immutable."""
    if path in allowed:
        return True
    if type(before) is not type(after):
        return False
    if isinstance(before, dict):
        for key in before.keys() | after.keys():
            child = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in before or key not in after:
                if child not in allowed:
                    return False
            elif not same_outside_errors(before[key], after[key], allowed, child):
                return False
        return True
    if isinstance(before, list):
        return len(before) == len(after) and all(
            same_outside_errors(a, b, allowed, path + "/" + str(i))
            for i, (a, b) in enumerate(zip(before, after)))
    return before == after


class VerificationCompletionError(ValueError):
    """A known truncation or an unconfirmed completion is not a semantic verdict."""
    def __init__(self, code, unit_id, finish_reason, attempt):
        super().__init__("核验输出未完整结束；保留回执，不采用部分结论")
        self.code, self.unit_id = code, unit_id
        self.finish_reason, self.attempt = finish_reason, attempt
