"""Evidence-bound writing and verification. No network or database calls here."""
import re
from decimal import Decimal

from .prompts import WRITE, PLAN, BOUND_PLAN, VERIFY, REVISE, SUPPORT_AUDIT
from .evidence import model_input, locate_support, reference_target

CITATION = re.compile(r"\[([A-Za-z][A-Za-z0-9_.:-]*)\]")
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-−]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)%?")
LABELS = {"OBJ", "NUM", "TIME", "COND", "STR", "REF"}


def packet_input(packet):
    if not isinstance(packet.get("question"), str) or not packet["question"].strip():
        raise ValueError("question不能为空")
    evidence = packet.get("evidence", [])
    ids = set()
    for item in evidence:
        key = item["id"]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", key) or key in ids:
            raise ValueError("证据id须唯一，并能直接作为引用")
        ids.add(key)
        if not item.get("text", "").strip() or not item.get("version"):
            raise ValueError("每份证据须有text和version")
    if not evidence:
        raise ValueError("需要至少一份原文证据")
    # Explicitly select model input; evaluation labels never enter model requests.
    return {k: packet[k] for k in ("question", "evidence", "assessment") if k in packet}


def generate(packet, call, method="bound"):
    source = packet_input(packet)
    plan = None
    if method not in {"direct", "plain", "bound"}:
        raise ValueError("method须为direct/plain/bound")
    if method != "direct":
        plan = call("plan_" + method, BOUND_PLAN if method == "bound" else PLAN, model_input(source))
        points = plan.get("points", [])
        if not points:
            raise ValueError("生成计划为空")
        lookup = {e["id"]: e for e in source["evidence"]}
        for point in points:
            refs = point.get("evidence_ids", [])
            if not refs or any(key not in lookup for key in refs):
                raise ValueError("计划缺少有效依据")
            if method == "bound":
                supports = locate_support(point.get("support", []), lookup)
                if not supports or any(x["evidence_id"] not in refs for x in supports):
                    raise ValueError("绑定计划缺少对应原文")
                if not isinstance(point.get("binding"), dict):
                    raise ValueError("绑定计划缺少binding")
    request = dict(source)
    if plan is not None:
        request["plan"] = {"points": [{k: v for k, v in point.items() if k != "support"}
                                     for point in plan["points"]]}
    output = call("write_" + method, WRITE, model_input(request, numbered=False))
    draft = output.get("draft")
    if not isinstance(draft, str) or not draft.strip():
        raise ValueError("生成结果缺少非空draft")
    return {"method": method, "plan": plan, "draft": draft}


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


def numeric_hints(unit, evidence):
    refs = [reference_target(k, evidence)[0] for k in CITATION.findall(unit["text"])]
    refs = [k for k in refs if k in evidence]
    text = " ".join(evidence[k]["text"] for k in refs)
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


def verify(packet, draft, call):
    source = packet_input(packet)
    if not isinstance(draft, str) or not draft.strip():
        raise ValueError("待核稿不能为空")
    units = split_units(draft)
    lookup = {e["id"]: e for e in source["evidence"]}
    model_units = [dict(u, numeric_hints=numeric_hints(u, lookup)) for u in units]
    response = call("verify", VERIFY, model_input(dict(source, draft=draft, units=model_units)))
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
        issues = []
        unit_errors = []

        def support(items, *, allow_scope=False):
            try:
                return locate_support(items, lookup, allow_scope=allow_scope)
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
        claims = review.get("claims", [])
        if not claims:
            raise ValueError("核验缺少对子陈述的检查")
        normalized = []
        claim_positions = set()
        for claim in claims:
            span = claim.get("span", "")
            cv = claim.get("verdict")
            if cv not in {"supported", "contradicted", "insufficient"}:
                raise ValueError("子陈述结论无效")
            try:
                start, end = locate_span(unit["text"], claim)
            except ValueError as error:
                unit_errors.append(str(error))
                continue
            if (start, end) in claim_positions:
                unit_errors.append("同一陈述位置被重复核验，无法区分原稿中的不同陈述")
                continue
            claim_positions.add((start, end))
            previous_errors = len(unit_errors)
            absence = claim.get("kind") == "source_absence"
            if absence and not claim.get("missing_information"):
                unit_errors.append("缺省判断未说明本次材料缺少什么")
            supports = support(claim.get("evidence", []), allow_scope=absence)
            if cv in {"supported", "contradicted"} and not supports:
                unit_errors.append("核验判断缺少原文位置：" + span)
            if len(unit_errors) > previous_errors:
                cv = "insufficient"
            if cv == "supported":
                used_refs = {e["evidence_id"] for e in supports}
                local_targets = [reference_target(k, lookup) for k in claim_citations(unit["text"], start, end)]
                local_refs = {key for key, part in local_targets}
                wrong_part = any(
                    not any(key == e["evidence_id"] and (part is None or part == e.get("segment_id"))
                            for key, part in local_targets)
                    for e in supports)
                if used_refs - local_refs or wrong_part:
                    issues.append({"span": span, "span_start": start, "span_end": end,
                                   "labels": ["REF"], "evidence": supports,
                                   "reason": "本句引用与核验依据不一致"})
                    cv = "contradicted"
            normalized.append(dict(claim, span_start=start, span_end=end, verdict=cv, evidence=supports))
        if any(c["verdict"] == "contradicted" for c in normalized):
            verdict = "contradicted"
        elif any(c["verdict"] == "insufficient" for c in normalized) and verdict == "supported":
            verdict = "insufficient"
        cited = [reference_target(k, lookup)[0] for k in CITATION.findall(unit["text"])]
        used = {e["evidence_id"] for c in normalized if c["verdict"] == "supported" for e in c["evidence"]}
        if used - set(cited):
            issues.append({"span": unit["text"], "labels": ["REF"],
                           "reason": "支持依据未绑定到本段的实际引用：" + ", ".join(sorted(used - set(cited))),
                           "evidence": []})
            verdict = "contradicted"
        unknown = sorted(set(cited) - lookup.keys())
        if unknown:
            issues.append({"span": unit["text"], "labels": ["REF"],
                           "reason": "未知引用：" + ", ".join(unknown), "evidence": []})
            verdict = "contradicted"
        if not cited:
            issues.append({"span": unit["text"], "labels": ["REF"],
                           "reason": "本段没有绑定原文引用", "evidence": []})
            if verdict == "supported":
                verdict = "insufficient"
        resolutions = {}
        for item in review.get("numeric_resolutions", []):
            token = item.get("token")
            if token in unit["numeric_hints"] and item.get("reason"):
                supports = support(item.get("evidence", []))
                if supports:
                    resolutions[token] = dict(item, evidence=supports)
        unresolved = [t for t in unit["numeric_hints"] if t not in resolutions]
        if unresolved and verdict == "supported":
            verdict = "insufficient"
            issues.append({"span": unit["text"], "labels": ["NUM"],
                           "reason": "下列数字尚缺原文或换算解释：" + ", ".join(unresolved),
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
        results.append(item)
        if verdict != "supported":
            problems.append(unit["id"])
    coverage = response.get("coverage", {})
    if coverage.get("verdict") not in {"complete", "incomplete", "uncertain"}:
        raise ValueError("核验结果缺少任务覆盖评价")
    status = "FAIL" if any(x["verdict"] == "contradicted" for x in results) else (
        "REVIEW" if problems or coverage["verdict"] != "complete" else "PASS")
    return {"status": status, "draft": draft, "units": results, "problem_units": problems,
            "coverage": coverage, "verification_errors": verification_errors, "scope": "固定材料内的机器核验；段落覆盖由程序检查，子声明语义由模型判断"}



def audit_support(packet, review, call, *, targets=None):
    """Check selected passages only. This opt-in call never edits a draft or its old review."""
    lookup = {e["id"]: e for e in packet_input(packet)["evidence"]}
    requested = None if targets is None else set(targets)
    found, items, skipped, errors = set(), [], [], []
    result = {"status": "NOT_RUN", "original_status": review["status"], "checks": [],
              "skipped": skipped, "verification_errors": errors,
              "scope": "仅复核checks列出的陈述及所选片段；PASS不代表整稿通过，不判断事实整体真伪，不自动修订正文"}
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
            if claim.get("kind", "fact") != "fact" or claim["verdict"] != "supported":
                skipped.append(dict(identity, reason="只复核已判支持的事实；材料缺省等判断须另查全文"))
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


def revise(packet, review, call):
    units = review["units"]
    editable = [u for u in units if u["unit_id"] in review["problem_units"]]
    if not editable:
        return {"draft": review["draft"], "changed_units": [], "reason": "无可定位修改项；覆盖缺口交回调查方"}
    request = dict(packet_input(packet), draft=review["draft"], editable_units=editable)
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
            changes.append({"unit_id": unit["unit_id"], "before": unit["text"], "after": text})
    return {"draft": draft, "changed_units": list(reversed(changes))}


def run(packet, call, method="bound", *, repair=True):
    generated = generate(packet, call, method)
    review = verify(packet, generated["draft"], call)
    result = {"generated": generated, "initial_review": review, "final_review": review, "revision": None}
    if repair and review["problem_units"] and not review["verification_errors"]:
        revision = revise(packet, review, call)
        result.update(revision=revision, final_review=verify(packet, revision["draft"], call))
    result["final_text"] = result["final_review"]["draft"]
    result["status"] = result["final_review"]["status"]
    return result
