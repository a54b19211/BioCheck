"""Golden cases of the rule checkers: what each one answers today, frozen in tests/fixtures/checker_golden.jsonl and
replayed byte for byte by tests/test_checker_golden.py, so code can be moved without changing an answer.

The cases are every request the test suite sends to a checker (over HTTP or directly), every p3 matrix row and
its wrong-final negative under contracts v2 and v3, every canonical_period input of the suite, and the sentence
split of every evaluation draft. Freeze again only together with a CHECKER_VERSION bump, from the repo root:

    PYTHONPATH=. python tests/golden.py
"""

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "checker_golden.jsonl"


def checkers():
    """name -> (module, function name) of every frozen entry point."""
    from app import claims, dimension_rules, item_rules, measurement_rules

    return {"contract_v1": (item_rules, "check"), "contract_v2": (measurement_rules, "check"),
            "contract_v3": (dimension_rules, "check"), "canonical_period": (measurement_rules, "canonical_period"),
            "sentences": (claims, "sentences")}


def answer(name, request):
    """The checker's answer to a frozen request, as JSON-ready data (a contract break is an answer too)."""
    from app import claims, item_rules

    module, function = checkers()[name]
    run = getattr(module, function)
    try:
        if name == "canonical_period":
            return list(run(request["text"]))
        if name == "sentences":
            return [dict(s, numbers=claims.numeric_tokens(s["text"])) for s in run(request["draft"])]
        return run(copy.deepcopy(request))
    except item_rules.ContractError as exc:
        return {"contract_error": str(exc)}


def dumps(data):
    return json.dumps(data, ensure_ascii=False)


def _suite_requests():
    """Run the test suite with each entry point wrapped; the requests it sends, outermost calls only."""
    import pytest

    seen, depth = [], [0]

    def wrap(name, module, function):
        inner = getattr(module, function)

        def recording(*args, **kwargs):
            if depth[0] == 0 and not kwargs:  # a contract's own entry call, never an inner reuse
                request = {"text": args[0]} if name == "canonical_period" else copy.deepcopy(args[0])
                seen.append((name, request))
            depth[0] += 1
            try:
                return inner(*args, **kwargs)
            finally:
                depth[0] -= 1

        setattr(module, function, recording)
        return inner

    originals = {name: wrap(name, module, function) for name, (module, function) in checkers().items()
                 if name != "sentences"}
    try:
        code = pytest.main(["-q", "-p", "no:cacheprovider", str(ROOT / "tests"),
                            "--ignore", str(ROOT / "tests" / "test_checker_golden.py")])
    finally:
        for name, inner in originals.items():
            module, function = checkers()[name]
            setattr(module, function, inner)
    if code != 0:
        raise SystemExit(f"the suite must pass before freezing (pytest exit {code})")
    return seen


def _matrix_requests():
    """Every p3 matrix row and wrong-final negative, as the v2 request a correct client builds, and under v3."""
    sys.path.insert(0, str(ROOT / "tests"))
    from test_measurement_rules import MATRIX, ROWS, request_of

    out = []
    for row in MATRIX["rows"]:
        if row["group"] == "negative":
            if row.get("kind") != "wrong_final_at_real_span":
                continue
            final = copy.deepcopy(ROWS[row["of"]]["final"])
            for key, value in row["change"].items():
                final[key.split(".", 1)[1]] = value
            body = request_of(ROWS[row["of"]], final)
        else:
            body = request_of(row)
        body.pop("contract_version")
        out += [("contract_v2", body), ("contract_v3", copy.deepcopy(body))]
    return out


def _evaluation_requests():
    out = []
    for path in sorted((ROOT / "evaluation").glob("*.json")):
        out += [("sentences", {"draft": s["draft"]}) for s in json.loads(path.read_text(encoding="utf-8"))
                if s.get("draft")]
    return out


def freeze():
    cases, keys = [], set()
    for name, request in _suite_requests() + _matrix_requests() + _evaluation_requests():
        key = (name, dumps(request))
        if key not in keys:
            keys.add(key)
            cases.append({"case": len(cases), "checker": name, "request": request,
                          "response": answer(name, request)})
    FIXTURE.write_text("".join(dumps(c) + "\n" for c in cases), encoding="utf-8")
    counts = {}
    for c in cases:
        counts[c["checker"]] = counts.get(c["checker"], 0) + 1
    print(f"froze {len(cases)} cases into {FIXTURE.relative_to(ROOT)}: {counts}")


if __name__ == "__main__":
    freeze()
