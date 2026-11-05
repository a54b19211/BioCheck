"""Checker v10 (v9 + extraction_normalize.v3, Codex r163): the unit is the head of the metric phrase's own
subject - never a subset named after an inclusion / exclusion / example boundary. Generic templates."""

import pytest
from test_extraction_v4 import answer, client, fields_for, synthetic  # noqa: F401

from app import extraction_v4 as v10

YEAR = {"type": "interval", "precision": "year", "value": "2018"}


def en(client, metric, unit):  # noqa: F811
    text = "In 2018, Hospital B in total reported 23 " + metric + "."
    f = fields_for(text, "23", unit, "In 2018", YEAR, obj="Hospital B", metric=metric, mode="object_metric_total")
    return answer(client, synthetic(text, text, f))


def test_the_checker_is_v10_with_normalize_v3():
    assert v10.CHECKER_VERSION == "biocheck.extraction_rules.v10"
    assert v10.NORMALIZE_VERSION == "extraction_normalize.v3"


@pytest.mark.parametrize("metric,unit", [
    ("close contacts excluding children", "children"), ("close contacts including children", "children"),
    ("close contacts except children", "children"), ("close contacts such as nurses", "nurses"),
    ("contacts among children", "children"), ("contacts involving children", "children"),
    ("cases and deaths", "deaths"),
])
def test_a_subset_named_after_a_boundary_is_not_the_unit(client, metric, unit):  # noqa: F811
    out = en(client, metric, unit)
    assert out["fields"]["unit"]["result"] != "match", out["fields"]["unit"]
    assert out["status"] != "consistent"


@pytest.mark.parametrize("metric", ["close contacts", "close contacts excluding children",
                                    "close contacts including children", "health workers infected in May"])
def test_the_subject_head_is_the_unit(client, metric):  # noqa: F811
    unit = "workers" if "workers" in metric else "contacts"
    out = en(client, metric, unit)
    assert out["fields"]["unit"]["result"] == "match", out["fields"]["unit"]
