"""Small real-data experiments. Labels stay outside every model input."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import time

from .core import run, verify, revise
from .model import DeepSeek


def execute(job, root, model):
    kind, name, packet, extra = job
    folder = root / kind / name
    folder.mkdir(parents=True, exist_ok=True)
    client = DeepSeek(folder / "calls", model=model)
    started = time.monotonic()
    result = {"kind": kind, "name": name, "case_id": packet.get("case_id")}
    if kind == "controls":
        result.update(expected=extra["expected"], labels=extra.get("labels", []), draft=extra["draft"])
    try:
        if kind == "generation":
            result["result"] = run(packet, client, extra["method"])
        else:
            review = verify(packet, extra["draft"], client)
            result["review"] = review
            if review["problem_units"] and not review["verification_errors"]:
                revision = revise(packet, review, client)
                result["revision"] = revision
                result["final_review"] = verify(packet, revision["draft"], client)
            else:
                result["final_review"] = review
            result["expected"] = extra["expected"]
            result["labels"] = extra.get("labels", [])
            result["draft"] = extra["draft"]
        result["runtime"] = "ok"
    except Exception as error:
        result.update(runtime="error", error=str(error))
    result.update(calls=len(client.calls), seconds=round(time.monotonic()-started, 2),
                  known_tokens=sum(c.get("usage", {}).get("total_tokens", 0) for c in client.calls),
                  usage_unknown_calls=sum(c.get("usage", {}).get("total_tokens") is None for c in client.calls))
    (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: result[k] for k in ("kind", "name", "runtime", "calls", "known_tokens")}, ensure_ascii=False), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="data/cases.json")
    parser.add_argument("--controls", default="evaluation/controls.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--model")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--only", choices=["all", "generation", "controls"], default="all")
    args = parser.parse_args()
    root = Path(args.output)
    if root.exists():
        parser.error("实验输出目录已存在；请保留旧结果，选择新目录")
    root.mkdir(parents=True)
    cases = json.loads(Path(args.cases).read_text())
    lookup = {c["case_id"]: c for c in cases}
    jobs = []
    if args.only != "controls":
        for case in cases:
            for method in ("direct", "plain", "bound"):
                jobs.append(("generation", case["case_id"]+"-"+method, case, {"method": method}))
    if args.only != "generation":
        for control in json.loads(Path(args.controls).read_text()):
            packet = dict(lookup[control["case_id"]])
            packet["question"] = "核对给定研究陈述相对于本次材料是否准确，无需补充未被提问的其他结果。"
            jobs.append(("controls", control["id"], packet, control))
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(execute, job, root, args.model) for job in jobs]
        for future in as_completed(futures):
            results.append(future.result())
            (root / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print("Finished", len(results), "jobs", flush=True)


if __name__ == "__main__":
    main()
