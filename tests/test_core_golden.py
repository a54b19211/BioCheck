"""Every frozen generate / verify / revise run (tests/golden_core.py) asks the model the same and returns the same."""

import json

import pytest
from golden_core import FIXTURE, FLOWS, dumps, run

CASES = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]


def test_the_golden_runs_cover_every_flow():
    assert {c["flow"] for c in CASES} == set(FLOWS)


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c['flow']}-{c['case']}")
def test_a_frozen_run_gets_its_frozen_result(case):
    assert dumps(run(case["flow"], case)) == dumps(case["response"])
