"""Every frozen checker case (tests/golden.py) still gets exactly the answer it was frozen with, byte for byte."""

import json

import pytest
from golden import FIXTURE, answer, dumps

CASES = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]


def test_the_golden_set_covers_every_entry_point():
    names = {c["checker"] for c in CASES}
    assert names == {"contract_v1", "contract_v3", "canonical_period", "sentences"}


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c['checker']}-{c['case']}")
def test_a_frozen_case_gets_its_frozen_answer(case):
    assert dumps(answer(case["checker"], case["request"])) == dumps(case["response"])
