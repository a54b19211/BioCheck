"""Prepare the same research questions from Fabric's read-only records."""
import argparse
import json
from pathlib import Path

from .fabric import fetch, evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", default="evaluation/tasks.json")
    parser.add_argument("--records", help="Reuse an existing Fabric record file without network")
    parser.add_argument("--output", required=True)
    parser.add_argument("--fabric-config", help="Existing read-only Fabric configuration file")
    args = parser.parse_args()
    root = Path(args.output)
    if root.exists():
        parser.error("输出目录已存在，请选新目录；原实验材料保持原样")
    tasks = json.loads(Path(args.tasks).read_text())
    if args.records:
        records = json.loads(Path(args.records).read_text())
    else:
        grouped = {}
        for task in tasks:
            for source in task["sources"]:
                grouped.setdefault(source["source_id"], set()).add(source["record_id"])
        records = []
        for source, ids in grouped.items():
            response = fetch(source, record_ids=sorted(ids), limit=100, config_path=args.fabric_config)
            if response.get("next_cursor"):
                raise ValueError("返回记录需要翻页，请把题目按来源拆成较小批次")
            records.extend(response["items"])
    lookup = {(r["source_id"], r["source_record_id"], r["version_id"]): r for r in records}
    cases = []
    for task in tasks:
        sources = []
        for index, source in enumerate(task["sources"], 1):
            key = (source["source_id"], source["record_id"], source["version"])
            if key not in lookup:
                raise ValueError(f"缺少题目{task['case_id']}所需版本{source['record_id']}；请核对来源版本后再改题目")
            sources.append(evidence(lookup[key], f"E{index}"))
        cases.append({"case_id": task["case_id"], "question": task["question"], "evidence": sources})
    root.mkdir(parents=True)
    (root / "cases").mkdir()
    for case in cases:
        (root / "cases" / (case["case_id"] + ".json")).write_text(json.dumps(case, ensure_ascii=False, indent=2))
    (root / "cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2))
    (root / "fabric_records.json").write_text(json.dumps(records, ensure_ascii=False, indent=2))
    print(f"已准备{len(cases)}个问题；原文和版本保存在{root}")


if __name__ == "__main__":
    main()
