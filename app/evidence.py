"""Locate fixed source passages and supplied calculation outputs; perform no mining."""
import re


def segments(text, size=1200):
    result = []
    start = 0
    # Sentence boundaries retain every original character, including spaces.
    for match in re.finditer(r"[.!?。！？](?=\s|$)|\n+|\Z", text):
        end = match.end()
        if end <= start:
            continue
        for offset in range(start, end, size):
            stop = min(offset + size, end)
            result.append({"id": f"S{len(result)+1}", "start": offset,
                           "end": stop, "text": text[offset:stop]})
        start = end
    return result


def model_input(packet, *, numbered=True):
    evidence = []
    for source in packet["evidence"]:
        item = {k: source[k] for k in ("id", "version", "title", "record_id", "text_basis") if k in source}
        if numbered:
            item["segments"] = [{"id": s["id"], "text": s["text"]} for s in segments(source["text"])]
        else:
            item["text"] = source["text"]
        evidence.append(item)
    return dict(packet, evidence=evidence)


def locate_support(items, evidence, *, allow_scope=False):
    result = []
    for item in items:
        key = item.get("evidence_id")
        if not isinstance(key, str):
            raise ValueError("核验依据缺少evidence_id")
        source = evidence.get(key)
        if source is None:
            raise ValueError("核验结果引用了未知证据")
        if "version" in item and item["version"] != source["version"]:
            raise ValueError("核验依据与固定原文版本不一致")
        if item.get("scope") == "whole_source":
            if not allow_scope:
                raise ValueError("本步骤需要具体原文片段")
            result.append({"evidence_id": key, "version": source["version"],
                           "scope": "whole_source", "start": 0, "end": len(source["text"])})
            continue
        parts = {s["id"]: s for s in segments(source["text"])}
        part = parts.get(item.get("segment_id"))
        if part is None:
            raise ValueError(f"{key}的原文片段编号无效")
        result.append({"evidence_id": key, "version": source["version"],
                       "segment_id": part["id"], "quote": part["text"],
                       "start": part["start"], "end": part["end"]})
    return result


def reference_target(reference, evidence):
    """Accept source citations and explicit source:segment citations without rewriting them."""
    if reference in evidence:
        return reference, None
    match = re.fullmatch(r"(.+):(S\d+)", reference)
    if match and match[1] in evidence:
        valid = {s["id"] for s in segments(evidence[match[1]]["text"])}
        if match[2] in valid:
            return match[1], match[2]
    return reference, None


def analysis_results(packet):
    """Keep Radar's supplied results separate from original source text."""
    results = packet.get("assessment", {}).get("analysis_results", [])
    if not isinstance(results, list):
        raise ValueError("assessment.analysis_results须为列表")
    lookup = {}
    evidence = {e["id"]: e for e in packet.get("evidence", [])}
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("分析结果须为对象")
        key = item.get("id")
        if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", key)
                or key in lookup or key in evidence):
            raise ValueError("分析结果id须唯一且不与原文id重复")
        dataset = item.get("dataset_ref")
        if not isinstance(dataset, (str, dict)) or not dataset:
            raise ValueError("分析结果缺少固定dataset_ref")
        if not isinstance(item.get("method_version"), str) or not item["method_version"].strip():
            raise ValueError("分析结果缺少method_version")
        if not isinstance(item.get("parameters"), dict) or "output" not in item:
            raise ValueError("分析结果须保留parameters和output")
        refs = item.get("input_refs")
        if not isinstance(refs, list) or not refs:
            raise ValueError("分析结果缺少input_refs")
        for ref in refs:
            if isinstance(ref, str) and ref in evidence:
                continue
            if not isinstance(ref, dict) or not ref:
                raise ValueError("分析输入须引用本包原文id或带版本的来源身份")
            source = evidence.get(ref.get("evidence_id"))
            if source is not None:
                if ref.get("version", source["version"]) != source["version"]:
                    raise ValueError("分析输入与本包原文版本不一致")
            elif (not any(ref.get(k) for k in ("source_record_id", "record_id", "dataset_ref"))
                  or not (ref.get("version") or ref.get("version_id"))):
                raise ValueError("包外分析输入须保留来源身份和版本")
        if (not isinstance(item.get("limitations"), list)
                or not all(isinstance(x, str) for x in item["limitations"])):
            raise ValueError("分析结果须保留limitations列表")
        if "calculation_basis" in item and not isinstance(item["calculation_basis"], dict):
            raise ValueError("calculation_basis须为对象")
        lookup[key] = item
    return lookup


def locate_analysis(items, results):
    """Resolve a JSON pointer into a provided output, without recomputing it."""
    if not isinstance(items, list):
        raise ValueError("analysis_refs须为列表")
    located = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("分析引用须为对象")
        key = item.get("analysis_id")
        if not isinstance(key, str):
            raise ValueError("分析引用缺少analysis_id")
        result = results.get(key)
        if result is None:
            raise ValueError("核验引用了未知分析结果")
        path = item.get("output_path")
        if not isinstance(path, str) or (path and not path.startswith("/")):
            raise ValueError("分析引用须给出output_path JSON pointer")
        value = result["output"]
        try:
            for part in path.split("/")[1:] if path else []:
                if re.search(r"~(?![01])", part):
                    raise ValueError("无效JSON pointer转义")
                part = part.replace("~1", "/").replace("~0", "~")
                if isinstance(value, list):
                    if not re.fullmatch(r"0|[1-9][0-9]*", part):
                        raise ValueError("无效数组位置")
                    value = value[int(part)]
                elif isinstance(value, dict):
                    value = value[part]
                else:
                    raise ValueError("路径未指向分析输出")
        except (KeyError, IndexError, ValueError) as error:
            raise ValueError("分析输出位置无效：" + key + path) from error
        located.append({"analysis_id": key, "output_path": path, "value": value,
                        **{k: result[k] for k in ("dataset_ref", "method_version", "parameters",
                                                  "input_refs", "limitations")},
                        "calculation_basis": result.get("calculation_basis", {})})
    return located
