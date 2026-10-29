"""Give original text stable locations within one supplied evidence version."""
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
        source = evidence.get(key)
        if source is None:
            raise ValueError("核验结果引用了未知证据")
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
