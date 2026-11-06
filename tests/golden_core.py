"""Golden runs of generate / verify / revise: every call the test suite makes, with the model answers it got, frozen
in tests/fixtures/core_golden.jsonl and replayed by tests/test_core_golden.py - the same model requests in the same
order, and the same result byte for byte. Freeze again only when a change of these flows is meant, from the repo
root:

    PYTHONPATH=. python tests/golden_core.py
"""

import copy
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "core_golden.jsonl"
FLOWS = ("generate", "verify", "revise")
ENV = "LLM_WRITE_THINKING"


def dumps(data):
    return json.dumps(data, ensure_ascii=False)


def _asked(stage, system, payload, model_cls, options):
    return {"stage": stage, "system": hashlib.sha256(system.encode()).hexdigest()[:16],
            "payload": copy.deepcopy(payload), "model": model_cls.__name__, "options": dict(options)}


class Replay:
    """A model stand-in: the frozen answers in order, each only for exactly the frozen request."""

    def __init__(self, calls):
        self.calls = list(calls)

    def __call__(self, stage, system, payload, model_cls, **options):
        assert self.calls, f"a model call more than frozen: {stage}"
        want = self.calls.pop(0)
        got = _asked(stage, system, payload, model_cls, options)
        assert dumps(got) == dumps({k: want[k] for k in got}), f"model request differs at stage {stage}"
        parsed = None if want["parsed"] is None else model_cls.model_validate(want["parsed"])
        return parsed, copy.deepcopy(want["call"])


def run(flow, case):
    """Replay one frozen run; the result, or the InputError it raised, as JSON-ready data."""
    from app import core

    saved = os.environ.get(ENV)
    if case["env"] is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = case["env"]
    llm = Replay(case["llm"])
    try:
        out = getattr(core, flow)(copy.deepcopy(case["request"]), llm)
    except core.InputError as exc:
        out = {"input_error": {"code": exc.code, "message": exc.message, "status": exc.status,
                               "llm_calls": exc.llm_calls}}
    finally:
        if saved is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = saved
    assert not llm.calls, f"{len(llm.calls)} frozen model call(s) never made"
    return out


def _suite_runs():
    """Run the test suite with the three flows wrapped: each request, its environment and its model calls."""
    import pytest

    from app import core

    seen = []

    def wrap(flow):
        inner = getattr(core, flow)

        def recording(req, llm=core.chat_json):
            calls = []

            def recorder(stage, system, payload, model_cls, **options):
                entry = _asked(stage, system, payload, model_cls, options)
                parsed, call = llm(stage, system, payload, model_cls, **options)
                entry.update(parsed=None if parsed is None else parsed.model_dump(), call=copy.deepcopy(call))
                calls.append(entry)
                return parsed, call

            frozen = {"request": copy.deepcopy(req), "env": os.environ.get(ENV), "llm": calls}
            try:
                out = inner(req, recorder)
            except core.InputError:
                seen.append((flow, frozen))
                raise
            seen.append((flow, frozen))
            return out

        setattr(core, flow, recording)
        return inner

    originals = {flow: wrap(flow) for flow in FLOWS}
    try:
        code = pytest.main(["-q", "-p", "no:cacheprovider", str(ROOT / "tests"),
                            "--ignore", str(ROOT / "tests" / "test_core_golden.py")])
    finally:
        for flow, inner in originals.items():
            setattr(core, flow, inner)
    if code != 0:
        raise SystemExit(f"the suite must pass before freezing (pytest exit {code})")
    return seen


def freeze():
    cases, keys = [], set()
    for flow, frozen in _suite_runs():
        key = (flow, dumps(frozen))
        if key not in keys:
            keys.add(key)
            cases.append(dict(frozen, case=len(cases), flow=flow, response=run(flow, frozen)))
    FIXTURE.write_text("".join(dumps(c) + "\n" for c in cases), encoding="utf-8")
    counts = {flow: sum(c["flow"] == flow for c in cases) for flow in FLOWS}
    print(f"froze {len(cases)} runs into {FIXTURE.relative_to(ROOT)}: {counts}")


if __name__ == "__main__":
    freeze()
