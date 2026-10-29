"""Summarize machine outcomes without turning them into independent accuracy claims."""
import argparse
from collections import Counter
import json
from pathlib import Path


def summarize(path):
    rows = json.loads(Path(path).read_text())
    result = {"jobs": len(rows), "runtime": dict(Counter(r["runtime"] for r in rows)),
              "calls": sum(r["calls"] for r in rows),
              "known_tokens": sum(r.get("known_tokens", r.get("tokens", 0)) for r in rows),
              "usage_unknown_calls": sum(r.get("usage_unknown_calls", 0) for r in rows),
              "generation": {}, "controls": {}, "cases": []}
    for method in ("direct", "plain", "bound"):
        group = [r for r in rows if r["kind"] == "generation" and r["name"].endswith("-"+method)]
        if group:
            result["generation"][method] = {
                "jobs": len(group),
                "initial": dict(Counter(r.get("result", {}).get("initial_review", {}).get("status", "ERROR") for r in group)),
                "final": dict(Counter(r.get("result", {}).get("status", "ERROR") for r in group)),
                "known_tokens": sum(r.get("known_tokens", r.get("tokens", 0)) for r in group)}
    for expected in ("correct", "error"):
        group = [r for r in rows if r["kind"] == "controls" and r.get("expected") == expected]
        if group:
            result["controls"][expected] = {
                "jobs": len(group),
                "initial": dict(Counter(r.get("review", {}).get("status", "ERROR") for r in group)),
                "final": dict(Counter(r.get("final_review", {}).get("status", "ERROR") for r in group))}
    for row in rows:
        initial = row.get("review", row.get("result", {}).get("initial_review", {}))
        final = row.get("final_review", row.get("result", {}).get("final_review", {}))
        result["cases"].append({
            "name": row["name"], "kind": row["kind"], "runtime": row["runtime"],
            "expected": row.get("expected"), "initial": initial.get("status", "ERROR"),
            "final": final.get("status", "ERROR"), "error": row.get("error"),
            "labels": sorted({label for unit in initial.get("units", [])
                              for issue in unit["issues"] for label in issue["labels"]})})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("results", nargs="+")
    args = parser.parse_args()
    print(json.dumps({str(Path(p).parent): summarize(p) for p in args.results}, ensure_ascii=False, indent=2))
