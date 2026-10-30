"""Offline handoff examples: python3 -m evaluation.replay_research_loop --output DIR."""
import argparse
import copy
import json
from pathlib import Path

from app.core import audit_support, quality_feedback, run, verify


def examples():
    folder = Path(__file__).parent
    case = json.loads((folder / "selected_support_case.json").read_text())
    saved = json.loads((folder / "selected_support_results.json").read_text())
    review = verify(case["packet"], case["draft"], lambda *args: copy.deepcopy(case["raw_output"]))
    raw_audit = saved["cases"][0]["raw_output"]
    audit = audit_support(case["packet"], review, lambda *args: copy.deepcopy(raw_audit),
                          targets=[("U1", 0), ("U1", 2)])
    feedback = quality_feedback(review, audit)
    gaps = {"qualification": "原模型回执和已保存的两条片段复核回执，零新增API调用。",
            "review": review, "support_audit": audit, "feedback": feedback}
    fixture = json.loads((folder / "research_loop_cases.json").read_text())
    reports = []
    first_snapshot = None
    for version in fixture["versions"]:
        calls = []
        def call(stage, instruction, payload):
            calls.append({"stage": stage, "draft": payload.get("draft"),
                          "assessment": copy.deepcopy(payload.get("assessment"))})
            if stage == "write_direct":
                return {"draft": version["draft"]}
            if stage == "verify":
                if payload["draft"] != version["draft"]:
                    raise ValueError("最终稿与送检文字不一致")
                return copy.deepcopy(version["verify_reply"])
            raise ValueError("离线样例不允许额外调用：" + stage)
        result = run(version["packet"], call, method="direct", repair=False)
        reports.append({"report_id": version["report_id"], "parent_report_id": version["parent_report_id"],
                        "packet": copy.deepcopy(version["packet"]), "result": result, "calls": calls})
        if first_snapshot is None:
            first_snapshot = copy.deepcopy(reports[0])
    if reports[0] != first_snapshot:
        raise ValueError("旧版被后续执行覆盖")
    return {"selected_support_gap": gaps, "report_versions": {
        "qualification": fixture["qualification"], "reports": reports,
        "ownership": "报告编号、parent_report_id和旧版保存由Radar负责；本脚本仅模拟该约定。"}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output / "biocheck-examples.json"
    if output.exists():
        parser.error("输出已存在，请指定新目录，避免覆盖验收记录")
    output.write_text(json.dumps(examples(), ensure_ascii=False, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
