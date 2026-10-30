"""Evidence-bound writing and verification. No network or database calls here."""
import re
import json
from copy import deepcopy
from decimal import Decimal

from .prompts import WRITE, PLAN, BOUND_PLAN, VERIFY, REVISE, SUPPORT_AUDIT, REPLAN, VERIFY_REPAIR
from .evidence import model_input, locate_support, reference_target, analysis_results, locate_analysis, analysis_signature
from .source_context import evidence_catalog, source_updates, read_requests, check_source_use
from .contracts import (check_plan, PlanReferenceError, PLAN_DIAGNOSTIC_LIMIT,
                        check_verification_contract, VerificationContractError,
                        same_outside_errors)

CITATION = re.compile(r"\[([A-Za-z][A-Za-z0-9_.:-]*)\]")
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-−]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)%?")
LABELS = {"OBJ", "NUM", "TIME", "COND", "STR", "REF"}


def packet_input(packet):
    if not isinstance(packet, dict) or not isinstance(packet.get("question"), str) or not packet["question"].strip():
        raise ValueError("question不能为空")
    evidence = packet.get("evidence", [])
    if not isinstance(evidence, list):
        raise ValueError("evidence须为列表")
    ids = set()
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError("证据须为对象")
        key = item.get("id")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", key) or key in ids:
            raise ValueError("证据id须唯一，并能直接作为引用")
        ids.add(key)
        if not isinstance(item.get("text"), str) or not item["text"].strip() or not item.get("version"):
            raise ValueError("每份证据须有text和version")
    if not isinstance(packet.get("assessment", {}), dict):
        raise ValueError("assessment须为对象")
    analyses = analysis_results(packet)
    source_updates(packet)
    if not evidence and not analyses:
        raise ValueError("需要原文证据或带溯源的分析结果")
    # Explicitly select model input; evaluation labels never enter model requests.
    return dict({k: packet[k] for k in ("question", "assessment") if k in packet}, evidence=evidence)


def plan(packet, call, method="bound", *, max_plan_retries=0):
    """Optionally request one new plan; the host owns sending, payment and replay."""
    if type(max_plan_retries) is not int or max_plan_retries not in (0, 1):
        raise ValueError("max_plan_retries只允许0或1")
    source = packet_input(packet)
    if method not in {"direct", "plain", "bound"}:
        raise ValueError("method须为direct/plain/bound")
    if method == "direct":
        return None
    instruction = BOUND_PLAN if method == "bound" else PLAN
    planned = call("plan_" + method, instruction, model_input(source))
    try:
        check_plan(source, planned, method)
    except PlanReferenceError as error:
        if not max_plan_retries:
            raise
        repair = {"attempt": 1, "max_attempts": 1, "failed_plan": deepcopy(planned),
                  "diagnostics": error.diagnostics, "diagnostics_omitted": error.omitted}
        planned = call("replan_" + method, instruction + REPLAN,
                       model_input(dict(source, plan_repair=repair)))
        try:
            check_plan(source, planned, method)
        except PlanReferenceError as second:
            second.repair_attempted = 1
            raise
    return planned


def write(packet, call, method="bound", *, plan=None):
    """Write from an explicit validated plan; persist that plan in Radar between steps."""
    source = packet_input(packet)
    check_plan(source, plan, method)
    request = dict(source)
    if plan is not None:
        request["plan"] = deepcopy(plan)
    output = call("write_" + method, WRITE, model_input(request))
    draft = output.get("draft")
    if not isinstance(draft, str) or not draft.strip():
        raise ValueError("生成结果缺少非空draft")
    return {"method": method, "plan": plan, "draft": draft}


def generate(packet, call, method="bound", *, max_plan_retries=0):
    planned = plan(packet, call, method, max_plan_retries=max_plan_retries)
    return write(packet, call, method, plan=planned)


def split_units(draft):
    """Keep original paragraph spans; do not silently fix the author's statements."""
    units = []
    for match in re.finditer(r".+?(?=\n[ \t]*\n|\Z)", draft, re.S):
        text = match.group()
        start = match.start() + len(text) - len(text.lstrip())
        end = match.end() - (len(text) - len(text.rstrip()))
        if start < end:
            units.append({"id": f"U{len(units)+1}", "text": draft[start:end], "start": start, "end": end})
    return units


def numeric_tokens(text):
    text = CITATION.sub("", text)
    text = re.sub(r"\b(?:NCT\d+|PMID\s*:?\s*\d+)\b", "", text)
    return list(dict.fromkeys(m.group() for m in NUMBER.finditer(text)))


def number_value(token):
    return Decimal(token.replace(",", "").replace("−", "-").rstrip("%"))


def numeric_hints(unit, evidence, analyses=None):
    refs = [reference_target(k, evidence)[0] for k in CITATION.findall(unit["text"])]
    refs = [k for k in refs if k in evidence]
    text = " ".join(evidence[k]["text"] for k in refs)
    for key in CITATION.findall(unit["text"]):
        if key in (analyses or {}):
            result = analyses[key]
            text += " " + json.dumps({"output": result["output"],
                                       "calculation_basis": result.get("calculation_basis", {})}, ensure_ascii=False)
    available = {number_value(x) for x in numeric_tokens(text)}
    return [token for token in numeric_tokens(unit["text"]) if number_value(token) not in available]



def locate_span(text, item):
    """Resolve one exact occurrence; offsets are relative to the containing unit."""
    span = item.get("span", "")
    if not isinstance(span, str) or not span:
        raise ValueError("陈述缺少非空原文span")
    if "span_start" in item or "span_end" in item:
        start, end = item.get("span_start"), item.get("span_end")
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(text) or text[start:end] != span):
            raise ValueError("陈述位置无效或与span不一致")
        return start, end
    start = text.find(span)
    if start < 0:
        raise ValueError("陈述未逐字对应原稿：" + span)
    if text.find(span, start + 1) >= 0:
        raise ValueError("陈述位置不唯一；请给出span_start/span_end或包含引用的唯一原句：" + span)
    return start, start + len(span)


def claim_citations(text, start, end):
    """Read citations at the already resolved occurrence."""
    explicit = CITATION.findall(text[start:end])
    following = re.match(r"\s*(?:\[[A-Za-z][A-Za-z0-9_.:-]*\]\s*)+", text[end:])
    if following:
        explicit.extend(CITATION.findall(following.group()))
    if explicit:
        return set(explicit)
    # The remainder of this sentence may carry a shared citation.
    tail = re.split(r"[。！？!?;\n；]", text[end:], maxsplit=1)[0]
    sentence_refs = CITATION.findall(tail)
    return set(sentence_refs or CITATION.findall(text))


def verify(packet, draft, call, *, max_verify_retries=0):
    if type(max_verify_retries) is not int or max_verify_retries not in (0, 1):
        raise ValueError("max_verify_retries只允许0或1")
    source = packet_input(packet)
    if not isinstance(draft, str) or not draft.strip():
        raise ValueError("待核稿不能为空")
    units = split_units(draft)
    lookup = {e["id"]: e for e in source["evidence"]}
    analyses = analysis_results(source)
    catalog = evidence_catalog(source)
    model_units = [dict(u, numeric_hints=numeric_hints(u, lookup, analyses),
                        citations=[{"reference": m.group(1), "start": m.start(), "end": m.end()}
                                   for m in CITATION.finditer(u["text"])]) for u in units]
    payload = model_input(dict(source, draft=draft, units=model_units))
    response = call("verify", VERIFY, payload)
    try:
        check_verification_contract(response, model_units)
    except VerificationContractError as error:
        if not max_verify_retries or not error.retry_eligible:
            raise
        original = deepcopy(response)
        repair = {"attempt": 1, "max_attempts": 1, "failed_output": original,
                  "diagnostics": error.diagnostics, "diagnostics_omitted": error.omitted}
        response = call("verify_repair", VERIFY + VERIFY_REPAIR, dict(payload, verify_repair=repair))
        try:
            check_verification_contract(response, model_units)
        except VerificationContractError as second:
            second.repair_attempted = 1
            raise
        if not same_outside_errors(original, response, error.invalid_paths):
            raise ValueError("结构纠错改动了未报错的核验内容，不能采用")
    reviews = response.get("reviews", [])
    if len(reviews) != len(units) or {r.get("unit_id") for r in reviews} != {u["id"] for u in units}:
        raise ValueError("核验结果遗漏或重复了原稿段落")
    by_id = {r["unit_id"]: r for r in reviews}
    problems, results, verification_errors = [], [], []
    for unit in model_units:
        review = by_id[unit["id"]]
        verdict = review.get("verdict")
        if verdict not in {"supported", "contradicted", "insufficient"}:
            raise ValueError("无效核验结论")
        issues, unit_errors, scope_gaps, requests, source_gaps = [], [], [], [], []

        def request_read(claim, index, reason):
            refs = [x for x in claim.get("evidence", []) if isinstance(x, dict)] if isinstance(claim.get("evidence", []), list) else []
            refs = list(refs)
            # Actual draft citations identify candidate sources, not proof of support.
            try:
                start, end = locate_span(unit["text"], claim)
                citations = claim_citations(unit["text"], start, end)
            except ValueError:
                citations = CITATION.findall(unit["text"])
            for citation in sorted(citations):
                key, part = reference_target(citation, lookup)
                if key in lookup and not any(x.get("evidence_id") == key for x in refs):
                    refs.append({"evidence_id": key, **({"segment_id": part} if part else {})})
            needed = read_requests(refs, catalog, claim.get("span", unit["text"]), reason=reason)
            requests.extend(dict(x, unit_id=unit["id"], claim_index=index) for x in needed)
        citation_status = None

        def support(items, *, allow_scope=False):
            # One malformed item must not erase other correctly located passages.
            located = []
            if not isinstance(items, list):
                unit_errors.append("核验依据须为列表")
                return located
            for item in items:
                try:
                    if not isinstance(item, dict):
                        raise ValueError("核验依据须为对象")
                    located.extend(locate_support([item], lookup, allow_scope=allow_scope))
                except ValueError as error:
                    unit_errors.append(str(error))
            return located

        def computed(items):
            try:
                return locate_analysis(items, analyses)
            except ValueError as error:
                unit_errors.append(str(error))
                return []

        for issue in review.get("issues", []):
            labels = issue.get("labels", [])
            if not labels or set(labels) - LABELS:
                raise ValueError("问题标签无效")
            try:
                start, end = locate_span(unit["text"], issue)
            except ValueError as error:
                unit_errors.append(str(error))
                continue
            issues.append(dict(issue, span_start=start, span_end=end,
                               evidence=support(issue.get("evidence", []), allow_scope=True)))
            if "REF" in labels:
                citation_status = "invalid"
        claims = review.get("claims", [])
        if not claims:
            raise ValueError("核验缺少对子陈述的检查")
        normalized = []
        claim_positions = set()
        for index, claim in enumerate(claims):
            span = claim.get("span", "")
            cv = claim.get("verdict")
            if cv not in {"supported", "contradicted", "insufficient"}:
                raise ValueError("子陈述结论无效")
            try:
                start, end = locate_span(unit["text"], claim)
            except ValueError as error:
                unit_errors.append(str(error))
                request_read(claim, index, str(error))
                continue
            if (start, end) in claim_positions:
                unit_errors.append("同一陈述位置被重复核验，无法区分原稿中的不同陈述")
                continue
            claim_positions.add((start, end))
            previous_errors = len(unit_errors)
            kind = claim.get("kind", "fact")
            if kind not in {"fact", "source_absence", "analysis_result", "hypothesis"}:
                unit_errors.append("陈述kind无效")
            absence = kind == "source_absence"
            if absence and not claim.get("missing_information"):
                unit_errors.append("缺省判断未说明本次材料缺少什么")
            items = claim.get("evidence", [])
            scopes = []
            if not absence and isinstance(items, list):
                passages, scope_items = [], []
                for item in items:
                    if isinstance(item, dict) and item.get("scope") == "whole_source":
                        scope_items.append({k: v for k, v in item.items() if k != "segment_id"})
                        if item.get("segment_id"):
                            passages.append({k: v for k, v in item.items() if k != "scope"})
                    else:
                        passages.append(item)
                supports = support(passages)
                scopes = support(scope_items, allow_scope=True)
                if scopes:
                    scope_gaps.append({"claim_index": index, "span": span,
                                       "reason": claim.get("missing_information") or claim.get("reason", ""),
                                       "evidence": scopes,
                                       "scope": "仅本次提供材料；全文范围不能代替肯定事实的片段依据"})
            else:
                supports = support(items, allow_scope=absence)
            derived = computed(claim.get("analysis_refs", []))
            if kind == "fact" and derived:
                unit_errors.append("计算结果须标为analysis_result或hypothesis，不能当作原始实验事实")
            if absence and derived:
                unit_errors.append("原文材料缺省须引用实际检查范围，不能用分析输出代替")
            if kind == "analysis_result" and not derived:
                unit_errors.append("分析陈述缺少analysis_refs输出位置")
            if derived and numeric_tokens(span):
                binding = claim.get("binding")
                for result in derived:
                    basis = result["calculation_basis"]
                    required = {"analysis_unit", "unit", "period", "population", "denominator"}
                    selected_binding = binding.get(result["analysis_id"], binding) if isinstance(binding, dict) else None
                    if (required - basis.keys() or not isinstance(selected_binding, dict)
                            or basis.keys() - selected_binding.keys()
                            or any(selected_binding[k] != v for k, v in basis.items())):
                        unit_errors.append("分析数字缺少一致的单位、期间、总体、分母和分析单位绑定")
                        break
                selected_values = json.dumps([{ "value": r["value"], "basis": r["calculation_basis"]}
                                              for r in derived], ensure_ascii=False)
                available = {number_value(t) for t in numeric_tokens(selected_values)}
                for token in numeric_tokens(span):
                    if number_value(token) not in available and token not in unit["numeric_hints"]:
                        unit["numeric_hints"].append(token)
            if cv in {"supported", "contradicted"} and not (supports or derived):
                unit_errors.append("核验判断缺少原文位置或分析输出：" + span)
            if len(unit_errors) > previous_errors:
                cv = "insufficient"
                request_read(claim, index, "；".join(unit_errors[previous_errors:]))
            # Version changes and unconfirmed-source status are independent of model confidence.
            cited_refs = [{"evidence_id": reference_target(k, lookup)[0]}
                          for k in claim_citations(unit["text"], start, end)]
            policy_claim = dict(claim, analysis_refs=derived)
            gaps, support_scope = check_source_use(policy_claim, supports, analyses, source, cited_refs=cited_refs)
            if gaps:
                source_gaps.extend(dict(g, claim_index=index, span=span) for g in gaps)
                if cv != "contradicted":
                    cv = "insufficient"
            local_status = None
            if cv == "supported":
                local_targets = [reference_target(k, lookup) for k in claim_citations(unit["text"], start, end)]
                missing_passage = any(
                    not any(key == e["evidence_id"] and (part is None or part == e.get("segment_id"))
                            for key, part in local_targets)
                    for e in supports)
                if absence:
                    # Scope can cover a whole package, but the sentence's citation
                    # must still identify at least one source actually inspected.
                    missing_passage = not any(
                        key == e["evidence_id"] and (part is None or e.get("scope") == "whole_source"
                                                     or part == e.get("segment_id"))
                        for key, part in local_targets for e in supports)
                local_refs = {key for key, part in local_targets}
                missing_analysis = any(e["analysis_id"] not in local_refs for e in derived)
                if missing_passage or missing_analysis:
                    issues.append({"span": span, "span_start": start, "span_end": end,
                                   "labels": ["REF"], "evidence": supports,
                                   **({"analysis_refs": derived} if derived else {}),
                                   "reason": "本句引用与核验依据不一致"})
                    # A citation error is not a medical or scientific contradiction.
                    citation_status = local_status = "invalid"
            normalized_claim = dict(claim, span_start=start, span_end=end, verdict=cv, evidence=supports)
            if scopes:
                normalized_claim["scope_evidence"] = scopes
            if derived:
                normalized_claim["analysis_refs"] = derived
            if local_status:
                normalized_claim["citation_status"] = local_status
            if support_scope:
                normalized_claim["support_scope"] = support_scope
            if gaps:
                normalized_claim["source_gaps"] = gaps
            normalized_claim["read_requests"] = [x for x in requests if x.get("claim_index") == index]
            if cv == "insufficient" and not normalized_claim["read_requests"] and not gaps:
                normalized_claim["read_requests"] = read_requests(
                    supports + cited_refs, catalog, span, reason=claim.get("reason", "需要补充支持材料"))
            normalized.append(normalized_claim)
        if any(c["verdict"] == "contradicted" for c in normalized):
            verdict = "contradicted"
        elif any(c["verdict"] == "insufficient" for c in normalized):
            verdict = "insufficient"
        elif verdict == "contradicted":
            verdict = "insufficient"
        cited = [reference_target(k, lookup)[0] for k in CITATION.findall(unit["text"])]
        # A package-wide absence can legitimately inspect sources not cited one by one.
        used = {e["evidence_id"] for c in normalized if c["verdict"] == "supported"
                for e in c["evidence"] if e.get("scope") != "whole_source"}
        if used - set(cited):
            issues.append({"span": unit["text"], "labels": ["REF"],
                           "reason": "支持依据未绑定到本段的实际引用：" + ", ".join(sorted(used - set(cited))),
                           "evidence": []})
            citation_status = "invalid"
        unknown = sorted(set(cited) - lookup.keys() - analyses.keys())
        if unknown:
            issues.append({"span": unit["text"], "labels": ["REF"],
                           "reason": "未知引用：" + ", ".join(unknown), "evidence": []})
            citation_status = "invalid"
        if not cited:
            issues.append({"span": unit["text"], "labels": ["REF"],
                           "reason": "本段没有绑定原文或分析引用", "evidence": []})
            citation_status = "missing"
            if verdict == "supported":
                verdict = "insufficient"
        resolutions = {}
        for item in review.get("numeric_resolutions", []):
            token = item.get("token")
            if token in unit["numeric_hints"] and item.get("reason"):
                supports = support(item.get("evidence", []))
                derived = computed(item.get("analysis_refs", []))
                if derived:
                    calc = item.get("calculation", {})
                    if (not isinstance(calc, dict) or not isinstance(calc.get("expression"), str)
                            or not calc["expression"].strip()
                            or {"unit", "period", "population", "denominator"} - calc.keys()):
                        unit_errors.append("分析换算缺少计算式、单位、期间、总体或分母")
                        continue
                    # A changed output unit can be an explicit conversion; the
                    # source period, population, denominator and analysis unit cannot.
                    context = ("period", "population", "denominator", "analysis_unit")
                    if any(k in result["calculation_basis"]
                           and (k not in calc or calc[k] != result["calculation_basis"][k])
                           for result in derived for k in context):
                        unit_errors.append("分析换算与所引结果的期间、总体、分母或分析单位不一致")
                        continue
                if supports or derived:
                    resolutions[token] = dict(item, evidence=supports,
                                              **({"analysis_refs": derived} if derived else {}))
        unresolved = [t for t in unit["numeric_hints"] if t not in resolutions]
        if unresolved and verdict == "supported":
            verdict = "insufficient"
            issues.append({"span": unit["text"], "labels": ["NUM"],
                           "reason": "下列数字尚缺原文、分析输出或换算解释：" + ", ".join(unresolved),
                           "evidence": []})
        if issues and verdict == "supported":
            verdict = "insufficient"
        if unit_errors:
            if not any(c["verdict"] == "contradicted" for c in normalized):
                verdict = "insufficient"
            verification_errors.append({"unit_id": unit["id"], "errors": unit_errors})
        item = {"unit_id": unit["id"], "text": unit["text"], "start": unit["start"], "end": unit["end"],
                "verdict": verdict, "reason": review.get("reason", ""), "claims": normalized,
                "issues": issues, "numeric_hints": unit["numeric_hints"],
                "numeric_resolutions": list(resolutions.values())}
        if citation_status:
            item["citation_status"] = citation_status
        if scope_gaps:
            item["scope_gaps"] = scope_gaps
        if source_gaps:
            item["source_gaps"] = source_gaps
        if requests:
            item["read_requests"] = requests
        results.append(item)
        if verdict != "supported" or citation_status == "invalid":
            problems.append(unit["id"])
    coverage = response.get("coverage", {})
    if coverage.get("verdict") not in {"complete", "incomplete", "uncertain"}:
        raise ValueError("核验结果缺少任务覆盖评价")
    status = "FAIL" if any(x["verdict"] == "contradicted" or x.get("citation_status") == "invalid" for x in results) else (
        "REVIEW" if problems or coverage["verdict"] != "complete" else "PASS")
    all_requests = []
    for unit in results:
        candidates = list(unit.get("read_requests", []))
        candidates += [dict(r, unit_id=unit["unit_id"], claim_index=i)
                       for i, c in enumerate(unit["claims"]) for r in c.get("read_requests", [])]
        candidates += [dict(r, unit_id=unit["unit_id"], claim_index=g.get("claim_index"))
                       for g in unit.get("source_gaps", []) for r in g.get("read_requests", [])]
        for request in candidates:
            if request not in all_requests:
                all_requests.append(request)
    return {"status": status, "draft": draft, "units": results, "problem_units": problems,
            "coverage": coverage, "verification_errors": verification_errors,
            "raw_response": deepcopy(response), "evidence_catalog": catalog,
            "source_updates": source_updates(source), "analysis_signature": analysis_signature(source),
            "read_requests": all_requests,
            "scope": "固定材料内的机器核验；段落覆盖由程序检查，子声明语义由模型判断；分析仅核对给定输出，复算及总体构建由Radar负责"}


def review_locations_match(review):
    draft, saved = review.get("draft"), review.get("units", [])
    if not isinstance(draft, str) or not isinstance(saved, list):
        return False
    actual = split_units(draft)
    return len(actual) == len(saved) and all(
        u.get("unit_id") == v["id"] and all(u.get(k) == v[k] for k in ("text", "start", "end"))
        for u, v in zip(saved, actual))


def audit_support(packet, review, call, *, targets=None):
    """Check selected passages only. This opt-in call never edits a draft or its old review."""
    source = packet_input(packet)
    lookup = {e["id"]: e for e in source["evidence"]}
    requested = None if targets is None else set(targets)
    found, items, skipped, errors = set(), [], [], []
    result = {"status": "NOT_RUN", "original_status": review["status"], "checks": [],
              "skipped": skipped, "verification_errors": errors,
              "draft": review["draft"], "evidence_catalog": evidence_catalog(source),
              "source_updates": source_updates(source), "analysis_signature": analysis_signature(source),
              "scope": "仅复核checks列出的陈述及所选片段；PASS不代表整稿通过，不判断事实整体真伪，不自动修订正文"}
    if (not review_locations_match(review)
            or review.get("evidence_catalog") != evidence_catalog(source)
            or review.get("source_updates", []) != source_updates(source)
            or review.get("analysis_signature") != analysis_signature(source)):
        errors.append({"reason": "来源身份或状态已变化，须先重新核验整稿"})
        result["status"] = "REVIEW"
        return result
    if review.get("verification_errors"):
        result.update(status="REVIEW", verification_errors=review["verification_errors"])
        return result
    for unit in review["units"]:
        for index, claim in enumerate(unit["claims"]):
            key = (unit["unit_id"], index)
            found.add(key)
            if requested is not None and key not in requested:
                continue
            identity = {"unit_id": key[0], "claim_index": index}
            if (claim.get("kind", "fact") != "fact" or claim["verdict"] != "supported"
                    or claim.get("scope_evidence") or claim.get("support_scope")):
                skipped.append(dict(identity, reason="只复核已判支持的纯事实选段；材料缺省及含全文范围的复合陈述须另查范围"))
                continue
            try:
                start, end = locate_span(unit["text"], claim)
                selected = claim.get("evidence", [])
                passages = locate_support(selected, lookup)
                if not passages:
                    raise ValueError("缺少所选片段")
                for saved, actual in zip(selected, passages):
                    for field in ("version", "start", "end", "quote"):
                        if field in saved and saved[field] != actual[field]:
                            raise ValueError("保存的片段与当前固定材料不一致：" + field)
                for passage in passages:
                    source = lookup[passage["evidence_id"]]
                    passage["source_identity"] = {k: source[k] for k in ("title", "record_id") if k in source}
                    if source.get("text_basis") in {"prior_report", "retrieval_log"}:
                        passage["source_identity"]["text_basis"] = source["text_basis"]
                items.append(dict(identity, span=claim["span"], span_start=start, span_end=end,
                                  context=unit["text"], evidence=passages))
            except ValueError as error:
                errors.append(dict(identity, reason=str(error)))
    if requested is not None and requested - found:
        raise ValueError("复核目标不存在")
    if errors:
        result["status"] = "REVIEW"
        return result
    if not items:
        return result
    response = call("support_audit", SUPPORT_AUDIT, {"claims": items})
    result["raw_response"] = deepcopy(response)
    checks = response.get("checks", [])
    expected = {(x["unit_id"], x["claim_index"]) for x in items}
    valid_identity = isinstance(checks, list) and all(
        isinstance(c, dict) and isinstance(c.get("unit_id"), str)
        and type(c.get("claim_index")) is int for c in checks)
    if (not valid_identity or len(checks) != len(items)
            or {(c["unit_id"], c["claim_index"]) for c in checks} != expected):
        errors.append({"reason": "片段复核回执缺项、重复或目标不一致"})
        result["status"] = "REVIEW"
        return result
    by_id = {(c["unit_id"], c["claim_index"]): c for c in checks}
    for item in items:
        answer = by_id[(item["unit_id"], item["claim_index"])]
        verdict, reason = answer.get("verdict"), answer.get("reason")
        supports = answer.get("support", [])
        try:
            if (not isinstance(verdict, str) or verdict not in {"supported", "not_supported", "uncertain"}
                    or not isinstance(reason, str) or not reason.strip()):
                raise ValueError("片段复核缺少有效结论或理由")
            if not isinstance(supports, list) or (verdict == "supported" and not supports):
                raise ValueError("支持结论须摘录所选片段中的原文")
            selected = {(p["evidence_id"], p["segment_id"]): p for p in item["evidence"]}
            for support in supports:
                if not isinstance(support, dict) or not all(isinstance(support.get(k), str) for k in ("evidence_id", "segment_id")):
                    raise ValueError("复核摘录格式无效")
                passage = selected.get((support.get("evidence_id"), support.get("segment_id")))
                quote = support.get("quote")
                if passage is None or not isinstance(quote, str) or not quote.strip() or quote not in passage["quote"]:
                    raise ValueError("复核摘录不在本条所选片段中")
        except ValueError as error:
            errors.append({"unit_id": item["unit_id"], "claim_index": item["claim_index"], "reason": str(error)})
            verdict = "uncertain"
        result["checks"].append(dict(item, verdict=verdict, reason=reason, support=supports))
    result["status"] = "PASS" if not errors and all(c["verdict"] == "supported" for c in result["checks"]) else "REVIEW"
    return result



def quality_feedback(review, audit=None, *, source_errors=()):
    """Route existing findings; choosing queries, stopping and version storage stay with Radar."""
    review = review or {}
    gaps, waiting, candidates, blocked = [], [], [], set()
    result = {"machine_status": review.get("status"), "evidence_gaps": gaps,
              "repair_units": [], "review_required": waiting,
              "audit_status": audit.get("status") if audit is not None else "not_requested"}
    if review.get("status") not in {"PASS", "REVIEW", "FAIL"} or not review.get("units"):
        waiting.append({"kind": "verification_unavailable", "reason": "没有完整成稿核验回执"})
    if review.get("units") and not review_locations_match(review):
        waiting.append({"kind": "review_mismatch", "reason": "核验段落与完整稿件不一致，须重核"})
    waiting.extend({"kind": "source_failure", "detail": x} for x in source_errors)
    errors = review.get("verification_errors", [])
    waiting.extend({"kind": "verification_error", "detail": x} for x in errors)
    invalid_units = {x.get("unit_id") for x in errors}
    units = {u["unit_id"]: u for u in review.get("units", [])}
    catalog = review.get("evidence_catalog", [])

    def gap(kind, unit_id, reason, **extra):
        gaps.append(dict(kind=kind, unit_id=unit_id, reason=reason, **extra))
        blocked.add(unit_id)

    if audit is not None:
        if (audit.get("draft") != review.get("draft")
                or audit.get("evidence_catalog") != review.get("evidence_catalog")
                or audit.get("source_updates", []) != review.get("source_updates", [])
                or audit.get("analysis_signature") != review.get("analysis_signature")):
            waiting.append({"kind": "audit_mismatch", "reason": "片段复核属于另一份完整稿件或来源状态"})
            audit = None
    if audit is not None:
        waiting.extend({"kind": "audit_error", "detail": x} for x in audit.get("verification_errors", []))
        if audit.get("status") not in {"PASS", "REVIEW", "NOT_RUN"}:
            waiting.append({"kind": "audit_error", "reason": "片段复核没有有效状态"})
        for check in audit.get("checks", []):
            if (audit.get("verification_errors") or audit.get("status") not in {"PASS", "REVIEW"}
                    or check.get("unit_id") in invalid_units or None in invalid_units):
                continue
            unit = units.get(check.get("unit_id"))
            index = check.get("claim_index")
            if unit is None or type(index) is not int or not 0 <= index < len(unit.get("claims", [])):
                waiting.append({"kind": "audit_mismatch", "reason": "片段复核不属于本稿陈述"})
                continue
            claim = unit["claims"][index]
            fields = ("evidence_id", "version", "segment_id", "start", "end", "quote")
            same_support = ([{k: e.get(k) for k in fields} for e in claim.get("evidence", [])]
                            == [{k: e.get(k) for k in fields} for e in check.get("evidence", [])])
            if check.get("span") != claim["span"] or check.get("context") != unit["text"] or not same_support:
                waiting.append({"kind": "audit_mismatch", "reason": "片段复核的正文或选段已经变化"})
            elif audit.get("verification_errors"):
                continue
            elif check.get("verdict") == "not_supported":
                gap("evidence_selection", unit["unit_id"], check.get("reason", ""),
                    claim_index=index, span=claim["span"], evidence=check.get("evidence", []),
                    read_requests=read_requests(check.get("evidence", []), catalog, claim["span"],
                                                reason=check.get("reason", "")),
                    action="reselect_or_retrieve")
            elif check.get("verdict") != "supported":
                waiting.append({"kind": "audit_uncertain", "unit_id": unit["unit_id"],
                                "claim_index": index, "reason": check.get("reason", "")})

    for unit_id, unit in units.items():
        if unit.get("read_requests"):
            gap("source_position", unit_id, "核验缺少可用定位；先补读或修复回执，再重核原稿",
                read_requests=unit["read_requests"], action="locate_then_verify")
        for finding in unit.get("source_gaps", []):
            if finding.get("repair_supported") and unit_id not in invalid_units:
                candidates.append(unit_id)
            else:
                gap(finding["kind"], unit_id, finding["reason"],
                    **{k: v for k, v in finding.items() if k not in {"kind", "reason"}})
        if unit_id in invalid_units or None in invalid_units:
            continue
        claims = unit.get("claims", [])
        for scoped in unit.get("scope_gaps", []):
            gap("material_absence", unit_id, scoped.get("reason", ""),
                claim_index=scoped.get("claim_index"), span=scoped.get("span", ""),
                evidence=scoped.get("evidence", []), action="assess_need_for_retrieval")
        for index, claim in enumerate(claims):
            if claim.get("verdict") == "insufficient" and not claim.get("source_gaps"):
                gap("missing_support", unit_id, claim.get("reason", ""),
                    claim_index=index, span=claim["span"],
                    read_requests=claim.get("read_requests") or read_requests(
                        claim.get("evidence", []), catalog, claim["span"], reason=claim.get("reason", "")),
                    action="check_existing_then_retrieve")
            elif (claim.get("verdict") == "supported" and claim.get("kind") == "source_absence"
                  and claim.get("missing_information")):
                gap("material_absence", unit_id, claim["missing_information"],
                    claim_index=index, span=claim["span"], action="assess_need_for_retrieval")
            elif claim.get("verdict") == "contradicted" and (claim.get("evidence") or claim.get("analysis_refs")):
                candidates.append(unit_id)
        for issue in unit.get("issues", []):
            if "REF" in issue.get("labels", []) and (issue.get("evidence") or issue.get("analysis_refs")):
                candidates.append(unit_id)
        if unit_id in review.get("problem_units", []) and unit_id not in blocked and unit_id not in candidates:
            waiting.append({"kind": "unresolved_check", "unit_id": unit_id,
                            "reason": unit.get("reason", "缺少可安全修订的原文依据")})
    coverage = review.get("coverage", {})
    if coverage.get("verdict") in {"incomplete", "uncertain"}:
        gap("coverage", None, coverage.get("reason", ""), action="check_scope_and_existing_evidence")
    # A whole paragraph edit can alter a correct-but-unproven statement in that paragraph.
    if not waiting:
        result["repair_units"] = list(dict.fromkeys(u for u in candidates if u not in blocked))
    return result


def revise(packet, review, call, *, audit=None, source_errors=()):
    source = packet_input(packet)
    units = review["units"]
    feedback = quality_feedback(review, audit, source_errors=source_errors)
    if (review.get("evidence_catalog") != evidence_catalog(source)
            or review.get("source_updates", []) != source_updates(source)
            or review.get("analysis_signature") != analysis_signature(source)):
        feedback["review_required"].append({"kind": "source_changed", "reason": "补读或来源状态变化后须先重核原稿"})
        feedback["repair_units"] = []
    editable = [u for u in units if u["unit_id"] in feedback["repair_units"]]
    if not editable:
        return {"draft": review["draft"], "changed_units": [], "feedback": feedback, "requires_verification": False,
                "reason": "没有可安全自动修订的段落；补查与待核问题见feedback"}
    request = dict(source, draft=review["draft"], editable_units=editable)
    result = call("revise", REVISE, model_input(request, numbered=False))
    replacements = result.get("replacements", [])
    if len(replacements) != len(editable) or {r.get("unit_id") for r in replacements} != {u["unit_id"] for u in editable}:
        raise ValueError("修订没有严格对应允许修改的段落")
    mapped = {r["unit_id"]: r["text"] for r in replacements}
    draft = review["draft"]
    changes = []
    for unit in reversed(units):
        if unit["unit_id"] in mapped:
            text = mapped[unit["unit_id"]]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("不能用删除整段代替修订")
            draft = draft[:unit["start"]] + text + draft[unit["end"]:]
            if text != unit["text"]:
                changes.append({"unit_id": unit["unit_id"], "before": unit["text"], "after": text})
    return {"draft": draft, "changed_units": list(reversed(changes)), "requires_verification": bool(changes)}


def run(packet, call, method="bound", *, repair=True):
    generated = generate(packet, call, method)
    review = verify(packet, generated["draft"], call)
    result = {"generated": generated, "initial_review": review, "final_review": review, "revision": None}
    result["initial_feedback"] = quality_feedback(review)
    if repair and result["initial_feedback"]["repair_units"]:
        revision = revise(packet, review, call)
        result["revision"] = revision
        if revision["changed_units"]:
            result["final_review"] = verify(packet, revision["draft"], call)
    result["feedback"] = quality_feedback(result["final_review"])
    result["final_text"] = result["final_review"]["draft"]
    result["status"] = result["final_review"]["status"]
    return result
