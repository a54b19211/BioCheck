"""Keep source changes and unconfirmed reports separate from factual support."""
import re
from copy import deepcopy

SOURCE_FIELDS = ("id", "version", "record_id", "title", "text_basis", "source_kind",
                 "verification_status", "source_context")


def evidence_catalog(packet):
    return [{k: deepcopy(e[k]) for k in SOURCE_FIELDS if k in e} for e in packet["evidence"]]


def source_updates(packet):
    updates = packet.get("assessment", {}).get("source_updates", [])
    if not isinstance(updates, list):
        raise ValueError("source_updates须为列表")
    for source in packet["evidence"]:
        for field in ("source_kind", "verification_status"):
            if field in source and not isinstance(source[field], str):
                raise ValueError(field + "须为字符串")
        if "source_context" in source and not isinstance(source["source_context"], dict):
            raise ValueError("source_context须为对象")
    for update in updates:
        if not isinstance(update, dict) or update.get("kind") not in {"correction", "retraction"}:
            raise ValueError("来源变化须标明correction或retraction")
        for key in ("target", "replacement"):
            ref = update.get(key)
            if not isinstance(ref, dict) or not all(isinstance(ref.get(k), str) and ref[k].strip()
                                                   for k in ("record_id", "version")):
                raise ValueError("来源变化的两端须有record_id和version")
        if identity(update["target"]) == identity(update["replacement"]):
            raise ValueError("更正/撤回通知与被替代资料须为不同的固定来源")
    return deepcopy(updates)


def identity(source):
    return (source.get("record_id", source.get("source_record_id")),
            source.get("version", source.get("version_id")))


def read_requests(refs, catalog, span, *, reason="需重新定位支持该陈述的原文"):
    """Describe what to read. Never turn a guessed source or segment into support."""
    lookup = {e["id"]: e for e in catalog}
    result = []
    for ref in refs:
        key = ref.get("evidence_id")
        source = lookup.get(key, {})
        request = {"evidence_id": key, "record_id": source.get("record_id", ref.get("record_id")),
                   "version": source.get("version", ref.get("version")),
                   "needed_for": span, "reason": reason, "action": "locate_support"}
        if ref.get("segment_id"):
            request["requested_segment_id"] = ref["segment_id"]
        if ref.get("version") and source and ref["version"] != source["version"]:
            request["requested_version"] = ref["version"]
        if not request["record_id"]:
            request["target_status"] = "record_identity_missing"
        if request not in result:
            result.append(request)
    if not result:
        result.append({"evidence_id": None, "record_id": None, "version": None,
                       "needed_for": span, "reason": reason, "action": "identify_source",
                       "target_status": "unknown"})
    return result


def _anchor(claim, field, pattern):
    value = claim.get(field)
    return (isinstance(value, str) and bool(value.strip()) and value in claim["span"]
            and re.search(pattern, value, re.I) is not None)


def check_source_use(claim, refs, analyses, packet, *, cited_refs=()):
    """Check explicit source status; the model still judges the substantive meaning."""
    lookup = {e["id"]: e for e in packet["evidence"]}
    selected = [lookup[r["evidence_id"]] for r in refs if r.get("evidence_id") in lookup]
    derived = claim.get("analysis_refs", [])
    input_sources = []
    for ref in derived:
        for item in analyses[ref["analysis_id"]]["input_refs"]:
            if isinstance(item, str):
                input_sources.append(lookup[item])
            else:
                input_sources.append(lookup.get(item.get("evidence_id"), item))
    cited = [lookup[r["evidence_id"]] for r in cited_refs if r.get("evidence_id") in lookup]
    identities = {identity(e) for e in selected + cited + input_sources}
    updates = [u for u in source_updates(packet) if identity(u["target"]) in identities]
    gaps = []
    for update in updates:
        replacement = update["replacement"]
        loaded = [e for e in selected if identity(e) == identity(replacement)]
        history = claim.get("history_context", {})
        history = history if isinstance(history, dict) else {}
        anchored = {"span": claim["span"], **{k: history.get(k) for k in ("then_span", "update_span")}}
        historical = (claim.get("source_use") == "historical"
                      and _anchor(anchored, "then_span", r"当时|此前|原先|早先|曾披露|曾报告|最初|initial|previous|original|at the time")
                      and _anchor(anchored, "update_span", r"已更正|后更正|已修订|后修订|已撤回|已撤稿|was corrected|was retracted|was withdrawn")
                      and bool(loaded)
                      and any(identity(e) == identity(update["target"]) for e in selected))
        if not historical:
            target = dict(replacement)
            candidates = [e for e in packet["evidence"] if identity(e) == identity(replacement)]
            if candidates:
                target["evidence_id"] = candidates[0]["id"]
            requests = read_requests([target], evidence_catalog(packet), claim["span"],
                                     reason="读取更正/撤回通知，再核对旧数的适用范围")
            gaps.append({"kind": "source_updated", "reason": "所用来源已更正或撤回，不能直接作为当前事实",
                         "update": update, "read_requests": requests,
                         "action": "read_update_then_verify",
                         "repair_supported": bool(loaded) and claim.get("verdict") == "contradicted"
                                             and claim.get("kind", "fact") == "fact"})

    relevant = selected + cited + input_sources
    unconfirmed = [e for e in relevant
                   if e.get("verification_status") == "unverified"
                   or e.get("source_kind") in {"social_media", "rumor"}]
    environment = [e for e in relevant
                   if isinstance(e.get("source_context"), dict)
                   and e["source_context"].get("information_environment_event")]
    scope = None
    if unconfirmed or environment:
        mode = claim.get("source_use")
        attributed = _anchor(claim, "attribution_span", r"网传|据称|传言|帖文|帖子|贴文|发帖|通报|声明|报告称|reported|post|alleged|rumou?r")
        uncertain = _anchor(claim, "uncertainty_span", r"未证实|未核实|待核|待验证|尚待|传言|据称|疑似|假设|unconfirmed|unverified|alleged|hypothes")
        event_only = (bool(environment) and not unconfirmed and mode == "information_environment" and _anchor(
            claim, "attribution_span", r"删帖|删除|辟谣|处罚|澄清|删除通知|delet|denial|penalt|censor"))
        hypothesis = claim.get("kind") == "hypothesis" and mode == "hypothesis" and uncertain
        attributed_report = mode == "attributed_report" and attributed and uncertain
        if environment and len(environment) == len(relevant) and not (event_only or attributed_report):
            gaps.append({"kind": "information_environment_only",
                         "reason": "删帖、辟谣或处罚仅说明信息环境，不能单独证实事件或提高疾病风险"})
        elif not (event_only or attributed_report or hypothesis):
            gaps.append({"kind": "unconfirmed_source",
                         "reason": "未证实来源须明确转述者、待核状态或假设，不得作为已确认事实"})
        scope = "information_environment_only" if event_only else (
            "hypothesis_only" if hypothesis else "attribution_only")
    return gaps, scope
